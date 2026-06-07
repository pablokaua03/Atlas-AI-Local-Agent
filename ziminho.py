"""
ziminho — leitor mínimo de arquivos ZIM em Python PURO (sem libzim, sem DLL nativa).
Acha um artigo por título (ignorando acento/caixa) e devolve o HTML. Usa um índice
próprio em SQLite pra não depender da colação interna do ZIM.
"""
import struct
import unicodedata
import sqlite3

try:
    from compression import zstd as _zstd        # Python 3.14+
except Exception:
    _zstd = None
import lzma as _lzma

_HDR = "<IHH16sIIQQQQIIQ"
_MAGIC = 72173914


def _fold(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return s.lower()


class ZimReader:
    def __init__(self, caminho, indice=None):
        self.f = open(caminho, "rb")
        h = struct.unpack(_HDR, self.f.read(80))
        if h[0] != _MAGIC:
            raise ValueError("não é um ZIM válido")
        (self.artc, self.cluc, self.urlp, self.titp,
         self.clup, self.mimep, self.main, _layout, self.chks) = h[4:13]
        self.indice_path = indice or (caminho + ".idx.db")
        self._db = None

    def _entry_ptr(self, idx):
        self.f.seek(self.urlp + idx * 8)
        return struct.unpack("<Q", self.f.read(8))[0]

    def _read_entry(self, idx):
        self.f.seek(self._entry_ptr(idx))
        mt = struct.unpack("<H", self.f.read(2))[0]
        self.f.read(1)
        ns = chr(self.f.read(1)[0])
        struct.unpack("<I", self.f.read(4))[0]
        if mt == 0xffff:
            kind = ("redirect", struct.unpack("<I", self.f.read(4))[0])
        else:
            cl = struct.unpack("<I", self.f.read(4))[0]
            bl = struct.unpack("<I", self.f.read(4))[0]
            kind = ("article", cl, bl)
        rest = self.f.read(2048)
        z1 = rest.index(b"\x00")
        url = rest[:z1].decode("utf-8", "replace")
        z2 = rest.index(b"\x00", z1 + 1)
        title = rest[z1 + 1:z2].decode("utf-8", "replace")
        return ns, url, title, kind

    def _cluster_offset(self, cl):
        self.f.seek(self.clup + cl * 8)
        return struct.unpack("<Q", self.f.read(8))[0]

    def _blob(self, cl, bl):
        coff = self._cluster_offset(cl)
        nxt = self._cluster_offset(cl + 1) if cl + 1 < self.cluc else self.chks
        self.f.seek(coff)
        info = self.f.read(1)[0]
        comp, ext = info & 0x0f, bool(info & 0x10)
        raw = self.f.read(nxt - coff - 1)
        if comp == 1:
            data = raw
        elif comp == 4:
            data = _lzma.decompress(raw)
        elif comp == 5:
            if _zstd is None:
                raise RuntimeError("zstd indisponível")
            data = _zstd.decompress(raw)
        else:
            raise ValueError(f"compressão {comp}")
        osz, fmt = (8, "<Q") if ext else (4, "<I")
        s = struct.unpack(fmt, data[bl * osz:bl * osz + osz])[0]
        e = struct.unpack(fmt, data[(bl + 1) * osz:(bl + 1) * osz + osz])[0]
        return data[s:e]

    # ── índice próprio ────────────────────────────────────────────────────────
    def _db_(self):
        if self._db is None:
            self._db = sqlite3.connect(self.indice_path)
        return self._db

    def indice_pronto(self):
        try:
            return self._db_().execute("SELECT 1 FROM entradas LIMIT 1").fetchone() is not None
        except Exception:
            return False

    def construir_indice(self):
        db = self._db_()
        db.execute("DROP TABLE IF EXISTS entradas")
        db.execute("CREATE TABLE entradas (chave TEXT, idx INTEGER)")
        self.f.seek(self.urlp)
        ptrs = struct.unpack(f"<{self.artc}Q", self.f.read(self.artc * 8))
        lote = []
        for i, p in enumerate(ptrs):
            self.f.seek(p)
            buf = self.f.read(320)
            mt = buf[0] | (buf[1] << 8)
            if chr(buf[3]) != "C":
                continue
            off = 16 if mt != 0xffff else 12
            z = buf.find(b"\x00", off)
            if z < 0:
                continue
            url = buf[off:z].decode("utf-8", "replace")
            lote.append((_fold(url), i))
            if len(lote) >= 8000:
                db.executemany("INSERT INTO entradas VALUES (?,?)", lote)
                lote.clear()
        if lote:
            db.executemany("INSERT INTO entradas VALUES (?,?)", lote)
        db.execute("CREATE INDEX ix_chave ON entradas(chave)")
        db.commit()

    def artigo(self, titulo, max_redir=5):
        if not self.indice_pronto():
            return None
        chave = _fold(titulo.strip().replace(" ", "_"))
        idxs = [r[0] for r in self._db_().execute(
            "SELECT idx FROM entradas WHERE chave=?", (chave,)).fetchall()]
        for start in idxs:
            idx = start
            for _ in range(max_redir):
                ns, u, t, k = self._read_entry(idx)
                if k[0] == "redirect":
                    idx = k[1]
                    continue
                return self._blob(k[1], k[2]).decode("utf-8", "replace")
        return None
