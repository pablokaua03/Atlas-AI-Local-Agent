"""
bandeja — ícone na bandeja do Windows + atalho global pra abrir o agente rápido.

- Ícone na bandeja (pystray): Abrir, Pausar/Retomar, Sair.
- Atalho global Ctrl+Alt+A: abre http://127.0.0.1:5005 no navegador.

O atalho usa só ctypes (user32.RegisterHotKey), sem DLL de terceiros. O ícone
usa pystray (opcional): se não estiver instalado, o atalho continua valendo.
"""
import os
import threading
import webbrowser

import core

URL = "http://127.0.0.1:5005"


def abrir(*_):
    try:
        webbrowser.open(URL)
    except Exception:
        pass


def _alternar_ativo(*_):
    cfg = core.carregar_config()
    cfg["ativo"] = not cfg.get("ativo", True)
    core.salvar_config(cfg)
    if not cfg["ativo"]:
        threading.Thread(target=core.descarregar_modelos, daemon=True).start()


# ── ícone na bandeja (pystray) ────────────────────────────────────────────────
def _icone_img():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(32, 6), (58, 32), (32, 58), (6, 32)], fill=(120, 170, 255, 255))  # losango ◆
    d.polygon([(32, 18), (46, 32), (32, 46), (18, 32)], fill=(20, 24, 36, 255))
    return img


def _tray():
    try:
        import pystray
    except Exception:
        return
    try:
        menu = pystray.Menu(
            pystray.MenuItem("Abrir Atlas", abrir, default=True),
            pystray.MenuItem("Pausar / Retomar", _alternar_ativo),
            pystray.MenuItem("Sair", lambda icon, item: (icon.stop(), os._exit(0))),
        )
        pystray.Icon("Atlas", _icone_img(), "Atlas - AI Local Agent", menu).run()
    except Exception:
        pass


# ── atalho global (ctypes, sem dependência) ───────────────────────────────────
def _hotkey():
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
        WM_HOTKEY = 0x0312
        VK_A = 0x41
        if not u32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_A):
            return
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                abrir()
    except Exception:
        pass


def iniciar():
    threading.Thread(target=_tray, daemon=True).start()
    threading.Thread(target=_hotkey, daemon=True).start()
