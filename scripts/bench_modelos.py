"""Benchmark local dos modelos do Ollama para o Atlas (velocidade, VRAM, PT-BR, ferramentas).
Uso: python bench_modelos.py [modelo ...]   (sem argumentos: todos os modelos de chat instalados)
Saída: bench_resultados.json ao lado do script. Não toca nos dados do Atlas."""
import json, os, sys, time, math
import requests

OL = "http://127.0.0.1:11434"
CTX = 4096
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_resultados.json")

TOOLS = [
    {"type": "function", "function": {"name": "search_memories", "description": "Search the user's saved memories (facts, preferences, decisions, notes). Use before answering questions about the user, their projects or past decisions.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "What to look for."}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "save_memory", "description": "Save a durable memory when the user states a lasting fact/preference/decision or explicitly asks you to remember something.",
     "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "The memory, one self-contained sentence."},
                                                      "type": {"type": "string", "enum": ["fact", "preference", "decision", "task", "note"]}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "link_concepts", "description": "Link two concepts in the knowledge graph with a short relation.",
     "parameters": {"type": "object", "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "relation": {"type": "string"}}, "required": ["from", "to", "relation"]}}},
]
SYS = "Você é o assistente pessoal local do Pablo. Responda SEMPRE em português brasileiro. Use as ferramentas quando fizer sentido; não use para conversa casual."
CASOS = [
    ("save", "Guarda isso: meu time do coração é o Palmeiras.", "save_memory", lambda a: "palmeiras" in json.dumps(a, ensure_ascii=False).lower()),
    ("search", "O que eu decidi sobre o banco de dados do projeto SpaceBooks? Confere nas minhas memórias.", "search_memories", lambda a: any(k in json.dumps(a, ensure_ascii=False).lower() for k in ("banco", "spacebooks", "database", "dados"))),
    ("link", "No meu grafo, liga o conceito 'Atlas' ao conceito 'Ollama' com a relação 'usa'.", "link_concepts", lambda a: "atlas" in json.dumps(a).lower() and "ollama" in json.dumps(a).lower()),
    ("none", "Oi! Tudo bem?", None, None),
]
PT_PROMPT = ("Explique em no máximo 4 frases, para um iniciante, por que um modelo de IA local fica lento quando "
             "não cabe inteiro na VRAM da placa de vídeo.")


def show(m):
    try:
        return requests.post(f"{OL}/api/show", json={"model": m}, timeout=30).json()
    except Exception:
        return {}


def chat(m, msgs, tools=None, think=None, npred=300):
    body = {"model": m, "messages": msgs, "stream": False, "keep_alive": "10m",
            "options": {"num_ctx": CTX, "temperature": 0.2, "num_predict": npred}}
    if tools:
        body["tools"] = tools
    if think is not None:
        body["think"] = think
    t = time.time()
    r = requests.post(f"{OL}/api/chat", json=body, timeout=900).json()
    r["_wall"] = time.time() - t
    return r


def ps(m):
    for x in requests.get(f"{OL}/api/ps", timeout=10).json().get("models", []):
        if x.get("name") == m or x.get("model") == m:
            return x
    return {}


def bench(m):
    info = show(m)
    caps = info.get("capabilities") or []
    det = info.get("details") or {}
    think = False if "thinking" in caps else None
    res = {"model": m, "capabilities": caps, "params": det.get("parameter_size"), "quant": det.get("quantization_level"), "family": det.get("family")}
    # aquecimento (carrega o modelo) e tempo de carga
    w = chat(m, [{"role": "user", "content": "Diga só: ok"}], think=think, npred=5)
    res["load_s"] = round((w.get("load_duration") or 0) / 1e9, 2)
    p = ps(m)
    size, vram = p.get("size") or 0, p.get("size_vram") or 0
    res["mem_gb"] = round(size / 1e9, 2)
    res["vram_gb"] = round(vram / 1e9, 2)
    res["gpu_pct"] = round(100 * vram / size) if size else None
    # velocidade + qualidade PT-BR (2 execuções, mediana simples)
    speeds, pp = [], []
    resp = ""
    for i in range(2):
        r = chat(m, [{"role": "system", "content": "Responda em português brasileiro."}, {"role": "user", "content": PT_PROMPT}], think=think, npred=220)
        ec, ed = r.get("eval_count") or 0, r.get("eval_duration") or 1
        pc, pd = r.get("prompt_eval_count") or 0, r.get("prompt_eval_duration") or 1
        speeds.append(ec / (ed / 1e9))
        if pc:
            pp.append(pc / (pd / 1e9))
        resp = (r.get("message") or {}).get("content", "")
    res["tok_s"] = round(sorted(speeds)[len(speeds) // 2], 1)
    res["prompt_tok_s"] = round(max(pp), 1) if pp else None
    res["pt_answer"] = resp.strip()
    # ferramentas
    tool_res = []
    if "tools" in caps:
        for nome, prompt, esperado, ok_args in CASOS:
            try:
                r = chat(m, [{"role": "system", "content": SYS}, {"role": "user", "content": prompt}], tools=TOOLS, think=think, npred=250)
            except Exception as e:
                tool_res.append({"case": nome, "error": str(e)[:200]})
                continue
            msg = r.get("message") or {}
            calls = msg.get("tool_calls") or []
            nomes = [((c.get("function") or {}).get("name")) for c in calls]
            args = [((c.get("function") or {}).get("arguments")) for c in calls]
            if esperado is None:
                ok = not calls
            else:
                ok = esperado in nomes and any(ok_args(a or {}) for n, a in zip(nomes, args) if n == esperado)
            item = {"case": nome, "ok": ok, "calls": [{"name": n, "args": a} for n, a in zip(nomes, args)],
                    "text": (msg.get("content") or "")[:200], "s": round(r["_wall"], 1)}
            # rodada 2: devolve o resultado da ferramenta e vê se responde em PT
            if calls and esperado:
                follow = [{"role": "system", "content": SYS}, {"role": "user", "content": prompt}, msg,
                          {"role": "tool", "tool_name": nomes[0], "content": json.dumps({"ok": True, "items": [{"content": "Decidimos usar MySQL 8 no SpaceBooks (out/2026)."}]} if nomes[0] == "search_memories" else {"ok": True, "id": "mem_123"}, ensure_ascii=False)}]
                r2 = chat(m, follow, tools=TOOLS, think=think, npred=200)
                item["final"] = ((r2.get("message") or {}).get("content") or "")[:300]
            tool_res.append(item)
    res["tools"] = tool_res
    res["tools_score"] = f"{sum(1 for t in tool_res if t.get('ok'))}/{len(tool_res)}" if tool_res else "n/a"
    # descarrega
    requests.post(f"{OL}/api/generate", json={"model": m, "keep_alive": 0}, timeout=60)
    return res


def bench_embed(m):
    def emb(t):
        return requests.post(f"{OL}/api/embed", json={"model": m, "input": t}, timeout=120).json()["embeddings"][0]
    def cos(a, b):
        return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    emb("aquecimento")
    t = time.time()
    a = emb("Meu time do coração é o Palmeiras.")
    b = emb("Qual time de futebol eu torço?")
    c = emb("Decidimos usar MySQL no projeto SpaceBooks.")
    dt = (time.time() - t) / 3
    p = ps(m)
    requests.post(f"{OL}/api/generate", json={"model": m, "keep_alive": 0}, timeout=60)
    return {"model": m, "embed": True, "dims": len(a), "ms_per_text": round(dt * 1000), "sim_related": round(cos(a, b), 3),
            "sim_unrelated": round(cos(b, c), 3), "vram_gb": round((p.get("size_vram") or 0) / 1e9, 2)}


def main():
    tags = requests.get(f"{OL}/api/tags", timeout=10).json().get("models", [])
    nomes = sys.argv[1:] or [t["name"] for t in tags]
    out = []
    for n in nomes:
        caps = show(n).get("capabilities") or []
        print(">>", n, caps, flush=True)
        try:
            r = bench_embed(n) if "embedding" in caps else bench(n)
        except Exception as e:
            r = {"model": n, "error": str(e)[:300]}
        print(json.dumps({k: v for k, v in r.items() if k not in ("pt_answer", "tools")}, ensure_ascii=False), flush=True)
        out.append(r)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("salvo em", OUT)


if __name__ == "__main__":
    main()
