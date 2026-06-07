"""
skills — motor de habilidades de fundo do agente local.
Cada habilidade respeita o liga/desliga da config EM TEMPO REAL (os loops releem a
config a cada ciclo). Funciona com qualquer modelo (leve ou completo).
  - tela:       OCR da janela ativa
  - grafo:      tece um grafo de conhecimento do que vê/conversa
  - iniciativa: fala sozinho quando faz sentido
  - wikipedia:  consulta um .zim local via ziminho
  - memoria:    fatos + histórico, tudo local
"""
import os
import re
import json
import time
import glob
import queue
import ctypes
import threading
import unicodedata
import urllib.request

import requests
import core

# ── canal de eventos pra interface (iniciativa, observação, status) ──────────
_subs = []
_subs_lock = threading.Lock()
conversando = threading.Event()
_obs = []
_ts_iniciativa = 0.0
COOLDOWN = 180


def assinar() -> queue.Queue:
    q = queue.Queue(maxsize=200)
    with _subs_lock:
        _subs.append(q)
    return q


def desassinar(q):
    with _subs_lock:
        if q in _subs:
            _subs.remove(q)


def emitir(tipo, payload):
    with _subs_lock:
        alvos = list(_subs)
    for q in alvos:
        try:
            q.put_nowait((tipo, payload))
        except Exception:
            pass


# ── OCR / tela ───────────────────────────────────────────────────────────────
try:
    import pytesseract
    from PIL import ImageGrab
    for _t in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
               os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Tesseract-OCR\tesseract.exe")):
        if os.path.isfile(_t):
            pytesseract.pytesseract.tesseract_cmd = _t
            break
    _OCR_OK = True
except Exception:
    _OCR_OK = False

_TESSDATA = os.path.join(core.BASE_DIR, "tessdata")
if os.path.isdir(_TESSDATA):
    os.environ.setdefault("TESSDATA_PREFIX", _TESSDATA)   # robusto até com espaço no caminho
# --tessdata-dir só sem aspas e sem espaço (limitação do pytesseract); senão usa o env acima
_TESS_CFG = (f"--tessdata-dir {_TESSDATA}" if (os.path.isdir(_TESSDATA) and " " not in _TESSDATA) else "")
# o OCR segue o idioma da interface (+ inglês de apoio)
_LANG_OCR = {"pt": "por+eng", "en": "eng", "es": "spa+eng"}


def _ocr_lang():
    return _LANG_OCR.get(core.carregar_config().get("idioma", "pt"), "eng")


# os idiomas do OCR são BAIXADOS sob demanda (não ficam no repositório)
_TESS_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{}.traineddata"
_IDIOMA_TESS = {"pt": ["por", "eng"], "en": ["eng"], "es": ["spa", "eng"]}
_tess_lock = threading.Lock()


def garantir_ocr_lang(idioma="pt"):
    """Baixa o(s) .traineddata do idioma atual se faltar. Roda uma vez por idioma."""
    if not _OCR_OK:
        return
    with _tess_lock:
        os.makedirs(_TESSDATA, exist_ok=True)
        for l in _IDIOMA_TESS.get(idioma, ["eng"]):
            dest = os.path.join(_TESSDATA, l + ".traineddata")
            if os.path.isfile(dest):
                continue
            try:
                emitir("status", f"baixando idioma do OCR ({l})…")
                urllib.request.urlretrieve(_TESS_URL.format(l), dest)
            except Exception:
                pass


def janela_ativa() -> str:
    try:
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        return b.value.strip()
    except Exception:
        return ""


def _salvar_print(img, titulo=""):
    try:
        os.makedirs(core.PRINTS_DIR, exist_ok=True)
        th = img.copy()
        th.thumbnail((1280, 800))
        ts = time.strftime("%Y%m%d_%H%M%S")
        th.convert("RGB").save(os.path.join(core.PRINTS_DIR, f"{ts}.jpg"), "JPEG", quality=55)
        arqs = sorted(glob.glob(os.path.join(core.PRINTS_DIR, "*.jpg")))
        for old in arqs[:-40]:                 # mantém os 40 prints mais recentes
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass


def ler_tela(salvar=False):
    titulo = janela_ativa()
    texto = ""
    if _OCR_OK:
        try:
            img = ImageGrab.grab()
            if salvar:
                _salvar_print(img, titulo)
            if img.width > 1920:
                img.thumbnail((1920, 1080))
            try:
                texto = pytesseract.image_to_string(img, lang=_ocr_lang(), config=_TESS_CFG)
            except Exception:
                texto = pytesseract.image_to_string(img, lang="eng", config=_TESS_CFG)
            texto = "\n".join(l.strip() for l in texto.splitlines() if len(l.strip()) > 2)
        except Exception:
            pass
    return titulo, texto


def _ocr_confiavel(t) -> bool:
    return bool(t) and len(re.findall(r"[A-Za-zÀ-ú]{3,}", t)) >= 5


_GAT_TELA = ("minha tela", "na tela", "sua tela", "ver minha", "o que você vê", "o que voce ve",
             "tá vendo", "ta vendo", "está vendo", "esta vendo", "consegue ver", "o que estou fazendo",
             "o que eu tô fazendo", "o que tem aberto", "que site", "que janela", "olha minha tela",
             "my screen", "on screen", "see my screen", "what am i doing", "what's on my screen",
             "mi pantalla", "ves mi pantalla", "qué hay en mi pantalla")


def precisa_tela(texto):
    t = (texto or "").lower()
    return any(g in t for g in _GAT_TELA)


# ── chamada ao modelo (vale pra leve e completo) ─────────────────────────────
def _chat(prompt, system=None, fmt=None, npred=300, temp=0.4, timeout=120):
    cfg = core.carregar_config()
    modelo = core.modelo_atual(cfg)
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    body = {"model": modelo, "messages": msgs, "stream": False, "keep_alive": -1,
            "options": {"num_ctx": cfg.get("num_ctx", 4096), "num_gpu": 99,
                        "temperature": temp, "num_predict": npred}}
    if fmt:
        body["format"] = fmt
    if "qwen3" in modelo:
        body["think"] = False
    try:
        r = requests.post(f"{core.OLLAMA}/api/chat", json=body, timeout=timeout)
        return r.json().get("message", {}).get("content", "").strip()
    except Exception:
        return ""


_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF︀-️‍]")
def _sem_emoji(s):
    return _EMOJI.sub("", s or "")


def _strip_html(h):
    h = re.sub(r"(?is)<(script|style|sup|table).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = re.sub(r"&#?\w+;", " ", h)
    return re.sub(r"\s+", " ", h).strip()


# ── memória local ─────────────────────────────────────────────────────────────
def carregar_mem():
    try:
        with open(core.MEM_FILE, encoding="utf-8") as f:
            m = json.load(f)
    except Exception:
        m = {}
    m.setdefault("fatos", [])
    m.setdefault("historico", [])
    return m


def salvar_mem(m):
    with open(core.MEM_FILE, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


def extrair_fato(user_msg, resposta):
    """Guarda fatos duráveis da conversa na memória local. Heurística + modelo."""
    if not core.habilidade("memoria"):
        return

    # nome dito na conversa → atualiza o campo "Seu nome" do menu + grafo + memória
    mt = re.search(r"\b(?:meu nome é|me chamo|pode me chamar de|sou o|sou a)\s+([A-Za-zÀ-ú]{2,20})",
                   user_msg, re.I)
    if mt:
        nome = mt.group(1).strip()
        nome = nome[:1].upper() + nome[1:]
        cfg = core.carregar_config()
        if cfg.get("nome", "você").lower() in ("você", "voce", "you", "tú", "tu", ""):
            cfg["nome"] = nome                 # preenche o campo do menu automaticamente
            core.salvar_config(cfg)
            emitir("status", f"agora sei que você é o {nome}")
        definir_nome(nome)                      # cria o nó-pessoa no grafo + fato na memória

    # outros fatos via modelo
    if len(user_msg.split()) >= 4:
        m = carregar_mem()
        fatos = m["fatos"]
        p = ("A partir da FALA, escreva UM fato durável sobre o usuário (gosto, trabalho, rotina, "
             "projeto, objetivo), em uma frase curta começando com 'O usuário'. "
             "Se for algo passageiro ou vazio, responda exatamente: NADA.\n"
             "Exemplos:\n"
             "FALA: 'acabei de acordar' → NADA\n"
             "FALA: 'amo programar em Rust' → O usuário gosta de programar em Rust.\n"
             "FALA: 'sou dentista e jogo xadrez' → O usuário é dentista e joga xadrez.\n\n"
             f"FALA: \"{user_msg}\" →")
        fato = _chat(p, npred=50, temp=0.1, timeout=60).strip().strip('"').strip()
        if fato and "NADA" not in fato.upper() and 6 <= len(fato) <= 160 and fato not in fatos:
            fatos.append(fato)
            m["fatos"] = fatos[-60:]
            salvar_mem(m)
            emitir("status", "anotei na memória")


# ── grafo (Tecelão) ───────────────────────────────────────────────────────────
_grafo_lock = threading.Lock()
_GRAFO_SCHEMA = {
    "type": "object",
    "properties": {
        "nos": {"type": "array", "items": {"type": "object",
            "properties": {"label": {"type": "string"}, "tipo": {"type": "string"}},
            "required": ["label", "tipo"]}},
        "arestas": {"type": "array", "items": {"type": "object",
            "properties": {"de": {"type": "string"}, "para": {"type": "string"}, "rel": {"type": "string"}},
            "required": ["de", "para", "rel"]}},
    }, "required": ["nos", "arestas"],
}


def carregar_grafo():
    try:
        with open(core.GRAFO_FILE, encoding="utf-8") as f:
            g = json.load(f)
    except Exception:
        g = {}
    g.setdefault("nos", {})
    g.setdefault("arestas", {})
    return g


def _salvar_grafo(g):
    with open(core.GRAFO_FILE, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=2)


def _chave(label):
    s = "".join(c for c in unicodedata.normalize("NFKD", (label or "").lower()) if not unicodedata.combining(c))
    s = re.sub(r":\d+\b", "", s).replace(":", "")
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


def _resolver(chave, nos):
    if not chave:
        return ""
    if chave in nos:
        return chave
    toks = chave.split()
    for k in nos:
        if len(chave) >= 3 and len(k) >= 3 and (k.startswith(chave) or chave.startswith(k)) and abs(len(k) - len(chave)) <= 2:
            return k
        kt = k.split()
        if len(toks) >= 2 and len(kt) >= 2 and toks[0] == kt[0] and toks[-1] == kt[-1]:
            inter, uni = set(toks) & set(kt), set(toks) | set(kt)
            if len(inter) / len(uni) >= 0.6:
                return k
    return ""


_GENERICO = {"conversa", "mensagem", "app", "aplicativo", "texto", "ocr", "pessoa", "pessoas",
             "projeto", "projetos", "usuario", "agente local", "agente", "conceito", "conceitos",
             "tela", "janela", "conversacao", "assistente", "modelo", "modelos", "relacionamento",
             "coisa", "coisas", "informacao", "dados", "sistema", "ia",
             "nome de pessoa", "nome", "name", "person", "persona", "user", "jogo", "tecnologia",
             "tema", "ferramenta", "game", "games", "lugar", "objeto"}


def tecer(material):
    if not core.habilidade("grafo") or not material.strip():
        return
    p = (
        "Tarefa: ler o TEXTO e montar um grafo de conhecimento. Extraia as ENTIDADES (coisas com "
        "nome próprio: pessoas, jogos, programas, lugares, obras, matérias) e as RELAÇÕES entre elas.\n"
        "REGRAS:\n"
        "1) Em 'label', copie a palavra EXATA do texto. Nunca escreva categorias (NÃO use 'jogo', "
        "'pessoa', 'tecnologia', 'nome', 'app', 'usuário', 'conversa', 'coisa').\n"
        "2) Cada relação liga duas entidades com um verbo curto, tirado do sentido do texto.\n"
        "3) Se não houver entidade concreta, devolva listas vazias.\n\n"
        "EXEMPLOS (siga exatamente este formato):\n"
        "TEXTO: \"Maria criou o site Lojinha usando React\"\n"
        '{"nos":[{"label":"Maria","tipo":"pessoa"},{"label":"Lojinha","tipo":"projeto"},'
        '{"label":"React","tipo":"tecnologia"}],"arestas":[{"de":"Maria","para":"Lojinha","rel":"criou"},'
        '{"de":"Lojinha","para":"React","rel":"usa"}]}\n'
        "TEXTO: \"Pedro assiste One Piece e joga Valorant todo dia\"\n"
        '{"nos":[{"label":"Pedro","tipo":"pessoa"},{"label":"One Piece","tipo":"anime"},'
        '{"label":"Valorant","tipo":"jogo"}],"arestas":[{"de":"Pedro","para":"One Piece","rel":"assiste"},'
        '{"de":"Pedro","para":"Valorant","rel":"joga"}]}\n\n'
        f"Agora faça o mesmo com:\nTEXTO: \"{material[:1200]}\"")
    out = _chat(p, fmt=_GRAFO_SCHEMA, npred=500, temp=0.1, timeout=180)
    i, j = out.find("{"), out.rfind("}") + 1
    if i < 0:
        return
    try:
        dados = json.loads(out[i:j])
    except Exception:
        return
    agora = time.strftime("%Y-%m-%d")
    with _grafo_lock:
        g = carregar_grafo()
        for n in dados.get("nos", []):
            lbl = (n.get("label") or "").strip()
            if not (2 <= len(lbl) <= 40):
                continue
            k = _chave(lbl)
            if not k or k in _GENERICO:
                continue
            eq = _resolver(k, g["nos"])
            if eq:
                g["nos"][eq]["peso"] += 1
            else:
                g["nos"][k] = {"label": lbl, "tipo": (n.get("tipo") or "tema").strip().lower(),
                               "peso": 1, "visto": agora}
        for a in dados.get("arestas", []):
            de, para = _resolver(_chave(a.get("de", "")), g["nos"]), _resolver(_chave(a.get("para", "")), g["nos"])
            if not de or not para or de == para:
                continue
            ch = f"{de}|||{para}"
            chi = f"{para}|||{de}"
            ex = ch if ch in g["arestas"] else (chi if chi in g["arestas"] else None)
            if ex:
                g["arestas"][ex]["peso"] += 1
            else:
                rel = " ".join((a.get("rel") or "liga").split()[:3])[:24]
                g["arestas"][ch] = {"de": de, "para": para, "rel": rel, "peso": 1}
        if len(g["nos"]) > 250:
            fortes = dict(sorted(g["nos"].items(), key=lambda kv: -kv[1]["peso"])[:250])
            g["nos"] = fortes
            g["arestas"] = {k: v for k, v in g["arestas"].items() if v["de"] in fortes and v["para"] in fortes}
        _salvar_grafo(g)
    emitir("grafo", {"nos": len(g["nos"])})


# ── edição manual do grafo (afeta a memória) ─────────────────────────────────
def grafo_criar_no(label, tipo="tema"):
    """Cria um conceito manualmente no grafo."""
    label = (label or "").strip()
    if not label:
        return
    with _grafo_lock:
        g = carregar_grafo()
        k = _chave(label)
        if not k or _resolver(k, g["nos"]):
            return
        g["nos"][k] = {"label": label[:40], "tipo": (tipo or "tema").strip().lower()[:20],
                       "peso": 1, "visto": time.strftime("%Y-%m-%d")}
        _salvar_grafo(g)


def grafo_renomear_no(chave, label):
    with _grafo_lock:
        g = carregar_grafo()
        if chave in g["nos"]:
            g["nos"][chave]["label"] = (label or "").strip()[:40] or g["nos"][chave]["label"]
            _salvar_grafo(g)


def grafo_tipo_no(chave, tipo):
    with _grafo_lock:
        g = carregar_grafo()
        if chave in g["nos"]:
            g["nos"][chave]["tipo"] = (tipo or "tema").strip().lower()[:20]
            _salvar_grafo(g)


def grafo_excluir_no(chave):
    """Remove o nó + suas conexões, e poda os fatos da memória que o mencionam."""
    lbl = ""
    with _grafo_lock:
        g = carregar_grafo()
        if chave in g["nos"]:
            lbl = g["nos"][chave]["label"]
            del g["nos"][chave]
            g["arestas"] = {k: v for k, v in g["arestas"].items()
                            if v["de"] != chave and v["para"] != chave}
            _salvar_grafo(g)
    if lbl and core.habilidade("memoria"):
        m = carregar_mem()
        novos = [f for f in m["fatos"] if lbl.lower() not in f.lower()]
        if len(novos) != len(m["fatos"]):
            m["fatos"] = novos
            salvar_mem(m)


def grafo_criar_aresta(de_lbl, para_lbl, rel):
    with _grafo_lock:
        g = carregar_grafo()
        agora = time.strftime("%Y-%m-%d")

        def garantir(lbl):
            lbl = (lbl or "").strip()
            k = _chave(lbl)
            if not k:
                return ""
            eq = _resolver(k, g["nos"])
            if eq:
                return eq
            g["nos"][k] = {"label": lbl[:40], "tipo": "tema", "peso": 1, "visto": agora}
            return k
        de, para = garantir(de_lbl), garantir(para_lbl)
        if de and para and de != para:
            ch = f"{de}|||{para}"
            g["arestas"][ch] = {"de": de, "para": para, "rel": (rel or "liga").strip()[:24], "peso": 1}
            _salvar_grafo(g)


def grafo_excluir_aresta(de, para):
    with _grafo_lock:
        g = carregar_grafo()
        for k in (f"{de}|||{para}", f"{para}|||{de}"):
            g["arestas"].pop(k, None)
        _salvar_grafo(g)


def grafo_limpar(tambem_memoria=False):
    with _grafo_lock:
        _salvar_grafo({"nos": {}, "arestas": {}})
    if tambem_memoria:
        m = carregar_mem()
        m["fatos"] = []
        salvar_mem(m)


def grafo_traduzir(lang):
    """Traduz os rótulos dos nós pro idioma pedido (en/es) e guarda em node['t'][lang]."""
    if lang not in ("en", "es"):
        return
    with _grafo_lock:
        g = carregar_grafo()
        faltam = [k for k, n in g["nos"].items() if not (n.get("t") or {}).get(lang)]
        labels = [g["nos"][k]["label"] for k in faltam]
    if not labels:
        return
    idioma = {"en": "inglês", "es": "espanhol"}[lang]
    p = (f"Traduza cada termo da lista para {idioma}. Mantenha nomes próprios (pessoa, jogo, marca, "
         f"produto) SEM mudar. Responda SÓ um array JSON de strings, na MESMA ordem e tamanho.\n"
         f"Lista: {json.dumps(labels, ensure_ascii=False)}")
    out = _chat(p, fmt={"type": "array", "items": {"type": "string"}}, npred=600, temp=0.1, timeout=180)
    i, j = out.find("["), out.rfind("]") + 1
    if i < 0:
        return
    try:
        trads = json.loads(out[i:j])
    except Exception:
        return
    with _grafo_lock:
        g = carregar_grafo()
        for k, tr in zip(faltam, trads):
            if k in g["nos"] and isinstance(tr, str) and tr.strip():
                g["nos"][k].setdefault("t", {})[lang] = tr.strip()[:40]
        _salvar_grafo(g)


def limpar_genericos():
    """Remove nós-categoria genéricos (lixo de modelos fracos) do grafo."""
    with _grafo_lock:
        g = carregar_grafo()
        ruins = [k for k in g["nos"] if k in _GENERICO]
        for k in ruins:
            del g["nos"][k]
        if ruins:
            g["arestas"] = {k: v for k, v in g["arestas"].items()
                            if v["de"] in g["nos"] and v["para"] in g["nos"]}
            _salvar_grafo(g)
        return len(ruins)


def definir_nome(nome):
    """O usuário definiu o nome em 'Seu nome': atualiza a memória e o nó da pessoa no grafo."""
    nome = (nome or "").strip()
    if not nome or nome.lower() in ("você", "voce", "you", "tú", "tu"):
        return
    # memória: substitui o fato do nome
    m = carregar_mem()
    m["fatos"] = [f for f in m["fatos"] if "nome do usu" not in f.lower()]
    m["fatos"].insert(0, f"O nome do usuário é {nome}.")
    m["fatos"] = m["fatos"][-60:]
    salvar_mem(m)
    # grafo: tira nós genéricos de "nome" e garante o nó da pessoa
    with _grafo_lock:
        g = carregar_grafo()
        for gk in ("nome de pessoa", "nome", "name", "person", "user", "usuario"):
            if gk in g["nos"]:
                del g["nos"][gk]
        g["arestas"] = {k: v for k, v in g["arestas"].items()
                        if v["de"] in g["nos"] and v["para"] in g["nos"]}
        k = _chave(nome)
        if k:
            eq = _resolver(k, g["nos"])
            if eq:
                g["nos"][eq]["tipo"] = "pessoa"
                g["nos"][eq]["label"] = nome[:40]
            else:
                g["nos"][k] = {"label": nome[:40], "tipo": "pessoa", "peso": 3,
                               "visto": time.strftime("%Y-%m-%d")}
        _salvar_grafo(g)


def resumo_grafo(query="", max_nos=10):
    g = carregar_grafo()
    nos, arestas = g.get("nos", {}), list(g.get("arestas", {}).values())
    if not nos:
        return ""
    if query:
        termos = set(re.findall(r"[a-zà-ú0-9]{3,}", query.lower()))
        rel = [k for k in nos if any(t in k or t in nos[k]["label"].lower() for t in termos)]
        if not rel:
            return ""
        viz = set(rel)
        for a in arestas:
            if a["de"] in rel:
                viz.add(a["para"])
            if a["para"] in rel:
                viz.add(a["de"])
        escolhidos = sorted((k for k in viz if k in nos), key=lambda k: -nos[k]["peso"])[:max_nos]
    else:
        escolhidos = [k for k, _ in sorted(nos.items(), key=lambda kv: -kv[1]["peso"])[:max_nos]]
    linhas = []
    for k in escolhidos:
        c = [f"{a['rel']} {nos[a['para']]['label']}" for a in arestas if a["de"] == k and a["para"] in nos]
        linhas.append(f"- {nos[k]['label']}" + ((": " + "; ".join(c[:4])) if c else ""))
    return "\n".join(linhas)


# ── Wikipédia (ziminho) ───────────────────────────────────────────────────────
_zim = None


def _zim_reader():
    global _zim
    if _zim is None:
        zs = glob.glob(os.path.join(core.WIKI_DIR, "*.zim"))
        if not zs:
            return None
        try:
            import ziminho
            _zim = ziminho.ZimReader(zs[0])
        except Exception:
            _zim = None
    return _zim


def garantir_indice_wiki():
    z = _zim_reader()
    if not z:
        return
    try:
        if z.indice_pronto():
            return
    except Exception:
        return
    def _b():
        try:
            emitir("status", "indexando a Wikipédia (uma vez)…")
            z.construir_indice()
            emitir("status", "Wikipédia pronta")
        except Exception:
            pass
    threading.Thread(target=_b, daemon=True).start()


def consultar_wiki(query, limite=1500):
    if not core.habilidade("wikipedia"):
        return ""
    z = _zim_reader()
    if not z:
        return ""
    try:
        if not z.indice_pronto():
            return ""
    except Exception:
        return ""
    for v in [query, query.split(" (")[0]]:
        try:
            html = z.artigo(v.strip())
        except Exception:
            html = None
        if html:
            txt = _strip_html(html)
            if len(txt) > 80:
                return txt[:limite]
    return ""


# ── observação + iniciativa ───────────────────────────────────────────────────
def _carregar_obs():
    try:
        with open(core.OBS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _salvar_obs(obs):
    try:
        with open(core.OBS_FILE, "w", encoding="utf-8") as f:
            json.dump(obs[-80:], f, ensure_ascii=False)
    except Exception:
        pass


def _nota_atividade(titulo, texto):
    ocr = _ocr_confiavel(texto)
    bloco = (f"Texto lido na tela (OCR):\n{texto[:1000]}" if ocr else "OCR ilegível — use só o título da janela.")
    p = (f"Janela ativa: {titulo}\n{bloco}\n\n"
         "Em UMA frase curta e factual (português), diga o que a pessoa está fazendo na tela, "
         "com base no título e no texto. NÃO invente o que não está claro. Se não der pra saber, "
         "responda: NADA.\n"
         "Exemplos de boas respostas: 'Assistindo um vídeo no YouTube sobre culinária', "
         "'Editando código Python no VS Code', 'Lendo um artigo da Wikipédia sobre o Brasil', "
         "'Conversando no WhatsApp'.")
    nota = _chat(p, npred=60, temp=0.2, timeout=60).strip()
    return "" if (not nota or nota.upper().startswith("NADA") or len(nota) < 8) else nota


def talvez_iniciativa(forcar=False):
    global _ts_iniciativa
    if not core.habilidade("iniciativa") or conversando.is_set():
        return
    if not forcar and time.time() - _ts_iniciativa < COOLDOWN:
        return
    atividade = "\n".join(f"- {o['texto']}" for o in _obs[-5:]) or "(nada observado na tela)"
    fatos = carregar_mem().get("fatos", [])
    bloco_fatos = ("\nO QUE VOCÊ SABE DELE:\n" + "\n".join("- " + f for f in fatos[-10:])) if fatos else ""
    mapa = resumo_grafo(atividade)
    bloco_mapa = f"\nMAPA MENTAL:\n{mapa}\n" if mapa else ""
    obrig = ("Você DEVE mandar uma mensagem agora (frase não vazia)."
             if forcar else
             "Decida se vale falar algo AGORA. Só fale se tiver algo concreto e específico.")
    p = ("Você é o assistente pessoal local da pessoa.\n"
         f"O QUE ELA FEZ HÁ POUCO (tela):\n{atividade}\n{bloco_fatos}{bloco_mapa}\n"
         f"{obrig} Pode ser um comentário esperto, uma pergunta genuína sobre o que ela faz, "
         "ou algo ligado aos interesses dela. Nada de oferta vazia ('precisa de ajuda?'). "
         "Tom calmo, 1-2 frases, sem emoji.\n"
         'Responda SÓ JSON: {"falar": true/false, "frase": "a mensagem"}')
    out = _chat(p, npred=120, temp=0.6, timeout=90)
    i, j = out.find("{"), out.rfind("}") + 1
    if i < 0:
        return
    try:
        res = json.loads(out[i:j])
    except Exception:
        return
    frase = _sem_emoji(res.get("frase") or "").strip()
    if (res.get("falar") or forcar) and frase and not conversando.is_set():
        _ts_iniciativa = time.time()
        emitir("iniciativa", frase)


def iniciativa_loop():
    """Dispara iniciativa: a cada X min (modo intervalo) ou periodicamente deixando a IA decidir (dinâmico)."""
    time.sleep(40)
    while True:
        try:
            cfg = core.carregar_config()
            modo = cfg.get("iniciativa_modo", "dinamico")
            espera = max(60, int(cfg.get("iniciativa_intervalo", 10)) * 60) if modo == "intervalo" else 90
            time.sleep(espera)
            cfg = core.carregar_config()
            if not cfg.get("ativo", True):
                continue
            if not cfg["habilidades"].get("iniciativa") or conversando.is_set() or not core.ollama_online():
                continue
            talvez_iniciativa(forcar=(cfg.get("iniciativa_modo") == "intervalo"))
        except Exception:
            pass


def _memoria_de_obs(notas):
    """Extrai um interesse/hábito durável do que viu na tela e guarda na memória."""
    p = ("Pelas ATIVIDADES de tela, escreva UM interesse ou hábito DURÁVEL do usuário, em uma frase "
         "começando com 'O usuário'. Se for só uso pontual/sem padrão, responda exatamente: NADA.\n"
         "Exemplos:\n"
         "ATIVIDADES: 'assistiu vários vídeos de violão' → O usuário gosta de violão.\n"
         "ATIVIDADES: 'abriu o explorador de arquivos' → NADA\n\n"
         f"ATIVIDADES:\n{notas}\n→")
    f = _chat(p, npred=50, temp=0.2, timeout=60).strip().strip('"').strip()
    if f and "NADA" not in f.upper() and 6 <= len(f) <= 160:
        m = carregar_mem()
        if f not in m["fatos"]:
            m["fatos"].append(f)
            m["fatos"] = m["fatos"][-60:]
            salvar_mem(m)
            emitir("status", "aprendi algo te observando")


def observar_loop():
    global _obs
    _obs = _carregar_obs()
    ult = None
    while True:
        try:
            cfg = core.carregar_config()
            time.sleep(max(15, int(cfg.get("obs_intervalo", 60))))   # frequência configurável
            if not core.carregar_config().get("ativo", True):        # sistema pausado
                continue
            if conversando.is_set() or not core.ollama_online():
                continue
            if not cfg["habilidades"].get("tela"):
                continue
            garantir_ocr_lang(cfg.get("idioma", "pt"))   # baixa o idioma do OCR se faltar
            titulo, texto = ler_tela(salvar=True)         # salva o print na pasta
            if not titulo and not texto:
                continue
            assin = titulo + texto[:200]
            if assin == ult:
                continue
            ult = assin
            nota = _nota_atividade(titulo, texto)
            if not nota:
                continue
            _obs.append({"ts": time.strftime("%Y-%m-%d %H:%M"), "texto": nota, "janela": titulo})
            del _obs[:-60]
            _salvar_obs(_obs)
            emitir("observacao", nota)
            if cfg["habilidades"].get("grafo"):
                tecer("Atividade na tela: " + nota)        # vira conceito no grafo
            if cfg["habilidades"].get("memoria") and len(_obs) % 4 == 0:
                _memoria_de_obs("\n".join(o["texto"] for o in _obs[-6:]))   # vira fato na memória
        except Exception:
            pass


def iniciar():
    threading.Thread(target=observar_loop, daemon=True).start()
    threading.Thread(target=iniciativa_loop, daemon=True).start()
    garantir_indice_wiki()
