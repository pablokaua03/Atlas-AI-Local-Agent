"""
server — interface web local do agente configurável.
127.0.0.1:5005. Escolhe modelo (leve/completo), liga/desliga habilidades, conversa.
Nada sai da máquina.
"""
import os
import json
import queue
import ctypes
import threading
import subprocess
from ctypes import wintypes

import requests
from flask import Flask, request, Response, send_from_directory, jsonify

import core
import skills
import chats
import docs

WEB_DIR = os.path.join(core.BASE_DIR, "web")
HOST, PORT = "127.0.0.1", 5005

app = Flask(__name__, static_folder=None)

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


# ── PÁGINAS ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/grafo")
def grafo_page():
    return send_from_directory(WEB_DIR, "grafo.html")


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
    return jsonify(core.estado())


@app.route("/api/config", methods=["POST"])
def api_config():
    d = request.get_json(force=True, silent=True) or {}
    cfg = core.carregar_config()
    if d.get("modelo") in [m["nome"] for m in core.CATALOGO_MODELOS]:
        cfg["modelo"] = d["modelo"]
    if isinstance(d.get("obs_intervalo"), (int, float)):
        cfg["obs_intervalo"] = max(15, min(900, int(d["obs_intervalo"])))
    if d.get("iniciativa_modo") in ("dinamico", "intervalo"):
        cfg["iniciativa_modo"] = d["iniciativa_modo"]
    if isinstance(d.get("iniciativa_intervalo"), (int, float)):
        cfg["iniciativa_intervalo"] = max(1, min(180, int(d["iniciativa_intervalo"])))
    if "nome" in d:
        cfg["nome"] = (str(d["nome"]).strip()[:40] or "você")
    if d.get("idioma") in ("pt", "en", "es"):
        cfg["idioma"] = d["idioma"]
    if d.get("tema") in ("claro", "escuro"):
        cfg["tema"] = d["tema"]
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
    return jsonify(core.estado())


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
_BACKUP_ARQS = ["config.json", "memoria.json", "conversas.json", "grafo.json", "observacoes.json"]


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
            restaurados = []
            for nome in _BACKUP_ARQS:                 # só nomes da lista branca (nada de path traversal)
                if nome in nomes:
                    dados = z.read(nome)
                    json.loads(dados.decode("utf-8"))  # valida que é JSON antes de gravar
                    with open(os.path.join(core.BASE_DIR, nome), "wb") as out:
                        out.write(dados)
                    restaurados.append(nome)
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 400
    return jsonify({"ok": True, "restaurados": restaurados})


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
    d = request.get_json(force=True, silent=True) or {}
    return jsonify({"ok": chats.renomear(cid, d.get("titulo", ""))})


@app.route("/api/chats/<cid>", methods=["DELETE"])
def api_chat_excluir(cid):
    return jsonify({"atual": chats.excluir(cid)})


@app.route("/api/grafo")
def api_grafo():
    return jsonify(skills.carregar_grafo())


@app.route("/api/grafo/traduzir", methods=["POST"])
def api_grafo_traduzir():
    d = request.get_json(force=True, silent=True) or {}
    lang = d.get("lang", "")
    if lang in ("en", "es"):
        threading.Thread(target=skills.grafo_traduzir, args=(lang,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/grafo/editar", methods=["POST"])
def api_grafo_editar():
    d = request.get_json(force=True, silent=True) or {}
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
                tipo, payload = q.get()
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
    d = request.get_json(force=True, silent=True) or {}
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
        prox = next((m["nome"] for m in core.CATALOGO_MODELOS if m["nome"] in instalados), None)
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
            with requests.get(url, stream=True, timeout=60) as r:
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
            idx = requests.get(base, timeout=30).text
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
            with requests.get(base + arq, stream=True, timeout=60) as r:
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
    d = request.get_json(force=True, silent=True) or {}
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
    d = request.get_json(force=True, silent=True) or {}
    texto = (d.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False}), 400

    cfg = core.carregar_config()
    modelo = core.modelo_atual(cfg)
    nome = cfg.get("nome", "você")
    cid = d.get("chat_id") or chats.listar()["atual"]

    system = SYSTEM_BASE.format(nome=nome, idioma=INSTR_IDIOMA.get(cfg.get("idioma", "pt"), ""))
    if core.habilidade("memoria", cfg):
        fatos = skills.carregar_mem().get("fatos", [])
        if fatos:
            system += "\n\nFatos que você sabe:\n" + "\n".join("- " + f for f in fatos[-25:])
        rec = chats.recall(texto, excluir_id=cid)      # acesso a TODAS as conversas
        if rec:
            system += "\n\nDe conversas anteriores (use só se for relevante):\n" + rec
    if core.habilidade("grafo", cfg):
        mapa = skills.resumo_grafo(texto)
        if mapa:
            system += "\n\nDo seu grafo de conhecimento (use se ajudar):\n" + mapa
    if core.habilidade("wikipedia", cfg):
        wiki = skills.consultar_wiki(texto)
        if wiki:
            system += "\n\nCONHECIMENTO DA WIKIPÉDIA (explique com suas palavras, não copie cru):\n" + wiki
    if core.habilidade("documentos", cfg):
        docctx = docs.consultar(texto)
        if docctx:
            system += ("\n\nDOS DOCUMENTOS DO USUÁRIO (responda com base nisto; cite o arquivo "
                       "entre colchetes quando útil; se não houver resposta aqui, diga que não achou):\n" + docctx)
    if core.habilidade("tela", cfg) and skills.precisa_tela(texto):
        skills.garantir_ocr_lang(cfg.get("idioma", "pt"))
        titulo, ocr = skills.ler_tela()
        system += ("\n\n[LEITURA DA TELA AGORA — responda baseado SÓ nisto, sem inventar:]\n"
                   f"Janela ativa: {titulo or '(desconhecida)'}\nTexto na tela (OCR):\n"
                   f"{ocr[:1500] or '(nada legível)'}")

    chat_atual = chats.get(cid)
    msgs = [{"role": "system", "content": system}]
    for m in (chat_atual.get("mensagens", [])[-6:] if chat_atual else []):
        msgs.append({"role": "user", "content": m["u"]})
        msgs.append({"role": "assistant", "content": m["a"]})
    msgs.append({"role": "user", "content": texto})

    skills.conversando.set()

    def stream():
        full = ""
        ka = -1 if cfg.get("ativo", True) else 0     # pausado: descarrega após responder
        body = {"model": modelo, "messages": msgs, "stream": True, "keep_alive": ka,
                "options": {"num_ctx": cfg.get("num_ctx", 4096), "num_gpu": 99}}
        if "qwen3" in modelo:
            body["think"] = False
        try:
            r = requests.post(f"{core.OLLAMA}/api/chat", json=body, stream=True, timeout=300)
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                tok = data.get("message", {}).get("content", "")
                if tok:
                    full += tok
                    yield tok
                if data.get("done"):
                    break
        except Exception as e:
            yield f"\n(erro ao falar com o modelo: {e})"
        finally:
            skills.conversando.clear()
        full = full.strip()
        if full:
            chats.adicionar(cid, texto, full)
            def _pos():
                if not core.carregar_config().get("ativo", True):     # sistema pausado: não aprende
                    return
                if core.habilidade("memoria"):
                    skills.extrair_fato(texto, full)          # pode descobrir/definir o nome
                if core.habilidade("grafo"):
                    quem = core.carregar_config().get("nome", "você")   # nome já resolvido
                    skills.tecer(f"{quem}: {texto}")
            threading.Thread(target=_pos, daemon=True).start()

    return Response(stream(), mimetype="text/plain; charset=utf-8")


def iniciar():
    skills.iniciar()
    if core.habilidade("documentos"):     # indexa os documentos em segundo plano no start
        docs.reindexar_async(forcar=False)


if __name__ == "__main__":
    iniciar()
    print(f">> agente local em http://{HOST}:{PORT}/  (apenas local)")
    app.run(host=HOST, port=PORT, threaded=True)
