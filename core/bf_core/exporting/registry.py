"""The registry of export writers by object type (ТЗ 7.35 Д-17; ADR 0010 §1, ADR 0017 §5 «Реестр»).

Every pair «object type × format» the window «Экспорт проекта» shows is a `Writer` here, or the reason why there is
none (block models: DAT, CSV and Parquet since Э17). The writers are thin wrappers over the writers of the
modules (`exporting/writers.py`, loaded by an explicit import as `HANDLER_MODULES` — no scan of the package);
the window, the data manager and the job `export.objects` use this one registry.

Types (`KINDS`; the folder of each in the export, ADR 0017 §5 «Папки») and their formats, the first one is the
default (decision 6) unless it is unavailable:

| type            | objects                                  | formats                                         |
|-----------------|------------------------------------------|-------------------------------------------------|
| `drillhole_set` | `objects(kind="drillhole_set")`          | CSV (folder of the set), XLSX (book), DAT, source |
| `wireframe`     | `wireframe` without `dtm`            | DXF, DWG (ODA), TRIDB, OBJ, STL, Surpac DTM, OMF, source |
| `dtm`           | `wireframe` with `dtm`                   | the same                                        |
| `line_file`     | `line_file` without a cloud              | Micromine STR, DXF, Surpac STR, OMF, source     |
| `cloud`         | `line_file` with a cloud layer           | LAS, CSV, source                                |
| `raster`        | `raster`                                 | source file with its georeference, GeoTIFF      |
| `block_model`   | `block_model`                            | OMF, DAT, CSV and Parquet, source           |

«Исходный файл» (`source`) — the file as loaded (the `source` blob of the object or of its import, with its
`sidecar:*` companions), byte for byte, under its own name; an object made in the project has none, and the job
lists it among the skipped with the reason (`Writer.accepts`).

This module is imported by the API before BF-READY: no numpy, pyarrow or writers here (tests/test_process.py).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

import sqlalchemy as sa

from bf_core.db.schema import (
    blob_owners,
    blobs,
    dh_holes,
    dh_tables,
    import_log,
    line_layers,
    objects,
    rasters,
    wireframes,
)

from . import names

#: Modules whose import registers writers (an explicit list: no scan of the package in the frozen .exe).
WRITER_MODULES: tuple[str, ...] = ("bf_core.exporting.writers",)
SOURCE = "source"


@dataclass(frozen=True)
class Kind:
    key: str
    title: str  # the row of the window
    folder: str  # the folder of the type in the export


KINDS: tuple[Kind, ...] = (
    Kind("drillhole_set", "Наборы скважин", "Скважины"),
    Kind("wireframe", "Каркасы", "Каркасы"),
    Kind("dtm", "ЦТМ", "ЦТМ"),
    Kind("line_file", "Линии", "Линии"),
    Kind("cloud", "Облака точек", "Облака точек"),
    Kind("raster", "Растры", "Растры"),
    Kind("block_model", "Блочные модели", "Блочные модели"),
)
KIND = {k.key: k for k in KINDS}


@dataclass(frozen=True)
class Reason:
    """Why a format (or an object) cannot be written: a message and a hint, in Russian."""

    message: str
    hint: str = ""

    def public(self) -> dict[str, str]:
        return {"reason": self.message, "hint": self.hint}


@dataclass(frozen=True)
class Source:
    """A file the object was loaded from: its blob, its name as loaded, its companions {suffix: sha256}."""

    sha256: str
    name: str
    sidecars: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Item:
    """An object to export. `size` — bytes of its data (the progress of the job goes by them); `info` — what
    its writers need to know without reading it (counts, ids of layers)."""

    id: int
    kind: str
    name: str
    folder: str | None
    size: int = 0
    sources: tuple[Source, ...] = ()
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def stem(self) -> str:
        """The name of the files of the object (a line file, a cloud or a raster is named after its file)."""
        return names.stem_of(self.name) if self.kind in ("line_file", "cloud", "raster") else self.name


@dataclass
class Env:
    """What writers work with: the project (engine, blob store), «Настройка» (ODA File Converter) and the files
    of sources written by this export (one file of a TRIDB for its ten wireframes)."""

    engine: Any = None
    store: Any = None
    oda: str | None = None
    sources_written: dict[str, Path] = field(default_factory=dict)


@dataclass
class Written:
    files: list[Path]
    size: int
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


Tick = Callable[..., None]


def _always(_env: Env) -> Reason | None:
    return None


def _everything(_item: Item) -> Reason | None:
    return None


def _small(_item: Item) -> int:
    return 256


@dataclass(frozen=True)
class Writer:
    """One pair «type × format». `suffixes(item, env)` — the endings of the files it writes for an object, the
    main one first ("" — a folder: the CSV of a set); `write(item, path, env, tick)` writes them at
    `path` = folder + stem + first suffix and returns them; `available(env)` — None or why the format cannot be
    written at all (DWG without ODA); `accepts(item)` — None or why this object cannot be (no source file);
    `memory(item)` — MB for the scheduler."""

    kind: str
    format: str
    title: str
    write: Callable[[Item, Path, Env, Tick], Written] | None
    suffixes: Callable[[Item, Env], tuple[str, ...]]
    available: Callable[[Env], Reason | None] = _always
    accepts: Callable[[Item], Reason | None] = _everything
    memory: Callable[[Item], int] = _small
    stem: Callable[[Item], str] | None = None

    def stem_of(self, item: Item) -> str:
        return self.stem(item) if self.stem is not None else item.stem


REGISTRY: dict[tuple[str, str], Writer] = {}
ORDER: dict[str, list[str]] = {k.key: [] for k in KINDS}
_loaded = False


def register(writer: Writer) -> Writer:
    if writer.kind not in KIND:
        raise ValueError(f"unknown kind {writer.kind}")
    if (writer.kind, writer.format) not in REGISTRY:
        ORDER[writer.kind].append(writer.format)
    REGISTRY[(writer.kind, writer.format)] = writer
    return writer


def unavailable(kind: str, fmt: str, title: str, reason: Reason) -> Writer:
    """A pair the window shows, with the reason it cannot be written yet."""
    return register(Writer(kind, fmt, title, None, lambda _i, _e: (), available=lambda _e: reason))


def load_writers() -> None:
    global _loaded
    if not _loaded:
        for module in WRITER_MODULES:
            importlib.import_module(module)
        _loaded = True


def writer(kind: str, fmt: str) -> Writer | None:
    load_writers()
    return REGISTRY.get((kind, fmt))


def formats_of(kind: str) -> list[Writer]:
    load_writers()
    return [REGISTRY[(kind, f)] for f in ORDER.get(kind, [])]


def reason_of(w: Writer, env: Env) -> Reason | None:
    if w.write is None:
        return w.available(env) or Reason("Формат пока не поддерживается.")
    return w.available(env)


# --- the objects of the project ------------------------------------------------------------------------------


def _sources(conn: sa.Connection, ids: Sequence[int]) -> dict[int, list[Source]]:
    """The files the objects were loaded from: blobs of the object (`source`, `sidecar:*`) or of its imports."""
    out: dict[int, list[Source]] = {}
    if not ids:
        return out
    own: dict[int, dict[str, str]] = {}
    for r in conn.execute(
        sa.select(blob_owners.c.owner_id, blob_owners.c.role, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == "object",
            blob_owners.c.owner_id.in_(ids),
            sa.or_(blob_owners.c.role == SOURCE, blob_owners.c.role.like("sidecar:%")),
        )
    ):
        own.setdefault(int(r.owner_id), {})[str(r.role)] = str(r.sha256)
    logs = conn.execute(
        sa.select(import_log.c.id, import_log.c.object_id, import_log.c.file_name, import_log.c.sha256)
        .where(import_log.c.object_id.in_(ids))
        .order_by(import_log.c.id.desc())
    ).all()
    side: dict[int, dict[str, str]] = {}
    log_ids = [int(r.id) for r in logs]
    for start in range(0, len(log_ids), 500):
        for r in conn.execute(
            sa.select(blob_owners.c.owner_id, blob_owners.c.role, blob_owners.c.sha256).where(
                blob_owners.c.owner_kind == "import",
                blob_owners.c.owner_id.in_(log_ids[start : start + 500]),
                blob_owners.c.role.like("sidecar:%"),
            )
        ):
            side.setdefault(int(r.owner_id), {})[str(r.role)] = str(r.sha256)
    names_of: dict[int, str] = {}
    for r in logs:  # the latest import of the object names its file
        names_of.setdefault(int(r.object_id), str(r.file_name))
    for obj_id, roles in own.items():
        if SOURCE in roles:
            out[obj_id] = [Source(roles[SOURCE], _file_name(names_of.get(obj_id)), _sides(roles))]
    for r in logs:
        obj_id = int(r.object_id)
        if obj_id in own and SOURCE in own[obj_id] or r.sha256 is None:
            continue
        found = out.setdefault(obj_id, [])
        if all(s.sha256 != r.sha256 for s in found):
            found.append(Source(str(r.sha256), _file_name(r.file_name), _sides(side.get(int(r.id), {}))))
    return out


def _sides(roles: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(("." + role.split(":", 1)[1], sha) for role, sha in roles.items() if role.startswith("sidecar:"))
    )


def _file_name(name: str | None) -> str:
    return PureWindowsPath(str(name or "")).name or names.EMPTY


def items(conn: sa.Connection, kinds: Iterable[str] | None = None, ids: Iterable[int] | None = None) -> list[Item]:
    """The objects of the project to export, by type in the order of `KINDS`, by folder and name in a type."""
    wanted = set(kinds) if kinds is not None else set(KIND)
    chosen = None if ids is None else {int(i) for i in ids}
    base = sa.select(objects.c.id, objects.c.kind, objects.c.name, objects.c.folder, objects.c.meta).where(
        objects.c.state == "ready"
    )
    if chosen is not None:
        base = base.where(objects.c.id.in_(sorted(chosen)))
    out: list[Item] = []
    rows = conn.execute(
        base.where(objects.c.kind.in_(("drillhole_set", "wireframe", "line_file", "raster", "block_model")))
    ).all()
    by_kind: dict[str, list[Any]] = {}
    for r in rows:
        by_kind.setdefault(str(r.kind), []).append(r)
    sources = _sources(conn, [int(r.id) for r in rows])
    blob_size = _blob_sizes(conn, sources)

    def item(r: Any, kind: str, size: int, **info: Any) -> Item:
        src = tuple(sources.get(int(r.id), ()))
        return Item(int(r.id), kind, str(r.name), r.folder, max(size, 1), src, info)

    sets = [int(r.id) for r in by_kind.get("drillhole_set", [])]
    table_rows: dict[int, Any] = dict(
        conn.execute(  # type: ignore[arg-type]
            sa.select(dh_tables.c.set_id, sa.func.sum(dh_tables.c.row_count)).where(dh_tables.c.set_id.in_(sets))
            .group_by(dh_tables.c.set_id)
        ).all()
    ) if sets else {}  # fmt: skip
    holes: dict[int, Any] = dict(
        conn.execute(  # type: ignore[arg-type]
            sa.select(dh_holes.c.set_id, sa.func.count()).where(dh_holes.c.set_id.in_(sets)).group_by(dh_holes.c.set_id)
        ).all()
    ) if sets else {}  # fmt: skip
    for r in by_kind.get("drillhole_set", []):
        rows_n = int(table_rows.get(r.id) or 0) + 2 * int(holes.get(r.id) or 0)  # collars and surveys
        out.append(item(r, "drillhole_set", 100 * rows_n, rows=rows_n))
    wf = {int(r.id): r for r in by_kind.get("wireframe", [])}
    if wf:
        for w in conn.execute(
            sa.select(wireframes.c.object_id, wireframes.c.point_count, wireframes.c.triangle_count, wireframes.c.dtm)
            .where(wireframes.c.object_id.in_(sorted(wf)))
        ):  # fmt: skip
            geometry = 24 * int(w.point_count) + 12 * int(w.triangle_count)
            kind = "dtm" if w.dtm else "wireframe"
            out.append(item(wf[int(w.object_id)], kind, geometry, geometry=geometry, triangles=int(w.triangle_count)))
    files = {int(r.id): r for r in by_kind.get("line_file", [])}
    if files:
        layers: dict[int, list[Any]] = {}
        for lr in conn.execute(
            sa.select(objects.c.id, objects.c.parent_id, objects.c.meta, line_layers.c.point_count)
            .join(line_layers, line_layers.c.object_id == objects.c.id)
            .where(objects.c.parent_id.in_(sorted(files)), objects.c.state == "ready")
            .order_by(objects.c.sort_key, objects.c.id)
        ):  # fmt: skip
            layers.setdefault(int(lr.parent_id), []).append(lr)
        for file_id, r in files.items():
            own = layers.get(file_id, [])
            points = sum(int(lr.point_count) for lr in own)
            clouds = [int(lr.id) for lr in own if isinstance((lr.meta or {}).get("cloud"), dict)]
            kind = "cloud" if clouds else "line_file"
            ids_ = [int(lr.id) for lr in own]
            out.append(
                item(r, kind, 24 * points, points=points, layer_ids=ids_, cloud_id=clouds[0] if clouds else None)
            )
    ras = {int(r.id): r for r in by_kind.get("raster", [])}
    if ras:
        for rr in conn.execute(
            sa.select(rasters.c.object_id, rasters.c.width, rasters.c.height).where(
                rasters.c.object_id.in_(sorted(ras))
            )
        ):
            pixels = int(rr.width) * int(rr.height)
            out.append(item(ras[int(rr.object_id)], "raster", blob_size.get(int(rr.object_id), pixels), pixels=pixels))
    for r in by_kind.get("block_model", []):
        meta = r.meta or {}
        cells = int(meta.get("cells") or 0)
        counts = (meta.get("grid") or {}).get("counts") or [0, 0, 0]
        grid_cells = int(counts[0]) * int(counts[1]) * int(counts[2])  # OMF writes the whole grid
        out.append(item(r, "block_model", 8 * cells * (1 + len(meta.get("fields") or [])), cells=cells,
                        grid_cells=grid_cells))  # fmt: skip
    order = {k.key: n for n, k in enumerate(KINDS)}
    out = [i for i in out if i.kind in wanted]
    out.sort(key=lambda i: (order[i.kind], names.key(i.folder or ""), names.key(i.name), i.id))
    return out


def _blob_sizes(conn: sa.Connection, sources: dict[int, list[Source]]) -> dict[int, int]:
    shas = sorted({s.sha256 for found in sources.values() for s in found})
    if not shas:
        return {}
    size: dict[str, int] = {
        str(r.sha256): int(r.size)
        for r in conn.execute(sa.select(blobs.c.sha256, blobs.c.size).where(blobs.c.sha256.in_(shas)))
    }
    return {obj_id: sum(int(size.get(s.sha256) or 0) for s in found) for obj_id, found in sources.items()}


# --- GET /export/formats ---------------------------------------------------------------------------------------


def formats(conn: sa.Connection, env: Env) -> dict[str, Any]:
    """The table «тип → формат» of the window: for each type the number of objects, its formats with whether
    each can be written and why not, and the default (the first available). The contract of `GET
    /export/formats` (ADR 0017 §5):

        {"types": [{"kind", "title", "folder", "count", "default": format | null,
                    "formats": [{"format", "title", "ext", "available", "reason", "hint"}]}]}

    `ext` — the extension of the main file («.dxf»; "" — a folder of files, or the extension of the source).

    «Исходный файл» is available when at least one object of the type has one."""
    load_writers()
    found = items(conn)
    out = []
    for k in KINDS:
        own = [i for i in found if i.kind == k.key]
        rows = []
        for w in formats_of(k.key):
            why = reason_of(w, env)
            if why is None and own and all(w.accepts(i) is not None for i in own):
                why = w.accepts(own[0])
            rows.append(
                {"format": w.format, "title": w.title, "ext": _ext(w, k.key, env), "available": why is None,
                 **_public(why)}
            )  # fmt: skip
        default = next((r["format"] for r in rows if r["available"]), None)
        out.append({"kind": k.key, "title": k.title, "folder": k.folder, "count": len(own), "default": default,
                    "formats": rows})  # fmt: skip
    return {"types": out}


def _public(why: Reason | None) -> dict[str, str | None]:
    return {"reason": None, "hint": None} if why is None else {"reason": why.message, "hint": why.hint or None}


def _ext(w: Writer, kind: str, env: Env) -> str:
    if w.write is None or w.format == SOURCE:
        return ""
    try:
        return w.suffixes(Item(0, kind, "x", None), env)[0]
    except Exception:  # noqa: BLE001 - a writer that needs the object (a raster) shows no extension
        return ""
