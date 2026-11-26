"""/api/v1/implicit — the stack of relations, the faults and the systems of veins (ТЗ Д-24, Д-31, Д-39; подшаг 19.36;
e19-api.md §10). The paths are relative to `/implicit` (`router.py`):

  * `PUT /models/{id}/relations` {expected_version, order: [gid …], relations: {"gid": relation}} → {id, version} —
    the groups of the stack (every group but the faults) in the order of their age (the first is the youngest) with the
    relation each of them has to the older ones (`conformable`, `erode`, `onlap`, `intrusion`: `spec.parse_relations`);
    a cycle of the order of the solution — 422 `bad_relations`.
  * `POST /models/{id}/faults` {expected_version, group: {name, type: "fault", codes?, params: {radius?}}, manual?:
    [{layer_id, role}]} → {id, version, group} — a fault, added last; the hand data of the fault are layers of strings
    of the model with this group (`manual` of the body: they are given the number of the new group, a fault with no
    codes needs them); a `type` other than `fault` — 422 `bad_param {param: "group.type"}`.
  * `PUT /models/{id}/groups/{gid}/faults` {expected_version, fault_ids: [id …]} → {id, version} — the faults that cut
    a domain (the wings of its field); only a domain is cut, only a group of type `fault` cuts: 422 `bad_param
    {param: "fault_ids.N"}`.
  * `POST /models/{id}/vein_system` {expected_version, group: {name, type: "vein_system", params: {veins}}} → {id,
    version, group} — a system of 2–20 veins, the first is the youngest; its codes are those of the veins.

Each is an edit of the model like those of `routes_groups.py`: the whole model is checked again, the version moves, a
built model is «устарела» until it is built again; 404 `model_not_found`, `group_not_found`; 409 `model_building`,
`version_conflict {version}`; 422 `bad_param {param}`; 423 `project_read_only`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

from .routes_base import Payload, Svc, body_of
from .routes_groups import _edit

if TYPE_CHECKING:
    from .spec import Group, ModelSpec

router = APIRouter()


def _group_of(raw: dict[str, Any], gtype: str) -> Group:
    """The group of the body with the type the route makes (a given other type is a mistake of the caller)."""
    from . import store_base as sb
    from .spec import parse_group

    given = raw.get("group")
    if isinstance(given, dict) and given.get("type") not in (None, gtype):
        word = "разлом" if gtype == "fault" else "система жил"
        raise sb.bad("group.type", f"Здесь создаётся {word}: вид группы должен быть «{gtype}».",
                     "Уберите вид группы или выберите другой маршрут.")  # fmt: skip
    return parse_group({**given, "type": gtype} if isinstance(given, dict) else given, "group")


@router.put("/models/{model_id}/relations")
def put_relations(svc: Svc, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    from . import job_stack
    from .spec import parse_relations

    raw = body_of(body)

    def change(spec: ModelSpec) -> tuple[ModelSpec, None]:
        new = parse_relations(raw, spec)
        job_stack.check_stack(new)  # a cycle of the order is refused here, not by the build
        return new, None

    return _edit(svc, request, model_id, raw.get("expected_version"), change)


@router.post("/models/{model_id}/faults")
def add_fault(svc: Svc, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    from . import store_base as sb
    from .spec import parse_manual

    raw = body_of(body)

    def change(spec: ModelSpec) -> tuple[ModelSpec, Group]:
        g = _group_of(raw, "fault")
        if g.id is not None and any(x.id == g.id for x in spec.groups):
            raise sb.bad("group.id", "Группа с таким номером уже есть.", "Обновите вкладку модели.")
        nid = g.id if g.id is not None else max((x.id for x in spec.groups if x.id is not None), default=0) + 1
        given = raw.get("manual")
        if given is not None and not isinstance(given, list):
            raise sb.bad("manual", "Ручные данные разлома: нужен список слоёв.", "Передайте список {layer_id, role}.")
        layers = parse_manual([{**x, "group": nid} if isinstance(x, dict) else x for x in given or []])
        base = replace(spec, manual=(*spec.manual, *layers))
        new = base.with_group(replace(g, id=nid))
        return new, new.group(nid)

    return _edit(svc, request, model_id, raw.get("expected_version"), change)


@router.put("/models/{model_id}/groups/{group_id}/faults")
def put_group_faults(svc: Svc, request: Request, model_id: int, group_id: int, body: Payload) -> dict[str, Any]:
    from . import store_base as sb
    from .spec_parts import _ids

    raw = body_of(body)

    def change(spec: ModelSpec) -> tuple[ModelSpec, None]:
        g = spec.group(group_id)  # 404 `group_not_found`
        if g.type != "domain":
            raise sb.bad("fault_ids", "Разломы рассекают только группы-домены.",
                         "Выберите домен: оболочки, жилы и расширения разломами не рассекаются.")  # fmt: skip
        ids = _ids(raw.get("fault_ids"), "Разломы группы", "fault_ids", 64)
        kinds = {x.id: x.type for x in spec.groups}
        for k, i in enumerate(ids):
            if kinds.get(i) != "fault":
                raise sb.bad(f"fault_ids.{k}", "Эта группа не разлом.", "Выберите группу вида «Разлом».")
        return spec.with_group(replace(g, faults=tuple(ids))), None

    return _edit(svc, request, model_id, raw.get("expected_version"), change)


@router.post("/models/{model_id}/vein_system")
def add_vein_system(svc: Svc, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    from . import store_base as sb

    raw = body_of(body)

    def change(spec: ModelSpec) -> tuple[ModelSpec, Group]:
        g = _group_of(raw, "vein_system")
        if g.id is not None and any(x.id == g.id for x in spec.groups):
            raise sb.bad("group.id", "Группа с таким номером уже есть.", "Обновите вкладку модели.")
        new = spec.with_group(g)
        return new, new.groups[-1]

    return _edit(svc, request, model_id, raw.get("expected_version"), change)
