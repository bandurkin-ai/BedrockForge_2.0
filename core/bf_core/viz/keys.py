"""ETags of derived 3D data and the shared request helpers (ADR 0007 §1 «ETag»).

The ETag of a response is the SHA-256 of its key, not of its body: the format, the project uid, the set, its
`objects.version`, the revisions of its tables, the method, the parameters, the part and the audit run. The key
is a few small queries, so a repeated request gets 304 before any calculation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects

FORMAT = 1
CACHE_HEADERS = {"Cache-Control": "no-cache"}


def set_not_found() -> ApiError:
    return ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")


@dataclass(frozen=True)
class SetState:
    set_id: int
    name: str
    version: int
    tables: tuple[Any, ...]  # (sum of rev, count, digest of id:rev:created of every table)

    def key(self) -> list[Any]:
        return [self.set_id, self.version, *self.tables]


def _stamp(created: Any) -> str:
    return "0" if created is None else f"{int(created.timestamp() * 1_000_000):x}"


def set_state(conn: sa.Connection, set_id: int) -> SetState:
    s = conn.execute(
        sa.select(objects.c.name, objects.c.version, objects.c.created_at).where(
            objects.c.id == set_id, objects.c.kind == "drillhole_set", objects.c.state == "ready"
        )
    ).first()
    if s is None:
        raise set_not_found()
    rows = conn.execute(
        sa.select(dh_tables.c.id, dh_tables.c.rev, dh_tables.c.state, dh_tables.c.created_at)
        .where(dh_tables.c.set_id == set_id)
        .order_by(dh_tables.c.id)
    ).all()
    # a sum of revisions alone repeats when a table is replaced; ids and creation times tell them apart
    text = ";".join(f"{r.id}:{r.rev}:{r.state}:{_stamp(r.created_at)}" for r in rows)
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    return SetState(
        set_id, s.name, int(s.version), (sum(int(r.rev) for r in rows), len(rows), _stamp(s.created_at), digest)
    )


class Keyed(Protocol):
    """What an ETag is made of: the state of a set, of a line layer (`viz/lines.py`), …"""

    def key(self) -> list[Any]: ...


def etag(kind: str, uid: str, state: Keyed, *rest: Any) -> str:
    key = json.dumps([kind, FORMAT, uid, *state.key(), *rest], ensure_ascii=False, separators=(",", ":"))
    return '"' + hashlib.sha256(key.encode("utf-8")).hexdigest() + '"'


def matches(if_none_match: str | None, tag: str) -> bool:
    """If-None-Match holds the tag (a list, weak tags and «*» as RFC 9110 allows)."""
    if not if_none_match:
        return False
    for t in if_none_match.split(","):
        t = t.strip()
        if t == "*" or t.removeprefix("W/") == tag:
            return True
    return False


@dataclass(frozen=True)
class TableRef:
    id: int
    name: str
    kind: str  # assay | interval | event
    rev: int
    stamp: str


def table_by_key(conn: sa.Connection, set_id: int, name: str) -> TableRef:
    """A ready table of the set by `dh_tables.name_key` (the name survives «Заменить» and `.bfggis`)."""
    r = conn.execute(
        sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind, dh_tables.c.rev, dh_tables.c.created_at).where(
            dh_tables.c.set_id == set_id,
            dh_tables.c.state == "ready",
            dh_tables.c.name_key == dialect.name_key(name),
        )
    ).first()
    if r is None:
        raise ApiError(
            404,
            "table_not_found",
            f"Таблица «{name}» не найдена в наборе.",
            "Обновите дерево проекта или выберите таблицу заново.",
        )
    return TableRef(int(r.id), r.name, r.kind, int(r.rev), _stamp(r.created_at))


@dataclass(frozen=True)
class FieldRef:
    name: str
    key: str  # snapshot column key c<dh_columns.id>
    kind: str  # number | text
    unit: str | None


def fields_of(conn: sa.Connection, table: TableRef, names: list[str]) -> list[FieldRef]:
    """Value columns by name: the exact name, else the same name ignoring case."""
    from bf_core.drillholes.snapshot import table_columns

    cols = [c for c in table_columns(conn, table.id, table.kind) if c.role != "fixed"]
    out: list[FieldRef] = []
    for name in names:
        hit = next((c for c in cols if c.name == name), None) or next(
            (c for c in cols if dialect.name_key(c.name) == dialect.name_key(name)), None
        )
        if hit is None:
            raise ApiError(
                404,
                "field_not_found",
                f"Поле «{name}» не найдено в таблице «{table.name}».",
                "Обновите дерево проекта и выберите поле из списка таблицы.",
            )
        out.append(FieldRef(hit.name, hit.key, hit.kind, hit.unit))
    return out


def bad_method() -> ApiError:
    from bf_core.drillholes.desurvey import METHOD_TITLES

    return ApiError(
        422, "bad_method", "Неизвестный метод траекторий.", "Выберите: " + ", ".join(METHOD_TITLES.values()) + "."
    )
