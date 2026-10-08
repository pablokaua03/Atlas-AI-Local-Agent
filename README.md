# Atlas - AI Local Agent

A lightweight, fully local and private AI assistant. It runs entirely on your
machine (127.0.0.1), and nothing is sent to the network.

The repository contains only the code (around 150 KB). Models, the engine
(Ollama), OCR languages and the offline Wikipedia are downloaded on first use,
so you do not clone a huge folder.

## Features

- Chat with saved conversation history (create, rename, delete), a search box
  over titles and messages, and chats grouped by date
- Safe Markdown answers (headings, lists, tables, links, code blocks with a
  copy button; raw HTML is always escaped), smooth streaming that stops
  auto-scrolling when you scroll up, and a Stop button that keeps the partial
  answer
- Clear Ollama status with one-click start (progress and error messages), and a
  per-chat model picker
- Local memory of facts, plus access to all past conversations
- Screen awareness via OCR, an editable knowledge graph, proactive messages
  and offline Wikipedia
- Ask about your own files: drop .txt, .md or .pdf in a folder and the agent
  answers from them (local RAG with nomic-embed-text)
- One click backup: export and restore your memory, graph and conversations
- Optional encryption at rest: protect memory, conversations, graph and
  reminders with a password (standard library only, no native crypto)
- Natural language reminders ("remind me of this tomorrow") that fire through
  the proactive channel
- System tray icon and a global hotkey (Ctrl+Alt+A) to open the agent fast
- Light and dark theme, and three interface languages (PT, EN, ES)
- Every skill can be toggled on or off at any time
- Master switch to pause all background activity (no screenshots, no learning)
- Model catalog (Ollama, local): general, code, reasoning, vision, embeddings and
  light models for low-end PCs, with download size, approximate RAM, context
  window and installed status. Install with progress and clear errors, switch,
  delete, and set a per-model profile (context window, temperature...)
- Installed models list (including models pulled outside the catalog), pull any
  model by name, and a fit badge for your GPU: fits in VRAM, will also use RAM
  (slower) or too big
- Smarter context: memories are chosen by relevance + importance + recency,
  deduplicated, fitted to a budget derived from the model's context window, and
  shown (collapsible) under each answer with why each one was used. Pin or
  exclude memories per chat or per project, or pin a memory itself so it always
  goes into its project's chats. Weak matches are dropped instead of filling the
  budget. Durable facts are proposed for confirmation and never include
  passwords, tokens or cards
- Memories page: search with highlighting, pin/unpin and a pinned filter, edit
  content, type, project, importance and tags
- Graph page: memories as nodes, node size by number of links, search with a
  results list that focuses the node, touch and pinch zoom, zoom buttons and
  description editing. Fast with hundreds of nodes, no external libraries
- Settings in sections (Model, Memory and context, Personality and instructions
  with editable presets and per-project text, Generation: temperature, top_p,
  max tokens, seed, Privacy and security, Language and theme, Backup), with
  validated values, short descriptions in PT/EN/ES and restore defaults per section
- Built in system monitor (VRAM, CPU, RAM)
- Memory API for any AI: Claude, ChatGPT, local LLMs and scripts can create
  projects, store and search memories and edit the knowledge graph, through an
  MCP server or a token protected REST API (see [API.md](API.md))

## Requirements

- Python 3.10 or newer (tested on 3.14)
- Ollama, the model engine (installable from inside the interface)
- Tesseract OCR, optional, only needed for the "see the screen" feature:
  https://github.com/UB-Mannheim/tesseract/wiki

## Install

```bash
git clone https://github.com/pablokaua03/Atlas-AI-Local-Agent.git atlas
cd atlas
pip install -r requirements.txt
```

## Run

- Windows: double click `launcher.pyw` (or create a shortcut). It has Start,
  Stop and Open buttons and manages Ollama for you.
- Any OS: run `python server.py` and open http://127.0.0.1:5005
- Another port: set `ATLAS_PORT` (1024 to 65535), e.g. `ATLAS_PORT=5077 python server.py`.
  The MCP server then needs `ATLAS_URL=http://127.0.0.1:5077`.

## First time

1. Open http://127.0.0.1:5005
2. In the banner, click "Install Ollama" (downloads and installs in the
   background).
3. In Settings, Model section, click Download on the model you want.
4. Turn on the skills you want and start chatting.

## Optional

- Offline Wikipedia: in Settings, Wikipedia, click the download button. It
  fetches a compact .zim for your language from Kiwix and builds the index.
- OCR languages are downloaded automatically based on the interface language.

## Memory API (use Atlas as memory for other AIs)

Atlas exposes its memory and knowledge graph at `http://127.0.0.1:5005/v1`, and
ships an MCP server (`mcp_atlas.py`) for Claude Desktop, Claude Code, Cursor and
other MCP clients. Open Settings, Memory API to copy the token and the MCP
configuration. The 🗂️ button opens the Memories page, where you can review and
edit everything the AIs saved. Full guide: [API.md](API.md).

## Models, context and settings

The catalog lives in `core.py` (`CATALOGO_MODELOS`); sizes and RAM are rough
estimates. Providers are structured in `core.PROVEDORES` (only local Ollama is
implemented; no API key is ever stored or invented). Context selection is in
`contexto.py`, validated settings and section defaults in `ajustes.py`. All new
config fields are optional: an old `config.json` keeps working and is filled
with defaults on load. Details of the local UI endpoints: [API.md](API.md).

## Models for your machine

Atlas detects your hardware (NVIDIA through `nvidia-smi`, other GPUs through the
Windows registry/WMI or `/sys/class/drm` on Linux, Apple Silicon unified memory
through `sysctl`, CPU-only machines, system RAM and CPU cores) and gives every
catalog model a fit badge for this machine: fits in VRAM, partial offload (also
uses RAM, slower) or too big. Settings, Model opens on **Recommended for your
machine** (fast, balanced, smartest, vision, code and embedding picks); the
filter bar shows all models or one tier: light (1 to 4B), moderate (7 to 9B),
heavy (12 to 14B), large (20 to 35B, MoE) and huge (70B+). Each model lists its
quantization variants with their own fit badge, so you can pull any of them. If
detection is wrong (or you want to plan for another machine), use "Adjust
hardware" to set VRAM, RAM, GPU name and unified memory by hand.

Models marked "measured" were benchmarked on a 6 GB VRAM laptop GPU with
`scripts/bench_modelos.py` (speed, VRAM use, PT-BR answer, tool calling); the
others are marked "researched" (numbers from the model cards, not measured).
Run the benchmark on your own machine with Ollama running:

```bash
python scripts/bench_modelos.py qwen3.5:4b nomic-embed-text   # writes scripts/bench_resultados.json
```

## Tools: the model can use Atlas

With a model that supports tool calling (the catalog and Ollama's `/api/show`
say so; you can force it per model in the model profile), the chat model can call
Atlas itself: search, save, update and pin memories, search the knowledge graph,
add concepts and link them, list projects, recall past conversations and, with
the Documents skill on, list and read files inside the `docs/` folder only.
Calls show up live in a collapsible "Tools used" block under the answer. Writes
go through the same code as the Memories page and the `/v1` API, are tagged
`atlas-chat:<model>`, and are logged in `acoes_ia.json` (encrypted with the vault
when it is on), where each one can be undone from the block or from Settings,
Memory, Tools. Settings, Memory, Tools lets you turn tools off, allow read-only
tools, and set the maximum tool rounds per answer. Models without tool support
keep working as before (memories are injected into the prompt).

Memory questions are checked, not guessed:

- For questions about you, your projects or your past, Atlas searches before the
  model answers: memories in every project (from Geral it sees all of them),
  project names and descriptions, graph concepts and, if enabled, documents. Your
  exact words are always part of the query, so acronyms like `ACME` match even
  if the model rewrites them, and common Portuguese words also match their English
  translation (`primeiro`/`first`, `escopo`/`scope`...).
- If the model says it will look something up (or that it has no record) without
  calling a tool, Atlas runs the search itself and the model continues the answer.
- When you push back ("certeza?", "procura de novo"), the search widens: the
  previous question, more results, every project and past conversations.
- When nothing matches, the model is told to say what it checked.
- Tool results are shrunk to fit the model's context window, so answers no longer
  stop mid-sentence on 4k-token windows.

Settings, Memory, Tools also has "Search memory before answering" (on by default)
and "Reasoning with tools" for thinking models like Qwen3.5: off (fastest,
default), only when answering from a search (a bit more careful, about 2x slower),
or always.

## Privacy

Everything runs locally through Ollama on 127.0.0.1. No telemetry, no cloud,
no account. Your data (config.json, conversas.json, memoria.json, grafo.json,
observacoes.json, lembretes.json, memorias.json, acoes_ia.json, prints, docs and docs_index.json) stays only
on your machine and is listed in .gitignore.

You can also turn on encryption at rest in Settings, Security. A password is
turned into a key with scrypt, and memory, conversations, graph and reminders
are stored encrypted on disk (HMAC-SHA256 keystream with encrypt-then-MAC). The
password is never written to disk; it is only kept in memory while the vault is
unlocked. If you forget the password, the data cannot be recovered.

## License

Released under the MIT License. See the LICENSE file for details.
