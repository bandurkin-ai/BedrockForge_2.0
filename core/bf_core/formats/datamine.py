"""Datamine binary tables (`.dm`, `.fbm`) — ТЗ 7.35 Д-13, 9.12; ADR 0009; ADR 0013 §2.

`read_header(path)` → `DmHeader`: the names of the file and its dictionary of fields — enough to tell what the
table is (`DmHeader.kind`: triangles, points, strings, block_model or table) without reading a record.
`read_dm(path, progress, fields=…)` → `table.TableRead`: the stored fields as columns (`info.header` is the
`DmHeader`); `iter_chunks(path, fields=…)` gives the same columns a few megabytes of pages at a time (block
models of Э17). Values:
  * a number field (N) → float64; a float32 of a single precision file becomes the double of the same value;
    the absent data value of Datamine (−1e30) is NaN, never 0 and never −1e30; a trace (`TR`, 1e-30) stays as it
    is and is counted in a warning. A value such as −99 is data: what a model calls empty is for the importer;
  * a text field (A) → pyarrow strings, four characters to a word, trailing blanks dropped, a blank text is null;
  * an implicit field (stored word 0) has one value for every record: it is in `DmHeader.constants` and becomes
    a column only when `fields` names it.

The format (the owner's analysis of block models written by Micromine, `tests/data/real/blockmodels-dm-fbm-dat.md`;
field names of the kinds — the help of Datamine Studio): little-endian pages of 512 words; a word is 8 bytes
(extended precision, pages of 4096 bytes: a text word holds 4 characters and 4 blanks) or 4 bytes (single
precision, 2048). Page 1: file name (words 0–1), database (2–3), description (4–19), owner (20–21), then in a
`.dm` the number of field words (25), the number of the last page (26) and of the records in it (27); a `.fbm`
has them one word earlier (marker 42 in word 22). From word 28 — a descriptor of 7 words per field word: name
(2 words, 8 characters), type `N`/`A`, the number of the stored word in a record (0 — implicit), the number of
the word within a text field, a spare word, the default (the value of an implicit field). Data pages hold whole
records from their start. Text is Windows-1251 (`encoding` overrides).

Read by the description only, not checked against samples («предварительно», ADR 0013):
  * single precision files — the same words of 4 bytes (a warning says so);
  * more than 69 descriptors do not fit page 1: they are taken as running on over the next pages, or, when that
    does not give valid descriptors, as whole descriptors from the start of each next page (a warning);
  * a data page may hold records in all 512 words or leave the last 4 spare (508): both give 46 records of the
    sample. Where the two differ, full pages are looked at: blank words where one more record would be mean 508.
Not read: big-endian files, `.dmx`, names longer than 8 characters (kept elsewhere in new files; the 8-character
names are unique there as well).
Error codes: empty_file, not_datamine, truncated, broken, unsupported, unknown_encoding.
"""

from __future__ import annotations

import codecs
import math
from collections.abc import Generator, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Column, Progress, ReadInfo, TableRead, no_progress

WORDS = 512  # of a page
HEAD = 28  # words before the descriptors
DESCRIPTOR = 7
PAGE1_FIELDS = (WORDS - HEAD) // DESCRIPTOR  # 69 descriptors fit the first page
PAGE_FIELDS = WORDS // DESCRIPTOR  # whole descriptors of a further header page
SPARE = 4  # words a writer may leave unused at the end of a data page
MAX_FIELDS = 4096  # Datamine itself allows 256 (2048 in .dmx)
MAX_PAGES = 1 << 40
ABSENT_BELOW = -9.0e29  # −1e30 (as a float32: −1.00000002e30) is "absent data"
TRACES = (1.0e-30, float(np.float32(1.0e-30)))  # "TR"
ENCODING = "cp1251"
CHUNK_BYTES = 8 << 20
TRIANGLES, POINTS, STRINGS, BLOCK_MODEL, TABLE = "triangles", "points", "strings", "block_model", "table"
#: what a table is by its fields (ADR 0013 §2), the first that fits
_KINDS: tuple[tuple[str, frozenset[str]], ...] = (
    (TRIANGLES, frozenset({"PID1", "PID2", "PID3"})),
    (BLOCK_MODEL, frozenset({"XC", "XINC"})),
    (STRINGS, frozenset({"PTN", "PVALUE", "XP", "YP", "ZP"})),
    (POINTS, frozenset({"PID", "XP", "YP", "ZP"})),
)
_LAYOUTS = (("dm", 25, 26, 27), ("fbm", 24, 25, 26))  # words: field words, last page, records of the last page
FBM_MARKER = 42.0  # word 22 of a .fbm: its layout is tried first
_PADS = (b"    ", b"\x00\x00\x00\x00")  # the second half of a text word of 8 bytes
_HINT = "Сохраните файл заново в Datamine (Extended precision) или экспортируйте таблицу в CSV."
_HINT_COPY = "Скопируйте файл заново; если он такой же — сохраните его заново в Datamine."


class _Mismatch(Exception):
    """The file is not a Datamine table of the precision and layout being tried."""


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


@dataclass(frozen=True)
class DmField:
    name: str  # unique among the fields of the file without regard to case (a repeat gets a suffix)
    kind: str  # "N" — number, "A" — text
    words: tuple[int, ...]  # the stored words of a record it takes, from 0; () — an implicit field
    length: int  # characters of a text field (4 to a word); 0 for a number
    default: float | str | None  # number: the default (NaN — absent); text: the default, None when blank

    @property
    def stored(self) -> bool:
        return bool(self.words)


@dataclass
class DmHeader:
    name: str
    database: str
    description: str
    owner: str
    precision: str  # "extended" (float64) | "single" (float32)
    layout: str  # "dm" | "fbm": where the header keeps its numbers
    word: int  # bytes: 8 or 4
    page: int  # bytes: 4096 or 2048
    fields: list[DmField]  # in the order of the dictionary
    record_words: int  # stored words of a record
    per_page: int  # records of a full data page
    data_page: int  # the first data page, from 0
    records: int
    encoding: str
    warnings: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    @property
    def stored(self) -> list[DmField]:
        return [f for f in self.fields if f.stored]

    @property
    def constants(self) -> dict[str, float | str | None]:
        """The implicit fields: name → the value of every record (NaN or None — absent)."""
        return {f.name: f.default for f in self.fields if not f.stored}

    @property
    def kind(self) -> str:
        return table_kind(self.names)

    def field(self, name: str) -> DmField:
        """The field of this name; the case matters only when two names differ by it. KeyError — no such field."""
        folded = name.strip().casefold()
        found = [f for f in self.fields if f.name == name] or [f for f in self.fields if f.name.casefold() == folded]
        if not found:
            raise KeyError(name)
        return found[0]


@dataclass
class DmChunk:
    start: int  # the number of its first record, from 0
    rows: int
    columns: dict[str, Any]  # field name → float64 array (NaN — absent) or pyarrow strings (null — blank)
    traces: dict[str, int] = field(default_factory=dict)  # number fields: values «TR»
    damaged: dict[str, int] = field(default_factory=dict)  # text fields: cells the code page does not read


def table_kind(names: Iterable[str]) -> str:
    """What a Datamine table is by the names of its fields: wireframe triangles (`PID1…PID3`), wireframe points
    (`PID, XP, YP, ZP`), strings (`PTN, PVALUE, XP, YP, ZP`), a block model (`XC, XINC`) or just a table."""
    have = {name.strip().upper() for name in names}
    return next((kind for kind, need in _KINDS if need <= have), TABLE)


# ---------------------------------------------------------------- the header


def _whole(value: float, low: int, high: int) -> int:
    if not (math.isfinite(value) and value == math.floor(value) and low <= value <= high):
        raise _Mismatch
    return int(value)


def _label(raw: bytes, encoding: str) -> str:
    return raw.rstrip(b" \x00").decode(encoding, "replace")


class _Words:
    """The words of the header pages read so far: numbers, and the 4 characters of a text word."""

    def __init__(self, raw: bytes, word: int) -> None:
        self.raw, self.word = raw, word
        self.count = len(raw) // word
        self.numbers = np.frombuffer(raw, dtype=f"<f{word}", count=self.count)

    def number(self, i: int) -> float:
        return float(self.numbers[i])

    def text(self, first: int, count: int = 1) -> bytes:
        """Characters of `count` text words; a word of 8 bytes must end with blanks."""
        out = b""
        for i in range(first, first + count):
            at = i * self.word
            if self.word == 8 and self.raw[at + 4 : at + 8] not in _PADS:
                raise _Mismatch
            out += self.raw[at : at + 4]
        return out


def _descriptor(w: _Words, at: int) -> tuple[bytes, str, int, int, float, bytes]:
    """(name, type, stored word, word of the field, default as a number, default as text) of the 7 words at `at`."""
    name = w.text(at, 2)
    kind = w.text(at + 2)
    if kind[:1] not in (b"N", b"A") or kind[1:].strip(b" \x00") or not name.strip(b" \x00"):
        raise _Mismatch
    if any(c < 0x20 for c in name.rstrip(b"\x00")):
        raise _Mismatch
    stored = _whole(w.number(at + 3), 0, WORDS)
    part = _whole(w.number(at + 4), 1, WORDS) if kind[:1] == b"A" else 1
    at_default = (at + 6) * w.word
    return name, kind[:1].decode("ascii"), stored, part, w.number(at + 6), w.raw[at_default : at_default + 4]


def _places(count: int, mode: str) -> list[int]:
    """The first word of every descriptor: running on over the pages, or whole descriptors on each page."""
    if mode == "stream":
        return [HEAD + DESCRIPTOR * k for k in range(count)]
    rest = range(max(0, count - PAGE1_FIELDS))
    later = [WORDS * (1 + k // PAGE_FIELDS) + DESCRIPTOR * (k % PAGE_FIELDS) for k in rest]
    return [HEAD + DESCRIPTOR * k for k in range(min(count, PAGE1_FIELDS))] + later


def _fields(
    found: list[tuple[bytes, str, int, int, float, bytes]], encoding: str, warnings: list[str]
) -> list[DmField]:
    """Descriptors → fields: the words of a text field are put together, repeated names get a suffix."""
    groups: list[list[tuple[bytes, str, int, int, float, bytes]]] = []
    for d in found:
        last = groups[-1][-1] if groups else None
        if d[1] == "A" and last is not None and last[1] == "A" and last[0] == d[0] and d[3] == last[3] + 1:
            groups[-1].append(d)
        else:
            groups.append([d])
    words = sorted(d[2] for d in found if d[2])
    if words != list(range(1, len(words) + 1)):
        raise FormatError("broken", "Словарь полей файла Datamine повреждён: номера слов записи идут не подряд.", _HINT)
    out: list[DmField] = []
    taken: set[str] = set()
    damaged = 0
    for group in groups:
        raw_name, kind = group[0][0], group[0][1]
        name = base = _label(raw_name, encoding).strip() or "FIELD"
        damaged += "\ufffd" in base
        repeat = 1
        while name.casefold() in taken:
            repeat += 1
            name = f"{base}_{repeat}"
        taken.add(name.casefold())
        if name != base:
            warnings.append(f"Имя поля «{base}» повторяется: поле названо «{name}»")
        places = tuple(d[2] - 1 for d in group if d[2])
        if places and len(places) != len(group):
            raise FormatError(
                "broken", f"Словарь полей файла Datamine повреждён: поле «{base}» хранится не целиком.", _HINT
            )
        default: float | str | None
        if kind == "N":
            default = math.nan if group[0][4] < ABSENT_BELOW else group[0][4]
        else:
            default = _label(b"".join(d[5] for d in group), encoding) or None
        out.append(DmField(name, kind, places, 4 * len(group) if kind == "A" else 0, default))
    if damaged:
        warnings.append(f"Имена полей ({_n(damaged)}) содержат символы, которые не читаются в кодировке {encoding}")
    return out


def _capacity(f: BinaryIO, start: int, page: int, word: int, n: int, full_pages: int, last_records: int) -> int:
    """Words of a data page that hold records: all 512, or 508 when the place of one more record is blank in the
    full pages looked at (see the module docstring)."""
    tight = (WORDS - SPARE) // n
    if tight == WORDS // n or tight == 0 or last_records > tight or full_pages <= 0:
        return WORDS
    for p in sorted({int(i) for i in np.linspace(0, full_pages - 1, min(full_pages, 16))}):
        f.seek(start + p * page + tight * n * word)
        if any(f.read(page - tight * n * word)):
            return WORDS
    return WORDS - SPARE


def _header(f: BinaryIO, size: int, word: int, layout: tuple[str, int, int, int], encoding: str) -> DmHeader:
    page = WORDS * word
    f.seek(0)
    w = _Words(f.read(page), word)
    if w.count < HEAD + DESCRIPTOR:
        raise _Mismatch
    count = _whole(w.number(layout[1]), 1, MAX_FIELDS)
    last_page = _whole(w.number(layout[2]), 1, MAX_PAGES)
    last_records = _whole(w.number(layout[3]), 0, WORDS)
    labels = [_label(w.text(a, b - a), encoding).strip() for a, b in ((0, 2), (2, 4), (4, 20), (20, 22))]
    first = min(count, PAGE1_FIELDS, (w.count - HEAD) // DESCRIPTOR)
    found = [_descriptor(w, HEAD + DESCRIPTOR * k) for k in range(first)]  # a mismatch here: not this layout
    warnings: list[str] = []
    header_pages = 1
    if first < count:  # the dictionary is longer than what is read
        for mode in ("stream", "whole") if count > PAGE1_FIELDS else ("stream",):
            places = _places(count, mode)
            header_pages = -(-(places[-1] + DESCRIPTOR) // WORDS)
            f.seek(0)
            w = _Words(f.read(header_pages * page), word)
            if w.count < places[-1] + DESCRIPTOR:
                raise FormatError(
                    "truncated", f"Файл обрезан: описание {_n(count)} полей Datamine не помещается в файл.", _HINT_COPY
                )
            try:
                found = [_descriptor(w, at) for at in places]
                break
            except _Mismatch:
                continue
        else:
            if count <= PAGE1_FIELDS:
                raise _Mismatch
            raise FormatError(
                "unsupported",
                f"В файле Datamine {_n(count)} описателей полей: они занимают несколько страниц в виде, "
                "который программа не читает.",
                _HINT,
            )
        if count > PAGE1_FIELDS:
            warnings.append(
                "Описание полей занимает несколько страниц: такие файлы Datamine читаются по описанию формата "
                "без проверки на образцах — сверьте поля и значения"
            )
    fields = _fields(found, encoding, warnings)
    n = sum(len(x.words) for x in fields)
    data_pages = max(0, last_page - header_pages)
    start = header_pages * page
    capacity = _capacity(f, start, page, word, n, data_pages - 1, last_records) if n else WORDS
    per_page = capacity // n if n else 0
    if n and last_records > per_page:
        raise FormatError(
            "broken",
            f"Заголовок файла Datamine повреждён: на последней странице указано {_n(last_records)} записей, "
            f"а помещается {_n(per_page)}.",
            _HINT,
        )
    records = (data_pages - 1) * per_page + last_records if data_pages and n else 0
    need = start + (data_pages - 1) * page + last_records * n * word if records else 0
    if size < need:
        room = max(0, size - start)
        have = min(records, room // page * per_page + min(per_page, room % page // (n * word))) if records else 0
        raise FormatError(
            "truncated",
            f"Файл обрезан: в заголовке {_n(records)} записей, в файле помещается {_n(have)}.",
            _HINT_COPY,
        )
    if size > start + data_pages * page:
        extra = size - start - data_pages * page
        warnings.append(
            f"В файле есть данные после последней страницы, указанной в заголовке ({_n(extra)} байт): они не прочитаны"
        )
    if word == 4:
        warnings.append(
            "Файл Datamine одинарной точности: числа записаны как float32 (7 значащих цифр), большие координаты "
            "округлены в самом файле. Такие файлы читаются по описанию формата без проверки на образцах"
        )
    name, database, description, owner = labels
    precision = "extended" if word == 8 else "single"
    return DmHeader(
        name, database, description, owner, precision, layout[0], word, page, fields, n, per_page, header_pages,
        records, encoding, warnings,
    )  # fmt: skip


def read_header(path: str | Path, encoding: str | None = None) -> DmHeader:
    """The header and the dictionary of fields of a Datamine table (see the module docstring)."""
    path = Path(path)
    codec = encoding or ENCODING
    try:
        codecs.lookup(codec)
    except LookupError as exc:
        raise FormatError(
            "unknown_encoding", f"Неизвестная кодировка: {encoding}", "Выберите кодировку из списка."
        ) from exc
    size = path.stat().st_size
    if size == 0:
        raise FormatError("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    problem: FormatError | None = None  # of the first reading that fits the file but for its size or dictionary
    with path.open("rb") as f:
        for word in (8, 4):
            f.seek(22 * word)
            marker = np.frombuffer(f.read(word).ljust(word, b"\x00"), dtype=f"<f{word}")[0]
            for layout in _LAYOUTS[::-1] if marker == FBM_MARKER else _LAYOUTS:
                try:
                    return _header(f, size, word, layout, codec)
                except _Mismatch:
                    continue
                except FormatError as exc:
                    problem = problem or exc
    if problem is not None:
        raise problem
    raise FormatError(
        "not_datamine",
        "Файл не похож на таблицу Datamine (.dm): в его начале нет описания полей.",
        "Подходят двоичные файлы Datamine одинарной и расширенной точности. Файл .dmx или текстовую таблицу "
        "сохраните в Datamine как .dm или экспортируйте в CSV.",
    )


# ---------------------------------------------------------------- the records


def _choose(header: DmHeader, fields: Sequence[str] | None) -> list[DmField]:
    return header.stored if fields is None else [header.field(name) for name in fields]


def _text(raw: np.ndarray, encoding: str) -> tuple[Any, int]:
    """Cells of a text field (an S-dtype array) → pyarrow strings (blank — null) and how many are unreadable."""
    import pyarrow as pa

    uniques, inverse = np.unique(raw, return_inverse=True)
    labels = [_label(u, encoding) for u in uniques.tolist()]
    broken = np.array(["\ufffd" in label for label in labels], dtype=bool)
    inverse = inverse.ravel()
    damaged = int(broken[inverse].sum()) if broken.any() else 0
    cells = pa.array([label or None for label in labels], type=pa.string())
    return cells.take(pa.array(inverse, type=pa.int64())), damaged


def _columns(raw: np.ndarray, h: DmHeader, chosen: list[DmField], first: int, count: int) -> DmChunk:
    """The fields `chosen` of the `count` records that the bytes of whole pages `raw` begin with. Columns are
    taken straight from the pages (every n-th word), so nothing but them is copied."""
    n, per = h.record_words, h.per_page
    numbers = raw.view(f"<f{h.word}").reshape(-1, WORDS)
    chars = raw.reshape(-1, WORDS, h.word)
    chunk = DmChunk(first, count, {})
    for f in chosen:
        if f.kind == "N":
            with np.errstate(invalid="ignore"):  # a signalling NaN of a float32 file is a NaN
                values = np.array(numbers[:, f.words[0] : per * n : n], dtype=np.float64).reshape(-1)[:count]
            traces = int(np.count_nonzero((values == TRACES[0]) | (values == TRACES[1])))
            if traces:
                chunk.traces[f.name] = traces
            values[values < ABSENT_BELOW] = np.nan
            chunk.columns[f.name] = values
        else:
            cells = np.concatenate([chars[:, w : per * n : n, :4] for w in f.words], axis=2)
            cells = cells.reshape(-1, f.length)[:count]
            chunk.columns[f.name], damaged = _text(cells.view(f"S{f.length}").ravel(), h.encoding)
            if damaged:
                chunk.damaged[f.name] = damaged
    return chunk


def iter_chunks(
    path: str | Path,
    header: DmHeader | None = None,
    *,
    fields: Sequence[str] | None = None,
    chunk_rows: int | None = None,
    max_rows: int | None = None,
    encoding: str | None = None,
) -> Generator[DmChunk, None, None]:
    """The records of a Datamine table a few pages at a time (about 8 МБ, or `chunk_rows` records): the stored
    fields `fields` (all by default) as float64 arrays and pyarrow strings. The file is mapped, not loaded:
    memory is the columns of one chunk."""
    path = Path(path)
    h = header or read_header(path, encoding)
    chosen = [f for f in _choose(h, fields) if f.stored]
    rows = h.records if max_rows is None else min(h.records, max(0, max_rows))
    if rows == 0:
        return
    page, per = h.page, h.per_page
    pages = -(-rows // per)
    step = max(1, chunk_rows // per if chunk_rows else CHUNK_BYTES // page)
    start = h.data_page * page
    length = min(path.stat().st_size - start, pages * page)
    data: Any = np.memmap(path, dtype=np.uint8, mode="r", offset=start, shape=(length,))
    try:
        for p in range(0, pages, step):
            q = min(pages, p + step)
            raw = data[p * page : q * page]
            if len(raw) < (q - p) * page:  # the file ends with its last record, not with a whole page
                raw = np.concatenate([raw, np.zeros((q - p) * page - len(raw), dtype=np.uint8)])
            chunk = _columns(raw, h, chosen, p * per, min(rows, q * per) - p * per)
            del raw  # no view of the map is left when the reading stops here
            yield chunk
            del chunk  # … and its columns are freed before the next ones are made
    finally:  # close the map now: on Windows an open map keeps the file locked
        mm = getattr(data, "_mmap", None)
        del data
        if mm is not None:
            try:
                mm.close()
            except BufferError:  # an error inside a chunk keeps its views alive: the map goes with them
                pass


def read_dm(
    path: str | Path,
    progress: Progress | None = None,
    *,
    encoding: str | None = None,
    fields: Sequence[str] | None = None,
    max_rows: int | None = None,
    chunk_rows: int | None = None,
) -> TableRead:
    """Read a Datamine table (`.dm`, `.fbm`). `fields` — the fields to read, in this order (all stored ones by
    default; an implicit field named here becomes a column of its value); `max_rows` — only the first records;
    `chunk_rows` — about how many records are converted at a time (by default 8 МБ of pages)."""
    import pyarrow as pa

    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Чтение заголовка Datamine")
    h = read_header(path, encoding)
    chosen = _choose(h, fields)
    rows = h.records if max_rows is None else min(h.records, max(0, max_rows))
    stored = [f for f in chosen if f.stored]
    numbers = {f.name: np.empty(rows, dtype=np.float64) for f in stored if f.kind == "N"}
    texts: dict[str, list[Any]] = {f.name: [] for f in stored if f.kind == "A"}
    traces = dict.fromkeys(numbers, 0)
    damaged = dict.fromkeys(texts, 0)
    names = [f.name for f in stored]
    # closed here, not when the generator is collected: a cancelled reading must not keep the file open (Windows)
    with closing(iter_chunks(path, h, fields=names, chunk_rows=chunk_rows, max_rows=rows if stored else 0)) as chunks:
        for chunk in chunks:
            tick(chunk.start / rows, f"Чтение записей: {_n(chunk.start)} из {_n(rows)}")
            for name, values in chunk.columns.items():
                if name in numbers:
                    numbers[name][chunk.start : chunk.start + chunk.rows] = values
                else:
                    texts[name].append(values)
            for name, count in chunk.traces.items():
                traces[name] += count
            for name, count in chunk.damaged.items():
                damaged[name] += count
            del chunk  # its columns are freed before the next ones are made
    warnings = list(h.warnings)
    columns: list[Column] = []
    for f in chosen:
        if f.name in numbers:
            columns.append(Column(f.name, "number", numbers[f.name], source="N"))
            if traces[f.name]:
                warnings.append(f"Поле «{f.name}»: {_n(traces[f.name])} знач. «TR» (следы) оставлены числом 1e-30")
        elif f.name in texts:
            columns.append(Column(f.name, "text", pa.chunked_array(texts[f.name], type=pa.string()), source="A"))
            if damaged[f.name]:
                warnings.append(
                    f"Поле «{f.name}»: {_n(damaged[f.name])} знач. содержат символы, которые не читаются "
                    f"в кодировке {h.encoding}"
                )
        elif f.kind == "N":
            columns.append(Column(f.name, "number", np.full(rows, f.default, dtype=np.float64), source="N"))
        else:
            cells = pa.nulls(rows, pa.string()) if f.default is None else pa.repeat(pa.scalar(f.default), rows)
            columns.append(Column(f.name, "text", pa.chunked_array([cells], type=pa.string()), source="A"))
    tick(1.0, f"Прочитано записей: {_n(rows)}")
    return TableRead(columns, rows, ReadInfo("dm", encoding=h.encoding, header=h), warnings)


__all__ = [
    "BLOCK_MODEL",
    "POINTS",
    "STRINGS",
    "TABLE",
    "TRIANGLES",
    "DmChunk",
    "DmField",
    "DmHeader",
    "iter_chunks",
    "read_dm",
    "read_header",
    "table_kind",
]
