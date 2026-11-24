"""/api/v1/stats — swath plots (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5, §10): the sources of their series and the
calculation of a swath by its settings or a saved one — the port of BF `routers/swath.py` (series, cuboid,
counts, notes) onto the kernels `stats.swath_frame`, `stats.swath`, `stats.swath_blocks` without SQL.

    GET  /stats/swath/sources
        → {sets: [{id, name, tables: [{key (name_key), name, kind, rows}]}],
           models: [{id, name, numbers: [{name, unit}], categories: [{name, labels (≤ 300), more}],
                     block: [dx, dy, dz] | null, density: name | null, grades: [name]}]}
    POST /stats/swath  {settings} | {swath_id}
        → {direction, frame: {azimuth, dip, roll, u, v, w}, box, limits, custom_limits, start, width, count,
           centres, block, series: [...], notes: [str], swath?: {id, name, version}}

Settings (`charts.settings` of kind `swath`, v 1; `color`, `line`, `bars`, `range`, `view` are the window's):
`direction` x | y | z | angles, `azimuth`, `dip`, `roll`, `width` | null (the default width), `anchor` | null,
`limits` {u, v, w: [lo, hi]} | null (the cuboid of the data), `series` (1 … 12):
    {kind: "blocks", model_id, field, weight: volume | tonnage | none, density, filter: {field, labels} | null,
     filter_id: int | null}
    {kind: "samples", set_id, table, field, weight: none | length | field | length_field, weight_field,
     negatives: keep | half_abs | exclude, top_cut, within: box | blocks, within_series: index | null}
`within_series` — the number (from 0) of the model series the samples are taken inside; null — the first one.
`filter_id` — a filter of the library (ТЗ 7.35 Д-02): the blocks with f > 0 take part, the rest are `filtered_out`;
a gone filter or a field the model lacks is the `error` of that series. A model with a turn, sub-blocks or FRAC is
read by `swath_sub` (its blocks are cut between the swaths in proportion to the volume; samples «внутри блоков» of
such a series are not yet: `error` `blocks_unsupported` of the samples).

A series answers {index, kind, name, field, unit, weight, weight_name, weight_unit, source_id, within? (samples:
"box" or the model series), counts, mean [n | null], count [int], weights [n | null], deviation? [n | null]
(the series after the first: (m − m₁) / m₁ · 100), total {mean, count, weight, deviation?}, error}. A series
whose source is gone (its id null, the set, table, model or field missing) carries `error {code, message,
hint}` and empty numbers — the others are computed. The cuboid is built around the model series, without them
around the samples (`swath.cuboid`). Refusals `{code, message, hint, param}`: 422 `too_many_series` (series),
`too_many_swaths` (width), `too_much_data` (series: the model columns over `REQUEST_BYTES`), `bad_param` {param},
`no_data`; 404 `chart_not_found`.

The columns of a model come from `stats.blocks_cache` (≤ 600 МБ, by the SHA-256 of its file), those of a table
from `stats.source` (≤ 512 МБ, by its `rev`): an edit of the data is a miss, the swath is recomputed (Т7.30-5).
A request holds at most `REQUEST_BYTES` of model columns (`_load`: over it the values of a series are read when
it is computed).
At most two heavy calculations at a time (`routes_charts.run`); the kernels of a model series run on `THREADS`
threads. numpy and the kernels load on the first request, not with this module (tests/test_process.py)."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from bf_core.api.errors import ApiError
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from .routes_charts import checked, register, run

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]

THREADS = max(1, min(4, os.cpu_count() or 1))  # of one model series (the groups of rows, `swath_blocks`)
LABELS = 300  # values of a category field in the sources
DENSITY_NAMES = ("density", "dens", "плотность", "sg", "rho", "ов", "объемный вес", "объёмный вес")
#: the columns of models one swath may hold at a time (ADR 0019 §3: «Статистика» ≤ 1 ГБ with the cache ≤ 600 МБ;
#: two series of 20 million blocks — value, density, domain and `idx`, 480 МБ — are read in one pass)
REQUEST_BYTES = 640 * 2**20
BLOCK_WEIGHTS = {"volume": ("объём", "м³"), "tonnage": ("тоннаж", "т"), "none": ("число блоков", "")}


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, extra={"param": param})


def _error(code: str, message: str, hint: str) -> dict[str, str]:
    return {"code": code, "message": message, "hint": hint}


def _gone(what: str) -> dict[str, str]:
    return _error("source_deleted", f"Источник ряда удалён: {what}.", "Выберите источник ряда заново.")


# --- the sources --------------------------------------------------------------------------------------------


def is_density(name: str) -> bool:
    key = str(name).casefold()
    return key in DENSITY_NAMES or key.startswith(("dens", "плотн"))


def model_source(info: dict[str, Any]) -> dict[str, Any]:
    """A block model as the series form offers it (`blocks.store.block_model_info`)."""
    from . import swath_frame as sf

    numbers = [f for f in info["fields"] if f["kind"] == "number"]
    density = next((f["name"] for f in numbers if is_density(f["name"])), None)
    grid = info.get("grid") or {}
    try:
        block: list[float] | None = [float(x) for x in sf.grid_sides(grid)]
    except (KeyError, TypeError, ValueError):
        block = None
    cats = [f for f in info["fields"] if f["kind"] == "category"]
    return {
        "id": int(info["id"]), "name": info["name"],
        "numbers": [{"name": f["name"], "unit": f.get("unit")} for f in numbers],
        "categories": [{"name": f["name"], "labels": list(f.get("labels") or [])[:LABELS],
                        "more": len(f.get("labels") or []) > LABELS} for f in cats],
        "block": block, "density": density, "grades": [f["name"] for f in numbers if f["name"] != density],
    }  # fmt: skip


def sources_of(project: Any) -> dict[str, Any]:
    import sqlalchemy as sa

    from bf_core.blocks import store
    from bf_core.db import dialect
    from bf_core.db.schema import objects

    from . import source

    with dialect.read_tx(project.engine) as conn:
        rows = conn.execute(
            sa.select(objects.c.id, objects.c.name)
            .where(objects.c.kind == "drillhole_set", objects.c.state == "ready")
            .order_by(objects.c.sort_key, objects.c.name_key, objects.c.id)
        ).all()
        sets = [
            {"id": int(r.id), "name": r.name,
             "tables": [{"key": t.name_key, "name": t.name, "kind": t.kind, "rows": t.rows}
                        for t in source.tables(conn, int(r.id))]}
            for r in rows
        ]  # fmt: skip
        models = [model_source(store.block_model_info(conn, m["id"])) for m in store.list_block_models(conn)]
    return {"sets": sets, "models": models}


@router.get("/swath/sources")
def get_sources(svc: Svc) -> dict[str, Any]:
    return sources_of(svc.require_current())


# --- the series ---------------------------------------------------------------------------------------------


@dataclass
class Series:
    """One series of the settings, read: its data, or the error that keeps it out."""

    index: int
    kind: str
    field: str | None
    weight: str
    source_id: int | None
    name: str = ""
    unit: str | None = None
    weight_name: str = ""
    weight_unit: str = ""
    error: dict[str, str] | None = None
    # blocks
    grid: dict[str, Any] = dc_field(default_factory=dict)
    cols: Any = None
    values: Any = None
    density: Any = None
    codes: Any = None
    keep: Any = None
    model: Any = None  # `blocks_cache.Model`
    density_name: str | None = None
    category: str | None = None  # the field of the filter
    labels: list[str] = dc_field(default_factory=list)
    filter_id: int | None = None  # a filter of the library
    mask: Any = None  # bool [rows]: the rows the filter passes
    warnings: list[str] = dc_field(default_factory=list)
    sub: Any = None  # `swath_sub.Source` of a model with a turn, sub-blocks or FRAC
    # samples
    sel: Any = None
    points: Any = None
    within: int | None = None  # the model series the samples are taken inside
    ext: Any = None
    result: Any = None


def _text(s: dict[str, Any], key: str, k: int) -> str | None:
    v = s.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise _bad(f"series[{k}].{key}", f"Ряд {k + 1}: «{key}» задаётся названием.", "Выберите значение из списка.")
    return v


def _blocks(project: Any, k: int, s: dict[str, Any]) -> Series:
    """The series of a model, its fields checked; the columns are read by `_load` for all of them."""
    from bf_core.db import dialect

    from . import blocks_cache as bc
    from .swath_blocks import WEIGHTS

    weight = s.get("weight") or "volume"
    if weight not in WEIGHTS:
        raise _bad(f"series[{k}].weight", f"Ряд {k + 1}: вес блоков «{str(weight)[:40]}» неизвестен.",
                   "Допустимо: volume — объём, tonnage — тоннаж, none — без веса.")  # fmt: skip
    name, density = _text(s, "field", k), _text(s, "density", k)
    if weight == "tonnage" and density is None:
        raise _bad(f"series[{k}].density", f"Ряд {k + 1}: для веса по тоннажу не задано поле плотности.",
                   "Выберите поле плотности модели или вес «объём».")  # fmt: skip
    flt = s.get("filter")
    if flt is not None and not (isinstance(flt, dict) and isinstance(flt.get("labels", []), list)):
        raise _bad(f"series[{k}].filter", f"Ряд {k + 1}: отбор — поле и список значений.", "Задайте отбор заново.")
    fid = s.get("filter_id")
    if fid is not None and (isinstance(fid, bool) or not isinstance(fid, int)):
        raise _bad(f"series[{k}].filter_id", f"Ряд {k + 1}: фильтр задаётся номером.", "Выберите фильтр из библиотеки.")
    out = Series(k, "blocks", name, weight, s.get("model_id"))
    out.filter_id = fid
    out.weight_name, out.weight_unit = BLOCK_WEIGHTS[weight]
    out.name = f"Модель · {name or '—'}"
    if out.source_id is None:
        out.error = _gone("блочная модель")
        return out
    try:
        with dialect.read_tx(project.engine) as conn:
            m = bc.model_of(conn, int(out.source_id))
    except ApiError as e:
        out.error = _error("source_deleted" if e.status == 404 else e.code, e.message, e.hint)
        return out
    out.name, out.grid = f"{m.name} · {name or '—'}", m.grid

    def own(n: str | None, kind: str, what: str) -> str | None:
        f = m.field(n) if n is not None else None
        if f is None or f["kind"] != kind:
            sort = "числового" if kind == "number" else "категориального"
            message = f"В модели «{m.name}» нет {sort} поля «{str(n or '')[:80]}» ({what})."
            out.error = _error("field_not_found", message, "Выберите поле модели заново.")
            return None
        return str(f["name"])

    if name is None:
        name = next((f["name"] for f in m.fields if f["kind"] == "number" and not is_density(f["name"])), None)
    field_name = own(name, "number", "значение")
    dens = own(density, "number", "плотность") if weight == "tonnage" and out.error is None else None
    labels = [str(x) for x in (flt or {}).get("labels") or []]
    cat = own(_text(flt or {}, "field", k), "category", "отбор") if labels and out.error is None else None
    if out.error is not None or field_name is None:
        return out
    out.field = field_name
    out.name = f"{m.name} · {field_name}"
    out.unit = (m.field(field_name) or {}).get("unit")
    out.model, out.density_name, out.category, out.labels = m, dens, cat, labels
    if not m.plain:
        from . import swath_sub as ss

        try:
            out.sub = ss.open_source(project, m)
        except ApiError as e:
            out.error = _error(e.code, e.message, e.hint)
    return out


def _bytes(m: Any, names: list[str], idx: bool = True) -> int:
    """What the columns `names` (and `idx`) of the model take in the memory."""
    from . import blocks_cache as bc

    per = sum(8 if (m.field(n) or {}).get("kind") == "number" else 4 for n in dict.fromkeys(names))
    return m.cells * (per + ((4 if m.cells < bc.U32_CELLS else 8) if idx else 0))


def _load(project: Any, series: list[Series]) -> None:
    """The columns of the model series (ADR 0019 §3, «Статистика» ≤ 1 ГБ). When all the fields of all the models
    fit `REQUEST_BYTES`, each file is read once for all its series; else only `idx` and the fields of the filters
    are kept for the whole request and the values of a series are read when it is computed and let go after
    (`_values`; the cache keeps what it can) — what one request holds is then bounded too, or 422 `too_much_data`."""
    import numpy as np

    from . import blocks_cache as bc
    from .swath_blocks import keep_of

    groups: dict[int, list[Series]] = {}
    for x in series:
        if x.kind == "blocks" and x.error is None and x.model is not None and x.sub is None:
            groups.setdefault(x.model.id, []).append(x)
    if not groups:
        return
    models = {mid: xs[0].model for mid, xs in groups.items()}
    own = {mid: [n for x in xs for n in (x.field, x.density_name) if n] for mid, xs in groups.items()}
    cats = {mid: [x.category for x in xs if x.category] for mid, xs in groups.items()}
    eager = sum(_bytes(models[mid], own[mid] + cats[mid]) for mid in groups) <= REQUEST_BYTES
    if not eager:
        held = sum(_bytes(models[mid], cats[mid]) for mid in groups)
        one = max(_bytes(x.model, [n for n in (x.field, x.density_name) if n], idx=False)
                  for xs in groups.values() for x in xs)  # fmt: skip
        if held + one > REQUEST_BYTES:
            raise ApiError(422, "too_much_data",
                           f"Ряды моделей swath требуют {math.ceil((held + one) / 2**20)} МБ памяти — больше "
                           f"{REQUEST_BYTES // 2**20} МБ на один расчёт.",
                           "Уберите часть рядов блочных моделей или постройте их в отдельном swath.",
                           extra={"param": "series"})  # fmt: skip
    for mid, xs in groups.items():
        m = models[mid]
        try:
            cols = bc.columns(project, m, (own[mid] if eager else []) + cats[mid])
        except ApiError as e:
            for x in xs:
                x.error = _error(e.code, e.message, e.hint)
            continue
        for x in xs:
            x.cols = cols
            if eager:
                x.values = cols.number(str(x.field))
                x.density = cols.number(x.density_name) if x.density_name else None
            if x.category:
                x.codes, x.keep = cols.codes(x.category), np.asarray(keep_of(m.labels(x.category), x.labels))


def _filters(project: Any, series: list[Series]) -> None:
    """The filters of the library of the model series: the mask of the rows that pass (`swath_sub.filter_mask`);
    on a model of Э13 it joins the selection by categories (`fold_selection`)."""
    from . import swath_sub as ss

    folded: dict[tuple[int, int, int], tuple[Any, Any]] = {}  # the same filter on the same selection: once
    for x in series:
        if x.kind != "blocks" or x.error is not None or x.filter_id is None:
            continue
        try:
            x.mask, x.warnings = ss.filter_mask(project, int(x.model.id), x.filter_id)
        except ApiError as e:
            x.error = _error("source_deleted" if e.code == "block_model_not_found" else e.code, e.message, e.hint)
            continue
        if x.sub is None:
            key = (id(x.mask), id(x.codes), id(x.keep))
            if key not in folded:
                folded[key] = ss.fold_selection(x.mask, x.codes, x.keep)
            x.codes, x.keep = folded[key]


def _values(project: Any, x: Series) -> tuple[Any, Any]:
    """The values and the density of a model series: those `_load` read, else read now (from the cache or the file)."""
    from . import blocks_cache as bc

    if x.values is not None:
        return x.values, x.density
    cols = bc.columns(project, x.model, [n for n in (x.field, x.density_name) if n])
    return cols.number(str(x.field)), (cols.number(x.density_name) if x.density_name else None)


def _samples(project: Any, k: int, s: dict[str, Any]) -> Series:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import objects

    from . import routes_charts, source
    from .select import SampleFilter, select_sample
    from .weighted import StatsError

    keys = ("weight", "weight_field", "negatives", "top_cut")
    try:
        flt = SampleFilter.from_settings({"weight": "length", **{x: s.get(x) for x in keys if s.get(x) is not None}})
    except StatsError as e:
        raise _bad(f"series[{k}].{e.param or 'weight'}", f"Ряд {k + 1}: {e.message}", e.hint) from e
    within = s.get("within") or "box"
    if within not in ("box", "blocks"):
        raise _bad(f"series[{k}].within", f"Ряд {k + 1}: отбор проб «{str(within)[:40]}» неизвестен.",
                   "Допустимо: box — в кубоиде, blocks — внутри блоков модели.")  # fmt: skip
    name = _text(s, "field", k)
    out = Series(k, "samples", name, flt.weight, s.get("set_id"))
    out.within = -1 if within == "blocks" else None
    wf = flt.weight_field if flt.weight in routes_charts.FIELD_WEIGHTS else None
    names_of = {"none": ("число проб", ""), "length": ("длина", "м"), "field": (wf or "", ""),
                "length_field": (f"длина × {wf}", "м")}  # fmt: skip
    out.weight_name, out.weight_unit = names_of[flt.weight]
    out.name = f"Пробы · {name or '—'}"
    if out.source_id is None:
        out.error = _gone("набор скважин")
        return out
    set_id = int(out.source_id)
    try:
        t, names = routes_charts.named_fields(
            project, set_id, _text(s, "table", k), {"field": name, "weight_field": wf}
        )
        read = [names["field"], *([source.LENGTH] if flt.weight in ("length", "length_field") else []),
                *([names["weight_field"]] if wf else [])]  # fmt: skip
        cols = source.read(project, set_id, t.id, read)
    except ApiError as e:
        out.error = _error("source_deleted" if e.code == "set_not_found" else e.code, e.message, e.hint)
        return out
    with dialect.read_tx(project.engine) as conn:
        set_name = conn.execute(sa.select(objects.c.name).where(objects.c.id == set_id)).scalar()
    out.field = names["field"]
    out.name = f"{set_name or t.name} · {out.field}"
    out.unit = cols.infos[out.field].unit
    if wf:
        unit = cols.infos[names["weight_field"]].unit
        out.weight_name = names["weight_field"] if flt.weight == "field" else f"длина × {names['weight_field']}"
        out.weight_unit = (unit or "") if flt.weight == "field" else ("м·" + unit if unit else "м")
    args = cols.select_args(out.field, names.get("weight_field"))
    out.sel = select_sample(args["values"], flt, length=args["length"], weight_values=args["weight_values"])
    out.points = source.midpoints(project, cols)
    if within == "blocks":
        ws = s.get("within_series")
        if ws is not None and (isinstance(ws, bool) or not isinstance(ws, int)):
            raise _bad(f"series[{k}].within_series", f"Ряд {k + 1}: ряд модели задаётся номером.",
                       "Выберите ряд блочной модели.")  # fmt: skip
        out.within = -1 if ws is None else int(ws)
    return out


def _link(series: list[Series]) -> None:
    """The model series of the samples taken «inside the blocks»: the one named, else the first one."""
    models = [x.index for x in series if x.kind == "blocks"]
    for x in series:
        if x.kind != "samples" or x.within is None:
            continue
        k = x.index
        target = (models[0] if models else None) if x.within == -1 else x.within
        if target is None or not (0 <= target < len(series)) or series[target].kind != "blocks":
            raise _bad(f"series[{k}].within_series", f"Ряд {k + 1}: отбор «внутри блоков» требует ряда блочной модели.",
                       "Добавьте ряд модели или выберите «в кубоиде».")  # fmt: skip
        x.within = target
        if x.error is None and series[target].sub is not None:
            message = f"Ряд {k + 1}: пробы внутри блоков ряда {target + 1} (поворот, субблоки) пока не отбираются."
            x.error = _error("blocks_unsupported", message, "Выберите отбор «в кубоиде».")
        elif x.error is None and series[target].error is not None:
            message = f"Ряд {k + 1}: ряд модели {target + 1} не рассчитан — пробы внутри его блоков не отобрать."
            x.error = _error("within_not_computed", message, "Исправьте ряд модели или выберите «в кубоиде».")


# --- the calculation ----------------------------------------------------------------------------------------


def _number(settings: dict[str, Any], key: str, title: str, default: float | None = None) -> float | None:
    v = settings.get(key)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise _bad(key, f"{title}: ожидается число.", "Введите число или очистите поле.")
    return float(v)


def _answer(x: Series, n: int, base: Series | None) -> dict[str, Any]:
    from . import swath as sw

    out: dict[str, Any] = {
        "index": x.index, "kind": x.kind, "name": x.name, "field": x.field, "unit": x.unit, "weight": x.weight,
        "weight_name": x.weight_name, "weight_unit": x.weight_unit, "source_id": x.source_id,
    }  # fmt: skip
    if x.kind == "samples":
        out["within"] = "box" if x.within is None else x.within
    if x.error is not None or x.result is None:
        out.update(counts={}, mean=[None] * n, count=[0] * n, weights=[None] * n,
                   total={"mean": None, "count": 0, "weight": None}, error=x.error)  # fmt: skip
        if base is not None and x.index > 0:
            out["deviation"] = [None] * n
            out["total"]["deviation"] = None
        return out
    r = x.result
    d = r.as_dict()
    total = dict(d["total"])
    if not total["count"]:
        total["weight"] = None
    out.update(counts=d["counts"], mean=d["mean"], count=d["count"],
               weights=[w if c else None for w, c in zip(d["weight"], d["count"], strict=True)],
               total=total, error=None)  # fmt: skip
    if x.index > 0 and base is not None:
        if base.result is None:
            out["deviation"], total["deviation"] = [None] * n, None
        else:
            dev = sw.deviation(r.mean, base.result.mean)
            out["deviation"] = [float(v) if math.isfinite(v) else None for v in dev]
            td = float(sw.deviation(total["mean"], base.result.total["mean"]))
            total["deviation"] = td if math.isfinite(td) else None
    return out


@register("swath")
def swath_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """The swath of the settings (module docstring) on the current data."""
    from . import swath as sw
    from . import swath_blocks as sb
    from . import swath_frame as sf
    from . import swath_sub as ss
    from .weighted import StatsError

    direction = settings.get("direction") or "x"
    if direction not in sf.DIRECTIONS:
        raise _bad("direction", f"Неизвестное направление полос «{str(direction)[:40]}».",
                   "Выберите X, Y, Z или «по углам».")  # fmt: skip
    f = sf.direction_frame(direction, *(_number(settings, a, t, 0.0) for a, t in
                                        (("azimuth", "Азимут"), ("dip", "Наклон"), ("roll", "Поворот"))))  # fmt: skip
    width = _number(settings, "width", "Ширина полосы")
    anchor = _number(settings, "anchor", "Привязка полос")
    raw = settings.get("series") or []
    sw.check_series(len(raw))
    series: list[Series] = []
    for k, s in enumerate(raw):
        kind = s.get("kind")
        if kind not in ("blocks", "samples"):
            raise _bad(f"series[{k}].kind", f"Ряд {k + 1}: вид «{str(kind)[:40]}» неизвестен.",
                       "Ряд — блочная модель (blocks) или пробы (samples).")  # fmt: skip
        series.append(_blocks(project, k, s) if kind == "blocks" else _samples(project, k, s))
    _load(project, series)
    _filters(project, series)
    _link(series)

    ok = [x for x in series if x.error is None]
    for x in ok:
        if x.kind == "blocks" and x.sub is not None:
            x.ext = ss.bounds(x.sub, f, mask=x.mask, category=x.category, keep=x.keep, threads=THREADS)
        elif x.kind == "blocks":
            x.ext = sb.block_bounds(f, x.grid, x.cols.idx, codes=x.codes, keep=x.keep, threads=THREADS)
        else:
            x.ext = sw.sample_bounds(f, x.points, x.sel)
    first = next((x for x in ok if x.kind == "blocks"), None)
    block = None
    if first is not None:
        block = ss.block_side(f, first.sub) if first.sub is not None else sf.block_side(f.u, sf.grid_sides(first.grid))
    try:
        exts = {kind: [x.ext for x in ok if x.kind == kind] for kind in ("blocks", "samples")}
        c = sw.cuboid(f, models=exts["blocks"], samples=exts["samples"], limits=settings.get("limits"), width=width,
                      anchor=anchor, block=block)  # fmt: skip
    except StatsError as e:
        failed = next((x.error for x in series if x.error is not None), None)
        if e.code == "no_data" and failed is not None:
            e.hint = f"{failed['message']} {e.hint}"
        raise

    cells: dict[int, Any] = {}
    for x in ok:
        if x.kind == "blocks" and x.sub is not None:
            x.result = ss.series(x.sub, f, c, field=str(x.field), weight=x.weight, density=x.density_name,
                                 mask=x.mask, category=x.category, keep=x.keep, threads=THREADS)  # fmt: skip
            continue
        if x.kind == "blocks":
            values, density = _values(project, x)
            x.result = sb.block_series(x.grid, x.cols.idx, values, f, c, weight=x.weight, density=density,
                                       codes=x.codes, keep=x.keep, threads=THREADS)  # fmt: skip
            x.values = x.density = values = density = None  # a series read alone is let go before the next one
            continue
        inside = None
        if x.within is not None:
            m = series[x.within]
            if x.within not in cells:
                cells[x.within] = sw.cells_of(m.grid, sb.selected_idx(m.cols.idx, m.codes, m.keep))
            inside = cells[x.within]
        x.result = sw.sample_series(x.points, x.sel, f, c, cells=inside)

    notes = [n for n in (None if first is not None and first.sub is not None else sw.width_note(c),) if n]
    notes += [f"Ряд {x.index + 1}: блоки на границе полос делятся между полосами пропорционально объёму."
              for x in ok if x.sub is not None]  # fmt: skip
    notes += [f"Ряд {x.index + 1}: {w}" for x in ok for w in x.warnings]
    base = series[0]
    return {"direction": direction, "frame": f.as_dict(), **c.as_dict(),
            "series": [_answer(x, c.count, base) for x in series], "notes": notes}  # fmt: skip


def _saved(project: Any, swath_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    from bf_core.charts import service
    from bf_core.charts.kinds import SWATH, is_id
    from bf_core.db import dialect

    if not is_id(swath_id):
        raise _bad("swath_id", "Swath задаётся номером.", "Выберите swath в дереве проекта.")
    try:
        with dialect.read_tx(project.engine) as conn:
            chart = service.get(conn, int(swath_id))
    except service.NotFound as e:
        raise ApiError(404, "chart_not_found", "Swath не найден.", "Обновите дерево проекта.") from e
    if chart["kind"] != SWATH:
        raise _bad("swath_id", "Это не swath, а график другого вида.", "Выберите swath в «Аудит и статистика».")
    head = {"id": chart["id"], "name": chart["name"], "version": chart["version"]}
    return (chart["settings"] if isinstance(chart["settings"], dict) else {}), head


@router.post("/swath")
def post_swath(svc: Svc, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    project = svc.require_current()
    head: dict[str, Any] | None = None
    raw: Any
    if body.get("swath_id") is not None:
        raw, head = _saved(project, body["swath_id"])
    else:
        raw = body.get("settings")
        if not isinstance(raw, dict):
            raise _bad("settings", "Нет настроек swath.", "Передайте настройки swath или его номер.")
    settings = checked("swath", raw)
    out = run(lambda: swath_of(project, settings))
    return out if head is None else {**out, "swath": head}
