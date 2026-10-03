"""
Testes da API de memória (/v1), do servidor MCP e do backup com cofre.

Rodam isolados numa pasta temporária (não tocam nos seus dados) e sem Ollama:
    python -m unittest discover -s tests
"""
import io
import os
import sys
import json
import shutil
import zipfile
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core      # noqa: E402
import cofre     # noqa: E402
import skills    # noqa: E402
import memstore  # noqa: E402
import memapi    # noqa: E402
import server    # noqa: E402
import mcp_atlas  # noqa: E402

BASE = "http://127.0.0.1:5005"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="atlas-test-")
        self._orig = {}
        alvos = {
            (core, "BASE_DIR"): self.dir,
            (core, "CONFIG_FILE"): os.path.join(self.dir, "config.json"),
            (core, "MEM_FILE"): os.path.join(self.dir, "memoria.json"),
            (core, "CONVERSAS_FILE"): os.path.join(self.dir, "conversas.json"),
            (core, "GRAFO_FILE"): os.path.join(self.dir, "grafo.json"),
            (core, "OBS_FILE"): os.path.join(self.dir, "observacoes.json"),
            (memstore, "STORE_FILE"): os.path.join(self.dir, "memorias.json"),
            (memstore, "VETORES_FILE"): os.path.join(self.dir, "memorias_vetores.json"),
            (memstore, "_embed_disponivel"): lambda: False,     # sem Ollama nos testes
            (memstore, "indexar_async"): lambda: None,
        }
        for (mod, nome), valor in alvos.items():
            self._orig[(mod, nome)] = getattr(mod, nome)
            setattr(mod, nome, valor)
        cofre.bloquear()
        self.c = server.app.test_client()

    def tearDown(self):
        cofre.bloquear()
        for (mod, nome), valor in self._orig.items():
            setattr(mod, nome, valor)
        shutil.rmtree(self.dir, ignore_errors=True)

    def api(self, metodo, caminho, corpo=None, token=True, base=BASE):
        h = {"Authorization": "Bearer " + memapi.token()} if token else {}
        r = self.c.open(caminho, method=metodo, base_url=base, json=corpo, headers=h)
        return r.status_code, r.get_json()


class TestSeguranca(Base):
    def test_sem_token(self):
        st, d = self.api("GET", "/v1", token=False)
        self.assertEqual(st, 401)
        self.assertEqual(d["error"]["code"], "unauthorized")

    def test_token_errado(self):
        r = self.c.get("/v1", base_url=BASE, headers={"Authorization": "Bearer nope"})
        self.assertEqual(r.status_code, 401)

    def test_x_atlas_token(self):
        r = self.c.get("/v1", base_url=BASE, headers={"X-Atlas-Token": memapi.token()})
        self.assertEqual(r.status_code, 200)

    def test_host_estranho(self):
        st, _ = self.api("GET", "/v1", base="http://evil.example:5005")
        self.assertEqual(st, 403)
        r = self.c.get("/api/memapi", base_url="http://evil.example:5005")
        self.assertEqual(r.status_code, 403)

    def test_openapi_sem_token(self):
        st, d = self.api("GET", "/v1/openapi.json", token=False)
        self.assertEqual(st, 200)
        self.assertEqual(d["openapi"], "3.1.0")

    def test_api_desligada(self):
        cfg = core.carregar_config()
        cfg["api_ativa"] = False
        core.salvar_config(cfg)
        st, d = self.api("GET", "/v1/projects")
        self.assertEqual(st, 503)

    def test_girar_token(self):
        antigo = memapi.token()
        self.c.post("/api/memapi/girar", base_url=BASE)
        self.assertNotEqual(antigo, memapi.token())
        r = self.c.get("/v1", base_url=BASE, headers={"Authorization": "Bearer " + antigo})
        self.assertEqual(r.status_code, 401)


class TestProjetos(Base):
    def test_crud(self):
        st, p = self.api("POST", "/v1/projects", {"name": "Site da Loja", "description": "React", "tags": ["Web"]})
        self.assertEqual(st, 201)
        self.assertEqual(p["id"], "site-da-loja")
        self.assertEqual(p["tags"], ["web"])
        self.assertEqual(self.api("POST", "/v1/projects", {"name": "site da loja"})[0], 409)
        st, p = self.api("PATCH", "/v1/projects/Site da Loja", {"name": "Loja Online"})
        self.assertEqual((st, p["name"]), (200, "Loja Online"))
        ids = [x["id"] for x in self.api("GET", "/v1/projects")[1]["items"]]
        self.assertEqual(ids, ["geral", "site-da-loja"])
        # o projeto vira nó do grafo (e acompanha o nome)
        st, no = self.api("GET", "/v1/graph/nodes/" + p["node"])
        self.assertEqual((no["type"], no["label"]), ("projeto", "Loja Online"))

    def test_nome_obrigatorio(self):
        self.assertEqual(self.api("POST", "/v1/projects", {})[0], 400)

    def test_excluir(self):
        self.api("POST", "/v1/projects", {"name": "Temp"})
        self.api("POST", "/v1/memories", {"content": "algo no temp", "project": "temp", "entities": ["Rust"]})
        st, d = self.api("DELETE", "/v1/projects/temp")
        self.assertEqual((st, d["error"]["code"]), (409, "project_not_empty"))
        st, d = self.api("DELETE", "/v1/projects/temp?cascade=true")
        self.assertEqual((st, d["memories_deleted"]), (200, 1))
        self.assertEqual(self.api("GET", "/v1/projects/temp")[0], 404)
        chaves = [n["key"] for n in self.api("GET", "/v1/graph")[1]["nodes"]]
        self.assertNotIn("temp", chaves)          # nó do projeto some
        self.assertIn("rust", chaves)             # outros nós ficam, desvinculados
        self.assertEqual(self.api("GET", "/v1/graph/nodes/rust")[1]["projects"], [])

    def test_padrao_nao_exclui(self):
        self.assertEqual(self.api("DELETE", "/v1/projects/geral")[0], 400)


class TestHierarquia(Base):
    def montar(self):
        self.api("POST", "/v1/projects", {"name": "Empresa"})
        self.api("POST", "/v1/projects", {"name": "Site", "parent": "empresa"})
        self.api("POST", "/v1/projects", {"name": "Checkout", "parent": "Site"})
        self.api("POST", "/v1/memories", {"content": "Pablo gosta de respostas curtas"})              # geral
        self.api("POST", "/v1/memories", {"content": "A empresa usa Python no backend", "project": "empresa"})
        self.api("POST", "/v1/memories", {"content": "O site usa React", "project": "site"})
        self.api("POST", "/v1/memories", {"content": "O checkout usa Stripe", "project": "checkout"})

    def test_estrutura(self):
        self.montar()
        p = self.api("GET", "/v1/projects/checkout")[1]
        self.assertEqual((p["parent"], p["depth"]), ("site", 3))
        self.assertEqual([x["id"] for x in p["path"]], ["geral", "empresa", "site", "checkout"])
        ids = [x["id"] for x in self.api("GET", "/v1/projects")[1]["items"]]
        self.assertEqual(ids, ["geral", "empresa", "site", "checkout"])          # ordem de árvore
        arv = self.api("GET", "/v1/projects/tree")[1]
        self.assertEqual(arv["id"], "geral")
        self.assertEqual(arv["children"][0]["children"][0]["children"][0]["id"], "checkout")
        self.assertEqual(arv["children"][0]["total_memories"], 3)
        self.assertEqual(arv["total_memories"], 4)
        self.assertEqual(self.api("POST", "/v1/projects", {"name": "X", "parent": "nope"})[0], 404)

    def test_heranca(self):
        self.montar()
        d = self.api("GET", "/v1/memories?project=checkout&scope=inherit")[1]
        self.assertEqual(d["total"], 4)
        dist = {m["content"]: (m["distance"], m["inherited"]) for m in d["items"]}
        self.assertEqual(dist["O checkout usa Stripe"], (0, False))
        self.assertEqual(dist["Pablo gosta de respostas curtas"], (3, True))
        self.assertEqual(self.api("GET", "/v1/memories?project=checkout")[1]["total"], 1)        # exact
        self.assertEqual(self.api("GET", "/v1/memories?project=empresa&scope=tree")[1]["total"], 3)
        self.assertEqual(self.api("GET", "/v1/memories?project=site&scope=bad")[0], 400)
        # busca herda por padrão, e o próprio projeto pesa mais
        self.api("POST", "/v1/memories", {"content": "Pagamentos: Stripe na empresa", "project": "empresa"})
        r = self.api("POST", "/v1/memories/search", {"query": "stripe", "project": "checkout"})[1]
        self.assertEqual([m["content"] for m in r["items"]][:2],
                         ["O checkout usa Stripe", "Pagamentos: Stripe na empresa"])
        r = self.api("POST", "/v1/memories/search", {"query": "stripe", "project": "site", "scope": "exact"})[1]
        self.assertEqual(r["items"], [])
        ctx = self.api("POST", "/v1/context", {"query": "react python respostas", "project": "site"})[1]["text"]
        self.assertIn("# Project: Empresa › Site", ctx)
        self.assertIn("inherited from: Empresa, Geral", ctx)
        self.assertIn("A empresa usa Python no backend", ctx)

    def test_mover_e_ciclo(self):
        self.montar()
        self.assertEqual(self.api("PATCH", "/v1/projects/empresa", {"parent": "checkout"})[0], 400)
        self.assertEqual(self.api("PATCH", "/v1/projects/site", {"parent": "site"})[0], 400)
        self.assertEqual(self.api("PATCH", "/v1/projects/geral", {"parent": "site"})[0], 400)
        p = self.api("PATCH", "/v1/projects/checkout", {"parent": "empresa"})[1]
        self.assertEqual(p["parent"], "empresa")
        p = self.api("PATCH", "/v1/projects/checkout", {"parent": None})[1]
        self.assertEqual((p["parent"], p["depth"]), ("geral", 1))

    def test_grafo_hierarquia(self):
        self.montar()
        arestas = self.api("GET", "/v1/graph")[1]["edges"]
        hier = {(e["from"], e["to"]) for e in arestas if e["hierarchy"]}
        self.assertEqual(hier, {("empresa", "site"), ("site", "checkout")})
        self.api("PATCH", "/v1/projects/checkout", {"parent": "empresa"})
        hier = {(e["from"], e["to"]) for e in self.api("GET", "/v1/graph")[1]["edges"] if e["hierarchy"]}
        self.assertEqual(hier, {("empresa", "site"), ("empresa", "checkout")})
        sub = self.api("GET", "/v1/graph?project=empresa&scope=tree")[1]
        self.assertEqual({n["key"] for n in sub["nodes"]}, {"empresa", "site", "checkout"})

    def test_excluir_sobe_filhos(self):
        self.montar()
        d = self.api("DELETE", "/v1/projects/site?cascade=true")[1]
        self.assertEqual(d["children_moved"], ["checkout"])
        self.assertEqual(self.api("GET", "/v1/projects/checkout")[1]["parent"], "empresa")
        hier = {(e["from"], e["to"]) for e in self.api("GET", "/v1/graph")[1]["edges"] if e["hierarchy"]}
        self.assertEqual(hier, {("empresa", "checkout")})


class TestMemorias(Base):
    def test_crud(self):
        st, m = self.api("POST", "/v1/memories", {
            "content": "A loja usa Stripe", "project": "Loja", "type": "Decision",
            "tags": "pagamentos, web", "importance": 9, "entities": ["Stripe"], "source": "claude"})
        self.assertEqual(st, 201)
        self.assertEqual((m["project"], m["type"], m["importance"]), ("loja", "decision", 5))
        self.assertEqual(m["tags"], ["pagamentos", "web"])
        self.assertEqual(m["entities"], ["stripe"])
        st, dup = self.api("POST", "/v1/memories", {"content": "a loja usa stripe", "project": "loja"})
        self.assertEqual((st, dup["id"], dup["duplicate"]), (200, m["id"], True))
        st, m2 = self.api("PATCH", f"/v1/memories/{m['id']}", {"add_tags": ["infra"], "importance": 2})
        self.assertEqual((m2["tags"], m2["importance"]), (["pagamentos", "web", "infra"], 2))
        self.assertEqual(self.api("GET", f"/v1/memories/{m['id']}")[1]["content"], "A loja usa Stripe")
        self.assertEqual(self.api("DELETE", f"/v1/memories/{m['id']}")[0], 200)
        self.assertEqual(self.api("GET", f"/v1/memories/{m['id']}")[0], 404)

    def test_validacao(self):
        self.assertEqual(self.api("POST", "/v1/memories", {"content": ""})[0], 400)
        self.assertEqual(self.api("POST", "/v1/memories", {"content": "x" * 5000})[0], 400)
        self.assertEqual(self.api("POST", "/v1/memories", ["lista"])[0], 400)
        st, _ = self.api("POST", "/v1/memories", {"content": "x", "project": "nao-existe", "create_project": False})
        self.assertEqual(st, 404)

    def test_lote(self):
        st, d = self.api("POST", "/v1/memories/batch", {"items": [
            {"content": "um"}, {"content": ""}, {"content": "dois", "project": "p2"}]})
        self.assertEqual(st, 201)
        self.assertEqual(len(d["created"]), 2)
        self.assertEqual(d["errors"][0]["index"], 1)

    def test_listar_filtros(self):
        self.api("POST", "/v1/memories", {"content": "a", "tags": ["x"], "importance": 1})
        self.api("POST", "/v1/memories", {"content": "b", "tags": ["x", "y"], "importance": 5, "type": "task"})
        self.api("POST", "/v1/memories", {"content": "c", "expires_at": "2000-01-01T00:00:00"})
        d = self.api("GET", "/v1/memories")[1]
        self.assertEqual(d["total"], 2)               # a expirada não aparece
        d = self.api("GET", "/v1/memories?tag=x&tag=y")[1]
        self.assertEqual([m["content"] for m in d["items"]], ["b"])
        d = self.api("GET", "/v1/memories?sort=importance")[1]
        self.assertEqual(d["items"][0]["content"], "b")
        d = self.api("GET", "/v1/memories?type=task")[1]
        self.assertEqual(d["total"], 1)

    def test_busca(self):
        self.api("POST", "/v1/memories", {"content": "A loja usa Stripe para pagamentos"})
        self.api("POST", "/v1/memories", {"content": "O cliente se chama João"})
        st, d = self.api("POST", "/v1/memories/search", {"query": "como são os pagamentos?"})
        self.assertEqual(st, 200)
        self.assertFalse(d["semantic"])
        self.assertEqual([m["content"] for m in d["items"]], ["A loja usa Stripe para pagamentos"])
        d = self.api("GET", "/v1/memories?q=joao")[1]          # sem acento também acha
        self.assertEqual(d["items"][0]["content"], "O cliente se chama João")

    def test_contexto(self):
        self.api("POST", "/v1/projects", {"name": "Loja", "description": "e-commerce"})
        self.api("POST", "/v1/memories", {"content": "Pagamentos via Stripe", "project": "loja", "type": "decision"})
        self.api("POST", "/v1/graph/edges", {"from": "Stripe", "to": "Loja", "rel": "integra", "project": "loja"})
        st, d = self.api("POST", "/v1/context", {"query": "pagamentos", "project": "loja"})
        self.assertEqual(st, 200)
        self.assertIn("# Project: Loja", d["text"])
        self.assertIn("Pagamentos via Stripe", d["text"])
        self.assertIn("Stripe integra Loja", d["text"])

    def test_chat_usa_memorias(self):
        self.api("POST", "/v1/memories", {"content": "Pablo prefere Python"})
        self.assertIn("Pablo prefere Python", memstore.contexto_chat("eu gosto de python?"))


class TestGrafo(Base):
    def test_nos_e_arestas(self):
        self.api("POST", "/v1/projects", {"name": "Loja"})
        st, n = self.api("POST", "/v1/graph/nodes", {"label": "Stripe", "type": "ferramenta",
                                                     "project": "loja", "description": "pagamentos"})
        self.assertEqual((st, n["key"], n["pinned"]), (201, "stripe", True))
        st, n2 = self.api("POST", "/v1/graph/nodes", {"label": "stripe", "project": "loja"})
        self.assertEqual(n2["key"], "stripe")          # reaproveita o nó
        self.assertEqual(self.api("POST", "/v1/graph/nodes", {"label": "x", "project": "nope"})[0], 404)
        st, e = self.api("POST", "/v1/graph/edges", {"from": "Stripe", "to": "React", "rel": "usa"})
        self.assertEqual((st, e["from"], e["to"]), (201, "stripe", "react"))
        self.assertEqual(self.api("POST", "/v1/graph/edges", {"from": "a", "to": "A"})[0], 400)
        d = self.api("GET", "/v1/graph/nodes/Stripe")[1]
        self.assertEqual(d["outgoing"][0]["to_label"], "React")
        viz = self.api("GET", "/v1/graph/nodes/react/neighbors?depth=2")[1]
        self.assertEqual({n["key"] for n in viz["nodes"]}, {"react", "stripe"})
        sub = self.api("GET", "/v1/graph?project=loja")[1]
        self.assertEqual({n["key"] for n in sub["nodes"]}, {"loja", "stripe"})
        self.assertEqual(self.api("GET", "/v1/graph/nodes?q=stri")[1]["total"], 1)
        st, n = self.api("PATCH", "/v1/graph/nodes/react", {"type": "tecnologia", "add_project": "loja"})
        self.assertEqual((n["type"], n["projects"]), ("tecnologia", ["loja"]))
        self.assertEqual(self.api("DELETE", "/v1/graph/edges?from=react&to=stripe")[0], 200)
        self.assertEqual(self.api("DELETE", "/v1/graph/edges?from=react&to=stripe")[0], 404)
        self.assertEqual(self.api("DELETE", "/v1/graph/nodes/React")[0], 200)
        self.assertEqual(self.api("GET", "/v1/graph/nodes/react")[0], 404)

    def test_entidades_ligadas_ao_projeto(self):
        self.api("POST", "/v1/projects", {"name": "Loja"})
        self.api("POST", "/v1/memories", {"content": "Front em React", "project": "loja", "entities": ["React"]})
        sub = self.api("GET", "/v1/graph?project=loja")[1]
        self.assertIn({"from": "loja", "to": "react", "rel": "envolve", "weight": 1, "project": "loja",
                       "hierarchy": False}, sub["edges"])
        # memória no "geral" (sem nó de projeto) não cria aresta
        self.api("POST", "/v1/memories", {"content": "Gosto de Rust", "entities": ["Rust"]})
        self.assertEqual(self.api("GET", "/v1/graph/nodes/rust")[1]["incoming"], [])

    def test_no_com_memorias(self):
        st, m = self.api("POST", "/v1/memories", {"content": "Usamos Postgres", "entities": ["Postgres"]})
        d = self.api("GET", "/v1/graph/nodes/postgres")[1]
        self.assertEqual([x["id"] for x in d["memories"]], [m["id"]])
        self.api("DELETE", "/v1/graph/nodes/postgres")
        self.assertEqual(self.api("GET", f"/v1/memories/{m['id']}")[1]["entities"], [])

    def test_nos_fixos_nao_sao_limpos(self):
        self.api("POST", "/v1/graph/nodes", {"label": "Ferramenta"})     # palavra "genérica"
        g = skills.carregar_grafo()
        g["nos"]["jogo"] = {"label": "jogo", "tipo": "tema", "peso": 1, "visto": ""}
        skills._salvar_grafo(g)
        self.assertEqual(skills.limpar_genericos(), 1)
        self.assertIn("ferramenta", skills.carregar_grafo()["nos"])


class TestCofre(Base):
    def test_travado_responde_423(self):
        self.api("POST", "/v1/memories", {"content": "segredo"})
        self.assertTrue(cofre.ativar("senha123"))
        with open(memstore.STORE_FILE, "rb") as f:
            self.assertEqual(f.read(5), cofre.MAGIC)
        cofre.bloquear()
        st, d = self.api("GET", "/v1/memories")
        self.assertEqual((st, d["error"]["code"]), (423, "vault_locked"))
        self.assertEqual(self.api("POST", "/v1/memories", {"content": "x"})[0], 423)
        self.assertTrue(cofre.desbloquear("senha123"))
        self.assertEqual(self.api("GET", "/v1/memories")[1]["total"], 1)

    def test_backup_cifrado_restaura(self):
        self.api("POST", "/v1/memories", {"content": "memória do backup"})
        cofre.ativar("senha123")
        zipado = self.c.get("/api/backup/exportar", base_url=BASE).data
        self.api("DELETE", "/v1/memories/" + self.api("GET", "/v1/memories")[1]["items"][0]["id"])
        r = self.c.post("/api/backup/importar", base_url=BASE,
                        data={"arquivo": (io.BytesIO(zipado), "b.zip")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.api("GET", "/v1/memories")[1]["total"], 1)

    def test_backup_de_outra_senha_trava(self):
        cofre.ativar("senha123")
        self.api("POST", "/v1/memories", {"content": "de outro cofre"})
        zipado = self.c.get("/api/backup/exportar", base_url=BASE).data
        cofre.desativar("senha123")
        cofre.ativar("outra")
        token = memapi.token()
        r = self.c.post("/api/backup/importar", base_url=BASE,
                        data={"arquivo": (io.BytesIO(zipado), "b.zip")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["cofre"]["desbloqueado"])   # pede a senha do backup
        self.assertEqual(memapi.token(), token)                   # token das IAs preservado
        self.assertTrue(cofre.desbloquear("senha123"))
        self.assertEqual(self.api("GET", "/v1/memories")[1]["items"][0]["content"], "de outro cofre")

    def test_backup_cifrado_sem_config_recusa(self):
        cofre.ativar("senha123")
        self.api("POST", "/v1/memories", {"content": "x"})
        with open(memstore.STORE_FILE, "rb") as f:
            blob = f.read()
        cofre.desativar("senha123")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("memorias.json", blob)
        r = self.c.post("/api/backup/importar", base_url=BASE,
                        data={"arquivo": (io.BytesIO(buf.getvalue()), "b.zip")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_backup_invalido_nao_grava_nada(self):
        self.api("POST", "/v1/memories", {"content": "fica"})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("grafo.json", "{}")
            z.writestr("memorias.json", "não é json")
        r = self.c.post("/api/backup/importar", base_url=BASE,
                        data={"arquivo": (io.BytesIO(buf.getvalue()), "b.zip")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api("GET", "/v1/memories")[1]["total"], 1)


class TestMCP(Base):
    def setUp(self):
        super().setUp()
        self._api_orig = mcp_atlas._api

        def via_flask(metodo, caminho, corpo=None, query=None):
            if query:
                query = {k: v for k, v in query.items() if v not in (None, "")}
                if query:
                    from urllib.parse import urlencode
                    caminho += "?" + urlencode(query, doseq=True)
            st, d = self.api(metodo, caminho, corpo)
            if st >= 400:
                raise RuntimeError(f"Atlas API {st}: {d['error']['message']}")
            return d
        mcp_atlas._api = via_flask

    def tearDown(self):
        mcp_atlas._api = self._api_orig
        super().tearDown()

    def rpc(self, metodo, params=None, mid=1):
        return mcp_atlas._tratar({"jsonrpc": "2.0", "id": mid, "method": metodo, "params": params or {}})

    def chamar(self, nome, args):
        r = self.rpc("tools/call", {"name": nome, "arguments": args})
        return r["isError"], (json.loads(r["content"][0]["text"]) if not r["isError"] else r["content"][0]["text"])

    def test_handshake(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(r["protocolVersion"], "2025-03-26")
        self.assertEqual(self.rpc("initialize", {"protocolVersion": "1999"})["protocolVersion"], "2025-06-18")
        self.assertIsNone(mcp_atlas._tratar({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        nomes = [t["name"] for t in self.rpc("tools/list")["tools"]]
        self.assertIn("atlas_remember", nomes)
        self.assertIn("atlas_project_tree", nomes)
        self.assertTrue(all("run" not in t for t in self.rpc("tools/list")["tools"]))
        with self.assertRaises(NotImplementedError):
            self.rpc("nope")

    def test_fluxo(self):
        err, p = self.chamar("atlas_create_project", {"name": "Atlas API"})
        self.assertFalse(err)
        err, m = self.chamar("atlas_remember", {"content": "A API usa token", "project": "atlas-api",
                                                "entities": ["Flask"]})
        self.assertEqual((err, m["source"]), (False, "mcp"))
        err, ctx = self.chamar("atlas_get_context", {"query": "token"})
        self.assertIn("A API usa token", ctx["text"])
        err, viz = self.chamar("atlas_graph_node", {"node": "Atlas API", "depth": 1})
        self.assertEqual(viz["center"], "atlas api")
        err, _ = self.chamar("atlas_graph_link", {"from": "Atlas API", "to": "Flask", "rel": "usa"})
        self.assertFalse(err)
        err, _ = self.chamar("atlas_update_memory", {"id": m["id"], "importance": 5})
        self.assertFalse(err)
        err, msg = self.chamar("atlas_forget", {"id": "mem_nope"})
        self.assertTrue(err)
        self.assertIn("404", msg)
        err, sp = self.chamar("atlas_create_project", {"name": "MCP", "parent": "atlas-api"})
        self.assertEqual(sp["parent"], "atlas-api")
        err, arv = self.chamar("atlas_project_tree", {})
        self.assertEqual(arv["children"][0]["children"][0]["id"], "mcp")
        err, ctx = self.chamar("atlas_get_context", {"query": "token", "project": "mcp"})
        self.assertIn("A API usa token", ctx["text"])                 # herdada do pai
        err, d = self.chamar("atlas_delete_project", {"id": "atlas-api", "cascade": True})
        self.assertEqual(d["memories_deleted"], 1)


class TestRobustez(Base):
    """Correções da revisão de bugs: segredos fora da UI, gravação atômica e validação de entrada."""

    def test_estado_nao_vaza_token_nem_material_do_cofre(self):
        memapi.token()                                   # garante um api_token no config
        d = self.c.get("/api/estado").get_json()
        for campo in ("api_token", "cripto_salt", "cripto_verif"):
            self.assertNotIn(campo, d["config"])
        self.assertNotIn(memapi.token(), json.dumps(d))
        d2 = self.c.post("/api/config", json={"tema": "claro"}).get_json()
        self.assertNotIn("api_token", d2["config"])
        self.assertEqual(d2["config"]["tema"], "claro")
        self.assertTrue(memapi.token())                  # e o token continua no arquivo

    def test_salvar_config_atomico_com_leitores_concorrentes(self):
        import threading
        tk = memapi.token()
        parar, falhas = threading.Event(), []

        def leitor():
            while not parar.is_set():
                if core.carregar_config().get("api_token") != tk:
                    falhas.append(1)
                    return

        ths = [threading.Thread(target=leitor) for _ in range(4)]
        for t in ths:
            t.start()
        for i in range(150):
            cfg = core.carregar_config()
            cfg["nome"] = f"n{i}"
            core.salvar_config(cfg)
        parar.set()
        for t in ths:
            t.join()
        self.assertEqual(falhas, [])
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".tmp")], [])

    def test_config_com_valores_estranhos_nao_quebra(self):
        for corpo in ({"obs_intervalo": float("nan")}, {"obs_intervalo": float("inf")},
                      {"iniciativa_intervalo": "10"}, {"obs_intervalo": True}, {"habilidades": ["x"]}):
            r = self.c.post("/api/config", data=json.dumps(corpo), content_type="application/json")
            self.assertEqual(r.status_code, 200, corpo)
        r = self.c.post("/api/config", json=[1, 2, 3])          # corpo que nao e objeto
        self.assertEqual(r.status_code, 200)
        r = self.c.post("/api/config", json={"obs_intervalo": 99999})
        self.assertEqual(r.get_json()["config"]["obs_intervalo"], 900)

    def test_chat_valida_entrada(self):
        self.assertEqual(self.c.post("/chat", json=[1]).status_code, 400)
        self.assertEqual(self.c.post("/chat", json={"texto": 5}).status_code, 400)
        self.assertEqual(self.c.post("/chat", json={"texto": "", "anexos": ["x", 3]}).status_code, 400)

    def test_backup_com_config_invalido(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("config.json", "[1, 2]")
        buf.seek(0)
        r = self.c.post("/api/backup/importar", data={"arquivo": (buf, "b.zip")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_entidades_como_texto_e_projeto_nao_textual(self):
        st, m = self.api("POST", "/v1/memories", {"content": "Pagamentos via Stripe", "entities": "Stripe"})
        self.assertEqual(st, 201)
        self.assertEqual(len(m["entities"]), 1)           # antes: um no por letra
        st, _ = self.api("POST", "/v1/memories", {"content": "outra", "project": 123})
        self.assertEqual(st, 201)
        st, _ = self.api("POST", "/v1/memories", {"content": "terceira", "entities": 7})
        self.assertEqual(st, 201)

    def test_erro_inesperado_vira_json(self):
        orig = memstore.projetos_listar
        memstore.projetos_listar = lambda: 1 / 0
        try:
            r = self.c.get("/v1/projects", base_url=BASE, headers={"Authorization": "Bearer " + memapi.token()})
        finally:
            memstore.projetos_listar = orig
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["error"]["code"], "internal_error")
        self.assertNotIn("division", json.dumps(r.get_json()))


if __name__ == "__main__":
    unittest.main()
