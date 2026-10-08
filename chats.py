"""
chats — conversas salvas localmente (em conversas.json).
Lista, cria, renomeia, exclui e busca em TODAS as conversas (recall) pra dar ao
modelo acesso ao que já foi falado em qualquer chat.
"""
import os
import re
import json
import time
import uuid
import threading
import unicodedata

import core
import cofre

_lock = threading.Lock()
_NOVOS = {"Novo chat", "New chat", "Nuevo chat", ""}


def _load():
    d = cofre.ler_json(core.CONVERSAS_FILE, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("atual", None)
    d.setdefault("chats", [])
    return d


def _save(d):
    cofre.salvar_json(core.CONVERSAS_FILE, d)


def _garantir_um(d):
    if not d["chats"]:
        cid = uuid.uuid4().hex[:12]
        d["chats"].append({"id": cid, "titulo": "Novo chat",
                            "criado": time.time(), "atualizado": time.time(), "mensagens": []})
        d["atual"] = cid
    if d.get("atual") not in [c["id"] for c in d["chats"]]:
        d["atual"] = d["chats"][0]["id"]
    return d


def _resumo(c):
    return {"id": c["id"], "titulo": c["titulo"], "atualizado": c.get("atualizado", 0),
            "n": len(c.get("mensagens", [])), "projeto": c.get("projeto"), "modelo": c.get("modelo")}


def _trecho(texto, alvo, raio=50):
    """Pedaço do texto em volta da primeira ocorrência (já normalizada) de `alvo`."""
    i = _norm(texto).find(alvo)
    if i < 0:
        return ""
    a, b = max(0, i - raio), min(len(texto), i + len(alvo) + raio)
    return ("…" if a else "") + texto[a:b].replace("\n", " ").strip() + ("…" if b < len(texto) else "")


def listar(q=None):
    """Resumo das conversas. Com `q`, só as que têm o texto no título ou nas mensagens
    (sem diferenciar acentos/maiúsculas); essas ganham `trecho` com o pedaço encontrado."""
    with _lock:
        d = _garantir_um(_load())
        _save(d)
    alvo = _norm(q or "").strip()
    if not alvo:
        return {"atual": d["atual"], "chats": [_resumo(c) for c in d["chats"]]}
    achados = []
    for c in d["chats"]:
        if alvo in _norm(c["titulo"]):
            achados.append({**_resumo(c), "trecho": ""})
            continue
        for m in reversed(c.get("mensagens", [])):
            t = next((x for x in (m.get("u", ""), m.get("a", "")) if alvo in _norm(x)), None)
            if t is not None:
                achados.append({**_resumo(c), "trecho": _trecho(t, alvo)})
                break
    return {"atual": d["atual"], "chats": achados, "q": q}


def get(cid):
    d = _load()
    for c in d["chats"]:
        if c["id"] == cid:
            return c
    return None


def novo(titulo="Novo chat"):
    with _lock:
        d = _load()
        cid = uuid.uuid4().hex[:12]
        d["chats"].insert(0, {"id": cid, "titulo": titulo, "criado": time.time(),
                              "atualizado": time.time(), "mensagens": []})
        d["atual"] = cid
        _save(d)
        return cid


def set_atual(cid):
    with _lock:
        d = _load()
        if any(c["id"] == cid for c in d["chats"]):
            d["atual"] = cid
            _save(d)


def renomear(cid, titulo):
    with _lock:
        d = _load()
        for c in d["chats"]:
            if c["id"] == cid:
                c["titulo"] = (titulo or "").strip()[:60] or "Sem título"
                _save(d)
                return True
        return False


def excluir(cid):
    with _lock:
        d = _load()
        d["chats"] = [c for c in d["chats"] if c["id"] != cid]
        if d.get("atual") == cid:
            d["atual"] = d["chats"][0]["id"] if d["chats"] else None
        d = _garantir_um(d)
        _save(d)
        return d["atual"]


def adicionar(cid, u, a, ctx=None, parcial=False):
    """ctx (opcional): memórias usadas na resposta, [{id,t,p}], para a interface citar a fonte.
    parcial=True marca uma resposta interrompida pelo usuário."""
    with _lock:
        d = _load()
        for c in d["chats"]:
            if c["id"] == cid:
                msg = {"u": u, "a": a}
                if ctx:
                    msg["ctx"] = ctx
                if parcial:
                    msg["parcial"] = True
                c["mensagens"].append(msg)
                c["atualizado"] = time.time()
                if c["titulo"] in _NOVOS:           # primeiro título = começo da pergunta
                    c["titulo"] = (u.strip()[:42] or "Sem título")
                _save(d)
                return


_ID_OK = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


_MODELO_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,79}(:[A-Za-z0-9_.\-]{1,40})?$")


def definir_ctx(cid, projeto=..., fixas=None, excluidas=None, modelo=...):
    """Projeto ativo, modelo próprio e memórias fixadas/excluídas DESTA conversa (campos opcionais do chat).
    projeto=.../modelo=... mantém o atual; None/"" limpa. Devolve o chat (resumo) ou None."""
    with _lock:
        d = _load()
        for c in d["chats"]:
            if c["id"] != cid:
                continue
            if projeto is not ...:
                if projeto and _ID_OK.match(str(projeto)):
                    c["projeto"] = str(projeto)
                else:
                    c.pop("projeto", None)
            if modelo is not ...:
                if modelo and _MODELO_OK.match(str(modelo)):
                    c["modelo"] = str(modelo)
                else:
                    c.pop("modelo", None)
            for chave, val in (("ctx_fixas", fixas), ("ctx_excluidas", excluidas)):
                if val is not None:
                    ids = [i for i in dict.fromkeys(val) if isinstance(i, str) and _ID_OK.match(i)][:200]
                    if ids:
                        c[chave] = ids
                    else:
                        c.pop(chave, None)
            # uma memória não pode estar nas duas listas
            exc = set(c.get("ctx_excluidas") or [])
            if exc & set(c.get("ctx_fixas") or []):
                c["ctx_fixas"] = [i for i in c["ctx_fixas"] if i not in exc]
            _save(d)
            return {"id": cid, "projeto": c.get("projeto"), "modelo": c.get("modelo"),
                    "fixas": c.get("ctx_fixas", []), "excluidas": c.get("ctx_excluidas", [])}
    return None


def remover_ultima(cid):
    """Remove o último par (pergunta+resposta) de um chat. Usado pra regenerar.
    Devolve o texto da pergunta removida (pra reenviar)."""
    with _lock:
        d = _load()
        for c in d["chats"]:
            if c["id"] == cid and c.get("mensagens"):
                msg = c["mensagens"].pop()
                c["atualizado"] = time.time()
                _save(d)
                return msg.get("u", "")
    return ""


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


# palavras que aparecem em quase toda conversa e não dizem nada sobre o assunto (PT/EN/ES)
_VAZIAS = set("""
para pra com sem que como qual quais quando onde voce voces ele ela eles elas meu minha meus minhas seu sua seus suas
isso isto esse essa este esta estes estas aquele aquela aqui ali mais mas muito muita pouco tambem ainda agora hoje
ontem amanha sempre nunca nada tudo todo toda todos todas cada outro outra sobre entre depois antes porque pois
entao bem sim nao ser estar esta estou estamos sao era foi sera tem tenho temos ter fazer faz fiz pode posso
podemos quer quero queria vou vai vamos ver sei saber acho coisa coisas algo alguem gente obrigado obrigada
ola oi tchau favor certo legal tipo assim dia vez
the and for with from this that these those what which who whom how when where why you your yours they them their
are was were been being have has had having does did doing can could would should will just also very really
about into over then than there here some any all each more most other such only own same too now today
hello thanks please okay yes not
los las una unos unas del por con sin que como cual cuando donde usted ustedes ellos ellas esto eso este esta
pero muy tambien ahora hoy siempre nunca nada todo toda todos cada otro otra sobre entre despues antes porque
bien hola gracias puedo quiero tengo hacer
""".split())


def _termos(s):
    return {t for t in re.findall(r"[a-z0-9]{3,}", _norm(s)) if t not in _VAZIAS}


def recall(query, excluir_id=None, max_trechos=4):
    """Busca por palavras-chave em TODAS as conversas — dá ao modelo acesso ao passado.
    Ignora palavras vazias e acentos, pesa termos raros mais que comuns (IDF), exige que o
    trecho cubra uma parte razoável da pergunta e, no empate, prefere o mais recente."""
    import math
    termos = _termos(query)
    if not termos:
        return ""
    d = _load()
    docs = []
    for c in d["chats"]:
        if c["id"] == excluir_id:
            continue
        for i, m in enumerate(c.get("mensagens", [])):
            docs.append((c, i, m, _termos(m.get("u", "") + " " + m.get("a", ""))))
    if not docs:
        return ""
    n = len(docs)
    df = {t: sum(1 for x in docs if t in x[3]) for t in termos}
    peso = {t: math.log(1 + n / df[t]) if df[t] else math.log(1 + n) for t in termos}
    total = sum(peso.values()) or 1.0
    minimo = 1 if len(termos) == 1 else 2
    pont = []
    for c, i, m, toks in docs:
        hits = [t for t in termos if t in toks]
        if len(hits) < minimo and sum(peso[t] for t in hits) / total < 0.5:
            continue
        cobertura = sum(peso[t] for t in hits) / total
        if cobertura < 0.25:
            continue
        pont.append((cobertura, c.get("atualizado", 0), i, c["titulo"], m.get("u", ""), m.get("a", "")))
    pont.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    out, vistos = [], []
    for _s, _t, _i, tit, u, a in pont:
        chave = _norm(u).strip()
        if chave in vistos:
            continue
        vistos.append(chave)
        out.append(f"[da conversa \"{tit}\"] {u}: {a[:220]}")
        if len(out) >= max_trechos:
            break
    return "\n".join(out)
