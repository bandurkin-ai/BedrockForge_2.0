"""Delimited text files (CSV, TXT) with detection of encoding and delimiter."""

from __future__ import annotations

import codecs
import csv
from collections import Counter
from pathlib import Path

from app.formats.tabular import ReadOptions, TableRead, frame_from_rows

ENCODINGS = ("utf-8", "utf-8-sig", "cp1251", "cp866", "koi8-r", "utf-16", "latin-1")
DELIMITERS = {",": ",", ";": ";", "\t": "\t", "|": "|", "whitespace": None}
_FREQUENT = set("оеаинтсрвлОЕАИНТСРВЛ")


def detect_encoding(raw: bytes) -> str:
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    # Russian single-byte encodings: the right one makes frequent letters frequent.
    def score(encoding: str) -> float:
        text = raw[:200_000].decode(encoding, errors="replace")
        letters = [ch for ch in text if "Ѐ" <= ch <= "ӿ"]
        return sum(ch in _FREQUENT for ch in letters) / len(letters) if letters else 0.0
    return max(("cp1251", "cp866", "koi8-r"), key=score)


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


def read_text(path: Path, options: ReadOptions) -> TableRead:
    raw = path.read_bytes()
    notes: list[str] = []
    encoding = options.encoding or detect_encoding(raw)
    if encoding not in ENCODINGS:
        raise ValueError(f"Неизвестная кодировка: {encoding}")
    text = raw.decode(encoding, errors="replace")
    if "�" in text and options.encoding:
        notes.append(f"Часть символов не читается в кодировке {encoding}: проверьте выбор кодировки")

    # Row numbers (header_row) count non-empty lines, as the preview shows them.
    lines = [line for line in text.splitlines() if line.strip()]
    delimiter = options.delimiter or detect_delimiter(lines[max(options.header_row - 1, 0):])
    if delimiter not in DELIMITERS:
        raise ValueError(f"Неизвестный разделитель: {delimiter!r}")

    rows = [_split(line, delimiter) for line in lines]
    frame = frame_from_rows(rows, options.header_row, notes)
    return TableRead(frame, "text", {"encoding": encoding, "delimiter": delimiter, "header_row": options.header_row}, notes=notes)
