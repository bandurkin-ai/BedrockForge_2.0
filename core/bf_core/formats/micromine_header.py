"""Header of a Micromine data file (.DAT, .STR): parsing and building (ТЗ 9.2).

File layout (verified in BF on Collar, Survey and Assay files of a real project and on the STR
files of `tests/data/real`):

* Extended header: one or more 4096-byte pages. The first 42 bytes are the magic line
  `THIS IS MICROMINE EXTENDED DATA FILE!\\x07\\x1a\\x01\\r\\n`, then XML metadata (`title`,
  `encoding` with the `codepage` attribute, `field-class` per field) padded with NUL bytes.
  Block model exports have a longer XML (8192 bytes and more): the code page is looked for in all of it.
* `N VARIABLES` block right after the pages: N lines `NAME TYPE WIDTH DECIMALS`. NAME is 10 wide
  and the type follows it directly (`RUN_AU_CUTN 12 8`); newer versions append `|description`.
* Fixed-width records, each followed by CRLF.
* Standard header (the format before the XML pages, described in Micromine help; no sample
  files were available): the file starts with the `N VARIABLES` block. It has no code page.

Field types: `C` text padded with spaces, `R` IEEE double (little-endian), `N` a number written
as text; by size (not met in real files) `F` float32, `L` int32, `S` int16, `B` byte.
Code pages: `ibm-5347` = Windows-1251 (files say `ibm-5347_P100-1998`, the ICU suffix is dropped),
`ibm-1251`, `windows-1251`, `ibm-5348`/`windows-1252`, `utf-8`, `ascii`, any codec Python knows
by that name, and `BOCU-1` (formats/bocu1.py): every name and every C/N value is encoded on its own.

Ported from BF `formats/micromine.py` (`_parse_header`, `read_header`) and `formats/export.py`
(`_str_header`). New in BF2: field descriptions are kept, the Standard header, the raw XML, title
and description, an unknown code page or field type is a warning (not a silent loss), limits
against "bombs", Russian `FormatError`s.
"""

from __future__ import annotations

import codecs
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

from bf_core.formats import bocu1
from bf_core.formats.errors import FormatError

MAGIC = b"THIS IS MICROMINE EXTENDED DATA FILE"
MAGIC_LINE = MAGIC + b"!\x07\x1a\x01\r\n"  # 42 bytes
PAGE = 4096
VARIABLES_TAG = b"VARIABLES\r\n"
RECORD_TRAILER = b"\r\n"
NAME_WIDTH = 10  # Micromine field names are at most 10 characters
TEXT_LIMIT = 255  # the widest character field of a Micromine file
DEFAULT_CODEPAGE = "ibm-5347_P100-1998"  # Windows-1251, the code page of the sample files
TIME_STAMP = "67452301efcdab89"  # the constant Micromine writes in every meta-object
BOCU = "bocu-1"
FALLBACK_CODEC = "cp1251"

# Binary field types and their numpy types; C and N are text of WIDTH bytes.
BINARY_TYPES = {"R": "<f8", "F": "<f4", "L": "<i4", "S": "<i2", "B": "u1"}
BINARY_SIZES = {"R": 8, "F": 4, "L": 4, "S": 2, "B": 1}
FIELD_TYPES = ("C", "N", *BINARY_TYPES)

MAX_FIELDS = 10_000
MAX_WIDTH = 65_535
MAX_HEADER = 64 << 20

_CODEPAGE_RE = re.compile(rb'codepage="([^"]*)"')
_VERSION_RE = re.compile(rb'<metadata[^>]*\bversion="([^"]*)"')
_TITLE_RE = re.compile(r'<meta-object[^>]*type="title"[^>]*>\s*<data([^>]*)/?>')
_ATTR_RE = re.compile(r'\b(title|description)="([^"]*)"')
# Bytes that may precede the header: a UTF-8 BOM, blanks, line ends, NULs (a file saved by an editor or exported).
_LEAD = b"\xef\xbb\xbf \t\r\n\x00"
# `N VARIABLES` at the start of a Standard header: any letter case, LF or CRLF, blanks before the line end.
_STANDARD_RE = re.compile(rb"[ \t]*(\d+)[ \t]*VARIABLES[ \t]*\r?\n", re.IGNORECASE)
_VARIABLES_RE = re.compile(rb"VARIABLES[ \t]*\r?\n", re.IGNORECASE)
# The first line of an extended file; Micromine versions differ in the words and the case.
_MAGIC_RE = re.compile(rb"THIS IS MICROMINE[A-Z ]*DATA FILE", re.IGNORECASE)
# A field line is `NAME(10 bytes)` + `TYPE` + `WIDTH` + `DECIMALS`: the width follows the type directly
# (`C255  0`, `N 12  8`, `R  8  0`), a description may follow after "|".
_TAIL_RE = re.compile(rb"\s*(\d+)\s+(\d+)\s*(?:\|(.*))?$", re.DOTALL)
# The same for a name that is not padded to 10 bytes (or longer than that): found by the type letter. In BOCU-1
# files printable characters of the name are bytes >= 0x80, so the letter (0x41-0x5A) is not a name byte.
_VARIABLE_RE = re.compile(rb"^(.*?)\s*([A-Z])\s*(\d+)\s+(\d+)\s*(?:\|(.*))?$", re.DOTALL)

# Micromine code page names -> Python codecs. ibm-5347 is Windows-1251 (Cyrillic).
_CODECS = {
    **{"ibm-5347": "cp1251", "ibm-1251": "cp1251", "windows-1251": "cp1251", "cp1251": "cp1251"},
    **{"ibm-5348": "cp1252", "windows-1252": "cp1252", "utf-8": "utf-8", "ascii": "ascii"},
}

_HINT_BROKEN = "Откройте файл в Micromine и сохраните его заново или экспортируйте таблицу в CSV."


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # "C" (text), "R" (real), "N" (number stored as text); F, L, S, B by size
    width: int
    decimals: int
    description: str = ""  # the text after "|" in the VARIABLES line


@dataclass
class DatHeader:
    extended: bool  # XML pages before the VARIABLES block (False: Standard header)
    codepage: str  # as written in the file; "" when the header has none
    encoding: str  # Python codec of names and text, or "bocu-1"
    fields: list[Field]
    data_offset: int  # offset of the first record
    title: str = ""
    description: str = ""
    xml: str = ""  # the XML of the extended header as it is in the file
    declared: bool = True  # False: the encoding is a guess (no or unknown code page)
    warnings: list[str] = field(default_factory=list)

    @property
    def record_size(self) -> int:
        return sum(f.width for f in self.fields) + len(RECORD_TRAILER)

    @property
    def names(self) -> list[str]:
        from bf_core.formats.table import unique_names

        return unique_names([f.name for f in self.fields])


class _NeedMore(Exception):
    """The header does not end within the bytes read so far."""


def codec_of(codepage: str) -> str | None:
    """Python codec (or "bocu-1") of a Micromine code page name; ICU names carry a suffix: "ibm-5347_P100-1998"."""
    name = codepage.lower().strip()
    if name == BOCU:
        return BOCU
    known = _CODECS.get(name) or _CODECS.get(name.split("_")[0])
    if known:
        return known
    try:
        return codecs.lookup(name).name if name else None
    except LookupError:
        return None


def check_codec(encoding: str) -> str:
    """A codec chosen by the user: its Python name, or FormatError."""
    found = codec_of(encoding)
    if found is None:
        raise FormatError("unknown_encoding", f"Неизвестная кодировка: {encoding}", "Выберите кодировку из списка.")
    return found


def decode_text(raw: bytes, encoding: str) -> str:
    if encoding == BOCU:
        return bocu1.decode(raw.rstrip(b" \x00"), errors="replace").rstrip(" \x00")
    return raw.decode(encoding, errors="replace").rstrip(" \x00")


def encode_text(text: str, encoding: str) -> bytes:
    """Text of one field; UnicodeEncodeError when the code page has no such character."""
    return bocu1.encode(text) if encoding == BOCU else text.encode(encoding)


def _fail(code: str, message: str, hint: str = _HINT_BROKEN) -> FormatError:
    return FormatError(code, message, hint)


def header_start(data: bytes) -> int | None:
    """Offset of the Micromine header in the first bytes of a file (after a BOM, blanks, NULs), or None.

    The header is the magic line of an extended file or `N VARIABLES` of a Standard one."""
    stripped = data.lstrip(_LEAD)
    lead = len(data) - len(stripped)
    if _MAGIC_RE.match(stripped) or _STANDARD_RE.match(stripped):
        return lead
    return None


def _split_variable(line: bytes) -> tuple[bytes, str, int, int, bytes] | None:
    """(raw name, type, width, decimals, raw description) of one VARIABLES line; None when it cannot be read.

    By position first: the name is the first NAME_WIDTH bytes and the type letter follows it, so names with
    digits, spaces and letters (`Тип руды`, `Au 5`) cannot be mistaken for the type and width."""
    kind = line[NAME_WIDTH : NAME_WIDTH + 1].decode("latin-1")
    tail = _TAIL_RE.match(line, NAME_WIDTH + 1) if kind in FIELD_TYPES else None
    if tail:
        return line[:NAME_WIDTH].rstrip(b" \t\x00"), kind, int(tail.group(1)), int(tail.group(2)), tail.group(3) or b""
    m = _VARIABLE_RE.match(line)
    if m:
        return m.group(1), m.group(2).decode("ascii"), int(m.group(3)), int(m.group(4)), m.group(5) or b""
    return None


def _variable_error(number: int, line: bytes, codec: str | None) -> FormatError:
    """The line of the VARIABLES block that cannot be read: its number, text in the code page of the header, bytes."""
    codec = codec or FALLBACK_CODEC
    shown = "".join(c if c.isprintable() else " " for c in decode_text(line[:60], codec))
    return _fail(
        "broken",
        f"Строка {number} блока VARIABLES не читается: «{shown}» (байты: {line[:24].hex(' ')}). "
        "Ожидается: имя поля в 10 байт, тип (C, N, R …), ширина и число десятичных знаков.",
        "Если буквы в названии поля искажены, выберите кодировку файла в мастере импорта (например, Windows-1251). "
        + _HINT_BROKEN,
    )


def parse_header(data: bytes, encoding: str | None = None, complete: bool = True) -> DatHeader:
    """Header from the first bytes of a file. `encoding` overrides the code page of the file.

    `complete=False`: `data` is only the start of the file — raises _NeedMore when the header is longer.
    """
    warnings: list[str] = []
    if not data:
        raise _fail("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    lead = header_start(data)
    if lead is None:
        raise _fail(
            "not_micromine",
            "Файл не является таблицей Micromine: нет строки «THIS IS MICROMINE EXTENDED DATA FILE» "
            f"и строки «<число> VARIABLES» в начале. Начало файла: {data[:16].hex(' ')}.",
            "Если это текстовая таблица, сохраните её с расширением .csv или .txt.",
        )
    if lead:
        data = data[lead:]  # a BOM or blanks before the header: offsets below count from the header
    standard = _STANDARD_RE.match(data)
    if _MAGIC_RE.match(data):
        if not data.startswith(MAGIC_LINE) and len(data) >= len(MAGIC_LINE):
            odd = data[len(MAGIC) : len(MAGIC_LINE)].hex(" ")
            warnings.append(f"Неизвестный вариант заголовка Micromine (байты {odd}): файл прочитан как расширенный DAT")
        tag = _VARIABLES_RE.search(data, PAGE)
        if tag is None:
            if not complete:
                raise _NeedMore
            raise _fail("truncated", "Файл обрезан: после заголовка Micromine нет блока VARIABLES.")
        vars_pos = tag.start()
        # "<N>   VARIABLES": the header before it is padded with zero bytes (to 4096 or 8192).
        count_token = data[PAGE:vars_pos].replace(b"\x00", b" ").split()
        if not count_token or not count_token[-1].isdigit():
            raise _fail("broken", "В заголовке Micromine не найдено число полей перед VARIABLES.")
        count = int(count_token[-1])
        pos = tag.end()
        eol = b"\r\n" if tag.group().endswith(b"\r\n") else b"\n"
        extended = True
    else:
        assert standard is not None
        count = int(standard.group(1))
        pos = standard.end()
        eol = b"\r\n" if standard.group().endswith(b"\r\n") else b"\n"
        vars_pos = 0
        extended = False
    if count > MAX_FIELDS:
        raise _fail("too_large", f"В заголовке указано {count} полей — больше допустимых {MAX_FIELDS}.")

    # The XML header may be longer than 4096 bytes (then padded to 8192): look in all of it.
    head = data[:vars_pos]
    codepage_match = _CODEPAGE_RE.search(head)
    codepage = codepage_match.group(1).decode("ascii", "replace") if codepage_match else ""
    xml = ""
    title = description = ""
    if extended:
        start = head.find(b"<", len(MAGIC))
        xml = head[start:].split(b"\x00", 1)[0].decode("utf-8", "replace") if start >= 0 else ""
        version = _VERSION_RE.search(head)
        if version and version.group(1) != b"1.0":
            shown = version.group(1).decode("ascii", "replace")
            warnings.append(f"Версия метаданных Micromine {shown} неизвестна: файл прочитан как версия 1.0")
        found = _TITLE_RE.search(xml)
        if found:
            attrs = {k: html.unescape(v) for k, v in _ATTR_RE.findall(found.group(1))}
            title, description = attrs.get("title", ""), attrs.get("description", "")

    lines: list[tuple[bytes, str, int, int, bytes]] = []
    for number in range(1, count + 1):
        end = data.find(eol, pos)
        if end < 0:
            if not complete:
                raise _NeedMore
            raise _fail("truncated", "Файл обрезан: блок VARIABLES короче, чем указано в заголовке.")
        line = data[pos:end]
        parsed = _split_variable(line)
        if parsed is None:
            shown_codec = None
            if encoding is not None:
                shown_codec = codec_of(encoding)
            elif codepage:
                shown_codec = codec_of(codepage)
            raise _variable_error(number, line, shown_codec)
        pos = end + len(eol)
        width = parsed[2]
        if width > MAX_WIDTH:
            raise _fail("too_large", f"Ширина поля {width} байт больше допустимой {MAX_WIDTH}.")
        lines.append(parsed)

    declared = True
    if encoding is not None:
        codec = check_codec(encoding)
    elif codepage:
        known = codec_of(codepage)
        if known is None:
            warnings.append(
                f"Кодовая страница «{codepage}» неизвестна: текст прочитан как Windows-1251 — проверьте русские буквы"
            )
            declared = False
        codec = known or FALLBACK_CODEC
    else:
        # No code page in the header: BOCU-1 names are ASCII + 0x50 (every byte above 0x70).
        raws = [name for name, *_ in lines if name.strip()]
        is_bocu = bool(raws) and all(all(b > 0x70 or b == 0x20 for b in r.strip()) for r in raws)
        codec = BOCU if is_bocu else FALLBACK_CODEC
        declared = False
        if not is_bocu and any(b >= 0x80 for name, *_ in lines for b in name):
            warnings.append("В заголовке не указана кодовая страница: имена полей прочитаны как Windows-1251")

    fields: list[Field] = []
    for name_raw, kind, width, decimals, note in lines:
        name = decode_text(name_raw, codec)
        if width < 1:
            raise _fail("broken", f"Поле «{name}»: ширина {width} недопустима.")
        if kind in BINARY_SIZES and width != BINARY_SIZES[kind]:
            raise _fail(
                "broken", f"Поле «{name}» типа {kind} должно занимать {BINARY_SIZES[kind]} байт, указано {width}."
            )
        fields.append(Field(name, kind, width, decimals, decode_text(note, codec) if note else ""))
    # Records are not flagged in the header: they start right after the last variable line.
    return DatHeader(extended, codepage, codec, fields, pos + lead, title, description, xml, declared, warnings)


def read_header(path: str | Path, encoding: str | None = None) -> DatHeader:
    """Header of a DAT/STR file, reading only the start of the file."""
    path = Path(path)
    size = 1 << 16
    with path.open("rb") as f:
        while True:
            head = f.read(size)
            f.seek(0)
            complete = len(head) < size or size >= MAX_HEADER
            try:
                return parse_header(head, encoding, complete)
            except _NeedMore:
                size *= 4


def default_xml(names: list[str], codepage: str, title: str = "", description: str = "") -> str:
    """The metadata Micromine writes for a plain table: title, encoding and a field class per field."""
    stamp = f'version="1.0" time-stamp="{TIME_STAMP}"'
    parts = [
        '<?xml version="1.0" encoding="utf-8"?><metadata version="1.0">',
        f'<meta-object type="title" {stamp} compulsory="true">'
        f"<data title={quoteattr(title)} description={quoteattr(description)}/></meta-object>",
        f'<meta-object type="encoding" {stamp} compulsory="true"><data codepage={quoteattr(codepage)}/></meta-object>',
    ]
    for name in names:
        parts.append(
            f'<meta-object type="field-class" {stamp}>'
            f'<field name={quoteattr(name)}/><data field-class="Generic"/></meta-object>'
        )
    parts.append("</metadata>")
    return "".join(parts)


def build_header(fields: list[Field], encoding: str, xml: str | None) -> bytes:
    """Everything before the first record. `xml` None writes the Standard header (no pages)."""
    out = bytearray()
    if xml is not None:
        head = MAGIC_LINE + xml.encode("utf-8")
        # NUL padding to 4096 bytes; a longer header (many fields) is padded to the next multiple.
        out += head.ljust(PAGE * max(1, -(-(len(head) + 1) // PAGE)), b"\x00")
    out += f"{len(fields):<4}VARIABLES\r\n".encode("ascii")
    for f in fields:
        line = encode_text(f.name, encoding).ljust(NAME_WIDTH) + f"{f.kind}{f.width:>3} {f.decimals:>2}".encode("ascii")
        if f.description:
            line += b"|" + encode_text(f.description, encoding)
        out += line + b"\r\n"
    return bytes(out)
