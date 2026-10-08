"""
core — núcleo do agente local configurável.
Tudo roda 100% na máquina (Ollama em 127.0.0.1). Nada sai pra rede.

Responsabilidades:
- carregar/salvar a configuração (modelo escolhido + habilidades ligadas/desligadas)
- falar com o Ollama (status, lista de modelos, baixar modelo, chat)
"""
import os
import re
import time
import json
import shutil
import platform
import threading
import subprocess
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
MEM_FILE = os.path.join(BASE_DIR, "memoria.json")
CONVERSAS_FILE = os.path.join(BASE_DIR, "conversas.json")
GRAFO_FILE = os.path.join(BASE_DIR, "grafo.json")
OBS_FILE = os.path.join(BASE_DIR, "observacoes.json")
WIKI_DIR = os.path.join(BASE_DIR, "wiki")          # coloque um .zim aqui pra ativar a Wikipédia
PRINTS_DIR = os.path.join(BASE_DIR, "prints")      # screenshots salvos da observação de tela
OLLAMA = "http://127.0.0.1:11434"

# ── CATÁLOGO DE MODELOS ───────────────────────────────────────────────────────
# Cada item: nome (tag padrão do Ollama), rotulo, usos (geral|codigo|raciocinio|visao|leve|embed),
# gb (download aprox. da tag padrão), ram (RAM aprox. em GB para rodar só na CPU), vram (texto legado),
# ctx (janela padrão sugerida), ctx_max (máximo do modelo), temp (temperatura padrão), tipo (chat|embed),
# visao (aceita imagens) e think=False (desliga o "pensar" explícito, ex. Qwen3).
# Campos de recomendação (out/2026):
#   nivel       leve (≤4B) | moderado (7–9B) | pesado (12–14B) | grande (20–35B / MoE) | enorme (70B+)
#   params      bilhões de parâmetros (total); ativos = parâmetros ativos por token em modelos MoE
#   ferramentas suporta chamada de ferramentas (tool calling) no Ollama
#   q           qualidade relativa pesquisada (1–10) usada para desempatar recomendações
#   fortes      pontos fortes (pt/en/es)
#   variantes   outras quantizações publicadas no Ollama [{tag, quant, gb}]
#   medido      números medidos com scripts/bench_modelos.py numa RTX 4050 Laptop 6 GB (Ryzen 7 7735HS,
#               16 GB RAM, Ollama 0.30.6, num_ctx 4096); ferramentas = acertos nos 4 casos do teste
#               (None = não deu para medir: faltou RAM para o offload parcial);
#               sem "medido" = só pesquisado (tamanhos da biblioteca do Ollama, benchmarks públicos).
# Tamanhos e RAM são aproximações para ajudar na escolha, não garantias.
def _m(nome, rotulo, usos, gb, ram, ctx_max, temp=0.7, **extra):
    d = {"nome": nome, "rotulo": rotulo, "usos": usos, "gb": gb, "ram": ram,
         "vram": f"~{gb:.1f} GB", "ctx": min(4096, ctx_max), "ctx_max": ctx_max, "temp": temp,
         "tipo": "chat", "provedor": "ollama", "ferramentas": False}
    d.update(extra)
    return d


def _v(tag, quant, gb):
    return {"tag": tag, "quant": quant, "gb": gb}


def _f(pt, en, es):
    return {"pt": pt, "en": en, "es": es}


CATALOGO_MODELOS = [
    # ── leve (≤4B): rápido, cabe inteiro em quase qualquer GPU; roda até só na CPU ──
    _m("qwen3.5:0.8b", "Qwen3.5 0.8B", ["leve", "geral"], 1.3, 2, 262144, 0.6, nivel="leve", params=0.8,
       ferramentas=True, visao=True, think=False, q=3,
       fortes=_f("Minúsculo e muito rápido; bom para PCs fracos e respostas curtas.",
                 "Tiny and very fast; good for weak PCs and short answers.",
                 "Diminuto y muy rápido; bueno para PCs modestos y respuestas cortas."),
       variantes=[_v("qwen3.5:0.8b-q8_0", "Q8_0", 1.0)],
       medido={"tok_s": 79.8, "gpu_pct": 100, "vram_gb": 0.4, "ferramentas": "3/4", "quant": "Q8_0"}),
    _m("qwen3.5:2b", "Qwen3.5 2B", ["leve", "geral"], 2.7, 4, 262144, 0.6, nivel="leve", params=2,
       ferramentas=True, visao=True, think=False, q=5,
       fortes=_f("Leve com ferramentas e visão; PT-BR razoável.", "Light with tools and vision; decent Portuguese.",
                 "Ligero con herramientas y visión; portugués/español razonable."),
       variantes=[_v("qwen3.5:2b-q4_K_M", "Q4_K_M", 1.9), _v("qwen3.5:2b-q8_0", "Q8_0", 2.7)]),
    _m("qwen3.5:4b", "Qwen3.5 4B", ["geral", "leve", "raciocinio"], 3.3, 5, 262144, 0.6, nivel="leve", params=4,
       ferramentas=True, visao=True, think=False, q=7,
       fortes=_f("Melhor custo-benefício até 6 GB: ferramentas confiáveis, visão, bom PT-BR.",
                 "Best value up to 6 GB: reliable tool calls, vision, good Portuguese.",
                 "La mejor relación hasta 6 GB: herramientas fiables, visión, buen español."),
       variantes=[_v("qwen3.5:4b-q4_K_M", "Q4_K_M", 3.3), _v("qwen3.5:4b-q8_0", "Q8_0", 5.2)],
       medido={"tok_s": 49.4, "gpu_pct": 100, "vram_gb": 2.8, "ferramentas": "4/4", "quant": "Q4_K_M"}),
    _m("granite4.1:3b", "Granite 4.1 3B", ["leve", "geral"], 2.1, 4, 131072, 0.5, nivel="leve", params=3,
       ferramentas=True, q=5,
       fortes=_f("IBM, Apache 2.0; JSON estruturado e ferramentas estáveis; contexto barato.",
                 "IBM, Apache 2.0; structured JSON and steady tool use; cheap context.",
                 "IBM, Apache 2.0; JSON estructurado y herramientas estables."),
       variantes=[_v("granite4.1:3b-q8_0", "Q8_0", 3.6)]),
    _m("phi4-mini", "Phi-4 mini", ["raciocinio", "geral", "leve"], 2.5, 5, 131072, nivel="leve", params=3.8,
       ferramentas=True, q=5,
       fortes=_f("Raciocínio e matemática fortes para o tamanho; PT-BR mais fraco.",
                 "Strong reasoning and math for its size; weaker Portuguese.",
                 "Razonamiento y matemáticas fuertes para su tamaño; español más flojo.")),
    _m("llama3.2:3b", "Llama 3.2 3B", ["geral", "leve"], 2.0, 4, 131072, nivel="leve", params=3,
       ferramentas=True, q=4,
       fortes=_f("Rápido e muito testado; ferramentas básicas.", "Fast and battle-tested; basic tool use.",
                 "Rápido y muy probado; herramientas básicas.")),
    _m("gemma4:e2b-it-qat", "Gemma 4 E2B (QAT)", ["leve", "geral", "visao"], 4.3, 6, 131072, nivel="leve", params=2,
       ferramentas=True, visao=True, q=5,
       fortes=_f("Gemma mais nova que cabe em 6 GB; visão e áudio; multilíngue.",
                 "Newest Gemma that fits 6 GB; vision and audio; multilingual.",
                 "La Gemma más nueva que cabe en 6 GB; visión y audio; multilingüe."),
       variantes=[_v("gemma4:e2b", "Q4_K_M", 4.6)]),
    _m("qwen2.5:0.5b", "Qwen2.5 0.5B", ["leve", "geral"], 0.4, 2, 32768, nivel="leve", params=0.5, ferramentas=True, q=1),
    _m("qwen2.5:1.5b", "Qwen2.5 1.5B", ["geral", "leve"], 1.0, 3, 32768, nivel="leve", params=1.5, ferramentas=True, q=2),
    _m("llama3.2:1b", "Llama 3.2 1B", ["leve", "geral"], 1.3, 3, 131072, nivel="leve", params=1, ferramentas=True, q=2),
    _m("gemma3:1b", "Gemma 3 1B", ["leve", "geral"], 0.8, 3, 32768, nivel="leve", params=1, q=2),
    _m("qwen3:1.7b", "Qwen3 1.7B", ["leve", "raciocinio"], 1.4, 4, 40960, 0.6, think=False, nivel="leve", params=1.7,
       ferramentas=True, q=3),
    _m("qwen2.5:3b", "Qwen2.5 3B", ["geral"], 1.9, 4, 32768, nivel="leve", params=3, ferramentas=True, q=3),
    _m("qwen3:4b", "Qwen3 4B", ["geral", "raciocinio"], 2.5, 5, 40960, 0.6, think=False, nivel="leve", params=4,
       ferramentas=True, q=6),
    _m("gemma3:4b", "Gemma 3 4B", ["geral", "visao"], 3.3, 6, 131072, visao=True, nivel="leve", params=4, q=4),
    _m("deepseek-r1:1.5b", "DeepSeek-R1 1.5B", ["raciocinio", "leve"], 1.1, 3, 131072, 0.6, nivel="leve", params=1.5, q=2),
    _m("qwen2.5-coder:1.5b", "Qwen2.5 Coder 1.5B", ["codigo", "leve"], 1.0, 3, 32768, 0.2, nivel="leve", params=1.5,
       ferramentas=True, q=3),
    _m("qwen2.5-coder:3b", "Qwen2.5 Coder 3B", ["codigo", "leve"], 1.9, 4, 32768, 0.2, nivel="leve", params=3,
       ferramentas=True, q=4),
    _m("moondream", "Moondream (visão)", ["visao", "leve"], 1.7, 4, 2048, visao=True, ctx=2048, nivel="leve", params=1.9, q=2),
    # ── moderado (7–9B): ~5–7 GB; inteiro numa GPU de 8 GB, dividido numa de 6 GB ──
    _m("qwen3.5:9b", "Qwen3.5 9B", ["geral", "raciocinio", "visao"], 6.6, 10, 262144, 0.6, nivel="moderado", params=9,
       ferramentas=True, visao=True, think=False, q=8,
       fortes=_f("O mais confiável em ferramentas na faixa de 8 GB; ótimo PT-BR; visão.",
                 "Most reliable tool caller in the 8 GB class; great Portuguese; vision.",
                 "El más fiable con herramientas en 8 GB; muy buen español; visión."),
       variantes=[_v("qwen3.5:9b-q4_K_M", "Q4_K_M", 6.6), _v("qwen3.5:9b-q8_0", "Q8_0", 10.0)],
       medido={"tok_s": 6.0, "gpu_pct": 51, "vram_gb": 3.0, "ferramentas": None, "quant": "Q4_K_M"}),
    _m("ministral-3:8b", "Ministral 3 8B", ["geral", "visao"], 6.0, 9, 262144, nivel="moderado", params=8,
       ferramentas=True, visao=True, q=7,
       fortes=_f("Mistral: ferramentas nativas, prompt de sistema firme, bom em PT/ES.",
                 "Mistral: native tool calling, strong system-prompt adherence, good PT/ES.",
                 "Mistral: herramientas nativas, buen apego al prompt de sistema, buen ES/PT.")),
    _m("granite4.1:8b", "Granite 4.1 8B", ["geral"], 5.3, 9, 131072, 0.5, nivel="moderado", params=8,
       ferramentas=True, q=6,
       fortes=_f("Ferramentas e JSON estáveis; a variante Q3 cabe em 6 GB.",
                 "Steady tools and JSON; the Q3 variant fits 6 GB.",
                 "Herramientas y JSON estables; la variante Q3 cabe en 6 GB."),
       variantes=[_v("granite4.1:8b-q3_K_M", "Q3_K_M", 4.3), _v("granite4.1:8b-q4_K_M", "Q4_K_M", 5.3),
                  _v("granite4.1:8b-q8_0", "Q8_0", 9.3)]),
    _m("gemma4:e4b-it-qat", "Gemma 4 E4B (QAT)", ["geral", "visao"], 6.1, 9, 131072, nivel="moderado", params=4,
       ferramentas=True, visao=True, q=6,
       fortes=_f("Gemma 4 com visão e áudio; função nativa; bom multilíngue.",
                 "Gemma 4 with vision and audio; native function calling; multilingual.",
                 "Gemma 4 con visión y audio; funciones nativas; multilingüe."),
       variantes=[_v("gemma4:e4b", "Q4_K_M", 6.6)]),
    _m("qwen3:8b", "Qwen3 8B", ["geral", "raciocinio"], 5.2, 9, 40960, 0.6, think=False, nivel="moderado", params=8,
       ferramentas=True, q=6),
    _m("llama3.1:8b", "Llama 3.1 8B", ["geral"], 4.9, 8, 131072, nivel="moderado", params=8, ferramentas=True, q=5),
    _m("mistral:7b", "Mistral 7B", ["geral"], 4.4, 8, 32768, nivel="moderado", params=7, ferramentas=True, q=3),
    _m("deepseek-r1:7b", "DeepSeek-R1 7B", ["raciocinio"], 4.7, 8, 131072, 0.6, nivel="moderado", params=7, q=4),
    _m("deepseek-r1:8b", "DeepSeek-R1 8B", ["raciocinio"], 5.2, 9, 131072, 0.6, nivel="moderado", params=8,
       ferramentas=True, q=5),
    _m("qwen2.5-coder:7b", "Qwen2.5 Coder 7B", ["codigo"], 4.7, 8, 32768, 0.2, nivel="moderado", params=7,
       ferramentas=True, q=5),
    _m("llava:7b", "LLaVA 7B (visão)", ["visao"], 4.7, 8, 32768, visao=True, nivel="moderado", params=7, q=2),
    # ── pesado (12–14B): ~8–10 GB; GPU de 12 GB, ou dividido com a RAM (mais lento) ──
    _m("ministral-3:14b", "Ministral 3 14B", ["geral", "visao", "raciocinio"], 9.1, 14, 262144, nivel="pesado", params=14,
       ferramentas=True, visao=True, q=8,
       fortes=_f("Mistral 14B: agente forte, ferramentas nativas, visão, bom PT.",
                 "Mistral 14B: strong agent, native tools, vision, good Portuguese.",
                 "Mistral 14B: agente fuerte, herramientas nativas, visión.")),
    _m("gemma4:12b-it-qat", "Gemma 4 12B (QAT)", ["geral", "visao", "raciocinio"], 7.2, 12, 262144, nivel="pesado", params=12,
       ferramentas=True, visao=True, q=8,
       fortes=_f("Gemma 4 12B: escrita e PT-BR muito bons, visão, função nativa.",
                 "Gemma 4 12B: very good writing and Portuguese, vision, native functions.",
                 "Gemma 4 12B: muy buena redacción, visión, funciones nativas."),
       variantes=[_v("gemma4:12b", "Q4_K_M", 8.0), _v("gemma4:12b-it-q8_0", "Q8_0", 13.0)]),
    _m("qwen3:14b", "Qwen3 14B", ["geral", "raciocinio"], 9.3, 14, 40960, 0.6, think=False, nivel="pesado", params=14,
       ferramentas=True, q=7),
    _m("phi4", "Phi-4 14B", ["raciocinio", "geral"], 9.1, 14, 16384, nivel="pesado", params=14, q=6,
       fortes=_f("Raciocínio/matemática fortes; sem ferramentas no Ollama.", "Strong reasoning/math; no tool calling in Ollama.",
                 "Razonamiento fuerte; sin herramientas en Ollama.")),
    _m("deepseek-r1:14b", "DeepSeek-R1 14B", ["raciocinio"], 9.0, 14, 131072, 0.6, nivel="pesado", params=14, q=6),
    _m("gemma3:12b", "Gemma 3 12B", ["geral", "visao"], 8.1, 12, 131072, visao=True, nivel="pesado", params=12, q=5),
    _m("llama3.2-vision:11b", "Llama 3.2 Vision 11B", ["visao"], 7.8, 12, 131072, visao=True, nivel="pesado", params=11, q=4),
    # ── grande (20–35B e MoE): GPU de 16–24 GB, Mac com 32 GB+, ou MoE dividido com bastante RAM ──
    _m("gpt-oss:20b", "gpt-oss 20B (MoE)", ["raciocinio", "geral", "codigo"], 14, 16, 131072, nivel="grande", params=21,
       ativos=3.6, ferramentas=True, q=8,
       fortes=_f("OpenAI open-weight MoE: raciocínio ajustável e ferramentas; roda com 16 GB.",
                 "OpenAI open-weight MoE: adjustable reasoning and tools; runs in 16 GB.",
                 "MoE open-weight de OpenAI: razonamiento ajustable y herramientas.")),
    _m("qwen3.5:35b-a3b", "Qwen3.5 35B-A3B (MoE)", ["geral", "raciocinio", "visao", "codigo"], 22, 26, 262144, 0.6,
       nivel="grande", params=35, ativos=3, ferramentas=True, visao=True, think=False, q=9,
       fortes=_f("MoE com 3B ativos: qualidade de modelo grande com velocidade de pequeno se couber na RAM.",
                 "MoE with 3B active: big-model quality at small-model speed if it fits in RAM.",
                 "MoE con 3B activos: calidad de modelo grande a velocidad de pequeño.")),
    _m("gemma4:26b-a4b-it-qat", "Gemma 4 26B-A4B (MoE)", ["geral", "visao", "raciocinio"], 16, 20, 262144,
       nivel="grande", params=26, ativos=4, ferramentas=True, visao=True, q=9,
       fortes=_f("MoE do Gemma 4: muito bom em PT-BR e visão, rápido para o tamanho.",
                 "Gemma 4 MoE: very good Portuguese and vision, fast for its size.",
                 "MoE de Gemma 4: muy bueno en español y visión.")),
    _m("nemotron-3.5-lightning:30b", "Nemotron 3.5 Lightning 30B (MoE)", ["geral", "raciocinio"], 25, 30, 1048576,
       nivel="grande", params=30, ativos=3, ferramentas=True, think=False, q=8,
       fortes=_f("NVIDIA, feito para agentes sempre ligados; 3B ativos; contexto de 1M.",
                 "NVIDIA, built for always-on agents; 3B active; 1M context.",
                 "NVIDIA, hecho para agentes; 3B activos; contexto de 1M.")),
    _m("qwen3.5:27b", "Qwen3.5 27B", ["geral", "raciocinio", "visao", "codigo"], 17, 22, 262144, 0.6, nivel="grande",
       params=27, ferramentas=True, visao=True, think=False, q=9,
       fortes=_f("Denso 27B: excelente em ferramentas e código; pede GPU de 24 GB.",
                 "Dense 27B: excellent tools and code; wants a 24 GB GPU.",
                 "Denso 27B: excelente en herramientas y código; pide GPU de 24 GB.")),
    _m("qwen3.6:27b", "Qwen3.6 27B", ["codigo", "geral", "raciocinio", "visao"], 18, 23, 262144, 0.6, nivel="grande",
       params=27, ferramentas=True, visao=True, think=False, q=9,
       fortes=_f("Qwen mais novo: agente de código e 'pensamento preservado'.",
                 "Newest Qwen: agentic coding and thinking preservation.",
                 "El Qwen más nuevo: agente de código.")),
    _m("gemma4:31b-it-qat", "Gemma 4 31B (QAT)", ["geral", "visao", "raciocinio"], 19, 24, 262144, nivel="grande",
       params=31, ferramentas=True, visao=True, q=9),
    _m("granite4.1:30b", "Granite 4.1 30B", ["geral"], 17, 22, 131072, 0.5, nivel="grande", params=30,
       ferramentas=True, q=7),
    _m("qwen3:32b", "Qwen3 32B", ["geral", "raciocinio"], 20, 26, 40960, 0.6, think=False, nivel="grande", params=32,
       ferramentas=True, q=7),
    _m("deepseek-r1:32b", "DeepSeek-R1 32B", ["raciocinio"], 20, 26, 131072, 0.6, nivel="grande", params=32, q=7),
    # ── enorme (70B+): estações com 48 GB+ de VRAM ou Mac com 64–128 GB ──
    _m("llama3.3:70b", "Llama 3.3 70B", ["geral"], 43, 48, 131072, nivel="enorme", params=70, ferramentas=True, q=8),
    _m("gpt-oss:120b", "gpt-oss 120B (MoE)", ["raciocinio", "geral", "codigo"], 65, 70, 131072, nivel="enorme",
       params=117, ativos=5.1, ferramentas=True, q=9),
    _m("qwen3.5:122b-a10b", "Qwen3.5 122B-A10B (MoE)", ["geral", "raciocinio", "visao", "codigo"], 81, 90, 262144, 0.6,
       nivel="enorme", params=122, ativos=10, ferramentas=True, visao=True, think=False, q=10),
    # ── embeddings (busca por significado nas memórias e documentos) ──
    _m("nomic-embed-text", "Nomic Embed Text", ["embed"], 0.27, 1, 8192, 0.0, tipo="embed", ctx=2048,
       fortes=_f("Padrão do Atlas: leve e rápido.", "Atlas default: light and fast.", "Predeterminado de Atlas: ligero y rápido."),
       medido={"ms_por_texto": 142, "dims": 768}),
    _m("qwen3-embedding:0.6b", "Qwen3 Embedding 0.6B", ["embed"], 0.64, 2, 32768, 0.0, tipo="embed", ctx=2048,
       fortes=_f("Multilíngue (ótimo em PT-BR); um pouco maior.", "Multilingual (great in Portuguese); a bit larger.",
                 "Multilingüe (muy bueno en español).")),
    _m("embeddinggemma", "EmbeddingGemma 300M", ["embed"], 0.62, 2, 2048, 0.0, tipo="embed", ctx=2048),
    _m("mxbai-embed-large", "mxbai Embed Large", ["embed"], 0.67, 2, 512, 0.0, tipo="embed", ctx=512),
    _m("bge-m3", "BGE-M3", ["embed"], 1.2, 3, 8192, 0.0, tipo="embed", ctx=2048,
       fortes=_f("Multilíngue e robusto para textos longos.", "Multilingual and robust on long texts.",
                 "Multilingüe y robusto en textos largos.")),
]

NIVEIS = ("leve", "moderado", "pesado", "grande", "enorme")

# Provedores de modelo. Hoje só o Ollama local é suportado. Para adicionar outro (nuvem, LM Studio...):
# 1) registre aqui, 2) marque os itens do catálogo com "provedor", 3) implemente o cliente de chat
# no server.py. Chaves de API NUNCA vão no catálogo: ficam no cofre/config do usuário.
PROVEDORES = {"ollama": {"nome": "Ollama (local)", "local": True, "url": "http://127.0.0.1:11434"}}


def modelos_chat() -> list:
    return [m for m in CATALOGO_MODELOS if m.get("tipo", "chat") == "chat"]


def modelos_embed() -> list:
    return [m for m in CATALOGO_MODELOS if m.get("tipo") == "embed"]


def info_modelo(nome: str) -> dict:
    return next((m for m in CATALOGO_MODELOS if m["nome"] == nome), {})


_MODELO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,79}(:[A-Za-z0-9_.\-]{1,40})?$")


def nome_modelo_valido(nome) -> bool:
    """Tag aceitável do Ollama (ex.: 'qwen3:8b', 'hf.co/user/repo:Q4_K_M')."""
    return isinstance(nome, str) and bool(_MODELO_RE.match(nome)) and ".." not in nome


def mesmo_modelo(a: str, b: str) -> bool:
    """'llama3' e 'llama3:latest' são o mesmo modelo no Ollama."""
    sem = lambda n: n[:-7] if isinstance(n, str) and n.endswith(":latest") else n
    return bool(a) and sem(a) == sem(b)


def eh_embed(nome: str) -> bool:
    m = info_modelo(nome) or info_modelo(nome[:-7] if nome.endswith(":latest") else nome)
    if m:
        return m.get("tipo") == "embed"
    n = nome.lower()
    return "embed" in n or n.startswith(("bge-", "all-minilm", "snowflake-arctic-embed", "granite-embedding"))


CONFIG_PADRAO = {
    "nome": "você",
    "idioma": "pt",                        # "pt" | "en" | "es"
    "tema": "escuro",                      # "escuro" | "claro"
    "ativo": True,                         # interruptor mestre — desligado pausa tudo (prints, etc.)
    "provedor": "ollama",                  # só "ollama" por enquanto (ver PROVEDORES)
    "modelo": "qwen2.5:1.5b",              # modelo ativo (um do catálogo, tipo chat)
    "embed": "nomic-embed-text",
    "num_ctx": 4096,                       # janela de contexto padrão (tokens)
    "obs_intervalo": 60,                   # segundos entre prints/leituras de tela
    "iniciativa_modo": "dinamico",         # "dinamico" (a IA decide) | "intervalo" (a cada X min)
    "iniciativa_intervalo": 10,            # minutos, quando modo = intervalo
    "iniciar_com_windows": False,          # subir o agente junto com o Windows
    "api_ativa": True,                     # API de memória /v1 (outras IAs, com token)
    "habilidades": {
        "memoria":    True,    # lembra de fatos e do histórico (local)
        "tela":       False,   # observa a tela por OCR
        "grafo":      False,   # monta um grafo de conhecimento do que vê/conversa
        "iniciativa": False,   # fala sozinho quando faz sentido
        "wikipedia":  False,   # consulta a Wikipédia offline (precisa de um .zim em /wiki)
        "documentos": False,   # responde com base nos seus arquivos em /docs (RAG local)
    },
    # ── geração: None = usar o perfil/padrão do modelo ──
    "geracao": {"temperatura": None, "top_p": None, "max_tokens": None, "seed": None},
    "perfis_modelo": {},                   # {"qwen3:8b": {"num_ctx": 8192, "temperatura": 0.5}}
    # ── memória e contexto ──
    "contexto": {
        "ctx_pct": 35,                     # % da janela reservada ao contexto recuperado
        "max_memorias": 6,                 # máximo de memórias injetadas por resposta
        "recencia_dias": 30,               # meia-vida da recência no ranking
        "incluir_conversas": True,         # recall de conversas anteriores
        "busca_semantica": False,          # usa embeddings na seleção (mais preciso, um pouco mais lento)
        "hist_msgs": 6,                    # pares pergunta/resposta recentes enviados
        "fatos_modo": "perguntar",         # "perguntar" | "automatico" | "desligado"
    },
    # ── personalidade ──
    "instrucoes": {"preset": "padrao", "extra": "", "personalizados": []},
    "instrucoes_projeto": {},              # {"id-do-projeto": "texto"}
    "ctx_projeto": {},                     # {"id-do-projeto": {"fixas": [ids], "excluidas": [ids]}}
    # ── ferramentas: o modelo pode consultar/gravar memória, grafo, projetos e conversas no chat ──
    "ferramentas": {"ativo": True, "escrita": True, "max_rodadas": 4, "busca_auto": True, "pensar": "nunca"},
    # ── hardware informado à mão (sobrescreve a detecção automática quando ativo) ──
    "hardware_manual": {"ativo": False, "vram_gb": None, "ram_gb": None, "gpu": "", "unificada": False},
}

_ANINHADOS = ("geracao", "contexto", "instrucoes", "ferramentas", "hardware_manual")

_lock = threading.Lock()


def substituir_arquivo(tmp: str, destino: str, tentativas: int = 60):
    """os.replace com novas tentativas. No Windows o replace falha com PermissionError
    enquanto outra thread está lendo o arquivo de destino; a leitura dura milissegundos."""
    for i in range(tentativas):
        try:
            os.replace(tmp, destino)
            return
        except PermissionError:
            if i == tentativas - 1:
                raise
            time.sleep(0.02)


def abrir_leitura(caminho: str, modo: str = "rb", tentativas: int = 6, **kw):
    """open() de leitura que repete se o arquivo estiver trocado/bloqueado naquele instante
    (Windows). FileNotFoundError continua sendo propagado na hora."""
    for i in range(tentativas):
        try:
            return open(caminho, modo, **kw)
        except PermissionError:
            if i == tentativas - 1:
                raise
            time.sleep(0.02)


# Campos que nunca devem sair pela interface /api/estado (segredos do cofre e da API)
_CAMPOS_SECRETOS = ("api_token", "cripto_salt", "cripto_verif")


def carregar_config() -> dict:
    try:
        with abrir_leitura(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    # mescla com o padrão pra nunca faltar chave (ignora chaves legadas)
    legado = {"habilidades", "modelos", "modelo_modo"}
    mesclado = json.loads(json.dumps(CONFIG_PADRAO))
    mesclado.update({k: v for k, v in cfg.items() if k not in legado})
    if isinstance(cfg.get("habilidades"), dict):
        mesclado["habilidades"].update(cfg["habilidades"])
    for k in _ANINHADOS:                                   # blocos novos: mescla com o padrão
        if isinstance(cfg.get(k), dict):
            mesclado[k].update(cfg[k])
    for k in ("perfis_modelo", "instrucoes_projeto", "ctx_projeto"):
        mesclado[k] = cfg[k] if isinstance(cfg.get(k), dict) else {}
    # aceita modelos fora do catálogo (instalados à mão no Ollama), mas nunca um de embeddings
    if not nome_modelo_valido(mesclado.get("modelo")) or eh_embed(mesclado["modelo"]):
        mesclado["modelo"] = CONFIG_PADRAO["modelo"]
    if not nome_modelo_valido(mesclado.get("embed")):
        mesclado["embed"] = CONFIG_PADRAO["embed"]
    return mesclado


def config_publica(cfg: dict) -> dict:
    """Cópia da config sem token da API nem material do cofre (segura para a interface)."""
    return {k: v for k, v in cfg.items() if k not in _CAMPOS_SECRETOS}


def salvar_config(cfg: dict) -> dict:
    """Grava a config de forma atômica (arquivo temporário + os.replace), para que
    uma leitura concorrente nunca veja o arquivo pela metade (o que perderia o token)."""
    with _lock:
        tmp = f"{CONFIG_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            substituir_arquivo(tmp, CONFIG_FILE)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return cfg


def set_autostart(ligar: bool) -> bool:
    """Cria/remove o atalho na pasta Inicializar do Windows (sobe o servidor no login)."""
    import sys
    import subprocess
    startup = os.path.join(os.environ.get("APPDATA", ""),
                           r"Microsoft\Windows\Start Menu\Programs\Startup")
    lnk = os.path.join(startup, "Agente Local.lnk")
    if ligar:
        pyw = sys.executable
        if pyw.lower().endswith("python.exe"):
            pyw = pyw[:-10] + "pythonw.exe"
        alvo = os.path.join(BASE_DIR, "server.py")
        ps = (f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut("{lnk}");'
              f'$s.TargetPath="{pyw}";$s.Arguments=\'"{alvo}"\';'
              f'$s.WorkingDirectory="{BASE_DIR}";$s.Save()')
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           creationflags=0x08000000, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return False
    else:
        try:
            os.remove(lnk)
        except Exception:
            pass
    return ligar


def modelo_atual(cfg: dict = None) -> str:
    cfg = cfg or carregar_config()
    return cfg.get("modelo") or CONFIG_PADRAO["modelo"]


def habilidade(nome: str, cfg: dict = None) -> bool:
    cfg = cfg or carregar_config()
    return bool(cfg.get("habilidades", {}).get(nome, False))


# ── OLLAMA ────────────────────────────────────────────────────────────────────
_NO_WIN = 0x08000000


def ollama_online() -> bool:
    try:
        requests.get(f"{OLLAMA}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def achar_ollama() -> str:
    cands = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", "ollama.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return shutil.which("ollama") or ""


def achar_tesseract() -> str:
    cands = [
        os.path.join(os.environ.get("ProgramFiles", ""), "Tesseract-OCR", "tesseract.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Tesseract-OCR", "tesseract.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return shutil.which("tesseract") or ""


def primeiro_visao_instalado() -> str:
    """Nome do primeiro modelo de visão instalado (pra analisar imagens). '' se nenhum."""
    inst = listar_modelos()
    for m in modelos_chat():
        if m.get("visao") and (m["nome"] in inst or (m["nome"] + ":latest") in inst):
            return m["nome"]
    return ""


def descarregar_modelos():
    """Tira da memória todos os modelos carregados (libera o llama-server)."""
    try:
        carregados = [m["name"] for m in requests.get(f"{OLLAMA}/api/ps", timeout=10).json().get("models", [])]
    except Exception:
        carregados = []
    for m in carregados:
        try:
            requests.post(f"{OLLAMA}/api/generate", json={"model": m, "keep_alive": 0}, timeout=20)
        except Exception:
            pass


def iniciar_ollama_detalhe(espera: float = 12.0) -> dict:
    """Sobe o `ollama serve` se estiver instalado.
    Devolve {"ok", "online", "erro"} com erro em nao_instalado | falhou | timeout (ou None)."""
    if ollama_online():
        return {"ok": True, "online": True, "erro": None}
    exe = achar_ollama()
    if not exe:
        return {"ok": False, "online": False, "erro": "nao_instalado"}
    env = dict(os.environ)
    env["OLLAMA_FLASH_ATTENTION"] = "1"
    env["OLLAMA_KV_CACHE_TYPE"] = "q8_0"
    try:
        subprocess.Popen([exe, "serve"], creationflags=_NO_WIN, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return {"ok": False, "online": False, "erro": "falhou"}
    fim = time.time() + espera
    while time.time() < fim:
        if ollama_online():
            return {"ok": True, "online": True, "erro": None}
        time.sleep(0.5)
    on = ollama_online()
    return {"ok": on, "online": on, "erro": None if on else "timeout"}


def iniciar_ollama() -> bool:
    """Sobe o `ollama serve` se estiver instalado. Retorna True se ficou online."""
    return iniciar_ollama_detalhe()["ok"]


def listar_modelos_info() -> list:
    """Modelos instalados com tamanho e detalhes do /api/tags ([] se o Ollama estiver parado)."""
    try:
        ms = requests.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models", [])
    except Exception:
        return []
    out = []
    for m in ms if isinstance(ms, list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("name"), str):
            continue
        det = m.get("details") if isinstance(m.get("details"), dict) else {}
        tam = m.get("size") if isinstance(m.get("size"), (int, float)) else 0
        out.append({"nome": m["name"], "bytes": int(tam), "gb": round(tam / 1e9, 2),
                    "familia": str(det.get("family") or "")[:40],
                    "parametros": str(det.get("parameter_size") or "")[:20],
                    "quant": str(det.get("quantization_level") or "")[:20],
                    "modificado": str(m.get("modified_at") or "")[:40]})
    return out


def listar_modelos() -> list:
    return [m["nome"] for m in listar_modelos_info()]


def modelo_instalado(nome: str) -> bool:
    instalados = listar_modelos()
    return nome in instalados or (nome + ":latest") in instalados


# ── HARDWARE (GPU/VRAM, RAM, CPU) para dizer se um modelo cabe ───────────────
# Detecção multiplataforma, só leitura e com cache:
#   Windows: nvidia-smi + registro do Windows (AMD/Intel com VRAM real) + WMI (CPU)
#   Linux:   nvidia-smi, /sys/class/drm (AMD), /proc/cpuinfo
#   macOS:   sysctl (Apple Silicon = memória unificada; a GPU usa ~2/3–3/4 da RAM)
# O usuário pode sobrescrever tudo em Ajustes (config "hardware_manual").
_hw_cache = {"t": 0.0, "v": None}
_INTEGRADA = re.compile(r"(?i)(radeon\(tm\) graphics|radeon graphics|vega \d+ graphics|uhd graphics|hd graphics|"
                        r"iris|intel\(r\) graphics|microsoft basic|remote display|parsec|virtual)")


def _ram_total_gb() -> float:
    try:
        import ctypes
        from ctypes import wintypes

        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = _MS()
        m.dwLength = ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return round(m.ullTotalPhys / 1024 ** 3, 1)
    except Exception:
        pass
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1)
    except Exception:
        pass
    try:                                                     # macOS
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=4).stdout
        return round(int(out.strip()) / 1024 ** 3, 1)
    except Exception:
        return 0.0


def _rodar(cmd, timeout=6) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=_NO_WIN if os.name == "nt" else 0).stdout or ""
    except Exception:
        return ""


def _gpus_nvidia() -> list:
    out = _rodar(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpus = []
    for linha in out.strip().splitlines():
        try:
            nome, tot = [x.strip() for x in linha.rsplit(",", 1)]
            gpus.append({"nome": nome[:60], "vendor": "nvidia", "vram_gb": round(int(float(tot)) / 1024, 1)})
        except Exception:
            continue
    return gpus


def _gpus_windows() -> list:
    """AMD/Intel no Windows. O WMI (AdapterRAM) trava em 4 GB, então lê qwMemorySize do registro."""
    ps = ("$k='HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*';"
          "Get-ItemProperty $k -ErrorAction SilentlyContinue | ForEach-Object {"
          "$m=$_.'HardwareInformation.qwMemorySize'; if(-not $m){$m=$_.'HardwareInformation.MemorySize'};"
          "if($m -is [byte[]]){$m=[BitConverter]::ToUInt32($m,0)};"
          "'{0}|{1}' -f $_.DriverDesc,$m }")
    gpus = []
    for linha in _rodar(["powershell", "-NoProfile", "-Command", ps], timeout=8).strip().splitlines():
        nome, _, mem = linha.partition("|")
        nome = nome.strip()
        if not nome:
            continue
        try:
            gb = round(int(mem.strip() or 0) / 1024 ** 3, 1)
        except ValueError:
            gb = 0.0
        low = nome.lower()
        vendor = "nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low) else \
            "intel" if "intel" in low else "outro"
        gpus.append({"nome": nome[:60], "vendor": vendor, "vram_gb": gb})
    return gpus


def _gpus_linux() -> list:
    gpus = []
    try:
        for card in sorted(os.listdir("/sys/class/drm")):
            if not re.fullmatch(r"card\d+", card):
                continue
            base = f"/sys/class/drm/{card}/device"
            try:
                with open(f"{base}/vendor") as f:
                    vendor = f.read().strip()
            except OSError:
                continue
            if vendor == "0x1002":                          # AMD
                try:
                    with open(f"{base}/mem_info_vram_total") as f:
                        gb = round(int(f.read().strip()) / 1024 ** 3, 1)
                except (OSError, ValueError):
                    gb = 0.0
                gpus.append({"nome": "AMD Radeon", "vendor": "amd", "vram_gb": gb})
            elif vendor == "0x8086":                        # Intel (integrada na maioria)
                gpus.append({"nome": "Intel Graphics", "vendor": "intel", "vram_gb": 0.0})
    except OSError:
        pass
    return gpus


def _cpu_info() -> dict:
    nome, nucleos = "", 0
    sistema = platform.system()
    if sistema == "Windows":
        out = _rodar(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_Processor | ForEach-Object { '{0}|{1}' -f $_.Name,$_.NumberOfCores }"])
        for linha in out.strip().splitlines():
            n, _, c = linha.partition("|")
            nome = nome or n.strip()
            try:
                nucleos += int(c.strip())
            except ValueError:
                pass
    elif sistema == "Darwin":
        nome = _rodar(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        try:
            nucleos = int(_rodar(["sysctl", "-n", "hw.physicalcpu"]).strip())
        except ValueError:
            pass
    else:
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            m = re.search(r"model name\s*:\s*(.+)", txt)
            nome = m.group(1).strip() if m else ""
            pares = set(re.findall(r"physical id\s*:\s*(\d+)[\s\S]*?core id\s*:\s*(\d+)", txt))
            nucleos = len(pares)
        except OSError:
            pass
    return {"cpu": (nome or platform.processor() or "")[:80], "nucleos": nucleos or 0, "threads": os.cpu_count() or 0}


def _uso_unificada(ram_gb: float) -> float:
    """Quanto da memória unificada do Mac a GPU pode usar (limite padrão do macOS)."""
    return round(ram_gb * (0.67 if ram_gb <= 36 else 0.75), 1)


def _detectar_hw() -> dict:
    sistema = platform.system()
    ram = _ram_total_gb()
    gpus = []
    if sistema == "Darwin":
        cpu = _cpu_info()
        if platform.machine() == "arm64":
            chip = cpu["cpu"] or "Apple Silicon"
            gpus = [{"nome": chip[:60], "vendor": "apple", "vram_gb": _uso_unificada(ram), "unificada": True}]
        hw = {**cpu}
    else:
        gpus = _gpus_nvidia()
        if sistema == "Windows":
            extra = [g for g in _gpus_windows() if g["vendor"] != "nvidia"]
        else:
            extra = [g for g in _gpus_linux()]
        gpus += extra
        hw = _cpu_info()
    for g in gpus:                                           # integradas não contam (Ollama ignora por padrão)
        g["integrada"] = bool(g.get("vendor") != "apple" and (g["vram_gb"] < 1.5 or _INTEGRADA.search(g["nome"])))
    uteis = [g for g in gpus if not g["integrada"]]
    unificada = any(g.get("unificada") for g in uteis)
    tipo = uteis[0]["vendor"] if uteis else "cpu"
    vram = round(sum(g["vram_gb"] for g in uteis), 1)
    principal = max(uteis, key=lambda g: g["vram_gb"]) if uteis else None
    return {**hw, "gpu": principal["nome"] if principal else "", "vram_gb": vram, "ram_gb": ram,
            "gpus": gpus, "n_gpus": len(uteis), "tipo": tipo, "unificada": unificada,
            "so": sistema, "fonte": "auto"}


def _aplicar_manual(hw: dict, man) -> dict:
    """Sobrescreve a detecção com o que o usuário informou em Ajustes (só campos preenchidos)."""
    if not isinstance(man, dict) or not man.get("ativo"):
        return hw
    hw = dict(hw)
    for k in ("vram_gb", "ram_gb"):
        v = man.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            hw[k] = round(float(v), 1)
    if isinstance(man.get("gpu"), str) and man["gpu"].strip():
        hw["gpu"] = man["gpu"].strip()[:60]
    if isinstance(man.get("unificada"), bool):
        hw["unificada"] = man["unificada"]
    if isinstance(man.get("nucleos"), int) and man["nucleos"] > 0:
        hw["nucleos"] = man["nucleos"]
    hw["tipo"] = ("apple" if hw.get("unificada") else (hw.get("tipo") if hw.get("tipo") not in (None, "cpu") else "gpu")) \
        if hw.get("vram_gb") else "cpu"
    hw["fonte"] = "manual"
    return hw


def hardware_detectado(ttl: float = 300.0) -> dict:
    """Só a detecção automática (cache de alguns minutos)."""
    agora = time.time()
    if _hw_cache["v"] is None or agora - _hw_cache["t"] > ttl:
        try:
            _hw_cache["v"] = _detectar_hw()
        except Exception:
            _hw_cache["v"] = {"gpu": "", "vram_gb": 0.0, "ram_gb": _ram_total_gb(), "gpus": [], "tipo": "cpu",
                              "fonte": "auto"}
        _hw_cache["t"] = agora
    return json.loads(json.dumps(_hw_cache["v"]))


def hardware(ttl: float = 300.0) -> dict:
    """{"gpu", "vram_gb", "ram_gb", "gpus", "tipo", "unificada", "cpu", "nucleos", "threads", "so", "fonte"}
    da máquina, já com o ajuste manual do usuário aplicado (0 = não detectado)."""
    return _aplicar_manual(hardware_detectado(ttl), carregar_config().get("hardware_manual"))


def cabe(gb_modelo, hw: dict = None, num_ctx: int = 4096):
    """Onde o modelo roda: "vram" (cabe na placa), "parcial" (divide com a RAM, mais lento),
    "grande" (não cabe) ou None (tamanho/hardware desconhecido). Estimativa: arquivo + cache
    de contexto (~0,5 GB a cada 4k tokens) + folga de 0,5 GB. Na memória unificada do Mac,
    "vram" já é a parte que a GPU pode usar."""
    hw = hw if hw is not None else hardware()
    try:
        gb = float(gb_modelo)
    except (TypeError, ValueError):
        return None
    if gb <= 0:
        return None
    vram, ram = float(hw.get("vram_gb") or 0), float(hw.get("ram_gb") or 0)
    if not vram and not ram:
        return None
    preciso = gb + 0.5 * max(1, (num_ctx or 4096) / 4096) + 0.5
    if vram and preciso <= vram * 0.95:
        return "vram"
    if hw.get("unificada"):                                  # Mac: o resto vem da mesma RAM
        return "parcial" if preciso <= ram * 0.85 else "grande"
    if preciso <= vram + ram * 0.6:
        return "parcial"
    return "grande"


def melhor_variante(m: dict, hw: dict, num_ctx: int = 4096) -> dict:
    """A quantização mais fiel que cabe melhor nesta máquina: {"tag", "quant", "gb", "cabe"}."""
    ordem = {"vram": 0, "parcial": 1, "grande": 2, None: 3}
    opcoes = [{"tag": m["nome"], "quant": m.get("quant", ""), "gb": m["gb"]}] + list(m.get("variantes") or [])
    vistos, uniq = set(), []
    for o in opcoes:
        if o["tag"] not in vistos:
            vistos.add(o["tag"])
            uniq.append({**o, "cabe": cabe(o["gb"], hw, num_ctx)})
    # melhor encaixe primeiro; inteiro na VRAM, o maior arquivo (= quantização mais fiel);
    # dividido com a RAM, o menor (= mais camadas na GPU, mais rápido)
    uniq.sort(key=lambda o: (ordem.get(o["cabe"], 3), -o["gb"] if o["cabe"] == "vram" else o["gb"]))
    return uniq[0]


def _velocidade_ok(m: dict, var: dict, hw: dict) -> bool:
    """Dividido com a RAM ainda é usável? MoE (poucos parâmetros ativos) sim; denso só se a
    maior parte couber na GPU."""
    if var["cabe"] == "vram":
        return True
    if var["cabe"] != "parcial":
        return False
    vram, ram = float(hw.get("vram_gb") or 0), float(hw.get("ram_gb") or 0)
    # a parte que fica na RAM precisa deixar folga para o sistema e os outros programas
    # (no PC de teste, 16 GB, o offload parcial de ~2 GB já esbarrou no limite de memória)
    if not hw.get("unificada") and ram and (var["gb"] + 1.0 - vram * 0.95) > ram * 0.35:
        return False
    if m.get("ativos"):
        return m["ativos"] <= 6
    return bool(vram) and var["gb"] <= vram * 1.25          # denso: ~80% na GPU (9B Q4 em 6 GB = ~6 tok/s)


PAPEIS = ("rapido", "equilibrado", "inteligente", "visao", "codigo", "embed")


def recomendacoes(hw: dict = None, num_ctx: int = 4096) -> dict:
    """Melhores modelos do catálogo para ESTA máquina, por papel:
    rapido (folgado na VRAM), equilibrado (o melhor que cabe inteiro), inteligente (o melhor
    ainda usável dividindo com a RAM, ex. MoE), visao, codigo e embed.
    Devolve {papel: {"nome", "tag", "quant", "gb", "cabe"}}."""
    hw = hw if hw is not None else hardware()
    vram, ram = float(hw.get("vram_gb") or 0), float(hw.get("ram_gb") or 0)
    if not vram and not ram:
        return {}
    chat = [m for m in modelos_chat() if m.get("nivel")]
    cand = []
    for m in chat:
        v = melhor_variante(m, hw, num_ctx)
        cand.append((m, v))

    def melhor(filtro, chave=lambda mv: (mv[0].get("q", 0), -mv[1]["gb"])):
        ok = [mv for mv in cand if filtro(*mv)]
        if not ok:
            return None
        m, v = max(ok, key=chave)
        return {"nome": m["nome"], "tag": v["tag"], "quant": v.get("quant", ""), "gb": v["gb"], "cabe": v["cabe"]}

    sem_gpu = not vram
    lim_rapido = (vram * 0.6) if vram else min(3.5, ram * 0.2)
    out = {}
    out["rapido"] = melhor(lambda m, v: m.get("ferramentas") and v["gb"] <= lim_rapido and
                           (v["cabe"] == "vram" or (sem_gpu and v["cabe"] == "parcial")))
    out["equilibrado"] = melhor(lambda m, v: m.get("ferramentas") and
                                (v["cabe"] == "vram" or (sem_gpu and v["cabe"] == "parcial" and v["gb"] <= ram * 0.35)))
    out["inteligente"] = melhor(lambda m, v: m.get("ferramentas") and _velocidade_ok(m, v, hw) and
                                (not sem_gpu or v["gb"] <= ram * 0.5) and
                                m["nome"] != (out.get("equilibrado") or {}).get("nome"))
    out["visao"] = melhor(lambda m, v: m.get("visao") and (v["cabe"] == "vram" or (sem_gpu and v["gb"] <= ram * 0.3)))
    out["codigo"] = melhor(lambda m, v: "codigo" in m["usos"] and _velocidade_ok(m, v, hw))
    # inteligente só vale se for outro modelo, no mínimo tão bom quanto o equilibrado
    if out.get("inteligente") and out.get("equilibrado") and (
            out["inteligente"]["nome"] == out["equilibrado"]["nome"] or
            info_modelo(out["inteligente"]["nome"]).get("q", 0) < info_modelo(out["equilibrado"]["nome"]).get("q", 0)):
        out["inteligente"] = None
    emb = "nomic-embed-text"
    e = info_modelo(emb)
    out["embed"] = {"nome": emb, "tag": emb, "quant": "", "gb": e["gb"], "cabe": "vram"}
    return {k: v for k, v in out.items() if v}


# ── capacidades reportadas pelo Ollama (/api/show): tools, thinking, vision, embedding ──
_caps_cache = {}


def capacidades(nome: str, ttl: float = 600.0):
    """Lista de capacidades do modelo instalado segundo o Ollama, ou None se não deu pra saber
    (Ollama parado, modelo não instalado, versão antiga)."""
    if not nome_modelo_valido(nome):
        return None
    agora = time.time()
    c = _caps_cache.get(nome)
    if c and agora - c[0] < ttl:
        return list(c[1])
    if not ollama_online():
        return None
    try:
        d = requests.post(f"{OLLAMA}/api/show", json={"model": nome}, timeout=10).json()
        caps = d.get("capabilities") if isinstance(d, dict) else None
    except Exception:
        caps = None
    if not isinstance(caps, list):
        return None
    caps = [str(x) for x in caps][:20]
    _caps_cache[nome] = (agora, caps)
    return list(caps)


def suporta_ferramentas(nome: str, cfg: dict = None) -> bool:
    """O modelo pode chamar ferramentas? Ordem: ajuste manual do perfil do modelo >
    capacidades do Ollama > catálogo."""
    cfg = cfg or carregar_config()
    p = (cfg.get("perfis_modelo") or {}).get(nome) or {}
    if isinstance(p.get("ferramentas"), bool):
        return p["ferramentas"]
    caps = capacidades(nome)
    if caps is not None:
        return "tools" in caps
    m = info_modelo(nome) or info_modelo(nome[:-7] if nome.endswith(":latest") else nome)
    return bool(m.get("ferramentas"))


def _catalogo_por_variante(nome: str) -> dict:
    """Item do catálogo do qual `nome` é uma variante de quantização (ex.: qwen3.5:9b-q8_0)."""
    for m in CATALOGO_MODELOS:
        if any(v["tag"] == nome for v in (m.get("variantes") or [])):
            return m
    return {}


def _variantes_com_encaixe(m, hw, ctx):
    opcoes = [{"tag": m["nome"], "quant": m.get("quant", ""), "gb": m["gb"]}] + list(m.get("variantes") or [])
    vistos, out = set(), []
    for o in opcoes:
        if o["tag"] in vistos:
            continue
        vistos.add(o["tag"])
        out.append({**o, "cabe": cabe(o["gb"], hw, ctx)})
    return out


def estado() -> dict:
    """Resumo pra interface saber o que está pronto."""
    import ajustes                        # import tardio (ajustes importa o core)
    cfg = carregar_config()
    online = ollama_online()
    info = listar_modelos_info() if online else []
    instalados = [m["nome"] for m in info]
    hw = hardware()
    ctx = cfg.get("num_ctx") or 4096

    def inst(n):
        return online and (n in instalados or (n + ":latest") in instalados)

    recs = recomendacoes(hw, ctx)
    papeis = {}
    for papel, r in recs.items():
        papeis.setdefault(r["nome"], []).append(papel)
    catalogo = []
    for m in CATALOGO_MODELOS:
        vs = _variantes_com_encaixe(m, hw, ctx)
        catalogo.append({**m, "instalado": inst(m["nome"]) or any(inst(v["tag"]) for v in vs),
                         "cabe": cabe(m["gb"], hw, ctx), "variantes": vs,
                         "sugerida": melhor_variante(m, hw, ctx)["tag"],
                         "recomendado": papeis.get(m["nome"], []),
                         "ativo": m["nome"] == (cfg.get("embed") if m.get("tipo") == "embed" else cfg.get("modelo"))})
    instalados_info = []
    for m in info:
        cat = info_modelo(m["nome"]) or info_modelo(m["nome"][:-7] if m["nome"].endswith(":latest") else "") \
            or _catalogo_por_variante(m["nome"])
        tipo = "embed" if eh_embed(m["nome"]) else "chat"
        instalados_info.append({**m, "tipo": tipo, "catalogo": cat.get("nome", ""),
                                "rotulo": cat.get("rotulo", m["nome"]),
                                "cabe": cabe(m["gb"], hw, ctx) if tipo == "chat" else "vram",
                                "ferramentas": suporta_ferramentas(m["nome"], cfg) if tipo == "chat" else False,
                                "ativo": mesmo_modelo(m["nome"], cfg.get("embed" if tipo == "embed" else "modelo"))})
    try:
        wiki_pronto = any(f.lower().endswith(".zim") for f in os.listdir(WIKI_DIR))
    except Exception:
        wiki_pronto = False
    return {
        "ollama_online": online,
        "ollama_instalado": online or bool(achar_ollama()),
        "tesseract_instalado": bool(achar_tesseract()),
        "wiki_pronto": wiki_pronto,
        "config": config_publica(cfg),
        "catalogo": catalogo,
        "modelo_atual": modelo_atual(cfg),
        "instalados": instalados,
        "instalados_info": instalados_info,
        "hardware": hw,
        "hardware_detectado": hardware_detectado() if hw.get("fonte") == "manual" else None,
        "recomendacoes": recs,
        "niveis": list(NIVEIS),
        "modelo_instalado": inst(modelo_atual(cfg)),
        "provedores": PROVEDORES,
        "ajustes": ajustes.meta(),
    }
