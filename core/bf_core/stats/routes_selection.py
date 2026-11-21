"""/api/v1/stats/selection — the selection of samples of the charts (ТЗ 8.3, № 237; ADR 0019 §8, §10).

    POST   /stats/selection                   one of the four bodies below → {token, set_id, table_id, table_name,
                                              rev, n, unknown, source}
        {set_id, table, rows: [dh_rows.id, …]}                       the points of a chart (≤ 200 000)
        {chart: {kind: "scatter", …settings}, polygon: [[x, y], …] | rect: [x0, x1, y0, y1]}
                                              the density of a scatter: the core picks the pairs (data units)
        {chart: {kind: "box" | "probplot", …settings}, range: [lo, hi], groups: [label | null, …] | null}
                                              the samples of the groups (null — the rows without a domain, or
                                              all of a chart without domains; null / [] — every group) whose
                                              value after the selection of values lies in [lo; hi]
        {set_id, table, filter: {where: str | null, hole: dh_holes.id | null}}
                                              «и наоборот»: the rows of the filter of the «Таблица» panel
    GET    /stats/selection/{token}            → {…the same, rows: [dh_rows.id ascending]}
    GET    /stats/selection/{token}/segments?method=   BFA1 `selection_segments` (`stats.selection`)
    DELETE /stats/selection/{token}            → {deleted: bool}

`table` — `name_key` or id (null — the sample table of the set); the table of a chart — that of its X (its
field). Errors `{code, message, hint, param?}`: 422 `bad_param` {param}, `too_many_rows` {param} (> 200 000),
the errors of the chart settings (`routes_charts`), `bad_expression`, `bad_method`; 404 `selection_not_found`,
`set_not_found`, `table_not_found`; 409 `selection_stale` {token} — the table was edited or replaced after the
selection ({token: null} — while it was being made: the rows read are not those of one revision). Synchronous,
in a HEAVY slot of `routes_charts`; numpy loads on the first request."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

from bf_core.api.errors import ApiError

from . import routes_charts as rc
from .routes_charts import Settings, Svc

router = APIRouter()
KINDS = ("scatter", "box", "probplot")
BODY_KEYS = ("rows", "chart", "filter")


def _bad(param: str, message: str, hint: str) -> ApiError:
    return rc._bad(param, message, hint)


def _changed() -> ApiError:
    """409 `selection_stale` {token: null}: the table changed while the selection was being made."""
    return ApiError(409, "selection_stale", "Таблица изменилась, пока создавалось выделение.",
                    "Выделите пробы на графике или в таблице заново.", extra={"token": None})  # fmt: skip


def _numbers(v: Any, k: int, param: str, message: str, hint: str) -> list[float]:
    import math

    if not isinstance(v, list | tuple) or len(v) != k:
        raise _bad(param, message, hint)
    out = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(float(x)):
            raise _bad(param, message, hint)
        out.append(float(x))
    return out


def _table(project: Any, body: dict[str, Any]) -> Any:
    from bf_core.db import dialect

    from . import source

    set_id = rc._set_id(body)
    table = body.get("table")
    if table is not None and not isinstance(table, str | int) or isinstance(table, bool):
        raise _bad("table", "Таблица задаётся названием или номером.", "Выберите таблицу набора.")
    with dialect.read_tx(project.engine) as conn:
        return source.find_table(conn, set_id, None if table in (None, "") else table)


def _from_rows(project: Any, body: dict[str, Any]) -> Any:
    import numpy as np

    from . import selection

    raw = body["rows"]
    if not isinstance(raw, list) or not all(isinstance(r, int) and not isinstance(r, bool) for r in raw):
        raise _bad("rows", "Строки выделения — список номеров строк таблицы.", "Выделите пробы на графике заново.")
    if len(raw) > selection.MAX_ROWS:
        raise selection.too_many(len(raw), "rows")
    t = _table(project, body)
    ids, unknown = selection.known(project, t, np.asarray(raw, dtype=np.int64) if raw else np.zeros(0, np.int64))
    return selection.make(project, t, ids, source="rows", unknown=unknown)


def _scatter(project: Any, settings: dict[str, Any], body: dict[str, Any]) -> Any:
    from . import selection
    from .routes_more import scatter_inputs
    from .scatter import pairs, rows_in_polygon, rows_in_rect

    has_poly, has_rect = body.get("polygon") is not None, body.get("rect") is not None
    if has_poly == has_rect:
        raise _bad("polygon", "Для рассеяния нужен контур (polygon) или рамка (rect) — что-то одно.",
                   "Обведите область на графике заново.")  # fmt: skip
    d = scatter_inputs(project, settings)
    p = pairs(d["xs"], d["ys"], d["w"], log_x=d["log_x"], log_y=d["log_y"])
    if has_poly:
        rows = rows_in_polygon(p, body["polygon"], threads=2)
    else:
        x0, x1, y0, y1 = _numbers(body["rect"], 4, "rect", "Рамка — четыре числа: X от и до, Y от и до.",
                                  "Выделите область на графике заново.")  # fmt: skip
        rows = rows_in_rect(p, x0, x1, y0, y1)
    cols = d["cols"]
    return selection.make(project, cols.table, cols.row_ids[rows], source="scatter",
                          param="polygon" if has_poly else "rect")  # fmt: skip


def _groups(raw: Any) -> list[str | None] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) > 1000 or not all(g is None or isinstance(g, str) for g in raw):
        raise _bad("groups", "Группы выделения — список доменов (null — без домена).", "Выделите область заново.")
    return list(raw) or None


def _by_value(project: Any, kind: str, settings: dict[str, Any], body: dict[str, Any]) -> Any:
    """The samples of the chosen groups of a box plot or a log-probability plot with a value in the range."""
    import numpy as np

    from . import selection
    from .routes_more import _by_domain

    lo, hi = sorted(_numbers(body.get("range"), 2, "range", "Диапазон выделения — два числа: от и до.",
                             "Выделите область на графике заново."))  # fmt: skip
    groups = _groups(body.get("groups"))
    d = _by_domain(project, settings)
    sel, codes, labels, cols = d["sel"], d["codes"], d["labels"], d["cols"]
    inside = (sel.values >= lo) & (sel.values <= hi)
    if kind == "box" and settings.get("log") or kind == "probplot":
        inside &= sel.values > 0.0  # a log axis does not draw them
    if groups is not None:
        if codes is None:
            ok = np.full(sel.n, None in groups)
        else:
            c = np.asarray(codes)[sel.rows]
            names = list(labels or [])
            wanted = np.zeros(len(names) + 1, dtype=bool)
            for g in groups:
                if g is None:
                    wanted[len(names)] = True
                elif g in names:
                    wanted[names.index(g)] = True
            c = np.where((c < 0) | (c >= len(names)), len(names), c)
            ok = wanted[c]
        inside &= ok
    return selection.make(project, cols.table, cols.row_ids[sel.rows[inside]], source=kind, param="range")


def _from_chart(project: Any, body: dict[str, Any]) -> Any:
    chart = body["chart"]
    if not isinstance(chart, dict) or chart.get("kind") not in KINDS:
        raise _bad("chart", "Выделение по ядру — у рассеяния, ящиков с усами и лог-вероятностного графика.",
                   "Передайте настройки графика с kind: scatter, box или probplot.")  # fmt: skip
    kind = str(chart["kind"])
    settings = rc.checked(kind, {k: v for k, v in chart.items() if k != "kind"})
    if kind == "scatter":
        return _scatter(project, settings, body)
    return _by_value(project, kind, settings, body)


def _from_filter(project: Any, body: dict[str, Any]) -> Any:
    import numpy as np

    from bf_core.drillholes.expr import ExprError
    from bf_core.drillholes.query import mask
    from bf_core.drillholes.router import _expr_error, _snapshot

    from . import selection

    flt = body["filter"]
    if not isinstance(flt, dict):
        raise _bad("filter", "Фильтр таблицы — {where, hole}.", "Задайте фильтр в панели «Таблица».")
    where, hole = flt.get("where"), flt.get("hole")
    if where is not None and (not isinstance(where, str) or len(where) > 2000):
        raise _bad("where", "Фильтр — выражение не длиннее 2000 знаков.", "Исправьте фильтр в панели «Таблица».")
    if hole is not None and (not isinstance(hole, int) or isinstance(hole, bool)):
        raise _bad("hole", "Скважина задаётся номером.", "Выберите скважину заново.")
    t = _table(project, body)
    snap = _snapshot(project, t.set_id, str(t.id))
    if int(snap.key[-1]) != t.rev:
        raise _changed()
    try:
        m = mask(snap, hole, where or None)
    except ExprError as exc:
        raise _expr_error(exc) from exc
    n = int(np.count_nonzero(m))
    if n > selection.MAX_ROWS:
        raise selection.too_many(n, "filter")
    ids, _unknown = selection.known(project, t, snap.ids[m])  # rows without FROM or TO are not samples
    return selection.make(project, t, ids, source="table", param="filter")


@router.post("/selection")
def post_selection(svc: Svc, body: Settings) -> dict[str, Any]:
    project = svc.require_current()
    given = [k for k in BODY_KEYS if body.get(k) is not None]
    if len(given) != 1:
        raise _bad("rows", "Выделение задаётся одним способом: rows, chart или filter.",
                   "Выделите пробы на графике или в таблице заново.")  # fmt: skip
    make = {"rows": _from_rows, "chart": _from_chart, "filter": _from_filter}[given[0]]
    try:
        return rc.run(lambda: make(project, body).info())
    except ApiError as e:
        if e.code == "table_changed":  # the columns of a chart read across an edit (`stats.source`)
            raise _changed() from e
        raise


@router.get("/selection/{token}")
def selection_rows(svc: Svc, token: str) -> dict[str, Any]:
    from . import selection

    project = svc.require_current()
    sel = selection.current(project, token[:64])
    return {**sel.info(), "rows": sel.ids.tolist()}


@router.get("/selection/{token}/segments")
def selection_segments(
    svc: Svc, token: str, method: Annotated[str, Query(max_length=40)] = "minimum_curvature"
) -> Response:
    from bf_core.drillholes.desurvey import METHODS
    from bf_core.viz.keys import bad_method

    from . import selection

    if method not in METHODS:
        raise bad_method()
    project = svc.require_current()
    sel = selection.current(project, token[:64])
    with rc._SLOTS:
        body = selection.segments(project, sel, method)
    return Response(content=body, media_type="application/octet-stream", headers={"Cache-Control": "no-cache"})


@router.delete("/selection/{token}")
def delete_selection(svc: Svc, token: str) -> dict[str, Any]:
    from . import selection

    project = svc.require_current()
    return {"deleted": selection.drop(project, token[:64])}
