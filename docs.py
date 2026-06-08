"""
docs — perguntar sobre seus arquivos (RAG 100% local).

Coloque .txt, .md ou .pdf na pasta /docs e o agente passa a responder com base
neles. Os trechos relevantes são achados por similaridade (embeddings do
nomic-embed-text rodando no Ollama, na sua máquina). Nada vai pra rede.

O índice (docs_index.json) guarda os vetores dos trechos. É pessoal: fica só no
disco e está no .gitignore.
"""
import os
import re
import json
import math
import time
import threading

import requests
import core

DOCS_DIR = os.path.join(core.BASE_DIR, "docs")
INDEX_FILE = os.path.join(core.BASE_DIR, "docs_index.json")
EXTS = (".txt", ".md", ".markdown", ".pdf")

_lock = threading.Lock()
_indexando = threading.Event()


# ── leitura de arquivos ───────────────────────────────────────────────────────
def _ler_pdf(caminho):
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        r = PdfReader(caminho)
        return "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception:
        return ""


def extrair_texto(nome, data: bytes) -> str:
    """Extrai texto de um arquivo recebido em memória (anexo do chat)."""
    ext = os.path.splitext(nome or "")[1].lower()
    if ext == ".pdf":
        try:
            import io
            from pypdf import PdfReader
            r = PdfReader(io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in r.pages)
        except Exception:
            return ""
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _ler_arquivo(caminho):
    ext = os.path.splitext(caminho)[1].lower()
    if ext == ".pdf":
        return _ler_pdf(caminho)
    try:
        with open(caminho, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _trechos(texto, tamanho=900, sobrepor=150):
    texto = re.sub(r"[ \t]+", " ", texto or "")
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    if not texto:
        return []
    partes, i, n = [], 0, len(texto)
    while i < n:
        fim = min(i + tamanho, n)
        corte = texto.rfind(". ", i + tamanho // 2, fim)  # tenta cortar numa frase
        if corte > 0 and fim < n:
            fim = corte + 1
        bloco = texto[i:fim].strip()
        if len(bloco) > 40:
            partes.append(bloco)
        if fim >= n:
            break
        i = max(fim - sobrepor, i + 1)
    return partes


# ── embeddings (via Ollama, local) ────────────────────────────────────────────
def _modelo_embed():
    return core.carregar_config().get("embed", "nomic-embed-text")


def _garantir_embed():
    """Garante que o modelo de embedding está baixado (puxa uma vez, se faltar)."""
    nome = _modelo_embed()
    if core.modelo_instalado(nome):
        return True
    try:
        emitir = __import__("skills").emitir
    except Exception:
        emitir = lambda *a: None
    emitir("status", f"baixando {nome} (uma vez)…")
    try:
        r = requests.post(f"{core.OLLAMA}/api/pull", json={"model": nome, "stream": False}, timeout=1800)
        return r.ok and core.modelo_instalado(nome)
    except Exception:
        return False


def _embed(texto):
    try:
        r = requests.post(f"{core.OLLAMA}/api/embeddings",
                          json={"model": _modelo_embed(), "prompt": texto}, timeout=120)
        v = r.json().get("embedding")
        return v if isinstance(v, list) and v else None
    except Exception:
        return None


def _cos(a, b):
    s = na = nb = 0.0
    for x, y in zip(a, b):
        s += x * y
        na += x * x
        nb += y * y
    return s / (math.sqrt(na) * math.sqrt(nb) + 1e-9)


# ── índice ────────────────────────────────────────────────────────────────────
def _carregar_indice():
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _salvar_indice(idx):
    with _lock:
        tmp = INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        os.replace(tmp, INDEX_FILE)


def _arquivos_na_pasta():
    os.makedirs(DOCS_DIR, exist_ok=True)
    achados = []
    for raiz, _, nomes in os.walk(DOCS_DIR):
        for n in nomes:
            if n.lower().endswith(EXTS):
                achados.append(os.path.join(raiz, n))
    return achados


def indexar(forcar=False):
    """(Re)indexa a pasta /docs. Só reprocessa arquivos novos ou alterados."""
    if _indexando.is_set():
        return
    _indexando.set()
    try:
        emitir = __import__("skills").emitir
    except Exception:
        emitir = lambda *a: None
    try:
        if not core.ollama_online():
            return
        if not _garantir_embed():
            emitir("status", "modelo de embedding indisponível")
            return
        idx = {} if forcar else _carregar_indice()
        idx.setdefault("modelo", _modelo_embed())
        if idx.get("modelo") != _modelo_embed():        # trocou o embed → refaz tudo
            idx = {"modelo": _modelo_embed(), "arquivos": {}}
        idx.setdefault("arquivos", {})
        atuais = _arquivos_na_pasta()
        chaves_atuais = {os.path.relpath(c, DOCS_DIR) for c in atuais}
        # remove do índice o que sumiu da pasta
        for k in list(idx["arquivos"].keys()):
            if k not in chaves_atuais:
                idx["arquivos"].pop(k, None)
        novos = 0
        for caminho in atuais:
            rel = os.path.relpath(caminho, DOCS_DIR)
            try:
                mt = os.path.getmtime(caminho)
            except Exception:
                mt = 0
            reg = idx["arquivos"].get(rel)
            if reg and abs(reg.get("mtime", 0) - mt) < 1 and reg.get("trechos"):
                continue                                 # inalterado
            emitir("status", f"lendo {rel}…")
            texto = _ler_arquivo(caminho)
            trechos = _trechos(texto)
            vetores = []
            for tr in trechos:
                v = _embed(tr)
                if v:
                    vetores.append({"t": tr, "v": v})
            idx["arquivos"][rel] = {"mtime": mt, "trechos": vetores}
            novos += 1
            _salvar_indice(idx)
        _salvar_indice(idx)
        if novos:
            emitir("status", "documentos indexados")
    finally:
        _indexando.clear()


def status():
    idx = _carregar_indice()
    arqs = idx.get("arquivos", {})
    lista = [{"nome": k, "trechos": len(v.get("trechos", []))} for k, v in sorted(arqs.items())]
    return {
        "arquivos": lista,
        "total": sum(a["trechos"] for a in lista),
        "indexando": _indexando.is_set(),
        "pasta": DOCS_DIR,
    }


def consultar(query, k=4, limite=2200):
    """Devolve os trechos mais parecidos com a pergunta (ou '' se nada serve)."""
    if not core.habilidade("documentos"):
        return ""
    idx = _carregar_indice()
    arqs = idx.get("arquivos", {})
    if not arqs:
        return ""
    qv = _embed(query)
    if not qv:
        return ""
    candidatos = []
    for rel, reg in arqs.items():
        for tr in reg.get("trechos", []):
            candidatos.append((_cos(qv, tr["v"]), rel, tr["t"]))
    if not candidatos:
        return ""
    candidatos.sort(key=lambda x: x[0], reverse=True)
    saida, usado = [], 0
    for score, rel, txt in candidatos[:k]:
        if score < 0.35:                # corta trechos irrelevantes
            continue
        bloco = f"[{rel}] {txt}"
        if usado + len(bloco) > limite:
            bloco = bloco[: max(0, limite - usado)]
        saida.append(bloco)
        usado += len(bloco)
        if usado >= limite:
            break
    return "\n\n".join(saida)


def reindexar_async(forcar=True):
    threading.Thread(target=indexar, kwargs={"forcar": forcar}, daemon=True).start()
