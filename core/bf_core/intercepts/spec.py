"""Parameters of «Истинная мощность» and «Рудные пересечения» (ТЗ 8.5.4; ADR 0021 §4–10, решения 4–18): the bodies of
`POST /intercepts/tt` and `POST /intercepts/run` checked against the project and the ranges (docs/progress/e15-api.md).

    spec = parse_tt(conn, body)    # TtSpec; ApiError 422 `bad_param` {param}, `no_orientation` {param}; 404
    spec = parse_run(conn, body)   # RunSpec; its orientation may be empty: TT empty, TT_SRC «нет»

`orientation` — 1–3 sources by priority (a row takes the normal of the first that gave one; forms — e15-api.md).
The simple form of the command (§9: `source` and flat fields, `attitudes` as «код=α/Aп; …», `fallback_alpha` /
`fallback_dipdir` — a second, global source) is turned into that list first. `param` of an error in a source is a
path: «orientation.0.alpha».

`spec.body` is the canonical body (`dh_results.params`): tables as ids (text), fields by their stored names, every
number filled in, the orientation as a list; `rebind` puts the current ids into it for «Перестроить» (the tables of
`inputs` in the order of `tables()`, the wireframes in the order of `mesh_ids()`, a trend by its name — `trend_lookup`).
No numpy here (before BF-READY).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.composites import jobs as cj
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_results, dh_tables, objects

from . import INCLUDE_REL, LINES
from .spec_sources import (  # re-exported: the names of `spec` before the split
    ATTITUDES_MAX,
    MESH_MAX,
    RANGES,
    SOURCES,
    SRC_MAX,
    Attitude,
    Source,
    _any_table,
    _code,
    _core,
    _domain,
    _empty,
    _located,
    _mesh,
    _span,
    angle_pair,
    bad,
    choice,
    number,
    parse_attitudes,
    parse_orientation,
    ranged,
    simple_orientation,
)

__all__ = [
    "ANGLE_COLUMNS", "ATTITUDES_MAX", "DEFAULT_RUN_NAME", "LINES", "MESH_MAX", "MIN_BY", "MISSING", "RANGES",
    "SOURCES", "SRC_MAX", "TT_COLUMNS", "TT_TITLE", "Attitude", "RunSpec", "Source", "TtSpec", "_any_table", "_code",
    "_core", "_domain", "_empty", "_located", "_mesh", "_span", "angle_pair", "bad", "check_columns", "choice",
    "number", "parse_attitudes", "parse_orientation", "parse_run", "parse_tt", "ranged", "rebind",
    "simple_orientation",
]  # fmt: skip

MIN_BY = ("length", "tt")
MISSING = ("waste", "break")
TT_COLUMNS = ("TT", "TT_FLAG", "TT_SRC")
ANGLE_COLUMNS = ("ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ")
DEFAULT_RUN_NAME = "Рудные пересечения"
TT_TITLE = "Истинная мощность"


@dataclass
class TtSpec:
    set_id: int
    table_id: int
    table_name: str
    orientation: list[Source]
    eps: float
    step: float
    angles: bool
    body: dict[str, Any]  # canonical, without `replace` — `dh_results.params`
    rows: int = 0

    def columns(self) -> list[str]:
        """The fields written into the table (ADR 0021 §8)."""
        return [*TT_COLUMNS, *(ANGLE_COLUMNS if self.angles else ())]

    def tables(self) -> list[tuple[int, list[str]]]:
        """(table id, fields read) — `results.make_inputs`: the source first, then the tables of the orientation."""
        return _tables(self.table_id, [], self.orientation)

    def mesh_ids(self) -> list[int]:
        return [i for s in self.orientation for i in s.mesh_ids()]

    def hash_params(self) -> dict[str, Any]:
        return {"op": "tt", "orientation": [s.hash() for s in self.orientation], "eps": self.eps, "step": self.step,
                "angles": self.angles}  # fmt: skip


@dataclass
class RunSpec:
    set_id: int
    table_id: int
    table_name: str
    field: str  # stored name of the grade
    unit: str | None
    means: list[str]  # numeric fields averaged into the table: the grade first
    units: dict[str, str | None]
    density: str | None
    cutoff: float
    include_cutoff: float | None  # c2 > c of «в том числе»; None — none
    l_min: float  # L_min (min_by = length) or m_min (min_by = tt)
    min_by: str
    waste_max: float
    missing: str
    orientation: list[Source]
    eps: float
    step: float
    name: str
    replace: bool
    body: dict[str, Any]
    rows: int = 0

    def tables(self) -> list[tuple[int, list[str]]]:
        own = [*self.means, *([self.density] if self.density else [])]
        return _tables(self.table_id, own, self.orientation)

    def read_fields(self) -> list[str]:
        """Fields of the samples read (the grades, the density, a domain or core field of the same table)."""
        return self.tables()[0][1]

    def mesh_ids(self) -> list[int]:
        return [i for s in self.orientation for i in s.mesh_ids()]

    def hash_params(self) -> dict[str, Any]:
        """The units too: they go into LABEL and LEN_G / TT_G, and a table replaced with other units but the same
        values must not be taken as fresh by «Перестроить»."""
        return {"op": "intercepts", "field": self.field, "unit": self.unit, "means": self.means,
                "units": self.units, "density": self.density,
                "cutoff": self.cutoff, "include_cutoff": self.include_cutoff, "l_min": self.l_min,
                "min_by": self.min_by, "waste_max": self.waste_max, "missing": self.missing,
                "orientation": [s.hash() for s in self.orientation], "eps": self.eps, "step": self.step}  # fmt: skip


def _tables(table_id: int, own: list[str], orientation: list[Source]) -> list[tuple[int, list[str]]]:
    """The source first, then each other table once, in the order of the sources (`rebind` walks the same)."""
    order: dict[int, list[str]] = {table_id: list(own)}
    for s in orientation:
        t = s.table()
        if t is not None:
            order.setdefault(t[0], []).extend(t[1])
    return [(tid, list(dict.fromkeys(f))) for tid, f in order.items()]


def _core_rows(conn: sa.Connection, orientation: list[Source]) -> int:
    ids = [s.table_id for s in orientation if s.kind == "core"]
    q = sa.select(sa.func.sum(dh_tables.c.row_count)).where(dh_tables.c.id.in_(ids))
    return int(conn.execute(q).scalar() or 0) if ids else 0


def _set_id(body: dict[str, Any]) -> int:
    set_id = body.get("set_id")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    return set_id


def _source(conn: sa.Connection, set_id: int, body: dict[str, Any]) -> Any:
    return _located(lambda: cj.resolve_table(conn, set_id, body.get("table")), "", "table")


def parse_tt(conn: sa.Connection, body: dict[str, Any]) -> TtSpec:
    """The body of `POST /intercepts/tt`: an interval table of the set and 1–3 sources of the orientation."""
    set_id = _set_id(body)
    t = _source(conn, set_id, body)
    orientation = parse_orientation(conn, set_id, t, body)
    if not orientation:
        raise _empty("не выбран источник", "Выберите источник ориентировки пласта.", "orientation")
    eps, step = ranged(body, "eps"), ranged(body, "step")
    angles = body.get("angles", True)
    if not isinstance(angles, bool):
        raise bad("bad_param", "Углы: ожидается «да» или «нет».", "Отметьте или снимите галочку.", "angles")
    canon = {"set_id": set_id, "table": str(t.id), "orientation": [s.canonical() for s in orientation], "eps": eps,
             "step": step, "angles": angles}  # fmt: skip
    rows = int(t.row_count or 0) + _core_rows(conn, orientation)
    return TtSpec(set_id, int(t.id), str(t.name), orientation, eps, step, angles, canon, rows)


def _numeric(conn: sa.Connection, table_id: int, raw: Any, label: str, param: str) -> Any:
    from bf_core.composites.source import _fields

    if not isinstance(raw, str) or not raw.strip():
        raise bad("bad_param", f"{label}: не выбрано поле.", "Выберите числовое поле таблицы.", param)
    f = _located(lambda: next(iter(_fields(conn, table_id, [raw.strip()]).values())), "", param)
    if f.kind != "number":
        raise bad("bad_param", f"{label}: поле «{f.name}» не числовое.", "Выберите числовое поле.", param)
    return f


def parse_run(conn: sa.Connection, body: dict[str, Any]) -> RunSpec:
    """The body of `POST /intercepts/run`: the rule of 8.5.3 on an interval table, the orientation optional."""
    set_id = _set_id(body)
    t = _source(conn, set_id, body)
    tid = int(t.id)
    g = _numeric(conn, tid, body.get("field"), "Поле содержания", "field")
    cutoff = number(body.get("cutoff"), "Бортовое содержание c", "cutoff")
    if not cutoff > 0.0:  # as `rule.RuleParams.checked` (ТЗ 8.5.4): g ≥ c with c ≤ 0 would take every sample
        raise bad("bad_param", "Бортовое содержание c должно быть больше нуля.", "Задайте c больше нуля.", "cutoff")
    inc = body.get("include_cutoff")
    c2 = None if inc in (None, "") else number(inc, "Борт «в том числе» c2", "include_cutoff")
    if c2 is not None and not c2 > cutoff * (1.0 + INCLUDE_REL):  # as `rule.RuleParams.checked`
        raise bad("bad_param", "Борт «в том числе» c2 должен быть больше бортового c.",
                  "Задайте c2 больше c или оставьте поле пустым.", "include_cutoff")  # fmt: skip
    density = None
    if isinstance(body.get("density"), str) and body["density"].strip():
        density = _numeric(conn, tid, body["density"], "Плотность", "density").name
    means = _means(conn, tid, g.name, density, body.get("fields"))
    orientation = parse_orientation(conn, set_id, t, body)
    name = body.get("name")
    name = str(name).strip() if isinstance(name, str) and name.strip() else DEFAULT_RUN_NAME
    if len(name) > cj.NAME_MAX:
        raise bad("bad_param", "Имя таблицы слишком длинное.", f"Не больше {cj.NAME_MAX} знаков.", "name")
    if dialect.name_key(name) == dialect.name_key(t.name):
        raise bad("name_is_source", "Имя результата совпадает с таблицей-источником.", "Задайте другое имя.", "name")
    units = dict(conn.execute(sa.select(dh_columns.c.name, dh_columns.c.unit).where(
        dh_columns.c.table_id == tid, dh_columns.c.deleted.is_(False))).tuples().all())  # fmt: skip
    rule = {"field": g.name, "cutoff": cutoff, "include_cutoff": c2, "l_min": ranged(body, "l_min"),
            "min_by": choice(body.get("min_by"), MIN_BY, "length", "Критерий приёмки", "min_by"),
            "waste_max": ranged(body, "waste_max"),
            "missing": choice(body.get("missing"), MISSING, "waste", "Пропуски", "missing")}  # fmt: skip
    eps, step = ranged(body, "eps"), ranged(body, "step")
    canon = {"set_id": set_id, "table": str(tid), **rule, "density": density, "fields": means[1:],
             "orientation": [s.canonical() for s in orientation], "eps": eps, "step": step, "name": name}  # fmt: skip
    rows = int(t.row_count or 0) + _core_rows(conn, orientation)
    return RunSpec(set_id=set_id, table_id=tid, table_name=str(t.name), field=g.name, unit=g.unit, means=means,
                   units={n: units.get(n) for n in means}, density=density, cutoff=cutoff, include_cutoff=c2,
                   l_min=rule["l_min"], min_by=rule["min_by"], waste_max=rule["waste_max"], missing=rule["missing"],
                   orientation=orientation, eps=eps, step=step, name=name, replace=body.get("replace") is True,
                   body=canon, rows=rows)  # fmt: skip


def _means(conn: sa.Connection, table_id: int, grade: str, density: str | None, raw: Any) -> list[str]:
    """The grade first, then the numeric fields averaged: `fields` as given, or every numeric field of the table but
    the density, the fixed ones and those «Истинная мощность» writes (TT, ALPHA, …)."""
    from bf_core.composites.source import _fields

    skip = {grade.casefold(), *([density.casefold()] if density else [])}
    if raw is not None and raw != "":
        names = _located(lambda: cj._names(raw), "", "fields")
        chosen = _located(lambda: _fields(conn, table_id, names), "", "fields")
        text = [f.name for f in chosen.values() if f.kind != "number"]
        if text:
            raise bad("bad_param", f"Поле «{text[0]}» не числовое.", "Отметьте числовые поля содержаний.", "fields")
        return [grade, *(n for n in chosen if n.casefold() not in skip)]
    skip |= {c.casefold() for c in (*TT_COLUMNS, *ANGLE_COLUMNS)}
    cols = conn.execute(
        sa.select(dh_columns.c.name, dh_columns.c.role)
        .where(dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False), dh_columns.c.kind == "number")
        .order_by(dh_columns.c.position, dh_columns.c.id)
    ).all()
    return [grade, *(c.name for c in cols if c.name.casefold() not in skip and c.role not in ("source", "fixed"))]


def check_columns(conn: sa.Connection, spec: TtSpec) -> None:
    """409 `column_exists` {param: "table", name}: a field TT, ALPHA, … of the table that no earlier «Истинная
    мощность» of this table wrote (a field of the user is never overwritten; a repeat overwrites its own)."""
    q = sa.select(dh_columns.c.name).where(dh_columns.c.table_id == spec.table_id, dh_columns.c.deleted.is_(False))
    live = {r.name.casefold(): r.name for r in conn.execute(q)}
    ours: set[str] = set()
    q = sa.select(dh_results.c.report).where(dh_results.c.table_id == spec.table_id, dh_results.c.op == "tt")
    for r in conn.execute(q):
        for w in (r.report or {}).get("written") or []:
            ours |= {str(n).casefold() for n in w.get("columns") or []}
    clash = [live[n.casefold()] for n in spec.columns() if n.casefold() in live and n.casefold() not in ours]
    if clash:
        raise ApiError(409, "column_exists", f"В таблице «{spec.table_name}» уже есть поле «{clash[0]}».",
                       "Переименуйте поле таблицы: в него пишется истинная мощность.",
                       {"param": "table", "name": clash[0]})  # fmt: skip


def trend_lookup(conn: sa.Connection) -> Callable[[Any, Any], int]:
    """(old id, name) of a source `trend` → its id in this project: the old id when it still names a trend of that
    name, else the trend of that name (the first), else 0 (`parse_orientation`: 422 `trend_not_found`) — a `.bfggis`
    import renumbers the objects."""
    named = {int(r.id): str(r.name_key) for r in conn.execute(sa.select(objects.c.id, objects.c.name_key)
                                                              .where(objects.c.kind == "trend"))}  # fmt: skip

    def find(old: Any, name: Any) -> int:
        key = dialect.name_key(str(name or ""))
        try:
            if named.get(int(old)) == key:
                return int(old)
        except (TypeError, ValueError):
            pass
        return min((i for i, k in named.items() if k == key), default=0)

    return find


def rebind(params: dict[str, Any], tables: list[int], meshes: list[int],
           trends: Callable[[Any, Any], int] | None = None) -> dict[str, Any]:  # fmt: skip
    """The canonical body of a result with the current ids: `tables` — of `inputs.tables` now (the order of
    `tables()`), `meshes` — of `inputs.meshes` now (the order of `mesh_ids()`), `trends` (`trend_lookup`) — the ids of
    the sources `trend` by their names; None — as they are."""
    seen: dict[str, str] = {}
    free = iter(str(t) for t in tables)
    flat = iter(meshes)

    def table(old: Any) -> str:
        key = str(old)
        if key not in seen:
            seen[key] = next(free, key)
        return seen[key]

    out = {**params, "table": table(params.get("table"))}
    srcs = []
    for s in params.get("orientation") or []:
        s = dict(s)
        if s.get("source") in ("domain", "core") and s.get("table") is not None:
            s["table"] = table(s["table"])
        if s.get("wireframe_ids"):
            s["wireframe_ids"] = [next(flat, i) for i in s["wireframe_ids"]]
        if s.get("source") == "trend" and trends is not None:
            s["trend_id"] = trends(s.get("trend_id"), s.get("trend"))
        srcs.append(s)
    out["orientation"] = srcs
    return out
