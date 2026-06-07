"""
cofre — criptografia em repouso dos arquivos pessoais (memória, conversas,
grafo, observações, lembretes).

Tudo com a biblioteca padrão do Python (hashlib/hmac/secrets). Nenhuma DLL
nativa de terceiros, pra não esbarrar no Smart App Control.

Construção: a senha vira uma chave via scrypt (lento de propósito, anti força
bruta). Com a chave faz-se cifragem autenticada "encrypt-then-MAC":
  - keystream = HMAC-SHA256(chave_enc, nonce || contador), em blocos de 32 bytes
  - texto cifrado = texto XOR keystream
  - tag = HMAC-SHA256(chave_mac, nonce || cifrado)  (detecta senha errada/adulteração)
A senha NUNCA é gravada. Só fica na memória durante a sessão desbloqueada.
"""
import os
import json
import hmac
import base64
import struct
import secrets
import hashlib
import threading

import core

MAGIC = b"ATLZ1"
_VERIF = b"atlas-ok"                      # texto conhecido pra validar a senha
_sessao = {"chave": None}                 # chave derivada (64 bytes) — só em memória
_lock = threading.Lock()


def _protegidos():
    return [
        core.MEM_FILE, core.CONVERSAS_FILE, core.GRAFO_FILE, core.OBS_FILE,
        os.path.join(core.BASE_DIR, "lembretes.json"),
    ]


# ── primitivas ────────────────────────────────────────────────────────────────
def _derivar(senha, salt):
    return hashlib.scrypt(senha.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=64)


def _keystream(chave_enc, nonce, n):
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hmac.new(chave_enc, nonce + struct.pack(">Q", i), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def _xor(dados, ks):
    if not dados:
        return b""
    return (int.from_bytes(dados, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(dados), "big")


def cifrar(texto: bytes, chave: bytes) -> bytes:
    enc, mac = chave[:32], chave[32:]
    nonce = secrets.token_bytes(16)
    ct = _xor(texto, _keystream(enc, nonce, len(texto)))
    tag = hmac.new(mac, nonce + ct, hashlib.sha256).digest()
    return MAGIC + nonce + tag + ct


def decifrar(blob: bytes, chave: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("formato inválido")
    enc, mac = chave[:32], chave[32:]
    nonce, tag, ct = blob[5:21], blob[21:53], blob[53:]
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + ct, hashlib.sha256).digest()):
        raise ValueError("senha incorreta ou arquivo adulterado")
    return _xor(ct, _keystream(enc, nonce, len(ct)))


# ── estado da sessão ──────────────────────────────────────────────────────────
def ligada():
    return bool(core.carregar_config().get("cripto"))


def desbloqueado():
    return _sessao["chave"] is not None


def disponivel():
    """True se os dados podem ser lidos/gravados agora (cripto desligada ou destravada)."""
    return (not ligada()) or desbloqueado()


def bloquear():
    _sessao["chave"] = None


def desbloquear(senha: str) -> bool:
    cfg = core.carregar_config()
    if not cfg.get("cripto"):
        return True
    try:
        salt = bytes.fromhex(cfg.get("cripto_salt", ""))
        verif = base64.b64decode(cfg.get("cripto_verif", ""))
        chave = _derivar(senha, salt)
        if decifrar(verif, chave) == _VERIF:
            _sessao["chave"] = chave
            return True
    except Exception:
        pass
    return False


# ── leitura/escrita central (transparente) ────────────────────────────────────
def ler_json(caminho, default):
    """Lê JSON, decifrando se preciso. Se estiver cifrado e travado, devolve o default."""
    try:
        with open(caminho, "rb") as f:
            raw = f.read()
    except Exception:
        return default
    if raw[:5] == MAGIC:
        if _sessao["chave"] is None:
            return default                 # travado: não dá pra ler
        try:
            raw = decifrar(raw, _sessao["chave"])
        except Exception:
            return default
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return default


def salvar_json(caminho, obj, indent=2):
    """Grava JSON, cifrando se a cripto estiver ligada. Se travado, NÃO grava
    (evita sobrescrever os dados cifrados com conteúdo vazio)."""
    if ligada() and not desbloqueado():
        return False                       # travado: protege o que está no disco
    data = json.dumps(obj, ensure_ascii=False, indent=indent).encode("utf-8")
    if ligada():
        data = cifrar(data, _sessao["chave"])
    tmp = caminho + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, caminho)
    return True


# ── ligar / desligar a criptografia ───────────────────────────────────────────
def ativar(senha: str) -> bool:
    """Liga a cripto: deriva a chave, guarda salt+verificador e cifra os arquivos atuais."""
    if not senha or len(senha) < 4:
        return False
    with _lock:
        # lê tudo em claro ANTES de ligar
        atuais = {p: ler_json(p, None) for p in _protegidos()}
        salt = secrets.token_bytes(16)
        chave = _derivar(senha, salt)
        _sessao["chave"] = chave
        cfg = core.carregar_config()
        cfg["cripto"] = True
        cfg["cripto_salt"] = salt.hex()
        cfg["cripto_verif"] = base64.b64encode(cifrar(_VERIF, chave)).decode("ascii")
        core.salvar_config(cfg)
        # regrava cada arquivo já cifrado
        for p, obj in atuais.items():
            if obj is not None:
                salvar_json(p, obj)
    return True


def desativar(senha: str) -> bool:
    """Desliga a cripto: confere a senha, decifra tudo e volta a texto puro."""
    if not desbloquear(senha):
        return False
    with _lock:
        atuais = {p: ler_json(p, None) for p in _protegidos()}
        cfg = core.carregar_config()
        cfg["cripto"] = False
        cfg.pop("cripto_salt", None)
        cfg.pop("cripto_verif", None)
        core.salvar_config(cfg)
        _sessao["chave"] = None
        for p, obj in atuais.items():       # agora salvar_json grava em claro
            if obj is not None:
                salvar_json(p, obj)
    return True


def estado():
    return {"ligada": ligada(), "desbloqueado": desbloqueado()}
