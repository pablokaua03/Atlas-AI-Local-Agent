"""
ajustes — validação, limites e seções das configurações do Atlas.

Tudo que a interface (ou um backup restaurado) manda para /api/config passa por aqui:
valores são convertidos, limitados ao intervalo permitido e o que for inválido é
ignorado com um aviso (nunca vira erro 500). Também define as seções do painel de
Ajustes e o "restaurar padrão" de cada uma. Não mexe em api_token nem no material do cofre.
"""
import re
import hashlib
import json

import core

# (mínimo, máximo, padrão)
LIM_NUM_CTX = (1024, 32768, 4096)
LIM_GERACAO = {"temperatura": (0.0, 2.0), "top_p": (0.05, 1.0), "max_tokens": (16, 8192), "seed": (0, 2147483647)}
LIM_CONTEXTO = {"ctx_pct": (10, 70), "max_memorias": (0, 20), "recencia_dias": (1, 365), "hist_msgs": (0, 20)}
FATOS_MODOS = ("perguntar", "automatico", "desligado")
MAX_EXTRA = 2000
MAX_NOME_PRESET = 40
MAX_PRESETS = 12
MAX_IDS = 200

# Seções do painel -> chaves da config que "restaurar padrão" devolve ao valor de fábrica
SECOES = {
    "modelo": ["modelo", "embed", "num_ctx", "perfis_modelo"],
    "memoria": ["contexto", "ctx_projeto"],
    "personalidade": ["instrucoes", "instrucoes_projeto"],
    "geracao": ["geracao"],
    "privacidade": ["api_ativa", "ativo"],
    "geral": ["idioma", "tema", "nome"],
}

# Presets de fábrica (somente leitura). O texto é acrescentado ao prompt do sistema.
PRESETS = {
    "padrao": {"nome": {"pt": "Padrão", "en": "Default", "es": "Predeterminado"},
               "texto": {"pt": "", "en": "", "es": ""}},
    "conciso": {"nome": {"pt": "Conciso", "en": "Concise", "es": "Conciso"},
                "texto": {"pt": "Seja extremamente conciso: vá direto ao ponto, em poucas frases.",
                          "en": "Be extremely concise: get straight to the point in a few sentences.",
                          "es": "Sé extremadamente conciso: ve directo al punto en pocas frases."}},
    "tecnico": {"nome": {"pt": "Técnico", "en": "Technical", "es": "Técnico"},
                "texto": {"pt": "Responda como um engenheiro sênior: preciso, com exemplos de código quando ajudar e citando premissas.",
                          "en": "Answer like a senior engineer: precise, with code examples when helpful, stating assumptions.",
                          "es": "Responde como un ingeniero sénior: preciso, con ejemplos de código cuando ayude y indicando supuestos."}},
    "professor": {"nome": {"pt": "Professor", "en": "Teacher", "es": "Profesor"},
                  "texto": {"pt": "Explique passo a passo, com analogias simples, e confirme se ficou claro.",
                            "en": "Explain step by step with simple analogies, and check that it is clear.",
                            "es": "Explica paso a paso con analogías simples y confirma que quedó claro."}},
    "criativo": {"nome": {"pt": "Criativo", "en": "Creative", "es": "Creativo"},
                 "texto": {"pt": "Seja criativo e proponha alternativas originais, mantendo o foco no pedido.",
                           "en": "Be creative and suggest original alternatives while staying on topic.",
                           "es": "Sé creativo y propone alternativas originales sin perder el foco."}},
}


def meta() -> dict:
    """Limites e presets para a interface montar os controles."""
    return {
        "num_ctx": list(LIM_NUM_CTX), "geracao": {k: list(v) for k, v in LIM_GERACAO.items()},
        "contexto": {k: list(v) for k, v in LIM_CONTEXTO.items()}, "fatos_modos": list(FATOS_MODOS),
        "max_extra": MAX_EXTRA, "max_presets": MAX_PRESETS, "secoes": SECOES, "presets": PRESETS,
    }


# ── conversões ────────────────────────────────────────────────────────────────
def _num(v, lo, hi, inteiro=False):
    """Número finito limitado a [lo, hi]; None se não der pra converter."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    f = max(lo, min(hi, f))
    return int(round(f)) if inteiro else round(f, 3)


def _texto(v, maximo):
    return v.strip()[:maximo] if isinstance(v, str) else None


def _ids(v):
    if not isinstance(v, list):
        return None
    out = []
    for x in v:
        if isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", x) and x not in out:
            out.append(x)
    return out[:MAX_IDS]


def _limites_modelo(nome):
    m = core.info_modelo(nome)
    return min(LIM_NUM_CTX[1], m.get("ctx_max", LIM_NUM_CTX[1])) if m else LIM_NUM_CTX[1]


def _perfil(d, nome):
    """Valida um perfil de modelo; chaves vazias/None = usar o padrão."""
    if not isinstance(d, dict):
        return None
    out = {}
    ctx = _num(d.get("num_ctx"), LIM_NUM_CTX[0], _limites_modelo(nome), True)
    if ctx is not None:
        out["num_ctx"] = ctx
    for k, (lo, hi) in LIM_GERACAO.items():
        v = _num(d.get(k), lo, hi, k in ("max_tokens", "seed"))
        if v is not None:
            out[k] = v
    return out


def _presets(lista, avisos):
    out, vistos = [], set()
    if not isinstance(lista, list):
        avisos.append("instrucoes.personalizados: lista esperada")
        return None
    for it in lista[:MAX_PRESETS]:
        if not isinstance(it, dict):
            continue
        nome, texto = _texto(it.get("nome"), MAX_NOME_PRESET), _texto(it.get("texto"), MAX_EXTRA)
        pid = it.get("id") if isinstance(it.get("id"), str) and re.fullmatch(r"[a-z0-9_\-]{1,40}", it.get("id")) else None
        if not nome or texto is None:
            continue
        if not pid or pid in PRESETS or pid in vistos:
            pid = "p" + hashlib.sha1((nome + "\0" + texto).encode("utf-8")).hexdigest()[:8]
            while pid in vistos or pid in PRESETS:
                pid += "x"
        vistos.add(pid)
        out.append({"id": pid, "nome": nome, "texto": texto})
    return out


# ── aplicar um patch vindo da interface ──────────────────────────────────────
def aplicar(cfg: dict, d: dict):
    """Aplica em cfg os campos NOVOS de um patch (os antigos seguem em server.api_config).
    Devolve (cfg, avisos)."""
    avisos = []
    if not isinstance(d, dict):
        return cfg, avisos

    if "modelo" in d:
        if d["modelo"] in [m["nome"] for m in core.modelos_chat()]:
            cfg["modelo"] = d["modelo"]
        else:
            avisos.append("modelo: não é um modelo de chat do catálogo")
    if "embed" in d:
        if d["embed"] in [m["nome"] for m in core.modelos_embed()]:
            cfg["embed"] = d["embed"]
        else:
            avisos.append("embed: não é um modelo de embeddings do catálogo")
    if "num_ctx" in d:
        v = _num(d["num_ctx"], *LIM_NUM_CTX[:2], inteiro=True)
        if v is None:
            avisos.append("num_ctx inválido")
        else:
            cfg["num_ctx"] = v

    if isinstance(d.get("perfis_modelo"), dict):
        perfis = dict(cfg.get("perfis_modelo") or {})
        for nome, val in d["perfis_modelo"].items():
            if nome not in [m["nome"] for m in core.modelos_chat()]:
                avisos.append(f"perfis_modelo.{str(nome)[:40]}: modelo desconhecido")
                continue
            if val is None or val == {}:
                perfis.pop(nome, None)
                continue
            p = _perfil(val, nome)
            if p is None:
                avisos.append(f"perfis_modelo.{nome}: objeto esperado")
            elif p:
                perfis[nome] = p
            else:
                perfis.pop(nome, None)
        cfg["perfis_modelo"] = perfis

    if isinstance(d.get("geracao"), dict):
        g = dict(cfg.get("geracao") or {})
        for k, (lo, hi) in LIM_GERACAO.items():
            if k in d["geracao"]:
                if d["geracao"][k] in (None, ""):
                    g[k] = None
                else:
                    v = _num(d["geracao"][k], lo, hi, k in ("max_tokens", "seed"))
                    if v is None:
                        avisos.append(f"geracao.{k} inválido")
                    else:
                        g[k] = v
        cfg["geracao"] = g

    if isinstance(d.get("contexto"), dict):
        c = dict(cfg.get("contexto") or {})
        dc = d["contexto"]
        for k, (lo, hi) in LIM_CONTEXTO.items():
            if k in dc:
                v = _num(dc[k], lo, hi, True)
                if v is None:
                    avisos.append(f"contexto.{k} inválido")
                else:
                    c[k] = v
        if "incluir_conversas" in dc:
            c["incluir_conversas"] = bool(dc["incluir_conversas"])
        if "fatos_modo" in dc:
            if dc["fatos_modo"] in FATOS_MODOS:
                c["fatos_modo"] = dc["fatos_modo"]
            else:
                avisos.append("contexto.fatos_modo inválido")
        cfg["contexto"] = c

    if isinstance(d.get("instrucoes"), dict):
        ins = dict(cfg.get("instrucoes") or {})
        di = d["instrucoes"]
        if "personalizados" in di:
            novos = _presets(di["personalizados"], avisos)
            if novos is not None:
                ins["personalizados"] = novos
        if "extra" in di:
            t = _texto(di["extra"], MAX_EXTRA)
            if t is None:
                avisos.append("instrucoes.extra: texto esperado")
            else:
                ins["extra"] = t
        if "preset" in di:
            ids = set(PRESETS) | {p["id"] for p in ins.get("personalizados", [])}
            if di["preset"] in ids:
                ins["preset"] = di["preset"]
            else:
                avisos.append("instrucoes.preset desconhecido")
        if ins.get("preset") not in set(PRESETS) | {p["id"] for p in ins.get("personalizados", [])}:
            ins["preset"] = "padrao"            # preset apagado -> volta ao padrão
        cfg["instrucoes"] = ins

    if isinstance(d.get("instrucoes_projeto"), dict):
        ip = dict(cfg.get("instrucoes_projeto") or {})
        for pid, txt in d["instrucoes_projeto"].items():
            if not (isinstance(pid, str) and re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", pid)):
                continue
            t = _texto(txt, MAX_EXTRA) if txt is not None else ""
            if t is None:
                avisos.append("instrucoes_projeto: texto esperado")
            elif t:
                ip[pid] = t
            else:
                ip.pop(pid, None)
        cfg["instrucoes_projeto"] = ip

    if isinstance(d.get("ctx_projeto"), dict):
        cp = dict(cfg.get("ctx_projeto") or {})
        for pid, val in d["ctx_projeto"].items():
            if not (isinstance(pid, str) and re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", pid)) or not isinstance(val, dict):
                continue
            atual = dict(cp.get(pid) or {})
            for k in ("fixas", "excluidas"):
                if k in val:
                    ids = _ids(val[k])
                    if ids is not None:
                        atual[k] = ids
            atual = {k: v for k, v in atual.items() if v}
            if atual:
                cp[pid] = atual
            else:
                cp.pop(pid, None)
        cfg["ctx_projeto"] = cp
    return cfg, avisos


def restaurar_secao(cfg: dict, secao: str):
    """Devolve a seção aos valores de fábrica. None se a seção não existe."""
    if secao not in SECOES:
        return None
    for k in SECOES[secao]:
        cfg[k] = json.loads(json.dumps(core.CONFIG_PADRAO[k]))
    return cfg


# ── valores efetivos ─────────────────────────────────────────────────────────
def perfil_efetivo(cfg: dict, nome_modelo: str) -> dict:
    """Parâmetros finais de geração: perfil do modelo > geração global > padrão do catálogo."""
    m = core.info_modelo(nome_modelo)
    p = (cfg.get("perfis_modelo") or {}).get(nome_modelo) or {}
    g = cfg.get("geracao") or {}
    teto = _limites_modelo(nome_modelo)
    ctx = p.get("num_ctx") or cfg.get("num_ctx") or LIM_NUM_CTX[2]
    ctx = max(LIM_NUM_CTX[0], min(teto, int(ctx)))
    def pick(k, padrao=None):
        if p.get(k) is not None:
            return p[k]
        if g.get(k) is not None:
            return g[k]
        return padrao
    return {"num_ctx": ctx, "temperatura": pick("temperatura", m.get("temp", 0.7)),
            "top_p": pick("top_p"), "max_tokens": pick("max_tokens"), "seed": pick("seed")}
