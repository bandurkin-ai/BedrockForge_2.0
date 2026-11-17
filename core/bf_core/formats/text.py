"""Delimited text files (CSV, TXT) with detection of encoding and delimiter — ТЗ 7.7, 9.6.

`read_text(path, options, progress)` → `table.TableRead`. The file is read in blocks of 1 МБ:
decoded, split into lines and cells, and given to `table.TableBuilder`, so memory is bounded by
the result (float64 and compact strings), not by the size of the file. A block in which every
line has the same number of delimiters and no quotes is split at once, column by column.

  * Encodings: UTF-8 with or without BOM, UTF-16 (BOM, or recognised by its zero bytes), then the
    Russian single-byte ones — cp1251, cp866, KOI8-R — by the share of frequent letters. latin-1 is
    the spare one, chosen by hand. The whole file is checked for UTF-8, not only its start.
    A UTF-8 file with a few broken bytes stays UTF-8: the bytes become U+FFFD and are counted.
  * Delimiters: `;`, `,`, tab, `|`, runs of spaces — the one giving the same number (> 1) of fields
    on most of the first 200 lines.
  * One line is one record; `"…"` quotes a cell with delimiters, `""` is a quote; a quote inside an
    unquoted cell (`HQ 3" core`) is a character. Empty lines are skipped and not counted, so
    `header_row` is the number of the header among the non-empty lines.

Ported from BF `formats/text.py`. Differences: streaming, progress and cancellation; UTF-16
without a BOM; broken characters are always reported with a count (BF: only for a chosen encoding);
a second pass when a column turns out to be text late in the file (`TableBuilder.late_text`);
limits on line length and column count; `FormatError` instead of `ValueError`.
"""

from __future__ import annotations

import codecs
import csv
from collections import Counter
from collections.abc import Iterator
from itertools import repeat
from pathlib import Path

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, ReadInfo, ReadOptions, TableBuilder, TableRead, no_progress

ENCODINGS = ("utf-8", "utf-8-sig", "cp1251", "cp866", "koi8-r", "utf-16", "utf-16-le", "utf-16-be", "latin-1")
DELIMITERS = {",": ",", ";": ";", "\t": "\t", "|": "|", "whitespace": None}
BLOCK = 1 << 20  # bytes decoded and parsed at a time
MAX_LINE = 4 << 20  # characters in one line
_FREQUENT = set("оеаинтсрвлОЕАИНТСРВЛ")
_SINGLE_BYTE = ("cp1251", "cp866", "koi8-r")
_ASCII = bytes(range(128))
_NOT_LEAD = bytes(b for b in range(256) if not 0xC2 <= b <= 0xF4)
_HINT_EMPTY = "Проверьте, что файл скопирован полностью и в нём есть строки таблицы."


def _score(high: bytes, encoding: str) -> float:
    """Share of the ten most frequent Russian letters among the Cyrillic letters: the right encoding makes it high."""
    letters = [ch for ch in high.decode(encoding, errors="replace") if "\u0400" <= ch <= "\u04ff"]
    return sum(ch in _FREQUENT for ch in letters) / len(letters) if letters else 0.0


def _utf16_without_bom(sample: bytes) -> str | None:
    """UTF-16 text is half zero bytes, all on one side of each pair."""
    pairs = len(sample) // 2
    if pairs < 2:
        return None
    even, odd = sample[0 : pairs * 2 : 2].count(0), sample[1 : pairs * 2 : 2].count(0)
    if odd > 0.4 * pairs and even < 0.05 * pairs:
        return "utf-16-le"
    if even > 0.4 * pairs and odd < 0.05 * pairs:
        return "utf-16-be"
    return None


def _sniff(blocks: Iterator[bytes]) -> str:
    """Encoding of a file given as blocks of bytes (see the module docstring)."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    valid = True
    multibyte = 0  # characters outside ASCII decoded so far
    broken = 0
    high = bytearray()  # bytes outside ASCII: all the single-byte encodings differ in
    first = True
    for block in blocks:
        if first:
            first = False
            if block.startswith(codecs.BOM_UTF8):
                return "utf-8-sig"
            if block.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
                return "utf-16"
            found = _utf16_without_bom(block[: 1 << 16])
            if found:
                return found
        if len(high) < 200_000:
            high += block.translate(None, _ASCII)[:200_000]
        if valid:
            try:
                decoder.decode(block)
                multibyte += len(block.translate(None, _NOT_LEAD))  # one lead byte per character
            except UnicodeDecodeError:
                valid = False
                text = block.decode("utf-8", errors="replace")
                broken = text.count("\ufffd")
                multibyte += len(text) - len(text.encode("ascii", "ignore")) - broken
        elif len(high) >= 200_000:
            break
    if valid:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            valid, broken = False, 1
    if valid or multibyte > 2 * broken:
        return "utf-8"  # a few broken bytes in a UTF-8 file: the reader reports them
    # Russian single-byte encodings: the right one makes frequent letters frequent.
    return max(_SINGLE_BYTE, key=lambda encoding: _score(bytes(high), encoding))


def detect_encoding(raw: bytes) -> str:
    """Encoding of text given as bytes (BF signature); `sniff_encoding` does the same for a file."""
    return _sniff(iter([raw]))


def _blocks(path: Path, limit: int | None, tick: Progress) -> Iterator[bytes]:
    done = 0
    with path.open("rb") as f:
        while block := f.read(BLOCK):
            yield block
            done += len(block)
            if limit is not None and done >= limit:
                return
            if done % (16 * BLOCK) == 0:
                tick(0.0, "Определение кодировки")


def sniff_encoding(path: str | Path, limit: int | None = None, progress: Progress | None = None) -> str:
    """Encoding of a file; the whole file is examined unless `limit` bytes are enough (previews)."""
    return _sniff(_blocks(Path(path), limit, progress or no_progress))


def _split(line: str, delimiter: str) -> list[str]:
    if DELIMITERS[delimiter] is None:
        return line.split()
    return next(csv.reader([line], delimiter=delimiter, quotechar='"'))


def detect_delimiter(lines: list[str]) -> str:
    """The delimiter giving the same number (>1) of fields on most lines."""
    sample = [line for line in lines if line.strip()][:200]
    if not sample:
        return ","
    best, best_score = ",", (0.0, 0)
    for delimiter in (";", "\t", ",", "|", "whitespace"):
        counts = [len(_split(line, delimiter)) for line in sample]
        fields, hits = Counter(counts).most_common(1)[0]
        if fields < 2:
            continue
        score = (hits / len(sample), fields)
        if score > best_score:
            best, best_score = delimiter, score
    return best


class _Stats:
    replaced = 0  # characters that could not be decoded
    nul = 0


def line_blocks(
    path: Path, encoding: str, stats: _Stats | None = None, keep_blank: bool = False
) -> Iterator[tuple[list[str], bool, int]]:
    """Lines of the file, a block at a time: (lines, any quote in them, bytes read so far).

    Empty lines are left out unless `keep_blank`.
    """
    stats = stats or _Stats()
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    carry = ""
    first = True
    with path.open("rb") as f:
        while True:
            raw = f.read(BLOCK)
            buf = carry + decoder.decode(raw, final=not raw)
            if first and buf:
                first = False
                buf = buf.removeprefix("\ufeff")
            if raw:
                # Cut after the last line break; a "\r" at the very end may be half of "\r\n".
                cut = max(buf.rfind("\n"), buf.rfind("\r", 0, len(buf) - 1)) + 1
                head, carry = buf[:cut], buf[cut:]
                if len(carry) > MAX_LINE:
                    raise FormatError(
                        "too_large",
                        f"Строка длиннее {MAX_LINE // (1 << 20)} МБ: это не текстовая таблица.",
                        "Проверьте файл: таблица должна состоять из строк с разделителями.",
                    )
            else:
                head, carry = buf, ""
            if head:
                stats.replaced += head.count("\ufffd")
                stats.nul += head.count("\x00")
                lines = head.splitlines()
                if not keep_blank:
                    lines = [line for line in lines if line and not line.isspace()]
                if lines:
                    yield lines, '"' in head, f.tell()
            if not raw:
                return


def split_lines(lines: list[str], delimiter: str, quoted: bool) -> list[list[str]]:
    """Cells of each line. Lines without a quote are split directly, the others by the csv module, one by one."""
    sep = DELIMITERS[delimiter]
    if sep is None:
        return [line.split() for line in lines]
    if not quoted:
        return [line.split(sep) for line in lines]
    try:
        return [
            line.split(sep) if '"' not in line else next(csv.reader([line], delimiter=sep, quotechar='"'))
            for line in lines
        ]
    except csv.Error as exc:
        raise FormatError("broken", f"Строка файла не читается: {exc}", "Проверьте кавычки в файле.") from exc


def _feed(builder: TableBuilder, lines: list[str], delimiter: str, quoted: bool) -> int:
    """Give the lines of one block to the builder; returns the number of non-empty lines."""
    sep = DELIMITERS[delimiter]
    pending = builder.header_pending
    count = 0
    if pending or sep is None or quoted:
        lines = [line for line in lines if line and not line.isspace()]
        if pending:  # the title rows and the header go as rows
            builder.add(split_lines(lines[:pending], delimiter, quoted))
            count = min(pending, len(lines))
            lines = lines[pending:]
            if not lines:
                return count
    if sep is not None and not quoted:
        # Every line has the same number of delimiters (so none is empty): the cells of the whole block
        # are split at once and taken column by column — no Python object per row.
        fields = set(map(str.count, lines, repeat(sep)))
        if len(fields) == 1 and min(fields) >= 1:
            width = min(fields) + 1
            flat = sep.join(lines).split(sep)
            builder.add_columns([flat[j::width] for j in range(width)], len(lines), plain=True)
            return count + len(lines)
        lines = [line for line in lines if line and not line.isspace()]
    builder.add(split_lines(lines, delimiter, quoted))
    return count + len(lines)


def _check(options: ReadOptions) -> None:
    if options.encoding is not None and options.encoding not in ENCODINGS:
        raise FormatError(
            "unknown_encoding", f"Неизвестная кодировка: {options.encoding}", "Выберите кодировку из списка."
        )
    if options.delimiter is not None and options.delimiter not in DELIMITERS:
        raise FormatError(
            "unknown_delimiter",
            f"Неизвестный разделитель: {options.delimiter!r}",
            "Допустимы «;», «,», табуляция, «|» и пробелы.",
        )
    if options.header_row < 0:
        raise FormatError(
            "header_row", "Номер строки заголовка не может быть отрицательным.", "Укажите 0, если заголовка нет."
        )


def head_lines(path: Path, encoding: str, count: int) -> list[str]:
    """The first `count` non-empty lines."""
    lines: list[str] = []
    for block, *_ in line_blocks(path, encoding):
        lines.extend(block)
        if len(lines) >= count:
            break
    return lines[:count]


def read_text(path: str | Path, options: ReadOptions | None = None, progress: Progress | None = None) -> TableRead:
    """Read a CSV/TXT file. Options left as None are detected; what was used is in `info`."""
    path = Path(path)
    options = options or ReadOptions()
    tick = progress or no_progress
    _check(options)
    size = path.stat().st_size
    if size == 0:
        raise FormatError("empty_file", "Файл пуст.", _HINT_EMPTY)
    tick(0.0, "Определение кодировки")
    if size >= 2 * BLOCK:
        from bf_core.formats import numbers

        numbers.load_kernel()  # a big file: every column goes through the numba kernel, however short the block
        tick(0.0, "Определение кодировки")
    encoding = options.encoding or sniff_encoding(path, progress=tick)
    skip = max(options.header_row - 1, 0)
    delimiter = options.delimiter or detect_delimiter(head_lines(path, encoding, skip + 200)[skip:])
    tick(0.02, "Чтение строк")

    late: set[int] = set()
    lo, span = 0.02, 0.88
    for _ in range(3):
        stats = _Stats()
        builder = TableBuilder(options.header_row, options.text_columns, sorted(late))
        lines_read = 0
        expected = 0
        for lines, quoted, done in line_blocks(path, encoding, stats, keep_blank=True):
            lines_read += _feed(builder, lines, delimiter, quoted)
            if not expected and builder.rows:
                expected = int(builder.rows * size / done * 1.02) + 1024  # the columns are allocated once
                builder.expect(expected)
            tick(lo + span * done / size, f"Чтение строк: {lines_read:,}".replace(",", " "))
        if lines_read == 0:
            raise FormatError("empty_file", "В файле нет строк с данными.", _HINT_EMPTY)
        if not builder.late_text:
            break
        # A column turned out to be text after its first blocks were stored as numbers: read again.
        late |= builder.late_text
        lo, span = 0.9, 0.09
    else:
        raise FormatError(
            "broken", "Файл меняется во время чтения.", "Дождитесь окончания записи файла и повторите импорт."
        )
    columns, warnings = builder.finish()
    if stats.replaced:
        warnings.insert(0, f"{stats.replaced} символов не читаются в кодировке {encoding}: проверьте выбор кодировки")
    if stats.nul:
        warnings.insert(0, f"В файле {stats.nul} нулевых символов: похоже, это не текстовая таблица")
    tick(1.0, f"Прочитано строк: {builder.rows:,}".replace(",", " "))
    info = ReadInfo("csv", encoding=encoding, delimiter=delimiter, header_row=options.header_row)
    return TableRead(columns, builder.rows, info, warnings)
