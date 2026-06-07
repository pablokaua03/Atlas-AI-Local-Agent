"""
lembretes — lembretes simples em linguagem natural ("me lembra disso amanhã").

Entende expressões de tempo em PT/EN/ES (amanhã, daqui a 2 horas, às 15h...),
guarda em lembretes.json (cifrado se o cofre estiver ligado) e, na hora certa,
dispara pelo mesmo canal da iniciativa (aparece como uma mensagem do agente).
"""
import os
import re
import time
import uuid
import threading
import datetime

import core
import cofre

ARQ = os.path.join(core.BASE_DIR, "lembretes.json")
_lock = threading.Lock()

_GATILHOS = (
    r"me lembra", r"lembra de", r"lembre-me", r"lembrete", r"me lembre",
    r"remind me", r"reminder", r"don't let me forget",
    r"recu[ée]rdame", r"recuerda", r"recordatorio",
)
_CONFIRMA = {
    "pt": "Combinado. Te lembro {quando}: {texto}",
    "en": "Done. I'll remind you {quando}: {texto}",
    "es": "Hecho. Te recuerdo {quando}: {texto}",
}
_DISPARO = {"pt": "Lembrete: {texto}", "en": "Reminder: {texto}", "es": "Recordatorio: {texto}"}


# ── armazenamento ─────────────────────────────────────────────────────────────
def carregar():
    d = cofre.ler_json(ARQ, [])
    return d if isinstance(d, list) else []


def salvar(lista):
    cofre.salvar_json(ARQ, lista, indent=None)


def listar():
    agora = time.time()
    itens = sorted(carregar(), key=lambda x: x.get("quando", 0))
    return [{**x, "vencido": (not x.get("feito")) and x.get("quando", 0) <= agora} for x in itens]


def criar(texto, quando_ts):
    with _lock:
        lista = carregar()
        item = {"id": uuid.uuid4().hex[:10], "texto": texto, "quando": quando_ts,
                "criado": time.time(), "feito": False}
        lista.append(item)
        salvar(lista)
        return item


def excluir(lid):
    with _lock:
        lista = [x for x in carregar() if x.get("id") != lid]
        salvar(lista)
        return True


# ── interpretação de tempo (PT/EN/ES) ─────────────────────────────────────────
def _parse_tempo(txt, agora=None):
    agora = agora or datetime.datetime.now()
    t = " " + txt.lower() + " "

    # relativo: "daqui a / em / dentro de / in / en  N  unidade"
    m = re.search(r"\b(?:daqui a|daqui|em|dentro de|in|en)\s+(\d+)\s*(minut\w*|min\b|hor\w*|\bh\b|dia\w*|day\w*|hour\w*|semana\w*|week\w*)", t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        if u.startswith(("hor", "hour")) or u.strip() == "h":
            d = datetime.timedelta(hours=n)
        elif u.startswith(("dia", "day")):
            d = datetime.timedelta(days=n)
        elif u.startswith(("seman", "week")):
            d = datetime.timedelta(weeks=n)
        else:
            d = datetime.timedelta(minutes=n)
        return agora + d

    # âncora de dia
    base = None
    if re.search(r"depois de amanh[ãa]|pasado ma[ñn]ana|day after tomorrow", t):
        base = agora.date() + datetime.timedelta(days=2)
    elif re.search(r"\bamanh[ãa]\b|\btomorrow\b|\bma[ñn]ana\b", t):
        base = agora.date() + datetime.timedelta(days=1)
    elif re.search(r"\bhoje\b|\btoday\b|\bhoy\b|\besta noite\b|\btonight\b", t):
        base = agora.date()

    # hora do dia: "às 15h", "as 15:30", "at 9", "a las 18", "9pm"
    hora = None
    hm = re.search(r"\b(?:[àa]s|at|a las)\s*(\d{1,2})(?:[:h](\d{2}))?\s*(am|pm)?", t)
    if not hm:
        hm = re.search(r"\b(\d{1,2})(?:[:h](\d{2}))?\s*(am|pm)\b", t)
    if hm:
        H, M, ap = int(hm.group(1)), int(hm.group(2) or 0), hm.group(3)
        if ap == "pm" and H < 12:
            H += 12
        if ap == "am" and H == 12:
            H = 0
        if 0 <= H <= 23 and 0 <= M <= 59:
            hora = (H, M)

    if base is None and hora is None:
        return None
    if base is None:
        base = agora.date()
    H, M = hora if hora else (9, 0)         # só "amanhã" sem hora → 9h da manhã
    dt = datetime.datetime.combine(base, datetime.time(H, M))
    if hora and base == agora.date() and dt <= agora:
        dt += datetime.timedelta(days=1)    # a hora de hoje já passou → vai pra amanhã
    return dt


def _limpar_texto(txt, idioma):
    s = txt
    for g in _GATILHOS:
        s = re.sub(g + r"\s*(?:de|que|para|to|of)?\s*[:,-]?\s*", " ", s, flags=re.I)
    # remove as expressões de tempo do texto do lembrete
    s = re.sub(r"\b(?:daqui a|daqui|dentro de|in|en|em)\s+\d+\s*\w+", " ", s, flags=re.I)
    s = re.sub(r"\b(?:[àa]s|at|a las)\s*\d{1,2}(?:[:h]\d{2}|h)?\s*(?:am|pm)?", " ", s, flags=re.I)
    s = re.sub(r"\bdepois de amanh[ãa]\b|\bpasado ma[ñn]ana\b|\bday after tomorrow\b", " ", s, flags=re.I)
    s = re.sub(r"\bamanh[ãa]\b|\btomorrow\b|\bma[ñn]ana\b|\bhoje\b|\btoday\b|\bhoy\b|\bhoje à noite\b|\btonight\b|\besta noite\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" .,:-")
    if not s or len(s) < 2:
        s = {"pt": "isso", "en": "this", "es": "esto"}.get(idioma, "isso")
    return s[:200]


def _formatar_quando(ts, idioma):
    dt = datetime.datetime.fromtimestamp(ts)
    hoje = datetime.date.today()
    dia = dt.date()
    hora = dt.strftime("%H:%M")
    if dia == hoje:
        pref = {"pt": "hoje às", "en": "today at", "es": "hoy a las"}.get(idioma, "hoje às")
    elif dia == hoje + datetime.timedelta(days=1):
        pref = {"pt": "amanhã às", "en": "tomorrow at", "es": "mañana a las"}.get(idioma, "amanhã às")
    else:
        return dt.strftime("%d/%m %H:%M")
    return f"{pref} {hora}"


def detectar(texto, idioma="pt"):
    """Se a mensagem é um pedido de lembrete com tempo claro, cria e devolve a
    confirmação pronta. Senão, devolve None (a conversa segue normal)."""
    baixo = texto.lower()
    if not any(re.search(g, baixo) for g in _GATILHOS):
        return None
    dt = _parse_tempo(texto)
    if not dt or dt <= datetime.datetime.now():
        return None
    conteudo = _limpar_texto(texto, idioma)
    ts = dt.timestamp()
    criar(conteudo, ts)
    quando = _formatar_quando(ts, idioma)
    return _CONFIRMA.get(idioma, _CONFIRMA["pt"]).format(quando=quando, texto=conteudo)


# ── disparo (laço de fundo, usa o canal da iniciativa) ────────────────────────
def loop():
    import skills
    while True:
        try:
            time.sleep(20)
            if not cofre.disponivel():
                continue
            if not core.carregar_config().get("ativo", True):
                continue                       # pausado: segura os lembretes pra depois
            agora = time.time()
            lista = carregar()
            mudou = False
            idioma = core.carregar_config().get("idioma", "pt")
            for it in lista:
                if not it.get("feito") and it.get("quando", 0) <= agora:
                    frase = _DISPARO.get(idioma, _DISPARO["pt"]).format(texto=it.get("texto", ""))
                    skills.emitir("iniciativa", frase)
                    it["feito"] = True
                    mudou = True
            if mudou:
                with _lock:
                    salvar(lista)
        except Exception:
            pass


def iniciar():
    threading.Thread(target=loop, daemon=True).start()
