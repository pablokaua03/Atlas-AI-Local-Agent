"""
Testes das ferramentas do modelo (tool calling no chat), do desfazer, da detecção de hardware
e das recomendações de modelo. Isolados em pasta temporária e com um Ollama falso:
    python -m pytest -q
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_memapi as tm   # noqa: E402
import test_upgrade as tu  # noqa: E402
import core                # noqa: E402
import chats               # noqa: E402
import docs                # noqa: E402
import skills              # noqa: E402
import server              # noqa: E402
import ajustes             # noqa: E402
import memstore            # noqa: E402
import ferramentas         # noqa: E402

M = ferramentas.MARCA


def _linha(d):
    return json.dumps(d).encode()


def chamada(nome, args):
    return _linha({"message": {"role": "assistant", "content": "",
                               "tool_calls": [{"function": {"name": nome, "arguments": args}}]}, "done": True})


def texto(*partes):
    return [_linha({"message": {"content": p}}) for p in partes] + [_linha({"done": True})]


class FakeOllama(tm.Base):
    """Ollama falso: cada POST /api/chat consome o próximo roteiro da fila."""

    def setUp(self):
        super().setUp()
        self._ol = (core.ollama_online, server.requests.post, skills.extrair_fato, skills.tecer, docs.DOCS_DIR)
        core.ollama_online = lambda: False          # sem /api/show: o catálogo decide o suporte
        skills.extrair_fato = lambda *a, **k: None
        skills.tecer = lambda *a, **k: None
        docs.DOCS_DIR = os.path.join(self.dir, "docs")
        self.roteiros, self.enviado = [], []

        def post(url, json=None, **kw):
            self.enviado.append(json)
            linhas = self.roteiros.pop(0) if self.roteiros else texto("fim")
            return tu.FakeResp(linhas)
        server.requests.post = post
        core.salvar_config({**core.carregar_config(), "modelo": "qwen3.5:4b"})
        self.cid = chats.listar()["atual"]

    def tearDown(self):
        core.ollama_online, server.requests.post, skills.extrair_fato, skills.tecer, docs.DOCS_DIR = self._ol
        super().tearDown()

    def conversar(self, msg):
        corpo = self.c.post("/chat", json={"texto": msg, "chat_id": self.cid}).get_data(as_text=True)
        eventos = [json.loads(x) for x in corpo.split(M)[1::2]]
        limpo = "".join(corpo.split(M)[0::2])
        return limpo, eventos


class TestLoopFerramentas(FakeOllama):
    def test_salva_memoria_pela_ferramenta_e_responde(self):
        self.roteiros = [[chamada("save_memory", {"content": "Meu time do coração é o Palmeiras.", "type": "preference",
                                                   "entities": ["Palmeiras"]})],
                         texto("Anotado, ", "Palmeiras!")]
        limpo, eventos = self.conversar("guarda isso: meu time é o Palmeiras")
        self.assertEqual(limpo, "Anotado, Palmeiras!")
        self.assertEqual(len(eventos), 1)
        ev = eventos[0]
        self.assertTrue(ev["ok"])
        self.assertEqual(ev["tool"], "save_memory")
        self.assertTrue(ev["acao"].startswith("act_"))
        # primeira chamada levou as ferramentas; a segunda leva o resultado como mensagem "tool"
        self.assertIn("tools", self.enviado[0])
        nomes = {t["function"]["name"] for t in self.enviado[0]["tools"]}
        self.assertTrue({"search_memories", "save_memory", "link_concepts", "recall_conversation"} <= nomes)
        tool_msg = self.enviado[1]["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertEqual(tool_msg["tool_name"], "save_memory")
        self.assertIn("mem_", tool_msg["content"])
        # gravou pelo mesmo caminho da interface (memstore), com a origem do chat
        itens = memstore.memorias_listar()["items"]
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["source"], "atlas-chat:qwen3.5:4b")
        self.assertEqual(itens[0]["type"], "preference")
        self.assertIn("palmeiras", itens[0]["entities"])
        # histórico guarda as ferramentas usadas (para reabrir a conversa)
        m = chats.get(self.cid)["mensagens"][-1]
        self.assertEqual(m["a"], "Anotado, Palmeiras!")
        self.assertEqual(m["ferramentas"][0]["tool"], "save_memory")
        # e a ação aparece no registro
        acoes = self.c.get("/api/ferramentas").get_json()["acoes"]
        self.assertEqual(acoes[0]["id"], ev["acao"])
        self.assertNotIn("mem_antes", acoes[0])

    def test_busca_e_resposta_em_paragrafo_novo(self):
        memstore.memoria_criar({"content": "Decidimos usar MySQL 8 no Aurora.", "type": "decision"})
        self.roteiros = [[_linha({"message": {"content": "Vou conferir."}})] + [chamada("search_memories", {"query": "Aurora banco"})],
                         texto("Vocês escolheram MySQL 8.")]
        limpo, eventos = self.conversar("qual banco decidimos no Aurora?")
        self.assertEqual(limpo, "Vou conferir.\n\nVocês escolheram MySQL 8.")
        self.assertEqual(eventos[0]["resumo"], "1 memória(s)")
        self.assertIn("MySQL 8", self.enviado[1]["messages"][-1]["content"])
        self.assertEqual(self.enviado[1]["messages"][-2]["role"], "assistant")

    def test_limite_de_rodadas_forca_resposta_sem_ferramentas(self):
        cfg = core.carregar_config()
        cfg["ferramentas"]["max_rodadas"] = 2
        core.salvar_config(cfg)
        self.roteiros = [[chamada("list_projects", {})], [chamada("list_projects", {})], texto("Pronto.")]
        limpo, eventos = self.conversar("liste meus projetos")
        self.assertEqual(limpo, "Pronto.")
        self.assertEqual(len(eventos), 2)
        self.assertEqual(len(self.enviado), 3)
        self.assertNotIn("tools", self.enviado[2])

    def test_ferramenta_desconhecida_e_argumentos_ruins_nao_quebram(self):
        self.roteiros = [[_linha({"message": {"tool_calls": [{"function": {"name": "rm_rf", "arguments": {}}},
                                                             {"function": {"name": "update_memory", "arguments": "{nao json"}}]},
                                  "done": True})],
                         texto("Ok.")]
        limpo, eventos = self.conversar("faz algo")
        self.assertEqual(limpo, "Ok.")
        self.assertFalse(eventos[0]["ok"])
        self.assertFalse(eventos[1]["ok"])
        self.assertIn("error", self.enviado[1]["messages"][-1]["content"])

    def test_recusa_dado_sensivel(self):
        self.roteiros = [[chamada("save_memory", {"content": "minha senha do banco: Xy7!kq99"})], texto("Não guardo senhas.")]
        _, eventos = self.conversar("guarda minha senha do banco: Xy7!kq99")
        self.assertFalse(eventos[0]["ok"])
        self.assertIn("refused", eventos[0]["erro"])
        self.assertEqual(memstore.memorias_listar()["total"], 0)

    def test_modelo_sem_ferramentas_mantem_injecao_de_contexto(self):
        memstore.memoria_criar({"content": "Ana prefere respostas curtas em português.", "type": "preference"})
        cfg = core.carregar_config()
        cfg["perfis_modelo"] = {"qwen3.5:4b": {"ferramentas": False}}
        core.salvar_config(cfg)
        self.roteiros = [texto("Certo.")]
        limpo, eventos = self.conversar("como prefiro respostas? português curtas")
        self.assertEqual((limpo, eventos), ("Certo.", []))
        self.assertNotIn("tools", self.enviado[0])
        self.assertIn("respostas curtas", self.enviado[0]["messages"][0]["content"])
        self.assertNotIn("ferramentas", chats.get(self.cid)["mensagens"][-1])

    def test_escrita_desligada_so_oferece_leitura(self):
        cfg = core.carregar_config()
        cfg["ferramentas"]["escrita"] = False
        core.salvar_config(cfg)
        self.roteiros = [[chamada("save_memory", {"content": "algo durável"})], texto("Ok.")]
        _, eventos = self.conversar("lembra disso")
        nomes = {t["function"]["name"] for t in self.enviado[0]["tools"]}
        self.assertNotIn("save_memory", nomes)
        self.assertIn("search_memories", nomes)
        self.assertFalse(eventos[0]["ok"])
        self.assertEqual(memstore.memorias_listar()["total"], 0)

    def test_ferramentas_desligadas_e_imagens_nao_usam_tools(self):
        cfg = core.carregar_config()
        cfg["ferramentas"]["ativo"] = False
        core.salvar_config(cfg)
        self.roteiros = [texto("Oi.")]
        self.conversar("oi")
        self.assertNotIn("tools", self.enviado[0])

    def test_think_so_quando_o_modelo_pensa(self):
        core._caps_cache["qwen3.5:4b"] = (9e18, ["completion", "tools"])
        try:
            self.roteiros = [texto("Oi.")]
            self.conversar("oi")
            self.assertNotIn("think", self.enviado[0])
            core._caps_cache["qwen3.5:4b"] = (9e18, ["completion", "tools", "thinking"])
            self.roteiros = [texto("Oi.")]
            self.conversar("oi de novo")
            self.assertIs(self.enviado[1]["think"], False)
        finally:
            core._caps_cache.pop("qwen3.5:4b", None)


class TestDesfazer(FakeOllama):
    def ctx(self):
        return {"chat_id": self.cid, "modelo": "qwen3.5:4b", "projeto": None, "cfg": core.carregar_config()}

    def test_desfazer_memoria_criada_apaga_ela_e_os_nos_novos(self):
        memstore.no_criar("Atlas")                                    # nó que já existia fica
        r = ferramentas.executar("save_memory", {"content": "O Atlas usa o Ollama localmente.",
                                                 "entities": ["Atlas", "Ollama"]}, self.ctx())
        self.assertTrue(r["ok"])
        g = skills.carregar_grafo()
        self.assertIn("ollama", g["nos"])
        st = self.c.post("/api/ferramentas/desfazer", json={"id": r["evento"]["acao"]})
        self.assertEqual(st.status_code, 200)
        self.assertEqual(memstore.memorias_listar()["total"], 0)
        g = skills.carregar_grafo()
        self.assertNotIn("ollama", g["nos"])
        self.assertIn("atlas", g["nos"])
        self.assertEqual(self.c.post("/api/ferramentas/desfazer", json={"id": r["evento"]["acao"]}).status_code, 409)
        self.assertTrue(ferramentas.listar_acoes()[0]["desfeita"])

    def test_desfazer_edicao_e_conflito(self):
        m = memstore.memoria_criar({"content": "Moro em Campinas.", "type": "fact"})
        r = ferramentas.executar("update_memory", {"id": m["id"], "content": "Moro em São Paulo.", "importance": 5}, self.ctx())
        self.assertTrue(r["ok"])
        self.assertEqual(memstore.memoria_obter(m["id"])["content"], "Moro em São Paulo.")
        self.assertEqual(ferramentas.desfazer(r["evento"]["acao"]), {"ok": True})
        volta = memstore.memoria_obter(m["id"])
        self.assertEqual((volta["content"], volta["importance"]), ("Moro em Campinas.", 3))
        # editada à mão depois da IA: desfazer recusa, a menos que forçado
        r2 = ferramentas.executar("pin_memory", {"id": m["id"], "pinned": True}, self.ctx())
        self.assertTrue(memstore.memoria_obter(m["id"])["pinned"])
        import time as _t
        _t.sleep(1.1)
        memstore.memoria_atualizar(m["id"], {"content": "Moro em Santos."})
        self.assertEqual(ferramentas.desfazer(r2["evento"]["acao"])["erro"], "conflito")
        self.assertTrue(ferramentas.desfazer(r2["evento"]["acao"], forcar=True)["ok"])
        self.assertFalse(memstore.memoria_obter(m["id"])["pinned"])

    def test_desfazer_ligacao_no_grafo(self):
        memstore.no_criar("Ana", "pessoa")
        r = ferramentas.executar("link_concepts", {"from": "Ana", "to": "Aurora", "relation": "trabalha em"}, self.ctx())
        self.assertTrue(r["ok"])
        busca = ferramentas.executar("graph_search", {"query": "Ana"}, self.ctx())
        self.assertIn("Ana trabalha em Aurora", busca["resultado"]["concepts"][0]["relations"])
        self.assertTrue(ferramentas.desfazer(r["evento"]["acao"])["ok"])
        g = skills.carregar_grafo()
        self.assertIn("ana", g["nos"])
        self.assertNotIn("aurora", g["nos"])
        self.assertEqual(g["arestas"], {})

    def test_leitura_nao_registra_acao(self):
        ferramentas.executar("search_memories", {"query": "x"}, self.ctx())
        ferramentas.executar("list_projects", {}, self.ctx())
        self.assertEqual(ferramentas.listar_acoes(), [])

    def test_documentos_so_dentro_da_pasta(self):
        os.makedirs(docs.DOCS_DIR, exist_ok=True)
        with open(os.path.join(docs.DOCS_DIR, "notas.md"), "w", encoding="utf-8") as f:
            f.write("# Notas\nO deploy é às sextas.")
        with open(os.path.join(self.dir, "segredo.txt"), "w", encoding="utf-8") as f:
            f.write("não pode ler")
        ctx = self.ctx()
        lst = ferramentas.executar("list_documents", {}, ctx)["resultado"]
        self.assertEqual(lst["documents"][0]["path"], "notas.md")
        ok = ferramentas.executar("read_document", {"path": "notas.md"}, ctx)
        self.assertIn("sextas", ok["resultado"]["text"])
        for ruim in ("../segredo.txt", os.path.join(self.dir, "segredo.txt"), "..\\segredo.txt", "", "config.json"):
            self.assertFalse(ferramentas.executar("read_document", {"path": ruim}, ctx)["ok"], ruim)

    def test_ferramentas_seguem_as_habilidades(self):
        cfg = core.carregar_config()
        self.assertNotIn("read_document", ferramentas.disponiveis(cfg))
        cfg["habilidades"]["documentos"] = True
        self.assertIn("read_document", ferramentas.disponiveis(cfg))
        cfg["habilidades"]["memoria"] = False
        cfg["habilidades"]["grafo"] = False
        self.assertEqual(ferramentas.disponiveis(cfg), ["list_documents", "read_document"])

    def test_cofre_travado_vira_erro_para_o_modelo(self):
        orig = memstore._checar_cofre

        def travado():
            raise memstore.ErroAPI(423, "vault_locked", "The Atlas vault is locked.")
        memstore._checar_cofre = travado
        try:
            r = ferramentas.executar("search_memories", {"query": "x"}, self.ctx())
        finally:
            memstore._checar_cofre = orig
        self.assertFalse(r["ok"])
        self.assertIn("locked", r["resultado"]["error"])


class TestBuscaNaMemoria(FakeOllama):
    """Perguntas sobre o usuário: o servidor busca antes, com nomes/siglas exatos, em todos os projetos;
    'vou procurar' sem ferramenta faz o servidor procurar; 'certeza?' amplia a busca."""

    def acme(self):
        memstore.projeto_criar("ACME", "ACME Corp - client. Umbrella for every ACME automation.")
        memstore.projeto_criar("ACME Field Alerts", "The original ACME project: a PHP cron that watches jobs.", pai="acme")
        memstore.projeto_criar("ACME Package Intake", "Form 12, the package intake form.", pai="acme")
        memstore.memoria_criar({"content": "Form 12 is the first ACME system with real users.", "type": "note",
                                "project": "acme-package-intake"})
        memstore.memoria_criar({"content": "Lembrar de usar stash antes do rebase.", "type": "note"})   # 'usar' verbo
        chats.definir_ctx(self.cid, projeto="geral")

    def ctx(self, texto=""):
        return {"chat_id": self.cid, "modelo": "qwen3.5:4b", "projeto": "geral", "cfg": core.carregar_config(),
                "texto_usuario": texto}

    def test_pergunta_sobre_o_usuario_busca_antes_em_todos_os_projetos(self):
        self.acme()
        self.roteiros = [texto("Foi o ACME Field Alerts.")]
        limpo, eventos = self.conversar("Qual foi meu primeiro projeto na ACME?")
        self.assertEqual(limpo, "Foi o ACME Field Alerts.")
        self.assertEqual(len(self.enviado), 1)                       # a busca não gastou uma rodada do modelo
        self.assertEqual((eventos[0]["tool"], eventos[0]["auto"]), ("search_memories", True))
        msgs = self.enviado[0]["messages"]
        self.assertEqual([m["role"] for m in msgs[-3:]], ["user", "assistant", "tool"])
        self.assertIn("The original ACME project", msgs[-1]["content"])                 # descrição do projeto
        self.assertIn("first ACME system", msgs[-1]["content"])                          # memória de outro projeto
        self.assertNotIn("Memórias relacionadas", msgs[0]["content"])                   # sem repetir no prompt
        # fixada continua sempre no prompt, mesmo com a busca automática
        fix = memstore.memoria_criar({"content": "Responder sempre em tom direto.", "type": "preference", "pinned": True})
        self.roteiros = [texto("Ok.")]
        self.conversar("Qual foi meu último projeto na ACME?")
        self.assertIn("tom direto", self.enviado[1]["messages"][0]["content"])
        self.assertTrue(fix["pinned"])
        self.assertEqual(chats.get(self.cid)["mensagens"][-1]["ferramentas"][0]["tool"], "search_memories")

    def test_consulta_do_modelo_com_sigla_quebrada_ainda_acha(self):
        self.acme()
        self.assertEqual(ferramentas.consertar_siglas("primeiro projeto ACM E", "meu projeto na ACME?"),
                         "primeiro projeto ACME")
        r = ferramentas.executar("search_memories", {"query": "primeiro projeto ACM E", "project": "Geral"},
                                 self.ctx("Qual foi meu primeiro projeto na ACME?"))["resultado"]
        self.assertEqual(r["searched"]["projects"], "all")                               # 'Geral' = tudo
        self.assertIn("first ACME system", json.dumps(r))
        self.assertEqual(r["projects"][0]["id"], "acme-field-alerts")                  # 'primeiro' ~ 'original'

    def test_promessa_sem_ferramenta_faz_o_servidor_buscar_uma_vez(self):
        memstore.memoria_criar({"content": "O deploy da loja é às sextas.", "type": "fact"})
        self.roteiros = [texto("Vou procurar nas memórias."), texto("Achei: às sextas.")]
        limpo, eventos = self.conversar("e o deploy da loja?")
        self.assertEqual(limpo, "Vou procurar nas memórias.\n\nAchei: às sextas.")
        self.assertEqual(len(self.enviado), 2)
        self.assertTrue(eventos[0]["auto"])
        msgs = self.enviado[1]["messages"]
        self.assertEqual(msgs[-2]["role"], "assistant")
        self.assertEqual(msgs[-2]["content"], "Vou procurar nas memórias.")
        self.assertEqual(msgs[-2]["tool_calls"][0]["function"]["name"], "search_memories")
        self.assertIn("sextas", msgs[-1]["content"])
        # se ele prometer de novo, não vira laço: no máximo um empurrão por resposta
        self.enviado.clear()
        self.roteiros = [texto("Vou verificar."), texto("Vou verificar de novo.")]
        limpo, eventos = self.conversar("e o deploy da loja nova?")
        self.assertEqual(len(self.enviado), 2)
        self.assertEqual(len(eventos), 1)

    def test_nao_tenho_a_informacao_sem_ter_buscado_tambem_busca(self):
        memstore.memoria_criar({"content": "O deploy da loja é às sextas.", "type": "fact"})
        self.roteiros = [texto("Não tenho essa informação registrada."), texto("É às sextas.")]
        limpo, eventos = self.conversar("e o deploy da loja?")
        self.assertTrue(limpo.endswith("É às sextas."))
        self.assertEqual(len(eventos), 1)

    def test_insistencia_faz_busca_ampla_em_outros_projetos(self):
        memstore.projeto_criar("Aurora")
        memstore.projeto_criar("Loja")
        memstore.memoria_criar({"content": "No Aurora o banco escolhido foi MySQL 8.", "type": "decision", "project": "aurora"})
        chats.definir_ctx(self.cid, projeto="loja")
        chats.adicionar(self.cid, "Qual banco escolhemos no Aurora?", "Não sei.")
        self.roteiros = [texto("Foi MySQL 8.")]
        limpo, eventos = self.conversar("Certeza?")
        self.assertEqual(limpo, "Foi MySQL 8.")
        ev = eventos[0]
        self.assertTrue(ev["auto"])
        self.assertIn("Aurora", ev["args"]["query"])                                     # repete a pergunta anterior
        self.assertEqual(ev["args"]["limit"], "10")
        res = json.loads(self.enviado[0]["messages"][-1]["content"])
        self.assertIn("MySQL 8", json.dumps(res, ensure_ascii=False))
        self.assertEqual(res["searched"]["projects"], "all")
        self.assertIn("doubts", res["note"])
        # um segundo "certeza?" ainda volta à pergunta de verdade, não ao "certeza?" anterior
        self.roteiros = [texto("Sim.")]
        _, eventos = self.conversar("tem certeza mesmo?")
        self.assertIn("Aurora", eventos[0]["args"]["query"])

    def test_nada_encontrado_diz_o_que_verificou(self):
        memstore.memoria_criar({"content": "Ana prefere respostas curtas.", "type": "preference"})
        r = ferramentas.executar("search_memories", {"query": "receita de bolo"}, self.ctx("receita de bolo?"))
        res = r["resultado"]
        self.assertIs(res["found"], False)
        self.assertEqual(res["checked"]["memories"], 1)
        self.assertIn("what you checked", res["note"])

    def test_conversa_simples_nao_busca(self):
        self.acme()
        for msg in ("oi, tudo bem?", "me explica o que é docker", "lembra disso: gosto de café", "Como usar o git?"):
            self.assertIsNone(ferramentas.classificar(msg, "pergunta anterior"), msg)
        self.roteiros = [texto("Tudo ótimo.")]
        limpo, eventos = self.conversar("oi, tudo bem?")
        self.assertEqual((limpo, eventos, len(self.enviado)), ("Tudo ótimo.", [], 1))
        self.assertFalse(any(m["role"] == "tool" for m in self.enviado[0]["messages"]))
        self.assertEqual(ferramentas.classificar("o que você lembra sobre o projeto ACME?"), "memoria")
        self.assertEqual(ferramentas.classificar("Quem aprova o escopo na ACME?"), "memoria")   # nome conhecido

    def test_busca_automatica_pode_ser_desligada(self):
        self.acme()
        cfg = core.carregar_config()
        cfg["ferramentas"]["busca_auto"] = False
        core.salvar_config(cfg)
        self.roteiros = [texto("Não tenho essa informação.")]
        _, eventos = self.conversar("Qual foi meu primeiro projeto na ACME?")
        self.assertEqual(len(eventos), 1)                    # só o empurrão do "não sei", não a busca antes
        self.assertEqual(len(self.enviado), 2)

    def test_pensar_auto_so_na_rodada_que_responde_da_busca(self):
        self.acme()
        core._caps_cache["qwen3.5:4b"] = (9e18, ["completion", "tools", "thinking"])
        try:
            cfg = core.carregar_config()
            cfg["ferramentas"]["pensar"] = "auto"
            core.salvar_config(cfg)
            self.roteiros = [texto("Foi o Field Alerts.")]
            self.conversar("Qual foi meu primeiro projeto na ACME?")
            self.assertIs(self.enviado[0]["think"], True)
            self.roteiros = [texto("Oi!")]
            self.conversar("oi")
            self.assertIs(self.enviado[1]["think"], False)
        finally:
            core._caps_cache.pop("qwen3.5:4b", None)

    def test_resultado_encolhe_para_caber_na_janela(self):
        res = {"count": 12, "searched": {"queries": ["x"], "projects": "all"},
               "projects": [{"id": "acme", "name": "ACME", "description": "d" * 400, "memories": 3}],
               "memories": [{"id": f"mem_{i}", "content": "c" * 400, "project_name": "ACME"} for i in range(12)],
               "concepts": [{"label": "ACME", "relations": ["a b c"] * 6}], "past_conversations": "p" * 800}
        t = ferramentas.resultado_texto(res, 2500)
        self.assertLessEqual(len(t), 2500)
        d = json.loads(t)                                                                  # continua JSON válido
        self.assertEqual(d["projects"][0]["id"], "acme")
        self.assertNotIn("past_conversations", d)
        self.assertEqual(d["count"], len(d["memories"]))
        self.assertGreater(len(d["memories"]), 0)

    def test_janela_pequena_corta_historico_antigo(self):
        cfg = core.carregar_config()
        cfg["num_ctx"] = 2048
        cfg["contexto"] = {**(cfg.get("contexto") or {}), "hist_msgs": 6}
        core.salvar_config(cfg)
        for i in range(6):
            chats.adicionar(self.cid, f"pergunta {i} " + "x" * 900, "resposta " + "y" * 900)
        self.roteiros = [texto("Ok.")]
        self.conversar("oi")
        n_hist = sum(1 for m in self.enviado[0]["messages"] if m["role"] == "user") - 1
        self.assertLess(n_hist, 6)


class TestBuscaPalavras(tm.Base):
    def test_traducao_e_nome_do_projeto_contam_na_busca(self):
        memstore.projeto_criar("ACME")
        memstore.memoria_criar({"content": "Bia approves scope and budget.", "type": "person", "project": "acme"})
        memstore.memoria_criar({"content": "Vou usar o cupom amanhã.", "type": "note"})
        ids = [m["content"] for m in memstore.memorias_buscar("quem aprova o escopo na ACME?", limite=3,
                                                              semantica=False)["items"]]
        self.assertEqual(ids[0], "Bia approves scope and budget.")
        self.assertEqual(memstore._tokens_busca("Certeza que não lembra?"), [])

    def test_geral_enxerga_todos_os_projetos_no_contexto(self):
        import contexto
        memstore.projeto_criar("ACME")
        m = memstore.memoria_criar({"content": "ACME n8n account is on the Starter plan.", "type": "fact", "project": "acme"})
        cfg = core.carregar_config()
        sel = contexto.selecionar_memorias("plano do n8n da ACME", "geral", cfg, {}, 5000)
        self.assertIn(m["id"], [u["id"] for u in sel])


class TestHardware(unittest.TestCase):
    def setUp(self):
        self._orig = (core._rodar, core.platform.system, core.platform.machine, core._ram_total_gb, core._gpus_windows,
                      core._gpus_linux, core._cpu_info)

    def tearDown(self):
        (core._rodar, core.platform.system, core.platform.machine, core._ram_total_gb, core._gpus_windows,
         core._gpus_linux, core._cpu_info) = self._orig
        core._hw_cache.update({"t": 0.0, "v": None})

    def test_duas_nvidia_e_integrada_ignorada(self):
        core.platform.system = lambda: "Windows"
        core._ram_total_gb = lambda: 64.0
        core._cpu_info = lambda: {"cpu": "Ryzen 9", "nucleos": 16, "threads": 32}
        core._rodar = lambda cmd, timeout=6: "NVIDIA GeForce RTX 4090, 24564\nNVIDIA GeForce RTX 3090, 24576\n"
        core._gpus_windows = lambda: [{"nome": "AMD Radeon(TM) Graphics", "vendor": "amd", "vram_gb": 0.5}]
        hw = core._detectar_hw()
        self.assertEqual(hw["vram_gb"], 48.0)
        self.assertEqual(hw["n_gpus"], 2)
        self.assertEqual(hw["tipo"], "nvidia")
        self.assertTrue([g for g in hw["gpus"] if g["vendor"] == "amd"][0]["integrada"])
        self.assertEqual(hw["nucleos"], 16)

    def test_apple_silicon_memoria_unificada(self):
        core.platform.system = lambda: "Darwin"
        core.platform.machine = lambda: "arm64"
        core._ram_total_gb = lambda: 32.0
        core._cpu_info = lambda: {"cpu": "Apple M3 Pro", "nucleos": 12, "threads": 12}
        hw = core._detectar_hw()
        self.assertTrue(hw["unificada"])
        self.assertEqual(hw["tipo"], "apple")
        self.assertEqual(hw["vram_gb"], 21.4)
        self.assertEqual(core.cabe(17, hw), "vram")                # 27B Q4 cabe num Mac de 32 GB
        self.assertEqual(core.cabe(25, hw), "parcial")
        self.assertEqual(core.cabe(43, hw), "grande")

    def test_so_cpu_no_linux(self):
        core.platform.system = lambda: "Linux"
        core._ram_total_gb = lambda: 16.0
        core._cpu_info = lambda: {"cpu": "Intel i5", "nucleos": 4, "threads": 8}
        core._rodar = lambda cmd, timeout=6: ""
        core._gpus_linux = lambda: [{"nome": "Intel Graphics", "vendor": "intel", "vram_gb": 0.0}]
        hw = core._detectar_hw()
        self.assertEqual((hw["tipo"], hw["vram_gb"]), ("cpu", 0.0))
        rec = core.recomendacoes(hw)
        self.assertLessEqual(rec["rapido"]["gb"], 3.5)
        self.assertTrue(core.info_modelo(rec["equilibrado"]["nome"])["ferramentas"])

    def test_ajuste_manual_sobrescreve(self):
        hw = {"gpu": "X", "vram_gb": 6.0, "ram_gb": 16.0, "tipo": "nvidia", "fonte": "auto"}
        self.assertEqual(core._aplicar_manual(hw, {"ativo": False, "vram_gb": 24}), hw)
        m = core._aplicar_manual(hw, {"ativo": True, "vram_gb": 24, "ram_gb": None, "gpu": "RTX 4090"})
        self.assertEqual((m["vram_gb"], m["ram_gb"], m["gpu"], m["fonte"]), (24.0, 16.0, "RTX 4090", "manual"))
        cpu = core._aplicar_manual(hw, {"ativo": True, "vram_gb": 0})
        self.assertEqual(cpu["tipo"], "cpu")

    def test_recomendacoes_por_maquina(self):
        seis = core.recomendacoes({"vram_gb": 6.0, "ram_gb": 16.0})
        self.assertEqual(seis["equilibrado"]["cabe"], "vram")
        self.assertLessEqual(seis["equilibrado"]["gb"], 5.0)
        grande = core.recomendacoes({"vram_gb": 24.0, "ram_gb": 64.0})
        self.assertGreaterEqual(core.info_modelo(grande["equilibrado"]["nome"])["params"], 20)
        self.assertEqual(core.recomendacoes({"vram_gb": 0, "ram_gb": 0}), {})
        for r in list(seis.values()) + list(grande.values()):
            self.assertTrue(core.nome_modelo_valido(r["tag"]))
        # 6 GB + 16 GB: o "inteligente" divide pouco com a RAM; nada que encha a RAM (ex. 14 GB)
        self.assertEqual(seis["inteligente"]["nome"], "qwen3.5:9b")
        for r in seis.values():
            self.assertLess(r["gb"], 8, r)
        self.assertNotEqual(grande["inteligente"]["nome"], grande["equilibrado"]["nome"])

    def test_catalogo_medido(self):
        medidos = [m["nome"] for m in core.CATALOGO_MODELOS if m.get("medido")]
        self.assertIn("qwen3.5:4b", medidos)
        self.assertEqual(core.info_modelo("qwen3.5:4b")["medido"]["ferramentas"], "4/4")
        self.assertNotIn("medido", core.info_modelo("gpt-oss:20b"))       # só pesquisado

    def test_melhor_variante(self):
        m = core.info_modelo("qwen3.5:9b")
        self.assertEqual(core.melhor_variante(m, {"vram_gb": 6.0, "ram_gb": 16})["tag"], "qwen3.5:9b")     # parcial: o menor
        self.assertEqual(core.melhor_variante(m, {"vram_gb": 16.0, "ram_gb": 32})["tag"], "qwen3.5:9b-q8_0")  # cabe: o mais fiel

    def test_catalogo_consistente(self):
        nomes = set()
        for m in core.CATALOGO_MODELOS:
            self.assertTrue(core.nome_modelo_valido(m["nome"]), m["nome"])
            self.assertNotIn(m["nome"], nomes)
            nomes.add(m["nome"])
            if m.get("tipo") != "embed":
                self.assertIn(m.get("nivel"), core.NIVEIS, m["nome"])
            for v in m.get("variantes") or []:
                self.assertTrue(core.nome_modelo_valido(v["tag"]), v["tag"])
        for nivel in core.NIVEIS:
            self.assertTrue(any(m.get("nivel") == nivel for m in core.CATALOGO_MODELOS), nivel)


class TestEstadoEAjustes(tu.BaseModelos):
    def test_estado_traz_recomendacoes_variantes_e_ferramentas(self):
        d = self.c.get("/api/estado").get_json()
        self.assertIn("equilibrado", d["recomendacoes"])
        cat = {m["nome"]: m for m in d["catalogo"]}
        self.assertTrue(cat[d["recomendacoes"]["equilibrado"]["nome"]]["recomendado"])
        self.assertEqual(len(cat["qwen3.5:9b"]["variantes"]), 3)
        self.assertIn(cat["qwen3.5:9b"]["variantes"][0]["cabe"], ("vram", "parcial", "grande"))
        info = {m["nome"]: m for m in d["instalados_info"]}
        self.assertTrue(info["qwen3:4b"]["ferramentas"])
        self.assertFalse(info["meu-modelo:7b"]["ferramentas"])
        self.assertIn("enorme", d["niveis"])

    def test_hardware_manual_validado(self):
        cfg = core.carregar_config()
        cfg, av = ajustes.aplicar(cfg, {"hardware_manual": {"ativo": True, "vram_gb": "24", "ram_gb": -5, "gpu": "RTX 4090"},
                                        "ferramentas": {"ativo": False, "max_rodadas": 99},
                                        "perfis_modelo": {"qwen3:4b": {"ferramentas": False}}}, ["qwen3:4b"])
        self.assertEqual(cfg["hardware_manual"], {"ativo": True, "vram_gb": 24.0, "ram_gb": 0.0, "gpu": "RTX 4090",
                                                  "unificada": False})
        self.assertEqual(cfg["ferramentas"], {"ativo": False, "escrita": True, "max_rodadas": 8, "busca_auto": True,
                                              "pensar": "nunca"})
        cfg, av = ajustes.aplicar(cfg, {"ferramentas": {"pensar": "talvez", "busca_auto": False}})
        self.assertEqual((cfg["ferramentas"]["pensar"], cfg["ferramentas"]["busca_auto"]), ("nunca", False))
        self.assertIn("ferramentas.pensar inválido", av)
        self.assertIs(cfg["perfis_modelo"]["qwen3:4b"]["ferramentas"], False)
        self.assertFalse(core.suporta_ferramentas("qwen3:4b", cfg))
        cfg, av = ajustes.aplicar(cfg, {"hardware_manual": {"vram_gb": "abc"}})
        self.assertTrue(av)

    def test_api_hardware(self):
        d = self.c.get("/api/hardware").get_json()
        self.assertEqual(d["efetivo"], tu.HW)
        self.assertIn("rapido", d["recomendacoes"])
