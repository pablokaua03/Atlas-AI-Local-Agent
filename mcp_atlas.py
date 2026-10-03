"""
mcp_atlas — servidor MCP (Model Context Protocol) da memória do Atlas.

Deixa qualquer cliente MCP (Claude Desktop, Claude Code, Cursor, VS Code,
LM Studio...) usar a memória do Atlas: projetos, memórias e grafo.
Fala JSON-RPC por stdio e repassa tudo pra API local /v1 (o Atlas precisa
estar rodando). Só biblioteca padrão, sem dependências.

Configuração (ex.: claude_desktop_config.json):
  {"mcpServers": {"atlas": {"command": "python",
                            "args": ["C:/caminho/atlas/mcp_atlas.py"]}}}

Variáveis opcionais:
  ATLAS_URL    (padrão http://127.0.0.1:5005)
  ATLAS_TOKEN  (padrão: lido do config.json ao lado deste arquivo)
  ATLAS_SOURCE (nome gravado como 'source' nas memórias; padrão "mcp")
"""
import os
import sys
import json
import urllib.parse
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("ATLAS_URL", "http://127.0.0.1:5005").rstrip("/")
FONTE = os.environ.get("ATLAS_SOURCE", "mcp")
VERSOES = ("2025-06-18", "2025-03-26", "2024-11-05")
_abrir = urllib.request.build_opener(urllib.request.ProxyHandler({})).open   # local: ignora proxy do sistema


def _token():
    tk = os.environ.get("ATLAS_TOKEN", "").strip()
    if tk:
        return tk
    try:
        with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("api_token", "")
    except Exception:
        return ""


def _api(metodo, caminho, corpo=None, query=None):
    url = URL + caminho
    if query:
        query = {k: v for k, v in query.items() if v not in (None, "")}
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
    data = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(url, data=data, method=metodo, headers={
        "Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        with _abrir(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise RuntimeError(f"Atlas API {e.code}: {msg or e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Atlas is not reachable at {URL} ({e.reason}). Start Atlas first.")


def _q(v):
    return urllib.parse.quote(str(v), safe="")


# ── ferramentas ───────────────────────────────────────────────────────────────
def _s(tipo, desc, **extra):
    return {"type": tipo, "description": desc, **extra}


_STR_LIST = {"type": "array", "items": {"type": "string"}}
_SCOPE = {"type": "string", "enum": ["exact", "inherit", "tree", "all"],
          "description": "exact = only this project; inherit (default) = this project + parent projects + general "
                         "memory; tree = this project + its subprojects; all = everything."}

FERRAMENTAS = [
    {"name": "atlas_get_context",
     "description": "Get a ready-to-use context block from Atlas memory (facts about the user, memories relevant to the "
                    "query and knowledge graph links). Call this at the start of a task or when you need to recall.",
     "inputSchema": {"type": "object", "properties": {
         "query": _s("string", "What you need to remember about (topic, question, task)."),
         "project": _s("string", "Focus on one project (id or name). By default its parent projects and the "
                                 "general memory are included too (inherited)."),
         "scope": _SCOPE,
         "limit": _s("integer", "Max memories (default 8).")}},
     "annotations": {"readOnlyHint": True},
     "run": lambda a: _api("POST", "/v1/context", {"query": a.get("query", ""), "project": a.get("project"),
                                                    "limit": a.get("limit", 8), "scope": a.get("scope")})},
    {"name": "atlas_remember",
     "description": "Store a durable memory in Atlas (fact, preference, decision, task, note, event). Keep each memory "
                    "one self-contained statement. Exact duplicates are ignored.",
     "inputSchema": {"type": "object", "required": ["content"], "properties": {
         "content": _s("string", "The memory, as one clear self-contained statement."),
         "project": _s("string", "Project id or name (default 'geral'; created if missing)."),
         "type": _s("string", "fact | preference | decision | task | note | event (default note)."),
         "tags": {**_STR_LIST, "description": "Short tags."},
         "importance": _s("integer", "1 (trivial) to 5 (critical). Default 3.", minimum=1, maximum=5),
         "entities": {**_STR_LIST, "description": "Names of people, projects, tools... this memory is about. "
                                                  "They become linked nodes in the knowledge graph."}}},
     "run": lambda a: _api("POST", "/v1/memories", {**a, "source": a.get("source") or FONTE})},
    {"name": "atlas_search_memories",
     "description": "Search Atlas memories (keyword + semantic). Returns the best matches with ids and scores.",
     "inputSchema": {"type": "object", "properties": {
         "query": _s("string", "Search text. Empty returns the most important memories."),
         "project": _s("string", "Project id or name."), "scope": _SCOPE,
         "tags": {**_STR_LIST, "description": "Every tag must match."},
         "type": _s("string", "Memory type filter."),
         "limit": _s("integer", "Max results (default 10).")}},
     "annotations": {"readOnlyHint": True},
     "run": lambda a: _api("POST", "/v1/memories/search", a)},
    {"name": "atlas_list_memories",
     "description": "List memories, newest first (or by importance), with optional filters and pagination.",
     "inputSchema": {"type": "object", "properties": {
         "project": _s("string", "Project id or name."), "type": _s("string", "Memory type."),
         "scope": {**_SCOPE, "description": "exact (default) | inherit | tree | all"},
         "tag": _s("string", "Tag filter."), "sort": _s("string", "recent | importance"),
         "limit": _s("integer", "Default 50."), "offset": _s("integer", "Default 0.")}},
     "annotations": {"readOnlyHint": True},
     "run": lambda a: _api("GET", "/v1/memories", query=a)},
    {"name": "atlas_update_memory",
     "description": "Correct or enrich an existing memory by id (content, project, type, tags, add_tags, importance, "
                    "entities, expires_at).",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {
         "id": _s("string", "Memory id (mem_...)."), "content": _s("string", "New content."),
         "project": _s("string", "Move to this project."), "type": _s("string", "New type."),
         "tags": {**_STR_LIST, "description": "Replace tags."}, "add_tags": {**_STR_LIST, "description": "Add tags."},
         "importance": _s("integer", "1-5."), "entities": {**_STR_LIST, "description": "Replace linked entities."},
         "expires_at": _s("string", "ISO date-time after which it is ignored (empty to clear).")}},
     "run": lambda a: _api("PATCH", f"/v1/memories/{_q(a['id'])}", {k: v for k, v in a.items() if k != "id"})},
    {"name": "atlas_forget",
     "description": "Permanently delete a memory by id.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": _s("string", "Memory id.")}},
     "annotations": {"destructiveHint": True},
     "run": lambda a: _api("DELETE", f"/v1/memories/{_q(a['id'])}")},
    {"name": "atlas_project_tree",
     "description": "Get the project hierarchy as a tree (general memory at the root, projects and subprojects "
                    "below, with memory counts). A project's memory inherits from its parents and the general memory.",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True},
     "run": lambda a: _api("GET", "/v1/projects/tree")},
    {"name": "atlas_list_projects",
     "description": "List Atlas projects (in tree order) with parent, path and memory/node counts.",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True},
     "run": lambda a: _api("GET", "/v1/projects")},
    {"name": "atlas_create_project",
     "description": "Create a project (a workspace that groups memories and graph nodes). Pass parent to create a "
                    "subproject; it inherits the parent's memory.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": _s("string", "Project name."), "description": _s("string", "What the project is about."),
         "parent": _s("string", "Parent project id or name (omit for a top-level project)."),
         "tags": {**_STR_LIST, "description": "Tags."}}},
     "run": lambda a: _api("POST", "/v1/projects", a)},
    {"name": "atlas_update_project",
     "description": "Rename a project, change its description/tags, or move it in the hierarchy (parent).",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {
         "id": _s("string", "Project id or name."), "name": _s("string", "New name."),
         "parent": _s("string", "New parent project ('geral' or empty = top level)."),
         "description": _s("string", "New description."), "tags": {**_STR_LIST, "description": "Replace tags."}}},
     "run": lambda a: _api("PATCH", f"/v1/projects/{_q(a['id'])}", {k: v for k, v in a.items() if k != "id"})},
    {"name": "atlas_delete_project",
     "description": "Delete a project. Fails if it still has memories unless cascade is true (deletes them too). "
                    "Its subprojects move up one level.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {
         "id": _s("string", "Project id or name."), "cascade": _s("boolean", "Also delete its memories.")}},
     "annotations": {"destructiveHint": True},
     "run": lambda a: _api("DELETE", f"/v1/projects/{_q(a['id'])}",
                           query={"cascade": "true" if a.get("cascade") else None})},
    {"name": "atlas_graph_search",
     "description": "Find nodes in the knowledge graph by text, project or type.",
     "inputSchema": {"type": "object", "properties": {
         "q": _s("string", "Text to match."), "project": _s("string", "Project id or name."),
         "type": _s("string", "Node type (pessoa, projeto, tecnologia...)."), "limit": _s("integer", "Default 50.")}},
     "annotations": {"readOnlyHint": True},
     "run": lambda a: _api("GET", "/v1/graph/nodes", query=a)},
    {"name": "atlas_graph_node",
     "description": "Get one graph node with its incoming/outgoing links and related memories. With depth > 0, "
                    "returns the neighborhood subgraph instead.",
     "inputSchema": {"type": "object", "required": ["node"], "properties": {
         "node": _s("string", "Node key or label."), "depth": _s("integer", "0 = node details; 1-4 = neighbors.")}},
     "annotations": {"readOnlyHint": True},
     "run": lambda a: (_api("GET", f"/v1/graph/nodes/{_q(a['node'])}/neighbors", query={"depth": a["depth"]})
                       if a.get("depth") else _api("GET", f"/v1/graph/nodes/{_q(a['node'])}"))},
    {"name": "atlas_graph_project",
     "description": "Get the whole knowledge graph, or only the subgraph of one project.",
     "inputSchema": {"type": "object", "properties": {"project": _s("string", "Project id or name."),
                                                     "scope": _s("string", "exact | tree (include subprojects)")}},
     "annotations": {"readOnlyHint": True},
     "run": lambda a: _api("GET", "/v1/graph", query=a)},
    {"name": "atlas_graph_add_node",
     "description": "Add a concept to the knowledge graph (reuses a matching node if it already exists).",
     "inputSchema": {"type": "object", "required": ["label"], "properties": {
         "label": _s("string", "Node name."), "type": _s("string", "pessoa, projeto, tecnologia, lugar, tema..."),
         "project": _s("string", "Attach to this project."), "description": _s("string", "Short description.")}},
     "run": lambda a: _api("POST", "/v1/graph/nodes", a)},
    {"name": "atlas_graph_update_node",
     "description": "Rename a node, change its type/description, or attach/detach it from a project.",
     "inputSchema": {"type": "object", "required": ["node"], "properties": {
         "node": _s("string", "Node key or label."), "label": _s("string", "New label."),
         "type": _s("string", "New type."), "description": _s("string", "New description."),
         "add_project": _s("string", "Project to attach."), "remove_project": _s("string", "Project to detach.")}},
     "run": lambda a: _api("PATCH", f"/v1/graph/nodes/{_q(a['node'])}", {k: v for k, v in a.items() if k != "node"})},
    {"name": "atlas_graph_link",
     "description": "Create a relation between two concepts (from -rel-> to). Missing nodes are created.",
     "inputSchema": {"type": "object", "required": ["from", "to"], "properties": {
         "from": _s("string", "Source node label or key."), "to": _s("string", "Target node label or key."),
         "rel": _s("string", "Short verb, e.g. 'uses', 'works on', 'depends on'."),
         "project": _s("string", "Attach both nodes to this project.")}},
     "run": lambda a: _api("POST", "/v1/graph/edges", a)},
    {"name": "atlas_graph_unlink",
     "description": "Remove the relation between two nodes.",
     "inputSchema": {"type": "object", "required": ["from", "to"], "properties": {
         "from": _s("string", "Node label or key."), "to": _s("string", "Node label or key.")}},
     "annotations": {"destructiveHint": True},
     "run": lambda a: _api("DELETE", "/v1/graph/edges", query={"from": a["from"], "to": a["to"]})},
    {"name": "atlas_graph_delete_node",
     "description": "Delete a node and all its relations from the knowledge graph.",
     "inputSchema": {"type": "object", "required": ["node"], "properties": {
         "node": _s("string", "Node key or label.")}},
     "annotations": {"destructiveHint": True},
     "run": lambda a: _api("DELETE", f"/v1/graph/nodes/{_q(a['node'])}")},
]
_POR_NOME = {f["name"]: f for f in FERRAMENTAS}

INSTRUCOES = (
    "Atlas is the user's local, private long-term memory. Use atlas_get_context before answering questions that may "
    "depend on past knowledge, and atlas_remember to save durable facts, preferences and decisions (one statement per "
    "memory, with a project when the work belongs to one). Projects form a hierarchy: a subproject inherits the "
    "memory of its parents and of the general memory, so save shared knowledge at the highest level where it applies "
    "(atlas_project_tree shows the structure). Use the graph tools to connect people, projects and tools."
)


# ── JSON-RPC ──────────────────────────────────────────────────────────────────
def _tratar(msg):
    metodo, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:                       # notificação (ex.: notifications/initialized)
        return None
    if metodo == "initialize":
        pedida = params.get("protocolVersion")
        return {"protocolVersion": pedida if pedida in VERSOES else VERSOES[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "atlas-memory", "version": "1.0.0"},
                "instructions": INSTRUCOES}
    if metodo == "ping":
        return {}
    if metodo == "tools/list":
        return {"tools": [{k: v for k, v in f.items() if k != "run"} for f in FERRAMENTAS]}
    if metodo == "tools/call":
        f = _POR_NOME.get(params.get("name"))
        if not f:
            raise LookupError(f"Unknown tool: {params.get('name')}")
        try:
            out = f["run"](params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
                    "isError": False}
        except Exception as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    raise NotImplementedError(metodo)


def _responder(resp):
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for enc in ("stdin", "stdout"):
        try:
            getattr(sys, enc).reconfigure(encoding="utf-8")
        except Exception:
            pass
    for linha in sys.stdin:
        linha = linha.strip()
        if not linha:
            continue
        try:
            msg = json.loads(linha)
        except Exception:
            _responder({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        lote = msg if isinstance(msg, list) else [msg]
        for m in lote:
            if not isinstance(m, dict):
                continue
            try:
                res = _tratar(m)
                if res is not None:
                    _responder({"jsonrpc": "2.0", "id": m.get("id"), "result": res})
            except LookupError as e:
                _responder({"jsonrpc": "2.0", "id": m.get("id"), "error": {"code": -32602, "message": str(e)}})
            except NotImplementedError as e:
                _responder({"jsonrpc": "2.0", "id": m.get("id"),
                            "error": {"code": -32601, "message": f"Method not found: {e}"}})
            except Exception as e:
                _responder({"jsonrpc": "2.0", "id": m.get("id"), "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
