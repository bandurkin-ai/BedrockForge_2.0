"""/api/v1/implicit — the groups of a model (ТЗ 8.9.2; e19-api.md §5; подшаг 19.16):

  * `POST /models/{id}/groups` {expected_version, group} → {id, version, group} — the group of the form of §4 is added
    last (the oldest); a given `id` that the model has already — 422 `bad_param {param: "group.id"}`.
  * `PUT /models/{id}/groups/{gid}` {expected_version, group} → {id, version, group}; 404 `group_not_found`; the group
    keeps its place and its number.
  * `DELETE /models/{id}/groups/{gid}[?expected_version]` (or the version in the body) → {id, version, deleted: gid};
    the manual layers of the group and the references to it from the faults of the other groups go with it.

Each is an edit of the model: the whole model is checked again (`store.update`), the version moves, a built model is
«устарела» until it is built again; 404 `model_not_found`, 409 `model_building`, `version_conflict {version}`, 422
`bad_param {param}` (`groups.N.…` of the model, `group.…` of the body), 423 `project_read_only`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Body, Query, Request

from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore

from .routes_base import Payload, Svc, body_of, publish

if TYPE_CHECKING:
    from .spec import Group, ModelSpec

router = APIRouter()


def _edit(svc: Svc, request: Request, model_id: int, version: Any,
          change: Callable[[ModelSpec], tuple[ModelSpec, Group | None]]) -> dict[str, Any]:  # fmt: skip
    """`change(spec)` → (the model with the groups changed, the group to answer with); written as one edit."""
    from bf_core.geostat.variogram import store as ms

    from . import store as st
    from . import store_base as sb

    project = svc.require_current(writable=True)
    expected = ms.clean_version(version)
    with dialect.write_tx(project.engine) as conn:
        row = sb.stored(conn, model_id)
        if row.state == "building":
            raise sb.building()
        if int(row.version) != expected:
            raise sb.conflict(int(row.version))
        new, group = change(sb.load_spec(dict(row.meta or {})))
        keys = {"groups": [g.canonical() for g in new.groups], "manual": [m.canonical() for m in new.manual]}
        out = st.update(conn, BlobStore(project.folder), project, model_id, keys, expected)
    publish(request, model_id)
    return {"id": out["id"], "version": out["version"], **({} if group is None else {"group": group.canonical()})}


@router.post("/models/{model_id}/groups")
def add_group(svc: Svc, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    from . import store_base as sb
    from .spec import parse_group

    raw = body_of(body)

    def change(spec: ModelSpec) -> tuple[ModelSpec, Group]:
        g = parse_group(raw.get("group"), "group")
        if g.id is not None and any(x.id == g.id for x in spec.groups):
            raise sb.bad("group.id", "Группа с таким номером уже есть.", "Обновите вкладку модели.")
        new = spec.with_group(g)
        return new, new.groups[-1]

    return _edit(svc, request, model_id, raw.get("expected_version"), change)


@router.put("/models/{model_id}/groups/{group_id}")
def put_group(svc: Svc, request: Request, model_id: int, group_id: int, body: Payload) -> dict[str, Any]:
    from .spec import parse_group

    raw = body_of(body)

    def change(spec: ModelSpec) -> tuple[ModelSpec, Group]:
        spec.group(group_id)  # 404 `group_not_found`
        g = replace(parse_group(raw.get("group"), "group"), id=group_id)
        return spec.with_group(g), g

    return _edit(svc, request, model_id, raw.get("expected_version"), change)


@router.delete("/models/{model_id}/groups/{group_id}")
def delete_group(svc: Svc, request: Request, model_id: int, group_id: int,
                 expected_version: Annotated[int | None, Query()] = None,
                 body: Annotated[Any, Body()] = None) -> dict[str, Any]:  # fmt: skip
    version = expected_version if expected_version is not None else (
        body.get("expected_version") if isinstance(body, dict) else None)  # fmt: skip

    def change(spec: ModelSpec) -> tuple[ModelSpec, None]:
        spec.group(group_id)
        groups = tuple(replace(g, faults=tuple(f for f in g.faults if f != group_id))
                       for g in spec.groups if g.id != group_id)  # fmt: skip
        manual = tuple(m for m in spec.manual if m.group != group_id)
        return replace(spec, groups=groups, manual=manual), None

    out = _edit(svc, request, model_id, version, change)
    return {**out, "deleted": group_id}
