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

Memories created through the API also show up in Atlas's own chat when they are
relevant, so what one AI saves, Atlas and every other AI can recall.

## Authentication and safety

- Every `/v1` call needs the token:
  `Authorization: Bearer <token>` (or `X-Atlas-Token: <token>`).
- The token is shown in **Settings → Memory API**, where you can also copy the MCP
  configuration, generate a new token or turn the API off. It is stored as
  `api_token` in `config.json`.
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
| `atlas_list_projects` / `atlas_create_project` / `atlas_update_project` / `atlas_delete_project` | Manage projects |
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
| GET | `/v1/projects` | List projects (with memory and node counts) |
| POST | `/v1/projects` | Create `{name, description?, tags?, meta?}` |
| GET / PATCH / DELETE | `/v1/projects/{id}` | Read, update, delete (`?cascade=true` also deletes its memories) |
| GET | `/v1/memories` | List (`project`, `tag`, `type`, `source`, `sort=recent\|importance`, `limit`, `offset`) or search with `q` |
| POST | `/v1/memories` | Create a memory (exact duplicates in the same project return the existing one) |
| POST | `/v1/memories/batch` | Create up to 200 at once `{items: [...]}` |
| POST | `/v1/memories/search` | `{query, project?, tags?, type?, limit?, semantic?}` |
| GET / PATCH / DELETE | `/v1/memories/{id}` | Read, update (also `add_tags`), delete |
| GET | `/v1/graph` | Whole graph, or `?project=` subgraph |
| GET | `/v1/graph/nodes` | Find nodes (`q`, `project`, `type`, `limit`) |
| POST | `/v1/graph/nodes` | Create `{label, type?, project?, description?}` (reuses a matching node) |
| GET / PATCH / DELETE | `/v1/graph/nodes/{key or label}` | Node with edges and related memories; update (`label`, `type`, `description`, `add_project`, `remove_project`); delete |
| GET | `/v1/graph/nodes/{key or label}/neighbors?depth=2` | Neighborhood subgraph (1 to 4 hops) |
| POST | `/v1/graph/edges` | `{from, to, rel?, project?}` (missing nodes are created) |
| DELETE | `/v1/graph/edges?from=..&to=..` | Remove a relation |
| POST | `/v1/context` | `{query, project?, limit?, include_graph?, include_facts?}` returns `text` ready for a prompt |
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

## Search

Search always matches words (accents and common stop words are ignored). When the
embedding model (`nomic-embed-text`, the same one used for documents) is installed
in Ollama, Atlas also computes embeddings for memories in the background and mixes
semantic similarity into the score. Results carry a `score`, and the response says
whether semantic search was used (`"semantic": true`).
