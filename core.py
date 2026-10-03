"""
core — núcleo do agente local configurável.
Tudo roda 100% na máquina (Ollama em 127.0.0.1). Nada sai pra rede.

Responsabilidades:
- carregar/salvar a configuração (modelo escolhido + habilidades ligadas/desligadas)
- falar com o Ollama (status, lista de modelos, baixar modelo, chat)
"""
import os
import time
import json
import shutil
import threading
import subprocess
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
MEM_FILE = os.path.join(BASE_DIR, "memoria.json")
CONVERSAS_FILE = os.path.join(BASE_DIR, "conversas.json")
GRAFO_FILE = os.path.join(BASE_DIR, "grafo.json")
OBS_FILE = os.path.join(BASE_DIR, "observacoes.json")
WIKI_DIR = os.path.join(BASE_DIR, "wiki")          # coloque um .zim aqui pra ativar a Wikipédia
PRINTS_DIR = os.path.join(BASE_DIR, "prints")      # screenshots salvos da observação de tela
OLLAMA = "http://127.0.0.1:11434"

# ── CATÁLOGO DE MODELOS ───────────────────────────────────────────────────────
# Cada item: nome (tag do Ollama), rotulo, usos (geral|codigo|raciocinio|visao|leve|embed),
# gb (download aprox.), ram (RAM aprox. em GB para rodar), vram (texto legado), ctx (janela
# padrão sugerida), ctx_max (máximo do modelo), temp (temperatura padrão), tipo (chat|embed),
# visao (aceita imagens) e think=False (desliga o "pensar" explícito, ex. Qwen3).
# Tamanhos e RAM são aproximações para ajudar na escolha, não garantias.
def _m(nome, rotulo, usos, gb, ram, ctx_max, temp=0.7, **extra):
    d = {"nome": nome, "rotulo": rotulo, "usos": usos, "gb": gb, "ram": ram,
         "vram": f"~{gb:.1f} GB", "ctx": min(4096, ctx_max), "ctx_max": ctx_max, "temp": temp,
         "tipo": "chat", "provedor": "ollama"}
    d.update(extra)
    return d


CATALOGO_MODELOS = [
    _m("qwen2.5:0.5b", "Qwen2.5 0.5B", ["leve", "geral"], 0.4, 2, 32768),
    _m("qwen2.5:1.5b", "Qwen2.5 1.5B", ["geral", "leve"], 1.0, 3, 32768),
    _m("llama3.2:1b", "Llama 3.2 1B", ["leve", "geral"], 1.3, 3, 131072),
    _m("gemma3:1b", "Gemma 3 1B", ["leve", "geral"], 0.8, 3, 32768),
    _m("qwen3:1.7b", "Qwen3 1.7B", ["leve", "raciocinio"], 1.4, 4, 40960, 0.6, think=False),
    _m("llama3.2:3b", "Llama 3.2 3B", ["geral", "leve"], 2.0, 4, 131072),
    _m("qwen2.5:3b", "Qwen2.5 3B", ["geral"], 1.9, 4, 32768),
    _m("phi4-mini", "Phi-4 mini", ["raciocinio", "geral"], 2.5, 5, 131072),
    _m("qwen3:4b", "Qwen3 4B", ["geral", "raciocinio"], 2.5, 5, 40960, 0.6, think=False),
    _m("gemma3:4b", "Gemma 3 4B", ["geral", "visao"], 3.3, 6, 131072, visao=True),
    _m("mistral:7b", "Mistral 7B", ["geral"], 4.4, 8, 32768),
    _m("llama3.1:8b", "Llama 3.1 8B", ["geral"], 4.9, 8, 131072),
    _m("qwen3:8b", "Qwen3 8B", ["geral", "raciocinio"], 5.2, 9, 40960, 0.6, think=False),
    _m("gemma3:12b", "Gemma 3 12B", ["geral", "visao"], 8.1, 12, 131072, visao=True),
    _m("qwen3:14b", "Qwen3 14B", ["geral", "raciocinio"], 9.3, 14, 40960, 0.6, think=False),
    _m("deepseek-r1:1.5b", "DeepSeek-R1 1.5B", ["raciocinio", "leve"], 1.1, 3, 131072, 0.6),
    _m("deepseek-r1:7b", "DeepSeek-R1 7B", ["raciocinio"], 4.7, 8, 131072, 0.6),
    _m("deepseek-r1:8b", "DeepSeek-R1 8B", ["raciocinio"], 5.2, 9, 131072, 0.6),
    _m("qwen2.5-coder:1.5b", "Qwen2.5 Coder 1.5B", ["codigo", "leve"], 1.0, 3, 32768, 0.2),
    _m("qwen2.5-coder:7b", "Qwen2.5 Coder 7B", ["codigo"], 4.7, 8, 32768, 0.2),
    _m("moondream", "Moondream (visão)", ["visao", "leve"], 1.7, 4, 2048, visao=True, ctx=2048),
    _m("llava:7b", "LLaVA 7B (visão)", ["visao"], 4.7, 8, 32768, visao=True),
    _m("llama3.2-vision:11b", "Llama 3.2 Vision 11B", ["visao"], 7.8, 12, 131072, visao=True),
    _m("nomic-embed-text", "Nomic Embed Text", ["embed"], 0.27, 1, 8192, 0.0, tipo="embed", ctx=2048),
    _m("mxbai-embed-large", "mxbai Embed Large", ["embed"], 0.67, 2, 512, 0.0, tipo="embed", ctx=512),
    _m("bge-m3", "BGE-M3", ["embed"], 1.2, 3, 8192, 0.0, tipo="embed", ctx=2048),
]

# Provedores de modelo. Hoje só o Ollama local é suportado. Para adicionar outro (nuvem, LM Studio...):
# 1) registre aqui, 2) marque os itens do catálogo com "provedor", 3) implemente o cliente de chat
# no server.py. Chaves de API NUNCA vão no catálogo: ficam no cofre/config do usuário.
PROVEDORES = {"ollama": {"nome": "Ollama (local)", "local": True, "url": "http://127.0.0.1:11434"}}


def modelos_chat() -> list:
    return [m for m in CATALOGO_MODELOS if m.get("tipo", "chat") == "chat"]


def modelos_embed() -> list:
    return [m for m in CATALOGO_MODELOS if m.get("tipo") == "embed"]


def info_modelo(nome: str) -> dict:
    return next((m for m in CATALOGO_MODELOS if m["nome"] == nome), {})


CONFIG_PADRAO = {
    "nome": "você",
    "idioma": "pt",                        # "pt" | "en" | "es"
    "tema": "escuro",                      # "escuro" | "claro"
    "ativo": True,                         # interruptor mestre — desligado pausa tudo (prints, etc.)
    "provedor": "ollama",                  # só "ollama" por enquanto (ver PROVEDORES)
    "modelo": "qwen2.5:1.5b",              # modelo ativo (um do catálogo, tipo chat)
    "embed": "nomic-embed-text",
    "num_ctx": 4096,                       # janela de contexto padrão (tokens)
    "obs_intervalo": 60,                   # segundos entre prints/leituras de tela
    "iniciativa_modo": "dinamico",         # "dinamico" (a IA decide) | "intervalo" (a cada X min)
    "iniciativa_intervalo": 10,            # minutos, quando modo = intervalo
    "iniciar_com_windows": False,          # subir o agente junto com o Windows
    "api_ativa": True,                     # API de memória /v1 (outras IAs, com token)
    "habilidades": {
        "memoria":    True,    # lembra de fatos e do histórico (local)
        "tela":       False,   # observa a tela por OCR
        "grafo":      False,   # monta um grafo de conhecimento do que vê/conversa
        "iniciativa": False,   # fala sozinho quando faz sentido
        "wikipedia":  False,   # consulta a Wikipédia offline (precisa de um .zim em /wiki)
        "documentos": False,   # responde com base nos seus arquivos em /docs (RAG local)
    },
    # ── geração: None = usar o perfil/padrão do modelo ──
    "geracao": {"temperatura": None, "top_p": None, "max_tokens": None, "seed": None},
    "perfis_modelo": {},                   # {"qwen3:8b": {"num_ctx": 8192, "temperatura": 0.5}}
    # ── memória e contexto ──
    "contexto": {
        "ctx_pct": 35,                     # % da janela reservada ao contexto recuperado
        "max_memorias": 6,                 # máximo de memórias injetadas por resposta
        "recencia_dias": 30,               # meia-vida da recência no ranking
        "incluir_conversas": True,         # recall de conversas anteriores
        "hist_msgs": 6,                    # pares pergunta/resposta recentes enviados
        "fatos_modo": "perguntar",         # "perguntar" | "automatico" | "desligado"
    },
    # ── personalidade ──
    "instrucoes": {"preset": "padrao", "extra": "", "personalizados": []},
    "instrucoes_projeto": {},              # {"id-do-projeto": "texto"}
    "ctx_projeto": {},                     # {"id-do-projeto": {"fixas": [ids], "excluidas": [ids]}}
}

_ANINHADOS = ("geracao", "contexto", "instrucoes")

_lock = threading.Lock()


def substituir_arquivo(tmp: str, destino: str, tentativas: int = 60):
    """os.replace com novas tentativas. No Windows o replace falha com PermissionError
    enquanto outra thread está lendo o arquivo de destino; a leitura dura milissegundos."""
    for i in range(tentativas):
        try:
            os.replace(tmp, destino)
            return
        except PermissionError:
            if i == tentativas - 1:
                raise
            time.sleep(0.02)


def abrir_leitura(caminho: str, modo: str = "rb", tentativas: int = 6, **kw):
    """open() de leitura que repete se o arquivo estiver trocado/bloqueado naquele instante
    (Windows). FileNotFoundError continua sendo propagado na hora."""
    for i in range(tentativas):
        try:
            return open(caminho, modo, **kw)
        except PermissionError:
            if i == tentativas - 1:
                raise
            time.sleep(0.02)


# Campos que nunca devem sair pela interface /api/estado (segredos do cofre e da API)
_CAMPOS_SECRETOS = ("api_token", "cripto_salt", "cripto_verif")


def carregar_config() -> dict:
    try:
        with abrir_leitura(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    # mescla com o padrão pra nunca faltar chave (ignora chaves legadas)
    legado = {"habilidades", "modelos", "modelo_modo"}
    mesclado = json.loads(json.dumps(CONFIG_PADRAO))
    mesclado.update({k: v for k, v in cfg.items() if k not in legado})
    if isinstance(cfg.get("habilidades"), dict):
        mesclado["habilidades"].update(cfg["habilidades"])
    for k in _ANINHADOS:                                   # blocos novos: mescla com o padrão
        if isinstance(cfg.get(k), dict):
            mesclado[k].update(cfg[k])
    for k in ("perfis_modelo", "instrucoes_projeto", "ctx_projeto"):
        mesclado[k] = cfg[k] if isinstance(cfg.get(k), dict) else {}
    if mesclado.get("modelo") not in [m["nome"] for m in modelos_chat()]:
        mesclado["modelo"] = CONFIG_PADRAO["modelo"]
    return mesclado


def config_publica(cfg: dict) -> dict:
    """Cópia da config sem token da API nem material do cofre (segura para a interface)."""
    return {k: v for k, v in cfg.items() if k not in _CAMPOS_SECRETOS}


def salvar_config(cfg: dict) -> dict:
    """Grava a config de forma atômica (arquivo temporário + os.replace), para que
    uma leitura concorrente nunca veja o arquivo pela metade (o que perderia o token)."""
    with _lock:
        tmp = f"{CONFIG_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            substituir_arquivo(tmp, CONFIG_FILE)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return cfg


def set_autostart(ligar: bool) -> bool:
    """Cria/remove o atalho na pasta Inicializar do Windows (sobe o servidor no login)."""
    import sys
    import subprocess
    startup = os.path.join(os.environ.get("APPDATA", ""),
                           r"Microsoft\Windows\Start Menu\Programs\Startup")
    lnk = os.path.join(startup, "Agente Local.lnk")
    if ligar:
        pyw = sys.executable
        if pyw.lower().endswith("python.exe"):
            pyw = pyw[:-10] + "pythonw.exe"
        alvo = os.path.join(BASE_DIR, "server.py")
        ps = (f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut("{lnk}");'
              f'$s.TargetPath="{pyw}";$s.Arguments=\'"{alvo}"\';'
              f'$s.WorkingDirectory="{BASE_DIR}";$s.Save()')
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           creationflags=0x08000000, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return False
    else:
        try:
            os.remove(lnk)
        except Exception:
            pass
    return ligar


def modelo_atual(cfg: dict = None) -> str:
    cfg = cfg or carregar_config()
    return cfg.get("modelo") or CONFIG_PADRAO["modelo"]


def habilidade(nome: str, cfg: dict = None) -> bool:
    cfg = cfg or carregar_config()
    return bool(cfg.get("habilidades", {}).get(nome, False))


# ── OLLAMA ────────────────────────────────────────────────────────────────────
_NO_WIN = 0x08000000


def ollama_online() -> bool:
    try:
        requests.get(f"{OLLAMA}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def achar_ollama() -> str:
    cands = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", "ollama.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return shutil.which("ollama") or ""


def achar_tesseract() -> str:
    cands = [
        os.path.join(os.environ.get("ProgramFiles", ""), "Tesseract-OCR", "tesseract.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Tesseract-OCR", "tesseract.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return shutil.which("tesseract") or ""


def primeiro_visao_instalado() -> str:
    """Nome do primeiro modelo de visão instalado (pra analisar imagens). '' se nenhum."""
    inst = listar_modelos()
    for m in modelos_chat():
        if m.get("visao") and (m["nome"] in inst or (m["nome"] + ":latest") in inst):
            return m["nome"]
    return ""


def descarregar_modelos():
    """Tira da memória todos os modelos carregados (libera o llama-server)."""
    try:
        carregados = [m["name"] for m in requests.get(f"{OLLAMA}/api/ps", timeout=10).json().get("models", [])]
    except Exception:
        carregados = []
    for m in carregados:
        try:
            requests.post(f"{OLLAMA}/api/generate", json={"model": m, "keep_alive": 0}, timeout=20)
        except Exception:
            pass


def iniciar_ollama() -> bool:
    """Sobe o `ollama serve` se estiver instalado. Retorna True se ficou online."""
    if ollama_online():
        return True
    exe = achar_ollama()
    if not exe:
        return False
    env = dict(os.environ)
    env["OLLAMA_FLASH_ATTENTION"] = "1"
    env["OLLAMA_KV_CACHE_TYPE"] = "q8_0"
    try:
        subprocess.Popen([exe, "serve"], creationflags=_NO_WIN, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    for _ in range(24):
        if ollama_online():
            return True
        time.sleep(0.5)
    return ollama_online()


def listar_modelos() -> list:
    try:
        return [m["name"] for m in requests.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models", [])]
    except Exception:
        return []


def modelo_instalado(nome: str) -> bool:
    instalados = listar_modelos()
    return nome in instalados or (nome + ":latest") in instalados


def estado() -> dict:
    """Resumo pra interface saber o que está pronto."""
    import ajustes                        # import tardio (ajustes importa o core)
    cfg = carregar_config()
    online = ollama_online()
    instalados = listar_modelos() if online else []

    def inst(n):
        return online and (n in instalados or (n + ":latest") in instalados)

    catalogo = [{**m, "instalado": inst(m["nome"]),
                 "ativo": m["nome"] == (cfg.get("embed") if m.get("tipo") == "embed" else cfg.get("modelo"))}
                for m in CATALOGO_MODELOS]
    try:
        wiki_pronto = any(f.lower().endswith(".zim") for f in os.listdir(WIKI_DIR))
    except Exception:
        wiki_pronto = False
    return {
        "ollama_online": online,
        "ollama_instalado": online or bool(achar_ollama()),
        "tesseract_instalado": bool(achar_tesseract()),
        "wiki_pronto": wiki_pronto,
        "config": config_publica(cfg),
        "catalogo": catalogo,
        "modelo_atual": modelo_atual(cfg),
        "instalados": instalados,
        "provedores": PROVEDORES,
        "ajustes": ajustes.meta(),
    }
