"""
ferramentas — o que o modelo pode fazer no Atlas durante uma conversa (tool calling nativo do Ollama).

Modelos com suporte a ferramentas (qwen3.5, qwen3, llama3.x, ministral, gemma4, granite...) recebem
a lista abaixo no /api/chat e podem consultar e gravar memória, grafo, projetos, conversas e
documentos. Modelos sem suporte continuam com a injeção de contexto de sempre (server.chat).

Regras:
- Toda escrita passa pelas MESMAS funções da interface e da API /v1 (memstore), com source
  "atlas-chat:<modelo>", e é registrada em acoes_ia.json (cifrado com o cofre, como o resto).
- Cada escrita guarda o "antes" (memória e diff do grafo) e pode ser desfeita (desfazer()).
- Texto que parece senha/token/cartão é recusado (contexto.sensivel).
- Leitura de arquivos só dentro da pasta docs/ do Atlas, sem sair dela.
"""
import os
import copy
import json
import time
import secrets
import threading

import core
import cofre
import chats
import skills
import memstore
import contexto

MAX_LOG = 500
MAX_RESULTADO = 6000            # caracteres do resultado que voltam para o modelo
MARCA = "\x1e"                   # separador do evento de ferramenta dentro do stream de texto do /chat
_lock = threading.Lock()


def _log_file():
    return os.path.join(core.BASE_DIR, "acoes_ia.json")


def _docs_dir():
    import docs
    return os.path.realpath(docs.DOCS_DIR)


# ── definição das ferramentas ─────────────────────────────────────────────────
def _fn(nome, desc, props=None, req=None):
    return {"type": "function", "function": {"name": nome, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": req or []}}}


_S = lambda d, **kw: {"type": "string", "description": d, **kw}       # noqa: E731
_I = lambda d, **kw: {"type": "integer", "description": d, **kw}      # noqa: E731
_B = lambda d: {"type": "boolean", "description": d}                  # noqa: E731
_TIPOS_MEM = ["fact", "preference", "decision", "task", "note", "person", "project"]

ESQUEMAS = {
    "search_memories": _fn(
        "search_memories",
        "Search the user's long-term memories (facts, preferences, decisions, tasks, notes). Use it before "
        "answering anything about the user, their projects, people or past decisions. Empty query lists the most important.",
        {"query": _S("Keywords or a question."), "project": _S("Optional project id or name to focus on."),
         "pinned_only": _B("Only pinned memories."), "limit": _I("Max results (1-15).", minimum=1, maximum=15)}),
    "save_memory": _fn(
        "save_memory",
        "Save ONE durable memory when the user states a lasting fact, preference or decision, or asks you to remember "
        "something. Do not save small talk, temporary things or secrets.",
        {"content": _S("One self-contained sentence, in the user's language."),
         "type": _S("Kind of memory.", enum=_TIPOS_MEM), "project": _S("Optional project id or name."),
         "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional short tags."},
         "importance": _I("1 (trivial) to 5 (critical).", minimum=1, maximum=5),
         "entities": {"type": "array", "items": {"type": "string"},
                      "description": "Optional names of people/things it mentions (become graph concepts)."}},
        ["content"]),
    "update_memory": _fn(
        "update_memory",
        "Correct or complete an existing memory (use the id from search_memories).",
        {"id": _S("Memory id (mem_...)."), "content": _S("New full text."), "type": _S("Kind.", enum=_TIPOS_MEM),
         "tags": {"type": "array", "items": {"type": "string"}}, "importance": _I("1-5.", minimum=1, maximum=5),
         "project": _S("Move to this project (id or name).")},
        ["id"]),
    "pin_memory": _fn(
        "pin_memory",
        "Pin (always keep in context for its project) or unpin a memory.",
        {"id": _S("Memory id (mem_...)."), "pinned": _B("true to pin, false to unpin.")}, ["id"]),
    "graph_search": _fn(
        "graph_search",
        "Search the user's knowledge graph for concepts (people, projects, tools, places) and how they are related.",
        {"query": _S("Concept name or keywords."), "limit": _I("Max concepts (1-10).", minimum=1, maximum=10)},
        ["query"]),
    "add_concept": _fn(
        "add_concept",
        "Add (or enrich) a concept in the knowledge graph.",
        {"label": _S("Short name of the concept."), "type": _S("pessoa, projeto, ferramenta, lugar, tema..."),
         "description": _S("Optional one-line description."), "project": _S("Optional project id or name.")},
        ["label"]),
    "link_concepts": _fn(
        "link_concepts",
        "Create or update a relation between two concepts in the knowledge graph (creates missing concepts).",
        {"from": _S("Source concept."), "to": _S("Target concept."),
         "relation": _S("Short verb phrase, e.g. 'usa', 'trabalha em'."), "project": _S("Optional project id or name.")},
        ["from", "to", "relation"]),
    "list_projects": _fn("list_projects", "List the user's projects (id, name, description, memory count)."),
    "recall_conversation": _fn(
        "recall_conversation",
        "Find what was said in the user's PREVIOUS conversations with you about a topic.",
        {"query": _S("Topic or keywords.")}, ["query"]),
    "list_documents": _fn("list_documents", "List the user's documents in the Atlas docs folder."),
    "read_document": _fn(
        "read_document",
        "Read a text/markdown/PDF document from the Atlas docs folder (path relative to that folder).",
        {"path": _S("Relative path, as returned by list_documents."),
         "offset": _I("Character offset to start from (for long files).", minimum=0)}, ["path"]),
}

ESCRITA = {"save_memory", "update_memory", "pin_memory", "add_concept", "link_concepts"}
GRUPOS = {
    "memoria": ["search_memories", "save_memory", "update_memory", "pin_memory", "list_projects", "recall_conversation"],
    "grafo": ["graph_search", "add_concept", "link_concepts"],
    "documentos": ["list_documents", "read_document"],
}


def config(cfg=None) -> dict:
    cfg = cfg or core.carregar_config()
    f = cfg.get("ferramentas") or {}
    return {"ativo": f.get("ativo", True) is not False, "escrita": f.get("escrita", True) is not False,
            "max_rodadas": int(f.get("max_rodadas") or 4)}


def disponiveis(cfg=None) -> list:
    """Nomes das ferramentas liberadas pelas habilidades e ajustes atuais."""
    cfg = cfg or core.carregar_config()
    fc = config(cfg)
    if not fc["ativo"]:
        return []
    nomes = []
    if core.habilidade("memoria", cfg):
        nomes += GRUPOS["memoria"]
    if core.habilidade("memoria", cfg) or core.habilidade("grafo", cfg):
        nomes += GRUPOS["grafo"]
    if core.habilidade("documentos", cfg):
        nomes += GRUPOS["documentos"]
    if not fc["escrita"]:
        nomes = [n for n in nomes if n not in ESCRITA]
    return list(dict.fromkeys(nomes))


def esquemas(nomes) -> list:
    return [ESQUEMAS[n] for n in nomes if n in ESQUEMAS]


INSTRUCOES = {
    "pt": ("\n\nVocê tem ferramentas para consultar e atualizar a memória do usuário (memórias, grafo, projetos, "
           "conversas anteriores{docs}). Use-as quando ajudarem: procure antes de responder sobre o usuário ou seus "
           "projetos; salve só fatos, preferências e decisões duráveis (ou quando ele pedir para lembrar). Nunca salve "
           "senhas ou dados sensíveis. Depois de usar uma ferramenta, responda normalmente, sem descrever a ferramenta."),
    "en": ("\n\nYou have tools to look up and update the user's memory (memories, graph, projects, past "
           "conversations{docs}). Use them when they help: search before answering about the user or their projects; "
           "only save durable facts, preferences and decisions (or when asked to remember). Never save passwords or "
           "sensitive data. After using a tool, answer normally without describing the tool."),
    "es": ("\n\nTienes herramientas para consultar y actualizar la memoria del usuario (memorias, grafo, proyectos, "
           "conversaciones anteriores{docs}). Úsalas cuando ayuden: busca antes de responder sobre el usuario o sus "
           "proyectos; guarda solo hechos, preferencias y decisiones duraderas (o cuando lo pida). Nunca guardes "
           "contraseñas ni datos sensibles. Después de usar una herramienta, responde normalmente sin describirla."),
}


def instrucoes(idioma, nomes) -> str:
    docs = {"pt": ", documentos", "en": ", documents", "es": ", documentos"}
    t = INSTRUCOES.get(idioma, INSTRUCOES["pt"])
    return t.format(docs=docs.get(idioma, "") if "read_document" in nomes else "")


# ── utilitários ───────────────────────────────────────────────────────────────
def _str(v, maximo=4000):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    return str(v).strip()[:maximo]


def _int(v, padrao, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return padrao


def _bool(v, padrao=True):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "nao", "não", "")
    return padrao if v is None else bool(v)


def _lista(v):
    if isinstance(v, str):
        try:
            p = json.loads(v)
            v = p if isinstance(p, list) else [x for x in v.split(",")]
        except ValueError:
            v = [x for x in v.split(",")]
    if not isinstance(v, list):
        return []
    return [str(x).strip()[:40] for x in v if str(x).strip()][:20]


def _mem_out(m):
    texto = m.get("content", "")
    if contexto.sensivel(texto):
        texto = "[hidden: looks like sensitive data]"
    return {"id": m["id"], "content": texto[:500], "type": m.get("type"), "project": m.get("project"),
            "tags": m.get("tags") or [], "importance": m.get("importance"), "pinned": bool(m.get("pinned")),
            "updated": m.get("updated")}


def _projeto(v, ctx):
    p = _str(v, 64)
    return p or None


def _fonte(ctx):
    return ("atlas-chat:" + (ctx.get("modelo") or ""))[:64]


# ── ferramentas de leitura ───────────────────────────────────────────────────
def _search_memories(a, ctx):
    lim = _int(a.get("limit"), 6, 1, 15)
    proj = _projeto(a.get("project"), ctx)
    if _bool(a.get("pinned_only"), False):
        r = memstore.memorias_listar(projeto=proj, fixadas=True, limite=lim, escopo="inherit" if proj else "exact")
        itens = r["items"]
    else:
        sem = bool(((ctx.get("cfg") or {}).get("contexto") or {}).get("busca_semantica"))
        r = memstore.memorias_buscar(_str(a.get("query"), 500), projeto=proj, limite=lim, semantica=sem)
        itens = r["items"]
    return {"count": len(itens), "memories": [_mem_out(m) for m in itens]}


def _graph_search(a, ctx):
    lim = _int(a.get("limit"), 6, 1, 10)
    r = memstore.nos_listar(_str(a.get("query"), 200), limite=lim)
    out = []
    for n in r["items"]:
        try:
            d = memstore.no_obter(n["key"], com_memorias=False)
        except memstore.ErroAPI:
            continue
        rels = [f"{d['label']} {e['rel']} {e['to_label']}" for e in d["outgoing"][:8]] + \
               [f"{e['from_label']} {e['rel']} {d['label']}" for e in d["incoming"][:8]]
        out.append({"key": n["key"], "label": n["label"], "type": n["type"], "description": n.get("description", ""),
                    "projects": n.get("projects", []), "relations": rels[:12]})
    return {"count": len(out), "concepts": out}


def _list_projects(a, ctx):
    ps = memstore.projetos_listar()
    out = []
    for p in ps:
        out.append({"id": p.get("id"), "name": p.get("name"), "description": (p.get("description") or "")[:200],
                    "parent": p.get("parent"), "memories": p.get("memories"), "nodes": p.get("nodes")})
    return {"count": len(out), "projects": out, "active_project": ctx.get("projeto")}


def _recall_conversation(a, ctx):
    q = _str(a.get("query"), 300)
    txt = chats.recall(q, excluir_id=ctx.get("chat_id"), max_trechos=5) if q else ""
    return {"found": bool(txt), "excerpts": txt[:3000] if txt else ""}


def _caminho_doc(rel):
    base = _docs_dir()
    rel = _str(rel, 300).replace("\\", "/").lstrip("/")
    alvo = os.path.realpath(os.path.join(base, rel))
    if not rel or os.path.commonpath([base, alvo]) != base or not os.path.isfile(alvo):
        raise ValueError("document not found inside the Atlas docs folder")
    return alvo


def _list_documents(a, ctx):
    import docs
    base = _docs_dir()
    out = []
    for raiz, _, nomes in os.walk(base):
        for n in sorted(nomes):
            if n.lower().endswith(docs.EXTS):
                c = os.path.join(raiz, n)
                out.append({"path": os.path.relpath(c, base).replace("\\", "/"), "kb": round(os.path.getsize(c) / 1024, 1)})
            if len(out) >= 200:
                break
    return {"count": len(out), "documents": out}


def _read_document(a, ctx):
    import docs
    alvo = _caminho_doc(a.get("path"))
    if not alvo.lower().endswith(docs.EXTS):
        raise ValueError("unsupported file type")
    txt = docs._ler_arquivo(alvo)
    ini = _int(a.get("offset"), 0, 0, max(0, len(txt)))
    pedaco = txt[ini:ini + 5000]
    return {"path": os.path.relpath(alvo, _docs_dir()).replace("\\", "/"), "offset": ini, "length": len(txt),
            "text": pedaco, "more": ini + len(pedaco) < len(txt)}


# ── ferramentas de escrita ───────────────────────────────────────────────────
def _recusar_sensivel(texto):
    if contexto.sensivel(texto):
        raise ValueError("refused: the text looks like a password, token, card or document number")


def _save_memory(a, ctx):
    conteudo = _str(a.get("content"), memstore.MAX_CONTEUDO)
    if not conteudo:
        raise ValueError("'content' is required")
    _recusar_sensivel(conteudo)
    tipo = _str(a.get("type"), 32).lower() or "note"
    dados = {"content": conteudo, "type": tipo if tipo in _TIPOS_MEM else "note",
             "project": _projeto(a.get("project"), ctx) or ctx.get("projeto"), "create_project": False,
             "tags": _lista(a.get("tags")), "importance": _int(a.get("importance"), 3, 1, 5),
             "source": _fonte(ctx), "meta": {"via": "chat-tool", "chat_id": ctx.get("chat_id") or ""}}
    ents = _lista(a.get("entities"))
    if ents:
        dados["entities"] = ents
    m = memstore.memoria_criar(dados)
    return {"id": m["id"], "duplicate": bool(m.get("duplicate")), "project": m["project"]}, \
        ({} if m.get("duplicate") else {m["id"]: None})


def _update_memory(a, ctx):
    mid = _str(a.get("id"), 64)
    antes = copy.deepcopy(memstore.memoria_obter(mid))
    dados = {}
    if a.get("content") not in (None, ""):
        dados["content"] = _str(a["content"], memstore.MAX_CONTEUDO)
        _recusar_sensivel(dados["content"])
    if a.get("type"):
        t = _str(a["type"], 32).lower()
        dados["type"] = t if t in _TIPOS_MEM else antes.get("type")
    if a.get("tags") is not None:
        dados["tags"] = _lista(a["tags"])
    if a.get("importance") is not None:
        dados["importance"] = _int(a["importance"], antes.get("importance", 3), 1, 5)
    if a.get("project"):
        dados["project"] = _str(a["project"], 64)
    if not dados:
        raise ValueError("nothing to change")
    m = memstore.memoria_atualizar(mid, dados)
    return {"id": m["id"], "updated": sorted(dados)}, {mid: antes}


def _pin_memory(a, ctx):
    mid = _str(a.get("id"), 64)
    antes = copy.deepcopy(memstore.memoria_obter(mid))
    m = memstore.memoria_atualizar(mid, {"pinned": _bool(a.get("pinned"), True)})
    return {"id": m["id"], "pinned": bool(m.get("pinned"))}, {mid: antes}


def _add_concept(a, ctx):
    lbl = _str(a.get("label"), 80)
    _recusar_sensivel(lbl + " " + _str(a.get("description"), 500))
    n = memstore.no_criar(lbl, _str(a.get("type"), 20) or "tema", projeto=_projeto(a.get("project"), ctx),
                          descricao=_str(a.get("description"), 500))
    return {"key": n["key"], "label": n["label"], "type": n["type"]}, {}


def _link_concepts(a, ctx):
    de, para = _str(a.get("from"), 80), _str(a.get("to"), 80)
    _recusar_sensivel(de + " " + para)
    e = memstore.aresta_criar(de, para, _str(a.get("relation"), 40), projeto=_projeto(a.get("project"), ctx))
    return {"from": e["from"], "to": e["to"], "relation": e["rel"]}, {}


FUNCOES = {
    "search_memories": _search_memories, "graph_search": _graph_search, "list_projects": _list_projects,
    "recall_conversation": _recall_conversation, "list_documents": _list_documents, "read_document": _read_document,
    "save_memory": _save_memory, "update_memory": _update_memory, "pin_memory": _pin_memory,
    "add_concept": _add_concept, "link_concepts": _link_concepts,
}


# ── grafo: diff antes/depois (para desfazer) ────────────────────────────────
def _foto_grafo():
    g = skills.carregar_grafo()
    return copy.deepcopy(g.get("nos", {})), copy.deepcopy(g.get("arestas", {}))


def _diff(antes, depois):
    """{chave: valor_antes} para tudo que mudou (None = não existia)."""
    out = {}
    for k in set(antes) | set(depois):
        if antes.get(k) != depois.get(k):
            out[k] = copy.deepcopy(antes.get(k))
    return out


# ── registro de ações ────────────────────────────────────────────────────────
def _carregar_log():
    d = cofre.ler_json(_log_file(), {})
    if not isinstance(d, dict) or not isinstance(d.get("acoes"), list):
        d = {"acoes": []}
    return d


def _salvar_log(d):
    d["acoes"] = d["acoes"][-MAX_LOG:]
    cofre.salvar_json(_log_file(), d)


def _registrar(item):
    with _lock:
        d = _carregar_log()
        d["acoes"].append(item)
        _salvar_log(d)


def listar_acoes(limite=50, chat_id=None):
    """Ações de escrita feitas pelo modelo, mais novas primeiro (sem os 'antes' internos)."""
    with _lock:
        acoes = _carregar_log()["acoes"]
    if chat_id:
        acoes = [x for x in acoes if x.get("chat_id") == chat_id]
    limite = max(1, min(500, int(limite or 50)))
    return [{k: v for k, v in x.items() if k not in ("mem_antes", "grafo_antes", "mem_depois")}
            for x in acoes[::-1][:limite]]


def _resumo(nome, args, res):
    if not isinstance(res, dict):
        return ""
    if nome == "search_memories":
        return f"{res.get('count', 0)} memória(s)"
    if nome == "graph_search":
        return f"{res.get('count', 0)} conceito(s)"
    if nome == "list_projects":
        return f"{res.get('count', 0)} projeto(s)"
    if nome == "recall_conversation":
        return "trechos encontrados" if res.get("found") else "nada encontrado"
    if nome == "list_documents":
        return f"{res.get('count', 0)} documento(s)"
    if nome == "read_document":
        return f"{res.get('path')} ({len(res.get('text', ''))} car.)"
    if nome == "save_memory":
        return ("já existia: " if res.get("duplicate") else "salva: ") + _str(args.get("content"), 80)
    if nome == "update_memory":
        return f"{res.get('id')}: " + ", ".join(res.get("updated") or [])
    if nome == "pin_memory":
        return f"{res.get('id')} " + ("fixada" if res.get("pinned") else "desafixada")
    if nome == "add_concept":
        return res.get("label", "")
    if nome == "link_concepts":
        return f"{res.get('from')} → {res.get('relation')} → {res.get('to')}"
    return ""


def executar(nome, args, ctx):
    """Roda uma ferramenta. ctx: {chat_id, modelo, projeto, cfg, permitidas}.
    Devolve {"ok", "resultado" (vai para o modelo), "evento" (vai para a interface/histórico)}."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    args = args if isinstance(args, dict) else {}
    permitidas = ctx.get("permitidas")
    evento = {"tool": nome, "args": {k: _str(v, 200) for k, v in list(args.items())[:8]}, "ok": False}
    if nome not in FUNCOES or (permitidas is not None and nome not in permitidas):
        evento["erro"] = "ferramenta indisponível"
        return {"ok": False, "resultado": {"error": f"tool '{nome}' is not available"}, "evento": evento}
    escrita = nome in ESCRITA
    g_antes = _foto_grafo() if escrita else None
    try:
        if escrita:
            res, mem_antes = FUNCOES[nome](args, ctx)
        else:
            res, mem_antes = FUNCOES[nome](args, ctx), {}
    except memstore.ErroAPI as e:
        evento["erro"] = e.msg[:200]
        return {"ok": False, "resultado": {"error": e.msg}, "evento": evento}
    except (ValueError, TypeError, KeyError) as e:
        evento["erro"] = str(e)[:200]
        return {"ok": False, "resultado": {"error": str(e)[:300]}, "evento": evento}
    except Exception as e:                                   # nunca derruba o chat
        evento["erro"] = "erro interno"
        return {"ok": False, "resultado": {"error": f"internal error: {type(e).__name__}"}, "evento": evento}
    evento["ok"] = True
    evento["resumo"] = _resumo(nome, args, res)
    if escrita:
        nos1, ar1 = _foto_grafo()
        g_diff = {"nos": _diff(g_antes[0], nos1), "arestas": _diff(g_antes[1], ar1)}
        mem_depois = {}
        for mid in mem_antes:
            try:
                mem_depois[mid] = memstore.memoria_obter(mid).get("updated")
            except memstore.ErroAPI:
                pass
        if mem_antes or g_diff["nos"] or g_diff["arestas"]:
            aid = "act_" + secrets.token_hex(5)
            _registrar({"id": aid, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "chat_id": ctx.get("chat_id"),
                        "modelo": ctx.get("modelo"), "tool": nome, "args": evento["args"], "resumo": evento["resumo"],
                        "desfeita": False, "mem_antes": mem_antes, "mem_depois": mem_depois, "grafo_antes": g_diff})
            evento["acao"] = aid
    return {"ok": True, "resultado": res, "evento": evento}


def resultado_texto(res) -> str:
    t = json.dumps(res, ensure_ascii=False, default=str)
    return t if len(t) <= MAX_RESULTADO else t[:MAX_RESULTADO] + "…(truncated)"


def marcador(evento) -> str:
    """Evento de ferramenta embutido no stream de texto do /chat (a interface tira e mostra à parte)."""
    return MARCA + json.dumps(evento, ensure_ascii=False, separators=(",", ":")) + MARCA


# ── desfazer ─────────────────────────────────────────────────────────────────
def desfazer(aid, forcar=False):
    """Volta uma ação do modelo: memórias ao estado anterior (ou apagadas, se foram criadas) e o grafo
    ao 'antes' só nas chaves que a ação mudou. Recusa (conflito) se a memória foi editada depois,
    a menos que forcar=True. Devolve {"ok", "erro"?}."""
    with _lock:
        d = _carregar_log()
        item = next((x for x in d["acoes"] if x.get("id") == aid), None)
    if not item:
        return {"ok": False, "erro": "nao_encontrada"}
    if item.get("desfeita"):
        return {"ok": False, "erro": "ja_desfeita"}
    if not forcar:
        for mid, upd in (item.get("mem_depois") or {}).items():
            try:
                atual = memstore.memoria_obter(mid).get("updated")
            except memstore.ErroAPI:
                continue
            if upd and atual != upd:
                return {"ok": False, "erro": "conflito"}
    for mid, antes in (item.get("mem_antes") or {}).items():
        try:
            if antes is None:
                memstore.memoria_excluir(mid)
            else:
                memstore.memoria_atualizar(mid, {"content": antes["content"], "type": antes.get("type") or "note",
                                                 "tags": antes.get("tags") or [], "importance": antes.get("importance", 3),
                                                 "project": antes.get("project"), "pinned": bool(antes.get("pinned"))})
        except memstore.ErroAPI as e:
            if e.status != 404:
                return {"ok": False, "erro": e.codigo}
    gd = item.get("grafo_antes") or {}
    if gd.get("nos") or gd.get("arestas"):
        with skills._grafo_lock:
            g = skills.carregar_grafo()
            for k, antes in (gd.get("arestas") or {}).items():
                if antes is None:
                    g["arestas"].pop(k, None)
                else:
                    g["arestas"][k] = antes
            for k, antes in (gd.get("nos") or {}).items():
                if antes is None:
                    if k in g["nos"]:
                        memstore._remover_no(g, k)
                else:
                    g["nos"][k] = antes
            skills._salvar_grafo(g)
        skills.emitir("grafo", {"nos": len(g["nos"])})
    with _lock:
        d = _carregar_log()
        for x in d["acoes"]:
            if x.get("id") == aid:
                x["desfeita"] = True
                x["desfeita_em"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _salvar_log(d)
    return {"ok": True}
