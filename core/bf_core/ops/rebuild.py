"""«Перестроить» (`model.rebuild`; ТЗ 7.23; ADR 0015 §6): the operation and the parameters of `meta.built` on the
current sources — an ordinary job with `rebuild_of`. The same lines are read by their stable numbers (`lid`), the
tie lines too (by their numbers and the mark `ROLE = TIE`); the result is a new object, the old one is not
changed. A source deleted since, or a line that is gone, is a 422 that names it. A DTM (`built.kind = "dtm"`)
is rebuilt by the job `model.dtm` from the sources of its record: layers, collars of sets, clouds and the
boundary (its `line` — the number kept in the record).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import objects, wireframes
from bf_core.storage.blobs import BlobStore

from . import registry
from .registry import Checked
from .sources import LAYER, WIREFRAME

#: Operations whose result is one wireframe: a rebuild keeps its name («имя (2)» — the name is taken).
SINGLE = frozenset({"stitch", "extrude", "edit_triangles", "repair", "simplify", "smooth", "merge", "boolean"})


def _not_rebuildable(name: str) -> ApiError:
    return ApiError(
        422, "not_rebuildable", f"«{name}» не построен операцией: перестраивать нечего.",
        "«Перестроить» работает для результатов операций и для ЦТМ, построенных в программе.",
    )  # fmt: skip


def _gone(source: dict[str, Any]) -> ApiError:
    return ApiError(
        422, "source_missing", f"Исходный объект «{source.get('name') or '?'}» удалён из проекта.",
        "Перестроить нельзя: выберите исходные заново и запустите операцию.", {"role": source.get("role")},
    )  # fmt: skip


def _dtm_params(conn: sa.Connection, built: dict[str, Any], wf_id: int, meta: dict[str, Any]) -> dict[str, Any]:
    layers: list[int] = []
    sets: list[int] = []
    clouds: list[dict[str, Any]] = []
    boundary = None
    kept = (built.get("params") or {}).get("boundary") or {}
    for s in built.get("sources") or []:
        if not isinstance(s, dict):
            continue
        raw = s.get("id")
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise _gone(s)
        sid = int(raw)
        if conn.execute(sa.select(objects.c.id).where(objects.c.id == sid)).first() is None:
            raise _gone(s)
        if s.get("kind") == "drillhole_set":
            sets.append(sid)
        elif "boundary" in s:
            boundary = {"layer_id": sid, "line": kept.get("line"), "use_z": bool(kept.get("use_z"))}
        elif "classes" in s:
            clouds.append({"layer_id": sid, "classes": s.get("classes"), "cell": s.get("cell")})
        else:
            layers.append(sid)
    dtm = conn.execute(sa.select(wireframes.c.dtm).where(wireframes.c.object_id == wf_id)).scalar()
    params = built.get("params") or {}
    return {
        "layers": layers, "set_ids": sets, "clouds": clouds or None, "boundary": boundary,
        "breaklines": params.get("breaklines", True), "max_edge": params.get("max_edge"),
        "to_dtm": dtm is not None, "color": meta.get("color"),
    }  # fmt: skip


def plan(conn: sa.Connection, store: BlobStore, object_id: int, kind: str) -> tuple[Checked, dict[str, Any]]:
    """The checked operation of a rebuild of `object_id` (a wireframe or a line layer) and the arguments of
    `job.submit` (name, folder, rebuild_of, known)."""
    row = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.meta, objects.c.folder, objects.c.parent_id).where(
            objects.c.id == object_id, objects.c.kind == kind, objects.c.state == "ready"
        )
    ).first()
    if row is None:
        if kind == WIREFRAME:
            raise ApiError(404, "wireframe_not_found", "Каркас не найден.", "Обновите дерево проекта.")
        raise ApiError(404, "line_layer_not_found", "Слой линий не найден.", "Обновите дерево проекта.")
    meta = dict(row.meta or {})
    built = meta.get("built")
    if not isinstance(built, dict):
        raise _not_rebuildable(str(row.name))
    if built.get("kind") == "dtm" and kind == WIREFRAME:
        params = _dtm_params(conn, built, int(row.id), meta)
        return registry.precheck(conn, store, "dtm", params, name=str(row.name)), {}
    if built.get("kind") != "operation" or not isinstance(built.get("params"), dict):
        raise _not_rebuildable(str(row.name))
    sources = [s for s in built.get("sources") or [] if isinstance(s, dict)]
    known = {str(s["role"]): str(s.get("name") or "") for s in sources if s.get("role")}
    op = built.get("op")
    checked = registry.precheck(conn, store, op, built["params"], known=known)
    name: str | None = None
    folder = row.folder
    if kind == WIREFRAME and (op in SINGLE or (str(op).startswith("clip_") and built["params"].get("keep") != "both")):
        name = str(row.name)
    if kind == LAYER:  # the name and the folder of the line file of the result
        parent = conn.execute(sa.select(objects.c.name, objects.c.folder).where(objects.c.id == row.parent_id)).first()
        if parent is not None:
            name, folder = str(parent.name), parent.folder
    return checked, {"name": name, "folder": folder, "rebuild_of": int(row.id), "known": known}
