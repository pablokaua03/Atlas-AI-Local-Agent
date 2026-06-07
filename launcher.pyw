"""
launcher — painel com botões pra ligar/desligar/abrir o Agente Local.
Roda sem console (pythonw). Dê dois cliques no atalho da área de trabalho.
"""
import os
import sys
import time
import threading
import subprocess
import urllib.request
import webbrowser
import tkinter as tk

BASE = os.path.dirname(os.path.abspath(__file__))
EXE = sys.executable
PYW = EXE[:-10] + "pythonw.exe" if EXE.lower().endswith("python.exe") else EXE
URL = "http://127.0.0.1:5005/"
OLLAMA = "http://127.0.0.1:11434/api/tags"
NO_WINDOW = 0x08000000

BG = "#0d0f13"; PANEL = "#161a21"; LINE = "#262c38"
TXT = "#e6e9ef"; DIM = "#8b94a3"; ACCENT = "#7c9bff"; ROXO = "#c08bff"
GOOD = "#5ad1c2"; BAD = "#ff6b6b"; WARN = "#ffd166"

server_proc = None


def _up(url, t=1.0):
    try:
        urllib.request.urlopen(url, timeout=t)
        return True
    except Exception:
        return False


def _achar_ollama():
    cands = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama app.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", "ollama.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


class App:
    def __init__(self, root):
        self.root = root
        self.srv = False
        self.oll = False
        root.title("Agente Local")
        root.configure(bg=BG)
        root.geometry("380x280")
        root.resizable(False, False)
        try:
            root.iconbitmap(os.path.join(BASE, "icon.ico"))
        except Exception:
            pass

        topo = tk.Frame(root, bg=BG)
        topo.pack(pady=(20, 2))
        tk.Label(topo, text="◆", bg=BG, fg=ACCENT, font=("Segoe UI", 20)).pack(side="left", padx=(0, 8))
        tk.Label(topo, text="Agente Local", bg=BG, fg=TXT, font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(root, text="100% na sua máquina · nada sai daqui", bg=BG, fg=DIM,
                 font=("Segoe UI", 9)).pack()

        st = tk.Frame(root, bg=BG)
        st.pack(pady=14)
        self.lbl_srv = tk.Label(st, text="●  servidor", bg=BG, fg=DIM, font=("Segoe UI", 10))
        self.lbl_srv.grid(row=0, column=0, sticky="w", padx=8, pady=2)
        self.lbl_oll = tk.Label(st, text="●  Ollama", bg=BG, fg=DIM, font=("Segoe UI", 10))
        self.lbl_oll.grid(row=1, column=0, sticky="w", padx=8, pady=2)

        bf = tk.Frame(root, bg=BG)
        bf.pack(pady=6)
        self.b_iniciar = self._btn(bf, "▶  Iniciar", self.iniciar, ACCENT, "#0d0f13")
        self.b_iniciar.grid(row=0, column=0, padx=4)
        self.b_parar = self._btn(bf, "⏹  Parar", self.parar, PANEL, TXT)
        self.b_parar.grid(row=0, column=1, padx=4)
        self.b_abrir = self._btn(bf, "🌐  Abrir", self.abrir, PANEL, TXT)
        self.b_abrir.grid(row=0, column=2, padx=4)

        of = tk.Frame(root, bg=BG)
        of.pack(pady=(4, 0))
        self.b_ollama = self._btn(of, "Iniciar Ollama", self.acao_ollama, PANEL, TXT)
        self.b_ollama.grid(row=0, column=0, padx=4)

        self.hint = tk.Label(root, text="", bg=BG, fg=DIM, font=("Segoe UI", 9), wraplength=330)
        self.hint.pack(pady=(10, 0))

        self._atualizar_async()

    def acao_ollama(self):
        if self.oll:
            self.toast("Ollama já está rodando")
            return
        exe = _achar_ollama()
        if not exe:
            webbrowser.open("https://ollama.com/download")
            self.toast("instale o Ollama (abri o site) e clique de novo")
            return
        self.toast("iniciando o Ollama…")
        try:
            os.environ["OLLAMA_FLASH_ATTENTION"] = "1"
            os.environ["OLLAMA_KV_CACHE_TYPE"] = "q8_0"
            subprocess.Popen([exe, "serve"], creationflags=NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.toast(f"erro ao iniciar Ollama: {e}")

    def _btn(self, parent, txt, cmd, bg, fg):
        return tk.Button(parent, text=txt, command=cmd, bg=bg, fg=fg,
                         activebackground=bg, activeforeground=fg, relief="flat",
                         font=("Segoe UI", 10, "bold"), cursor="hand2",
                         padx=10, pady=7, bd=0, highlightthickness=0)

    def toast(self, msg):
        self.hint.config(text=msg)

    def iniciar(self):
        global server_proc
        if self.srv:
            self.toast("já está rodando — clique em Abrir")
            return
        self.toast("iniciando…")
        try:
            server_proc = subprocess.Popen([PYW, os.path.join(BASE, "server.py")],
                                           cwd=BASE, creationflags=NO_WINDOW)
        except Exception as e:
            self.toast(f"erro: {e}")
            return

        def esperar():
            for _ in range(24):
                if _up(URL, 0.6):
                    webbrowser.open(URL)
                    self.root.after(0, lambda: self.toast("aberto no navegador"))
                    return
                time.sleep(0.5)
            self.root.after(0, lambda: self.toast("demorou demais pra subir"))
        threading.Thread(target=esperar, daemon=True).start()

    def parar(self):
        global server_proc
        if server_proc:
            try:
                server_proc.terminate()
            except Exception:
                pass
            server_proc = None
        # garante: mata qualquer python servindo a 5005
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetTCPConnection -LocalPort 5005 -State Listen -EA SilentlyContinue | "
                 "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }"],
                creationflags=NO_WINDOW, timeout=10)
        except Exception:
            pass
        self.toast("servidor parado")

    def abrir(self):
        if self.srv:
            webbrowser.open(URL)
        else:
            self.toast("inicie o servidor primeiro (▶)")

    def _atualizar_async(self):
        def checar():
            srv = _up(URL, 0.6)
            oll = _up(OLLAMA, 0.6)
            self.root.after(0, lambda: self._aplicar(srv, oll))
        threading.Thread(target=checar, daemon=True).start()
        self.root.after(2000, self._atualizar_async)

    def _aplicar(self, srv, oll):
        self.srv, self.oll = srv, oll
        self.lbl_srv.config(fg=GOOD if srv else BAD,
                            text=("●  servidor ligado" if srv else "●  servidor desligado"))
        self.lbl_oll.config(fg=GOOD if oll else WARN,
                            text=("●  Ollama ok" if oll else "●  Ollama não está rodando"))
        if oll:
            self.b_ollama.config(text="Ollama ✓", state="disabled")
        elif _achar_ollama():
            self.b_ollama.config(text="Iniciar Ollama", state="normal")
        else:
            self.b_ollama.config(text="Instalar Ollama", state="normal")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
