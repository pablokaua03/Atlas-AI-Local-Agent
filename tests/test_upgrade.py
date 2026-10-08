"""
Testes das melhorias de outubro: parar a geração sem perder a resposta, busca nas conversas,
recall de conversas mais limpo, modelos instalados (fora do catálogo), indicador de VRAM,
modelo por conversa, status do Ollama e seleção de memórias (piso de relevância, IDF, fixadas).
Isolados em pasta temporária e sem Ollama:
    python -m pytest -q
"""
import os
import sys
import json
import urllib.parse
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_memapi as tm  # noqa: E402
import core               # noqa: E402
import chats              # noqa: E402
import skills             # noqa: E402
import server             # noqa: E402
import memstore           # noqa: E402
import contexto           # noqa: E402


class FakeResp:
    """Resposta de streaming do Ollama falsa; registra se foi fechada."""

    def __init__(self, linhas):
        self.linhas = linhas
        self.fechada = False

    def iter_lines(self):
        for x in self.linhas:
            if self.fechada:
                return
            yield x

    def close(self):
        self.fechada = True


class BaseChat(tm.Base):
    def setUp(self):
        super().setUp()
        self._ol = (core.ollama_online, core.achar_ollama, server.requests.post, skills.extrair_fato)
        core.ollama_online = lambda: False
        core.achar_ollama = lambda: ""
        skills.extrair_fato = lambda *a, **k: None
        self.enviado = []
        self.resp = None

        def post(url, json=None, **kw):
            self.enviado.append(json)
            self.resp = FakeResp([b'{"message":{"content":"Primeira "}}', b'{"message":{"content":"parte "}}',
                                  b'{"message":{"content":"segunda parte"}}', b'{"done":true}'])
            return self.resp
        server.requests.post = post

    def tearDown(self):
        core.ollama_online, core.achar_ollama, server.requests.post, skills.extrair_fato = self._ol
        super().tearDown()


class TestPararGeracao(BaseChat):
    def test_parar_guarda_parcial_e_fecha_stream(self):
        cid = chats.listar()["atual"]
        r = self.c.post("/chat", json={"texto": "conte uma história", "chat_id": cid}, buffered=False)
        it = iter(r.response)
        primeiro = next(it)
        self.assertIn(b"Primeira", primeiro)
        r.close()                                         # o navegador abortou (botão parar)
        self.assertTrue(self.resp.fechada)
        msgs = chats.get(cid)["mensagens"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["a"], "Primeira")
        self.assertTrue(msgs[0]["parcial"])

    def test_resposta_completa_nao_e_parcial(self):
        cid = chats.listar()["atual"]
        corpo = self.c.post("/chat", json={"texto": "oi", "chat_id": cid}).get_data(as_text=True)
        self.assertEqual(corpo, "Primeira parte segunda parte")
        m = chats.get(cid)["mensagens"][-1]
        self.assertNotIn("parcial", m)


class TestBuscaConversas(tm.Base):
    def test_busca_titulo_e_conteudo_sem_acento(self):
        a = chats.novo()
        chats.adicionar(a, "Plano da viagem a São Paulo", "Vamos de ônibus.")
        b = chats.novo()
        chats.adicionar(b, "receita de bolo", "Use três ovos e açúcar mascavo.")
        d = chats.listar("sao paulo")
        self.assertEqual([c["id"] for c in d["chats"]], [a])
        d = chats.listar("ACUCAR")
        self.assertEqual([c["id"] for c in d["chats"]], [b])
        self.assertIn("açúcar", d["chats"][0]["trecho"])
        r = self.c.get("/api/chats?q=onibus").get_json()
        self.assertEqual([c["id"] for c in r["chats"]], [a])
        self.assertEqual(len(self.c.get("/api/chats").get_json()["chats"]), 2)   # sem q: todas
        self.assertEqual(self.c.get("/api/chats?q=zzzz").get_json()["chats"], [])


class TestRecall(tm.Base):
    def test_ignora_palavras_vazias_e_prefere_o_assunto(self):
        a = chats.novo()
        chats.adicionar(a, "como você está hoje?", "Estou bem, e você?")
        chats.adicionar(a, "o que você acha disso hoje", "Acho que está tudo certo.")
        b = chats.novo()
        chats.adicionar(b, "o foguete usa metano líquido?", "Sim, o Raptor queima metano.")
        atual = chats.novo()
        rec = chats.recall("como está o foguete hoje? usa metano?", excluir_id=atual)
        self.assertIn("foguete", rec)
        self.assertNotIn("Estou bem", rec)
        self.assertEqual(chats.recall("como você está hoje", excluir_id=atual), "")   # só palavras vazias
        self.assertEqual(chats.recall("", excluir_id=atual), "")

    def test_um_termo_raro_basta(self):
        a = chats.novo()
        chats.adicionar(a, "me explica kubernetes", "É um orquestrador de contêineres.")
        chats.adicionar(a, "e docker?", "Empacota aplicações.")
        rec = chats.recall("kubernetes", excluir_id=chats.novo())
        self.assertIn("orquestrador", rec)


INSTALADOS = [
    {"nome": "qwen3:4b", "bytes": 2_500_000_000, "gb": 2.5, "familia": "qwen3", "parametros": "4.0B",
     "quant": "Q4_K_M", "modificado": ""},
    {"nome": "meu-modelo:7b", "bytes": 5_300_000_000, "gb": 5.3, "familia": "llama", "parametros": "7B",
     "quant": "Q4_0", "modificado": ""},
    {"nome": "gigante:70b", "bytes": 40_000_000_000, "gb": 40.0, "familia": "llama", "parametros": "70B",
     "quant": "Q4_0", "modificado": ""},
    {"nome": "snowflake-arctic-embed:latest", "bytes": 600_000_000, "gb": 0.6, "familia": "bert",
     "parametros": "335M", "quant": "F16", "modificado": ""},
]
HW = {"gpu": "GPU de teste", "vram_gb": 6.0, "ram_gb": 16.0}


class BaseModelos(BaseChat):
    def setUp(self):
        super().setUp()
        self._mod = (core.listar_modelos_info, core.hardware)
        core.ollama_online = lambda: True
        core.listar_modelos_info = lambda: [dict(m) for m in INSTALADOS]
        core.hardware = lambda ttl=300.0: dict(HW)

    def tearDown(self):
        core.listar_modelos_info, core.hardware = self._mod
        super().tearDown()


class TestCabe(unittest.TestCase):
    def test_faixas(self):
        self.assertEqual(core.cabe(2.5, HW), "vram")
        self.assertEqual(core.cabe(5.2, HW), "parcial")          # 8B quantizado: divide com a RAM
        self.assertEqual(core.cabe(40, HW), "grande")
        self.assertEqual(core.cabe(2.5, HW, num_ctx=32768), "parcial")   # contexto grande pesa
        self.assertIsNone(core.cabe(2.5, {"vram_gb": 0, "ram_gb": 0}))
        self.assertIsNone(core.cabe(None, HW))
        self.assertEqual(core.cabe(3.0, {"vram_gb": 0, "ram_gb": 16}), "parcial")   # sem GPU: só RAM

    def test_nomes_e_tipos(self):
        for ok in ("qwen3:8b", "phi4-mini", "hf.co/user/repo:Q4_K_M", "llama3"):
            self.assertTrue(core.nome_modelo_valido(ok), ok)
        for ruim in ("", None, "../x", "a b", ":8b", "x" * 200, "a;rm"):
            self.assertFalse(core.nome_modelo_valido(ruim), ruim)
        self.assertTrue(core.mesmo_modelo("llama3", "llama3:latest"))
        self.assertFalse(core.mesmo_modelo("llama3", "llama3:8b"))
        self.assertTrue(core.eh_embed("nomic-embed-text"))
        self.assertTrue(core.eh_embed("snowflake-arctic-embed:latest"))
        self.assertFalse(core.eh_embed("meu-modelo:7b"))


class TestModelosInstalados(BaseModelos):
    def test_estado_traz_instalados_com_tamanho_e_encaixe(self):
        d = self.c.get("/api/estado").get_json()
        self.assertEqual(d["hardware"], HW)
        info = {m["nome"]: m for m in d["instalados_info"]}
        self.assertEqual(info["qwen3:4b"]["cabe"], "vram")
        self.assertEqual(info["qwen3:4b"]["catalogo"], "qwen3:4b")
        self.assertEqual(info["meu-modelo:7b"]["cabe"], "parcial")
        self.assertEqual(info["meu-modelo:7b"]["catalogo"], "")
        self.assertEqual(info["gigante:70b"]["cabe"], "grande")
        self.assertEqual(info["snowflake-arctic-embed:latest"]["tipo"], "embed")
        cat = {m["nome"]: m for m in d["catalogo"]}
        self.assertTrue(cat["qwen3:4b"]["instalado"])
        self.assertIn(cat["qwen3:14b"]["cabe"], ("parcial", "grande"))
        self.assertIn("qwen3:4b", d["instalados"])                     # campo antigo continua

    def test_modelo_fora_do_catalogo_so_se_instalado(self):
        d = self.c.post("/api/config", json={"modelo": "meu-modelo:7b"}).get_json()
        self.assertEqual(d["config"]["modelo"], "meu-modelo:7b")
        self.assertNotIn("avisos", d)
        self.assertEqual(core.carregar_config()["modelo"], "meu-modelo:7b")      # persiste e recarrega
        d = self.c.post("/api/config", json={"modelo": "nao-instalado:3b"}).get_json()
        self.assertEqual(d["config"]["modelo"], "meu-modelo:7b")
        self.assertTrue(d["avisos"])
        d = self.c.post("/api/config", json={"modelo": "snowflake-arctic-embed"}).get_json()
        self.assertEqual(d["config"]["modelo"], "meu-modelo:7b")            # embed não vira modelo de chat
        d = self.c.post("/api/config", json={"embed": "snowflake-arctic-embed"}).get_json()
        self.assertEqual(d["config"]["embed"], "snowflake-arctic-embed")
        d = self.c.post("/api/config", json={"perfis_modelo": {"meu-modelo:7b": {"num_ctx": 8192}}}).get_json()
        self.assertEqual(d["config"]["perfis_modelo"]["meu-modelo:7b"]["num_ctx"], 8192)

    def test_config_antiga_com_lixo_volta_ao_padrao(self):
        with open(core.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"modelo": "../../etc", "embed": 5}, f)
        cfg = core.carregar_config()
        self.assertEqual(cfg["modelo"], core.CONFIG_PADRAO["modelo"])
        self.assertEqual(cfg["embed"], core.CONFIG_PADRAO["embed"])

    def test_modelo_por_conversa(self):
        cid = chats.listar()["atual"]
        r = self.c.post(f"/api/chats/{cid}/ctx", json={"modelo": "meu-modelo:7b"}).get_json()
        self.assertEqual(r["modelo"], "meu-modelo:7b")
        self.assertEqual(self.c.get("/api/chats").get_json()["chats"][0]["modelo"], "meu-modelo:7b")
        r = self.c.post("/chat", json={"texto": "oi", "chat_id": cid})
        r.get_data()
        self.assertEqual(self.enviado[-1]["model"], "meu-modelo:7b")
        info = json.loads(urllib.parse.unquote(r.headers["X-Atlas-Contexto"]))
        self.assertEqual(info["modelo"], "meu-modelo:7b")
        # desinstalado → volta ao modelo padrão sem quebrar
        core.listar_modelos_info = lambda: [dict(INSTALADOS[0])]
        self.c.post("/chat", json={"texto": "oi de novo", "chat_id": cid}).get_data()
        self.assertEqual(self.enviado[-1]["model"], core.modelo_atual())
        self.assertEqual(self.c.post(f"/api/chats/{cid}/ctx", json={"modelo": "a b"}).status_code, 400)
        self.assertEqual(self.c.post(f"/api/chats/{cid}/ctx", json={"modelo": "nomic-embed-text"}).status_code, 400)
        r = self.c.post(f"/api/chats/{cid}/ctx", json={"modelo": None}).get_json()
        self.assertIsNone(r["modelo"])


class TestOllamaStatus(BaseChat):
    def test_status_e_start(self):
        d = self.c.get("/api/ollama/status").get_json()
        self.assertEqual(d, {"online": False, "instalado": False, "modelos": []})
        d = self.c.post("/api/ollama/start").get_json()
        self.assertEqual(d, {"ok": False, "online": False, "erro": "nao_instalado"})

    def test_start_timeout(self):
        antigo = (core.subprocess.Popen, core.time.sleep)
        core.achar_ollama = lambda: "C:/fake/ollama.exe"
        core.subprocess.Popen = lambda *a, **k: None
        core.time.sleep = lambda s: None
        try:
            d = core.iniciar_ollama_detalhe(espera=0.01)
        finally:
            core.subprocess.Popen, core.time.sleep = antigo
        self.assertEqual(d["erro"], "timeout")
        self.assertFalse(d["ok"])

    def test_pull_valida_nome_e_ollama_parado(self):
        self.assertEqual(self.c.post("/api/pull", json={"modelo": "a b"}).status_code, 400)
        linhas = self.c.post("/api/pull", json={"modelo": "qwen3:4b"}).get_data(as_text=True).strip()
        self.assertEqual(json.loads(linhas)["error"], "ollama_offline")
        self.assertEqual(self.c.post("/api/ollama/delete", json={"modelo": "../x"}).status_code, 400)


class TestSelecaoMemorias(tm.Base):
    def cfg(self, **c):
        cfg = core.carregar_config()
        cfg["contexto"].update(c)
        return cfg

    def test_idf_prefere_termo_raro(self):
        for i in range(6):
            self.api("POST", "/v1/memories", {"content": f"Hoje eu fiz a tarefa comum número {i}"})
        st, raro = self.api("POST", "/v1/memories", {"content": "O cluster kubernetes roda no servidor de casa"})
        st, comum = self.api("POST", "/v1/memories", {"content": "Hoje o servidor reiniciou sozinho"})
        r = memstore.memorias_buscar("kubernetes hoje", semantica=False)["items"]
        self.assertEqual(r[0]["id"], raro["id"])

    def test_piso_de_relevancia(self):
        itens = [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.2}, {"id": "c", "score": 0.1}]
        self.assertEqual([m["id"] for m in contexto.cortar_fracas(itens)], ["a"])
        itens = [{"id": "a", "score": 0.3}, {"id": "b", "score": 0.15}]
        self.assertEqual([m["id"] for m in contexto.cortar_fracas(itens)], ["a", "b"])
        self.assertEqual(contexto.cortar_fracas([]), [])

    def test_memoria_fraca_nao_entra_no_contexto(self):
        st, forte = self.api("POST", "/v1/memories", {"content": "O foguete usa metano líquido e oxigênio"})
        st, fraca = self.api("POST", "/v1/memories",
                             {"content": "Lista de compras: arroz, feijão, café, leite, pão, ovos, manteiga e foguetes de festa"})
        u = contexto.selecionar_memorias("qual combustível o foguete usa? metano líquido?", None, self.cfg(), {}, 5000)
        ids = [x["id"] for x in u]
        self.assertIn(forte["id"], ids)
        self.assertNotIn(fraca["id"], ids)

    def test_fixada_global_respeita_projeto(self):
        st, g = self.api("POST", "/v1/memories", {"content": "Prefiro respostas curtas", "pinned": True})
        self.assertTrue(g["pinned"])
        st, p = self.api("POST", "/v1/memories", {"content": "Deploy do foguete é na sexta", "project": "foguete"})
        st, d = self.api("PATCH", f"/v1/memories/{p['id']}", {"pinned": True})
        self.assertEqual(st, 200)
        self.assertTrue(d["pinned"])
        self.api("POST", "/v1/memories", {"content": "Outro projeto", "project": "loja", "pinned": True})
        st, d = self.api("PATCH", f"/v1/memories/{p['id']}", {"pinned": "sim"})
        self.assertEqual(st, 400)

        sem_proj = {x["id"] for x in contexto.selecionar_memorias("bom dia", None, self.cfg(), {}, 5000)}
        self.assertEqual(sem_proj, {g["id"]})                                  # só a da geral
        no_foguete = contexto.selecionar_memorias("bom dia", "foguete", self.cfg(), {}, 5000)
        self.assertEqual({x["id"] for x in no_foguete}, {g["id"], p["id"]})      # herda a da geral, não a da loja
        self.assertTrue(all(x["pinned"] and x["why"] == "fixada" for x in no_foguete))
        excl = contexto.selecionar_memorias("bom dia", "foguete", self.cfg(), {"ctx_excluidas": [g["id"]]}, 5000)
        self.assertEqual({x["id"] for x in excl}, {p["id"]})                   # exclusão da conversa vence

        st, d = self.api("GET", "/v1/memories?pinned=true&scope=all")
        self.assertEqual(d["total"], 3)
        st, d = self.api("GET", "/v1/memories?pinned=false&scope=all")
        self.assertEqual(d["total"], 0)
        st, d = self.api("GET", f"/v1/memories/{p['id']}")
        self.assertTrue(d["pinned"])
        st, d = self.api("POST", "/v1/memories", {"content": "Sem campo novo"})
        self.assertNotIn("pinned", d)                                          # formato antigo continua igual

    def test_busca_semantica_opcional_salva(self):
        d = self.c.post("/api/config", json={"contexto": {"busca_semantica": True}}).get_json()
        self.assertTrue(d["config"]["contexto"]["busca_semantica"])
        self.assertTrue(core.carregar_config()["contexto"]["busca_semantica"])


class TestPorta(unittest.TestCase):
    def test_atlas_port(self):
        antigo = os.environ.get("ATLAS_PORT")
        try:
            os.environ["ATLAS_PORT"] = "5077"
            self.assertEqual(server._porta(), 5077)
            for ruim in ("abc", "80", "99999"):
                os.environ["ATLAS_PORT"] = ruim
                self.assertEqual(server._porta(), 5005)
            os.environ.pop("ATLAS_PORT")
            self.assertEqual(server._porta(), 5005)
        finally:
            if antigo is not None:
                os.environ["ATLAS_PORT"] = antigo


if __name__ == "__main__":
    unittest.main()
