"""Datamine `.dmx` — the compressed tables of Datamine Studio: told for what they are, not read (ADR 0009 № 4).

`is_dmx(path)` — the file ends as a `.dmx` does; `dmx_error(name)` — the FormatError("unsupported") said about
one: by `registry.detect_object` for the extension, by `datamine.read_header` for such a file under the name of
a `.dm`.

The layout, by the owner's wireframe `tests/data/real/Datamine_wireframe.zip` (`FXDRIVES_tr.dmx`, `_pt.dmx`:
110 000 triangles of 10 number fields, 56 043 points — decoded apart from the program; a reader is a stage of its
own, text fields and implicit fields have no sample):
  * the file is blocks, each packed as an LZ4 block (kept as it is when packing gains nothing: the two sizes are
    equal) with the xxHash32 (seed 0) of its unpacked bytes; all numbers are little-endian;
  * the last 25 bytes: «DMX», 1; the place (u64), the packed and the unpacked size (u32 each) and the checksum
    (u32) of the directory block, which ends where the footer begins; a flag byte (1);
  * the directory: the number of blocks (u32), then per block its place (u64), packed size, unpacked size,
    checksum (u32 each) and a key — a count (u16) of named values: the name (u16 length, UTF-16), a variant type
    (u16: 0 — none, 3 — int32, 17 — byte) and the value;
  * keys: `I=0, S` — the description of the file (UTF-16); `I=1` — settings: named texts (the version of
    Studio, legends as XML); `I=0, S, T=1` — the fields: a count (u32), per field the name (u16 length, UTF-16),
    a type byte (1 — number), the default (float64; −1e30 — absent), a byte, the size (u32: 8); `A, B, I=0,
    T=1` — the records A…B (from 1; 256 to a block): the number of columns (u32), per column a type byte, the
    count (u32) and that many float64.
"""

from __future__ import annotations

from pathlib import Path

from bf_core.formats.errors import FormatError

DMX_MAGIC = b"DMX\x01"  # begins the footer
DMX_FOOTER = 25  # bytes of the footer


def is_dmx(path: str | Path) -> bool:
    """The file ends as a `.dmx` does: the footer with its mark, and a directory that lies before it. A file that
    cannot be opened is not one."""
    try:
        with Path(path).open("rb") as f:
            size = f.seek(0, 2)
            if size < DMX_FOOTER:
                return False
            f.seek(size - DMX_FOOTER)
            foot = f.read(DMX_FOOTER)
    except OSError:
        return False
    end = int.from_bytes(foot[4:12], "little") + int.from_bytes(foot[12:16], "little")
    return foot.startswith(DMX_MAGIC) and end <= size - DMX_FOOTER


def dmx_error(name: str) -> FormatError:
    """What is said about a `.dmx` (by its name alone, or by its content under another name)."""
    return FormatError(
        "unsupported",
        f"Файл «{name}» — таблица Datamine в сжатом формате DMX (Datamine Studio): такие файлы программа "
        "пока не читает.",
        "Сохраните таблицу в Datamine в формате .dm (Extended precision). Каркас — это два файла: "
        "треугольники (…tr.dm) и точки (…pt.dm).",
    )


__all__ = ["DMX_FOOTER", "DMX_MAGIC", "dmx_error", "is_dmx"]
