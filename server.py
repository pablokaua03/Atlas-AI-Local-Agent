"""
server — interface web local do agente configurável.
127.0.0.1:5005. Escolhe modelo (leve/completo), liga/desliga habilidades, conversa.
Nada sai da máquina.
"""
import os
import json
import queue
import base64
import ctypes
import shutil
import threading
import subprocess
from ctypes import wintypes

import requests
from flask import Flask, request, Response, send_from_directory, jsonify

import core
import skills
import chats
import docs
import cofre
import lembretes
import bandeja
import memstore
import memapi
import ajustes
import contexto
import urllib.parse

WEB_DIR = os.path.join(core.BASE_DIR, "web")
HOST, PORT = "127.0.0.1", 5005

app = Flask(__name__, static_folder=None)
memapi.registrar(app, HOST, PORT)       # API de memória /v1 para qualquer IA

SYSTEM_BASE = (
    "Você é o assistente pessoal local do {nome}. Tom calmo, natural e direto. "
    "Sem emoji decorativo, sem ofertas vazias no fim, sem bajulação. "
    "NUNCA mencione seus mecanismos internos (grafo, memória, OCR, contexto, sistema) — "
    "apenas use o que sabe de forma natural. NÃO faça várias perguntas de uma vez; no máximo uma, "
    "e só se for genuína. Respostas curtas quando a conversa é simples. "
    "Se não souber algo, admita em vez de inventar.{idioma}"
)
INSTR_IDIOMA = {
    "pt": " Responda SEMPRE em português brasileiro.",
    "en": " Always reply in English.",
    "es": " Responde SIEMPRE en español.",
}


def _corpo_json() -> dict:
    """Corpo JSON como dict (qualquer outra coisa — lista, texto, vazio — vira {})."""
    d = request.get_json(force=True, silent=True)
    return d if isinstance(d, dict) else {}


def _inteiro(v, minimo, maximo):
    """int limitado a [minimo, maximo]; None se não for um número finito."""
    try:
        if isinstance(v, bool) or v != v or v in (float("inf"), float("-inf")):
            return None
        return max(minimo, min(maximo, int(v)))
    except (TypeError, ValueError, OverflowError):
        return None


def _baixar_ok(r):
    """Falha cedo se o download não veio com HTTP 2xx (evita gravar uma página de erro como arquivo)."""
    r.raise_for_status()
    return r


# ── PÁGINAS ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/grafo")
def grafo_page():
    return send_from_directory(WEB_DIR, "grafo.html")


@app.route("/memorias")
def memorias_page():
    return send_from_directory(WEB_DIR, "memorias.html")


@app.route("/static/<path:nome>")
def estatico(nome):
    return send_from_directory(WEB_DIR, nome)


# ── MONITOR DO SISTEMA (GPU/VRAM via nvidia-smi, CPU/RAM via ctypes) ──────────
class _MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

_cpu_prev = {"idle": 0, "total": 0}


def _cpu_pct():
    try:
        idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        v = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime
        i, total = v(idle), v(kern) + v(user)
        di, dt = i - _cpu_prev["idle"], total - _cpu_prev["total"]
        _cpu_prev["idle"], _cpu_prev["total"] = i, total
        return max(0, min(100, round((1 - di / dt) * 100))) if dt > 0 else 0
    except Exception:
        return None


def _ram():
    try:
        m = _MEMSTAT()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.dwMemoryLoad, m.ullTotalPhys, m.ullTotalPhys - m.ullAvailPhys
    except Exception:
        return None, 0, 0


def _gpu():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=4, creationflags=0x08000000).stdout.strip().splitlines()[0]
        u, tot, util = [int(x.strip()) for x in out.split(",")]
        return {"vram_usada": u, "vram_total": tot, "gpu": util}
    except Exception:
        return {}


@app.route("/api/sistema")
def api_sistema():
    load, rtot, rused = _ram()
    return jsonify({**_gpu(), "cpu": _cpu_pct(), "ram_pct": load,
                    "ram_usada": rused, "ram_total": rtot})


# ── ESTADO / CONFIG ───────────────────────────────────────────────────────────
@app.route("/api/estado")
def api_estado():
    return jsonify({**core.estado(), "cofre": cofre.estado()})


@app.route("/api/config", methods=["POST"])
def api_config():
    d = _corpo_json()
    cfg = core.carregar_config()
    cfg, avisos = ajustes.aplicar(cfg, d)      # modelo, embed, geração, contexto, instruções... (validado/limitado)
    if _inteiro(d.get("obs_intervalo"), 15, 900) is not None:
        cfg["obs_intervalo"] = _inteiro(d["obs_intervalo"], 15, 900)
    if d.get("iniciativa_modo") in ("dinamico", "intervalo"):
        cfg["iniciativa_modo"] = d["iniciativa_modo"]
    if _inteiro(d.get("iniciativa_intervalo"), 1, 180) is not None:
        cfg["iniciativa_intervalo"] = _inteiro(d["iniciativa_intervalo"], 1, 180)
    if "nome" in d:
        cfg["nome"] = (str(d["nome"]).strip()[:40] or "você")
    if d.get("idioma") in ("pt", "en", "es"):
        cfg["idioma"] = d["idioma"]
    if d.get("tema") in ("claro", "escuro"):
        cfg["tema"] = d["tema"]
    if "api_ativa" in d:
        cfg["api_ativa"] = bool(d["api_ativa"])
    if "ativo" in d:
        cfg["ativo"] = bool(d["ativo"])
        if not cfg["ativo"]:                 # pausou → libera o modelo da memória
            threading.Thread(target=core.descarregar_modelos, daemon=True).start()
    if "iniciar_com_windows" in d:
        cfg["iniciar_com_windows"] = bool(d["iniciar_com_windows"])
        core.set_autostart(cfg["iniciar_com_windows"])
    ligou_docs = False
    if isinstance(d.get("habilidades"), dict):
        for k, v in d["habilidades"].items():
            if k in cfg["habilidades"]:
                if k == "documentos" and bool(v) and not cfg["habilidades"]["documentos"]:
                    ligou_docs = True
                cfg["habilidades"][k] = bool(v)
    core.salvar_config(cfg)
    if ligou_docs:                       # acabou de ligar os documentos → indexa a pasta
        docs.reindexar_async(forcar=False)
    if "nome" in d:                      # nome definido → atualiza grafo + memória
        skills.definir_nome(cfg["nome"])
    out = core.estado()
    if avisos:
        out["avisos"] = avisos
    return jsonify(out)


@app.route("/api/config/restaurar", methods=["POST"])
def api_config_restaurar():
    """Devolve UMA seção das configurações ao padrão (nunca toca no token da API nem no cofre)."""
    secao = _corpo_json().get("secao")
    cfg = core.carregar_config()
    if ajustes.restaurar_secao(cfg, secao) is None:
        return jsonify({"ok": False, "erro": "seção desconhecida", "secoes": list(ajustes.SECOES)}), 400
    core.salvar_config(cfg)
    if secao == "privacidade" and cfg.get("ativo", True) is False:
        threading.Thread(target=core.descarregar_modelos, daemon=True).start()
    return jsonify({"ok": True, **core.estado()})


@app.route("/api/memoria")
def api_memoria():
    return jsonify(skills.carregar_mem())


@app.route("/api/abrir_pasta", methods=["POST"])
def api_abrir_pasta():
    try:
        os.startfile(core.BASE_DIR)          # abre o Explorer na pasta do agente
    except Exception:
        try:
            subprocess.Popen(["explorer", core.BASE_DIR])
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500
    return jsonify({"ok": True, "pasta": core.BASE_DIR})


# ── DOCUMENTOS (RAG local: perguntar sobre seus arquivos em /docs) ────────────
@app.route("/api/docs/status")
def api_docs_status():
    return jsonify(docs.status())


@app.route("/api/docs/reindexar", methods=["POST"])
def api_docs_reindexar():
    docs.reindexar_async(forcar=False)
    return jsonify({"ok": True})


@app.route("/api/docs/abrir", methods=["POST"])
def api_docs_abrir():
    os.makedirs(docs.DOCS_DIR, exist_ok=True)
    try:
        os.startfile(docs.DOCS_DIR)
    except Exception:
        try:
            subprocess.Popen(["explorer", docs.DOCS_DIR])
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500
    return jsonify({"ok": True, "pasta": docs.DOCS_DIR})


# ── BACKUP (exportar / restaurar memória, grafo e conversas) ──────────────────
_BACKUP_ARQS = ["config.json", "memoria.json", "conversas.json", "grafo.json", "observacoes.json",
                "memorias.json"]


@app.route("/api/backup/exportar")
def api_backup_exportar():
    import io
    import zipfile
    import datetime
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nome in _BACKUP_ARQS:
            caminho = os.path.join(core.BASE_DIR, nome)
            if os.path.isfile(caminho):
                z.write(caminho, nome)
    buf.seek(0)
    from flask import send_file
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"atlas-backup-{stamp}.zip")


@app.route("/api/backup/importar", methods=["POST"])
def api_backup_importar():
    import io
    import zipfile
    f = request.files.get("arquivo")
    if not f:
        return jsonify({"ok": False, "erro": "sem arquivo"}), 400
    try:
        with zipfile.ZipFile(io.BytesIO(f.read())) as z:
            nomes = set(z.namelist())
            arqs = {n: z.read(n) for n in _BACKUP_ARQS if n in nomes}   # lista branca: nada de path traversal
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 400

    # valida TUDO antes de gravar qualquer coisa (nada de restauração pela metade)
    cfg_atual = core.carregar_config()
    cfg_nova = None
    try:
        for nome, dados in arqs.items():
            if dados[:len(cofre.MAGIC)] == cofre.MAGIC:
                continue                               # cifrado pelo cofre: conferido abaixo
            obj = json.loads(dados.decode("utf-8"))
            if nome == "config.json":
                if not isinstance(obj, dict):
                    raise ValueError("config.json deve ser um objeto JSON")
                cfg_nova = obj
    except Exception as e:
        return jsonify({"ok": False, "erro": f"arquivo inválido: {e}"}), 400
    cifrados = [n for n, d in arqs.items() if d[:len(cofre.MAGIC)] == cofre.MAGIC]
    if "config.json" in cifrados:
        return jsonify({"ok": False, "erro": "config.json não pode estar cifrado"}), 400
    cfg_final = cfg_nova if cfg_nova is not None else cfg_atual
    if cifrados and not cfg_final.get("cripto"):
        return jsonify({"ok": False, "erro": "o backup tem arquivos cifrados, mas sem a configuração "
                                             "de criptografia correspondente"}), 400

    if cfg_nova is not None and cfg_atual.get("api_token"):
        cfg_nova["api_token"] = cfg_atual["api_token"]   # mantém as IAs conectadas funcionando
        arqs["config.json"] = json.dumps(cfg_nova, ensure_ascii=False, indent=2).encode("utf-8")
    for nome, dados in arqs.items():
        tmp = os.path.join(core.BASE_DIR, nome + ".tmp")
        with open(tmp, "wb") as out:
            out.write(dados)
        core.substituir_arquivo(tmp, os.path.join(core.BASE_DIR, nome))

    # senha/chave diferente (ou cripto mudou) → trava; o usuário desbloqueia com a senha do backup
    if cfg_nova is not None and (cfg_nova.get("cripto_salt") != cfg_atual.get("cripto_salt")
                                 or bool(cfg_nova.get("cripto")) != bool(cfg_atual.get("cripto"))):
        cofre.bloquear()
    return jsonify({"ok": True, "restaurados": sorted(arqs), "cofre": cofre.estado()})


# ── API DE MEMÓRIA (token pra interface mostrar/girar) ────────────────────────
@app.route("/api/memapi")
def api_memapi():
    if not memapi.host_local():
        return jsonify({"ok": False}), 403
    cfg = core.carregar_config()
    return jsonify({"ativa": cfg.get("api_ativa", True), "token": memapi.token(),
                    "url": f"http://{HOST}:{PORT}/v1",
                    "mcp": os.path.join(core.BASE_DIR, "mcp_atlas.py")})


@app.route("/api/memapi/girar", methods=["POST"])
def api_memapi_girar():
    if not memapi.host_local():
        return jsonify({"ok": False}), 403
    return jsonify({"ok": True, "token": memapi.girar_token()})


# ── COFRE (criptografia em repouso, protegida por senha) ──────────────────────
@app.route("/api/cofre/estado")
def api_cofre_estado():
    return jsonify(cofre.estado())


@app.route("/api/cofre/ativar", methods=["POST"])
def api_cofre_ativar():
    d = _corpo_json()
    ok = cofre.ativar(d.get("senha", ""))
    return jsonify({"ok": ok, **cofre.estado()})


@app.route("/api/cofre/desativar", methods=["POST"])
def api_cofre_desativar():
    d = _corpo_json()
    ok = cofre.desativar(d.get("senha", ""))
    return jsonify({"ok": ok, **cofre.estado()})


@app.route("/api/cofre/desbloquear", methods=["POST"])
def api_cofre_desbloquear():
    d = _corpo_json()
    ok = cofre.desbloquear(d.get("senha", ""))
    return jsonify({"ok": ok, **cofre.estado()})


@app.route("/api/cofre/bloquear", methods=["POST"])
def api_cofre_bloquear():
    cofre.bloquear()
    threading.Thread(target=core.descarregar_modelos, daemon=True).start()
    return jsonify({"ok": True, **cofre.estado()})


# ── LEMBRETES ─────────────────────────────────────────────────────────────────
@app.route("/api/lembretes")
def api_lembretes():
    return jsonify(lembretes.listar())


@app.route("/api/lembretes/<lid>", methods=["DELETE"])
def api_lembrete_excluir(lid):
    return jsonify({"ok": lembretes.excluir(lid)})


# ── CONVERSAS (chats salvos localmente) ───────────────────────────────────────
@app.route("/api/chats")
def api_chats():
    return jsonify(chats.listar())


@app.route("/api/chats/<cid>")
def api_chat_get(cid):
    c = chats.get(cid)
    return jsonify(c or {}), (200 if c else 404)


@app.route("/api/chats/novo", methods=["POST"])
def api_chat_novo():
    return jsonify({"id": chats.novo()})


@app.route("/api/chats/<cid>/atual", methods=["POST"])
def api_chat_atual(cid):
    chats.set_atual(cid)
    return jsonify({"ok": True})


@app.route("/api/chats/<cid>/renomear", methods=["POST"])
def api_chat_renomear(cid):
    d = _corpo_json()
    return jsonify({"ok": chats.renomear(cid, d.get("titulo", ""))})


@app.route("/api/chats/<cid>", methods=["DELETE"])
def api_chat_excluir(cid):
    return jsonify({"atual": chats.excluir(cid)})


def _resolver_mems(ids):
    """ids -> [{id, t, p}] (texto curto + projeto); ids que não existem mais ficam sem texto."""
    out = []
    for mid in ids:
        try:
            m = memstore.memoria_obter(mid)
            out.append({"id": mid, "t": m["content"][:160], "p": m.get("project")})
        except Exception:
            out.append({"id": mid, "t": "", "p": None})
    return out


@app.route("/api/projetos")
def api_projetos():
    """Lista leve de projetos para os seletores da interface ([] se o cofre estiver travado)."""
    try:
        return jsonify([{"id": p.get("id"), "name": p.get("name"), "parent": p.get("parent"), "depth": p.get("depth", 0)}
                        for p in memstore.projetos_listar()])
    except Exception:
        return jsonify([])


@app.route("/api/chats/<cid>/ctx", methods=["GET", "POST"])
def api_chat_ctx(cid):
    """Projeto ativo e memórias fixadas/excluídas do contexto, por conversa ou por projeto.
    POST {projeto?, acao: fixar|excluir|limpar, id, escopo: conversa|projeto}"""
    c = chats.get(cid)
    if c is None:
        return jsonify({"ok": False}), 404
    cfg = core.carregar_config()
    if request.method == "POST":
        d = _corpo_json()
        if "projeto" in d:
            chats.definir_ctx(cid, projeto=d.get("projeto") or None)
            c = chats.get(cid)
        acao, mid = d.get("acao"), d.get("id")
        if acao in ("fixar", "excluir", "limpar") and isinstance(mid, str) and mid:
            if d.get("escopo") == "projeto":
                pid = c.get("projeto")
                if not pid:
                    return jsonify({"ok": False, "erro": "sem projeto ativo"}), 400
                cur = (cfg.get("ctx_projeto") or {}).get(pid, {})
                fx = [i for i in cur.get("fixas", []) if i != mid]
                ex = [i for i in cur.get("excluidas", []) if i != mid]
                if acao == "fixar":
                    fx.append(mid)
                elif acao == "excluir":
                    ex.append(mid)
                cfg, _av = ajustes.aplicar(cfg, {"ctx_projeto": {pid: {"fixas": fx, "excluidas": ex}}})
                core.salvar_config(cfg)
            else:
                fx = [i for i in c.get("ctx_fixas", []) if i != mid]
                ex = [i for i in c.get("ctx_excluidas", []) if i != mid]
                if acao == "fixar":
                    fx.append(mid)
                elif acao == "excluir":
                    ex.append(mid)
                chats.definir_ctx(cid, fixas=fx, excluidas=ex)
            c = chats.get(cid)
    pid = c.get("projeto")
    proj = (cfg.get("ctx_projeto") or {}).get(pid or "", {}) or {}
    return jsonify({
        "ok": True, "projeto": pid,
        "conversa": {"fixas": _resolver_mems(c.get("ctx_fixas", [])), "excluidas": _resolver_mems(c.get("ctx_excluidas", []))},
        "projeto_regras": {"fixas": _resolver_mems(proj.get("fixas", [])), "excluidas": _resolver_mems(proj.get("excluidas", []))},
    })


@app.route("/api/fatos/confirmar", methods=["POST"])
def api_fatos_confirmar():
    """Grava um fato durável que o usuário confirmou. Recusa texto sensível (senha, token, cartão...)."""
    texto = _corpo_json().get("texto")
    if not isinstance(texto, str) or not skills.fato_valido(texto):
        return jsonify({"ok": False, "erro": "fato inválido ou com dado sensível"}), 400
    return jsonify({"ok": True, "novo": skills.salvar_fato(texto)})


@app.route("/api/chats/<cid>/regenerar", methods=["POST"])
def api_chat_regenerar(cid):
    return jsonify({"texto": chats.remover_ultima(cid)})


@app.route("/api/grafo")
def api_grafo():
    return jsonify(skills.carregar_grafo())


@app.route("/api/grafo/traduzir", methods=["POST"])
def api_grafo_traduzir():
    d = _corpo_json()
    lang = d.get("lang", "")
    if lang in ("en", "es"):
        threading.Thread(target=skills.grafo_traduzir, args=(lang,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/grafo/editar", methods=["POST"])
def api_grafo_editar():
    d = _corpo_json()
    a = d.get("acao")
    if a == "criar_no":
        skills.grafo_criar_no(d.get("label", ""), d.get("tipo", "tema"))
    elif a == "renomear_no":
        skills.grafo_renomear_no(d.get("chave", ""), d.get("label", ""))
    elif a == "tipo_no":
        skills.grafo_tipo_no(d.get("chave", ""), d.get("tipo", ""))
    elif a == "excluir_no":
        skills.grafo_excluir_no(d.get("chave", ""))
    elif a == "criar_aresta":
        skills.grafo_criar_aresta(d.get("de", ""), d.get("para", ""), d.get("rel", ""))
    elif a == "excluir_aresta":
        skills.grafo_excluir_aresta(d.get("de", ""), d.get("para", ""))
    elif a == "limpar":
        skills.grafo_limpar(bool(d.get("tambem_memoria")))
    else:
        return jsonify({"ok": False, "erro": "ação inválida"}), 400
    return jsonify(skills.carregar_grafo())


# ── EVENTOS (iniciativa / observação / status) via SSE ───────────────────────
@app.route("/eventos")
def eventos():
    q = skills.assinar()

    def stream():
        try:
            yield "data: " + json.dumps({"tipo": "status", "payload": "conectado"}) + "\n\n"
            while True:
                try:
                    tipo, payload = q.get(timeout=20)
                except queue.Empty:
                    yield ": keep-alive\n\n"       # detecta cliente que saiu (senão a thread fica presa)
                    continue
                yield "data: " + json.dumps({"tipo": tipo, "payload": payload}, ensure_ascii=False) + "\n\n"
        except GeneratorExit:
            pass
        finally:
            skills.desassinar(q)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── OLLAMA: iniciar / instalar direto da interface ───────────────────────────
@app.route("/api/ollama/start", methods=["POST"])
def ollama_start():
    ok = core.iniciar_ollama()
    return jsonify({"ok": ok, "online": core.ollama_online()})


@app.route("/api/ollama/delete", methods=["POST"])
def ollama_delete():
    d = _corpo_json()
    nome = (d.get("modelo") or "").strip()
    if not nome:
        return jsonify({"ok": False}), 400
    try:
        # descarrega da VRAM (se estiver) e remove de vez do disco
        try:
            requests.post(f"{core.OLLAMA}/api/generate", json={"model": nome, "keep_alive": 0}, timeout=10)
        except Exception:
            pass
        requests.delete(f"{core.OLLAMA}/api/delete", json={"model": nome}, timeout=30)
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500
    # se era o modelo ativo, troca pro próximo instalado (ou volta pro padrão)
    cfg = core.carregar_config()
    if cfg.get("modelo") == nome:
        instalados = core.listar_modelos()
        prox = next((m["nome"] for m in core.modelos_chat() if m["nome"] in instalados), None)
        cfg["modelo"] = prox or core.CONFIG_PADRAO["modelo"]
        core.salvar_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/ollama/install", methods=["POST"])
def ollama_install():
    def stream():
        import time
        import tempfile
        import threading as th
        import subprocess
        url = "https://ollama.com/download/OllamaSetup.exe"
        dest = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
        try:
            with _baixar_ok(requests.get(url, stream=True, timeout=60)) as r:
                total = int(r.headers.get("content-length", 0))
                feito = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(262144):
                        if chunk:
                            f.write(chunk)
                            feito += len(chunk)
                            yield (json.dumps({"baixando": feito, "total": total}) + "\n")
            yield (json.dumps({"status": "instalando em segundo plano…"}) + "\n")
            # instalação silenciosa (se o instalador ignorar os flags, abre a janela como reserva)
            try:
                subprocess.Popen([dest, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                                 creationflags=0x08000000)
            except Exception:
                subprocess.Popen([dest], creationflags=0x08000000)

            # quando o Ollama aparecer instalado, liga o motor sozinho
            def _esperar_e_ligar():
                for _ in range(120):
                    if core.achar_ollama():
                        core.iniciar_ollama()
                        return
                    time.sleep(2)
            th.Thread(target=_esperar_e_ligar, daemon=True).start()
            yield (json.dumps({"status": "instalando… a página reconhece sozinha"}) + "\n")
        except Exception as e:
            yield (json.dumps({"error": str(e)}) + "\n")

    return Response(stream(), mimetype="application/x-ndjson")


# ── TESSERACT OCR (instalar/desinstalar pelo winget — pra ler a tela) ─────────
def _winget():
    return shutil.which("winget") or ""


@app.route("/api/tesseract/install", methods=["POST"])
def tesseract_install():
    def stream():
        wg = _winget()
        if not wg:
            yield (json.dumps({"error": "winget não encontrado (Windows 10/11)"}) + "\n")
            return
        yield (json.dumps({"status": "instalando pelo winget (confirme o aviso do Windows)…"}) + "\n")
        try:
            p = subprocess.run([wg, "install", "--id", "UB-Mannheim.TesseractOCR", "-e",
                                "--accept-source-agreements", "--accept-package-agreements", "--silent"],
                               capture_output=True, text=True, timeout=900, creationflags=0x08000000)
            if core.achar_tesseract():
                yield (json.dumps({"status": "Tesseract instalado"}) + "\n")
            else:
                yield (json.dumps({"error": f"não concluído (winget {p.returncode})"}) + "\n")
        except Exception as e:
            yield (json.dumps({"error": str(e)}) + "\n")

    return Response(stream(), mimetype="application/x-ndjson")


@app.route("/api/tesseract/uninstall", methods=["POST"])
def tesseract_uninstall():
    wg = _winget()
    if not wg:
        return jsonify({"ok": False, "erro": "winget não encontrado"}), 400
    try:
        subprocess.run([wg, "uninstall", "--id", "UB-Mannheim.TesseractOCR", "-e",
                        "--accept-source-agreements", "--silent"],
                       capture_output=True, text=True, timeout=600, creationflags=0x08000000)
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500
    cfg = core.carregar_config()              # desinstalou → desativa a habilidade e fica desativada
    cfg["habilidades"]["tela"] = False
    core.salvar_config(cfg)
    return jsonify({"ok": not core.achar_tesseract()})


# ── BAIXAR MODELO ─────────────────────────────────────────────────────────────
@app.route("/api/wiki/baixar", methods=["POST"])
def api_wiki_baixar():
    """Baixa a Wikipédia offline (.zim) do Kiwix no idioma atual e indexa. Pra quem não quer net."""
    import re as _re
    cfg = core.carregar_config()
    lang = cfg.get("idioma", "pt") if cfg.get("idioma") in ("pt", "en", "es") else "pt"

    def stream():
        os.makedirs(core.WIKI_DIR, exist_ok=True)
        base = "https://download.kiwix.org/zim/wikipedia/"
        try:
            idx = _baixar_ok(requests.get(base, timeout=30)).text
        except Exception as e:
            yield (json.dumps({"error": f"sem acesso ao Kiwix: {e}"}) + "\n")
            return
        cands = []
        for pat in (f"wikipedia_{lang}_all_mini_", f"wikipedia_{lang}_all_nopic_",
                    f"wikipedia_{lang}_simple_all_nopic_"):
            ms = _re.findall(r'href="(' + _re.escape(pat) + r'[0-9-]+\.zim)"', idx)
            if ms:
                cands = sorted(ms)
                break
        if not cands:
            yield (json.dumps({"error": f"não achei Wikipédia offline em '{lang}'"}) + "\n")
            return
        arq = cands[-1]
        dest = os.path.join(core.WIKI_DIR, arq)
        yield (json.dumps({"status": f"baixando {arq}"}) + "\n")
        try:
            with _baixar_ok(requests.get(base + arq, stream=True, timeout=60)) as r:
                total = int(r.headers.get("content-length", 0))
                feito = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(524288):
                        if chunk:
                            f.write(chunk)
                            feito += len(chunk)
                            yield (json.dumps({"baixando": feito, "total": total}) + "\n")
            yield (json.dumps({"status": "indexando a Wikipédia…"}) + "\n")
            skills._zim = None
            skills.garantir_indice_wiki()
            yield (json.dumps({"status": "Wikipédia offline pronta"}) + "\n")
        except Exception as e:
            yield (json.dumps({"error": str(e)}) + "\n")

    return Response(stream(), mimetype="application/x-ndjson")


@app.route("/api/pull", methods=["POST"])
def api_pull():
    d = _corpo_json()
    nome = (d.get("modelo") or "").strip()
    if not nome:
        return jsonify({"ok": False}), 400

    def stream():
        try:
            r = requests.post(f"{core.OLLAMA}/api/pull", json={"model": nome, "stream": True},
                              stream=True, timeout=7200)
            for line in r.iter_lines():
                if line:
                    yield line + b"\n"
        except Exception as e:
            yield (json.dumps({"error": str(e)}) + "\n").encode()

    return Response(stream(), mimetype="application/x-ndjson")


# ── CHAT ──────────────────────────────────────────────────────────────────────
@app.route("/chat", methods=["POST"])
def chat():
    d = _corpo_json()
    if not isinstance(d.get("texto") or "", str):
        return jsonify({"ok": False, "erro": "texto inválido"}), 400
    texto = (d.get("texto") or "").strip()
    imagens = d.get("imagens") if isinstance(d.get("imagens"), list) else []     # imagens (base64)
    # arquivos anexados [{nome, b64}] — descarta itens que não sejam objetos
    anexos = [a for a in (d.get("anexos") if isinstance(d.get("anexos"), list) else []) if isinstance(a, dict)]
    if not texto and not imagens and not anexos:
        return jsonify({"ok": False}), 400

    cfg = core.carregar_config()
    idioma = cfg.get("idioma", "pt")
    if cofre.ligada() and not cofre.desbloqueado():
        msg = {"pt": "Desbloqueie o cofre (cadeado no topo) para conversar.",
               "en": "Unlock the vault (lock icon at the top) to chat.",
               "es": "Desbloquea la caja fuerte (candado arriba) para conversar."}.get(idioma, "")
        return Response(msg, mimetype="text/plain; charset=utf-8")

    modelo = core.modelo_atual(cfg)
    nome = cfg.get("nome", "você")
    cid = d.get("chat_id") if isinstance(d.get("chat_id"), str) and d.get("chat_id") else chats.listar()["atual"]

    if texto and not imagens and not anexos:
        conf = lembretes.detectar(texto, idioma)      # "me lembra disso amanhã"
        if conf:
            chats.adicionar(cid, texto, conf)
            return Response(conf, mimetype="text/plain; charset=utf-8")

    # imagens → precisa de um modelo de visão (usa o atual se for de visão, senão o primeiro instalado)
    imgs_b64 = [(i.split(",", 1)[-1] if isinstance(i, str) else "") for i in imagens][:4]
    imgs_b64 = [i for i in imgs_b64 if i]
    if imgs_b64:
        atual_visao = any(m["nome"] == modelo and m.get("visao") for m in core.CATALOGO_MODELOS)
        vm = modelo if atual_visao else core.primeiro_visao_instalado()
        if not vm:
            aviso = {"pt": "Para analisar imagens, instale um modelo de visão (Moondream ou LLaVA) no menu ⚙ → Modelo.",
                     "en": "To analyze images, install a vision model (Moondream or LLaVA) in Settings, Model.",
                     "es": "Para analizar imágenes, instala un modelo de visión (Moondream o LLaVA) en Ajustes, Modelo."}.get(idioma, "")
            return Response(aviso, mimetype="text/plain; charset=utf-8")
        modelo = vm

    # arquivos anexados → extrai texto e injeta no contexto desta resposta
    anexos_txt = ""
    for a in anexos[:5]:
        try:
            dados = base64.b64decode((a.get("b64") or "").split(",", 1)[-1])
        except Exception:
            continue
        txt = docs.extrair_texto(a.get("nome", ""), dados)
        if txt.strip():
            anexos_txt += f"\n\n[ARQUIVO: {a.get('nome', 'arquivo')}]\n{txt[:6000]}"

    chat_atual = chats.get(cid)
    projeto = (chat_atual or {}).get("projeto") or None
    perfil = ajustes.perfil_efetivo(cfg, modelo)
    cc = cfg.get("contexto") or {}
    orc = contexto.orcamento_chars(cfg, perfil)

    system = SYSTEM_BASE.format(nome=nome, idioma=INSTR_IDIOMA.get(cfg.get("idioma", "pt"), ""))
    system += contexto.instrucoes_texto(cfg, idioma, projeto)

    # contexto recuperado, em ordem de prioridade, dentro do orçamento da janela do modelo
    secoes, usadas, vistos = [], [], []
    if core.habilidade("memoria", cfg):
        fatos = [f for f in skills.carregar_mem().get("fatos", [])[-25:] if not contexto.sensivel(f)]
        fatos = contexto.deduplicar(fatos)
        if fatos:
            secoes.append(("fatos", "Fatos que você sabe:", "\n".join("- " + f for f in fatos)))
        usadas = contexto.selecionar_memorias(texto, projeto, cfg, chat_atual, orc)
        usadas = [u for u in usadas if not contexto.sensivel(u["content"])]
        # memórias que repetem um fato já listado não entram duas vezes
        usadas = [u for u in usadas if not any(contexto.parecido(u["content"], f) for f in fatos)]
        if usadas:
            secoes.append(("memorias", "Memórias relacionadas (use se for relevante):", contexto.linhas_memorias(usadas)))
        if cc.get("incluir_conversas", True):
            rec = chats.recall(texto, excluir_id=cid)      # acesso a TODAS as conversas
            if rec:
                secoes.append(("conversas", "De conversas anteriores (use só se for relevante):", rec))
    if core.habilidade("grafo", cfg):
        mapa = skills.resumo_grafo(texto)
        if mapa:
            secoes.append(("grafo", "Do seu grafo de conhecimento (use se ajudar):", mapa))
    if core.habilidade("documentos", cfg):
        docctx = docs.consultar(texto)
        if docctx:
            secoes.append(("docs", "DOS DOCUMENTOS DO USUÁRIO (responda com base nisto; cite o arquivo "
                           "entre colchetes quando útil; se não houver resposta aqui, diga que não achou):", docctx))
    if core.habilidade("wikipedia", cfg):
        wiki = skills.consultar_wiki(texto)
        if wiki:
            secoes.append(("wiki", "CONHECIMENTO DA WIKIPÉDIA (explique com suas palavras, não copie cru):", wiki))
    corpo, uso = contexto.ajustar_secoes(secoes, orc)
    if corpo:
        system += "\n\n" + corpo
        # só cita como "usada" a memória que realmente coube no prompt
        if "memorias" not in uso:
            usadas = []
    if core.habilidade("tela", cfg) and skills.precisa_tela(texto):
        skills.garantir_ocr_lang(cfg.get("idioma", "pt"))
        titulo, ocr = skills.ler_tela()
        system += ("\n\n[LEITURA DA TELA AGORA — responda baseado SÓ nisto, sem inventar:]\n"
                   f"Janela ativa: {titulo or '(desconhecida)'}\nTexto na tela (OCR):\n"
                   f"{ocr[:1500] or '(nada legível)'}")

    if anexos_txt:
        system += "\n\nARQUIVOS ANEXADOS PELO USUÁRIO (responda com base neles):" + anexos_txt

    ctx_msg = [{"id": u["id"], "t": u["content"][:160], "p": u.get("project"), "f": bool(u.get("pinned"))}
               for u in usadas][:8]
    info_ctx = {"mems": ctx_msg, "projeto": projeto, "orcamento": orc, "usado": sum(uso.values()),
                "ctx": perfil["num_ctx"]}

    msgs = [{"role": "system", "content": system}]
    hist_n = int(cc.get("hist_msgs", 6))
    for m in (chat_atual.get("mensagens", [])[-hist_n:] if (chat_atual and hist_n > 0) else []):
        msgs.append({"role": "user", "content": m["u"]})
        msgs.append({"role": "assistant", "content": m["a"]})

    conteudo = texto or ({"pt": "Analise o conteúdo anexado.", "en": "Analyze the attached content.",
                          "es": "Analiza el contenido adjunto."}.get(idioma) if (imgs_b64 or anexos_txt) else texto)
    um = {"role": "user", "content": conteudo}
    if imgs_b64:
        um["images"] = imgs_b64
    msgs.append(um)

    # o que vai pro histórico (não guardamos a imagem/arquivo cru, só um marcador)
    texto_salvar = texto or ("[imagem]" if imgs_b64 else ("[" + (anexos[0].get("nome", "arquivo")) + "]" if anexos else ""))

    skills.conversando.set()

    def stream():
        full = ""
        ka = -1 if cfg.get("ativo", True) else 0     # pausado: descarrega após responder
        opts = {"num_ctx": perfil["num_ctx"], "num_gpu": 99, "temperature": perfil["temperatura"]}
        if perfil.get("top_p") is not None:
            opts["top_p"] = perfil["top_p"]
        if perfil.get("max_tokens"):
            opts["num_predict"] = perfil["max_tokens"]
        if perfil.get("seed") is not None:
            opts["seed"] = perfil["seed"]
        body = {"model": modelo, "messages": msgs, "stream": True, "keep_alive": ka, "options": opts}
        if core.info_modelo(modelo).get("think") is False or "qwen3" in modelo:
            body["think"] = False
        filtro = contexto.FiltroPensamento()
        try:
            r = requests.post(f"{core.OLLAMA}/api/chat", json=body, stream=True, timeout=300)
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if data.get("error"):                     # ex.: modelo não encontrado no Ollama
                    yield f"\n(erro do modelo: {data['error']})"
                    break
                tok = (data.get("message") or {}).get("content", "")
                if tok:
                    tok = filtro.push(tok)               # tira <think>...</think> dos modelos de raciocínio
                    if tok:
                        full += tok
                        yield tok
                if data.get("done"):
                    break
        except Exception as e:
            yield f"\n(erro ao falar com o modelo: {e})"
        finally:
            skills.conversando.clear()
        resto = filtro.fim()
        if resto:
            full += resto
            yield resto
        full = full.strip()
        if full:
            chats.adicionar(cid, texto_salvar, full, ctx=ctx_msg)
            def _pos():
                if not core.carregar_config().get("ativo", True):     # sistema pausado: não aprende
                    return
                if not texto:                                  # imagem/arquivo sozinho: nada a aprender
                    return
                if core.habilidade("memoria"):
                    skills.extrair_fato(texto, full)          # pode descobrir/definir o nome
                if core.habilidade("grafo"):
                    quem = core.carregar_config().get("nome", "você")   # nome já resolvido
                    skills.tecer(f"{quem}: {texto}")
            threading.Thread(target=_pos, daemon=True).start()

    resp = Response(stream(), mimetype="text/plain; charset=utf-8")
    # quais memórias entraram no prompt (a interface mostra, colapsado, sob a resposta)
    resp.headers["X-Atlas-Contexto"] = urllib.parse.quote(json.dumps(info_ctx, ensure_ascii=False, separators=(",", ":")))
    resp.headers["Access-Control-Expose-Headers"] = "X-Atlas-Contexto"
    return resp


def iniciar():
    memapi.token()                           # garante o token da API (o MCP lê do config.json)
    skills.iniciar()
    lembretes.iniciar()
    bandeja.iniciar()
    if core.habilidade("documentos") and cofre.disponivel():
        docs.reindexar_async(forcar=False)   # indexa os documentos em segundo plano no start


if __name__ == "__main__":
    iniciar()
    print(f">> agente local em http://{HOST}:{PORT}/  (apenas local)")
    app.run(host=HOST, port=PORT, threaded=True)
