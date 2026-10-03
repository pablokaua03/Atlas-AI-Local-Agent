"""
Testes da lógica de contexto do chat, orçamento, configurações validadas, catálogo de modelos
e fatos duráveis. Isolados em pasta temporária e sem Ollama:
    python -m unittest discover -s tests
"""
import os
import sys
import json
import time
import urllib.parse
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_memapi as tm  # noqa: E402
import core               # noqa: E402
import ajustes            # noqa: E402
import contexto           # noqa: E402
import chats              # noqa: E402
import skills             # noqa: E402
import memstore           # noqa: E402
import server             # noqa: E402


def mem(i, texto, score=0.5, imp=3, dias=0, proj="p", dist=0):
    t = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - dias * 86400))
    return {"id": i, "content": texto, "project": proj, "type": "note", "importance": imp,
            "source": "api", "updated": t, "score": score, "distance": dist, "inherited": dist > 0}


class TestPuros(unittest.TestCase):
    def test_estimar_tokens(self):
        self.assertEqual(contexto.estimar_tokens(""), 1)
        self.assertGreater(contexto.estimar_tokens("a" * 350), 90)

    def test_orcamento_escala_com_janela(self):
        cfg = {"contexto": {"ctx_pct": 35}}
        pequeno = contexto.orcamento_chars(cfg, {"num_ctx": 2048})
        grande = contexto.orcamento_chars(cfg, {"num_ctx": 16384})
        self.assertGreater(grande, pequeno * 4)
        self.assertGreaterEqual(pequeno, contexto.MIN_ORCAMENTO)

    def test_orcamento_respeita_percentual_e_saida(self):
        base = {"num_ctx": 8192}
        a = contexto.orcamento_chars({"contexto": {"ctx_pct": 20}}, base)
        b = contexto.orcamento_chars({"contexto": {"ctx_pct": 60}}, base)
        self.assertGreater(b, a * 2)
        c = contexto.orcamento_chars({"contexto": {"ctx_pct": 60}}, {"num_ctx": 8192, "max_tokens": 6000})
        self.assertLess(c, b)

    def test_recencia_meia_vida(self):
        agora = time.time()
        novo = contexto.recencia(mem("a", "x", dias=0)["updated"], 30, agora)
        velho = contexto.recencia(mem("a", "x", dias=30)["updated"], 30, agora)
        self.assertAlmostEqual(novo, 1.0, places=2)
        self.assertAlmostEqual(velho, 0.5, places=2)
        self.assertLess(contexto.recencia("lixo", 30, agora), 0.01)

    def test_pontuacao_pesa_importancia_e_recencia(self):
        agora = time.time()
        base = contexto.pontuar(mem("a", "x", imp=3, dias=0), 0.5, 30, agora)
        self.assertGreater(contexto.pontuar(mem("a", "x", imp=5, dias=0), 0.5, 30, agora), base)
        self.assertLess(contexto.pontuar(mem("a", "x", imp=3, dias=200), 0.5, 30, agora), base)
        self.assertLess(contexto.pontuar(mem("a", "x", dist=2), 0.5, 30, agora), base)    # herdada pesa menos

    def test_sensivel(self):
        for t in ["minha senha é abc12345", "token: ghp_abcdefghijklmnopqrstuvwxyz0123", "api key = sk-abcdefghijklmnop1234",
                  "cartão 4111 1111 1111 1111", "meu cpf 123.456.789-09", "-----BEGIN RSA PRIVATE KEY-----",
                  "AKIAABCDEFGHIJKLMNOP", "password: hunter22", "usa eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc"]:
            self.assertTrue(contexto.sensivel(t), t)
        for t in ["O usuário gosta de programar em Rust.", "Reunião às 15:30 na sexta", "a senha deve ser trocada todo mês",
                  "O usuário é dentista e joga xadrez."]:
            self.assertFalse(contexto.sensivel(t), t)

    def test_parecido_e_deduplicar(self):
        self.assertTrue(contexto.parecido("O usuário gosta de café!", "o usuario gosta de cafe"))
        self.assertTrue(contexto.parecido("O usuário gosta muito de café preto", "O usuário gosta muito de café preto sim"))
        self.assertFalse(contexto.parecido("gosta de café", "trabalha com Python"))
        self.assertEqual(contexto.deduplicar(["a b c d", "A b c d.", "outra coisa aqui"]), ["a b c d", "outra coisa aqui"])

    def test_regras_conversa_vence_projeto(self):
        cfg = {"ctx_projeto": {"p": {"fixas": ["m1", "m2"], "excluidas": ["m3"]}}}
        fix, exc = contexto.regras(cfg, {"ctx_excluidas": ["m1"], "ctx_fixas": ["m3"]}, "p")
        self.assertEqual(fix, {"m2", "m3"})
        self.assertEqual(exc, {"m1"})
        fix, exc = contexto.regras(cfg, {}, None)           # sem projeto: regras do projeto não valem
        self.assertEqual((fix, exc), (set(), set()))

    def test_cortar_e_ajustar_secoes(self):
        self.assertEqual(contexto.cortar("abc", 10), "abc")
        c = contexto.cortar("linha um.\nlinha dois muito longa " * 20, 100)
        self.assertLessEqual(len(c), 100)
        self.assertTrue(c.endswith("…"))
        secoes = [("a", "A:", "x" * 1000), ("b", "B:", "y" * 1000), ("c", "C:", "z" * 1000)]
        txt, uso = contexto.ajustar_secoes(secoes, 900)
        self.assertLessEqual(len(txt), 900)
        self.assertEqual(list(uso), ["a", "b", "c"])
        self.assertGreater(uso["a"], uso["b"])                # a 1ª tem prioridade
        txt, uso = contexto.ajustar_secoes([("a", "A:", "  "), ("b", "B:", "ok")], 500)
        self.assertEqual(list(uso), ["b"])

    def test_filtro_pensamento(self):
        f = contexto.FiltroPensamento()
        out = "".join(f.push(p) for p in ["Oi <th", "ink>pensando", " muito</thi", "nk>\nResposta <b>ok</b> 1<2"]) + f.fim()
        self.assertEqual(out, "Oi Resposta <b>ok</b> 1<2")
        f = contexto.FiltroPensamento()
        self.assertEqual(f.push("sem tags") + f.fim(), "sem tags")
        f = contexto.FiltroPensamento()
        self.assertEqual(f.push("a<think>nunca fecha") + f.fim(), "a")

    def test_instrucoes_texto(self):
        cfg = {"instrucoes": {"preset": "conciso", "extra": "Chame-me de chefe.",
                              "personalizados": [{"id": "x1", "nome": "X", "texto": "Fale como pirata."}]},
               "instrucoes_projeto": {"p": "Foque em Rust."}}
        t = contexto.instrucoes_texto(cfg, "pt", "p")
        self.assertIn("conciso", t.lower())
        self.assertIn("chefe", t)
        self.assertIn("Rust", t)
        self.assertIn("concise", contexto.instrucoes_texto(cfg, "en", None).lower())
        cfg["instrucoes"]["preset"] = "x1"
        self.assertIn("pirata", contexto.instrucoes_texto(cfg, "pt"))
        self.assertEqual(contexto.instrucoes_texto({"instrucoes": {"preset": "padrao"}}, "pt"), "")


class TestSelecao(unittest.TestCase):
    def cfg(self, **c):
        return {"contexto": {"max_memorias": 3, "recencia_dias": 30, **c}}

    def sel(self, itens, cfg=None, chat=None, projeto=None, orc=5000, listar=None, texto="consulta"):
        banco = {m["id"]: m for m in itens}
        return contexto.selecionar_memorias(
            texto, projeto, cfg or self.cfg(), chat or {}, orc,
            buscar=lambda q, p, n: list(itens), obter=lambda i: banco[i],
            listar=listar or (lambda p, n: []))

    def test_ordena_por_relevancia_importancia_recencia(self):
        itens = [mem("velha", "texto antigo sobre python", score=0.5, imp=3, dias=300),
                 mem("nova", "texto recente sobre rust", score=0.5, imp=3, dias=1),
                 mem("imp", "texto importante sobre java", score=0.5, imp=5, dias=100)]
        ids = [u["id"] for u in self.sel(itens)]
        self.assertEqual(ids[0], "nova")
        self.assertEqual(set(ids), {"velha", "nova", "imp"})
        self.assertLess(ids.index("imp"), ids.index("velha"))

    def test_limite_max_memorias(self):
        itens = [mem(f"m{i}", f"conteudo distinto numero {i} alfa{i}") for i in range(10)]
        self.assertEqual(len(self.sel(itens)), 3)
        self.assertEqual(self.sel(itens, cfg=self.cfg(max_memorias=0)), [])

    def test_deduplica(self):
        itens = [mem("a", "O usuário gosta de café preto"), mem("b", "o usuario gosta de cafe preto!"),
                 mem("c", "Reunião semanal toda sexta")]
        self.assertEqual([u["id"] for u in self.sel(itens)], ["a", "c"])

    def test_fixadas_entram_primeiro_e_excluidas_saem(self):
        itens = [mem("a", "alfa um"), mem("b", "beta dois"), mem("c", "gama três"), mem("fix", "fixada sem relevância", score=0)]
        chat = {"ctx_fixas": ["fix"], "ctx_excluidas": ["a"]}
        u = self.sel(itens, chat=chat)
        self.assertEqual(u[0]["id"], "fix")
        self.assertTrue(u[0]["pinned"])
        self.assertNotIn("a", [x["id"] for x in u])
        self.assertEqual(len(u), 3)

    def test_regras_do_projeto(self):
        cfg = self.cfg()
        cfg["ctx_projeto"] = {"p": {"fixas": ["b"], "excluidas": ["a"]}}
        itens = [mem("a", "alfa um"), mem("b", "beta dois", score=0)]
        self.assertEqual([x["id"] for x in self.sel(itens, cfg=cfg, projeto="p")], ["b"])
        self.assertEqual({x["id"] for x in self.sel(itens, cfg=cfg, projeto=None)}, {"a", "b"})

    def test_orcamento_pequeno_corta(self):
        itens = [mem(f"m{i}", ("palavra%d " % i) * 40) for i in range(5)]
        u = self.sel(itens, cfg=self.cfg(max_memorias=5), orc=900)
        self.assertGreaterEqual(len(u), 1)
        self.assertLess(len(u), 5)

    def test_projeto_ativo_completa_com_importantes(self):
        imp = mem("imp", "decisão central do projeto", imp=5)
        u = self.sel([], projeto="p", listar=lambda p, n: [imp, mem("baixa", "detalhe", imp=2)])
        self.assertEqual([x["id"] for x in u], ["imp"])
        self.assertEqual(u[0]["why"], "projeto")

    def test_erros_viram_lista_vazia(self):
        def quebra(*a):
            raise RuntimeError("cofre travado")
        self.assertEqual(contexto.selecionar_memorias("x", None, self.cfg(), {}, 1000, buscar=quebra,
                                                      obter=quebra, listar=quebra), [])


class TestConfigValidada(unittest.TestCase):
    def cfg(self):
        return json.loads(json.dumps(core.CONFIG_PADRAO))

    def test_clamp_geracao(self):
        c, av = ajustes.aplicar(self.cfg(), {"geracao": {"temperatura": 9, "top_p": 0, "max_tokens": 1, "seed": -5}})
        g = c["geracao"]
        self.assertEqual((g["temperatura"], g["top_p"], g["max_tokens"], g["seed"]), (2.0, 0.05, 16, 0))
        c, av = ajustes.aplicar(c, {"geracao": {"seed": "", "temperatura": "abc", "max_tokens": 99999}})
        self.assertIsNone(c["geracao"]["seed"])
        self.assertEqual(c["geracao"]["temperatura"], 2.0)             # inválido: mantém
        self.assertEqual(c["geracao"]["max_tokens"], 8192)
        self.assertTrue(any("temperatura" in a for a in av))

    def test_numeros_estranhos(self):
        c, _ = ajustes.aplicar(self.cfg(), {"geracao": {"temperatura": float("nan"), "top_p": True}, "num_ctx": "1e999"})
        self.assertEqual(c["geracao"]["temperatura"], core.CONFIG_PADRAO["geracao"]["temperatura"])
        self.assertEqual(c["num_ctx"], core.CONFIG_PADRAO["num_ctx"])

    def test_clamp_contexto(self):
        c, av = ajustes.aplicar(self.cfg(), {"contexto": {"ctx_pct": 99, "max_memorias": -3, "recencia_dias": 0,
                                                          "hist_msgs": 100, "fatos_modo": "hack", "incluir_conversas": 0}})
        k = c["contexto"]
        self.assertEqual((k["ctx_pct"], k["max_memorias"], k["recencia_dias"], k["hist_msgs"]), (70, 0, 1, 20))
        self.assertEqual(k["fatos_modo"], "perguntar")
        self.assertFalse(k["incluir_conversas"])
        self.assertTrue(any("fatos_modo" in a for a in av))

    def test_modelo_so_do_catalogo(self):
        c, av = ajustes.aplicar(self.cfg(), {"modelo": "nome-inventado:1b", "embed": "qwen2.5:3b"})
        self.assertEqual(c["modelo"], core.CONFIG_PADRAO["modelo"])
        self.assertEqual(len(av), 2)
        c, _ = ajustes.aplicar(c, {"modelo": "qwen2.5:3b", "embed": "nomic-embed-text"})
        self.assertEqual((c["modelo"], c["embed"]), ("qwen2.5:3b", "nomic-embed-text"))
        c, av = ajustes.aplicar(c, {"modelo": "nomic-embed-text"})          # embedding não é modelo de chat
        self.assertEqual(c["modelo"], "qwen2.5:3b")

    def test_perfil_por_modelo(self):
        c, _ = ajustes.aplicar(self.cfg(), {"perfis_modelo": {"llama3.2:1b": {"num_ctx": 999999, "temperatura": 0.2},
                                                               "fantasma:1b": {"num_ctx": 2048}}})
        p = c["perfis_modelo"]["llama3.2:1b"]
        self.assertEqual(p["num_ctx"], min(ajustes.LIM_NUM_CTX[1], core.info_modelo("llama3.2:1b")["ctx_max"]))
        self.assertNotIn("fantasma:1b", c["perfis_modelo"])
        ef = ajustes.perfil_efetivo(c, "llama3.2:1b")
        self.assertEqual(ef["temperatura"], 0.2)
        c, _ = ajustes.aplicar(c, {"perfis_modelo": {"llama3.2:1b": None}})
        self.assertEqual(c["perfis_modelo"], {})

    def test_perfil_efetivo_precedencia(self):
        c = self.cfg()
        c["num_ctx"] = 8192
        c["geracao"]["top_p"] = 0.5
        ef = ajustes.perfil_efetivo(c, "qwen2.5:3b")
        self.assertEqual((ef["num_ctx"], ef["top_p"]), (8192, 0.5))
        self.assertEqual(ef["temperatura"], core.info_modelo("qwen2.5:3b")["temp"])    # padrão do catálogo
        c["perfis_modelo"] = {"qwen2.5:3b": {"temperatura": 1.1, "top_p": 0.9}}
        ef = ajustes.perfil_efetivo(c, "qwen2.5:3b")
        self.assertEqual((ef["temperatura"], ef["top_p"]), (1.1, 0.9))
        c["num_ctx"] = 999999                                  # nunca passa do máximo do modelo
        self.assertLessEqual(ajustes.perfil_efetivo(c, "qwen2.5:0.5b")["num_ctx"], core.info_modelo("qwen2.5:0.5b")["ctx_max"])

    def test_presets_personalizados(self):
        lista = [{"id": "meu", "nome": "  Meu  ", "texto": "x" * 5000}, {"nome": "", "texto": "vazio"}, "lixo",
                 {"id": "conciso", "nome": "Colide", "texto": "t"}]
        c, _ = ajustes.aplicar(self.cfg(), {"instrucoes": {"personalizados": lista, "preset": "meu", "extra": "e" * 5000}})
        ps = c["instrucoes"]["personalizados"]
        self.assertEqual(len(ps), 2)
        self.assertEqual(ps[0]["nome"], "Meu")
        self.assertEqual(len(ps[0]["texto"]), ajustes.MAX_EXTRA)
        self.assertNotIn(ps[1]["id"], ajustes.PRESETS)           # id que colide com preset de fábrica é trocado
        self.assertEqual(c["instrucoes"]["preset"], "meu")
        c, _ = ajustes.aplicar(c, {"instrucoes": {"personalizados": []}})
        self.assertEqual(c["instrucoes"]["preset"], "padrao")    # preset apagado volta ao padrão
        c, _ = ajustes.aplicar(c, {"instrucoes": {"personalizados": [{"nome": f"p{i}", "texto": "t"} for i in range(30)]}})
        self.assertEqual(len(c["instrucoes"]["personalizados"]), ajustes.MAX_PRESETS)

    def test_instrucoes_e_ctx_por_projeto(self):
        c, _ = ajustes.aplicar(self.cfg(), {"instrucoes_projeto": {"proj": "Fale de Rust", "../x": "no", "vazio": ""},
                                            "ctx_projeto": {"proj": {"fixas": ["a", "a", "b/../c", 5], "excluidas": ["z"]}}})
        self.assertEqual(c["instrucoes_projeto"], {"proj": "Fale de Rust"})
        self.assertEqual(c["ctx_projeto"], {"proj": {"fixas": ["a"], "excluidas": ["z"]}})
        c, _ = ajustes.aplicar(c, {"instrucoes_projeto": {"proj": ""}, "ctx_projeto": {"proj": {"fixas": [], "excluidas": []}}})
        self.assertEqual((c["instrucoes_projeto"], c["ctx_projeto"]), ({}, {}))

    def test_restaurar_secao(self):
        c = self.cfg()
        c["api_token"] = "segredo-de-teste"
        c["geracao"]["temperatura"] = 1.9
        c["contexto"]["max_memorias"] = 1
        c["tema"] = "claro"
        c["api_ativa"] = False
        ajustes.restaurar_secao(c, "geracao")
        self.assertEqual(c["geracao"], core.CONFIG_PADRAO["geracao"])
        self.assertEqual(c["contexto"]["max_memorias"], 1)               # outra seção intacta
        ajustes.restaurar_secao(c, "geral")
        self.assertEqual(c["tema"], core.CONFIG_PADRAO["tema"])
        ajustes.restaurar_secao(c, "privacidade")
        self.assertTrue(c["api_ativa"])
        self.assertEqual(c["api_token"], "segredo-de-teste")             # token nunca é tocado
        self.assertIsNone(ajustes.restaurar_secao(c, "api_token"))
        # restaurar não deixa referência compartilhada com o padrão
        c["geracao"]["top_p"] = 0.1
        self.assertNotEqual(core.CONFIG_PADRAO["geracao"]["top_p"], 0.1)

    def test_secoes_so_tem_chaves_conhecidas(self):
        for secao, chaves in ajustes.SECOES.items():
            for k in chaves:
                self.assertIn(k, core.CONFIG_PADRAO, f"{secao}.{k}")
            self.assertFalse({"api_token", "cripto_salt", "cripto_verif"} & set(chaves))


class TestCatalogo(unittest.TestCase):
    def test_campos_e_usos(self):
        usos = set()
        for m in core.CATALOGO_MODELOS:
            for k in ("nome", "rotulo", "usos", "gb", "ram", "ctx", "ctx_max", "temp", "tipo"):
                self.assertIn(k, m, m["nome"])
            self.assertLessEqual(m["ctx"], m["ctx_max"])
            self.assertGreater(m["gb"], 0)
            usos.update(m["usos"])
        self.assertTrue({"geral", "codigo", "raciocinio", "visao", "leve", "embed"} <= usos)
        nomes = [m["nome"] for m in core.CATALOGO_MODELOS]
        self.assertEqual(len(nomes), len(set(nomes)))

    def test_chat_vs_embed(self):
        self.assertTrue(all(m["tipo"] == "chat" for m in core.modelos_chat()))
        self.assertTrue(all(m["tipo"] == "embed" for m in core.modelos_embed()))
        self.assertIn(core.CONFIG_PADRAO["modelo"], [m["nome"] for m in core.modelos_chat()])
        self.assertIn(core.CONFIG_PADRAO["embed"], [m["nome"] for m in core.modelos_embed()])
        self.assertEqual(core.info_modelo("nao-existe"), {})

    def test_provedor_padrao_ollama_sem_chaves(self):
        self.assertIn("ollama", core.PROVEDORES)
        self.assertNotIn("api_key", json.dumps(core.PROVEDORES).lower())


class TestIntegracao(tm.Base):
    def setUp(self):
        super().setUp()
        self._ol = (core.ollama_online, core.achar_ollama, server.requests.post, server.requests.get)
        core.ollama_online = lambda: False
        core.achar_ollama = lambda: ""
        self.enviado = []

        class R:
            def __init__(s, linhas):
                s.linhas = linhas

            def iter_lines(s):
                return iter(s.linhas)

        def post(url, json=None, **kw):                  # Ollama falso
            self.enviado.append(json)
            return R([b'{"message":{"content":"Ola "}}', b'{"message":{"content":"<think>x</think>mundo"}}',
                      b'{"done":true}'])
        server.requests.post = post
        # sem fatos automáticos nos testes
        self._pos = skills.extrair_fato
        skills.extrair_fato = lambda *a, **k: None
        self.cfg = core.carregar_config()
        self.cfg["habilidades"]["memoria"] = True
        core.salvar_config(self.cfg)

    def tearDown(self):
        core.ollama_online, core.achar_ollama, server.requests.post, server.requests.get = self._ol
        skills.extrair_fato = self._pos
        super().tearDown()

    def conversar(self, texto, cid=None):
        r = self.c.post("/chat", json={"texto": texto, "chat_id": cid or chats.listar()["atual"]})
        corpo = r.get_data(as_text=True)
        h = r.headers.get("X-Atlas-Contexto")
        return corpo, (json.loads(urllib.parse.unquote(h)) if h else None)

    def test_retrocompat_config_antigo(self):
        antigo = {"modelo": "qwen2.5:3b", "num_ctx": 4096, "tema": "claro", "habilidades": {"memoria": True}}
        with open(core.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(antigo, f)
        cfg = core.carregar_config()
        self.assertEqual(cfg["geracao"], core.CONFIG_PADRAO["geracao"])
        self.assertEqual(cfg["contexto"]["fatos_modo"], "perguntar")
        self.assertEqual(cfg["tema"], "claro")
        d = self.c.get("/api/estado").get_json()
        self.assertTrue(d["catalogo"])
        self.assertIn("presets", d["ajustes"])

    def test_config_modelo_invalido_nao_grava(self):
        d = self.c.post("/api/config", json={"modelo": "xyz:9b", "geracao": {"temperatura": 7}}).get_json()
        self.assertEqual(d["config"]["modelo"], core.CONFIG_PADRAO["modelo"])
        self.assertEqual(d["config"]["geracao"]["temperatura"], 2.0)
        self.assertTrue(d["avisos"])

    def test_restaurar_endpoint(self):
        self.c.post("/api/config", json={"geracao": {"temperatura": 1.7}, "tema": "claro"})
        r = self.c.post("/api/config/restaurar", json={"secao": "geracao"})
        self.assertEqual(r.get_json()["config"]["geracao"], core.CONFIG_PADRAO["geracao"])
        self.assertEqual(r.get_json()["config"]["tema"], "claro")
        self.assertEqual(self.c.post("/api/config/restaurar", json={"secao": "api_token"}).status_code, 400)
        self.assertTrue(memapi_token_ok())

    def test_chat_usa_memorias_cita_e_manda_opcoes(self):
        self.api("POST", "/v1/projects", {"name": "Foguete"})
        _, m1 = self.api("POST", "/v1/memories", {"content": "O foguete usa combustível metano líquido", "project": "foguete", "importance": 5})
        self.api("POST", "/v1/memories", {"content": "Receita de bolo de cenoura", "project": "foguete"})
        cid = chats.listar()["atual"]
        chats.definir_ctx(cid, projeto="foguete")
        core.salvar_config({**core.carregar_config(), "geracao": {"temperatura": 0.3, "top_p": 0.8, "max_tokens": 200, "seed": 7}})
        corpo, info = self.conversar("qual combustível o foguete usa?", cid)
        self.assertEqual(corpo.strip(), "Ola mundo")                   # <think> filtrado
        self.assertEqual(info["projeto"], "foguete")
        self.assertEqual(info["mems"][0]["id"], m1["id"])
        self.assertNotIn("bolo", json.dumps(info["mems"]))
        corpo_ollama = self.enviado[-1]
        self.assertIn("metano", corpo_ollama["messages"][0]["content"])
        o = corpo_ollama["options"]
        self.assertEqual((o["temperature"], o["top_p"], o["num_predict"], o["seed"]), (0.3, 0.8, 200, 7))
        self.assertEqual(chats.get(cid)["mensagens"][-1]["ctx"][0]["id"], m1["id"])   # fonte guardada no chat

    def test_fixar_e_excluir_por_conversa_e_projeto(self):
        self.api("POST", "/v1/projects", {"name": "Foguete"})
        _, a = self.api("POST", "/v1/memories", {"content": "Foguete usa metano", "project": "foguete"})
        _, b = self.api("POST", "/v1/memories", {"content": "Assunto sem relação alguma", "project": "foguete"})
        cid = chats.listar()["atual"]
        r = self.c.post(f"/api/chats/{cid}/ctx", json={"projeto": "foguete", "acao": "excluir", "id": a["id"]}).get_json()
        self.assertEqual(r["conversa"]["excluidas"][0]["id"], a["id"])
        _, info = self.conversar("foguete metano", cid)
        self.assertNotIn(a["id"], [m["id"] for m in info["mems"]])
        self.c.post(f"/api/chats/{cid}/ctx", json={"acao": "fixar", "id": b["id"]})
        _, info = self.conversar("foguete metano", cid)
        self.assertEqual(info["mems"][0]["id"], b["id"])
        self.assertTrue(info["mems"][0]["f"])
        # escopo projeto: vale para outras conversas do mesmo projeto
        r = self.c.post(f"/api/chats/{cid}/ctx", json={"acao": "limpar", "id": a["id"]}).get_json()
        self.assertEqual(r["conversa"]["excluidas"], [])
        r = self.c.post(f"/api/chats/{cid}/ctx", json={"acao": "excluir", "id": a["id"], "escopo": "projeto"}).get_json()
        self.assertEqual(r["projeto_regras"]["excluidas"][0]["id"], a["id"])
        outro = chats.novo()
        chats.definir_ctx(outro, projeto="foguete")
        _, info = self.conversar("foguete metano", outro)
        self.assertNotIn(a["id"], [m["id"] for m in info["mems"]])
        # sem projeto ativo, escopo projeto é recusado
        sem = chats.novo()
        st = self.c.post(f"/api/chats/{sem}/ctx", json={"acao": "fixar", "id": a["id"], "escopo": "projeto"}).status_code
        self.assertEqual(st, 400)
        self.assertEqual(self.c.get("/api/chats/inexistente/ctx").status_code, 404)

    def test_memoria_sensivel_nunca_vai_ao_prompt(self):
        self.api("POST", "/v1/memories", {"content": "senha do banco: Xyz12345 do projeto foguete"})
        self.api("POST", "/v1/memories", {"content": "foguete tem três estágios"})
        self.conversar("foguete senha estágios")
        sistema = self.enviado[-1]["messages"][0]["content"]
        self.assertNotIn("Xyz12345", sistema)
        self.assertIn("três estágios", sistema)

    def test_historico_respeita_hist_msgs(self):
        cid = chats.listar()["atual"]
        for i in range(5):
            chats.adicionar(cid, f"pergunta{i}", f"resposta{i}")
        core.salvar_config({**core.carregar_config(), "contexto": {**core.carregar_config()["contexto"], "hist_msgs": 2}})
        self.conversar("oi de novo", cid)
        n = len(self.enviado[-1]["messages"])
        self.assertEqual(n, 1 + 4 + 1)

    def test_instrucoes_no_system(self):
        self.c.post("/api/config", json={"instrucoes": {"preset": "conciso", "extra": "Trate-me por capitão."}})
        self.conversar("olá")
        s = self.enviado[-1]["messages"][0]["content"]
        self.assertIn("capitão", s)
        self.assertIn("conciso", s.lower())

    def test_fatos_sensiveis_recusados(self):
        for t in ["minha senha é abc12345", "token: ghp_abcdefghijklmnopqrstuvwxyz0123", "curto", "x" * 200]:
            r = self.c.post("/api/fatos/confirmar", json={"texto": t})
            self.assertEqual(r.status_code, 400, t)
        r = self.c.post("/api/fatos/confirmar", json={"texto": "O usuário gosta de xadrez."}).get_json()
        self.assertTrue(r["novo"])
        r = self.c.post("/api/fatos/confirmar", json={"texto": "o usuario gosta de xadrez"}).get_json()
        self.assertFalse(r["novo"])                                  # duplicata
        self.assertIn("O usuário gosta de xadrez.", skills.carregar_mem()["fatos"])
        self.assertEqual(self.c.post("/api/fatos/confirmar", json={"texto": 5}).status_code, 400)

    def test_extrair_fato_respeita_modo(self):
        skills.extrair_fato = self._pos
        eventos = []
        orig = (skills.emitir, skills.propor_fato)
        skills.emitir = lambda t, p=None: eventos.append((t, p))
        skills.propor_fato = lambda msg: "O usuário gosta de jazz."
        try:
            cfg = core.carregar_config()
            cfg["contexto"]["fatos_modo"] = "perguntar"
            core.salvar_config(cfg)
            skills.extrair_fato("eu gosto muito de jazz clássico", "ok")
            self.assertEqual(eventos[-1][0], "sugestao_fato")
            self.assertNotIn("O usuário gosta de jazz.", skills.carregar_mem()["fatos"])
            cfg["contexto"]["fatos_modo"] = "automatico"
            core.salvar_config(cfg)
            skills.extrair_fato("eu gosto muito de jazz clássico", "ok")
            self.assertIn("O usuário gosta de jazz.", skills.carregar_mem()["fatos"])
            cfg["contexto"]["fatos_modo"] = "desligado"
            skills.propor_fato = lambda msg: "O usuário gosta de rock."
            core.salvar_config(cfg)
            skills.extrair_fato("eu gosto muito de rock pesado", "ok")
            self.assertNotIn("O usuário gosta de rock.", skills.carregar_mem()["fatos"])
        finally:
            skills.emitir, skills.propor_fato = orig

    def test_propor_fato_nao_envia_sensivel_ao_modelo(self):
        chamadas = []
        orig = skills._chat
        skills._chat = lambda *a, **k: chamadas.append(a) or "O usuário usa a senha Abc12345 sempre."
        try:
            self.assertIsNone(skills.propor_fato("minha senha é abc12345 e uso sempre"))
            self.assertEqual(chamadas, [])
            self.assertIsNone(skills.propor_fato("eu uso a mesma coisa sempre para tudo"))   # o modelo devolveu algo sensível
        finally:
            skills._chat = orig

    def test_projetos_lista_leve(self):
        self.api("POST", "/v1/projects", {"name": "Alfa"})
        d = self.c.get("/api/projetos").get_json()
        alfa = [p for p in d if p["name"] == "Alfa"][0]
        self.assertEqual(set(alfa), {"id", "name", "parent", "depth"})


def memapi_token_ok():
    import memapi
    return bool(memapi.token())


if __name__ == "__main__":
    unittest.main()
