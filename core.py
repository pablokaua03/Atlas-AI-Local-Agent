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

# Configuração padrão (criada no primeiro uso)
CATALOGO_MODELOS = [
    {"nome": "qwen2.5:1.5b", "rotulo": "Qwen2.5 1.5B", "vram": "~1.0 GB"},
    {"nome": "llama3.2:3b",  "rotulo": "Llama 3.2 3B",  "vram": "~2.0 GB"},
    {"nome": "qwen2.5:3b",   "rotulo": "Qwen2.5 3B",    "vram": "~2.0 GB"},
    {"nome": "qwen3:4b",     "rotulo": "Qwen3 4B",      "vram": "~2.5 GB"},
    {"nome": "qwen3:8b",     "rotulo": "Qwen3 8B",      "vram": "~5.2 GB"},
    {"nome": "llama3.1:8b",  "rotulo": "Llama 3.1 8B",  "vram": "~4.9 GB"},
]

CONFIG_PADRAO = {
    "nome": "você",
    "idioma": "pt",                        # "pt" | "en" | "es"
    "tema": "escuro",                      # "escuro" | "claro"
    "ativo": True,                         # interruptor mestre — desligado pausa tudo (prints, etc.)
    "modelo": "qwen2.5:1.5b",              # modelo ativo (um do catálogo)
    "embed": "nomic-embed-text",
    "num_ctx": 4096,
    "obs_intervalo": 60,                   # segundos entre prints/leituras de tela
    "iniciativa_modo": "dinamico",         # "dinamico" (a IA decide) | "intervalo" (a cada X min)
    "iniciativa_intervalo": 10,            # minutos, quando modo = intervalo
    "iniciar_com_windows": False,          # subir o agente junto com o Windows
    "habilidades": {
        "memoria":    True,    # lembra de fatos e do histórico (local)
        "tela":       False,   # observa a tela por OCR
        "grafo":      False,   # monta um grafo de conhecimento do que vê/conversa
        "iniciativa": False,   # fala sozinho quando faz sentido
        "wikipedia":  False,   # consulta a Wikipédia offline (precisa de um .zim em /wiki)
    },
}

_lock = threading.Lock()


def carregar_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    # mescla com o padrão pra nunca faltar chave (ignora chaves legadas)
    legado = {"habilidades", "modelos", "modelo_modo"}
    mesclado = json.loads(json.dumps(CONFIG_PADRAO))
    mesclado.update({k: v for k, v in cfg.items() if k not in legado})
    if "habilidades" in cfg:
        mesclado["habilidades"].update(cfg["habilidades"])
    if mesclado.get("modelo") not in [m["nome"] for m in CATALOGO_MODELOS]:
        mesclado["modelo"] = CONFIG_PADRAO["modelo"]
    return mesclado


def salvar_config(cfg: dict) -> dict:
    with _lock:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
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
    cfg = carregar_config()
    online = ollama_online()
    instalados = listar_modelos() if online else []

    def inst(n):
        return online and (n in instalados or (n + ":latest") in instalados)

    catalogo = [{**m, "instalado": inst(m["nome"]), "ativo": m["nome"] == cfg.get("modelo")}
                for m in CATALOGO_MODELOS]
    try:
        wiki_pronto = any(f.lower().endswith(".zim") for f in os.listdir(WIKI_DIR))
    except Exception:
        wiki_pronto = False
    return {
        "ollama_online": online,
        "ollama_instalado": online or bool(achar_ollama()),
        "wiki_pronto": wiki_pronto,
        "config": cfg,
        "catalogo": catalogo,
        "modelo_atual": modelo_atual(cfg),
        "instalados": instalados,
    }
