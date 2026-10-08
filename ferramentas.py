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
import re
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
        "Search all the user's saved knowledge (memories, projects, graph, documents). Call it before answering "
        "about the user, their past, work, projects, people or decisions. Keep names/acronyms exactly as written.",
        {"query": _S("Keywords, with the user's exact names."), "project": _S("Optional project (omit = all)."),
         "pinned_only": _B("Only pinned memories."), "limit": _I("Max results (1-15).", minimum=1, maximum=15)}),
    "save_memory": _fn(
        "save_memory",
        "Save ONE durable fact, preference or decision the user states (or asks you to remember). "
        "No small talk, temporary things or secrets.",
        {"content": _S("One self-contained sentence, in the user's language."),
         "type": _S("Kind.", enum=_TIPOS_MEM), "project": _S("Optional project."),
         "tags": {"type": "array", "items": {"type": "string"}},
         "importance": _I("1-5.", minimum=1, maximum=5),
         "entities": {"type": "array", "items": {"type": "string"}, "description": "People/things it mentions."}},
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


# "pensar" (raciocínio explícito dos modelos que pensam, ex. qwen3.5) quando há ferramentas:
#   nunca  = desligado (mais rápido; padrão)
#   auto   = só na rodada que responde a partir de uma busca na memória
#   sempre = em todas as rodadas
PENSAR = ("nunca", "auto", "sempre")


def config(cfg=None) -> dict:
    cfg = cfg or core.carregar_config()
    f = cfg.get("ferramentas") or {}
    pensar = f.get("pensar") if f.get("pensar") in PENSAR else "nunca"
    return {"ativo": f.get("ativo", True) is not False, "escrita": f.get("escrita", True) is not False,
            "max_rodadas": int(f.get("max_rodadas") or 4), "busca_auto": f.get("busca_auto", True) is not False,
            "pensar": pensar}


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
    "pt": ("\n\nVocê tem ferramentas para consultar e atualizar a memória do usuário (memórias, projetos, grafo, "
           "conversas anteriores{docs}). Regras: (1) pergunta sobre o usuário, o passado, o trabalho, projetos, pessoas "
           "ou decisões dele: chame search_memories ANTES de responder, com os nomes e siglas exatamente como ele "
           "escreveu; (2) nunca diga que vai procurar sem chamar a ferramenta na mesma resposta; (3) se ele duvidar "
           "('certeza?', 'procura de novo'), procure de novo de forma mais ampla; (4) responda com base no que achou e "
           "cite o projeto ou a memória de onde veio; se não achar nada, diga em uma frase onde procurou. Salve só "
           "fatos, preferências e decisões duráveis (ou quando ele pedir para lembrar). Nunca salve senhas ou dados "
           "sensíveis. Para conversa simples, responda direto, sem ferramentas."),
    "en": ("\n\nYou have tools to look up and update the user's memory (memories, projects, graph, past "
           "conversations{docs}). Rules: (1) for questions about the user, their past, work, projects, people or "
           "decisions, call search_memories BEFORE answering, with names and acronyms exactly as written; (2) never say "
           "you will look something up without calling the tool in the same reply; (3) if the user doubts you ('are you "
           "sure?', 'look again'), search again more broadly; (4) answer from what you found and name the project or "
           "memory it came from; if nothing is found, say in one sentence where you looked. Only save durable facts, "
           "preferences and decisions (or when asked to remember). Never save passwords or sensitive data. For small "
           "talk, answer directly without tools."),
    "es": ("\n\nTienes herramientas para consultar y actualizar la memoria del usuario (memorias, proyectos, grafo, "
           "conversaciones anteriores{docs}). Reglas: (1) para preguntas sobre el usuario, su pasado, trabajo, proyectos, "
           "personas o decisiones, llama a search_memories ANTES de responder, con los nombres y siglas tal como los "
           "escribió; (2) nunca digas que vas a buscar sin llamar a la herramienta en la misma respuesta; (3) si duda "
           "('¿seguro?', 'busca otra vez'), busca de nuevo de forma más amplia; (4) responde con lo que encontraste y "
           "cita el proyecto o la memoria; si no hay nada, di en una frase dónde buscaste. Guarda solo hechos, "
           "preferencias y decisiones duraderas (o cuando lo pida). Nunca guardes contraseñas ni datos sensibles. Para "
           "charla simple, responde directo, sin herramientas."),
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
        return {"count": len(itens), "memories": [_mem_out(m) for m in itens]}
    extras = [ctx.get("texto_usuario") or ""]
    ampla = bool(ctx.get("ampla")) or _bool(a.get("broad"), False)
    if ampla:
        extras.append(ctx.get("anterior") or "")
    return buscar_tudo(_str(a.get("query"), 500), ctx, limite=lim, projeto=proj, ampla=ampla, extras=extras)


# ── busca ampla: memórias + projetos + grafo + documentos (+ conversas) ─────────
_SIGLA_PARTIDA = re.compile(r"\b([A-Z]{2,})\s+([A-Z])\b")


def consertar_siglas(q, referencia=""):
    """'ACM E' → 'ACME' quando a junção aparece na mensagem do usuário (modelos pequenos quebram siglas)."""
    ref = set(re.findall(r"\b[A-Z][A-Z0-9]{1,}\b", referencia or ""))

    def junta(mt):
        j = mt.group(1) + mt.group(2)
        return j if (j in ref or not ref) else mt.group(0)
    return _SIGLA_PARTIDA.sub(junta, q or "")


def _sem_geral(proj):
    """'geral' é a raiz: buscar 'na geral' significa buscar em tudo."""
    if not proj:
        return None
    try:
        info = memstore.projeto_obter(proj)
        return None if info.get("id") == memstore.PROJETO_PADRAO else info.get("id")
    except memstore.ErroAPI:
        return None                                  # projeto que não existe: busca em tudo


def _juntar(listas, limite):
    melhor = {}
    for itens in listas:
        for m in itens:
            if m["id"] not in melhor or m.get("score", 0) > melhor[m["id"]].get("score", 0):
                melhor[m["id"]] = m
    out = sorted(melhor.values(), key=lambda m: m.get("score", 0), reverse=True)
    if out and out[0].get("score"):
        corte = max(0.08, 0.35 * float(out[0]["score"]))
        out = [m for m in out if float(m.get("score") or 0) >= corte]
    return out[:limite]


def buscar_tudo(query, ctx, limite=6, projeto=None, ampla=False, extras=()):
    """Busca em tudo o que o usuário guardou. As frases do usuário (extras) entram como consultas
    extras, para que nomes e siglas exatos sempre contem, mesmo se o modelo reescrever a consulta.
    Na geral (ou sem projeto) busca em todos os projetos; num projeto, inclui pais e subprojetos e,
    se achar pouco, amplia para todos. Devolve um resultado compacto para caber em contextos de 4k."""
    cfg = ctx.get("cfg") or {}
    ref = " ".join(x for x in extras if x)
    query = consertar_siglas(query, ref)
    consultas = [x for x in dict.fromkeys([query] + [consertar_siglas(e, ref) for e in extras]) if x and x.strip()]
    if not consultas:
        consultas = [""]
    if ampla:
        limite = max(limite, 10)
        projeto = None
    pid = _sem_geral(projeto)
    sem = bool((cfg.get("contexto") or {}).get("busca_semantica"))
    n = max(limite * 2, 12)

    def busca(escopo_pid, escopo):
        return [memstore.memorias_buscar(q, projeto=escopo_pid, limite=n, semantica=sem, escopo=escopo)["items"]
                for q in consultas]

    if pid:
        mems = _juntar(busca(pid, "inherit") + busca(pid, "tree"), limite)
        ampliou = len(mems) < 2
        if ampliou:
            mems = _juntar(busca(None, "all"), limite)
    else:
        mems, ampliou = _juntar(busca(None, "all"), limite), False

    projs = {}
    for q in consultas:
        for p in memstore.projetos_buscar(q, limite=3):
            if p["id"] not in projs or p["score"] > projs[p["id"]]["score"]:
                projs[p["id"]] = p
    projs = sorted(projs.values(), key=lambda p: p["score"], reverse=True)[:4 if ampla else 3]

    conceitos = []
    if core.habilidade("memoria", cfg) or core.habilidade("grafo", cfg):
        vistos = set()
        for q in consultas[:2]:
            for c in _graph_search({"query": q, "limit": 3}, ctx)["concepts"]:
                if c["key"] not in vistos:
                    vistos.add(c["key"])
                    conceitos.append({"label": c["label"], "description": c.get("description", "")[:200],
                                      "relations": c["relations"][:6]})
        conceitos = conceitos[:4]

    documentos, docs_estado = [], "off"
    if core.habilidade("documentos", cfg):
        import docs
        docs_estado = "on"
        try:
            for q in consultas[:2]:
                t = docs.consultar(q, k=3, limite=1500)
                if t and t not in documentos:
                    documentos.append(t)
        except Exception:
            docs_estado = "error"

    conversas = ""
    if ampla:
        for q in consultas:
            t = chats.recall(q, excluir_id=ctx.get("chat_id"), max_trechos=3) if q.strip() else ""
            if t:
                conversas = t[:800]
                break

    nomes_proj = {p["id"]: p["name"] for p in memstore.projetos_listar()}
    corte = 300 if ampla else 400
    # ordem pensada para o corte de MAX_RESULTADO: o mais curto e decisivo (projetos) vem antes
    out = {"count": len(mems), "searched": {"queries": consultas[:3], "projects": "all" if (not pid or ampliou) else pid}}
    if projs:
        out["projects"] = [{"id": p["id"], "name": p["name"], "description": p["description"][:400],
                            "memories": p["memories"]} for p in projs]
    out["memories"] = [{**_mem_out(m), "content": _mem_out(m)["content"][:corte],
                        "project_name": nomes_proj.get(m.get("project"), m.get("project"))} for m in mems]
    for m in out["memories"]:
        for k in ("tags", "pinned", "updated"):
            m.pop(k, None)
    if conceitos:
        out["concepts"] = conceitos
    if documentos:
        out["documents"] = "\n\n".join(documentos)[:2000]
    if conversas:
        out["past_conversations"] = conversas
    if ampla and (mems or projs or documentos or conversas):
        out["note"] = ("The user doubts the previous answer. Re-check it against these results and answer in full: "
                       "confirm or correct it, and name the project or memory the answer comes from.")
    if not (mems or projs or documentos or conversas):
        out["found"] = False
        out["checked"] = {"memories": memstore.memorias_listar(limite=1)["total"], "projects": len(nomes_proj),
                          "graph": "yes" if (core.habilidade("memoria", cfg) or core.habilidade("grafo", cfg)) else "no",
                          "documents": docs_estado,
                          "past_conversations": "yes" if ampla else "no"}
        out["note"] = ("Nothing matched. Do not invent. Tell the user in one sentence what you checked "
                       "(memories in every project, project descriptions, documents) and ask for a detail.")
    return out


# ── quando buscar sozinho ────────────────────────────────────────────────────
def _n(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))


_RE_INSISTE = re.compile(
    r"(\b(tem |voce tem |vc tem |ta |esta |estas )?certeza\b|\bcerteza\?|\b(procura|procure|busca|busque|olha|olhe|"
    r"verifica|verifique|checa|cheque|tenta|tente|pesquisa|pesquise)\b.{0,20}\b(de novo|denovo|novamente|melhor|direito|"
    r"outra vez|mais)\b|\bnao (se )?lembra\b|\bnao (tem|achou|encontrou) nada\b|\bare you sure\b|\b(look|search|"
    r"check|try) (again|harder|better)\b|\byou (don'?t|do not) remember\b|^\s*(estas |esta )?segur[oa]\b|"
    r"\bbusca otra vez\b|\bno (te )?acuerdas\b)")
_RE_PESSOAL = re.compile(
    r"\b(meu|minha|meus|minhas|eu|comigo|nosso|nossa|nossos|nossas|a gente|lembra|lembrar|lembro|lembre|"
    r"recorda|voce sabe|vc sabe|sabe (o|a|quem|qual|quando|onde|se)|my|mine|i|our|we|remember|recall|"
    r"do you know|mi|mis|yo|nuestro|nuestra|recuerdas)\b")
_RE_PERGUNTA = re.compile(
    r"(\?|^\s*(qual|quais|quando|onde|quem|o que|quanto|quantos|quantas|por que|what|when|where|who|which|"
    r"did i|did we|have i|cual|cuando|donde|quien)\b)")
_RE_PROMESSA = re.compile(
    r"\b(vou|vamos|deixa eu|deixe-me|deixe me|irei|preciso) (procurar|buscar|verificar|checar|pesquisar|consultar|"
    r"olhar|dar uma olhada|conferir)\b|\bvamos (la )?(ver|procurar)\b|\b(let me|i'?ll|i will|let's) (search|look|"
    r"check|find|go through)\b|\b(voy a|vamos a|dejame) (buscar|revisar|verificar|mirar)\b")
_RE_SEM_INFO = re.compile(
    r"\bnao (tenho|encontrei|achei|possuo|sei|lembro)\b.{0,40}\b(informac|registr|dado|nada|detalhe|memoria|isso)|"
    r"\bnao tenho (essa|esta|nenhuma|a) informac|\bi (don'?t|do not) (have|know|remember|recall)\b|"
    r"\b(no|any) (information|record|memory)\b|\bno tengo (esa |ninguna )?informaci")


def _nomes_conhecidos():
    """Nomes de projetos e conceitos do grafo (para notar 'ACME', 'Aurora'...)."""
    nomes = set()
    try:
        for p in memstore.projetos_listar():
            if p.get("id") != memstore.PROJETO_PADRAO:
                nomes.update(t for t in memstore._tokens(p.get("name") or "") if len(t) >= 3)
        for k, nd in (skills.carregar_grafo().get("nos") or {}).items():
            lab = nd.get("label") or k
            if len(lab) >= 3:
                nomes.add(_n(lab))
    except Exception:
        pass
    return nomes


def insistencia(texto):
    """'Certeza?', 'procura de novo', 'are you sure?'... (mensagem curta duvidando da resposta)."""
    t = _n(texto).strip()
    return bool(t) and len(t) <= 160 and bool(_RE_INSISTE.search(t))


def classificar(texto, anterior=""):
    """None (conversa normal), 'memoria' (pergunta sobre o usuário/projetos) ou 'ampla' (o usuário
    duvidou da resposta anterior). Barato e conservador: conversa simples não dispara nada."""
    t = _n(texto).strip()
    if not t:
        return None
    if anterior and insistencia(texto):
        return "ampla"
    if not _RE_PERGUNTA.search(t):
        return None
    if _RE_PESSOAL.search(t):
        return "memoria"
    # nome próprio conhecido escrito com maiúscula ("ACME", "Aurora"): também é sobre a memória
    nomes = _nomes_conhecidos()
    for w in re.findall(r"\b[A-Z][\w-]{2,}", texto or ""):
        wn = _n(w)
        if wn in nomes or any(wn == x.split()[0] for x in nomes if " " in x):
            return "memoria"
    return None


def promete_buscar(texto, so_promessa=False):
    """O modelo disse que ia procurar ('vou procurar', 'let me check')? Sem so_promessa, também
    conta 'não tenho essa informação' (que só vale depois de procurar)."""
    t = _n(texto)
    return bool(_RE_PROMESSA.search(t) or (not so_promessa and _RE_SEM_INFO.search(t)))


def consulta_auto(texto, anterior, modo):
    return ((anterior or "") + " " + (texto or "")).strip() if modo == "ampla" else (texto or "")


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
        partes = [f"{res.get('count', 0)} memória(s)"]
        if res.get("projects"):
            partes.append(f"{len(res['projects'])} projeto(s)")
        if res.get("documents"):
            partes.append("documentos")
        if res.get("past_conversations"):
            partes.append("conversas")
        return ", ".join(partes)
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


# ── caber na janela do modelo ────────────────────────────────────────────────
# Em janelas de 4k tokens, esquemas das ferramentas + prompt + histórico + um resultado grande
# estouravam o contexto: o Ollama cortava o começo da conversa e a resposta parava no meio
# (done_reason "length"). Agora cada resultado é encolhido para o espaço que realmente sobra.
CHARS_TOKEN_JANELA = 3.3        # PT/EN/JSON medido no qwen3.5 (~3,6); um pouco de folga
RESERVA_RESPOSTA = 2400         # ~700 tokens para a resposta
RESERVA_PENSAR = 2000           # + ~600 tokens quando o "pensar" está ligado
MIN_RESULTADO = 1500            # nunca menos que isso para um resultado de ferramenta


def janela_chars(num_ctx) -> int:
    return int(int(num_ctx or 4096) * CHARS_TOKEN_JANELA)


def _encolher(res, limite):
    """Tira primeiro o que é acessório (conversas, documentos, conceitos), depois encurta e corta
    memórias do fim (as menos relevantes). Projetos e a nota ficam."""
    r = copy.deepcopy(res)
    caber = lambda: len(json.dumps(r, ensure_ascii=False, default=str)) <= limite   # noqa: E731
    passos = [
        lambda: r.pop("past_conversations", None),
        lambda: r.__setitem__("documents", r["documents"][:800]) if r.get("documents") else None,
        lambda: r.pop("concepts", None),
        lambda: [m.__setitem__("content", m["content"][:220]) for m in r.get("memories") or []],
        lambda: r.pop("documents", None),
        lambda: [p.__setitem__("description", p["description"][:220]) for p in r.get("projects") or []],
    ]
    for passo in passos:
        if caber():
            return r
        passo()
    while not caber() and r.get("memories"):
        r["memories"].pop()
        r["count"] = len(r["memories"])
    return r


def resultado_texto(res, limite=None) -> str:
    limite = max(MIN_RESULTADO, min(MAX_RESULTADO, int(limite))) if limite is not None else MAX_RESULTADO
    t = json.dumps(res, ensure_ascii=False, default=str)
    if len(t) > limite and isinstance(res, dict):
        t = json.dumps(_encolher(res, limite), ensure_ascii=False, default=str)
    return t if len(t) <= limite else t[:limite] + "…(truncated)"


def busca_servidor(modo, texto, anterior, ctx):
    """A busca que o servidor faz sozinho ('memoria' ou 'ampla'). Mesmo caminho da ferramenta
    (executar), com o evento marcado como automático. Devolve o resultado de executar + 'args'."""
    args = {"query": consulta_auto(texto, anterior, modo), "limit": 10 if modo == "ampla" else 6}
    res = executar("search_memories", args, {**ctx, "ampla": modo == "ampla"})
    res["evento"]["auto"] = True
    res["args"] = args
    return res


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
