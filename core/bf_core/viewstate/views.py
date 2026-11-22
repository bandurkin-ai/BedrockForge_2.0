"""Saved views (ТЗ 7.35 Д-09, ADR 0008 §2): objects of kind «view», meta = {v, camera, keys}.

A view keeps the camera (float64, applied by the interface) and the view keys of the project at the moment of
saving. Applying writes every key at once, in one transaction; ids of objects deleted since are dropped on read
(as for the keys themselves). Views always go into `.bfggis`: `remap_meta` renumbers the ids inside.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects, settings

from .keys import MAX_BYTES, SCOPE, map_ids, normalize
from .service import read_all

KIND = "view"
MAX_VIEWS = 200
#: the view keys a saved view keeps (camera aside); the active set, styles and columns are not part of a view
VIEW_KEYS = ("scene", "section", "hidden", "hide_all", "displays", "swaths", "ellipsoids")
DEFAULTS: dict[str, Any] = {
    "scene": {},
    "section": {"line": None, "clip": False},
    "hidden": [],
    "hide_all": {"on": False, "shown": []},
    "displays": [],
    "swaths": [],
    "ellipsoids": [],
}
_AUTO_NAME = re.compile(r"^Вид (\d{1,6})$")


class ViewError(ValueError):
    """422: the text is in Russian."""


class NotFound(LookupError):
    pass


class Conflict(Exception):
    def __init__(self, version: int) -> None:
        super().__init__("version_conflict")
        self.version = version


def _vec(v: Any, what: str) -> list[float]:
    if not isinstance(v, list) or len(v) != 3:
        raise ViewError(f"Камера: «{what}» — три числа.")
    out: list[float] = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(float(x)):
            raise ViewError(f"Камера: «{what}» — три конечных числа.")
        out.append(float(x))
    return out


def check_camera(v: Any) -> dict[str, Any]:
    """Position, target and up (float64, as the scene keeps them), zoom > 0, projection, field of view."""
    if not isinstance(v, dict):
        raise ViewError("Камера: ожидаются position, target, up, zoom, ortho, fov.")
    zoom, fov = v.get("zoom", 1.0), v.get("fov", 45.0)
    for name, x in (("zoom", zoom), ("fov", fov)):
        if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(float(x)):
            raise ViewError(f"Камера: «{name}» — число.")
    if float(zoom) <= 0:
        raise ViewError("Камера: масштаб больше нуля.")
    if not 1.0 <= float(fov) <= 179.0:
        raise ViewError("Камера: угол обзора от 1 до 179°.")
    ortho = v.get("ortho", True)
    if not isinstance(ortho, bool):
        raise ViewError("Камера: «ortho» — да или нет.")
    return {
        "position": _vec(v.get("position"), "position"),
        "target": _vec(v.get("target"), "target"),
        "up": _vec(v.get("up", [0.0, 0.0, 1.0]), "up"),
        "zoom": float(zoom),
        "ortho": ortho,
        "fov": float(fov),
    }


def _name(v: Any) -> str:
    name = v.strip() if isinstance(v, str) else ""
    if not name or len(name) > 200:
        raise ViewError("Название вида — от 1 до 200 символов.")
    return name


def _folder(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > 200:
        raise ViewError("Папка вида — до 200 символов.")
    parts = [p.strip() for p in v.split("/") if p.strip()]
    return "/".join(parts) or None


def snapshot(conn: sa.Connection) -> dict[str, Any]:
    """The view keys of the project now (normalized, deleted objects dropped), defaults for the missing."""
    values = read_all(conn)
    return {k: values[k]["value"] if k in values else normalize(k, DEFAULTS[k], strict=False) for k in VIEW_KEYS}


def _meta(camera: Any, keys: dict[str, Any]) -> dict[str, Any]:
    meta = {"v": 1, "camera": check_camera(camera), "keys": keys}
    if len(json.dumps(meta, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ViewError("Вид больше 512 КБ: скройте часть объектов списком или очистите окно и сохраните заново.")
    return meta


def _out(r: Any, full: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": int(r.id),
        "name": r.name,
        "folder": r.folder,
        "version": int(r.version),
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }
    if full:
        out["camera"] = r.meta.get("camera")
        out["keys"] = r.meta.get("keys", {})
    return out


def _row(conn: sa.Connection, view_id: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == view_id, objects.c.kind == KIND)).first()
    if r is None:
        raise NotFound(view_id)
    return r


def list_views(conn: sa.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.select(objects)
        .where(objects.c.kind == KIND, objects.c.state == "ready")
        .order_by(objects.c.sort_key, objects.c.id)
    ).all()
    return [_out(r, full=False) for r in rows]


def get(conn: sa.Connection, view_id: int) -> dict[str, Any]:
    return _out(_row(conn, view_id), full=True)


def _next_name(conn: sa.Connection) -> str:
    taken = [
        int(m.group(1))
        for (n,) in conn.execute(sa.select(objects.c.name).where(objects.c.kind == KIND))
        if (m := _AUTO_NAME.match(n))
    ]
    return f"Вид {max(taken, default=0) + 1}"


def create(
    conn: sa.Connection, camera: Any, name: Any = None, folder: Any = None, author: str | None = None
) -> dict[str, Any]:
    count = conn.execute(sa.select(sa.func.count()).select_from(objects).where(objects.c.kind == KIND)).scalar_one()
    if count >= MAX_VIEWS:
        raise ViewError(f"В проекте уже {MAX_VIEWS} видов: удалите ненужные или перезапишите один из них.")
    meta = _meta(camera, snapshot(conn))
    name = _next_name(conn) if name is None else _name(name)
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind=KIND,
            name=name,
            name_key=dialect.name_key(name),
            folder=_folder(folder),
            sort_key=float(count),
            state="ready",
            version=1,
            meta=meta,
            author=author,
            created_at=now,
            updated_at=now,
        )
    )
    key = res.inserted_primary_key
    assert key is not None
    return get(conn, int(key[0]))


def update(
    conn: sa.Connection,
    view_id: int,
    expected_version: int,
    *,
    name: Any = None,
    folder: Any = ...,
    camera: Any = None,
) -> dict[str, Any]:
    """Rename, move to a folder, or (with a camera) overwrite with the current view."""
    r = _row(conn, view_id)
    if int(r.version) != expected_version:
        raise Conflict(int(r.version))
    values: dict[str, Any] = {"version": int(r.version) + 1, "updated_at": dialect.utcnow()}
    if name is not None:
        values["name"] = _name(name)
        values["name_key"] = dialect.name_key(values["name"])
    if folder is not ...:
        values["folder"] = _folder(folder)
    if camera is not None:
        values["meta"] = _meta(camera, snapshot(conn))
    n = conn.execute(
        sa.update(objects).where(objects.c.id == view_id, objects.c.version == expected_version).values(**values)
    ).rowcount
    if n != 1:
        raise Conflict(int(r.version))
    return get(conn, view_id)


def delete(conn: sa.Connection, view_id: int) -> None:
    _row(conn, view_id)
    conn.execute(sa.delete(objects).where(objects.c.id == view_id))


def apply(conn: sa.Connection, view_id: int) -> dict[str, Any]:
    """Every key of the view written at once (no partial state); the answer is what the interface shows."""
    meta = _row(conn, view_id).meta or {}
    keys = meta.get("keys") or {}
    for key in VIEW_KEYS:
        try:
            value = normalize(key, keys.get(key, DEFAULTS[key]), strict=False)
        except ValueError:
            value = normalize(key, DEFAULTS[key], strict=False)
        row = conn.execute(
            sa.select(settings.c.version).where(settings.c.scope == SCOPE, settings.c.key == key)
        ).first()
        if row is None:
            conn.execute(sa.insert(settings).values(scope=SCOPE, key=key, value=value, version=1))
        else:
            conn.execute(
                sa.update(settings)
                .where(settings.c.scope == SCOPE, settings.c.key == key)
                .values(value=value, version=int(row.version) + 1)
            )
    values = read_all(conn)
    return {"camera": meta.get("camera"), "values": {k: values[k] for k in VIEW_KEYS if k in values}}


def remap_meta(meta: Any, new_id: Callable[[int], int | None]) -> Any:
    """Ids inside a saved view on a `.bfggis` import; keys this version does not know are dropped."""
    if not isinstance(meta, dict) or not isinstance(meta.get("keys"), dict):
        return meta
    keys: dict[str, Any] = {}
    for key, value in meta["keys"].items():
        try:
            keys[key] = map_ids(key, normalize(key, value, strict=False), new_id)
        except ValueError:
            continue
    return {**meta, "keys": keys}
