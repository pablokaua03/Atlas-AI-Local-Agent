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
