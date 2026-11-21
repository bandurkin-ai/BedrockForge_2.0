"""/api/v1/stats — the charts of ТЗ 7.25–7.27 and the calculation of a saved chart (ADR 0019 §10).

    POST /stats/histogram         body — the settings of a histogram (`charts.settings`; `view` is not read)
        → {field, set_id, log, weighted, counts, stats, stats_unweighted | null, bins, conventions}
    POST /stats/qq                body — the settings of a Q-Q chart
        → {field, set_id, y_field | null, y_set_id | null, mode, x, y, points, …} (`stats.qq.qq_chart`)
    POST /stats/dendrogram        body — the settings of a dendrogram (`fields` empty — all the components)
        → {set_id, fields, units, correlation, pairs, rows, used, …} (`stats.dendrogram.dendrogram_chart`)
    POST /stats/chart/{chart_id}  the saved chart by its settings and the current data (`chart.open`, the
        assistant) → the answer of its kind + `chart {id, kind, name, version}`

Settings (ADR 0019 §9): `set_id`, `table` (`name_key` or id; null — the sample table of the set), `field` (null —
the first component of the table), the selection of values (`stats.select.SampleFilter`), `holes` (names; empty —
all), then the keys of the kind. They are untrusted input: `charts.kinds.check` (version, size, lists, ids) first,
then the kernels check their own keys. The data are read anew every time (`stats.source`: an edit raises `rev`,
the cached columns miss, the chart is recomputed — Т7.28-1). Errors `{code, message, hint, param?}`: 422
`bad_param` {param}, `field_not_found` / `field_not_number` {param}, `no_values`, `no_positive`, `too_many_bins`,
`too_few_values`, `no_spread`; 404 `set_not_found`, `table_not_found`, `chart_not_found`.

Where it runs (ADR 0019 §3, решение 5): synchronously in the API process, in the thread pool of the server;
at most HEAVY (2) calculations at a time — the others wait for a free slot. numpy and the kernels load on the
first request, not with this module (tests/test_process.py)."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from bf_core.api.errors import ApiError
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from .route_args import bad, holes_of, name_of, set_id_of, stats_error

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Settings = Annotated[dict[str, Any], Body()]

HEAVY = 2
SLOTS = threading.BoundedSemaphore(HEAVY)
FIELD_WEIGHTS = ("field", "length_field")

#: kind → calculation(project, settings) of the charts this module answers for; `routes_more` (13.16) adds its own
CALCULATORS: dict[str, Callable[[Any, dict[str, Any]], dict[str, Any]]] = {}


def register(kind: str) -> Callable[[Callable[[Any, dict[str, Any]], dict[str, Any]]], Any]:
    def put(fn: Callable[[Any, dict[str, Any]], dict[str, Any]]) -> Any:
        CALCULATORS[kind] = fn
        return fn

    return put


# --- errors, the settings -----------------------------------------------------------------------------------


def checked(kind: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    """The settings of a calculation without `view`, checked as the stored ones are (`v` may be left out)."""
    from bf_core.charts import kinds

    body = {"v": kinds.VERSION, **{k: v for k, v in settings.items() if k != "view"}}
    try:
        kinds.check(kind, body)
    except kinds.KindError as e:
        code = e.code if e.code != "bad_chart" else "bad_param"
        raise ApiError(422, code, str(e), "Проверьте настройки графика.", extra={"param": e.param or "settings"}) from e
    return body


# --- the data of a side ---------------------------------------------------------------------------------------


def table_of(project: Any, set_id: int, table: str | None) -> tuple[Any, list[Any]]:
    """The table of the set (`name_key` or id; None — the sample table) and its fields (`source.fields_of`)."""
    from bf_core.db import dialect

    from . import source

    with dialect.read_tx(project.engine) as conn:
        t = source.find_table(conn, set_id, table)
        return t, source.fields_of(conn, t)


def field_of(t: Any, infos: list[Any], name: str, param: str, kinds: tuple[str, ...] = ("number",)) -> Any:
    """The field `name` (case aside) of the table: 422 `field_not_found` / `field_not_number` /
    `field_not_text` {param} when it is not there or not of `kinds`."""
    f = next((f for f in infos if f.name.casefold() == name.casefold()), None)
    if f is None:
        raise ApiError(422, "field_not_found", f"В таблице «{t.name}» нет поля «{name[:80]}».",
                       "Выберите поле из списка колонок.", extra={"param": param})  # fmt: skip
    if f.kind not in kinds:
        if f.kind == "number":
            raise ApiError(422, "field_not_text", f"Поле «{f.name}» — числовое.", "Выберите текстовое поле.",
                           extra={"param": param})  # fmt: skip
        raise ApiError(422, "field_not_number", f"Поле «{f.name}» — текстовое.", "Выберите числовое поле.",
                       extra={"param": param})  # fmt: skip
    return f


def first_number(t: Any, infos: list[Any], param: str, skip: tuple[str, ...] = ()) -> Any:
    """The field a setting without a name takes: the first component of the table, else its first numeric
    field but `length`; the names `skip` (case aside) are passed over."""
    from . import source

    taken = {s.casefold() for s in skip}
    numbers = [f for f in infos if f.kind == "number" and f.name.casefold() not in taken]
    first = next((f for f in numbers if f.role == "component"), None) or next(
        (f for f in numbers if f.role != source.LENGTH), None
    )
    if first is None:
        more = " других" if skip else ""
        raise bad(param, f"В таблице «{t.name}» нет{more} числовых полей.", "Выберите другую таблицу набора.")
    return first


def named_fields(
    project: Any, set_id: int, table: str | None, wanted: dict[str, str | None]
) -> tuple[Any, dict[str, str]]:
    """The table and the stored names of the fields `wanted` {param: name}; the param `field` (or `y_field`)
    without a name takes the first component of the table (else its first numeric field but `length`)."""
    t, infos = table_of(project, set_id, table)
    out: dict[str, str] = {}
    for param, name in wanted.items():
        if name is None:
            if param in ("field", "y_field"):
                out[param] = first_number(t, infos, param).name
            continue
        out[param] = field_of(t, infos, name, param).name
    return t, out


def only_holes(cols: Any, holes: list[str]) -> Any:
    """The columns of the holes `holes` (names, case aside; unknown names do not matter) — all of them when
    the list is empty. The rows keep their line order; `row_ids`, the sample key and the domains follow."""
    import numpy as np

    from . import source

    if not holes:
        return cols
    wanted = {h.casefold() for h in holes}
    chosen = np.array([h.casefold() in wanted for h in cols.holes], dtype=bool)
    idx = np.flatnonzero(chosen[cols.hole_of()]) if cols.holes else np.zeros(0, dtype=np.int64)
    sizes = np.diff(cols.starts)[chosen]
    return source.Columns(
        cols.table, cols.row_ids[idx], [h for h, c in zip(cols.holes, chosen, strict=True) if c],
        cols.hole_db_ids[chosen], np.concatenate(([0], np.cumsum(sizes))).astype(np.int64), cols.from_m[idx],
        cols.to_m[idx], cols.skipped, {k: v[idx] for k, v in cols.numbers.items()},
        {k: v[idx] for k, v in cols.codes.items()}, dict(cols.labels), dict(cols.infos),
    )  # fmt: skip


def side(
    project: Any, set_id: int, table: str | None, field: str | None, flt: Any, holes: list[str], *, y: bool = False
) -> tuple[str, Any]:
    """(the stored name of the field, `select.Selection`) of one side of a chart: the field of the table of the
    set through the selection `flt` (weights from the same table), only the rows of `holes` when given."""
    from . import select, source

    p = "y_" if y else ""
    weight_field = flt.weight_field if flt.weight in FIELD_WEIGHTS else None
    t, names = named_fields(project, set_id, table, {f"{p}field": field, "weight_field": weight_field})
    name = names[f"{p}field"]
    read = [name, source.LENGTH]
    if weight_field is not None:
        read.append(names["weight_field"])
    cols = only_holes(source.read(project, set_id, t.id, read), holes)
    return name, select.select_sample(**cols.select_args(name, names.get("weight_field")), flt=flt)


def _safe(x: Any) -> Any:
    """Nothing undefined leaves as a number: NaN and ±inf become null (JSON has neither; never 0)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _safe(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_safe(v) for v in x]
    return x


def run(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """`fn` in one of the HEAVY slots; the refusals of the kernels become 422 with their code and `param`."""
    from .weighted import StatsError

    with SLOTS:
        try:
            return _safe(fn())  # type: ignore[no-any-return]
        except StatsError as e:
            raise stats_error(e) from e


# --- the charts -----------------------------------------------------------------------------------------------


@register("histogram")
def histogram_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    from .histogram import HistogramSpec, histogram_chart
    from .select import SampleFilter

    flt, spec = SampleFilter.from_settings(settings), HistogramSpec.from_settings(settings)
    set_id = set_id_of(settings)
    field, sel = side(project, set_id, name_of(settings, "table"), name_of(settings, "field"), flt,
                      holes_of(settings, "holes"))  # fmt: skip
    return {"field": field, "set_id": set_id, **histogram_chart(sel, spec)}


@register("qq")
def qq_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    from .qq import QUANTILES, check_distribution, check_quantiles, qq_chart
    from .select import SampleFilter

    flt = SampleFilter.from_settings(settings)
    set_id = set_id_of(settings)
    distribution = settings.get("distribution") or "lognormal"
    if distribution != "sample":
        check_distribution(distribution)
    field, x = side(project, set_id, name_of(settings, "table"), name_of(settings, "field"), flt,
                    holes_of(settings, "holes"))  # fmt: skip
    if distribution != "sample":
        return {"field": field, "set_id": set_id, "y_field": None, "y_set_id": None,
                **qq_chart(x, distribution=distribution)}  # fmt: skip
    quantiles = check_quantiles(QUANTILES if settings.get("quantiles") is None else settings["quantiles"])
    y_field = name_of(settings, "y_field")
    if y_field is None:
        raise bad("y_field", "Не выбрано поле второй выборки.", "Выберите набор и поле стороны Y.")
    y_set = set_id if settings.get("y_set_id") is None else set_id_of(settings, "y_set_id")
    y_name, y = side(project, y_set, name_of(settings, "y_table"), y_field, flt, holes_of(settings, "y_holes"), y=True)
    out = qq_chart(x, distribution="sample", y=y, quantiles=quantiles)
    return {"field": field, "set_id": set_id, "y_field": y_name, "y_set_id": y_set, **out}


@register("dendrogram")
def dendrogram_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    from . import source
    from .correlation import CorrSpec
    from .dendrogram import TreeSpec, dendrogram_chart
    from .select import SampleFilter, sample_weights

    corr, tree = CorrSpec.from_settings(settings), TreeSpec.from_settings(settings)
    flt = SampleFilter.from_settings({k: settings.get(k) for k in ("weight", "weight_field")})
    set_id = set_id_of(settings)
    t, infos = table_of(project, set_id, name_of(settings, "table"))
    given = settings.get("fields") or []  # `kinds.check` made it a list of names
    if given:
        chosen = [field_of(t, infos, name, "fields") for name in given]
    else:  # all the components; a table without them — its numeric fields but `length`
        numbers = [f for f in infos if f.kind == "number"]
        chosen = [f for f in numbers if f.role == "component"] or [f for f in numbers if f.role != source.LENGTH]
    weight_field = field_of(t, infos, flt.weight_field, "weight_field").name if (
        flt.weight in FIELD_WEIGHTS and flt.weight_field is not None) else None  # fmt: skip
    read = [f.name for f in chosen] + [source.LENGTH] + ([weight_field] if weight_field else [])
    cols = only_holes(source.read(project, set_id, t.id, read), holes_of(settings, "holes"))
    weights = sample_weights(flt.weight, cols.number(source.LENGTH),
                             None if weight_field is None else cols.number(weight_field))  # fmt: skip
    out = dendrogram_chart([f.name for f in chosen], [cols.number(f.name) for f in chosen], weights, corr, tree,
                           units=[f.unit for f in chosen])  # fmt: skip
    return {"set_id": set_id, **out}


@router.post("/histogram")
def post_histogram(svc: Svc, body: Settings) -> dict[str, Any]:
    project = svc.require_current()
    settings = checked("histogram", body)
    return run(lambda: histogram_of(project, settings))


@router.post("/qq")
def post_qq(svc: Svc, body: Settings) -> dict[str, Any]:
    project = svc.require_current()
    settings = checked("qq", body)
    return run(lambda: qq_of(project, settings))


@router.post("/dendrogram")
def post_dendrogram(svc: Svc, body: Settings) -> dict[str, Any]:
    project = svc.require_current()
    settings = checked("dendrogram", body)
    return run(lambda: dendrogram_of(project, settings))


@router.post("/chart/{chart_id}")
def post_saved(svc: Svc, chart_id: int) -> JSONResponse:
    """The saved chart by its settings and the current data; the set is the one the chart lies in (a `.bfggis`
    import writes it into the settings too, `archive/chart_remap.py`; the parent wins all the same)."""
    from bf_core.charts import service
    from bf_core.db import dialect

    project = svc.require_current()
    try:
        with dialect.read_tx(project.engine) as conn:
            chart = service.get(conn, chart_id)
    except service.NotFound as e:
        raise ApiError(404, "chart_not_found", "График не найден.", "Обновите дерево проекта.") from e
    calc = CALCULATORS.get(chart["kind"])
    if calc is None:
        raise ApiError(422, "chart_not_computed", f"График вида «{chart['kind']}» так не рассчитывается.",
                       "Откройте его из дерева проекта.", extra={"param": "kind"})  # fmt: skip
    settings = checked(chart["kind"], chart["settings"] if isinstance(chart["settings"], dict) else {})
    if chart["set_id"] is not None:
        settings["set_id"] = chart["set_id"]
    head = {"id": chart["id"], "kind": chart["kind"], "name": chart["name"], "version": chart["version"]}
    return JSONResponse({**run(lambda: calc(project, settings)), "chart": head})  # big answers skip the encoder
