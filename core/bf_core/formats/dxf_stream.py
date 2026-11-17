"""Streaming tokenizer of ASCII DXF: group code / value pairs in blocks of whole records (ТЗ 9.5, 9.12; ADR 0011 §2).

A DXF file is lines in pairs: a group code (an integer) and its value. A pair with code 0 starts a record — a section
mark, a table entry or an entity. `DxfStream(path)` reads the file in blocks cut between records and gives every
block as columns (`Block`), so a reader works on arrays, not on a Python object per entity:
  * `code`, `start`, `end` — the group code of every pair and the byte range of its value in `raw`;
  * `num` — the value of a numeric group as float64 (NaN for text; `bad` marks a numeric group whose value is not
    a number). Numbers are converted by Arrow (correctly rounded, the same double as `float()`): coordinates
    written with `repr` come back bit for bit;
  * `z`, `kind`, `section` — the first pair, the kind (`KINDS`, −1 = another name) and the section of every record;
  * `col(code)` — for every record the pair that holds the group (−1 = absent), `intern` — values as small ids.
The scan of the bytes is one function, `_scan`: plain Python for a small file (numba is not loaded) and the same
function compiled by numba for a big one. Memory is the block plus its columns; one record longer than
`max_record_bytes` is refused. `version`, `codepage` and `text()` (code page of `$DWGCODEPAGE`, UTF-8 from AC1021,
`\\U+XXXX`) come from the HEADER section, which precedes everything that needs them.
Error codes: empty_file, not_dxf, unsupported (DWG), binary, broken, truncated, too_large.

Ported from BF `formats/dxf_stream.py` (`_pairs`: blocks of pairs). Differences: columns instead of a Python loop over
pairs, sections and the code page, progress and cancellation, limits, Russian `FormatError`s; binary DXF and files
with a broken structure are left to `ezdxf.recover` (`dxf_lines.read_dxf`).
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, no_progress

BLOCK_BYTES = 8 << 20
MAX_RECORD_BYTES = 256 << 20  # one record (an LWPOLYLINE of ~7 million vertices)
SMALL_BYTES = 64 << 10  # a file up to this size is scanned in Python: numba is not loaded for it
BINARY_MAGIC = b"AutoCAD Binary DXF\r\n\x1a\x00"
DEFAULT_CODEC = "cp1251"

KINDS = (
    "SECTION", "ENDSEC", "EOF", "LAYER", "LINE", "POINT", "LWPOLYLINE", "POLYLINE", "VERTEX", "SEQEND",
    "TEXT", "MTEXT", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "INSERT", "3DFACE", "MESH",
)  # fmt: skip
(
    K_SECTION, K_ENDSEC, K_EOF, K_LAYER, K_LINE, K_POINT, K_LWPOLYLINE, K_POLYLINE, K_VERTEX, K_SEQEND,
    K_TEXT, K_MTEXT, K_ARC, K_CIRCLE, K_ELLIPSE, K_SPLINE, K_INSERT, K_3DFACE, K_MESH,
) = range(len(KINDS))  # fmt: skip
K_OTHER = -1
SECTIONS = ("", "HEADER", "CLASSES", "TABLES", "BLOCKS", "ENTITIES", "OBJECTS")
S_NONE, S_HEADER, S_CLASSES, S_TABLES, S_BLOCKS, S_ENTITIES, S_OBJECTS, S_OTHER = range(8)

_NAME_LEN = np.array([len(k) for k in KINDS], dtype=np.int64)
_NAMES = np.zeros((len(KINDS), int(_NAME_LEN.max())), dtype=np.uint8)
for _i, _k in enumerate(KINDS):
    _NAMES[_i, : len(_k)] = np.frombuffer(_k.encode("ascii"), dtype=np.uint8)
_UNICODE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")
_HINT = "Сохраните чертёж заново как DXF (в AutoCAD: «Сохранить как» → AutoCAD DXF) и повторите загрузку."


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


def _scan(buf, n, final, names, name_len, code, start, end, kind, state, pk, poff, npair):  # type: ignore[no-untyped-def]
    """Pairs of buf[:n] into the arrays. Returns (pairs, pos, last0, last0_byte, status, numbers).

    `pos` — the first byte not taken; `last0`, `last0_byte` — the last record start (pair and byte); `state` —
    0 text, 1 a number (its text is appended to `pk`, ends in `poff`, pair in `npair`), 2 a numeric group whose
    value is not a number. status: 0 — the buffer ran out, 1 — the EOF record was read, 2 — the line at `pos`
    is not a group code, 3 — the last group code has no value (`final` only).
    Runs as plain Python (buf — bytes) and compiled by numba (buf — uint8 array): every byte is read with int().
    """
    cap = len(code)
    pairs = 0
    count = 0
    plen = 0
    pos = 0
    last0 = -1
    last0_byte = 0
    status = 0
    while pairs < cap:
        e = pos
        while e < n and int(buf[e]) != 10:
            e += 1
        if e >= n and not final:
            break
        p = pos
        q = e
        while p < q and (int(buf[p]) == 32 or int(buf[p]) == 9):
            p += 1
        while q > p and (int(buf[q - 1]) == 32 or int(buf[q - 1]) == 9 or int(buf[q - 1]) == 13):
            q -= 1
        if p == q:  # a blank line where a group code is expected: fine only as the tail of the file
            j = e
            while j < n and (int(buf[j]) <= 32):
                j += 1
            if j < n:
                status = 2
            elif final:
                pos = n
            break
        c = 0
        ok = q - p <= 5
        if ok:
            for j in range(p, q):
                d = int(buf[j]) - 48
                if d < 0 or d > 9:
                    ok = False
                    break
                c = c * 10 + d
        if not ok or c > 32767:
            status = 2
            break
        va = e + 1
        if va >= n:
            if final:
                status = 3
            break
        ve = va
        while ve < n and int(buf[ve]) != 10:
            ve += 1
        if ve >= n and not final:
            break
        nxt = ve + 1 if ve < n else n
        vb = ve
        if vb > va and int(buf[vb - 1]) == 13:
            vb -= 1
        numeric = (
            (10 <= c <= 99)
            or (110 <= c <= 149)
            or (160 <= c <= 179)
            or (210 <= c <= 239)
            or (270 <= c <= 299)
            or (370 <= c <= 389)
            or (400 <= c <= 409)
            or (420 <= c <= 429)
            or (440 <= c <= 469)
            or (1010 <= c <= 1071)
        )
        s = va
        t = vb
        if c == 0 or numeric:
            while s < t and (int(buf[s]) == 32 or int(buf[s]) == 9):
                s += 1
            while t > s and (int(buf[t - 1]) == 32 or int(buf[t - 1]) == 9):
                t -= 1
        code[pairs] = c
        start[pairs] = s
        end[pairs] = t
        state[pairs] = 0
        kind[pairs] = -2
        if c == 0:
            k = -1
            length = t - s
            for i in range(names.shape[0]):
                if int(name_len[i]) == length:
                    same = True
                    for j in range(length):
                        if int(buf[s + j]) != int(names[i, j]):
                            same = False
                            break
                    if same:
                        k = i
                        break
            kind[pairs] = k
            last0 = pairs
            last0_byte = pos
            pairs += 1
            pos = nxt
            if k == 2:  # K_EOF
                status = 1
                break
            continue
        if numeric:
            j = s
            if j < t and (int(buf[j]) == 43 or int(buf[j]) == 45):
                j += 1
            digits = 0
            while j < t and 48 <= int(buf[j]) <= 57:
                j += 1
                digits += 1
            if j < t and int(buf[j]) == 46:
                j += 1
                while j < t and 48 <= int(buf[j]) <= 57:
                    j += 1
                    digits += 1
            valid = digits > 0
            if valid and j < t and (int(buf[j]) == 101 or int(buf[j]) == 69):
                j += 1
                if j < t and (int(buf[j]) == 43 or int(buf[j]) == 45):
                    j += 1
                digits = 0
                while j < t and 48 <= int(buf[j]) <= 57:
                    j += 1
                    digits += 1
                valid = digits > 0
            if valid and j == t:
                state[pairs] = 1
                for j in range(s, t):
                    pk[plen] = int(buf[j])
                    plen += 1
                npair[count] = pairs
                count += 1
                poff[count] = plen
            else:
                state[pairs] = 2
        pairs += 1
        pos = nxt
    return pairs, pos, last0, last0_byte, status, count


_compiled: Any = None


def _kernel() -> Any:
    """`_scan` compiled by numba (cached on disk); loaded on the first big file."""
    global _compiled
    if _compiled is None:
        from bf_core.jit import njit

        _compiled = njit(cache=True, nogil=True)(_scan)
    return _compiled


def _numbers(pk: np.ndarray, poff: np.ndarray, count: int) -> np.ndarray:
    """Texts of numbers packed one after another → float64, correctly rounded (as `float()`)."""
    if count == 0:
        return np.empty(0, dtype=np.float64)
    import pyarrow as pa
    import pyarrow.compute as pc

    offsets = np.ascontiguousarray(poff[: count + 1])
    data = np.ascontiguousarray(pk[: int(offsets[-1])])
    try:
        cells = pa.Array.from_buffers(pa.string(), count, [None, pa.py_buffer(offsets), pa.py_buffer(data)])
        return np.asarray(pc.cast(cells, pa.float64()).to_numpy(zero_copy_only=False), dtype=np.float64)
    except pa.ArrowInvalid:  # a form Arrow does not take: the same grammar by float()
        raw = data.tobytes()
        return np.array([float(raw[a:b]) for a, b in zip(offsets[:-1].tolist(), offsets[1:].tolist(), strict=True)])


def codec_of(version: str | None, codepage: str | None) -> str | None:
    """Python codec of the texts of a drawing; None — the file does not say (or names an unknown code page)."""
    if version is not None and re.fullmatch(r"AC\d{4}", version) and version >= "AC1021":
        return "utf-8"
    page = (codepage or "").strip().upper()
    if page in ("UTF-8", "UTF8"):
        return "utf-8"
    m = re.search(r"(\d{3,5})$", page)
    if m is None:
        return None
    name = {"936": "gbk"}.get(m.group(1), "cp" + m.group(1))
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


class Block:
    """Whole records of a part of the file as columns (see the module docstring)."""

    def __init__(
        self,
        raw: bytes,
        code: np.ndarray,
        start: np.ndarray,
        end: np.ndarray,
        num: np.ndarray,
        bad: np.ndarray,
        kind: np.ndarray,
        first: int,
        pair0: int,
    ) -> None:
        self.raw, self.code, self.start, self.end, self.num, self.bad = raw, code, start, end, num, bad
        zero = code == 0
        self.z = np.flatnonzero(zero)  # the first pair of every record
        self.kind = kind[self.z]
        self.rec = np.cumsum(zero, dtype=np.int64) - 1  # the record of every pair
        self.first = first  # the number of the first record in the file
        self.pair0 = pair0  # the number of the first pair in the file
        self.section = np.zeros(len(self.z), dtype=np.int8)
        self._cols: dict[int, np.ndarray] = {}
        cut = np.flatnonzero(code == 101)  # "Embedded Object": the groups after it are not the entity's own
        self._cut: np.ndarray | None = None
        if len(cut):
            self._cut = np.full(len(self.z), len(code), dtype=np.int64)
            self._cut[self.rec[cut[::-1]]] = cut[::-1]

    def __len__(self) -> int:
        return len(self.z)

    def pairs(self, code: int, records: np.ndarray | None = None) -> np.ndarray:
        """Pairs with the group code, in file order; `records` — a bool mask of the records to look in."""
        p = np.flatnonzero(self.code == code)
        if len(p) and self._cut is not None:
            p = p[p < self._cut[self.rec[p]]]
        if len(p) and records is not None:
            p = p[records[self.rec[p]]]
        return p

    def col(self, code: int) -> np.ndarray:
        """For every record the pair with the group code (the last one when repeated), −1 when it has none."""
        out = self._cols.get(code)
        if out is None:
            out = self._cols[code] = np.full(len(self.z), -1, dtype=np.int64)
            p = self.pairs(code)
            out[self.rec[p]] = p
        return out

    def values(self, p: np.ndarray, default: float = np.nan) -> np.ndarray:
        """Numbers of the pairs `p`; `default` where p is −1. A value that is not a number is NaN."""
        if len(self.num) == 0:
            return np.full(len(p), default, dtype=np.float64)
        return np.where(p >= 0, self.num[np.maximum(p, 0)], default)

    def value(self, p: int) -> bytes:
        return self.raw[int(self.start[p]) : int(self.end[p])]

    def line(self, p: int) -> int:
        """The line of the file that holds the value of pair `p` (1-based)."""
        return 2 * (self.pair0 + int(p)) + 2

    def intern(self, p: np.ndarray, table: dict[bytes, int], default: bytes = b"") -> np.ndarray:
        """Values of the pairs `p` (stripped) as ids of `table`, which grows; −1 in `p` stands for `default`."""
        raw, get = self.raw, table.setdefault
        out = np.empty(len(p), dtype=np.int32)
        have = p >= 0
        s, e = self.start[p[have]].tolist(), self.end[p[have]].tolist()
        out[have] = np.fromiter(
            (get(raw[a:b].strip(), len(table)) for a, b in zip(s, e, strict=True)), dtype=np.int32, count=len(s)
        )
        if not have.all():
            out[~have] = get(default, len(table))
        return out


class DxfStream:
    """Blocks of an ASCII DXF file in file order; iterate once. Raises `FormatError` (see the module docstring).
    `salvage`: a file cut short is not an error — its last record, which may be incomplete, is left out."""

    def __init__(
        self,
        path: str | Path,
        progress: Progress | None = None,
        *,
        block_bytes: int | None = None,
        max_record_bytes: int | None = None,
        salvage: bool = False,
    ) -> None:
        self.path = Path(path)
        self.salvage = salvage  # a file without the EOF record is read up to its last whole record
        self.truncated = False  # … and this says so
        self.tick: Progress = progress or no_progress
        self.block_bytes = max(int(block_bytes or BLOCK_BYTES), 16)
        self.max_record_bytes = int(max_record_bytes or MAX_RECORD_BYTES)
        self.size = self.path.stat().st_size
        self.version: str | None = None  # $ACADVER
        self.codepage: str | None = None  # $DWGCODEPAGE
        self.records = 0
        self.pairs = 0
        self.ended = False  # the EOF record was read
        self.replaced = 0  # characters that could not be decoded
        self.fraction = 0.0  # of the file read so far
        self._section = S_NONE
        self._codec: str | None = None

    # --- texts ---------------------------------------------------------------------------------------

    @property
    def encoding(self) -> str:
        """The codec of the texts: by the header, else `DEFAULT_CODEC` (see `encoding_known`)."""
        return codec_of(self.version, self.codepage) or DEFAULT_CODEC

    @property
    def encoding_known(self) -> bool:
        return codec_of(self.version, self.codepage) is not None

    def text(self, raw: bytes) -> str:
        """A string value decoded; without a code page in the header — UTF-8 when it is valid UTF-8."""
        if self._codec is None:
            self._codec = self.encoding
        try:
            out = raw.decode(self._codec) if self.encoding_known or raw.isascii() else raw.decode("utf-8")
        except UnicodeDecodeError:
            out = raw.decode(self._codec, errors="replace")
            self.replaced += out.count("\ufffd")
        if "\\U+" in out:
            out = _UNICODE.sub(lambda m: chr(int(m.group(1), 16)), out)
            if any("\ud800" <= ch <= "\udfff" for ch in out):  # a character beyond U+FFFF as two UTF-16 units
                out = out.encode("utf-16", errors="surrogatepass").decode("utf-16", errors="replace")
        return out

    def alive(self) -> None:
        """A tick without news: a long calculation on a block stays cancellable."""
        self.tick(self.fraction, None)

    # --- reading -------------------------------------------------------------------------------------

    def _open(self, head: bytes) -> int:
        """Checks the start of the file; returns the bytes to skip (a UTF-8 mark)."""
        if self.size == 0:
            raise fail("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
        if head.startswith(BINARY_MAGIC[:18]):
            raise fail("binary", "Это двоичный DXF: он читается только в режиме восстановления.")
        if re.match(rb"AC\d\.\d|AC1\d{3}", head) or head.startswith(b"MC0.0"):
            raise fail("unsupported", "Это чертёж DWG, а не DXF: формат DWG не поддерживается.")
        skip = 3 if head.startswith(b"\xef\xbb\xbf") else 0
        if head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in head:
            raise fail("not_dxf", "Файл не похож на DXF: это не текстовый файл.")
        return skip

    def __iter__(self) -> Iterator[Block]:
        size = self.size
        with self.path.open("rb") as f:
            done = self._open(f.read(4096))
            f.seek(done)
            self.tick(0.0, "Чтение DXF")
            carry = b""
            want = self.block_bytes
            while not self.ended:
                chunk = f.read(want)
                final = len(chunk) < want
                done += len(chunk)
                data = carry + chunk if carry else chunk
                if not data:
                    break
                cap = (data.count(b"\n") + 1) // 2 + 2
                code = np.empty(cap, dtype=np.int16)
                start = np.empty(cap, dtype=np.int32)
                end = np.empty(cap, dtype=np.int32)
                kind = np.empty(cap, dtype=np.int8)
                state = np.empty(cap, dtype=np.uint8)
                pk = np.empty(len(data), dtype=np.uint8)
                poff = np.zeros(cap + 1, dtype=np.int32)
                npair = np.empty(cap, dtype=np.int32)
                if size <= SMALL_BYTES:
                    found = _scan(
                        data, len(data), final, _NAMES, _NAME_LEN, code, start, end, kind, state, pk, poff, npair
                    )
                else:
                    buf = np.frombuffer(data, dtype=np.uint8)
                    found = _kernel()(
                        buf, len(data), final, _NAMES, _NAME_LEN, code, start, end, kind, state, pk, poff, npair
                    )
                pairs, _pos, last0, last0_byte, status, count = (int(v) for v in found)
                if status == 2:
                    self._bad_code(data, pairs, _pos)
                use = pairs if status == 1 or (final and not self.salvage) else last0
                lo = 0
                if self.pairs == 0 and pairs:  # the start of the file: comments (999) may precede the first record
                    other = code[:pairs] != 999
                    lo = int(np.argmax(other)) if other.any() else pairs
                    if lo < pairs and (code[lo] != 0 or kind[lo] != K_SECTION):
                        raise fail("not_dxf", "Файл не похож на DXF: он не начинается с раздела SECTION.")
                if use <= lo:
                    if status == 1 or final:
                        break
                    if len(data) > self.max_record_bytes:
                        raise fail(
                            "too_large",
                            f"Объект в строке {n_text(2 * self.pairs + 1)} занимает больше "
                            f"{self.max_record_bytes >> 20} МБ.",
                            "Разделите объект на части в исходной программе и сохраните файл заново.",
                        )
                    carry, want = data, max(self.block_bytes, len(data))
                    continue
                carry, want = (b"" if use == pairs else data[last0_byte:]), self.block_bytes
                k = int(np.searchsorted(npair[:count], use))
                num = np.full(use, np.nan)
                num[npair[:k]] = _numbers(pk, poff, k)
                block = Block(
                    data, code[lo:use], start[lo:use], end[lo:use], num[lo:use], state[lo:use] == 2, kind[lo:use],
                    self.records, self.pairs + lo,
                )  # fmt: skip
                self._sections(block)
                self.records += len(block)
                self.pairs += use
                self.ended = status == 1
                yield block
                self.fraction = min(done / size, 1.0)
                self.tick(self.fraction, f"Чтение DXF: объектов {n_text(self.records)}")
        if not self.ended:
            if self.records == 0:
                raise fail("not_dxf", "Файл не похож на DXF: в нём нет ни одной записи.")
            if self.salvage:
                self.truncated = True
                return
            raise fail(
                "truncated",
                "Файл обрезан: в нём нет завершающей записи EOF.",
                "Скопируйте файл заново или сохраните чертёж ещё раз. Режим восстановления читает то, что уцелело.",
            )

    def _bad_code(self, data: bytes, pairs: int, pos: int) -> None:
        line = 2 * (self.pairs + pairs) + 1
        shown = data[pos : pos + 40].split(b"\n")[0].decode("latin-1").strip()
        if self.pairs + pairs == 0:
            raise fail("not_dxf", f"Файл не похож на DXF: первая строка — не код группы («{shown}»).")
        raise fail(
            "broken",
            f"Строка {n_text(line)}: вместо кода группы — «{shown}». Структура файла нарушена.",
            "Откройте чертёж в исходной программе и сохраните как DXF заново (в AutoCAD — после команды RECOVER).",
        )

    def _sections(self, block: Block) -> None:
        """The section of every record, and the header variables the texts depend on."""
        kind, n = block.kind, len(block)
        opens = np.flatnonzero(kind == K_SECTION)
        mark = (kind == K_SECTION) | (kind == K_ENDSEC)
        value = np.zeros(n, dtype=np.int8)
        for r in opens.tolist():
            p = int(block.z[r]) + 1
            name = block.value(p).strip().decode("latin-1").upper() if p < len(block.code) else ""
            value[r] = SECTIONS.index(name) if name in SECTIONS[1:] and block.code[p] == 2 else S_OTHER
            if value[r] == S_HEADER:
                self._header(block, r)
        last = np.maximum.accumulate(np.where(mark, np.arange(n), -1))
        block.section = np.where(last >= 0, value[np.maximum(last, 0)], self._section).astype(np.int8)
        if n:
            self._section = int(block.section[-1])

    def _header(self, block: Block, record: int) -> None:
        a = int(block.z[record])
        b = int(block.z[record + 1]) if record + 1 < len(block) else len(block.code)
        for p in (np.flatnonzero(block.code[a:b] == 9) + a).tolist():
            name = block.value(p).strip()
            if name in (b"$ACADVER", b"$DWGCODEPAGE") and p + 1 < b:
                text = block.value(p + 1).strip().decode("latin-1")
                if name == b"$ACADVER":
                    self.version = text
                else:
                    self.codepage = text
