# Atlas Memory API

Atlas can act as a local, private **memory manager for any AI**. Claude, ChatGPT,
local LLMs, scripts, n8n flows or your own agents can create projects, store and
search memories, and read or edit the knowledge graph that Atlas already builds.

There are two ways in:

| Way | For | Entry point |
| --- | --- | --- |
| **MCP server** | Claude Desktop, Claude Code, Cursor, VS Code and any MCP client | `mcp_atlas.py` |
| **REST API** | Anything that speaks HTTP | `http://127.0.0.1:5005/v1` |

Atlas has to be running (`python server.py` or `launcher.pyw`). Everything stays
on 127.0.0.1.

## Concepts

- **Project**: a workspace that groups memories and graph nodes (for example
  `store-website`). A default project, `geral`, always exists. Creating a project
  also adds a node of type `projeto` to the graph.
- **Memory**: one durable, self-contained statement with:
  `content`, `project`, `type` (`fact`, `preference`, `decision`, `task`, `note`,
  `event` or anything you like), `tags`, `importance` (1 to 5), `source` (which AI
  wrote it), `entities` (graph nodes it is about), `meta` (free JSON) and an
  optional `expires_at`.
- **Graph**: the same graph you see in the Atlas graph view. Nodes created through
  the API are *pinned*: the automatic graph builder never prunes them. Nodes can
  belong to one or more projects.

When a memory in a project lists `entities`, each entity node is linked to the
project node (relation `envolve`), so a project's subgraph stays connected.

Memories created through the API also show up in Atlas's own chat when they are
relevant, so what one AI saves, Atlas and every other AI can recall.

### Project hierarchy and inherited memory

Projects form a tree. The general memory (`geral`) is the root; a project created
without `parent` sits right below it, and any project can have subprojects:

```
Geral (general memory)
└── Company
    ├── Website
    │   └── Checkout
    └── Mobile app
```

A project's memory **inherits** from its parents and from the general memory. When
an AI asks for context or searches inside `Checkout`, it also gets what was saved
in `Website`, `Company` and `Geral`. Memories of the project itself rank higher,
and inherited ones come with `inherited: true` and a `distance` (levels up). Save
shared knowledge at the highest level where it applies and every subproject sees
it.

The `scope` parameter controls this:

| scope | Includes | Default for |
| --- | --- | --- |
| `exact` | only the project | listing (`GET /v1/memories`) |
| `inherit` | the project, its parents and the general memory | search and `/v1/context` |
| `tree` | the project and all its subprojects | |
| `all` | everything | |

Move a project with `PATCH /v1/projects/{id}` and `{"parent": "other-id"}` (or
`null` for the top level); moves that would create a cycle are refused. Deleting a
project moves its subprojects up one level. In the graph, parent and child project
nodes are linked by `contém` edges (`hierarchy: true`). `GET /v1/projects/tree`
returns the whole tree with memory counts.

### In the interface

- 🗂️ **Memories** page: the project tree in the sidebar (➕ creates a subproject),
  search, add, edit, re-rate and delete memories (including the ones other AIs
  wrote). With a project open, "include inherited" shows what it inherits.
- 🕸️ **Graph** page, with three views:
  - **Network**: 2D force graph with glow, neighborhood highlight and flowing links;
  - **3D Orbit**: the same graph in 3D, auto-rotating (drag to rotate, wheel to zoom);
  - **Hierarchy**: radial tree, general memory at the center, then projects,
    subprojects and their memories (double-click a project to open it in the
    network view).
  All views have search, a project filter (with or without subprojects), a
  clickable type legend and a details panel with the related memories. Everything
  is drawn locally, with no external libraries.

## Authentication and safety

- Every `/v1` call needs the token:
  `Authorization: Bearer <token>` (or `X-Atlas-Token: <token>`).
- The token is shown in **Settings → Memory API**, where you can also copy the MCP
  configuration, generate a new token or turn the API off (turning it off blocks
  other AIs; Atlas's own pages keep working). It is stored as `api_token` in
  `config.json` and is kept when you restore a backup.
- Requests must use `127.0.0.1` or `localhost` as the host. This blocks websites
  that try to reach the API through DNS rebinding.
- If the vault (encryption at rest) is locked, calls return `423 vault_locked`
  until you unlock it in the interface. Memories live in `memorias.json`, which is
  encrypted together with the rest of your data when encryption is on.

## MCP setup

Add Atlas to your MCP client. Example for Claude Desktop
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "atlas": {
      "command": "python",
      "args": ["C:/path/to/atlas/mcp_atlas.py"]
    }
  }
}
```

Claude Code:

```bash
claude mcp add atlas -- python /path/to/atlas/mcp_atlas.py
```

The MCP server reads the token from `config.json` next to it. Optional environment
variables: `ATLAS_URL` (default `http://127.0.0.1:5005`), `ATLAS_TOKEN` and
`ATLAS_SOURCE` (the `source` written on memories, default `mcp`).

Tools:

| Tool | What it does |
| --- | --- |
| `atlas_get_context` | Ready-to-use context block: user facts, relevant memories and graph links |
| `atlas_remember` | Store a memory |
| `atlas_search_memories` | Keyword + semantic search |
| `atlas_list_memories` | List with filters and pagination |
| `atlas_update_memory` | Edit a memory |
| `atlas_forget` | Delete a memory |
| `atlas_project_tree` | The project hierarchy (general memory → projects → subprojects) |
| `atlas_list_projects` / `atlas_create_project` / `atlas_update_project` / `atlas_delete_project` | Manage projects (`parent` creates or moves subprojects) |
| `atlas_graph_search` | Find nodes |
| `atlas_graph_node` | A node with its links and memories, or its neighborhood |
| `atlas_graph_project` | The whole graph or one project's subgraph |
| `atlas_graph_add_node` / `atlas_graph_update_node` / `atlas_graph_delete_node` | Manage nodes |
| `atlas_graph_link` / `atlas_graph_unlink` | Manage relations |

## REST API

The full OpenAPI 3.1 spec is at `GET /v1/openapi.json` (no token needed). You can
import it into ChatGPT custom GPT actions, Postman, n8n or any OpenAPI client.
Errors always look like `{"error": {"code": "...", "message": "..."}}`.

### Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/v1` | API info and vault state |
| GET | `/v1/projects` | List projects in tree order (with `parent`, `depth`, `path`, `children` and memory/node counts) |
| GET | `/v1/projects/tree` | The whole hierarchy, nested under the general memory |
| POST | `/v1/projects` | Create `{name, description?, parent?, tags?, meta?}` |
| GET / PATCH / DELETE | `/v1/projects/{id}` | Read, update (also `parent` to move it), delete (`?cascade=true` also deletes its memories; subprojects move up) |
| GET | `/v1/memories` | List (`project`, `scope`, `tag`, `type`, `source`, `sort=recent\|importance`, `limit`, `offset`) or search with `q` |
| POST | `/v1/memories` | Create a memory (exact duplicates in the same project return the existing one) |
| POST | `/v1/memories/batch` | Create up to 200 at once `{items: [...]}` |
| POST | `/v1/memories/search` | `{query, project?, scope?, tags?, type?, limit?, semantic?}` |
| GET / PATCH / DELETE | `/v1/memories/{id}` | Read, update (also `add_tags`), delete |
| GET | `/v1/graph` | Whole graph, or `?project=` subgraph (`&scope=tree` includes subprojects) |
| GET | `/v1/graph/nodes` | Find nodes (`q`, `project`, `type`, `limit`) |
| POST | `/v1/graph/nodes` | Create `{label, type?, project?, description?}` (reuses a matching node) |
| GET / PATCH / DELETE | `/v1/graph/nodes/{key or label}` | Node with edges and related memories; update (`label`, `type`, `description`, `add_project`, `remove_project`); delete |
| GET | `/v1/graph/nodes/{key or label}/neighbors?depth=2` | Neighborhood subgraph (1 to 4 hops) |
| POST | `/v1/graph/edges` | `{from, to, rel?, project?}` (missing nodes are created) |
| DELETE | `/v1/graph/edges?from=..&to=..` | Remove a relation |
| POST | `/v1/context` | `{query, project?, scope?, limit?, include_graph?, include_facts?}` returns `text` ready for a prompt |
| GET | `/v1/export` | Projects, memories and graph as JSON (`?project=` for one) |

### Examples

```bash
TOKEN=atlas_xxx   # Settings → Memory API
API=http://127.0.0.1:5005/v1

# create a project
curl -s -X POST $API/projects -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Store website", "description": "E-commerce in React"}'

# store a memory linked to graph entities
curl -s -X POST $API/memories -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "Payments go through Stripe", "project": "store-website",
       "type": "decision", "importance": 5, "entities": ["Stripe"], "source": "claude"}'

# search
curl -s -X POST $API/memories/search -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"query": "how do payments work?"}'

# context block to paste into any prompt
curl -s -X POST $API/context -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "payments", "project": "store-website"}'
```

Python:

```python
import requests

API = "http://127.0.0.1:5005/v1"
H = {"Authorization": "Bearer atlas_xxx"}

requests.post(f"{API}/memories", headers=H, json={
    "content": "The user prefers short answers", "type": "preference", "source": "my-agent"})
ctx = requests.post(f"{API}/context", headers=H, json={"query": "answer style"}).json()
print(ctx["text"])
```

## Local UI endpoints (not part of `/v1`)

The `/v1` contract above did not change. The web interface also uses a few local
endpoints (same machine only, no token). They are listed here because the config
file and the chat gained optional fields.

| Method and path | Purpose |
|---|---|
| `POST /api/config` | Partial update. New keys are validated and clamped; the response carries `avisos` (list of strings) for anything rejected. |
| `POST /api/config/restaurar` `{"secao": "modelo\|memoria\|personalidade\|geracao\|privacidade\|geral"}` | Restores one settings section to its defaults. Never touches `api_token` or the vault. |
| `GET /api/projetos` | Light project list (`id`, `name`, `parent`, `depth`) for selectors. `[]` while the vault is locked. |
| `GET /api/chats/<id>/ctx` | Active project and pinned/excluded memories of a chat and of its project (with text). |
| `POST /api/chats/<id>/ctx` | `{"projeto"?, "acao": "fixar\|excluir\|limpar", "id": "<memory id>", "escopo": "conversa\|projeto"}`. |
| `POST /api/fatos/confirmar` `{"texto"}` | Saves a durable fact the user confirmed. Rejects sensitive text (passwords, tokens, cards, IDs), too short or too long (6 to 160 chars). |
| `POST /chat` | Response header `X-Atlas-Contexto` (URL-encoded JSON) lists the memories used: `mems[{id,t,p,f}]`, `projeto`, `orcamento`, `usado`, `ctx`. |

New optional `config.json` fields (all have defaults and limits):

- `provedor` (`"ollama"`), `embed`, `num_ctx` (1024 to 32768, capped by the model).
- `geracao`: `temperatura` (0 to 2), `top_p` (0.05 to 1), `max_tokens` (16 to 8192), `seed` (0 to 2147483647). `null` means the model default.
- `perfis_modelo`: per model `num_ctx`, `temperatura`, `top_p`, `max_tokens`, `seed`; wins over `geracao`.
- `contexto`: `ctx_pct` (10 to 70, share of the window used by retrieved context), `max_memorias` (0 to 20), `recencia_dias` (1 to 365, half-life), `hist_msgs` (0 to 20), `incluir_conversas`, `fatos_modo` (`perguntar`, `automatico`, `desligado`).
- `instrucoes`: `preset`, `extra` (up to 2000 chars), `personalizados` (up to 12 editable presets); `instrucoes_projeto` (`{project_id: text}`).
- `ctx_projeto`: `{project_id: {"fixas": [memory ids], "excluidas": [memory ids]}}`.

Optional chat fields in `conversas.json`: `projeto`, `ctx_fixas`, `ctx_excluidas` per chat and `ctx` per message (memories used).

Context ranking: `score = 0.60 * relevance + 0.25 * importance/5 + 0.15 * recency`, where
recency halves every `recencia_dias`. Pinned memories always come first; excluded ones
never enter. A decision made for the chat wins over one made for the project.

## Tests

```bash
python -m unittest discover -s tests
```

The tests run in a temporary folder (your data is never touched) and do not need
Ollama.

## Search

Search always matches words (accents and common stop words are ignored). When the
embedding model (`nomic-embed-text`, the same one used for documents) is installed
in Ollama, Atlas also computes embeddings for memories in the background and mixes
semantic similarity into the score. Results carry a `score`, and the response says
whether semantic search was used (`"semantic": true`).
