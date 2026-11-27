"""Read tools `data_table` and `data_stats` of the assistant (подшаг 23.8; ТЗ 7.32, 7.10, 7.29; ADR 0029 решение 18;
e23-api.md §5.2) plus the helpers `data_audit.py` shares (`clean`, `require_set`, `project_of`, `fit`).

    data_table(ctx, params) -> dict
        {table, kind, total, columns, columns_total, rows [[…]], next_offset}: a page of a table of the set —
        «Устья», «Инклинометрия» or an interval table — by the snapshot of «Таблица» (`drillholes.query.page`,
        the same rows and order as the window). A missing value is `null`, never 0. `columns` — names, comma
        separated; an unknown one is an error that lists the columns of the table. `hole_id` — the name of a hole.
    data_stats(ctx, params) -> dict
        {table, field, unit, weight, n, missing, rows, min, p10, p50, p90, max, mean, sd, cv [, domains [..]]}: the
        statistics of a field as the tab «Статистика» shows them — not recomputed here: `stats.routes_more`
        `domains_of` (Э13: the weighted sample of `stats.select`, one sort, `stats.domain_stats`) with the default
        settings of ТЗ 7.25 (weight — the length of the interval). Missing values (and intervals without a length)
        take no part in `n`, they are counted in `missing`; nothing becomes 0. `domain` — a text field of the same
        table: the same numbers per domain.

`ctx` (made by `data_tools.py`) gives the open project and the active set; it is read by duck typing:
`ctx.project` (needs `engine`, `uid`, `folder`) and `ctx.set_id` (int | None). `params["set_id"]` wins.
The functions are pure reads, raise `ApiError` (Russian message + hint) — the caller turns it into `is_error`.
Texts from the project (names of holes, columns, cells) are untrusted data: control characters are removed and
the length is cut (`clean`). Numbers in `data_table` carry 6 significant digits (ADR 0029); `data_stats` keeps the
numbers of Э13 as they are (the caller rounds the answer).

The functions are registered at the bottom with `data_tools.register(name, schema, fn)`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects

from .data_tools import num

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_COLUMNS = 24  # columns shown when `columns` is not given
CELL_LEN = 200
BUDGET = 12_000  # characters of `rows` — the whole answer stays under the 16 000 of `data_tools`
MAX_DOMAINS = 30

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ​-‏‪-‮⁦-⁩﻿]")
_SPACES = re.compile(r"\s{2,}")


# --- shared helpers ---------------------------------------------------------------------------------------------


def clean(value: Any, limit: int = CELL_LEN) -> str:
    """A text of the project for the model: no control and direction characters, one line, ≤ `limit` signs."""
    text = _SPACES.sub(" ", _CONTROL.sub(" ", str(value))).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def error(code: str, message: str, hint: str = "", status: int = 422) -> ApiError:
    return ApiError(status, code, message, hint)


def project_of(ctx: Any) -> Any:
    project = ctx.get("project") if isinstance(ctx, dict) else getattr(ctx, "project", None)
    if project is None:
        raise error("no_project", "Проект не открыт.", "Откройте или создайте проект.", 409)
    return project


def _int(params: dict[str, Any], key: str, default: int | None, low: int, high: int | None = None) -> int | None:
    v = params.get(key)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int | float) or int(v) != v:
        raise error("bad_param", f"Параметр «{key}» должен быть целым числом.", "Передайте целое число.")
    v = int(v)
    if v < low:
        raise error("bad_param", f"Параметр «{key}» не может быть меньше {low}.", "Исправьте значение.")
    return v if high is None else min(v, high)


def page_args(params: dict[str, Any]) -> tuple[int, int]:
    """(offset, limit): offset ≥ 0, 1 ≤ limit ≤ 200 (a larger one is cut to 200)."""
    offset = _int(params, "offset", 0, 0) or 0
    limit = _int(params, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT) or DEFAULT_LIMIT
    return offset, limit


def require_set(project: Any, ctx: Any, params: dict[str, Any]) -> tuple[int, str]:
    """(id, name) of the set of the call: `set_id` of the params, else the active set of the screen."""
    raw = params.get("set_id")
    if raw is None:
        raw = ctx.get("set_id") if isinstance(ctx, dict) else getattr(ctx, "set_id", None)
    if raw is None:
        raise error("no_set", "Нет активного набора скважин: укажите set_id.", "Выберите набор в дереве проекта.")
    if isinstance(raw, bool) or not isinstance(raw, int | float) or int(raw) != raw:
        raise error("bad_param", "Параметр «set_id» должен быть целым числом.", "Укажите номер набора из дерева.")
    set_id = int(raw)
    with dialect.read_tx(project.engine) as conn:
        name = conn.execute(
            sa.select(objects.c.name).where(
                objects.c.id == set_id, objects.c.kind == "drillhole_set", objects.c.state == "ready"
            )
        ).scalar()
    if name is None:
        raise error("set_not_found", f"Набор скважин {set_id} не найден.", "Сверьтесь с деревом проекта.", 404)
    return set_id, str(name)


def tick(ctx: Any) -> None:
    """The cooperative stop of `data_tools.DataCall.check()`; a plain context (tests) has none."""
    check = getattr(ctx, "check", None)
    if callable(check):
        check()


def fit(rows: Iterable[Any], budget: int = BUDGET) -> list[Any]:
    """The rows that fit in `budget` characters of JSON (at least one). The rest goes to the next page."""
    out: list[Any] = []
    used = 0
    for r in rows:
        size = len(json.dumps(r, ensure_ascii=False, default=str)) + 1
        if out and used + size > budget:
            break
        out.append(r)
        used += size
    return out


def _listing(names: Iterable[str], limit: int = 30) -> str:
    shown = [clean(n, 60) for n in names]
    more = len(shown) - limit
    return ", ".join(shown[:limit]) + (f" и ещё {more}" if more > 0 else "")


# --- data_table -------------------------------------------------------------------------------------------------


def _tables(project: Any, set_id: int) -> list[tuple[str, str, str]]:
    """(key of the snapshot, name, kind) of every table of the set, as the tabs of «Таблица»."""
    with dialect.read_tx(project.engine) as conn:
        rows = conn.execute(
            sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind)
            .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
            .order_by(sa.case((dh_tables.c.kind == "assay", 0), (dh_tables.c.kind == "interval", 1), else_=2),
                      dh_tables.c.id)
        ).all()  # fmt: skip
    return [("collar", "Устья", "collar"), ("survey", "Инклинометрия", "survey"),
            *((str(r.id), str(r.name), str(r.kind)) for r in rows)]  # fmt: skip


def _find_table(project: Any, set_id: int, name: Any) -> tuple[str, str, str]:
    tabs = _tables(project, set_id)
    if not isinstance(name, str) or not name.strip():
        raise error("bad_param", "Не указана таблица.", f"Таблицы набора: {_listing(t[1] for t in tabs)}.")
    want = dialect.name_key(name)
    for key, title, kind in tabs:
        if want in (dialect.name_key(title), key):
            return key, title, kind
    raise error(
        "table_not_found",
        f"В наборе нет таблицы «{clean(name, 80)}».",
        f"Таблицы набора: {_listing(t[1] for t in tabs)}.",
        404,
    )


def _hole_id(snap: Any, name: Any) -> int:
    want = dialect.name_key(str(name))
    for i, n in snap.hole_names.items():
        if dialect.name_key(n) == want:
            return int(i)
    raise error("hole_not_found", f"Скважина «{clean(name, 80)}» не найдена в наборе.",
                "Имена скважин даёт инструмент data_holes.", 404)  # fmt: skip


def _pick_columns(snap: Any, wanted: Any, table: str) -> tuple[list[Any], bool]:
    """(the columns to show, whether the choice was the default one)."""
    infos = list(snap.columns)
    if wanted is None or (isinstance(wanted, str) and not wanted.strip()):
        return infos[:MAX_COLUMNS], True
    if not isinstance(wanted, str):
        raise error("bad_param", "Параметр «columns» — имена столбцов через запятую.", "Пример: «От, До, AU».")
    by_name = {c.name.casefold(): c for c in infos}
    by_key = {c.key.casefold(): c for c in infos}
    chosen: list[Any] = []
    for part in wanted.split(","):
        part = part.strip()
        if not part:
            continue
        c = by_name.get(part.casefold()) or by_key.get(part.casefold())
        if c is None:
            raise error(
                "column_not_found",
                f"Столбец «{clean(part, 80)}» не найден в таблице «{clean(table, 80)}».",
                f"Столбцы таблицы: {_listing(c.name for c in infos)}.",
            )
        if c not in chosen:
            chosen.append(c)
    if not chosen:
        raise error("bad_param", "В «columns» нет ни одного имени столбца.", "Пример: «От, До, AU».")
    return chosen, False


def _cell(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v  # an id or a sample number is never rounded
    if isinstance(v, float):
        return num(v)  # 6 significant digits, the integer part is kept; NaN / inf -> None (a gap is not 0)
    item = getattr(v, "item", None)  # a numpy scalar
    if callable(item):
        return _cell(item())
    return clean(v)


def data_table(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from bf_core.drillholes.query import page
    from bf_core.drillholes.router import snapshot_of

    project = project_of(ctx)
    set_id, _ = require_set(project, ctx, params)
    key, title, kind = _find_table(project, set_id, params.get("table"))
    offset, limit = page_args(params)
    snap = snapshot_of(project, set_id, key)
    cols, default = _pick_columns(snap, params.get("columns"), title)
    hole = None if params.get("hole_id") in (None, "") else _hole_id(snap, params["hole_id"])
    tick(ctx)
    got = page(snap, offset=offset, limit=limit, hole=hole)
    tick(ctx)
    total = int(got["total"])
    rows = fit([_cell(r[c.key]) for c in cols] for r in got["rows"])
    end = offset + len(rows)
    out: dict[str, Any] = {
        "table": clean(title),
        "kind": kind,
        "total": total,
        "columns": [clean(c.name, 80) for c in cols],
        "rows": rows,
        "next_offset": end if end < total else None,
    }
    if default and len(snap.columns) > len(cols):
        out["columns_total"] = len(snap.columns)  # the rest is asked for with `columns`
        out["columns_available"] = [clean(c.name, 80) for c in snap.columns[len(cols) :]][:60]
    return out


# --- data_stats -------------------------------------------------------------------------------------------------


def _fields(infos: list[Any], name: Any, kinds: tuple[str, ...], what: str, table: str) -> Any:
    if not isinstance(name, str) or not name.strip():
        raise error("bad_param", f"Не указано {what}.", "Передайте имя поля таблицы.")
    f = next((f for f in infos if f.name.casefold() == name.strip().casefold()), None)
    usable = [x.name for x in infos if x.kind in kinds and x.role != "length"]
    if f is None:
        raise error("field_not_found", f"Поле «{clean(name, 80)}» не найдено в таблице «{clean(table, 80)}».",
                    f"Подходящие поля: {_listing(usable)}.")  # fmt: skip
    if f.kind not in kinds:
        raise error("field_wrong_kind",
                    f"Поле «{clean(f.name, 80)}» — {'текстовое' if f.kind == 'text' else 'числовое'}: "
                    f"нужно {'числовое' if 'number' in kinds else 'текстовое'}.",
                    f"Подходящие поля: {_listing(usable)}.")  # fmt: skip
    return f


def _stat_row(r: dict[str, Any], label: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {} if label is None else {"domain": label}
    out.update(n=int(r["n"]), mean=r["mean"], sd=r["std"], cv=r["cv"], min=r["min"], p10=r["p10"], p50=r["p50"],
               p90=r["p90"], max=r["max"])  # fmt: skip
    return out


def data_stats(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from bf_core.stats import routes_charts as rc
    from bf_core.stats import routes_more, source
    from bf_core.stats.weighted import StatsError

    project = project_of(ctx)
    set_id, _ = require_set(project, ctx, params)
    tabs = _tables(project, set_id)
    tick(ctx)
    name = params.get("table")
    if isinstance(name, str) and dialect.name_key(name) in ("устья", "инклинометрия", "collar", "survey"):
        raise error("table_not_interval", f"«{clean(name, 80)}» — не таблица проб или интервалов.",
                    "Статистика считается по полям таблиц проб и интервалов: "
                    f"{_listing(t[1] for t in tabs if t[2] in ('assay', 'interval', 'event'))}.")  # fmt: skip
    try:
        t, infos = rc.table_of(project, set_id, name if isinstance(name, str) and name.strip() else None)
    except ApiError as exc:
        if exc.code == "table_not_found":
            raise error("table_not_found", f"В наборе нет таблицы «{clean(name or '', 80)}».",
                        f"Таблицы набора: {_listing(x[1] for x in tabs)}.", 404) from exc  # fmt: skip
        raise
    f = _fields(infos, params.get("field"), ("number",), "поле", t.name)
    settings: dict[str, Any] = {"set_id": set_id, "table": t.name, "field": f.name}
    dom = params.get("domain")
    if dom not in (None, ""):
        settings["domain"] = _fields(infos, dom, ("text",), "поле домена", t.name).name
    tick(ctx)
    try:
        res = routes_more.domains_of(project, rc.checked("domains", settings))
        tick(ctx)  # a call that outlived its time ends here, before the answer is built
    except StatsError as exc:
        if exc.code != "no_values":
            raise error(exc.code, exc.message, exc.hint) from exc
        cols = source.read(project, set_id, t.id, [f.name])
        return {"table": clean(t.name), "field": clean(f.name), "unit": f.unit, "n": 0, "missing": cols.n,
                "rows": cols.n, "message": f"В поле «{clean(f.name, 80)}» нет значений: нечего считать."}  # fmt: skip
    counts = res["counts"]
    out: dict[str, Any] = {
        "table": clean(t.name),
        "field": clean(f.name),
        "unit": f.unit,
        "weight": "длина интервала" if res["weighted"] else "без веса",
        "rows": int(counts["rows"]),
        "missing": int(counts["missing"]),
        **_stat_row(res["total"]),
    }
    if counts.get("bad_weight"):
        out["no_length"] = int(counts["bad_weight"])  # a value but no usable length: not in n
    if res.get("domain"):
        rows = res["rows"]
        out["domain_field"] = clean(res["domain"], 80)
        out["domains_total"] = len(rows)
        out["domains"] = [_stat_row(r, "(без домена)" if r["label"] is None else clean(r["label"], 80))
                          for r in rows[:MAX_DOMAINS]]  # fmt: skip
    return out


# --- registration -----------------------------------------------------------------------------------------------

SCHEMAS: dict[str, dict[str, Any]] = {
    "data_table": {
        "type": "object",
        "properties": {
            "set_id": {"type": "integer", "description": "Набор скважин; без него — активный."},
            "table": {"type": "string", "description": "Имя таблицы: «Устья», «Инклинометрия» или таблица набора."},
            "hole_id": {"type": "string", "description": "Имя скважины: только её строки."},
            "columns": {"type": "string", "description": "Имена столбцов через запятую; без них — первые 24."},
            "offset": {"type": "integer", "description": "Сколько строк пропустить."},
            "limit": {"type": "integer", "description": "Строк на странице, до 200."},
        },
        "required": ["table"],
        "additionalProperties": False,
    },
    "data_stats": {
        "type": "object",
        "properties": {
            "set_id": {"type": "integer", "description": "Набор скважин; без него — активный."},
            "table": {"type": "string", "description": "Таблица проб или интервалов."},
            "field": {"type": "string", "description": "Числовое поле (компонент)."},
            "domain": {"type": "string", "description": "Текстовое поле той же таблицы: статистика по доменам."},
        },
        "required": ["table", "field"],
        "additionalProperties": False,
    },
}
FUNCTIONS: dict[str, Callable[[Any, dict[str, Any]], dict[str, Any]]] = {
    "data_table": data_table,
    "data_stats": data_stats,
}

from .data_tools import register  # noqa: E402  (the common call of 23.7 imports this module after `register`)

TITLES = {"data_table": "Таблица набора", "data_stats": "Статистика компонента"}
DESCRIPTIONS = {
    "data_table": "Страница таблицы набора скважин («Устья», «Инклинометрия», таблица проб или интервалов); "
    "пропуск — null, не 0.",
    "data_stats": "Статистика числового поля таблицы проб или интервалов (n, пропуски, среднее, квантили, CV), "
    "при необходимости по доменам; как на вкладке «Статистика».",
}

for _name, _fn in FUNCTIONS.items():
    register(_name, SCHEMAS[_name], _fn, title=TITLES[_name], description=DESCRIPTIONS[_name])
