"""Quick previews for the import wizard: what is inside a file, without reading its geometry.

Every preview is bounded in time and memory (a few seconds for a 1 GB file): TRIDB lists its
wireframes from the catalogue tables, DXF is scanned entity by entity, Micromine STR shows its
fields and record count from the header, images only their size.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from app.formats.micromine import HEADER_SIZE, MAGIC, MicromineFormatError, _parse_header

def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


# DXF: entities counted in the first part of a big file.
DXF_SCAN_BYTES = 256 << 20
_DXF_FACE = {"3DFACE", "MESH", "POLYFACE"}
_DXF_LINE = {"LINE", "LWPOLYLINE", "POLYLINE", "3DPOLYLINE", "SPLINE", "ARC", "CIRCLE"}


def inspect_tridb(path: Path) -> dict:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return {"error": "Файл не является базой TRIDB (SQLite)"}
    try:
        if "GeneralInformation" not in tables:
            return {"error": "В файле нет таблицы GeneralInformation — это не TRIDB"}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(GeneralInformation)")}
        name = "Name" if "Name" in cols else "NULL"
        rows = conn.execute(f"SELECT ID, {name} FROM GeneralInformation ORDER BY ID").fetchall()
        counts = {}
        if "GeometryProperties" in tables:
            pcols = {r[1] for r in conn.execute("PRAGMA table_info(GeometryProperties)")}
            if {"ID", "PointCount", "TriangleCount"} <= pcols:
                counts = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT ID, PointCount, TriangleCount FROM GeometryProperties")}
        items = [{"name": str(n or f"Каркас {i}"), "points": counts.get(i, (None, None))[0],
                  "triangles": counts.get(i, (None, None))[1]} for i, n in rows]
        return {
            "items": items,
            "summary": f"Каркасов: {len(items)}"
            + (f", треугольников: {_n(sum(x['triangles'] or 0 for x in items))}" if counts else ""),
        }
    finally:
        conn.close()


def inspect_dxf(path: Path) -> dict:
    """Counts entities by layer from group-code pairs; reads at most DXF_SCAN_BYTES."""
    with path.open("rb") as f:
        if f.read(22).startswith(b"AutoCAD Binary DXF"):
            return {"summary": "Двоичный DXF: состав будет виден после импорта", "items": []}
    counts: Counter = Counter()
    layer_of: dict[str, Counter] = {}
    read = 0
    truncated = False
    current: list | None = None  # [entity type, layer]

    def flush() -> None:
        if current and current[0]:
            counts[current[0]] += 1
            layer_of.setdefault(current[1], Counter())[current[0]] += 1

    with path.open("r", encoding="cp1251", errors="replace", newline=None) as f:
        in_entities = False
        while True:
            code = f.readline()
            value = f.readline()
            if not value:
                break
            read += len(code) + len(value)
            if read > DXF_SCAN_BYTES:
                truncated = True
                break
            code, value = code.strip(), value.strip()
            if code == "0":
                flush()
                current = None
                if value == "SECTION":
                    in_entities = False
                elif in_entities and value not in ("VERTEX", "SEQEND", "ENDSEC"):
                    current = [value, "0"]
            elif code == "2" and value == "ENTITIES":
                in_entities = True
            elif current is not None and code == "8":
                current[1] = value
            elif current is not None and code == "70" and current[0] == "POLYLINE":
                try:
                    if int(value) & (16 | 64):  # polygon mesh / polyface mesh
                        current[0] = "POLYFACE"
                except ValueError:
                    pass
        flush()
    faces = sum(n for k, n in counts.items() if k in _DXF_FACE)
    lines = sum(n for k, n in counts.items() if k in _DXF_LINE)
    items = [{"name": layer, "entities": dict(c)} for layer, c in sorted(layer_of.items())]
    note = " (по первым 256 МБ)" if truncated else ""
    return {
        "items": items[:500],
        "layers": len(items),
        "entities": dict(counts),
        "summary": f"Слоёв: {len(items)}, сущностей: {_n(sum(counts.values()))} "
        f"(сеток и граней: {_n(faces)}, линий и полилиний: {_n(lines)}){note}",
    }


def inspect_str(path: Path) -> dict:
    with path.open("rb") as f:
        head = f.read(HEADER_SIZE + 65536)
    if head.startswith(MAGIC):
        try:
            _, fields, start = _parse_header(head)
        except MicromineFormatError as exc:
            return {"error": f"Заголовок Micromine не читается: {exc}"}
        record = sum(fd.width for fd in fields) + 2  # fixed width + CRLF
        size = path.stat().st_size
        n = max(0, (size - start) // record) if record > 2 else 0
        return {"format": "Micromine STR", "fields": [f.name for f in fields],
                "summary": f"Micromine STR: точек ≈ {_n(n)}, поля: {', '.join(f.name for f in fields)}"}
    text = head.decode("cp1251", errors="replace").splitlines()
    return {"format": "Surpac STR", "summary": f"Surpac STR (текст), заголовок: {text[0][:80] if text else '—'}"}


def inspect_image(path: Path) -> dict:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(path) as im:
            w, h = im.size
            mode = im.mode
    except Exception as exc:  # noqa: BLE001 — any decoder error means "not an image we can read"
        return {"error": f"Изображение не читается: {exc}"}
    return {"width": w, "height": h, "summary": f"{w} × {h} пикселей, {mode}"}


def inspect_blocks(path: Path, filename: str) -> dict:
    from app.formats.blockmodel import find_roles, header_fields

    names = header_fields(path, filename) or []
    roles = find_roles(names)
    size = path.stat().st_size
    count = ""
    with path.open("rb") as f:
        magic = f.read(len(MAGIC))
    if magic == MAGIC:
        from app.formats.micromine import read_header

        _, fields, start = read_header(path)
        record = sum(fd.width for fd in fields) + 2
        count = f"блоков {_n((size - start) // record)}, "
    used = {v for v in roles.values() if v}
    sizes = "размеры: " + ", ".join(roles[f"d{a}"] for a in "xyz") if all(roles[f"d{a}"] for a in "xyz") else "размеров нет — регулярная модель"
    attrs = [n for n in names if n not in used]
    return {"format": "Блочная модель", "fields": names,
            "summary": f"Блочная модель: {count}центр {', '.join(roles[a] or '?' for a in 'xyz')}, {sizes}; "
                       f"атрибуты ({len(attrs)}): {', '.join(attrs[:20])}{' …' if len(attrs) > 20 else ''}"}


def inspect(kind: str, filename: str, path: Path) -> dict:
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".tridb":
            return inspect_tridb(path)
        if suffix == ".dxf":
            return inspect_dxf(path)
        if suffix == ".str":
            return inspect_str(path)
        if kind == "rasters":
            return inspect_image(path)
        if kind == "blocks":
            return inspect_blocks(path, filename)
        if kind == "omf":
            from app.formats.omf import describe

            return describe(path)
    except OSError as exc:
        return {"error": f"Файл не читается: {exc}"}
    return {}
