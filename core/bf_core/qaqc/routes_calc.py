"""/api/v1/qaqc — the sections of the tab and the cards of records (ADR 0020 §6, §10; подшаг 14.12).

  * `POST /calc/{section}` {data_id | report_id, element, filter?, group?, kind?, offset?, limit?, max_points?,
    samples?, c?, intervals?} → the answer of `calc.run` (the head {element, unit, counts, limits, notes} and the
    keys of the section); a section is a name of `calc.SECTIONS`. With `group` (for `duplicates` always) the
    points are thinned to `max_points` (100 000 by default: every warning and failure stays, `thinned {shown,
    total}`); the points of a group of `external` go without the bootstrap (`intervals: false` unless given);
    `c` — up to 20 contents > 0 for the precision P(c) of Thompson–Howarth. With `report_id` the records, the
    settings, the reference and the filter are those of the snapshot of the report (`report.snapshot`); `filter`
    is then ignored. Errors: 404 `unknown_section`, `qaqc_data_not_found`, `qaqc_report_not_found`; 422 `bad_param
    {param}`, `field_not_found {param: element}` and the other refusals of the kernels (`QaqcError`).
  * `POST /records` {data_id | report_id, rows[] | batch + lab} → {records [{row, sample_id, hole, type_code,
    type, parent_id, crm_id, lab, batch, date, seq, parent_row, prev_row, parent_lab, values {E: {v, q, p, b,
    dl?}}, extra {name: value}}], total, more}: every field of the chosen records (≤ 1000 rows; a batch — ≤ 5000,
    by (SEQ, row)); `date` — ms since 1970, a missing value — null.

The calculation runs in the thread pool of the API under the semaphore of the heavy calculations of Э13
(`stats/routes_charts.SLOTS`, ≤ 2); the columns come through `records.CACHE`. numpy loads on the first request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]
MAX_POINTS = 100_000
MAX_ROWS = 1000
MAX_BATCH = 5000
PARAMS = ("group", "kind", "offset", "limit", "max_points", "samples", "c", "intervals")


def qaqc_error(e: Any) -> ApiError:
    """`limits.QaqcError` → the API error: 404 for an unknown section, else 422; `param` in the body."""
    status = 404 if e.code == "unknown_section" else 422
    return ApiError(status, e.code, e.message, e.hint, extra={"param": e.param} if e.param else None)


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из вкладки QAQC.")
    return raw


@dataclass
class Inputs:
    recs: Any
    config: dict[str, Any]
    crm: list[dict[str, Any]]
    filter: Any


def _id(body: dict[str, Any], key: str) -> int | None:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(422, "bad_param", "Неверный номер данных или отчёта.", "Выберите данные QAQC заново.",
                       extra={"param": key})  # fmt: skip
    return value


def inputs(project: Any, body: dict[str, Any]) -> Inputs:
    """The records and what they are judged by: of a data object (the current settings, the reference and the
    filter of the body), or of a report (its snapshot)."""
    from . import config as cf
    from . import service as sv

    data_id, report_id = _id(body, "data_id"), _id(body, "report_id")
    if (data_id is None) == (report_id is None):
        raise ApiError(422, "bad_param", "Нужны данные QAQC или отчёт.", "Выберите данные QAQC во вкладке.",
                       extra={"param": "data_id"})  # fmt: skip
    if report_id is not None:
        from . import report as rp

        snap = rp.snapshot(project, report_id)
        return Inputs(snap.recs, snap.config, snap.crm, snap.filter)
    with dialect.read_tx(project.engine) as conn:
        config, crm = cf.get_config(conn), cf.get_crm(conn)["rows"]
    assert data_id is not None
    return Inputs(sv.open_records(project, data_id), config, crm, body.get("filter"))


def compute(project: Any, section: str, body: dict[str, Any]) -> dict[str, Any]:
    """The answer of `POST /calc/{section}` (also what a benchmark calls)."""
    from bf_core.stats.routes_charts import SLOTS

    from . import calc
    from .limits import QaqcError

    if section not in calc.SECTIONS:
        raise qaqc_error(QaqcError("unknown_section", f"Нет раздела QAQC «{section}».",
                                   f"Разделы: {', '.join(calc.SECTIONS)}.", param="section"))  # fmt: skip
    got = inputs(project, body)
    params = {k: body[k] for k in PARAMS if body.get(k) is not None}
    if "group" in params or section == "duplicates":
        params.setdefault("max_points", MAX_POINTS)
    if section == "external" and "group" in params:
        params.setdefault("intervals", False)  # the points of a group: the bootstrap is in the groups already
    with SLOTS:
        try:
            element = body.get("element")
            el = got.recs.schema.element(element) if isinstance(element, str) else None
            if el is not None:
                got.recs.load(calc.columns_of(got.recs.schema, el.name))
            return calc.run(calc.context(got.recs, got.config, got.crm, element, got.filter), section, **params)
        except QaqcError as e:
            raise qaqc_error(e) from e


@router.post("/calc/{section}")
def calc_section(svc: Svc, section: str, body: Payload) -> dict[str, Any]:
    from . import records as r

    r.watch(svc)
    return compute(svc.require_current(), section, _body(body))


# --- records -------------------------------------------------------------------------------------------------------


def _pick(recs: Any, body: dict[str, Any]) -> tuple[Any, int]:
    """(row positions, total): of `rows`, or of the batch `batch` + `lab` by (SEQ, row)."""
    import numpy as np

    from . import records as r
    from .filters import name_key

    rows = body.get("rows")
    if rows is not None:
        if (
            not isinstance(rows, list)
            or len(rows) > MAX_ROWS
            or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x < recs.n for x in rows)
        ):
            raise ApiError(422, "bad_param", f"Номера строк — до {MAX_ROWS} целых в пределах данных.",
                           "Выберите пробы заново.", extra={"param": "rows"})  # fmt: skip
        at = np.searchsorted(recs.row, np.asarray(rows, dtype=np.int64))
        ok = (at < recs.n) & (recs.row[np.minimum(at, recs.n - 1)] == np.asarray(rows)) if recs.n else at < 0
        return at[ok], len(rows)
    batch, lab = body.get("batch"), body.get("lab")
    if not isinstance(batch, str) or not batch.strip():
        raise ApiError(422, "bad_param", "Нужны номера строк или партия.", "Выберите пробы или партию.",
                       extra={"param": "rows"})  # fmt: skip

    def match(text: r.Text, want: Any) -> Any:
        key = name_key(want) if isinstance(want, str) and want.strip() else None
        hits = [i for i, s in enumerate(text.labels) if name_key(s) == key]
        return np.isin(text.codes, hits) if key is not None else text.codes < 0

    mask = match(recs.text(r.BATCH), batch) & match(recs.text(r.LAB), lab)
    at = np.flatnonzero(mask)
    at = at[np.lexsort((recs.row[at], recs.number(r.SEQ)[at]))]
    return at[:MAX_BATCH], int(at.shape[0])


def _value(x: Any) -> Any:
    return None if isinstance(x, float) and x != x else x


def records_of(recs: Any, config: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Every field of the chosen records; of a file only its row groups that hold them (`records.read_rows`)."""
    import numpy as np

    from . import records as r
    from .config import type_of

    at, total = _pick(recs, body)
    if recs.path is not None:
        rows = np.unique(recs.row[at])
        recs = r.read_rows(recs.path, rows).take(np.searchsorted(rows, recs.row[at]))
        at = np.arange(recs.n)
    sch = recs.schema
    cols: dict[str, list[Any]] = {"row": recs.row[at].tolist()}
    for name in (r.SAMPLE_ID, r.PARENT_ID):
        cols[name] = recs.ids(name).take(at).to_pylist()
    for name in r.TEXTS:
        cols[name] = recs.text(name).take(at).values()
    cols[r.DATE] = [None if v != v else int(v) for v in recs.date()[at].tolist()]
    cols[r.SEQ] = [_value(v) for v in recs.number(r.SEQ)[at].tolist()]
    for name in (r.PARENT_ROW, r.PREV_ROW):
        cols[name] = [None if v < 0 else v for v in recs.index(name)[at].tolist()]
    values: dict[str, dict[str, list[Any]]] = {}
    for e in sch.elements:
        part = {"v": recs.number(r.v(e.name)), "q": recs.flags(r.q(e.name)), "p": recs.number(r.p(e.name)),
                "b": recs.number(r.b(e.name))}  # fmt: skip
        if e.has_dl:
            part["dl"] = recs.number(r.dl(e.name))
        values[e.name] = {k: [_value(v) for v in a[at].tolist()] for k, a in part.items()}
    extra = {}
    for f in sch.extra:
        col = recs.column(r.x(f.name))
        extra[f.name] = col.take(at).values() if f.kind == r.TEXT else [_value(v) for v in col[at].tolist()]
    out = []
    for i in range(len(cols["row"])):
        rec = {k: v[i] for k, v in cols.items()}
        rec["type"] = type_of(config, rec[r.TYPE_CODE])
        rec["values"] = {e: {k: v[i] for k, v in part.items()} for e, part in values.items()}
        rec["extra"] = {name: v[i] for name, v in extra.items()}
        out.append(rec)
    return {"records": out, "total": total, "more": total > len(out)}


@router.post("/records")
def records(svc: Svc, body: Payload) -> dict[str, Any]:
    from bf_core.stats.routes_charts import SLOTS

    from . import records as r

    r.watch(svc)
    raw = _body(body)
    got = inputs(svc.require_current(), raw)
    with SLOTS:
        return records_of(got.recs, got.config, raw)
