"""Hand edits of drillhole tables with a journal and undo (ТЗ 7.10 «Правка», «Журнал правок»; 7.34 п. 360).

Every change is one transaction: the data, the revision of the table (409 when the caller saw another one,
ADR 0003 §5) and the `edit_log` records (who, what, before → after). Undo applies the inverse in one
transaction and marks the record `undone`; an undone deletion comes back as a new row.

Revisions: an interval table — `dh_tables.rev`; collars and surveys — `objects.version` of the set.
"""

from __future__ import annotations

import getpass
import json
import math
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_rows, dh_surveys, dh_tables, edit_log, objects

from .snapshot import COLLAR_FIELDS, SURVEY_FIELDS, ColumnInfo, table_columns
from .store import pack, unpack

COLLAR_KEYS = {k for k, *_ in COLLAR_FIELDS} - {"hole"}
SURVEY_KEYS = {k for k, *_ in SURVEY_FIELDS} - {"hole"}


def author() -> str:
    try:
        return getpass.getuser()[:100]
    except Exception:  # noqa: BLE001 - no user name in the environment
        return "—"


def parse_number(value: Any, label: str) -> float | None:
    """«12,5» and «12.5» are 12.5; empty clears the field (ТЗ 7.10)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ApiError(422, "not_a_number", f"«{label}»: нужно число.", "Введите число, например 12,5.")
    if isinstance(value, int | float):
        f = float(value)
    else:
        text = str(value).strip().replace(" ", "").replace(" ", "")
        if not text:
            return None
        try:
            f = float(text.replace(",", "."))
        except ValueError:
            raise ApiError(
                422, "not_a_number", f"«{label}»: «{value}» — не число.", "Введите число, например 12,5."
            ) from None
    if math.isnan(f) or math.isinf(f):
        raise ApiError(422, "not_a_number", f"«{label}»: нужно конечное число.", "Введите число, например 12,5.")
    return f


def parse_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass
class Change:
    """One row change for the journal and the snapshot patch."""

    action: str  # update | insert | delete
    row_id: int
    hole: str | None
    hole_id: int | None
    changes: dict[str, list[Any]] = field(default_factory=dict)  # key → [before, after]


# --- the table being edited -------------------------------------------------------------------


@dataclass
class Target:
    set_id: int
    table: str  # collar | survey | <id>
    kind: str
    columns: list[ColumnInfo]

    def col(self, key: str) -> ColumnInfo:
        for c in self.columns:
            if c.key == key:
                return c
        if key.startswith("a:") and self.table in ("collar", "survey"):
            return ColumnInfo(key, key[2:], "text", role="attribute")
        raise ApiError(404, "column_not_found", "Колонка не найдена.", "Обновите таблицу.")


def target(conn: sa.Connection, set_id: int, table: str) -> Target:
    from .snapshot import build_collars, build_surveys

    if (
        conn.execute(sa.select(objects.c.id).where(objects.c.id == set_id, objects.c.kind == "drillhole_set")).first()
        is None
    ):
        raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
    if table == "collar":
        return Target(set_id, table, "collar", build_collars(conn, set_id, ()).columns)
    if table == "survey":
        return Target(set_id, table, "survey", build_surveys(conn, set_id, ()).columns)
    row = (
        conn.execute(
            sa.select(dh_tables.c.kind).where(
                dh_tables.c.id == int(table), dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
            )
        ).first()
        if table.isdigit()
        else None
    )
    if row is None:
        raise ApiError(404, "table_not_found", "Таблица не найдена.", "Обновите дерево проекта.")
    return Target(set_id, table, row.kind, table_columns(conn, int(table), row.kind))


def revision(conn: sa.Connection, t: Target) -> int:
    if t.table in ("collar", "survey"):
        return int(conn.execute(sa.select(objects.c.version).where(objects.c.id == t.set_id)).scalar_one())
    return int(conn.execute(sa.select(dh_tables.c.rev).where(dh_tables.c.id == int(t.table))).scalar_one())


def bump(conn: sa.Connection, t: Target, expected: int | None, rows_delta: int = 0) -> int:
    """Next revision; 409 when `expected` is not the current one (someone else changed the table)."""
    now = dialect.utcnow()
    if t.table in ("collar", "survey"):
        tbl, col, where = objects, objects.c.version, objects.c.id == t.set_id
        values: dict[str, Any] = {"version": objects.c.version + 1, "updated_at": now}
    else:
        tbl, col, where = dh_tables, dh_tables.c.rev, dh_tables.c.id == int(t.table)
        values = {"rev": dh_tables.c.rev + 1, "updated_at": now, "row_count": dh_tables.c.row_count + rows_delta}
    q = sa.update(tbl).where(where)
    if expected is not None:
        q = q.where(col == expected)
    if conn.execute(q.values(**values)).rowcount != 1:
        raise ApiError(
            409, "version_conflict", "Таблица изменилась, пока вы её правили.", "Обновите таблицу и повторите правку."
        )
    if t.table not in ("collar", "survey"):  # the set changed too (3D, charts, audit «устарел»)
        conn.execute(sa.update(objects).where(objects.c.id == t.set_id).values(updated_at=now))
    return revision(conn, t)


def new_group(conn: sa.Connection) -> int:
    return int(conn.execute(sa.select(sa.func.coalesce(sa.func.max(edit_log.c.group_id), 0))).scalar_one()) + 1


def journal(conn: sa.Connection, t: Target, group: int, changes: list[Change], who: str) -> None:
    if not changes:
        return
    now = dialect.utcnow()
    conn.execute(
        sa.insert(edit_log),
        [
            {
                "scope_kind": "object",
                "scope_id": t.set_id,
                "group_id": group,
                "action": c.action,
                "row_ref": {"table": t.table, "row": c.row_id, "hole": c.hole},
                "changes": c.changes,
                "undone": False,
                "author": who,
                "created_at": now,
            }
            for c in changes
        ],
    )


# --- reading and writing one row ---------------------------------------------------------------


def hole_by_name(conn: sa.Connection, set_id: int, name: str, *, create: bool) -> int | None:
    hid = conn.execute(sa.select(dh_holes.c.id).where(dh_holes.c.set_id == set_id, dh_holes.c.name == name)).scalar()
    if hid is None and create:
        res = conn.execute(sa.insert(dh_holes).values(set_id=set_id, name=name, has_collar=False))
        key = res.inserted_primary_key
        assert key is not None
        hid = int(key[0])
    return None if hid is None else int(hid)


def read_row(conn: sa.Connection, t: Target, row_id: int) -> tuple[dict[str, Any], int, str] | None:
    """Values of a row by column key, its hole id and name."""
    if t.table == "collar":
        r = conn.execute(
            sa.select(dh_holes).where(
                dh_holes.c.id == row_id, dh_holes.c.set_id == t.set_id, dh_holes.c.has_collar.is_(True)
            )
        ).first()
        if r is None:
            return None
        vals = {k: getattr(r, k) for k in COLLAR_KEYS}
        vals.update({f"a:{k}": v for k, v in (r.attrs or {}).items()})
        return {"hole": r.name, **vals}, int(r.id), r.name
    if t.table == "survey":
        r = conn.execute(
            sa.select(dh_surveys, dh_holes.c.name)
            .join(dh_holes, dh_holes.c.id == dh_surveys.c.hole_id)
            .where(dh_surveys.c.id == row_id, dh_surveys.c.set_id == t.set_id)
        ).first()
        if r is None:
            return None
        vals = {k: getattr(r, k) for k in SURVEY_KEYS}
        vals.update({f"a:{k}": v for k, v in (r.attrs or {}).items()})
        return {"hole": r.name, **vals}, int(r.hole_id), r.name
    r = conn.execute(
        sa.select(dh_rows, dh_holes.c.name)
        .join(dh_holes, dh_holes.c.id == dh_rows.c.hole_id)
        .where(dh_rows.c.id == row_id, dh_rows.c.table_id == int(t.table))
    ).first()
    if r is None:
        return None
    width = max([c.slot for c in t.columns if c.kind == "number" and c.slot is not None], default=-1) + 1
    nums = unpack(r.num, width)
    txt = json.loads(r.txt) if isinstance(r.txt, str) else (r.txt or {})
    vals = {"hole": r.name, "from": r.from_m, "to": r.to_m}
    for c in t.columns:
        if c.slot is None:
            continue
        if c.kind == "number":
            v = nums[c.slot]
            vals[c.key] = None if math.isnan(v) else v
        else:
            vals[c.key] = txt.get(str(c.slot))
    return vals, int(r.hole_id), r.name


def clean(t: Target, key: str, value: Any) -> Any:
    c = t.col(key)
    if c.kind == "number":
        return parse_number(value, c.name)
    return parse_text(value)


def check_row(t: Target, vals: dict[str, Any]) -> None:
    """Required fields, «до» ≥ «от» (ТЗ 7.10 «Проверки»)."""
    for c in t.columns:
        if c.required and vals.get(c.key) is None:
            raise ApiError(
                422, "required_field", f"«{c.name}» — обязательное поле.", "Заполните поле или отмените правку."
            )
    if t.kind in ("assay", "interval") and vals.get("from") is not None and vals.get("to") is not None:
        if vals["to"] < vals["from"]:
            raise ApiError(422, "to_before_from", "«До» меньше «От».", "Исправьте глубины интервала: «До» ≥ «От».")


def write_row(conn: sa.Connection, t: Target, row_id: int | None, hole_id: int, vals: dict[str, Any]) -> int:
    """Stores the whole row (insert when `row_id` is None); returns the row id."""
    attrs = {k[2:]: v for k, v in vals.items() if k.startswith("a:") and v not in (None, "")} or None
    if t.table == "collar":
        values = {k: vals.get(k) for k in COLLAR_KEYS}
        conn.execute(sa.update(dh_holes).where(dh_holes.c.id == hole_id).values(**values, has_collar=True, attrs=attrs))
        return hole_id
    if t.table == "survey":
        values = {k: vals.get(k) for k in SURVEY_KEYS}
        if row_id is None:
            res = conn.execute(sa.insert(dh_surveys).values(set_id=t.set_id, hole_id=hole_id, attrs=attrs, **values))
            key = res.inserted_primary_key
            assert key is not None
            return int(key[0])
        conn.execute(
            sa.update(dh_surveys).where(dh_surveys.c.id == row_id).values(hole_id=hole_id, attrs=attrs, **values)
        )
        return row_id
    width = max([c.slot for c in t.columns if c.kind == "number" and c.slot is not None], default=-1) + 1
    nums: list[float | None] = [None] * width
    txt: dict[str, str] = {}
    for c in t.columns:
        if c.slot is None:
            continue
        v = vals.get(c.key)
        if c.kind == "number":
            nums[c.slot] = v
        elif v not in (None, ""):
            txt[str(c.slot)] = v
    values = {
        "hole_id": hole_id,
        "from_m": vals.get("from"),
        "to_m": vals.get("from") if t.kind == "event" else vals.get("to"),
        "num": pack(nums),
        "txt": txt or None,
    }
    if row_id is None:
        res = conn.execute(sa.insert(dh_rows).values(table_id=int(t.table), **values))
        key = res.inserted_primary_key
        assert key is not None
        return int(key[0])
    conn.execute(sa.update(dh_rows).where(dh_rows.c.id == row_id).values(**values))
    return row_id


def unique_collar(conn: sa.Connection, t: Target, name: str, own_hole: int | None) -> int | None:
    """A collar row is the hole itself: a second collar with that number is refused (ТЗ 7.10). Returns the id
    of an existing hole without a collar to take over, or None to create one."""
    r = conn.execute(
        sa.select(dh_holes.c.id, dh_holes.c.has_collar).where(dh_holes.c.set_id == t.set_id, dh_holes.c.name == name)
    ).first()
    if r is None or r.id == own_hole:
        return None
    if r.has_collar:
        raise ApiError(
            409, "collar_exists", f"Устье скважины «{name}» уже есть в наборе.", "Введите другой номер скважины."
        )
    return int(r.id)


def _hole_text(v: Any) -> str:
    text = parse_text(v)
    if text is None:
        raise ApiError(422, "required_field", "«Скважина» — обязательное поле.", "Введите номер скважины.")
    return text
