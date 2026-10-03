# Atlas - AI Local Agent

A lightweight, fully local and private AI assistant. It runs entirely on your
machine (127.0.0.1), and nothing is sent to the network.

The repository contains only the code (around 150 KB). Models, the engine
(Ollama), OCR languages and the offline Wikipedia are downloaded on first use,
so you do not clone a huge folder.

## Features

- Chat with saved conversation history (create, rename, delete)
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
- Smarter context: memories are chosen by relevance + importance + recency,
  deduplicated, fitted to a budget derived from the model's context window, and
  shown (collapsible) under each answer. Pin or exclude memories per chat or per
  project. Durable facts are proposed for confirmation and never include
  passwords, tokens or cards
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

## Privacy

Everything runs locally through Ollama on 127.0.0.1. No telemetry, no cloud,
no account. Your data (config.json, conversas.json, memoria.json, grafo.json,
observacoes.json, lembretes.json, memorias.json, prints, docs and docs_index.json) stays only
on your machine and is listed in .gitignore.

You can also turn on encryption at rest in Settings, Security. A password is
turned into a key with scrypt, and memory, conversations, graph and reminders
are stored encrypted on disk (HMAC-SHA256 keystream with encrypt-then-MAC). The
password is never written to disk; it is only kept in memory while the vault is
unlocked. If you forget the password, the data cannot be recovered.

## License

Released under the MIT License. See the LICENSE file for details.
