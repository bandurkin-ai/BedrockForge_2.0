"""Point clouds in «Импорт файлов» (ТЗ 7.35 Д-22; ADR 0014 §1 «Сборка при импорте», §3, §6 «Импорт»).

A file goes into a cloud when it is a LAS (`.las`; a `.laz` is refused — `laz_not_supported`), or a table of
points the wizard asked to load as a cloud (`options.cloud`; «Облако точек» in the mode XYZ). Read options of a
cloud file: `every` — «каждая k-я запись» (1 by default), and of a table — `x`, `y`, `z`, `class_column` (the
column of the class; "" — none), `encoding`.

`summarize_cloud(g, options)` — the composition entry of the wizard (nothing is read but the head of a table, or
the header and a sample of 1 млн records of a LAS): `kind: "cloud"`, the totals of a file of one layer, and
`cloud` — what `formats.las.LasInspect.as_dict()` or `formats.points_stream.XyzHead.as_dict()` gives, with the
records the import takes (`points`) for the chosen `every`.
`write_cloud(...)` — the job: the cloud is built from its source in two passes into temporary files of the blob
store (`lines.cloud_build.stage_cloud`: about 300 MB whatever the size; progress and cancel between portions —
nothing is left then), then one short transaction writes the line file (state `building`, the CRS label of the
LAS) and its one layer — the cloud. The source file and «готово» come with the final transaction of the job
(`objects.py`), as for any file.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.formats.errors import FormatError

from .object_files import MAX_NOTES, Group, _size, n_text

MEMORY_MB = 768  # the streamed build: the portions, a part, the buffers of the temporary files
CLOUD_OPTIONS = ("cloud", "every", "class_column")
_EVERY_HINT = "Укажите целое число от 1: 1 — каждая запись, 2 — каждая вторая и т. д."


def wanted(g: Group, options: Mapping[str, Any] | None) -> bool:
    """The file is loaded as a cloud."""
    from bf_core.formats import registry as reg

    return reg.LAS in g.formats or (reg.POINTS in g.formats and bool((options or {}).get("cloud")))


def every_of(options: Mapping[str, Any] | None) -> int:
    raw = (options or {}).get("every")
    if raw in (None, ""):
        return 1
    try:
        every = int(str(raw).strip())
    except ValueError:
        every = 0
    if every < 1 or str(raw).strip().lstrip("+") != str(every):
        raise FormatError("bad_every", f"«Каждая k-я запись»: «{raw}» — не целое положительное число.", _EVERY_HINT)
    return every


@dataclass
class Source:
    """A cloud file opened for the build."""

    format: str  # "las" | "points_csv"
    label: str
    reader: Any  # formats.las.LasFile | formats.points_stream.XyzCloud
    every: int
    meta: dict[str, Any]  # `meta.cloud.source`
    crs: Any  # crs.CrsInfo | None
    notes: list[str]  # warnings of the header

    def chunks(self) -> Any:
        if self.format == "las":
            return self.reader.chunks(None, every=self.every)
        return self.reader.chunks()

    @property
    def expected(self) -> int:
        return int(self.reader.count(self.every) if self.format == "las" else self.reader.expected)

    @property
    def warnings(self) -> list[str]:
        """Of the header and of the last whole pass over the points."""
        found = self.reader.warnings
        return list(dict.fromkeys([*self.notes, *found]))


def open_source(g: Group, options: Mapping[str, Any] | None) -> Source:
    """The source of a cloud file; FormatError / OSError when it cannot be read."""
    from bf_core.formats import registry as reg

    options = options or {}
    every = every_of(options)
    base = {"filename": g.main.name, "every": every}
    if reg.LAS in g.formats:
        read = reg.read_objects(g.main)
        las = read.cloud
        las.chunks(None, every=every)  # refuses a cloud too big for the thinning before anything is read
        meta = {"kind": "las", **base, "las": las.header.as_meta()}
        return Source(reg.LAS, read.label, las, every, meta, read.crs, list(read.warnings))
    from bf_core.formats.points_stream import XyzCloud, inspect_xyz

    head = inspect_xyz(g.main, **_columns(options))
    meta = {"kind": "xyz", **base, "xyz": head.source()}
    label = reg.OBJECT_FORMATS[reg.POINTS].label
    return Source("points_csv", label, XyzCloud(g.main, head, every=every), every, meta, None, [])


def _columns(options: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: str(options[k]) for k in ("x", "y", "z") if options.get(k)}
    if options.get("class_column") is not None:
        out["cls"] = str(options["class_column"])
    if options.get("encoding"):
        out["encoding"] = str(options["encoding"])
    return out


# --- the composition -------------------------------------------------------------------------------------


def summarize_cloud(g: Group, options: Mapping[str, Any] | None) -> dict[str, Any]:
    """The composition entry of a cloud file (see the module docstring). Raises FormatError / OSError."""
    from bf_core.formats import registry as reg

    options = options or {}
    every = every_of(options)
    if reg.LAS in g.formats:
        read = reg.read_objects(g.main, inspect=True)
        found = read.detail
        cloud = found.as_dict()
        points = -(-found.header.point_count // every)
        fmt, label, crs, notes = reg.LAS, read.label, read.crs, list(found.warnings)
        left = [
            {"what": what, "count": None, "reason": "облако хранит X, Y, Z, класс и возврат"}
            for what in found.not_loaded
        ]
        encoding = None
    else:
        from bf_core.formats.points_stream import inspect_xyz

        head = inspect_xyz(g.main, **_columns(options))
        cloud = head.as_dict()
        points = -(-head.estimate // every)
        fmt, label, crs, notes = "points_csv", reg.OBJECT_FORMATS[reg.POINTS].label, None, list(head.warnings)
        left = [
            {"what": f"колонка «{n}»", "count": None, "reason": "облако хранит X, Y, Z и класс"} for n in head.left_out
        ]
        encoding = head.encoding
    cloud.update(points=points, every_chosen=every)
    return {
        "path": str(g.main), "file": g.main.name, "ok": True, "format": fmt, "label": label, "kind": "cloud",
        "encoding": encoding, "crs": None if crs is None else {"name": crs.name, "epsg": crs.epsg, "wkt": crs.wkt},
        "sidecars": [], "layers": [], "rasters": [], "wireframes": [],
        "totals": {"layers": 1, "lines": 0, "points": points, "vertices": 0, "rasters": 0, "wireframes": 0,
                   "triangles": 0},
        "unread": 0, "preliminary": False, "choices": {}, "warnings": list(dict.fromkeys(notes))[:MAX_NOTES],
        "left_out": left, "cloud": cloud,
    }  # fmt: skip


# --- the job ---------------------------------------------------------------------------------------------


@dataclass
class Written:
    file_id: int
    cloud_id: int
    counts: dict[str, Any]
    settings: dict[str, Any]
    warnings: list[str]


def write_cloud(
    ctx: Any,
    engine: sa.Engine,
    store: Any,
    g: Group,
    src: Source,
    tick: Callable[[float, str | None], None],
    *,
    color: str | None,
) -> Written:
    """Build the cloud of a file and write it as `building` (see the module docstring)."""
    from bf_core.db import dialect
    from bf_core.drillholes.edit import author
    from bf_core.lines import cloud as lc
    from bf_core.lines import store as ls
    from bf_core.lines.cloud_build import stage_cloud

    from .objects import free_name

    tick(0.0, "чтение точек облака")
    staged = stage_cloud(store, src.chunks, lambda f: tick(f, "сборка облака"), expected=src.expected)
    warnings = src.warnings
    if staged.skipped:
        warnings.append(
            f"Точек без места в облаке (координаты не числа или дальше ±1 000 000 000 м): {n_text(staged.skipped)} — "
            "они не загружены."
        )
    crs = src.crs
    meta = {
        "format": src.format, "label": src.label, "source_format": src.format, "filename": g.main.name,
        "size": _size(g.main), "encoding": src.meta.get("xyz", {}).get("encoding"), "fields": [],
        "crs_name": None if crs is None else crs.name, "epsg": None if crs is None else crs.epsg,
        "sidecars": [], "warnings": warnings[:MAX_NOTES], "left_out": [], "cloud": True,
    }  # fmt: skip
    who = author()
    try:
        with dialect.write_tx(engine) as conn:
            file_id = ls.create_file(
                conn, store, name=free_name(conn, ls.LINE_FILE, g.main.name), meta=meta,
                folder=ctx.params.get("folder") or None, state="building", job_id=ctx.id,
                crs=None if crs is None else crs.wkt, author=who,
            )  # fmt: skip
            cloud_id = lc.commit_cloud(
                conn, store, staged, file_id=file_id, name=Path(g.main.name).stem, color=color, source=src.meta,
                author=who,
            )  # fmt: skip
    finally:
        staged.discard()
    tick(1.0, "облако записано")
    counts = {
        "layers": 1, "lines": 0, "points": staged.count, "vertices": 0, "cloud_id": cloud_id,
        "parts": len(staged.parts), "skipped": staged.skipped, "every": src.every,
        "classes": {str(k): v for k, v in sorted(staged.classes.items())},
    }  # fmt: skip
    settings = {"cloud": True, "every": src.every, **({"xyz": src.meta["xyz"]} if "xyz" in src.meta else {})}
    return Written(file_id, cloud_id, counts, settings, warnings)


__all__ = ["CLOUD_OPTIONS", "MEMORY_MB", "Source", "Written", "open_source", "summarize_cloud", "wanted", "write_cloud"]
