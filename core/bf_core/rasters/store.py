"""Rasters of the project (ТЗ 7.19; ADR 0011 §1, §3).

A raster is `objects(kind="raster")` with a `rasters` row and blobs of the owner `object`: `source` (the image as
loaded), `sidecar:<ext>` (the georeference files that were read), `display0` (≤ 4096 px) and `display1`
(≤ 1024 px) — the copies the 3D view shows (`formats/raster_image.py`).

`rasters.georef` is `georef.describe`: affine (6, pixel corners), affine_z or null, source, crs, pixel, rotation,
azimuth, vertical, corners3d (4×3, an unknown Z is null) and the deviations at the control points of the other
files. `rasters.elevation` is the level of a plan raster (null — under the lowest object, never 0); a raster with
`affine_z` stands off the plan and has no elevation. Every change goes through `_save`: the georeference is
described again, `objects.extent` follows the corners, `rasters.version` and `objects.version` grow together and
a stale `expected_version` is a 409.

Writing a raster: `stage_displays` outside the write transaction, `create_raster` inside it; the source and the
sidecars are attached by the import job in its final transaction (`attach`).
Ported from BF `importing/objects.py` (`raster_placement`, `shift_raster_z`) and `routers/datasets.py`
(`patch_raster`); the section placement — from the interface of BF («На текущий разрез»).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, dh_holes, object_refs, objects, rasters, settings
from bf_core.formats import georef as gr
from bf_core.formats.errors import FormatError
from bf_core.storage.blobs import BlobStore

RASTER = "raster"
OWNER = "object"
DRAPE = "drape"  # object_refs(src=wireframe, dst=raster): the raster draped on a DTM
OPACITY_MIN, OPACITY_MAX = 0.05, 1.0
LEVELS = ("display0", "display1")
IMAGE_MEDIA = {"JPEG": "image/jpeg", "PNG": "image/png", "TIFF": "image/tiff", "BMP": "image/bmp"}
SECTION_NOTE = (
    "Файла привязки нет: изображение поставлено в плоскость линии разреза, уточните края и высоты в свойствах"
)

Staged = tuple[Path, str, int]
Extent = list[float | None]


def not_found() -> ApiError:
    return ApiError(404, "raster_not_found", "Растр не найден.", "Обновите дерево проекта.")


def metres(v: float) -> str:
    """A number for a message in Russian: «−5,25»."""
    return f"{v:.10g}".replace(".", ",").replace("-", "−")


# --- placement ---------------------------------------------------------------------------------------------


def placement(
    g: gr.Georef, width: int, height: int, checks: Sequence[Mapping[str, Any]] = ()
) -> tuple[dict[str, Any], float | None, bool, Extent]:
    """What the project keeps of a georeference: (`rasters.georef`, elevation, vertical, `objects.extent`)."""
    described = gr.describe(g, width, height, [dict(c) for c in checks])
    box = described["corners3d"]
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    zs = [p[2] for p in box if p[2] is not None]
    extent: Extent = [min(xs), min(ys), min(zs) if zs else None, max(xs), max(ys), max(zs) if zs else None]
    return described, (None if g.affine_z else g.elevation), bool(described["vertical"]), extent


def apply_level(g: gr.Georef, elevation: float | None = None, shift: float = 0.0) -> tuple[gr.Georef, list[str]]:
    """«Уровень растров по Z» of the import wizard (ТЗ 7.19): `elevation` replaces the level of a plan raster,
    `shift` then moves every raster, the corners of a vertical one too. Returns the notes for the journal."""
    notes: list[str] = []
    if elevation is not None and not g.affine_z:
        if g.elevation is not None and abs(g.elevation - elevation) > 1e-9:
            notes.append(f"Высота из файла привязки {metres(g.elevation)} м заменена на {metres(elevation)} м")
        g = replace(g, elevation=float(elevation))
    if shift:
        try:
            g = gr.shift_z(g, float(shift))
            notes.append(f"Сдвиг по Z: {'+' if shift > 0 else ''}{metres(shift)} м")
        except FormatError:
            notes.append(
                "Сдвиг по Z не применён: у растра нет высоты (он ляжет под самый низкий объект); "
                "задайте высоту в свойствах растра"
            )
    return g, notes


def _georef_of(row: Mapping[str, Any]) -> gr.Georef:
    d = row["georef"] or {}
    if len(d.get("affine") or ()) != 6:
        raise ApiError(
            422,
            "no_georef",
            "У растра нет привязки: менять его положение нечем.",
            "Поставьте растр на текущий разрез или загрузите его заново с файлом привязки.",
        )
    affine = tuple(float(v) for v in d["affine"])
    affine_z = tuple(float(v) for v in d["affine_z"]) if d.get("affine_z") else None
    return gr.Georef(affine, str(d.get("source") or gr.MANUAL), row["elevation"], affine_z=affine_z)  # type: ignore[arg-type]


# --- writing -----------------------------------------------------------------------------------------------


@dataclass
class StagedRaster:
    """The display copies of a raster in temporary files: role → (file, media type)."""

    files: dict[str, tuple[Staged, str]] = field(default_factory=dict)
    sizes: list[dict[str, Any]] = field(default_factory=list)  # [{level, width, height, mime}]

    def discard(self) -> None:
        for (tmp, _sha, _size), _media in self.files.values():
            tmp.unlink(missing_ok=True)
        self.files.clear()


def stage_displays(store: BlobStore, displays: Sequence[Any]) -> StagedRaster:
    """`raster_image.DisplayCopy` of both levels into temporary files (no database access)."""
    staged = StagedRaster()
    try:
        for copy in displays:
            role = LEVELS[int(copy.level)]
            staged.files[role] = (store.stage_bytes(copy.data), str(copy.mime))
            staged.sizes.append(
                {"level": int(copy.level), "width": copy.width, "height": copy.height, "mime": copy.mime}
            )
    except BaseException:
        staged.discard()
        raise
    return staged


def create_raster(
    conn: sa.Connection,
    store: BlobStore,
    *,
    name: str,
    width: int,
    height: int,
    georef: gr.Georef,
    checks: Sequence[Mapping[str, Any]] = (),
    displays: StagedRaster | None = None,
    meta: dict[str, Any] | None = None,
    opacity: float = 1.0,
    folder: str | None = None,
    state: str = "ready",
    job_id: int | None = None,
    author: str | None = None,
) -> int:
    """Inside the write transaction: the object, its `rasters` row and the display copies."""
    described, elevation, vertical, extent = placement(georef, width, height, checks)
    now = dialect.utcnow()
    info = dict(meta or {})
    if displays is not None:
        info["displays"] = displays.sizes
    res = conn.execute(
        sa.insert(objects).values(
            kind=RASTER,
            name=name,
            name_key=dialect.name_key(name),
            folder=folder,
            sort_key=0.0,
            state=state,
            version=1,
            meta=info,
            extent=extent,
            crs=described.get("crs"),
            job_id=job_id,
            author=author,
            created_at=now,
            updated_at=now,
        )  # fmt: skip
    )
    raster_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    conn.execute(
        sa.insert(rasters).values(
            object_id=raster_id,
            width=width,
            height=height,
            georef=described,
            elevation=elevation,
            vertical=vertical,
            opacity=opacity,
            version=1,
        )  # fmt: skip
    )
    if displays is not None:
        for role, (tmp, media) in list(displays.files.items()):
            store.commit_staged(conn, tmp, media_type=media, owner_kind=OWNER, owner_id=raster_id, role=role)
            del displays.files[role]  # in place now: a later discard() must not touch it
    return raster_id


def attach(conn: sa.Connection, store: BlobStore, object_id: int, role: str, staged: Staged, media: str) -> str:
    """A file of an object (`source`, `sidecar:<ext>`) — in the transaction that makes the object ready."""
    return store.commit_staged(conn, staged, media_type=media, owner_kind=OWNER, owner_id=object_id, role=role)


def free_name(conn: sa.Connection, kind: str, name: str, parent_id: int | None = None) -> str:
    """`name`, or «name (2)», «name (3)»… when an object of this kind (and parent) has it already."""
    name = " ".join(name.split())[:190] or "Без имени"
    same = [objects.c.kind == kind, objects.c.state.in_(("ready", "building"))]
    if parent_id is not None:
        same.append(objects.c.parent_id == parent_id)
    taken = {str(k) for k in conn.execute(sa.select(objects.c.name_key).where(*same)).scalars()}
    out, n = name, 1
    while dialect.name_key(out) in taken:
        n += 1
        out = f"{name} ({n})"
    return out


# --- reading -----------------------------------------------------------------------------------------------

_COLUMNS = (
    objects.c.id, objects.c.name, objects.c.folder, objects.c.state, objects.c.meta, objects.c.extent,
    objects.c.created_at, rasters.c.width, rasters.c.height, rasters.c.georef, rasters.c.elevation,
    rasters.c.vertical, rasters.c.opacity, rasters.c.version,
)  # fmt: skip


def _public(row: Any, images: Mapping[str, str]) -> dict[str, Any]:
    out = dict(row)
    meta = out.pop("meta") or {}
    out["created_at"] = out["created_at"].isoformat()
    out["format"], out["filename"] = meta.get("format"), meta.get("filename")
    out["sidecars"], out["warnings"] = meta.get("sidecars") or [], meta.get("warnings") or []
    out["displays"] = meta.get("displays") or []
    out["has_elevation"] = bool((out["georef"] or {}).get("affine_z")) or out["elevation"] is not None
    out["images"] = {role: images[role] for role in LEVELS if role in images}
    return out


def _blobs_of(conn: sa.Connection, ids: Sequence[int]) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {i: {} for i in ids}
    for a in range(0, len(ids), 500):  # SQLite parameter limit
        part = list(ids[a : a + 500])
        own = sa.and_(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id.in_(part))
        for r in conn.execute(sa.select(blob_owners.c.owner_id, blob_owners.c.role, blob_owners.c.sha256).where(own)):
            out[int(r.owner_id)][str(r.role)] = str(r.sha256)
    return out


def _row(conn: sa.Connection, raster_id: int) -> dict[str, Any]:
    row = (
        conn.execute(
            sa.select(*_COLUMNS)
            .join(rasters, rasters.c.object_id == objects.c.id)
            .where(objects.c.id == raster_id, objects.c.kind == RASTER, objects.c.state == "ready")
        )
        .mappings()
        .first()
    )
    if row is None:
        raise not_found()
    return dict(row)


def raster_info(conn: sa.Connection, raster_id: int) -> dict[str, Any]:
    return _public(_row(conn, raster_id), _blobs_of(conn, [raster_id])[raster_id])


def list_rasters(conn: sa.Connection) -> list[dict[str, Any]]:
    rows = (
        conn.execute(
            sa.select(*_COLUMNS)
            .join(rasters, rasters.c.object_id == objects.c.id)
            .where(objects.c.kind == RASTER, objects.c.state == "ready")
            .order_by(objects.c.sort_key, objects.c.id)
        )
        .mappings()
        .all()
    )
    files = _blobs_of(conn, [int(r["id"]) for r in rows])
    return [_public(r, files[int(r["id"])]) for r in rows]


def image_blob(conn: sa.Connection, raster_id: int, level: int) -> tuple[str, str, int]:
    """(sha256, media type, size) of a display copy; level 0 — ≤ 4096 px, 1 — ≤ 1024 px."""
    if level not in range(len(LEVELS)):
        raise ApiError(
            422, "bad_level", "Неизвестный уровень изображения растра.", "Укажите уровень 0 (до 4096 px) или 1."
        )
    _row(conn, raster_id)
    row = conn.execute(
        sa.select(blobs.c.sha256, blobs.c.media_type, blobs.c.size)
        .join(blob_owners, blob_owners.c.sha256 == blobs.c.sha256)
        .where(
            blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == raster_id, blob_owners.c.role == LEVELS[level]
        )
    ).first()
    if row is None:
        raise ApiError(
            404, "raster_image_missing", "Изображение растра не найдено в проекте.", "Загрузите растр заново."
        )
    return str(row.sha256), str(row.media_type), int(row.size)


# --- changing ----------------------------------------------------------------------------------------------


def _conflict(conn: sa.Connection, raster_id: int) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        "Растр изменили, пока вы его правили.",
        "Свойства растра обновлены — повторите правку.",
        extra={"raster": raster_info(conn, raster_id)},
    )


def _current(conn: sa.Connection, raster_id: int, expected_version: int) -> dict[str, Any]:
    row = _row(conn, raster_id)
    if int(row["version"]) != expected_version:
        raise _conflict(conn, raster_id)
    return row


def _save(
    conn: sa.Connection,
    row: Mapping[str, Any],
    g: gr.Georef | None = None,
    *,
    checks: Sequence[Mapping[str, Any]] | None = None,
    **values: Any,
) -> dict[str, Any]:
    """Store a changed raster: the georeference described again, the extent, the next version."""
    raster_id, version = int(row["id"]), int(row["version"])
    own: dict[str, Any] = {k: values.pop(k) for k in ("opacity",) if k in values}
    if g is not None:
        old = row["georef"] or {}
        kept = old.get("checks") or [] if checks is None else checks
        described, elevation, vertical, extent = placement(g, int(row["width"]), int(row["height"]), kept)
        described.update({k: old.get(k) for k in ("crs", "crs_name", "epsg")})
        own.update(georef=described, elevation=elevation, vertical=vertical)
        values["extent"] = extent
    n = conn.execute(
        sa.update(rasters)
        .where(rasters.c.object_id == raster_id, rasters.c.version == version)
        .values(**own, version=version + 1)
    ).rowcount
    if n != 1:
        raise _conflict(conn, raster_id)
    conn.execute(
        sa.update(objects)
        .where(objects.c.id == raster_id)
        .values(**values, version=version + 1, updated_at=dialect.utcnow())
    )
    return raster_info(conn, raster_id)


def _format_error(exc: FormatError) -> ApiError:
    return ApiError(422, exc.code, exc.message, exc.hint)


def dtm_users(conn: sa.Connection, raster_id: int) -> list[dict[str, Any]]:
    """The DTMs the raster is draped on: [{id, name}] by name."""
    rows = conn.execute(
        sa.select(objects.c.id, objects.c.name)
        .join(object_refs, object_refs.c.src_id == objects.c.id)
        .where(object_refs.c.dst_id == raster_id, object_refs.c.role == DRAPE)
        .order_by(objects.c.name_key, objects.c.id)
    ).all()
    return [{"id": int(r.id), "name": str(r.name)} for r in rows]


def _named(users: Sequence[Mapping[str, Any]]) -> str:
    return ", ".join(f"«{u['name']}»" for u in users[:5]) + (" и другие" if len(users) > 5 else "")


def _stays_in_plan(conn: sa.Connection, raster_id: int) -> None:
    """Before a raster is stood in a vertical plane: a raster a DTM wears stays in plan (ТЗ 7.20 — a vertical
    raster is not put on a relief, `wireframes.store._drape` refuses it with the same code)."""
    users = dtm_users(conn, raster_id)
    if users:
        raise ApiError(
            422,
            "drape_vertical_raster",
            f"Растр наложен на ЦТМ: {_named(users)}. На рельеф кладётся только растр в плане.",
            "Снимите растр в настройках этих ЦТМ, затем поставьте его в вертикальную плоскость.",
            extra={"wireframes": users},
        )


def update_raster(
    conn: sa.Connection, raster_id: int, expected_version: int, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """`raster.set` and the «Свойства» of a raster. `changes` holds only what is set: `name`, `opacity`,
    `elevation` (None clears it: under the lowest object) and `vertical` {x1, y1, x2, y2, z_top, z_bottom} — the
    left and right edges and the heights of a raster standing in a vertical plane (a raster draped on a DTM
    → 422 `drape_vertical_raster` with the list of the DTMs)."""
    row = _current(conn, raster_id, expected_version)
    values: dict[str, Any] = {}
    g: gr.Georef | None = None
    if "name" in changes:
        name = " ".join(str(changes["name"]).split())
        if not name:
            raise ApiError(422, "name_empty", "Название растра не может быть пустым.", "Введите название растра.")
        key = dialect.name_key(name)
        other = sa.and_(objects.c.kind == RASTER, objects.c.name_key == key, objects.c.id != raster_id)
        if conn.execute(sa.select(objects.c.id).where(other, objects.c.state.in_(("ready", "building")))).first():
            raise ApiError(409, "name_taken", f"Растр «{name}» уже есть.", "Введите другое название.")
        values.update(name=name, name_key=key)
    if "opacity" in changes:
        opacity = changes["opacity"]
        if opacity is None or not (OPACITY_MIN <= float(opacity) <= OPACITY_MAX):
            raise ApiError(422, "bad_opacity", "Непрозрачность растра — от 0,05 до 1.", "Введите число от 0,05 до 1.")
        values["opacity"] = float(opacity)
    if "vertical" in changes and changes["vertical"] is not None:
        _stays_in_plan(conn, raster_id)
        v = changes["vertical"]
        try:
            g = gr.vertical_georef(
                int(row["width"]), int(row["height"]), v["x1"], v["y1"], v["x2"], v["y2"], v["z_top"], v["z_bottom"],
                gr.MANUAL,
            )  # fmt: skip
        except FormatError as exc:
            raise _format_error(exc) from exc
    if "elevation" in changes:
        base = g or _georef_of(row)
        if base.affine_z:
            raise ApiError(
                422,
                "vertical_raster",
                "У вертикального растра нет одной высоты: он стоит между «Верх Z» и «Низ Z».",
                "Измените «Верх Z» и «Низ Z» или сдвиньте растр по Z.",
            )
        level = changes["elevation"]
        if level is not None and not math.isfinite(float(level)):
            raise ApiError(422, "not_a_number", "«Высота»: нужно число.", "Введите число, например 250,5.")
        g = replace(base, elevation=None if level is None else float(level))
    if g is not None and "vertical" in changes and changes["vertical"] is not None:
        return _save(conn, row, g, checks=[], **values)  # the control points belonged to the old placement
    return _save(conn, row, g, **values)


def shift_raster(conn: sa.Connection, raster_id: int, dz: float, expected_version: int) -> dict[str, Any]:
    """«Сдвинуть» (ТЗ 7.19): the level of a plan raster, every corner of a vertical one. A plan raster without
    an elevation has nothing to move: 422 `no_elevation`."""
    row = _current(conn, raster_id, expected_version)
    if not math.isfinite(dz):
        raise ApiError(422, "not_a_number", "«Сдвиг по Z»: нужно число.", "Введите число, например −5.")
    try:
        g = gr.shift_z(_georef_of(row), float(dz))
    except FormatError as exc:
        raise _format_error(exc) from exc
    return _save(conn, row, g)


def delete_raster(conn: sa.Connection, raster_id: int) -> int:
    """Delete a raster and its file references. A raster draped on a DTM stays: 422 with the list of the DTMs."""
    from bf_core.drillholes.store import drop_import_owners

    _row(conn, raster_id)
    users = dtm_users(conn, raster_id)
    if users:
        raise ApiError(
            422,
            "raster_in_use",
            f"Растр наложен на ЦТМ: {_named(users)}.",
            "Снимите растр в настройках этих ЦТМ, затем удалите его.",
            extra={"wireframes": users},
        )
    drop_import_owners(conn, [raster_id])  # import_log goes by cascade: its source files must not stay owned
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == raster_id))
    conn.execute(sa.delete(objects).where(objects.c.id == raster_id))
    return raster_id


# --- the section line (ТЗ 7.19 «На текущий разрез») ----------------------------------------------------------


def data_extent(conn: sa.Connection) -> Extent | None:
    """[xmin, ymin, zmin, xmax, ymax, zmax] of the data of the project: lines, wireframes (`objects.extent`) and
    drillholes (collars down to their depth). Rasters do not count: they are placed by the data."""
    lo: list[float | None] = [None, None, None]
    hi: list[float | None] = [None, None, None]

    def grow(values: Sequence[Any]) -> None:
        for k in range(3):
            a, b = values[k], values[k + 3]
            if a is not None and (lo[k] is None or a < lo[k]):  # type: ignore[operator]
                lo[k] = float(a)
            if b is not None and (hi[k] is None or b > hi[k]):  # type: ignore[operator]
                hi[k] = float(b)

    ready = sa.and_(objects.c.state == "ready", objects.c.kind != RASTER)
    for ext in conn.execute(sa.select(objects.c.extent).where(ready, objects.c.extent.is_not(None))).scalars():
        if isinstance(ext, list) and len(ext) == 6:
            grow(ext)
    h = dh_holes.c
    sets = sa.select(objects.c.id).where(objects.c.kind == "drillhole_set", objects.c.state == "ready")
    row = conn.execute(
        sa.select(
            sa.func.min(h.x),
            sa.func.min(h.y),
            sa.func.min(h.z - sa.func.coalesce(h.td, 0.0)),
            sa.func.max(h.x),
            sa.func.max(h.y),
            sa.func.max(h.z),
        ).where(h.set_id.in_(sets), h.has_collar.is_(True))  # fmt: skip
    ).first()
    if row is not None:
        grow(list(row))
    return None if lo[0] is None or lo[1] is None else [*lo, *hi]


def section_line(conn: sa.Connection) -> dict[str, Any] | None:
    """The section line of the view settings ({x0, y0, az, length, …}); None — no line is drawn."""
    from bf_core.viewstate.keys import SCOPE, normalize

    value = conn.execute(
        sa.select(settings.c.value).where(settings.c.scope == SCOPE, settings.c.key == "section")
    ).scalar()
    if value is None:
        return None
    try:
        line = normalize("section", value, strict=False)["line"]
    except ValueError:
        return None
    return dict(line) if line else None


def section_edges(conn: sa.Connection) -> dict[str, float | None] | None:
    """Where a raster stands on the current section (ТЗ 7.19): the left edge at the start of the line, the right
    one at its end — a line without a length runs within the data; top and bottom — the heights of the data
    (None when the data has no heights). None: no line, or a line without a length in a project without data."""
    line = section_line(conn)
    if line is None:
        return None
    x0, y0 = float(line["x0"]), float(line["y0"])
    az = math.radians(float(line["az"]))
    dx, dy = math.sin(az), math.cos(az)
    ext = data_extent(conn)
    if line.get("length"):
        t0, t1 = 0.0, float(line["length"])
    elif ext is None:
        return None
    else:
        box = [(x, y) for x in (ext[0], ext[3]) for y in (ext[1], ext[4])]
        along = [(x - x0) * dx + (y - y0) * dy for x, y in box]  # type: ignore[operator]
        t0, t1 = min(along), max(along)
        if t1 - t0 < 1e-6:
            return None
    return {
        "x1": x0 + dx * t0, "y1": y0 + dy * t0, "x2": x0 + dx * t1, "y2": y0 + dy * t1,
        "z_top": ext[5] if ext else None, "z_bottom": ext[2] if ext else None,
    }  # fmt: skip


def section_georef(edges: Mapping[str, Any], width: int, height: int, z_top: float | None = None) -> gr.Georef:
    """The georeference of an image on the section. With `z_top` the image keeps its proportions: the bottom is
    `z_top` minus the length of the line × height / width. Without it the image spans the heights of the data."""
    x1, y1, x2, y2 = (float(edges[k]) for k in ("x1", "y1", "x2", "y2"))
    if z_top is not None:
        top = float(z_top)
        bottom = top - math.hypot(x2 - x1, y2 - y1) * height / width
    elif edges.get("z_top") is None or edges.get("z_bottom") is None:
        raise FormatError(
            "no_heights",
            "В проекте нет данных с высотами: верх и низ растра на разрезе определить не по чему.",
            "Укажите высоту верхнего края растра.",
        )
    else:
        top, bottom = float(edges["z_top"]), float(edges["z_bottom"])
    return gr.vertical_georef(width, height, x1, y1, x2, y2, top, bottom)


def place_on_section(conn: sa.Connection, raster_id: int, z_top: float | None, expected_version: int) -> dict[str, Any]:
    """`raster.place_on_section` (ТЗ 7.19): the raster — a plan one too — into the plane of the section line.
    A raster draped on a DTM stays in plan: 422 `drape_vertical_raster`."""
    row = _current(conn, raster_id, expected_version)
    _stays_in_plan(conn, raster_id)
    edges = section_edges(conn)
    if edges is None:
        raise ApiError(
            422,
            "no_section",
            "Линия разреза не проведена (или у неё нет длины, а данных в проекте ещё нет).",
            "Проведите линию разреза в 3D-окне и повторите.",
        )
    try:
        g = section_georef(edges, int(row["width"]), int(row["height"]), z_top)
    except FormatError as exc:
        raise _format_error(exc) from exc
    return _save(conn, row, g, checks=[])
