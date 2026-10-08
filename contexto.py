"""
contexto — como o chat monta o contexto que o modelo recebe.

  * seleciona memórias por relevância + importância + recência (com projeto ativo e herança);
  * respeita um orçamento em caracteres derivado da janela de contexto do modelo;
  * remove duplicatas (fatos x memórias x conversas) e respeita fixadas/excluídas;
  * devolve QUAIS memórias foram usadas, para a interface citar a fonte;
  * filtra o que nunca deve ser salvo automaticamente (senhas, tokens, cartões...);
  * tira blocos <think> dos modelos de raciocínio.

Não grava nada; só lê (memstore, chats, config).
"""
import re
import time
import unicodedata

import core
import ajustes

CHARS_POR_TOKEN = 3.5          # estimativa conservadora para PT/EN
MIN_ORCAMENTO = 600            # nunca abaixo disso (caracteres)
PESO_RELEVANCIA, PESO_IMPORTANCIA, PESO_RECENCIA = 0.60, 0.25, 0.15
MAX_TRECHO_MEMORIA = 400


def estimar_tokens(texto: str) -> int:
    return int(len(texto or "") / CHARS_POR_TOKEN) + 1


def orcamento_chars(cfg: dict, perfil: dict) -> int:
    """Caracteres disponíveis para o contexto recuperado (memórias, grafo, docs...)."""
    ctx = int(perfil.get("num_ctx") or 4096)
    saida = int(perfil.get("max_tokens") or min(1024, ctx // 4))
    livre = max(256, ctx - saida - 300)                # 300 ≈ prompt-base + pergunta
    pct = int((cfg.get("contexto") or {}).get("ctx_pct", 35))
    return max(MIN_ORCAMENTO, int(livre * pct / 100 * CHARS_POR_TOKEN))


# ── dados sensíveis (nunca salvar sozinho) ────────────────────────────────────
_SENSIVEL = [
    # palavra-chave (até 3 palavras depois) + ":" ou "=" + valor
    re.compile(r"(?i)\b(?:senha|password|passwd|pwd|passcode|token|api[\s_-]?key|apikey|secret|segredo|"
               r"chave\s+(?:privada|de\s+api|secreta)|private\s+key|cvv|cvc)\b(?:\s+\w+){0,3}\s*[:=]\s*\S{3,}"),
    # palavra-chave + "é/is/es" + valor que tenha dígito ou símbolo (evita "a senha é importante")
    re.compile(r"(?i)\b(?:senha|password|passwd|pwd|passcode|token|api[\s_-]?key|apikey|secret|segredo|cvv|cvc)\b"
               r"(?:\s+\w+){0,3}\s+(?:é|e|is|es|era|foi)\s+(?=\S*[\d!@#$%^&*_\-+=])\S{4,}"),
    # palavra-chave seguida direto de algo que parece um valor (letras+dígitos): "senha Abc12345"
    re.compile(r"(?i)\b(?:senha|password|passwd|pwd|token|api[\s_-]?key|secret|segredo)\s+(?=[^\s\d]*\d)\S{6,}"),
    re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\batlas_[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),                       # CPF
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                                  # número de cartão
    re.compile(r"\b[A-Za-z0-9_\-+/=]{32,}\b"),                              # blob longo (chave/token)
]


def sensivel(texto: str) -> bool:
    """True se o texto parece conter senha, token, chave, cartão ou documento."""
    t = texto or ""
    return any(r.search(t) for r in _SENSIVEL)


# ── normalização / duplicatas ─────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if len(t) > 2}


def parecido(a: str, b: str, limiar: float = 0.8) -> bool:
    """Duplicata exata (normalizada) ou quase (Jaccard de palavras >= limiar)."""
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= limiar


def deduplicar(itens, chave=lambda x: x):
    out = []
    for it in itens:
        if not any(parecido(chave(it), chave(o)) for o in out):
            out.append(it)
    return out


# ── ranking ───────────────────────────────────────────────────────────────────
def _idade_dias(iso: str, agora: float) -> float:
    try:
        t = time.mktime(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
        return max(0.0, (agora - t) / 86400)
    except Exception:
        return 365.0


def recencia(iso: str, meia_vida_dias: float, agora: float = None) -> float:
    """1.0 = acabou de mudar; cai pela metade a cada `meia_vida_dias`."""
    agora = agora or time.time()
    return 0.5 ** (_idade_dias(iso, agora) / max(1.0, float(meia_vida_dias)))


def pontuar(m: dict, relevancia: float, meia_vida: float, agora: float = None) -> float:
    imp = max(1, min(5, int(m.get("importance") or 3))) / 5
    rel = max(0.0, min(1.0, relevancia))
    dist = 1 - 0.12 * float(m.get("distance") or 0)         # herdada de projeto-pai pesa menos
    return (PESO_RELEVANCIA * rel + PESO_IMPORTANCIA * imp +
            PESO_RECENCIA * recencia(m.get("updated"), meia_vida, agora)) * max(0.5, dist)


# ── fixadas / excluídas (conversa e projeto) ─────────────────────────────────
def regras(cfg: dict, chat: dict, projeto: str):
    """(fixas, excluidas) finais. A decisão da conversa vence a do projeto."""
    proj = (cfg.get("ctx_projeto") or {}).get(projeto or "", {}) or {}
    conv_fix, conv_exc = set((chat or {}).get("ctx_fixas") or []), set((chat or {}).get("ctx_excluidas") or [])
    fix, exc = set(), set()
    for i in proj.get("excluidas", []):
        exc.add(i)
    for i in proj.get("fixas", []):
        exc.discard(i)
        fix.add(i)
    for i in conv_exc:
        fix.discard(i)
        exc.add(i)
    for i in conv_fix:
        exc.discard(i)
        fix.add(i)
    return fix, exc


def _forma(m: dict, motivo: str, fixada: bool) -> dict:
    return {"id": m["id"], "content": m["content"], "project": m.get("project"), "type": m.get("type"),
            "importance": m.get("importance"), "source": m.get("source"), "updated": m.get("updated"),
            "inherited": bool(m.get("inherited")), "score": m.get("_final"), "why": motivo, "pinned": fixada}


PISO_RELEVANCIA = 0.12        # abaixo disso a memória quase não tem a ver com a pergunta
PISO_RELATIVO = 0.3           # ...ou é bem mais fraca que a melhor encontrada
MAX_FIXADAS_GLOBAIS = 8


def cortar_fracas(itens, piso: float = PISO_RELEVANCIA, relativo: float = PISO_RELATIVO):
    """Tira candidatas de relevância baixa (absoluta ou em relação à melhor), para não
    encher o prompt com memórias que só compartilham uma palavra com a pergunta."""
    if not itens:
        return []
    melhor = max(float(m.get("score") or 0) for m in itens)
    corte = max(piso, relativo * melhor)
    return [m for m in itens if float(m.get("score") or 0) >= corte]


def selecionar_memorias(texto: str, projeto: str, cfg: dict, chat: dict, orcamento: int, buscar=None, obter=None,
                        listar=None, agora: float = None, fixadas=None):
    """Escolhe as memórias do contexto. `buscar/obter/listar/fixadas` são injetáveis (testes); por padrão
    usam memstore. Devolve lista de dicts (ver _forma), na ordem em que entram no prompt."""
    import memstore
    if fixadas is None and buscar is not None:     # busca injetada (testes): não lê o banco real
        fixadas = lambda p: []
    cc = cfg.get("contexto") or {}
    semantica = bool(cc.get("busca_semantica"))
    # a geral é a raiz: conversando nela, a busca olha todos os projetos (senão as memórias de
    # 'ACME' nunca apareceriam numa conversa da Geral)
    buscar = buscar or (lambda q, p, n: cortar_fracas(memstore.memorias_buscar(
        q, None if p == memstore.PROJETO_PADRAO else p, limite=n, semantica=semantica, min_score=0.05,
        escopo="inherit")["items"]))
    obter = obter or memstore.memoria_obter
    listar = listar or (lambda p, n: memstore.memorias_listar(p, limite=n, ordem="importance",
                                                              escopo="inherit")["items"])
    # fixadas na própria memória (pinned): valem para o projeto delas e os subprojetos; sem projeto, só as da geral
    fixadas = fixadas or (lambda p: memstore.memorias_listar(p or memstore.PROJETO_PADRAO,
                                                             limite=MAX_FIXADAS_GLOBAIS, ordem="importance",
                                                             escopo="inherit" if p else "exact",
                                                             fixadas=True)["items"])
    maximo = int(cc.get("max_memorias", 6))
    meia = float(cc.get("recencia_dias", 30))
    fix, exc = regras(cfg, chat, projeto)
    try:
        globais = [m for m in fixadas(projeto or None) if m.get("pinned")][:MAX_FIXADAS_GLOBAIS]
    except Exception:
        globais = []
    if maximo <= 0 and not fix and not globais:
        return []

    escolhidas, ids = [], set()

    def ok(m):
        return m["id"] not in ids and m["id"] not in exc and not any(
            parecido(m["content"], e["content"]) for e in escolhidas)

    # 1) fixadas primeiro (sempre, mesmo sem relevância)
    for mid in sorted(fix):
        try:
            m = obter(mid)
        except Exception:
            continue
        if ok(m):
            escolhidas.append(_forma(m, "fixada", True))
            ids.add(m["id"])
    for m in globais:
        if ok(m):
            escolhidas.append(_forma(m, "fixada", True))
            ids.add(m["id"])

    # 2) relevantes: relevância + importância + recência
    cand = []
    if (texto or "").strip():
        try:
            cand = buscar(texto, projeto or None, max(30, maximo * 4))
        except Exception:
            cand = []
    ranq = []
    for m in cand:
        m = dict(m)
        m["_final"] = round(pontuar(m, float(m.get("score") or 0), meia, agora), 4)
        ranq.append(m)
    ranq.sort(key=lambda m: m["_final"], reverse=True)

    # 3) projeto ativo sem nada relevante: as mais importantes do projeto
    extra = []
    if projeto and len(ranq) < 2:
        try:
            for m in listar(projeto, 6):
                if (m.get("importance") or 0) >= 4:
                    m = dict(m)
                    m["_final"] = round(pontuar(m, 0.3, meia, agora), 4)
                    extra.append(m)
        except Exception:
            pass

    gasto = sum(min(len(e["content"]), MAX_TRECHO_MEMORIA) + 4 for e in escolhidas)
    livres = max(0, maximo - len([e for e in escolhidas]))
    for m, motivo in [(x, "relevante") for x in ranq] + [(x, "projeto") for x in extra]:
        if livres <= 0:
            break
        if not ok(m):
            continue
        custo = min(len(m["content"]), MAX_TRECHO_MEMORIA) + 4
        if gasto + custo > orcamento and escolhidas:
            continue
        escolhidas.append(_forma(m, motivo, False))
        ids.add(m["id"])
        gasto += custo
        livres -= 1
    return escolhidas


def linhas_memorias(usadas) -> str:
    return "\n".join("- " + u["content"][:MAX_TRECHO_MEMORIA] for u in usadas)


# ── orçamento de seções ───────────────────────────────────────────────────────
def cortar(texto: str, limite: int) -> str:
    """Corta em fim de linha/frase sem passar de `limite` caracteres."""
    if len(texto) <= limite:
        return texto
    if limite <= 1:
        return ""
    t = texto[:limite - 1]
    k = max(t.rfind("\n"), t.rfind(". "))
    if k > limite * 0.5:
        t = t[:k + 1]
    return t.rstrip() + "…"


def ajustar_secoes(secoes, total: int):
    """secoes: lista de (chave, titulo, texto) em ordem de prioridade (a 1ª é a mais importante).
    Cada seção recebe no máximo metade do que sobra (exceto a última), para que nenhuma fonte
    engula o orçamento. Devolve (texto_final, {chave: chars_usados})."""
    uso, partes, restante = {}, [], total
    validas = [(k, t, x) for k, t, x in secoes if (x or "").strip()]
    for i, (k, titulo, texto) in enumerate(validas):
        sobra = restante - len(titulo) - 4
        if sobra < 80:
            continue
        teto = sobra if i == len(validas) - 1 else max(80, int(sobra * 0.6))
        corpo = cortar(texto.strip(), teto)
        partes.append(f"{titulo}\n{corpo}")
        uso[k] = len(corpo)
        restante -= len(corpo) + len(titulo) + 4
    return ("\n\n".join(partes), uso)


# ── instruções (personalidade) ───────────────────────────────────────────────
def instrucoes_texto(cfg: dict, idioma: str, projeto: str = None) -> str:
    ins = cfg.get("instrucoes") or {}
    partes = []
    pid = ins.get("preset", "padrao")
    if pid in ajustes.PRESETS:
        t = ajustes.PRESETS[pid]["texto"].get(idioma) or ajustes.PRESETS[pid]["texto"]["pt"]
        if t:
            partes.append(t)
    else:
        for p in ins.get("personalizados", []):
            if p.get("id") == pid and p.get("texto"):
                partes.append(p["texto"])
    if ins.get("extra"):
        partes.append(ins["extra"])
    pt = (cfg.get("instrucoes_projeto") or {}).get(projeto or "")
    if pt:
        partes.append(pt)
    return ("\n\nInstruções do usuário:\n" + "\n".join(partes)) if partes else ""


# ── modelos de raciocínio: remover <think>...</think> do stream ───────────────
class FiltroPensamento:
    """Remove blocos <think>...</think> de um stream de texto, mesmo quebrados entre pedaços."""

    def __init__(self):
        self.dentro = False
        self.buf = ""

    def push(self, pedaco: str) -> str:
        self.buf += pedaco
        out = ""
        while self.buf:
            if self.dentro:
                k = self.buf.find("</think>")
                if k < 0:
                    self.buf = self.buf[-8:]        # guarda só o possível início de "</think>"
                    return out
                self.buf = self.buf[k + 8:].lstrip("\n")
                self.dentro = False
            else:
                k = self.buf.find("<think>")
                if k >= 0:
                    out += self.buf[:k]
                    self.buf = self.buf[k + 7:]
                    self.dentro = True
                    continue
                seg = self.buf.rfind("<")           # pode ser o começo de "<think>"
                if seg >= 0 and "<think>".startswith(self.buf[seg:]):
                    out += self.buf[:seg]
                    self.buf = self.buf[seg:]
                    return out
                out += self.buf
                self.buf = ""
        return out

    def fim(self) -> str:
        r = "" if self.dentro else self.buf
        self.buf, self.dentro = "", False
        return r
