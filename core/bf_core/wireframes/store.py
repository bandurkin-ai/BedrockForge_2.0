"""Wireframes of the project and the settings of a digital terrain model (ТЗ 7.20; ADR 0011 §1, §3).

A wireframe is `objects(kind="wireframe")` with a `wireframes` row and the geometry in immutable blobs of the
owner `object`: `xyz <f8 N×3` (the vertices as given, bit for bit, in their order) and `tri <i4 M×3`. The
triangles are stored along a Z-order curve of their centroids (the same triangles: the exact geometry does not
change), so the parts of the 3D view are runs of consecutive triangles; their table is `wireframes.render`
{part_triangles, parts [[first, count, xmin, ymin, zmin, xmax, ymax, zmax]]}. The levels of detail are derived
files in `cache\\mesh\\` (`viz/meshes.py`). `wireframes.stats` — `geo.meshes.mesh_stats`; the colour is
`objects.meta.color`; `stats.checks` — the job `wireframe.check` (`check.py`); `source` — its file, in brief.

A DTM is a wireframe with `wireframes.dtm` set: {clip, tint, opacity, shade}. The raster draped on it is the
row `object_refs(src=wireframe, dst=raster, role="drape")`, so a `.bfggis` renumbers it with the other
references and a raster in use cannot be deleted. «Вернуть в каркасы» clears the settings and the reference.
Attributes of the user are `wireframes.attrs`: a list [{name, value}] in the order they were added (a JSON
object would lose the order on PostgreSQL); value — text, number, yes/no or empty (null, never 0).

The version of a wireframe is `objects.version`: every change raises it, a stale `expected_version` is a 409
with the current wireframe. The geometry never changes in place (its ETags do not depend on the version).

Writing: `stage_wireframe` outside the write transaction (order, parts, measures, temp files with fsync),
`commit_wireframe` inside it; `StagedWireframe.discard()` drops the temp files when the commit does not happen.
Ported from BF `storage/geometry.py::store_mesh`, `models.py::Wireframe`, `routers/datasets.py::patch_wireframe`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, object_refs, objects, rasters, wireframes
from bf_core.storage.blobs import CHUNK, BlobStore

WIREFRAME = "wireframe"
RASTER = "raster"
OWNER = "object"
DRAPE = "drape"  # object_refs(src=wireframe, dst=raster): the raster draped on a DTM
ARRAY_MEDIA = "application/octet-stream"
ARRAYS: dict[str, str] = {"xyz": "<f8", "tri": "<i4"}
DTM_DEFAULTS: dict[str, Any] = {"clip": True, "tint": "#ffffff", "opacity": 1.0, "shade": True}
DTM_KEYS = ("clip", "tint", "opacity", "shade")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_ATTRS = 200
MAX_ATTR_NAME = 100
MAX_ATTR_TEXT = 2000
MAX_NAME = 200

Staged = tuple[Path, str, int]
Progress = Callable[[float, str], None] | None


def not_found() -> ApiError:
    return ApiError(404, "wireframe_not_found", "Каркас не найден.", "Обновите дерево проекта.")


def broken() -> ApiError:
    return ApiError(500, "wireframe_broken", "Файлы геометрии каркаса не найдены.", "Загрузите каркас заново.")


def _chunks(arr: np.ndarray) -> Iterator[bytes]:
    raw = memoryview(np.ascontiguousarray(arr)).cast("B")
    for start in range(0, len(raw), CHUNK):
        yield bytes(raw[start : start + CHUNK])


# --- writing ---------------------------------------------------------------------------------------------


@dataclass
class StagedWireframe:
    """A wireframe ready to be committed: its arrays (triangles in spatial order), measures and temp files."""

    xyz: np.ndarray
    tri: np.ndarray
    parts: list[Any]  # geo.meshes.MeshPart
    stats: dict[str, Any]
    render: dict[str, Any]
    extent: list[float] | None
    origin: list[float]
    files: dict[str, Staged] = field(default_factory=dict)
    shas: dict[str, str] = field(default_factory=dict)
    seq: np.ndarray | None = None  # <u4: the place of every stored triangle in the mesh as given (source.py)

    def discard(self) -> None:
        for tmp, _sha, _size in self.files.values():
            tmp.unlink(missing_ok=True)
        self.files.clear()


def render_of(parts: Sequence[Any], part_triangles: int) -> dict[str, Any]:
    return {"part_triangles": int(part_triangles), "parts": [[p.first, p.count, *p.bbox] for p in parts]}


def stage_wireframe(store: BlobStore, xyz: Any, tri: Any, *, progress: Progress = None) -> StagedWireframe:
    """Check the mesh, put its triangles in spatial order, cut the parts, measure it and write the blobs into
    temporary files (no database access). `progress(fraction, message)` may raise to cancel."""
    from bf_core.geo import meshes

    def tick(value: float, message: str) -> None:
        if progress:
            progress(value, message)

    def part(start: float, stop: float, message: str) -> Callable[[float], None]:
        return lambda f: tick(start + (stop - start) * f, message)

    tick(0.0, "Проверка каркаса")
    try:
        v, t = meshes.check_mesh(xyz, tri)
    except meshes.MeshError as exc:
        raise ApiError(422, exc.code, exc.message, exc.hint) from exc
    order = meshes.morton_order(v, t, part(0.05, 0.3, "Пространственный порядок треугольников"))
    t = np.ascontiguousarray(t[order])
    seq = order.astype("<u4")
    del order
    parts = meshes.parts_table(v, t, progress=part(0.3, 0.45, "Части каркаса"))
    stats = meshes.mesh_stats(v, t, part(0.45, 0.75, "Площадь и объём"))
    extent = meshes.extent_of(parts)
    staged = StagedWireframe(
        v, t, parts, stats, render_of(parts, meshes.PART_TRIANGLES), extent,
        list(meshes.origin_of(tuple(extent))) if extent else [0.0, 0.0, 0.0], seq=seq,
    )  # fmt: skip
    tick(0.75, "Запись каркаса")
    try:
        for role, arr in (("xyz", v), ("tri", t)):
            staged.files[role] = store.stage_stream(_chunks(np.asarray(arr, dtype=ARRAYS[role])))
            staged.shas[role] = staged.files[role][1]
        tick(1.0, "Запись каркаса")  # a cancel here must not leave the temp files either
    except BaseException:
        staged.discard()
        raise
    return staged


def free_name(conn: sa.Connection, name: str, exclude_id: int | None = None) -> str:
    """`name`, or «name (2)», «name (3)»… when a wireframe of the project has that name already."""
    base = " ".join(str(name).split())[:MAX_NAME] or "Каркас"
    taken = set(conn.execute(sa.select(objects.c.name_key).where(objects.c.kind == WIREFRAME)).scalars())
    if exclude_id is not None:
        own = conn.execute(sa.select(objects.c.name_key).where(objects.c.id == exclude_id)).scalar()
        taken.discard(own)
    candidate, k = base, 1
    while dialect.name_key(candidate) in taken:
        k += 1
        suffix = f" ({k})"
        candidate = base[: MAX_NAME - len(suffix)] + suffix
    return candidate


def commit_wireframe(
    conn: sa.Connection,
    store: BlobStore,
    staged: StagedWireframe,
    *,
    name: str,
    color: str | None = None,
    attrs: Any = None,
    dtm: Mapping[str, Any] | bool | None = None,
    raster_id: int | None = None,
    meta: Mapping[str, Any] | None = None,
    parent_id: int | None = None,
    folder: str | None = None,
    state: str = "ready",
    job_id: int | None = None,
    crs: str | None = None,
    author: str | None = None,
    sort_key: float = 0.0,
) -> int:
    """Inside the write transaction: the object, its blobs and its `wireframes` row. `dtm` True — a DTM with
    the default settings, a mapping — with these; `raster_id` — the raster draped on it."""
    now = dialect.utcnow()
    info = dict(meta or {})
    if color is not None:
        info["color"] = check_color(color)
    settings = None if dtm is None or dtm is False else _dtm_settings({}, {} if dtm is True else dtm)
    res = conn.execute(
        sa.insert(objects).values(
            kind=WIREFRAME,
            name=name,
            name_key=dialect.name_key(name),
            parent_id=parent_id,
            folder=folder,
            sort_key=sort_key,
            state=state,
            version=1,
            meta=info,
            extent=staged.extent,
            crs=crs,
            job_id=job_id,
            author=author,
            created_at=now,
            updated_at=now,
        )  # fmt: skip
    )
    wf_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    for role, tmp in list(staged.files.items()):
        store.commit_staged(conn, tmp, media_type=ARRAY_MEDIA, owner_kind=OWNER, owner_id=wf_id, role=role)
        del staged.files[role]  # in place now: a later discard() must not touch it
    conn.execute(
        sa.insert(wireframes).values(
            object_id=wf_id,
            point_count=len(staged.xyz),
            triangle_count=len(staged.tri),
            origin=staged.origin,
            stats=staged.stats,
            render=staged.render,
            attrs=_attrs_in(attrs),
            dtm=settings,
        )
    )
    if raster_id and settings is not None:
        _drape(conn, wf_id, int(raster_id))
    return wf_id


def put_wireframe(conn: sa.Connection, store: BlobStore, xyz: Any, tri: Any, *, name: str, **kw: Any) -> int:
    """Stage and commit in one go (small meshes, tests). The temp files go if the commit fails."""
    staged = stage_wireframe(store, xyz, tri)
    try:
        return commit_wireframe(conn, store, staged, name=name, **kw)
    finally:
        staged.discard()


# --- reading ---------------------------------------------------------------------------------------------

_COLUMNS = (
    objects.c.id, objects.c.name, objects.c.folder, objects.c.version, objects.c.meta, objects.c.extent,
    objects.c.crs, objects.c.created_at, wireframes.c.point_count, wireframes.c.triangle_count,
    wireframes.c.origin, wireframes.c.stats, wireframes.c.attrs, wireframes.c.dtm,
)  # fmt: skip


def blob_shas(conn: sa.Connection, wf_id: int) -> dict[str, str]:
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == wf_id, blob_owners.c.role.in_(list(ARRAYS))
        )
    ).all()
    return {str(r.role): str(r.sha256) for r in rows}


def attrs_list(raw: Any) -> list[dict[str, Any]]:
    """`wireframes.attrs` as a list [{name, value}] (a plain object of an older writer is read too)."""
    if isinstance(raw, Mapping):
        return [{"name": str(k), "value": v} for k, v in raw.items()]
    return [{"name": str(a["name"]), "value": a.get("value")} for a in raw or [] if isinstance(a, Mapping)]


def drapes(conn: sa.Connection, ids: Sequence[int]) -> dict[int, int]:
    """wireframe → the raster draped on it."""
    if not ids:
        return {}
    rows = conn.execute(
        sa.select(object_refs.c.src_id, object_refs.c.dst_id)
        .where(object_refs.c.role == DRAPE, object_refs.c.src_id.in_(list(ids)))
        .order_by(object_refs.c.dst_id)
    ).all()
    return {int(r.src_id): int(r.dst_id) for r in rows}


def dtm_public(dtm: Mapping[str, Any] | None, raster_id: int | None) -> dict[str, Any] | None:
    """The settings of a DTM as the API gives them: {raster_id, clip, tint, opacity, shade}; null — not a DTM."""
    if dtm is None:
        return None
    return {"raster_id": raster_id, **DTM_DEFAULTS, **{k: dtm[k] for k in DTM_KEYS if k in dtm}}


def _public(row: Mapping[str, Any], raster_id: int | None) -> dict[str, Any]:
    out = dict(row)
    meta = out.pop("meta") or {}
    src = {k: v for k in ("format", "file", "name", "layer", "precision") if (v := (meta.get("source") or {}).get(k))}
    out.update(color=meta.get("color"), built=meta.get("built"), source=src or None)
    out["attrs"] = attrs_list(out["attrs"])
    out["stats"] = out["stats"] or {}
    out["created_at"] = out["created_at"].isoformat()
    out["dtm"] = dtm_public(out["dtm"], raster_id)
    return out


def _rows(conn: sa.Connection, *where: Any) -> list[dict[str, Any]]:
    rows = (
        conn.execute(
            sa.select(*_COLUMNS)
            .join(wireframes, wireframes.c.object_id == objects.c.id)
            .where(objects.c.kind == WIREFRAME, objects.c.state == "ready", *where)
            .order_by(objects.c.sort_key, objects.c.id)
        )
        .mappings()
        .all()
    )
    drape = drapes(conn, [int(r["id"]) for r in rows])
    return [_public(dict(r), drape.get(int(r["id"]))) for r in rows]


def list_wireframes(conn: sa.Connection) -> list[dict[str, Any]]:
    """Wireframes of the project in the order of the tree; a DTM has `dtm` set («ЦТМ»), the rest — null."""
    return _rows(conn)


def wireframe_info(conn: sa.Connection, wf_id: int) -> dict[str, Any]:
    found = _rows(conn, objects.c.id == wf_id)
    if not found:
        raise not_found()
    return found[0]


def read_wireframe(conn: sa.Connection, store: BlobStore, wf_id: int) -> tuple[np.ndarray, np.ndarray]:
    """The exact geometry: vertices float64 n×3 and triangles int32 m×3 (in memory)."""
    wireframe_info(conn, wf_id)
    shas = blob_shas(conn, wf_id)
    if set(shas) != set(ARRAYS):
        raise broken()
    xyz = np.fromfile(store.path_of(shas["xyz"]), dtype=ARRAYS["xyz"]).reshape(-1, 3)
    tri = np.fromfile(store.path_of(shas["tri"]), dtype=ARRAYS["tri"]).reshape(-1, 3)
    return xyz, tri


# --- changing --------------------------------------------------------------------------------------------


def _conflict(current: dict[str, Any]) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        "Каркас изменили, пока вы его правили.",
        "Свойства каркаса обновлены — повторите правку.",
        extra={"wireframe": current},
    )


def _current(conn: sa.Connection, wf_id: int, expected_version: int) -> dict[str, Any]:
    current = wireframe_info(conn, wf_id)
    if int(current["version"]) != expected_version:
        raise _conflict(current)
    return current


def _save(conn: sa.Connection, wf_id: int, version: int, own: Mapping[str, Any], **values: Any) -> dict[str, Any]:
    """Store a change: the `wireframes` columns of `own`, the `objects` columns of `values`, the next version."""
    n = conn.execute(
        sa.update(objects)
        .where(objects.c.id == wf_id, objects.c.version == version)
        .values(**values, version=version + 1, updated_at=dialect.utcnow())
    ).rowcount
    if n != 1:
        raise _conflict(wireframe_info(conn, wf_id))
    if own:
        conn.execute(sa.update(wireframes).where(wireframes.c.object_id == wf_id).values(**own))
    return wireframe_info(conn, wf_id)


def check_color(value: Any) -> str:
    if not isinstance(value, str) or not COLOR_RE.match(value):
        raise ApiError(422, "bad_color", "Цвет задаётся как #rrggbb.", "Выберите цвет в палитре.")
    return value.lower()


def update_wireframe(
    conn: sa.Connection, wf_id: int, expected_version: int, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Rename and recolour. `changes` holds only what is set: `name`, `color` ("#rrggbb"; None — no own colour)."""
    current = _current(conn, wf_id, expected_version)
    values: dict[str, Any] = {}
    if "name" in changes:
        name = " ".join(str(changes["name"] or "").split())
        if not name:
            raise ApiError(422, "name_empty", "Название каркаса не может быть пустым.", "Введите название.")
        if len(name) > MAX_NAME:
            raise ApiError(422, "name_too_long", f"Название длиннее {MAX_NAME} знаков.", "Сократите название.")
        if free_name(conn, name, exclude_id=wf_id) != name:
            raise ApiError(409, "name_taken", f"Каркас «{name}» уже есть.", "Введите другое название.")
        values.update(name=name, name_key=dialect.name_key(name))
    if "color" in changes:
        meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == wf_id)).scalar() or {})
        if changes["color"] is None:
            meta.pop("color", None)
        else:
            meta["color"] = check_color(changes["color"])
        values["meta"] = meta
    return _save(conn, wf_id, int(current["version"]), {}, **values)


# --- attributes of the user (ТЗ 7.20 «Атрибуты») ---------------------------------------------------------


def _attr_name(value: Any) -> str:
    name = " ".join(str(value if value is not None else "").split())
    if not name:
        raise ApiError(422, "attr_name_empty", "У атрибута должно быть название.", "Введите название атрибута.")
    if len(name) > MAX_ATTR_NAME:
        raise ApiError(
            422, "attr_name_too_long", f"Название атрибута длиннее {MAX_ATTR_NAME} знаков.", "Сократите название."
        )
    return name


def _attr_value(name: str, value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        if not math.isfinite(float(value)):
            raise ApiError(422, "not_a_number", f"Атрибут «{name}»: нужно число.", "Введите число, например 12,5.")
        return value
    if isinstance(value, str):
        text = value.strip()
        if len(text) > MAX_ATTR_TEXT:
            raise ApiError(
                422, "attr_too_long", f"Значение атрибута «{name}» длиннее {MAX_ATTR_TEXT} знаков.", "Сократите текст."
            )
        return text or None  # an empty text is an empty value
    raise ApiError(
        422, "bad_attr_value", f"Атрибут «{name}»: значение — текст, число, да/нет или пусто.", "Исправьте значение."
    )


def _attrs_in(raw: Any) -> list[dict[str, Any]]:
    """Attributes given at creation (a mapping or a list of {name, value}) checked like edited ones."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in attrs_list(raw):
        name = _attr_name(a["name"])
        if dialect.name_key(name) in seen:
            raise ApiError(422, "attr_exists", f"Атрибут «{name}» указан дважды.", "Оставьте одно значение.")
        seen.add(dialect.name_key(name))
        out.append({"name": name, "value": _attr_value(name, a["value"])})
    if len(out) > MAX_ATTRS:
        raise ApiError(422, "too_many_attrs", f"Атрибутов больше {MAX_ATTRS}.", "Удалите лишние атрибуты.")
    return out


def edit_attrs(
    conn: sa.Connection,
    wf_id: int,
    expected_version: int,
    *,
    values: Mapping[str, Any] | None = None,
    rename: Mapping[str, str] | None = None,
    remove: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Add, change, rename and delete attributes: first `rename` {old: new}, then `remove` [names], then
    `values` {name: value} (a new name is added at the end). Names are compared without case."""
    current = _current(conn, wf_id, expected_version)
    attrs = [dict(a) for a in current["attrs"]]

    def find(name: str) -> int:
        key = dialect.name_key(name)
        return next((i for i, a in enumerate(attrs) if dialect.name_key(a["name"]) == key), -1)

    def missing(name: str) -> ApiError:
        return ApiError(404, "attr_not_found", f"Атрибута «{name}» у каркаса нет.", "Обновите свойства каркаса.")

    for old, new in (rename or {}).items():
        at = find(str(old))
        if at < 0:
            raise missing(str(old))
        name = _attr_name(new)
        if find(name) not in (-1, at):
            raise ApiError(422, "attr_exists", f"Атрибут «{name}» уже есть.", "Введите другое название.")
        attrs[at]["name"] = name
    for name in remove or []:
        at = find(str(name))
        if at < 0:
            raise missing(str(name))
        del attrs[at]
    for raw, value in (values or {}).items():
        name = _attr_name(raw)
        at = find(name)
        if at < 0:
            attrs.append({"name": name, "value": _attr_value(name, value)})
        else:
            attrs[at]["value"] = _attr_value(attrs[at]["name"], value)
    if len(attrs) > MAX_ATTRS:
        raise ApiError(422, "too_many_attrs", f"Атрибутов больше {MAX_ATTRS}.", "Удалите лишние атрибуты.")
    return _save(conn, wf_id, int(current["version"]), {"attrs": attrs})


# --- DTM (ТЗ 7.20) ---------------------------------------------------------------------------------------


def _dtm_settings(base: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    out = {**DTM_DEFAULTS, **{k: base[k] for k in DTM_KEYS if k in base}}
    for key in ("clip", "shade"):
        if changes.get(key) is not None:
            out[key] = bool(changes[key])
    if changes.get("tint") is not None:
        out["tint"] = check_color(changes["tint"])
    if changes.get("opacity") is not None:
        opacity = float(changes["opacity"])
        if not 0.0 <= opacity <= 1.0:  # NaN fails too
            raise ApiError(
                422, "bad_opacity", "Непрозрачность растра — число от 0 до 1.", "Укажите значение от 0 до 1."
            )
        out["opacity"] = opacity
    return out


def _drape(conn: sa.Connection, wf_id: int, raster_id: int) -> None:
    """Put a raster of the project on the DTM (one raster a DTM). A vertical raster is refused."""
    row = conn.execute(
        sa.select(objects.c.name, rasters.c.vertical)
        .join(rasters, rasters.c.object_id == objects.c.id)
        .where(objects.c.id == raster_id, objects.c.kind == RASTER, objects.c.state == "ready")
    ).first()
    if row is None:
        raise ApiError(404, "raster_not_found", "Растр не найден.", "Обновите дерево проекта и выберите растр заново.")
    if row.vertical:
        raise ApiError(
            422,
            "drape_vertical_raster",
            f"Растр «{row.name}» стоит в вертикальной плоскости: на рельеф кладётся только растр в плане.",
            "Выберите карту или ортофото в плане.",
        )
    _undrape(conn, wf_id)
    conn.execute(sa.insert(object_refs).values(src_id=wf_id, dst_id=raster_id, role=DRAPE))


def _undrape(conn: sa.Connection, wf_id: int) -> None:
    conn.execute(sa.delete(object_refs).where(object_refs.c.src_id == wf_id, object_refs.c.role == DRAPE))


def set_dtm(conn: sa.Connection, wf_id: int, expected_version: int, changes: Mapping[str, Any]) -> dict[str, Any]:
    """`wireframe.dtm`: `on` False — «Вернуть в каркасы» (the settings and the raster are cleared, whatever else
    is given); otherwise the wireframe is a DTM (settings without `on` move it there) and the given settings
    change: `clip`, `tint`, `opacity`, `shade`, `raster_id` (0 or null takes the raster off)."""
    current = _current(conn, wf_id, expected_version)
    if changes.get("on") is False:
        _undrape(conn, wf_id)
        return _save(conn, wf_id, int(current["version"]), {"dtm": None})
    settings = _dtm_settings(current["dtm"] or {}, changes)
    if "raster_id" in changes:
        if changes["raster_id"]:
            _drape(conn, wf_id, int(changes["raster_id"]))
        else:
            _undrape(conn, wf_id)
    return _save(conn, wf_id, int(current["version"]), {"dtm": settings})


def dtm_users(conn: sa.Connection, raster_id: int) -> list[dict[str, Any]]:
    """The DTMs a raster is draped on (a raster in use is not deleted: ADR 0011 §1)."""
    rows = conn.execute(
        sa.select(objects.c.id, objects.c.name)
        .join(object_refs, object_refs.c.src_id == objects.c.id)
        .where(object_refs.c.dst_id == raster_id, object_refs.c.role == DRAPE)
        .order_by(objects.c.name_key, objects.c.id)
    ).all()
    return [{"id": int(r.id), "name": str(r.name)} for r in rows]


# --- deleting --------------------------------------------------------------------------------------------


def delete_wireframe(conn: sa.Connection, wf_id: int) -> dict[str, Any]:
    """Delete a wireframe, its DTM settings, its drape reference and its file references. A wireframe other
    objects refer to stays: 422 with their list. Returns {id, shas, render, shared}: `shared` — another object
    still owns the geometry (its derived files in `cache\\mesh\\` must stay)."""
    from bf_core.drillholes.store import drop_import_owners

    wireframe_info(conn, wf_id)
    users = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.kind)
        .join(object_refs, object_refs.c.src_id == objects.c.id)
        .where(object_refs.c.dst_id == wf_id)
        .order_by(objects.c.name_key, objects.c.id)
    ).all()
    if users:
        names = ", ".join(f"«{u.name}»" for u in users[:5]) + (" и другие" if len(users) > 5 else "")
        raise ApiError(
            422,
            "wireframe_in_use",
            f"Каркас используется: {names}.",
            "Удалите или измените эти объекты, затем удалите каркас.",
            extra={"objects": [{"id": int(u.id), "name": str(u.name), "kind": str(u.kind)} for u in users]},
        )
    shas = blob_shas(conn, wf_id)
    render = conn.execute(sa.select(wireframes.c.render).where(wireframes.c.object_id == wf_id)).scalar()
    drop_import_owners(conn, [wf_id])  # import_log goes by cascade: its source files must not stay owned
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == wf_id))
    conn.execute(sa.delete(objects).where(objects.c.id == wf_id))
    return {"id": wf_id, "shas": shas, "render": render, "shared": geometry_shared(conn, shas)}


def geometry_shared(conn: sa.Connection, shas: Mapping[str, str]) -> bool:
    """An object of the project owns the geometry of these blobs {role: sha256}: its derived files in
    `cache\\mesh\\` (the levels of detail, keyed by the blobs) are in use and must stay."""
    if not shas:
        return False
    owned = (
        sa.select(sa.func.count())
        .select_from(blob_owners)
        .where(blob_owners.c.sha256.in_(list(shas.values())), blob_owners.c.role.in_(list(ARRAYS)))
    )
    return bool(conn.execute(owned).scalar_one() > 0)
