"""The request of «Модель на скважины» (ТЗ 7.35 Д-34 ч. 1; ADR 0026 решения 25, 28; e19-api.md §8.4) — checked
against the project before a job is made. No numpy here: the API process parses the request (BF-READY).

    spec = parse(conn, store, body)
    body = {set_id, name?, model_id | mesh_ids | block_model_id + field + table, codes?}

Two kinds of request:
  * bodies (`model_id` — the bodies of an implicit model, `mesh_ids` — closed wireframes): the whole of every hole
    is cut by the bodies into an interval table `FROM, TO, CODE` (a gap between the bodies has an empty CODE).
    The priority of the bodies is the order of the groups of the model (the first group is the youngest), within a
    group — the inner shell (the higher threshold) first; for `mesh_ids` — the order of the list.
    The CODE of a body: the name of its group (a shell: «Группа ≥ порог»), of a wireframe — its name; `codes`
    renames: `{group id: "код"}` for a model, `{wireframe id: "код"}` for `mesh_ids`. The «Прочее» body of a model
    is not coded (it is what is left of the boundary).
  * field (`block_model_id`, `field`, `table`): the number field of a block model at the middle of every interval
    of the table `table` — a new table `FROM, TO, <field>`; outside the model — empty, never 0.

Refusals ({code, message, hint[, param]}): 404 `set_not_found`, `model_not_found`, `block_model_not_found`,
`table_not_found`, `wireframe_not_found`; 409 `model_building`, `table_exists` {name, suggested}; 422 `bad_param`,
`no_bodies` (a model without bodies), `wireframe_open` (an output that is not a closed solid).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects

NAME_MAX = 200
CODE_MAX = 200
MAX_IDS = 2000
SET_KIND = "drillhole_set"


def bad(code: str, message: str, hint: str, param: str | None = None) -> ApiError:
    return ApiError(422, code, message, hint, {"param": param} if param else None)


@dataclass
class Spec:
    set_id: int
    name: str
    mode: str  # "bodies" | "field"
    body: dict[str, Any]  # the request as given (the params of the job)
    # bodies
    wireframe_ids: list[int] = field(default_factory=list)  # by priority
    codes: list[str] = field(default_factory=list)  # the CODE of every body, parallel to `wireframe_ids`
    model: dict[str, Any] | None = None  # {id, name, stale_reason} of an implicit model
    # field
    block: dict[str, Any] | None = None  # {id, name, field, unit}
    table: dict[str, Any] | None = None  # {id, name}
    journal: list[str] = field(default_factory=list)
    triangles: int = 0  # of the largest wireframe: the memory of the job


def _int(raw: Any, what: str, param: str) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().isascii() and raw.strip().isdigit() and len(raw.strip()) <= 18:
        return int(raw.strip())
    raise bad("bad_param", f"{what}: нужен номер.", "Выберите объект из списка.", param)


def _name(raw: Any, default: str) -> str:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if not isinstance(raw, str):
        raise bad("bad_param", "Имя таблицы: нужен текст.", "Введите имя новой таблицы.", "name")
    name = raw.strip()
    if len(name) > NAME_MAX:
        raise bad("bad_param", "Имя таблицы слишком длинное.", f"Не больше {NAME_MAX} знаков.", "name")
    return name


def _set(conn: sa.Connection, raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    found = conn.execute(sa.select(objects.c.id).where(
        objects.c.id == raw, objects.c.kind == SET_KIND, objects.c.state == "ready")).scalar()  # fmt: skip
    if found is None:
        raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
    return int(raw)


def check_free(conn: sa.Connection, set_id: int, name: str) -> None:
    """409 `table_exists` {name, suggested}: the table is new, it replaces nothing."""
    from bf_core.composites import sink

    if sink.taken(conn, set_id, name):
        raise ApiError(409, "table_exists", f"В наборе уже есть таблица «{name}».", "Задайте другое название.",
                       {"name": name, "suggested": sink.free_name(conn, set_id, name)})  # fmt: skip


def _codes_map(raw: Any, keys: set[int], what: str) -> dict[int, str]:
    """`codes` of the request: {number: code} (JSON keys are text), every key one of `keys`."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise bad("bad_param", "Коды тел: нужен список «объект — код».", "Задайте код для каждого тела.", "codes")
    out: dict[int, str] = {}
    for k, v in raw.items():
        key = _int(k, "Коды тел", "codes")
        if key not in keys:
            raise bad("bad_param", f"Коды тел: {what} № {key} нет в запросе.", "Уберите лишнюю строку.", "codes")
        if not isinstance(v, str) or not v.strip() or len(v.strip()) > CODE_MAX:
            raise bad("bad_param", "Код тела — непустой текст.", f"Не больше {CODE_MAX} знаков.", "codes")
        out[key] = v.strip()
    return out


def _threshold(t: Any) -> str:
    return f"{float(t):g}"


def _model_bodies(conn: sa.Connection, spec: Spec, model_id: int, codes_raw: Any) -> None:
    """The bodies of the outputs of a model, by the priority of its groups (module docstring)."""
    from bf_core.model import store as ms
    from bf_core.model import store_base as sb

    got = ms.get(conn, model_id)  # 404 `model_not_found`
    if got["state"] == "building":
        raise sb.building()
    meta = got["meta"]
    groups = [g for g in meta.get("groups") or [] if isinstance(g, Mapping)]
    order = {g.get("id"): i for i, g in enumerate(groups)}
    names = {g.get("id"): str(g.get("name") or "") for g in groups}
    # the bodies of a fault are the space of its wings and the common body of a vein system is the union of the
    # veins: neither is rock of its own, the coding takes the veins and the wings of the domains (19.36)
    bodies = [
        o for o in got["outputs"]
        if o.get("kind") == "body" and o.get("group") in order and not o.get("region") and not o.get("common")
    ]  # fmt: skip
    if not bodies:
        raise bad("no_bodies", f"В модели «{got['name']}» нет тел для кодирования.",
                  "Постройте модель и задайте группы с телами (кроме «Прочее»).", "model_id")  # fmt: skip
    # inner shells first (a higher threshold lies inside a lower one), then the wings, then the older output
    bodies.sort(
        key=lambda o: (
            order[o["group"]],
            -float(o["threshold"]) if o.get("threshold") is not None else 0.0,
            o.get("wing") if o.get("wing") is not None else -1,
            int(o["wireframe_id"]),
        )
    )
    renamed = _codes_map(codes_raw, {int(k) for k in order if isinstance(k, int)}, "группы")
    spec.wireframe_ids = [int(o["wireframe_id"]) for o in bodies]
    for o in bodies:
        gid = int(o["group"])
        if gid in renamed:
            code = renamed[gid]
        else:
            code = names[gid]
            if o.get("threshold") is not None:
                code = f"{code} ≥ {_threshold(o['threshold'])}"
        spec.codes.append(code)
    spec.model = {"id": model_id, "name": str(got["name"]), "stale_reason": got.get("stale_reason") if got.get("stale")
                  else None}  # fmt: skip
    if spec.model["stale_reason"]:
        spec.journal.append(f"Модель «{got['name']}» устарела ({spec.model['stale_reason']}): тела взяты как они есть.")
    spec.triangles = max((int(o.get("triangles") or 0) for o in bodies), default=0)


def _mesh_bodies(conn: sa.Connection, spec: Spec, raw_ids: Any, codes_raw: Any) -> None:
    from bf_core.composites import jobs_coding as jc

    wf = jc.parse_wireframes(conn, raw_ids)  # 422 `wireframe_not_found`, `wireframe_open`
    spec.wireframe_ids = wf.ids
    renamed = _codes_map(codes_raw, set(wf.ids), "каркаса")
    spec.codes = [renamed.get(i, n) for i, n in zip(wf.ids, wf.names(), strict=True)]
    spec.triangles = wf.triangles()


def _field_of(conn: sa.Connection, store: Any, spec: Spec, block_id: Any, name: Any, table: Any) -> None:
    from bf_core.blocks.model import Model
    from bf_core.blocks.schema import CATEGORY
    from bf_core.composites import jobs as cj

    bid = _int(block_id, "Блочная модель", "block_model_id")
    model = Model.open(conn, store, bid)  # 404 `block_model_not_found`
    if not isinstance(name, str) or not name.strip():
        raise bad("bad_param", "Не выбрано поле блочной модели.", "Выберите числовое поле.", "field")
    spec_field = model.field(name.strip())  # 404 `block_field_not_found`
    if spec_field.get("kind") == CATEGORY:
        raise bad("bad_param", f"Поле «{spec_field['name']}» не числовое.", "Выберите числовое поле модели.", "field")
    t = cj.resolve_table(conn, spec.set_id, table)  # 404 `table_not_found`
    spec.block = {"id": bid, "name": model.name, "field": str(spec_field["name"]), "unit": spec_field.get("unit")}
    spec.table = {"id": int(t.id), "name": str(t.name)}


def parse(conn: sa.Connection, store: Any, body: Any) -> Spec:
    """The request checked against the project (module docstring); `store` — the `BlobStore` of the project (a block
    model is read through it)."""
    if not isinstance(body, Mapping):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие в окне.")
    given = [k for k in ("model_id", "mesh_ids", "block_model_id") if body.get(k) is not None]
    if len(given) != 1:
        raise bad(
            "bad_param",
            "Выберите одно: модель, каркасы или поле блочной модели.",
            "Задайте ровно одно из model_id, mesh_ids, block_model_id.",
            "model_id",
        )
    set_id = _set(conn, body.get("set_id"))
    spec = Spec(set_id, "", "field" if given[0] == "block_model_id" else "bodies", dict(body))
    if given[0] == "model_id":
        _model_bodies(conn, spec, _int(body["model_id"], "Модель", "model_id"), body.get("codes"))
        default = f"{spec.model['name']} — на скважинах" if spec.model else "Модель на скважинах"
    elif given[0] == "mesh_ids":
        _mesh_bodies(conn, spec, body["mesh_ids"], body.get("codes"))
        default = "Каркасы на скважинах"
    else:
        _field_of(conn, store, spec, body["block_model_id"], body.get("field"), body.get("table"))
        assert spec.block is not None and spec.table is not None
        default = f"{spec.table['name']} — {spec.block['field']}"
    spec.name = _name(body.get("name"), default)
    check_free(conn, set_id, spec.name)
    return spec


def lookup(engine: sa.Engine, store: Any, body: Any) -> Spec:
    with dialect.read_tx(engine) as conn:
        return parse(conn, store, body)
