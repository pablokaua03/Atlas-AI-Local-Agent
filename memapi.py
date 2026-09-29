"""
memapi — API REST de memória do Atlas (/v1), para qualquer IA.

Qualquer agente (Claude, ChatGPT, scripts, n8n, outro LLM local...) pode criar
projetos, gravar e buscar memórias e ler/editar o grafo de conhecimento.

Segurança:
  - só escuta em 127.0.0.1 (como o resto do Atlas);
  - toda chamada exige o token:  Authorization: Bearer <token>
    (ou o cabeçalho X-Atlas-Token). O token fica em config.json e aparece em
    Configurações → API de memória;
  - o cabeçalho Host precisa ser 127.0.0.1/localhost (bloqueia DNS rebinding);
  - com o cofre travado, as rotas respondem 423.

Especificação: GET /v1/openapi.json  ·  Guia: API.md
"""
import hmac
import secrets

from flask import Blueprint, request, jsonify

import core
import cofre
import memstore
from memstore import ErroAPI

VERSAO = "1.0.0"
bp = Blueprint("memapi", __name__)
_hosts = set()


def registrar(app, host, port):
    _hosts.update({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"{host}:{port}"})
    app.register_blueprint(bp)


# ── token ─────────────────────────────────────────────────────────────────────
def token(gerar=True):
    cfg = core.carregar_config()
    tk = cfg.get("api_token") or ""
    if not tk and gerar:
        tk = "atlas_" + secrets.token_urlsafe(32)
        cfg["api_token"] = tk
        core.salvar_config(cfg)
    return tk


def girar_token():
    cfg = core.carregar_config()
    cfg["api_token"] = "atlas_" + secrets.token_urlsafe(32)
    core.salvar_config(cfg)
    return cfg["api_token"]


def host_local():
    return request.host in _hosts


def _erro(status, codigo, msg):
    return jsonify({"error": {"code": codigo, "message": msg}}), status


@bp.before_request
def _guarda():
    if not host_local():
        return _erro(403, "forbidden_host", "Requests must target 127.0.0.1 or localhost.")
    if request.endpoint == "memapi.openapi":
        return None
    # o interruptor desliga o acesso das outras IAs; a própria interface do Atlas
    # (que também manda o token) continua funcionando
    if not core.carregar_config().get("api_ativa", True) and request.headers.get("X-Atlas-UI") != "1":
        return _erro(503, "api_disabled", "The memory API is turned off in Atlas settings.")
    auth = request.headers.get("Authorization", "")
    enviado = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-Atlas-Token", "")
    if not enviado or not hmac.compare_digest(enviado.encode(), token().encode()):
        return _erro(401, "unauthorized", "Missing or invalid token. Use 'Authorization: Bearer <token>'.")
    return None


@bp.errorhandler(ErroAPI)
def _erro_api(e):
    return _erro(e.status, e.codigo, e.msg)


@bp.errorhandler(ValueError)
def _erro_valor(e):
    return _erro(400, "invalid_request", str(e))


def _corpo():
    d = request.get_json(force=True, silent=True)
    if d is None:
        d = {}
    if not isinstance(d, dict):
        raise ErroAPI(400, "invalid_request", "The JSON body must be an object.")
    return d


def _arg_bool(nome, padrao=False):
    v = request.args.get(nome)
    if v is None:
        return padrao
    return v.lower() in ("1", "true", "yes", "sim")


def _arg_tags():
    tags = request.args.getlist("tag")
    if request.args.get("tags"):
        tags += request.args["tags"].split(",")
    return tags


# ── info ──────────────────────────────────────────────────────────────────────
@bp.get("/v1")
def info():
    return jsonify({
        "name": "Atlas Memory API", "version": VERSAO,
        "vault": cofre.estado(), "default_project": memstore.PROJETO_PADRAO,
        "docs": "/v1/openapi.json",
    })


@bp.get("/v1/openapi.json")
def openapi():
    return jsonify(_openapi())


# ── projetos ──────────────────────────────────────────────────────────────────
@bp.get("/v1/projects")
def projetos_listar():
    return jsonify({"items": memstore.projetos_listar()})


@bp.post("/v1/projects")
def projeto_criar():
    d = _corpo()
    p = memstore.projeto_criar(d.get("name"), d.get("description", ""), d.get("tags"), d.get("meta"))
    return jsonify(p), 201


@bp.get("/v1/projects/<pid>")
def projeto_obter(pid):
    return jsonify(memstore.projeto_obter(pid))


@bp.patch("/v1/projects/<pid>")
def projeto_atualizar(pid):
    return jsonify(memstore.projeto_atualizar(pid, _corpo()))


@bp.delete("/v1/projects/<pid>")
def projeto_excluir(pid):
    return jsonify(memstore.projeto_excluir(pid, cascata=_arg_bool("cascade")))


# ── memórias ──────────────────────────────────────────────────────────────────
@bp.get("/v1/memories")
def memorias_listar():
    a = request.args
    if a.get("q"):
        return jsonify(memstore.memorias_buscar(a["q"], a.get("project"), _arg_tags(), a.get("type"),
                                                a.get("limit", 10, type=int)))
    return jsonify(memstore.memorias_listar(a.get("project"), _arg_tags(), a.get("type"), a.get("source"),
                                            a.get("limit", 50, type=int), a.get("offset", 0, type=int),
                                            a.get("sort", "recent")))


@bp.post("/v1/memories")
def memoria_criar():
    m = memstore.memoria_criar(_corpo())
    return jsonify(m), (200 if m.get("duplicate") else 201)


@bp.post("/v1/memories/batch")
def memorias_lote():
    itens = _corpo().get("items")
    if not isinstance(itens, list) or not itens:
        raise ErroAPI(400, "invalid_request", "'items' must be a non-empty list of memories.")
    if len(itens) > 200:
        raise ErroAPI(400, "invalid_request", "At most 200 memories per batch.")
    criadas, erros = [], []
    for i, item in enumerate(itens):
        try:
            if not isinstance(item, dict):
                raise ErroAPI(400, "invalid_request", "Each item must be an object.")
            criadas.append(memstore.memoria_criar(item))
        except ErroAPI as e:
            if e.status == 423:
                raise
            erros.append({"index": i, "code": e.codigo, "message": e.msg})
    return jsonify({"created": criadas, "errors": erros}), (201 if criadas else 400)


@bp.post("/v1/memories/search")
def memorias_buscar():
    d = _corpo()
    return jsonify(memstore.memorias_buscar(d.get("query", ""), d.get("project"), d.get("tags"),
                                            d.get("type"), d.get("limit", 10),
                                            semantica=bool(d.get("semantic", True))))


@bp.get("/v1/memories/<mid>")
def memoria_obter(mid):
    return jsonify(memstore.memoria_obter(mid))


@bp.patch("/v1/memories/<mid>")
def memoria_atualizar(mid):
    return jsonify(memstore.memoria_atualizar(mid, _corpo()))


@bp.delete("/v1/memories/<mid>")
def memoria_excluir(mid):
    return jsonify(memstore.memoria_excluir(mid))


# ── grafo ─────────────────────────────────────────────────────────────────────
@bp.get("/v1/graph")
def grafo():
    return jsonify(memstore.grafo(request.args.get("project")))


@bp.get("/v1/graph/nodes")
def nos_listar():
    a = request.args
    return jsonify(memstore.nos_listar(a.get("q", ""), a.get("project"), a.get("type"),
                                       a.get("limit", 50, type=int)))


@bp.post("/v1/graph/nodes")
def no_criar():
    d = _corpo()
    return jsonify(memstore.no_criar(d.get("label"), d.get("type") or "tema", d.get("project"),
                                     d.get("description", ""))), 201


@bp.get("/v1/graph/nodes/<path:ref>/neighbors")
def no_vizinhos(ref):
    return jsonify(memstore.vizinhos(ref, request.args.get("depth", 1, type=int)))


@bp.get("/v1/graph/nodes/<path:ref>")
def no_obter(ref):
    return jsonify(memstore.no_obter(ref))


@bp.patch("/v1/graph/nodes/<path:ref>")
def no_atualizar(ref):
    return jsonify(memstore.no_atualizar(ref, _corpo()))


@bp.delete("/v1/graph/nodes/<path:ref>")
def no_excluir(ref):
    return jsonify(memstore.no_excluir(ref))


@bp.post("/v1/graph/edges")
def aresta_criar():
    d = _corpo()
    return jsonify(memstore.aresta_criar(d.get("from"), d.get("to"), d.get("rel", ""), d.get("project"))), 201


@bp.delete("/v1/graph/edges")
def aresta_excluir():
    d = request.args if request.args.get("from") else _corpo()
    return jsonify(memstore.aresta_excluir(d.get("from"), d.get("to")))


# ── contexto / exportação ─────────────────────────────────────────────────────
@bp.post("/v1/context")
def contexto():
    d = _corpo()
    return jsonify(memstore.contexto(d.get("query", ""), d.get("project"), d.get("limit", 8),
                                     bool(d.get("include_graph", True)), bool(d.get("include_facts", True))))


@bp.get("/v1/export")
def exportar():
    return jsonify(memstore.exportar(request.args.get("project")))


# ── OpenAPI 3.1 ───────────────────────────────────────────────────────────────
def _openapi():
    def ref(n):
        return {"$ref": f"#/components/schemas/{n}"}

    def corpo(schema):
        return {"required": True, "content": {"application/json": {"schema": schema}}}

    def resp(schema, desc="OK"):
        return {"description": desc, "content": {"application/json": {"schema": schema}}}

    def q(nome, desc, tipo="string"):
        return {"name": nome, "in": "query", "required": False, "description": desc, "schema": {"type": tipo}}

    def p(nome, desc):
        return {"name": nome, "in": "path", "required": True, "description": desc, "schema": {"type": "string"}}

    lista = lambda n: {"type": "object", "properties": {"items": {"type": "array", "items": ref(n)}}}
    ok = resp({"type": "object"})
    return {
        "openapi": "3.1.0",
        "info": {"title": "Atlas Memory API", "version": VERSAO,
                 "description": "Local AI memory management: projects, memories and a knowledge graph. "
                                "Runs on 127.0.0.1. Every call needs 'Authorization: Bearer <token>' "
                                "(token shown in Atlas → Settings → Memory API)."},
        "servers": [{"url": "http://127.0.0.1:5005"}],
        "security": [{"bearer": []}],
        "paths": {
            "/v1": {"get": {"operationId": "getInfo", "summary": "API info and vault state", "responses": {"200": ok}}},
            "/v1/projects": {
                "get": {"operationId": "listProjects", "summary": "List projects with memory/node counts",
                        "responses": {"200": resp(lista("Project"))}},
                "post": {"operationId": "createProject", "summary": "Create a project (also adds a 'projeto' node to the graph)",
                         "requestBody": corpo(ref("ProjectInput")), "responses": {"201": resp(ref("Project"))}}},
            "/v1/projects/{id}": {
                "parameters": [p("id", "Project id or name")],
                "get": {"operationId": "getProject", "summary": "Get a project", "responses": {"200": resp(ref("Project"))}},
                "patch": {"operationId": "updateProject", "summary": "Update name, description, tags or meta",
                          "requestBody": corpo(ref("ProjectInput")), "responses": {"200": resp(ref("Project"))}},
                "delete": {"operationId": "deleteProject", "summary": "Delete a project",
                           "parameters": [q("cascade", "Also delete its memories", "boolean")], "responses": {"200": ok}}},
            "/v1/memories": {
                "get": {"operationId": "listMemories", "summary": "List memories (or search with q)",
                        "parameters": [q("project", "Project id"), q("q", "Search text"), q("tag", "Filter by tag (repeatable)"),
                                       q("type", "Memory type"), q("source", "Who wrote it"),
                                       q("sort", "recent | importance"), q("limit", "Max items", "integer"),
                                       q("offset", "Pagination offset", "integer")],
                        "responses": {"200": ok}},
                "post": {"operationId": "createMemory", "summary": "Store a memory (exact duplicates return the existing one)",
                         "requestBody": corpo(ref("MemoryInput")), "responses": {"201": resp(ref("Memory"))}}},
            "/v1/memories/batch": {"post": {
                "operationId": "createMemories", "summary": "Store up to 200 memories at once",
                "requestBody": corpo({"type": "object", "required": ["items"],
                                      "properties": {"items": {"type": "array", "items": ref("MemoryInput")}}}),
                "responses": {"201": ok}}},
            "/v1/memories/search": {"post": {
                "operationId": "searchMemories", "summary": "Hybrid keyword + semantic search",
                "requestBody": corpo({"type": "object", "properties": {
                    "query": {"type": "string"}, "project": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}}, "type": {"type": "string"},
                    "limit": {"type": "integer", "default": 10}, "semantic": {"type": "boolean", "default": True}}}),
                "responses": {"200": ok}}},
            "/v1/memories/{id}": {
                "parameters": [p("id", "Memory id")],
                "get": {"operationId": "getMemory", "summary": "Get a memory", "responses": {"200": resp(ref("Memory"))}},
                "patch": {"operationId": "updateMemory", "summary": "Update a memory (any MemoryInput field, plus add_tags)",
                          "requestBody": corpo(ref("MemoryInput")), "responses": {"200": resp(ref("Memory"))}},
                "delete": {"operationId": "deleteMemory", "summary": "Forget a memory", "responses": {"200": ok}}},
            "/v1/graph": {"get": {"operationId": "getGraph", "summary": "Whole graph, or a project's subgraph",
                                  "parameters": [q("project", "Project id")], "responses": {"200": ok}}},
            "/v1/graph/nodes": {
                "get": {"operationId": "listNodes", "summary": "Find nodes",
                        "parameters": [q("q", "Text"), q("project", "Project id"), q("type", "Node type"),
                                       q("limit", "Max items", "integer")], "responses": {"200": ok}},
                "post": {"operationId": "createNode", "summary": "Create a node (or reuse the matching one)",
                         "requestBody": corpo(ref("NodeInput")), "responses": {"201": resp(ref("Node"))}}},
            "/v1/graph/nodes/{ref}": {
                "parameters": [p("ref", "Node key or label")],
                "get": {"operationId": "getNode", "summary": "Node with its edges and related memories",
                        "responses": {"200": ok}},
                "patch": {"operationId": "updateNode", "summary": "Update label, type, description or projects",
                          "requestBody": corpo({"type": "object", "properties": {
                              "label": {"type": "string"}, "type": {"type": "string"}, "description": {"type": "string"},
                              "add_project": {"type": "string"}, "remove_project": {"type": "string"}}}),
                          "responses": {"200": resp(ref("Node"))}},
                "delete": {"operationId": "deleteNode", "summary": "Delete a node and its edges", "responses": {"200": ok}}},
            "/v1/graph/nodes/{ref}/neighbors": {"get": {
                "operationId": "getNeighbors", "summary": "Subgraph around a node",
                "parameters": [p("ref", "Node key or label"), q("depth", "Hops (1-4)", "integer")],
                "responses": {"200": ok}}},
            "/v1/graph/edges": {
                "post": {"operationId": "createEdge", "summary": "Link two nodes (missing nodes are created)",
                         "requestBody": corpo(ref("EdgeInput")), "responses": {"201": ok}},
                "delete": {"operationId": "deleteEdge", "summary": "Remove the link between two nodes",
                           "parameters": [q("from", "Node key or label"), q("to", "Node key or label")],
                           "responses": {"200": ok}}},
            "/v1/context": {"post": {
                "operationId": "getContext",
                "summary": "Ready-to-use context block (user facts, relevant memories, graph) for a prompt",
                "requestBody": corpo({"type": "object", "properties": {
                    "query": {"type": "string"}, "project": {"type": "string"}, "limit": {"type": "integer", "default": 8},
                    "include_graph": {"type": "boolean", "default": True},
                    "include_facts": {"type": "boolean", "default": True}}}),
                "responses": {"200": ok}}},
            "/v1/export": {"get": {"operationId": "exportData", "summary": "Export projects, memories and graph as JSON",
                                   "parameters": [q("project", "Only this project")], "responses": {"200": ok}}},
        },
        "components": {
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "ProjectInput": {"type": "object", "properties": {
                    "name": {"type": "string", "maxLength": 80}, "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}}, "meta": {"type": "object"}}},
                "Project": {"type": "object", "properties": {
                    "id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}}, "meta": {"type": "object"},
                    "node": {"type": "string"}, "memories": {"type": "integer"}, "nodes": {"type": "integer"},
                    "created": {"type": "string"}, "updated": {"type": "string"}}},
                "MemoryInput": {"type": "object", "properties": {
                    "content": {"type": "string", "maxLength": 4000},
                    "project": {"type": "string", "description": "Project id or name (default 'geral'; created if missing)"},
                    "type": {"type": "string", "description": "fact, preference, decision, task, note, event..."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                    "source": {"type": "string", "description": "Which AI or tool wrote it"},
                    "entities": {"type": "array", "items": {"type": "string"},
                                 "description": "Graph node labels this memory is about (created if missing)"},
                    "meta": {"type": "object"}, "expires_at": {"type": "string", "description": "ISO date-time"}}},
                "Memory": {"allOf": [ref("MemoryInput"), {"type": "object", "properties": {
                    "id": {"type": "string"}, "created": {"type": "string"}, "updated": {"type": "string"}}}]},
                "NodeInput": {"type": "object", "required": ["label"], "properties": {
                    "label": {"type": "string"}, "type": {"type": "string", "default": "tema"},
                    "project": {"type": "string"}, "description": {"type": "string"}}},
                "Node": {"type": "object", "properties": {
                    "key": {"type": "string"}, "label": {"type": "string"}, "type": {"type": "string"},
                    "weight": {"type": "integer"}, "projects": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"}, "pinned": {"type": "boolean"}}},
                "EdgeInput": {"type": "object", "required": ["from", "to"], "properties": {
                    "from": {"type": "string"}, "to": {"type": "string"}, "rel": {"type": "string"},
                    "project": {"type": "string"}}},
            },
        },
    }
