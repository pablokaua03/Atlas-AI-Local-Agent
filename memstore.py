"""
memstore — gestão de memória para QUALQUER IA (projetos, memórias e grafo).

É a camada de dados da API /v1 (memapi.py) e do servidor MCP (mcp_atlas.py).
Tudo local, e cifrado em repouso quando o cofre está ligado (via cofre.py).

Modelo:
  - projeto:  um espaço de trabalho (ex.: "site-da-loja"). Sempre existe o "geral".
              Cada projeto vira também um nó do tipo "projeto" no grafo.
  - memória:  um item durável (fato, preferência, decisão, tarefa, nota...) com
              tags, importância (1..5), fonte (qual IA gravou) e entidades
              (nós do grafo que ela menciona).
  - grafo:    o mesmo grafo.json da interface. Nós criados pela API ficam
              marcados como "fixo" (não são podados pelo Tecelão) e podem
              pertencer a projetos.

Busca: por palavras (sempre) + semântica com embeddings do Ollama quando o
modelo de embedding (nomic-embed-text) já está instalado. Nada vai pra rede.
"""
import os
import re
import time
import secrets
import threading
import unicodedata

import core
import cofre
import skills

STORE_FILE = os.path.join(core.BASE_DIR, "memorias.json")
VETORES_FILE = os.path.join(core.BASE_DIR, "memorias_vetores.json")

PROJETO_PADRAO = "geral"
MAX_CONTEUDO = 4000
MAX_TAGS = 20
_lock = threading.RLock()
_indexando = threading.Event()


class ErroAPI(Exception):
    """Erro com status HTTP, convertido em JSON pela memapi."""
    def __init__(self, status, codigo, msg):
        super().__init__(msg)
        self.status, self.codigo, self.msg = status, codigo, msg


def _agora():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _checar_cofre():
    if not cofre.disponivel():
        raise ErroAPI(423, "vault_locked", "The Atlas vault is locked. Unlock it in the Atlas interface.")


def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return s


def _tokens(s):
    return re.findall(r"[a-z0-9]{2,}", _norm(s))


_VAZIAS = set("""
a o e os as um uma uns umas de do da dos das em no na nos nas por pra para com sem que se como
qual quais quando onde eu voce ele ela eles elas meu minha seu sua isso isto esse essa este esta
ao aos mais mas ou ja sao foi ser ter tem nao sim muito sobre
the an of to in on at for with by and or is are was were be it this that what which who how
my your his her its our their do does did not from as about me you
el la los las un una y del al es son lo le por con su sus que como
""".split())


def _tokens_busca(s):
    """Tokens da consulta sem palavras vazias (se sobrar nada, usa todos)."""
    t = list(dict.fromkeys(_tokens(s)))
    return [x for x in t if x not in _VAZIAS] or t


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", _norm(s)).strip("-")[:48]


def _limpar_tags(tags):
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,;]", tags)]
    if not isinstance(tags, list):
        return []
    out = []
    for t in tags:
        t = str(t).strip().lower()[:32]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _importancia(v, padrao=3):
    try:
        return max(1, min(5, int(v)))
    except Exception:
        return padrao


def _texto(v, campo, maximo, obrigatorio=False):
    v = "" if v is None else str(v).strip()
    if obrigatorio and not v:
        raise ErroAPI(400, "invalid_request", f"'{campo}' is required.")
    if len(v) > maximo:
        raise ErroAPI(400, "invalid_request", f"'{campo}' is too long (max {maximo} characters).")
    return v


# ── persistência ──────────────────────────────────────────────────────────────
def _carregar():
    s = cofre.ler_json(STORE_FILE, {})
    if not isinstance(s, dict):
        s = {}
    s.setdefault("versao", 1)
    s.setdefault("projetos", {})
    s.setdefault("memorias", {})
    if PROJETO_PADRAO not in s["projetos"]:
        s["projetos"][PROJETO_PADRAO] = {
            "id": PROJETO_PADRAO, "name": "Geral", "description": "Default project.",
            "tags": [], "meta": {}, "created": _agora(), "updated": _agora(), "node": "",
        }
    return s


def _salvar(s):
    if not cofre.salvar_json(STORE_FILE, s):
        raise ErroAPI(423, "vault_locked", "The Atlas vault is locked. Unlock it in the Atlas interface.")


# ── projetos (hierarquia) ─────────────────────────────────────────────────────
# Os projetos formam uma árvore. "geral" é a raiz implícita: projeto sem pai fica
# logo abaixo dele. A memória de um projeto HERDA a dos ancestrais até a geral
# (escopo "inherit"), e um projeto pode ser visto junto com toda a sua subárvore
# (escopo "tree").
ESCOPOS = ("exact", "inherit", "tree", "all")


def _pai(s, pid):
    if pid == PROJETO_PADRAO:
        return None
    p = s["projetos"].get(pid, {}).get("parent")
    return p if p in s["projetos"] and p != pid else PROJETO_PADRAO


def _ancestrais(s, pid):
    """[pai, avô, ..., geral] (sem o próprio)."""
    out, x = [], _pai(s, pid)
    while x and x not in out:
        out.append(x)
        x = _pai(s, x)
    return out


def _filhos(s, pid):
    return sorted((k for k in s["projetos"] if k != PROJETO_PADRAO and _pai(s, k) == pid),
                  key=lambda k: s["projetos"][k]["created"])


def _descendentes(s, pid):
    """{id: profundidade relativa} de toda a subárvore (sem o próprio)."""
    out, fila = {}, [(pid, 0)]
    while fila:
        x, d = fila.pop(0)
        for f in _filhos(s, x):
            if f not in out and f != pid:
                out[f] = d + 1
                fila.append((f, d + 1))
    return out


def _escopo(s, pid, escopo):
    """{projeto: distância} dos projetos incluídos no escopo; None = todos."""
    escopo = (escopo or "exact").lower()
    if escopo not in ESCOPOS:
        raise ErroAPI(400, "invalid_request", f"'scope' must be one of: {', '.join(ESCOPOS)}.")
    if not pid or escopo == "all":
        return None
    if escopo == "inherit":
        return {pid: 0, **{a: i + 1 for i, a in enumerate(_ancestrais(s, pid))}}
    if escopo == "tree":
        return {pid: 0, **_descendentes(s, pid)}
    return {pid: 0}


def _caminho(s, pid):
    ids = list(reversed(_ancestrais(s, pid))) + [pid]
    return [{"id": x, "name": s["projetos"][x]["name"]} for x in ids]


def _stats(s, pid, g=None):
    g = g or skills.carregar_grafo()
    proprias = sum(1 for m in s["memorias"].values() if m["project"] == pid)
    arvore = {pid, *_descendentes(s, pid)}
    total = sum(1 for m in s["memorias"].values() if m["project"] in arvore)
    nos = sum(1 for n in g["nos"].values() if pid in (n.get("projetos") or []))
    return {"memories": proprias, "total_memories": total, "nodes": nos,
            "parent": _pai(s, pid), "depth": len(_ancestrais(s, pid)),
            "path": _caminho(s, pid), "children": _filhos(s, pid)}


def _projeto_out(s, pid, g=None):
    return {**s["projetos"][pid], **_stats(s, pid, g)}


def _resolver_projeto(s, pid, criar=False):
    """Aceita id ou nome. Com criar=True, cria o projeto se não existir."""
    pid = (str(pid) if pid is not None else "").strip() or PROJETO_PADRAO
    if pid in s["projetos"]:
        return pid
    alvo = _slug(pid)
    for k, p in s["projetos"].items():
        if k == alvo or _slug(p["name"]) == alvo:
            return k
    if criar:
        return _novo_projeto(s, pid)["id"]
    raise ErroAPI(404, "project_not_found", f"Project '{pid}' does not exist.")


def _resolver_pai(s, pai, pid=None):
    """None/''/'geral' → sem pai. Recusa ciclos (pai dentro da própria subárvore)."""
    if pai in (None, "", PROJETO_PADRAO):
        return None
    pai = _resolver_projeto(s, pai)
    if pai == PROJETO_PADRAO:
        return None
    if pid and (pai == pid or pai in _descendentes(s, pid)):
        raise ErroAPI(400, "invalid_request", "A project cannot be moved inside itself or its subprojects.")
    return pai


def _ligar_hierarquia(s, pid):
    """Nó do pai --contém--> nó do projeto (e remove ligações antigas de hierarquia)."""
    no = s["projetos"][pid].get("node")
    if not no:
        return
    pai = s["projetos"][pid].get("parent")
    no_pai = s["projetos"][pai].get("node") if pai in s["projetos"] else ""
    with skills._grafo_lock:
        g = skills.carregar_grafo()
        g["arestas"] = {k: a for k, a in g["arestas"].items()
                        if not (a.get("hierarquia") and a["para"] == no)}
        if no_pai and no_pai in g["nos"] and no in g["nos"] and no_pai != no:
            g["arestas"].pop(f"{no}|||{no_pai}", None)
            g["arestas"][f"{no_pai}|||{no}"] = {"de": no_pai, "para": no, "rel": "contém", "peso": 2,
                                               "projeto": pai, "hierarquia": True}
        skills._salvar_grafo(g)


def _novo_projeto(s, nome, descricao="", tags=None, meta=None, pai=None):
    nome = _texto(nome, "name", 80, obrigatorio=True)
    base = _slug(nome) or "projeto"
    pid, i = base, 2
    while pid in s["projetos"]:
        pid, i = f"{base}-{i}", i + 1
    pai = _resolver_pai(s, pai)
    no = no_criar(nome, "projeto", projeto=pid, _interno=True)
    p = {"id": pid, "name": nome, "description": _texto(descricao, "description", 2000),
         "parent": pai, "tags": _limpar_tags(tags or []), "meta": meta if isinstance(meta, dict) else {},
         "created": _agora(), "updated": _agora(), "node": no["key"] if no else ""}
    s["projetos"][pid] = p
    _ligar_hierarquia(s, pid)
    return p


def _ordem_arvore(s):
    """Ids em ordem de árvore (pré-ordem), começando pela geral."""
    out = []

    def visita(x):
        out.append(x)
        for f in _filhos(s, x):
            visita(f)
    visita(PROJETO_PADRAO)
    return out


def projetos_listar():
    with _lock:
        _checar_cofre()
        s = _carregar()
        g = skills.carregar_grafo()
        return [_projeto_out(s, k, g) for k in _ordem_arvore(s)]


def projetos_arvore():
    """A hierarquia inteira, aninhada, a partir da memória geral."""
    with _lock:
        _checar_cofre()
        s = _carregar()
        g = skills.carregar_grafo()

        def no(x):
            p = _projeto_out(s, x, g)
            p.pop("children")
            p.pop("path")
            p["children"] = [no(f) for f in _filhos(s, x)]
            return p
        return no(PROJETO_PADRAO)


def projeto_obter(pid):
    with _lock:
        _checar_cofre()
        s = _carregar()
        return _projeto_out(s, _resolver_projeto(s, pid))


def projeto_criar(nome, descricao="", tags=None, meta=None, pai=None):
    with _lock:
        _checar_cofre()
        s = _carregar()
        alvo = _slug(nome or "")
        for k, p in s["projetos"].items():
            if alvo and (k == alvo or _slug(p["name"]) == alvo):
                raise ErroAPI(409, "project_exists", f"Project '{p['name']}' already exists (id '{k}').")
        p = _novo_projeto(s, nome, descricao, tags, meta, pai)
        _salvar(s)
        return _projeto_out(s, p["id"])


def projeto_atualizar(pid, dados):
    with _lock:
        _checar_cofre()
        s = _carregar()
        pid = _resolver_projeto(s, pid)
        p = s["projetos"][pid]
        if "name" in dados:
            p["name"] = _texto(dados["name"], "name", 80, obrigatorio=True)
            if p.get("node"):
                no_atualizar(p["node"], {"label": p["name"]}, _interno=True)
        if "description" in dados:
            p["description"] = _texto(dados["description"], "description", 2000)
        if "tags" in dados:
            p["tags"] = _limpar_tags(dados["tags"])
        if isinstance(dados.get("meta"), dict):
            p["meta"] = dados["meta"]
        if "parent" in dados:
            if pid == PROJETO_PADRAO:
                raise ErroAPI(400, "invalid_request", "The default project is the root and has no parent.")
            p["parent"] = _resolver_pai(s, dados["parent"], pid)
            _ligar_hierarquia(s, pid)
        p["updated"] = _agora()
        _salvar(s)
        return _projeto_out(s, pid)


def projeto_excluir(pid, cascata=False):
    """Apaga o projeto. Sem cascata, recusa se ainda houver memórias nele.
    Com cascata, apaga as memórias do projeto. Subprojetos sobem um nível (vão
    pro pai do projeto apagado). No grafo, só o nó do próprio projeto é
    removido; os outros nós continuam, desvinculados."""
    with _lock:
        _checar_cofre()
        s = _carregar()
        pid = _resolver_projeto(s, pid)
        if pid == PROJETO_PADRAO:
            raise ErroAPI(400, "invalid_request", "The default project cannot be deleted.")
        mems = [k for k, m in s["memorias"].items() if m["project"] == pid]
        if mems and not cascata:
            raise ErroAPI(409, "project_not_empty",
                          f"Project has {len(mems)} memories. Pass cascade=true to delete them too.")
        for k in mems:
            del s["memorias"][k]
        avo = s["projetos"][pid].get("parent")
        filhos = _filhos(s, pid)
        no_proj = s["projetos"][pid].get("node")
        del s["projetos"][pid]
        for f in filhos:
            s["projetos"][f]["parent"] = avo if avo in s["projetos"] else None
        _salvar(s)
        _apagar_vetores(mems)
    with skills._grafo_lock:
        g = skills.carregar_grafo()
        if no_proj and no_proj in g["nos"]:
            _remover_no(g, no_proj)
        for n in g["nos"].values():
            if pid in (n.get("projetos") or []):
                n["projetos"].remove(pid)
        for a in g["arestas"].values():
            if a.get("projeto") == pid:
                a.pop("projeto", None)
        skills._salvar_grafo(g)
    with _lock:
        s = _carregar()
        for f in filhos:
            if f in s["projetos"]:
                _ligar_hierarquia(s, f)
    return {"deleted": pid, "memories_deleted": len(mems), "children_moved": filhos}


# ── memórias ──────────────────────────────────────────────────────────────────
def _expirada(m):
    exp = m.get("expires_at")
    return bool(exp) and exp < _agora()


def _garantir_entidades(entidades, projeto, no_projeto=""):
    """Garante os nós das entidades e liga cada um ao nó do projeto ("envolve"),
    pra que o subgrafo do projeto fique conectado."""
    chaves = []
    if isinstance(entidades, str):          # "Stripe" vira ["Stripe"] (antes virava um nó por letra)
        entidades = [entidades]
    if not isinstance(entidades, (list, tuple)):
        entidades = []
    for e in entidades[:30]:
        no = no_criar(str(e), "tema", projeto=projeto, _interno=True) if str(e).strip() else None
        if no and no["key"] not in chaves:
            chaves.append(no["key"])
    if no_projeto and chaves:
        with skills._grafo_lock:
            g = skills.carregar_grafo()
            if no_projeto in g["nos"]:
                for k in chaves:
                    if k == no_projeto or f"{no_projeto}|||{k}" in g["arestas"] or f"{k}|||{no_projeto}" in g["arestas"]:
                        continue
                    g["arestas"][f"{no_projeto}|||{k}"] = {"de": no_projeto, "para": k, "rel": "envolve",
                                                         "peso": 1, "projeto": projeto}
                skills._salvar_grafo(g)
    return chaves


def memoria_criar(dados):
    with _lock:
        _checar_cofre()
        s = _carregar()
        conteudo = _texto(dados.get("content"), "content", MAX_CONTEUDO, obrigatorio=True)
        pid = _resolver_projeto(s, dados.get("project"), criar=bool(dados.get("create_project", True)))
        # evita duplicata exata no mesmo projeto: devolve a existente
        for m in s["memorias"].values():
            if m["project"] == pid and _norm(m["content"]) == _norm(conteudo):
                return {**m, "duplicate": True}
        m = {
            "id": "mem_" + secrets.token_hex(6),
            "project": pid,
            "content": conteudo,
            "type": _texto(dados.get("type") or "note", "type", 32).lower(),
            "tags": _limpar_tags(dados.get("tags") or []),
            "importance": _importancia(dados.get("importance")),
            "source": _texto(dados.get("source") or "api", "source", 64),
            "entities": _garantir_entidades(dados.get("entities"), pid, s["projetos"][pid].get("node", "")),
            "meta": dados["meta"] if isinstance(dados.get("meta"), dict) else {},
            "expires_at": _texto(dados.get("expires_at"), "expires_at", 32) or None,
            "created": _agora(),
            "updated": _agora(),
        }
        s["memorias"][m["id"]] = m
        s["projetos"][pid]["updated"] = _agora()
        _salvar(s)
    indexar_async()
    return m


def memoria_obter(mid):
    with _lock:
        _checar_cofre()
        m = _carregar()["memorias"].get(mid)
        if not m:
            raise ErroAPI(404, "memory_not_found", f"Memory '{mid}' does not exist.")
        return m


def memoria_atualizar(mid, dados):
    with _lock:
        _checar_cofre()
        s = _carregar()
        m = s["memorias"].get(mid)
        if not m:
            raise ErroAPI(404, "memory_not_found", f"Memory '{mid}' does not exist.")
        mudou_texto = False
        if "content" in dados:
            novo = _texto(dados["content"], "content", MAX_CONTEUDO, obrigatorio=True)
            mudou_texto = novo != m["content"]
            m["content"] = novo
        if "project" in dados:
            m["project"] = _resolver_projeto(s, dados["project"], criar=bool(dados.get("create_project", False)))
        if "type" in dados:
            m["type"] = _texto(dados["type"] or "note", "type", 32).lower()
        if "tags" in dados:
            m["tags"] = _limpar_tags(dados["tags"])
        if "add_tags" in dados:
            m["tags"] = _limpar_tags(m["tags"] + _limpar_tags(dados["add_tags"]))
        if "importance" in dados:
            m["importance"] = _importancia(dados["importance"], m["importance"])
        if "source" in dados:
            m["source"] = _texto(dados["source"], "source", 64)
        if "entities" in dados:
            m["entities"] = _garantir_entidades(dados["entities"], m["project"],
                                                s["projetos"][m["project"]].get("node", ""))
        if isinstance(dados.get("meta"), dict):
            m["meta"] = dados["meta"]
        if "expires_at" in dados:
            m["expires_at"] = _texto(dados["expires_at"], "expires_at", 32) or None
        m["updated"] = _agora()
        _salvar(s)
    if mudou_texto:
        _apagar_vetores([mid])
        indexar_async()
    return m


def memoria_excluir(mid):
    with _lock:
        _checar_cofre()
        s = _carregar()
        if mid not in s["memorias"]:
            raise ErroAPI(404, "memory_not_found", f"Memory '{mid}' does not exist.")
        del s["memorias"][mid]
        _salvar(s)
    _apagar_vetores([mid])
    return {"deleted": mid}


def _filtrar(s, projeto=None, tags=None, tipo=None, fonte=None, incluir_expiradas=False, escopo="exact"):
    """Filtra as memórias. Com projeto + escopo, cada item ganha 'distance'
    (0 = do próprio projeto; n = herdada de n níveis acima/abaixo)."""
    pid = _resolver_projeto(s, projeto) if projeto else None
    dist = _escopo(s, pid, escopo)
    tags = _limpar_tags(tags or [])
    out = []
    for m in s["memorias"].values():
        if dist is not None and m["project"] not in dist:
            continue
        if tags and not all(t in m["tags"] for t in tags):
            continue
        if tipo and m["type"] != tipo.lower():
            continue
        if fonte and m["source"] != fonte:
            continue
        if not incluir_expiradas and _expirada(m):
            continue
        if dist is not None and escopo != "exact":
            m = {**m, "distance": dist[m["project"]], "inherited": m["project"] != pid}
        out.append(m)
    return out


def memorias_listar(projeto=None, tags=None, tipo=None, fonte=None, limite=50, offset=0, ordem="recent",
                    escopo="exact"):
    with _lock:
        _checar_cofre()
        s = _carregar()
        mems = _filtrar(s, projeto, tags, tipo, fonte, escopo=escopo)
    mems.sort(key=lambda m: m["updated"], reverse=True)
    if ordem == "importance":
        mems.sort(key=lambda m: m["importance"], reverse=True)   # estável: empate fica por data
    limite = max(1, min(500, int(limite or 50)))
    offset = max(0, int(offset or 0))
    return {"total": len(mems), "items": mems[offset:offset + limite]}


def _score_palavras(q_toks, m):
    if not q_toks:
        return 0.0
    m_toks = set(_tokens(m["content"])) | set(_tokens(" ".join(m["tags"]))) | \
        set(_tokens(" ".join(m.get("entities") or [])))
    acertos = 0.0
    for t in q_toks:
        if t in m_toks:
            acertos += 1.0
        elif len(t) >= 4 and any(x.startswith(t) or t.startswith(x) for x in m_toks if len(x) >= 4):
            acertos += 0.5
    return acertos / len(q_toks)


def memorias_buscar(query, projeto=None, tags=None, tipo=None, limite=10, semantica=True, min_score=0.05,
                    escopo="inherit"):
    """Busca híbrida: palavras + (se disponível) similaridade de embeddings.
    Com projeto, o padrão é herdar a memória dos projetos-pai e da geral; o que
    é do próprio projeto pesa mais que o herdado."""
    query = _texto(query, "query", 2000)
    with _lock:
        _checar_cofre()
        s = _carregar()
        mems = _filtrar(s, projeto, tags, tipo, escopo=escopo)
    q_toks = _tokens_busca(query)
    vetores, qv = {}, None
    if semantica and query and mems:
        vetores = _carregar_vetores().get("v", {})
        if vetores and _embed_disponivel():
            qv = _embed(query)
    res = []
    for m in mems:
        kw = _score_palavras(q_toks, m)
        sem = None
        if qv is not None and m["id"] in vetores:
            sem = max(0.0, _cos(qv, vetores[m["id"]]))
        base = (0.55 * sem + 0.45 * kw) if sem is not None else kw
        if query and base < min_score:
            continue
        score = (base + 0.02 * m["importance"]) * max(0.5, 1 - 0.12 * m.get("distance", 0))
        res.append({**m, "score": round(score, 4)})
    if not query:
        res.sort(key=lambda m: (m["importance"], m["updated"]), reverse=True)
    else:
        res.sort(key=lambda m: m["score"], reverse=True)
    limite = max(1, min(100, int(limite or 10)))
    return {"query": query, "semantic": qv is not None, "items": res[:limite]}


# ── grafo ─────────────────────────────────────────────────────────────────────
def _no_out(k, n):
    return {"key": k, "label": n.get("label", k), "type": n.get("tipo", "tema"),
            "weight": n.get("peso", 1), "seen": n.get("visto", ""),
            "projects": list(n.get("projetos") or []), "description": n.get("descricao", ""),
            "pinned": bool(n.get("fixo"))}


def _aresta_out(a):
    return {"from": a["de"], "to": a["para"], "rel": a.get("rel", ""),
            "weight": a.get("peso", 1), "project": a.get("projeto"), "hierarchy": bool(a.get("hierarquia"))}


def _achar_no(g, ref):
    """Acha um nó pela chave ou pelo rótulo (com a mesma tolerância da interface)."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if ref in g["nos"]:
        return ref
    return skills._resolver(skills._chave(ref), g["nos"])


def _remover_no(g, k):
    del g["nos"][k]
    g["arestas"] = {ak: a for ak, a in g["arestas"].items() if a["de"] != k and a["para"] != k}


def _vincular_projeto(n, projeto):
    if projeto:
        ps = n.setdefault("projetos", [])
        if projeto not in ps:
            ps.append(projeto)


def _pid_existente(projeto):
    """Valida o projeto para operações de grafo (não cria)."""
    if not projeto:
        return None
    with _lock:
        return _resolver_projeto(_carregar(), projeto)


def grafo(projeto=None, escopo="exact"):
    """Grafo inteiro, ou só o de um projeto (escopo 'tree' inclui os subprojetos)."""
    _checar_cofre()
    pid = _pid_existente(projeto)
    incluidos = None
    if pid:
        with _lock:
            incluidos = set(_escopo(_carregar(), pid, "tree" if escopo == "tree" else "exact") or [pid])
    g = skills.carregar_grafo()
    nos = {k: n for k, n in g["nos"].items()
           if incluidos is None or incluidos & set(n.get("projetos") or [])}
    arestas = [a for a in g["arestas"].values() if a["de"] in nos and a["para"] in nos]
    return {"project": pid, "nodes": [_no_out(k, n) for k, n in nos.items()],
            "edges": [_aresta_out(a) for a in arestas]}


def nos_listar(q="", projeto=None, tipo=None, limite=50):
    _checar_cofre()
    pid = _pid_existente(projeto)
    g = skills.carregar_grafo()
    q_toks = _tokens_busca(q)
    out = []
    for k, n in g["nos"].items():
        if pid and pid not in (n.get("projetos") or []):
            continue
        if tipo and n.get("tipo") != tipo.lower():
            continue
        if q_toks:
            alvo = set(_tokens(k + " " + n.get("label", "") + " " + n.get("descricao", "")))
            if not any(t in alvo or any(x.startswith(t) for x in alvo) for t in q_toks):
                continue
        out.append(_no_out(k, n))
    out.sort(key=lambda n: -n["weight"])
    limite = max(1, min(500, int(limite or 50)))
    return {"total": len(out), "items": out[:limite]}


def no_obter(ref, com_memorias=True):
    _checar_cofre()
    g = skills.carregar_grafo()
    k = _achar_no(g, ref)
    if not k:
        raise ErroAPI(404, "node_not_found", f"Node '{ref}' does not exist.")
    nos = g["nos"]
    saida, entrada = [], []
    for a in g["arestas"].values():
        if a["de"] == k and a["para"] in nos:
            saida.append({**_aresta_out(a), "to_label": nos[a["para"]]["label"]})
        elif a["para"] == k and a["de"] in nos:
            entrada.append({**_aresta_out(a), "from_label": nos[a["de"]]["label"]})
    out = {**_no_out(k, nos[k]), "outgoing": saida, "incoming": entrada}
    if com_memorias:
        with _lock:
            mems = _carregar()["memorias"].values()
        lbl = _norm(nos[k]["label"])
        out["memories"] = [m for m in mems if not _expirada(m) and
                           (k in (m.get("entities") or []) or (len(lbl) >= 3 and lbl in _norm(m["content"])))][:50]
    return out


def no_criar(label, tipo="tema", projeto=None, descricao="", _interno=False):
    """Cria (ou reaproveita) um nó. Nó existente ganha o projeto/descrição."""
    if not _interno:
        _checar_cofre()
        projeto = _pid_existente(projeto)
    label = _texto(label, "label", 40 if _interno else 80, obrigatorio=not _interno)[:40]
    k = skills._chave(label)
    if not k:
        if _interno:
            return None
        raise ErroAPI(400, "invalid_request", "'label' must contain letters or numbers.")
    with skills._grafo_lock:
        g = skills.carregar_grafo()
        eq = skills._resolver(k, g["nos"])
        if eq:
            n = g["nos"][eq]
            n["fixo"] = True
            if descricao:
                n["descricao"] = _texto(descricao, "description", 500)
            if tipo and (tipo == "projeto" or n.get("tipo") == "tema"):
                n["tipo"] = tipo.strip().lower()[:20]
            k = eq
        else:
            n = {"label": label, "tipo": (tipo or "tema").strip().lower()[:20], "peso": 1,
                 "visto": time.strftime("%Y-%m-%d"), "fixo": True}
            if descricao:
                n["descricao"] = _texto(descricao, "description", 500)
            g["nos"][k] = n
        _vincular_projeto(n, projeto)
        skills._salvar_grafo(g)
    skills.emitir("grafo", {"nos": len(g["nos"])})
    return _no_out(k, n)


def no_atualizar(ref, dados, _interno=False):
    if not _interno:
        _checar_cofre()
    # resolve os projetos ANTES de pegar o lock do grafo (ordem dos locks: memória → grafo)
    add_p = _pid_existente(dados["add_project"]) if "add_project" in dados else None
    rem_p = _pid_existente(dados["remove_project"]) if "remove_project" in dados else None
    with skills._grafo_lock:
        g = skills.carregar_grafo()
        k = _achar_no(g, ref)
        if not k:
            raise ErroAPI(404, "node_not_found", f"Node '{ref}' does not exist.")
        n = g["nos"][k]
        if "label" in dados:
            n["label"] = _texto(dados["label"], "label", 40, obrigatorio=True)
        if "type" in dados:
            n["tipo"] = (_texto(dados["type"], "type", 20) or "tema").lower()
        if "description" in dados:
            n["descricao"] = _texto(dados["description"], "description", 500)
        if add_p:
            _vincular_projeto(n, add_p)
        if rem_p and rem_p in (n.get("projetos") or []):
            n["projetos"].remove(rem_p)
        n["fixo"] = True
        skills._salvar_grafo(g)
    return _no_out(k, n)


def no_excluir(ref):
    _checar_cofre()
    with skills._grafo_lock:
        g = skills.carregar_grafo()
        k = _achar_no(g, ref)
        if not k:
            raise ErroAPI(404, "node_not_found", f"Node '{ref}' does not exist.")
        _remover_no(g, k)
        skills._salvar_grafo(g)
    with _lock:
        s = _carregar()
        mudou = False
        for m in s["memorias"].values():
            if k in (m.get("entities") or []):
                m["entities"].remove(k)
                mudou = True
        for p in s["projetos"].values():
            if p.get("node") == k:
                p["node"] = ""
                mudou = True
        if mudou:
            _salvar(s)
    return {"deleted": k}


def aresta_criar(de, para, rel="", projeto=None):
    _checar_cofre()
    pid = _pid_existente(projeto)
    rel = (_texto(rel, "rel", 40) or "relates to")[:24]
    a_de = no_criar(de, "tema", projeto=pid, _interno=True)
    a_para = no_criar(para, "tema", projeto=pid, _interno=True)
    if not a_de or not a_para:
        raise ErroAPI(400, "invalid_request", "'from' and 'to' must be valid node labels or keys.")
    if a_de["key"] == a_para["key"]:
        raise ErroAPI(400, "invalid_request", "An edge cannot connect a node to itself.")
    with skills._grafo_lock:
        g = skills.carregar_grafo()
        ch, chi = f"{a_de['key']}|||{a_para['key']}", f"{a_para['key']}|||{a_de['key']}"
        g["arestas"].pop(chi, None)
        a = g["arestas"].get(ch) or {"de": a_de["key"], "para": a_para["key"], "peso": 0}
        a["rel"] = rel
        a["peso"] = a.get("peso", 0) + 1
        if pid:
            a["projeto"] = pid
        g["arestas"][ch] = a
        skills._salvar_grafo(g)
    skills.emitir("grafo", {"nos": len(g["nos"])})
    return _aresta_out(a)


def aresta_excluir(de, para):
    _checar_cofre()
    with skills._grafo_lock:
        g = skills.carregar_grafo()
        k_de, k_para = _achar_no(g, de), _achar_no(g, para)
        removidas = 0
        for ch in (f"{k_de}|||{k_para}", f"{k_para}|||{k_de}"):
            if g["arestas"].pop(ch, None):
                removidas += 1
        if not removidas:
            raise ErroAPI(404, "edge_not_found", f"No edge between '{de}' and '{para}'.")
        skills._salvar_grafo(g)
    return {"deleted": removidas}


def vizinhos(ref, profundidade=1, limite=100):
    """Subgrafo em volta de um nó (busca em largura até 'profundidade' saltos)."""
    _checar_cofre()
    g = skills.carregar_grafo()
    k = _achar_no(g, ref)
    if not k:
        raise ErroAPI(404, "node_not_found", f"Node '{ref}' does not exist.")
    profundidade = max(1, min(4, int(profundidade or 1)))
    adj = {}
    for a in g["arestas"].values():
        adj.setdefault(a["de"], []).append(a["para"])
        adj.setdefault(a["para"], []).append(a["de"])
    visto, fronteira = {k: 0}, [k]
    for d in range(1, profundidade + 1):
        prox = []
        for x in fronteira:
            for y in adj.get(x, []):
                if y not in visto and y in g["nos"] and len(visto) < limite:
                    visto[y] = d
                    prox.append(y)
        fronteira = prox
    nos = [{**_no_out(x, g["nos"][x]), "depth": d} for x, d in visto.items()]
    arestas = [_aresta_out(a) for a in g["arestas"].values() if a["de"] in visto and a["para"] in visto]
    return {"center": k, "depth": profundidade, "nodes": nos, "edges": arestas}


# ── contexto pronto pra injetar no prompt de qualquer IA ──────────────────────
def contexto(query="", projeto=None, limite=8, incluir_grafo=True, incluir_fatos=True, escopo="inherit"):
    _checar_cofre()
    busca = memorias_buscar(query, projeto=projeto, limite=limite, escopo=escopo)
    mems = busca["items"]
    linhas = []
    nomes_proj = {p["id"]: p["name"] for p in projetos_listar()}
    if projeto:
        p = projeto_obter(projeto)
        trilha = " › ".join(x["name"] for x in p["path"] if x["id"] != PROJETO_PADRAO or len(p["path"]) == 1)
        linhas.append(f"# Project: {trilha}" + (f" — {p['description']}" if p["description"] else ""))
        pais = [x for x in p["path"][:-1] if x["id"] != PROJETO_PADRAO]
        if escopo == "inherit" and pais:
            linhas.append("(includes memories inherited from: " + ", ".join(x["name"] for x in pais) + ", Geral)")
    if incluir_fatos:
        fatos = skills.carregar_mem().get("fatos", [])[-15:]
        if fatos:
            linhas.append("## About the user")
            linhas += [f"- {f}" for f in fatos]
    if mems:
        linhas.append("## Relevant memories")
        for m in mems:
            extra = f" [{', '.join(m['tags'])}]" if m["tags"] else ""
            origem = nomes_proj.get(m["project"], m["project"])
            linhas.append(f"- ({m['type']}, {origem}) {m['content']}{extra}")
    mapa = ""
    if incluir_grafo:
        if projeto:
            sub = grafo(projeto, escopo="tree")
            nomes = {n["key"]: n["label"] for n in sub["nodes"]}
            rels = [f"- {nomes[e['from']]} {e['rel']} {nomes[e['to']]}" for e in sub["edges"]][:25]
            mapa = "\n".join(rels)
        else:
            mapa = skills.resumo_grafo(query) if query else skills.resumo_grafo("", max_nos=8)
        if mapa:
            linhas.append("## Knowledge graph")
            linhas.append(mapa)
    return {"query": query, "project": projeto, "text": "\n".join(linhas),
            "memories": mems, "semantic": busca["semantic"]}


def contexto_chat(texto, limite=6):
    """Versão curta pro chat do próprio Atlas (só palavras, sem latência extra)."""
    try:
        r = memorias_buscar(texto, limite=limite, semantica=False, min_score=0.2)
    except Exception:
        return ""
    return "\n".join(f"- {m['content']}" for m in r["items"])


def exportar(projeto=None):
    with _lock:
        _checar_cofre()
        s = _carregar()
        pid = _resolver_projeto(s, projeto) if projeto else None
        projetos = [p for k, p in s["projetos"].items() if not pid or k == pid]
        mems = [m for m in s["memorias"].values() if not pid or m["project"] == pid]
    return {"exported": _agora(), "projects": projetos, "memories": mems, "graph": grafo(pid)}


# ── embeddings (opcional, local via Ollama) ──────────────────────────────────
def _embed_disponivel():
    try:
        import docs
        return core.ollama_online() and core.modelo_instalado(docs._modelo_embed())
    except Exception:
        return False


def _embed(texto):
    import docs
    v = docs._embed(texto)
    return [round(x, 5) for x in v] if v else None


def _cos(a, b):
    import docs
    return docs._cos(a, b)


def _carregar_vetores():
    v = cofre.ler_json(VETORES_FILE, {})
    if not isinstance(v, dict):
        v = {}
    v.setdefault("v", {})
    return v


def _apagar_vetores(ids):
    if not ids:
        return
    with _lock:
        v = _carregar_vetores()
        if any(i in v["v"] for i in ids):
            for i in ids:
                v["v"].pop(i, None)
            cofre.salvar_json(VETORES_FILE, v, indent=None)


def indexar_async():
    """Calcula em segundo plano os embeddings que faltam (só se o modelo já estiver instalado)."""
    if _indexando.is_set():
        return
    _indexando.set()

    def _run():
        try:
            if not cofre.disponivel() or not _embed_disponivel():
                return
            import docs
            modelo = docs._modelo_embed()
            with _lock:
                mems = dict(_carregar()["memorias"])
                v = _carregar_vetores()
            if v.get("model") != modelo:
                v = {"model": modelo, "v": {}}
            faltam = [m for k, m in mems.items() if k not in v["v"]]
            novos = {}
            for m in faltam:
                e = _embed(m["content"] + (" " + " ".join(m["tags"]) if m["tags"] else ""))
                if e:
                    novos[m["id"]] = e
            with _lock:
                atual = _carregar_vetores()
                if atual.get("model") != modelo:
                    atual = {"model": modelo, "v": {}}
                vivos = set(_carregar()["memorias"])
                atual["v"].update({k: e for k, e in novos.items() if k in vivos})
                atual["v"] = {k: e for k, e in atual["v"].items() if k in vivos}
                cofre.salvar_json(VETORES_FILE, atual, indent=None)
        except Exception:
            pass
        finally:
            _indexando.clear()

    threading.Thread(target=_run, daemon=True).start()
