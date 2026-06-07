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


def listar():
    with _lock:
        d = _garantir_um(_load())
        _save(d)
        return {"atual": d["atual"],
                "chats": [{"id": c["id"], "titulo": c["titulo"], "atualizado": c.get("atualizado", 0),
                           "n": len(c.get("mensagens", []))} for c in d["chats"]]}


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


def adicionar(cid, u, a):
    with _lock:
        d = _load()
        for c in d["chats"]:
            if c["id"] == cid:
                c["mensagens"].append({"u": u, "a": a})
                c["atualizado"] = time.time()
                if c["titulo"] in _NOVOS:           # primeiro título = começo da pergunta
                    c["titulo"] = (u.strip()[:42] or "Sem título")
                _save(d)
                return


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


def recall(query, excluir_id=None, max_trechos=4):
    """Busca por palavras-chave em TODAS as conversas — dá ao modelo acesso ao passado."""
    termos = {t for t in re.findall(r"[a-zà-ú0-9]{4,}", query.lower())}
    if not termos:
        return ""
    d = _load()
    pont = []
    for c in d["chats"]:
        if c["id"] == excluir_id:
            continue
        for m in c.get("mensagens", []):
            blob = (m.get("u", "") + " " + m.get("a", "")).lower()
            s = sum(1 for t in termos if t in blob)
            if s:
                pont.append((s, c["titulo"], m.get("u", ""), m.get("a", "")))
    pont.sort(key=lambda x: -x[0])
    return "\n".join(f"[da conversa \"{tit}\"] {u}: {a[:220]}" for _s, tit, u, a in pont[:max_trechos])
