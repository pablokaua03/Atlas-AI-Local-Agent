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
- Light and dark theme, and three interface languages (PT, EN, ES)
- Every skill can be toggled on or off at any time
- Master switch to pause all background activity (no screenshots, no learning)
- Model picker: install, switch and delete models from the interface
- Built in system monitor (VRAM, CPU, RAM)

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

## Privacy

Everything runs locally through Ollama on 127.0.0.1. No telemetry, no cloud,
no account. Your data (config.json, conversas.json, memoria.json, grafo.json,
prints) stays only on your machine and is listed in .gitignore.
