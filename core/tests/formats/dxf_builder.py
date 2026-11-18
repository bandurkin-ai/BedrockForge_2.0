"""DXF text written by hand for the tests: exact control over every group (what ezdxf would not write)."""

from __future__ import annotations

from collections.abc import Sequence


def tags(*items: object, eol: str = "\r\n") -> str:
    """Group code / value pairs: tags(0, "LINE", 8, "Layer", 10, 1.5, ...). Floats are written with repr."""
    assert len(items) % 2 == 0
    out: list[str] = []
    for code, value in zip(items[0::2], items[1::2], strict=True):
        text = repr(value) if isinstance(value, float) else str(value)
        out.append(f"{code:>3}{eol}{text}{eol}")
    return "".join(out)


def drawing(
    entities: str,
    *,
    version: str | None = "AC1018",
    codepage: str | None = "ANSI_1251",
    layers: Sequence[tuple[str, int]] = (),
    eol: str = "\r\n",
    blocks: str = "",
    end: bool = True,
) -> str:
    """A whole drawing around the given ENTITIES text; `layers` — (name, ACI) of the LAYER table."""
    head: list[object] = [0, "SECTION", 2, "HEADER"]
    if version is not None:
        head += [9, "$ACADVER", 1, version]
    if codepage is not None:
        head += [9, "$DWGCODEPAGE", 3, codepage]
    head += [9, "$INSBASE", 10, 0.0, 20, 0.0, 30, 0.0, 0, "ENDSEC"]
    table: list[object] = [0, "SECTION", 2, "TABLES", 0, "TABLE", 2, "LAYER", 70, len(layers)]
    for name, aci in layers:
        table += [0, "LAYER", 2, name, 70, 0, 62, aci, 6, "CONTINUOUS"]
    table += [0, "ENDTAB", 0, "ENDSEC"]
    out = tags(*head, eol=eol) + tags(*table, eol=eol)
    if blocks:
        out += tags(0, "SECTION", 2, "BLOCKS", eol=eol) + blocks + tags(0, "ENDSEC", eol=eol)
    out += tags(0, "SECTION", 2, "ENTITIES", eol=eol) + entities
    if end:
        out += tags(0, "ENDSEC", 0, "EOF", eol=eol)
    return out
