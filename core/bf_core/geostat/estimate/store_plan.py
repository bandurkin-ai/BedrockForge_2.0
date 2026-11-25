"""The plans of the estimation (подшаг 18.11; ADR 0024 §2, решение 10; docs/progress/e18-api.md §2.2, §4):
`objects(kind="estimation_plan", parent_id=NULL)`, `meta` {v: 1, name, model_id, items: [estimation id]} — the order
of the items is the order of the rows; names unique among the plans. A plan holds the rows, not the other way: a
row deleted leaves its plans (`remove_item`), a plan deleted leaves its rows.

    check_plan(conn, items, model_id=None, param="items") -> [EstimationSpec]    # 404 / 422 `items.N`
    plan_create(conn, spec, author=None) -> {id, name, version}
    plan_update(conn, plan_id, raw) -> {id, name, version}      # raw {expected_version, name?, items?}
    plan_get(conn, plan_id) -> {id, name, model_id, version, items: [{id, name, method, prefix, label, stale}], …}

The rows that write one prefix (§2.2) must have the same method and fields and different domains of one field: they
fill one set of fields of the model, each its own blocks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.geostat.variogram import store as ms

from . import store_base as sb

if TYPE_CHECKING:
    from .spec import EstimationSpec, PlanSpec


def _model_name(conn: sa.Connection, model_id: int) -> str:
    name = conn.execute(sa.select(objects.c.name).where(
        objects.c.id == model_id, objects.c.kind == "block_model")).scalar()  # fmt: skip
    if name is None:
        raise sb.bad("model_id", "Блочная модель не найдена.", "Выберите модель из списка проекта.",
                     "block_model_not_found", 404)  # fmt: skip
    return str(name)


def _signature(spec: EstimationSpec) -> tuple[Any, ...]:
    return spec.method.kind, tuple((c["name"], c["dtype"], c["role"]) for c in spec.columns())


def _same_blocks(a: EstimationSpec, b: EstimationSpec) -> bool:
    """The blocks of two rows of one prefix overlap: no domain, another field of the domain, or the same label."""
    ta, tb = a.target, b.target
    if ta.domain_field is None or tb.domain_field is None:
        return True
    return ta.domain_field.casefold() != tb.domain_field.casefold() or ta.label == tb.label


def _name_of(conn: sa.Connection, ident: int) -> str:
    return str(conn.execute(sa.select(objects.c.name).where(objects.c.id == ident)).scalar() or "")


def check_plan(conn: sa.Connection, items: Sequence[int], model_id: int | None = None,
               param: str = "items") -> list[EstimationSpec]:  # fmt: skip
    """The rows of a plan (or of a run by `estimation_ids`), in order: every row is there (404
    `estimation_not_found {param: "items.N"}`), of the block model, and the rows of one prefix are compatible
    (422 `bad_param {param: "items.N"}`)."""
    specs: list[EstimationSpec] = []
    for i, ident in enumerate(items):
        row = conn.execute(sa.select(objects.c.meta).where(
            objects.c.id == ident, objects.c.kind == sb.KIND)).first()  # fmt: skip
        if row is None:
            raise sb.not_found(sb.KIND, f"{param}.{i}")
        spec = sb.load_spec(row.meta or {})
        if model_id is None:
            model_id = spec.model_id
        if spec.model_id != model_id:
            raise sb.bad(f"{param}.{i}", f"Строка «{_name_of(conn, ident)}» относится к другой блочной модели.",
                         "В план входят строки одной блочной модели.")  # fmt: skip
        specs.append(spec)
    by_prefix: dict[str, list[tuple[int, EstimationSpec]]] = {}
    for i, spec in enumerate(specs):
        prefix = spec.outputs.prefix
        for _j, other in by_prefix.get(prefix, []):
            if _signature(spec) != _signature(other) or _same_blocks(spec, other):
                raise sb.bad(
                    f"{param}.{i}", f"Строки с одним префиксом «{prefix}» пишут одни поля: у них должны быть "
                    "одинаковые метод и поля и разные домены одного поля модели.",
                    "Задайте строкам разные префиксы или разные домены.")  # fmt: skip
        by_prefix.setdefault(prefix, []).append((i, spec))
    return specs


def _meta(name: str, model_id: int, items: Sequence[int]) -> dict[str, Any]:
    return {"v": 1, "name": name, "model_id": model_id, "items": [int(i) for i in items]}


def plan_create(conn: sa.Connection, spec: PlanSpec, author: str | None = None) -> dict[str, Any]:
    """A plan (in the caller's write transaction); the default name — «План · ‹модель›», made free."""
    model = _model_name(conn, spec.model_id)
    check_plan(conn, spec.items, spec.model_id)
    final = sb.final_name(conn, sb.PLAN, spec.name, f"План · {model}")
    now = dialect.utcnow()
    res = conn.execute(sa.insert(objects).values(
        kind=sb.PLAN, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
        version=1, meta=_meta(final, spec.model_id, spec.items), author=author, created_at=now,
        updated_at=now))  # fmt: skip
    return {"id": int(res.inserted_primary_key[0]), "name": final, "version": 1}  # type: ignore[index]


def plan_update(conn: sa.Connection, plan_id: int, raw: Mapping[str, Any]) -> dict[str, Any]:
    """`name`, `items` given replace the stored; `expected_version` (required) — 409 `version_conflict {version}`."""
    from .spec import parse_plan

    row = sb.stored(conn, sb.PLAN, plan_id)
    version = ms.clean_version(raw.get("expected_version"))
    if int(row.version) != version:
        raise sb.conflict(int(row.version), sb.PLAN)
    meta = dict(row.meta or {})
    if raw.get("model_id") not in (None, meta.get("model_id")):
        raise sb.bad("model_id", "У плана нельзя сменить блочную модель.", "Создайте новый план для другой модели.")
    items = meta.get("items") or [] if raw.get("items") is None else raw["items"]
    body = {"name": meta.get("name") if raw.get("name") is None else raw["name"], "model_id": meta["model_id"],
            "items": items}  # fmt: skip
    spec = parse_plan(body)
    check_plan(conn, spec.items, spec.model_id)
    final = str(row.name) if raw.get("name") is None else sb.final_name(conn, sb.PLAN, raw["name"], "", plan_id)
    n = conn.execute(sa.update(objects).where(objects.c.id == plan_id, objects.c.version == version).values(
        name=final, name_key=dialect.name_key(final), meta=_meta(final, spec.model_id, spec.items),
        version=version + 1, updated_at=dialect.utcnow())).rowcount  # fmt: skip
    if n != 1:
        raise sb.conflict(int(sb.stored(conn, sb.PLAN, plan_id).version), sb.PLAN)
    return {"id": plan_id, "name": final, "version": version + 1}


def plan_delete(conn: sa.Connection, plan_id: int) -> dict[str, Any]:
    """The plan goes, its rows stay."""
    sb.stored(conn, sb.PLAN, plan_id)
    conn.execute(sa.delete(objects).where(objects.c.id == plan_id))
    return {"deleted": plan_id}


def plans_with(conn: sa.Connection) -> dict[int, list[int]]:
    """estimation id → the ids of the plans that hold it (by the name of the plan)."""
    out: dict[int, list[int]] = {}
    rows = conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == sb.PLAN)
                        .order_by(objects.c.name_key, objects.c.id)).all()  # fmt: skip
    for r in rows:
        for item in (r.meta or {}).get("items") or []:
            out.setdefault(int(item), []).append(int(r.id))
    return out


def remove_item(conn: sa.Connection, estimation_id: int) -> list[int]:
    """A row deleted leaves its plans: their `items` lose it, their `version` + 1; the ids of those plans."""
    plans = plans_with(conn).get(estimation_id, [])
    for pid in plans:
        row = sb.stored(conn, sb.PLAN, pid)
        meta = dict(row.meta or {})
        meta["items"] = [i for i in meta.get("items") or [] if int(i) != estimation_id]
        conn.execute(sa.update(objects).where(objects.c.id == pid).values(
            meta=meta, version=int(row.version) + 1, updated_at=dialect.utcnow()))  # fmt: skip
    return plans


def _item(conn: sa.Connection, ident: int) -> dict[str, Any] | None:
    from .store_check import freshness

    row = conn.execute(sa.select(objects.c.id, objects.c.name, objects.c.meta).where(
        objects.c.id == ident, objects.c.kind == sb.KIND)).first()  # fmt: skip
    if row is None:
        return None
    meta = row.meta or {}
    return {"id": int(row.id), "name": row.name, "method": (meta.get("method") or {}).get("kind"),
            "prefix": (meta.get("outputs") or {}).get("prefix"), "label": (meta.get("target") or {}).get("label"),
            "stale": bool(freshness(conn, meta)["stale"])}  # fmt: skip


def plan_get(conn: sa.Connection, plan_id: int) -> dict[str, Any]:
    row = sb.stored(conn, sb.PLAN, plan_id)
    meta = row.meta or {}
    items = [i for i in (_item(conn, int(x)) for x in meta.get("items") or []) if i is not None]
    return {"id": int(row.id), "name": row.name, "model_id": meta.get("model_id"), "version": int(row.version),
            "items": items, "created_at": row.created_at, "updated_at": row.updated_at}  # fmt: skip


def plan_list(conn: sa.Connection, model_id: int | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(sa.select(objects).where(objects.c.kind == sb.PLAN).order_by(objects.c.name_key, objects.c.id))
    out = []
    for r in rows:
        meta = r.meta or {}
        if model_id is not None and meta.get("model_id") != model_id:
            continue
        out.append({"id": int(r.id), "name": r.name, "model_id": meta.get("model_id"),
                    "items": [int(i) for i in meta.get("items") or []], "version": int(r.version),
                    "created_at": r.created_at, "updated_at": r.updated_at})  # fmt: skip
    return out
