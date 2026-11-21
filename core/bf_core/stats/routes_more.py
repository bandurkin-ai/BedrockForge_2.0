"""/api/v1/stats — the charts of ТЗ 8.3 (ADR 0019 §10): scatter, box plots, log-probability, ternary, by domains.

    POST /stats/scatter    {v, set_id, table, x, y, y_set_id, y_table, color, log_x, log_y, weight, weight_field,
                            negatives, exclude_zero, regression, holes}
        → {set_id, y_set_id, x_field, y_field, color_field, log_x, log_y, weighted, counts, mode, x[], y[], row[],
           c[] | null, color | null (mode "points") or density (mode "density"), pearson, spearman, n, fits,
           x_stats, y_stats, conventions}
    POST /stats/box        {…the selection of `routes_charts`, set_id, table, field, domain, domain_table, log, holes}
        → {set_id, field, domain, domain_table, domain_outside, log, weighted, counts, groups, outliers_shown,
           outliers_total, conventions}
    POST /stats/probplot   the same keys but `log` → {…, series [{label, role, points [[value, P %]], count,
                            nonpositive, stats {mean, median, cv, p90}}], conventions}
    POST /stats/domains    the same keys but `log` → {…, rows, total, decl_field | null, decl_hint | null, notes,
                            conventions}
    POST /stats/ternary    {v, set_id, table, a, b, c, color, holes}
        → {set_id, fields [a, b, c], color_field, counts, a[], b[], c[], row[], total, color_values | null,
           color | null, conventions}

The answers are those of the kernels (`stats.scatter`, `boxplot`, `probplot`, `domain_stats`, `ternary`) laid out
as the windows read them (`ui/src/modules/charts/apiMore.ts`); `docs/progress/e13-api.md` has the exact shapes.
A field without a name takes the first component of the table (`routes_charts.first_number`), a domain — a text
field of the same table or of `domain_table` (by the middle of a sample, `source.domain_at`), Y of the scatter —
of the same table or of `y_table` / `y_set_id` (by the sample key, `source.paired`; a chart of two sets lies in
the set of X). `row` — the ids of `dh_rows`. Where and how it runs, the errors — as `routes_charts`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from bf_core.api.errors import ApiError

from . import routes_charts as rc
from .routes_charts import Settings, Svc

router = APIRouter()

REGRESSIONS = ("ols", "rma", "weighted", "none")
MAX_COLORS = 300  # values of a text colour field (as many as `GET /stats/fields` lists)
MAX_TERNARY = 200_000  # points of a ternary diagram; more — evenly thinned


# --- the pieces of the settings ---------------------------------------------------------------------------


def _weight_field(t: Any, infos: list[Any], flt: Any) -> str | None:
    if flt.weight in rc.FIELD_WEIGHTS and flt.weight_field is not None:
        return str(rc.field_of(t, infos, flt.weight_field, "weight_field").name)
    return None


def _number(t: Any, infos: list[Any], settings: dict[str, Any], param: str, skip: tuple[str, ...] = ()) -> str:
    name = rc._name(settings, param)
    f = rc.first_number(t, infos, param, skip) if name is None else rc.field_of(t, infos, name, param)
    return str(f.name)


def _color_field(t: Any, infos: list[Any], settings: dict[str, Any]) -> Any:
    name = rc._name(settings, "color")
    return None if name is None else rc.field_of(t, infos, name, "color", ("number", "text"))


def _color_column(cols: Any, f: Any) -> tuple[Any, list[str] | None]:
    """The colour column (numbers, or the codes of a text field, −1 — empty) and the labels of a text one."""
    if f is None:
        return None, None
    if f.kind == "number":
        return cols.number(f.name), None
    codes, labels = cols.text(f.name)
    if len(labels) > MAX_COLORS:
        raise ApiError(422, "too_many_domains", f"В поле цвета «{f.name}» слишком много значений ({len(labels)}, "
                       f"максимум {MAX_COLORS}).", "Выберите числовое поле или текстовое с меньшим числом значений.",
                       extra={"param": "color"})  # fmt: skip
    return codes, labels


def _color_info(values: list[Any] | None, f: Any, labels: list[str] | None) -> tuple[list[Any] | None, Any]:
    """`c` of the points (a text field — the index into `labels`, null — empty) and `color` of the answer."""
    if f is None or values is None:
        return None, None
    if labels is not None:
        return [None if v is None or v < 0 else v for v in values], {"kind": "text", "labels": labels}
    shown = [v for v in values if v is not None]
    return values, {"kind": "number", "min": min(shown, default=None), "max": max(shown, default=None)}


def _domain(project: Any, set_id: int, t: Any, infos: list[Any], settings: dict[str, Any]) -> tuple[Any, str] | None:
    """(the table of the domain, the stored name of its text field) or None — no domain."""
    name = rc._name(settings, "domain")
    if name is None:
        return None
    table = rc._name(settings, "domain_table")
    dt, dinfos = (t, infos) if table is None else rc.table_of(project, set_id, table)
    return dt, str(rc.field_of(dt, dinfos, name, "domain", ("text",)).name)


def _domain_codes(project: Any, set_id: int, cols: Any, dom: tuple[Any, str] | None) -> tuple[Any, Any, int]:
    """(codes for every row of `cols`, labels, rows whose middle no interval of the other table covers)."""
    from . import source

    if dom is None:
        return None, None, 0
    dt, name = dom
    if dt.id == cols.table.id:
        codes, labels = cols.text(name)
        return codes, labels, 0
    found = source.domain_at(cols, source.read(project, set_id, dt.id, [name]), name)
    return found.codes, found.labels, found.outside


# --- the charts by domains ---------------------------------------------------------------------------------


def _by_domain(project: Any, settings: dict[str, Any], decl: bool = False) -> dict[str, Any]:
    """What the charts by domains share: the selection of the field and the codes of the domain."""
    from . import select, source
    from .domain_stats import DECL_FIELD

    flt = select.SampleFilter.from_settings(settings)
    set_id = rc._set_id(settings)
    t, infos = rc.table_of(project, set_id, rc._name(settings, "table"))
    field = _number(t, infos, settings, "field")
    weight_field = _weight_field(t, infos, flt)
    dom = _domain(project, set_id, t, infos, settings)
    decl_field = next((f.name for f in infos if f.kind == "number" and f.name.casefold() == DECL_FIELD.casefold()),
                      None) if decl else None  # fmt: skip
    read = [field, source.LENGTH, *(n for n in (weight_field, decl_field) if n is not None)]
    if dom is not None and dom[0].id == t.id:
        read.append(dom[1])
    cols = rc.only_holes(source.read(project, set_id, t.id, read), rc._holes(settings, "holes"))
    sel = select.select_sample(**cols.select_args(field, weight_field), flt=flt)
    codes, labels, outside = _domain_codes(project, set_id, cols, dom)
    return {
        "set_id": set_id, "field": field, "domain": None if dom is None else dom[1],
        "domain_table": None if dom is None or dom[0].id == t.id else dom[0].name, "domain_outside": outside,
        "sel": sel, "codes": codes, "labels": labels, "cols": cols, "decl_field": decl_field,
    }  # fmt: skip


def _head(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d[k] for k in ("set_id", "field", "domain", "domain_table", "domain_outside")}


@rc.register("box")
def box_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    from .boxplot import box_chart

    d = _by_domain(project, settings)
    out = box_chart(d["sel"], d["codes"], d["labels"], log=False if settings.get("log") is None else settings["log"])
    groups = [{
        "label": b["domain"], "role": b["role"], "n": b["count"], "weight_sum": b["weight_sum"],
        "mean": b["arithmetic_mean"], "geometric_mean": b["mean"] if out["log"] else None, "q1": b["q1"],
        "median": b["median"], "q3": b["q3"], "whisker_low": b["whisker_low"], "whisker_high": b["whisker_high"],
        "min": b["min"], "max": b["max"], "nonpositive": b["nonpositive"], "outliers": b["outliers"],
        "outliers_low": b["outliers_low"], "outliers_high": b["outliers_high"],
        "outliers_total": b["outliers_low"] + b["outliers_high"],
    } for b in out["boxes"]]  # fmt: skip
    return {**_head(d), "log": out["log"], "weighted": out["weighted"], "counts": out["counts"], "groups": groups,
            "outliers_shown": out["outliers"]["shown"], "outliers_total": out["outliers"]["total"],
            "conventions": out["conventions"]}  # fmt: skip


@rc.register("probplot")
def probplot_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """The lines of `probplot` with the numbers of their domains: the kernel's steps on one sort of the domains
    (`group_samples` → `probability_line`), the statistics of every domain from the same sort."""
    import math

    from .domain_stats import group_samples
    from .probplot import CONVENTIONS, probability_line
    from .weighted import StatsError

    d = _by_domain(project, settings)
    found = group_samples(d["sel"], d["codes"], d["labels"])
    series, dropped = [], 0
    for g in found.groups:
        line = probability_line(g.sample)
        dropped += g.sample.n - (0 if line is None else line["count"])
        if line is None:
            continue
        s = g.sample
        median, p90 = (float(q) for q in s.percentiles((50.0, 90.0)))
        mean, std = float(s.mean), math.sqrt(s.variance)
        series.append({
            "label": g.domain, "role": g.role, "points": [[p[0], p[2]] for p in line["points"]],
            "count": line["count"], "nonpositive": line["nonpositive"], "thinned": line["thinned"], "r": line["r"],
            "stats": {"mean": mean, "median": median, "cv": std / mean if mean > 0.0 else None, "p90": p90},
        })  # fmt: skip
    if not series:
        raise StatsError(
            "no_positive", "Для лог-вероятностного графика нужны значения больше нуля.",
            "Положительных значений в выборке нет: выберите другое поле или правило «половина модуля» для "
            "значений меньше нуля.",
        )  # fmt: skip
    return {**_head(d), "weighted": d["sel"].weighted, "counts": {**found.counts, "nonpositive_log": dropped},
            "series": series, "conventions": CONVENTIONS}  # fmt: skip


@rc.register("domains")
def domains_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    from . import source
    from .domain_stats import domain_stats

    d = _by_domain(project, settings, decl=True)
    cols, decl = d["cols"], d["decl_field"]
    out = domain_stats(d["sel"], d["codes"], d["labels"], length=cols.number(source.LENGTH),
                       decl_weights=None if decl is None else cols.number(decl))  # fmt: skip

    def row(r: dict[str, Any]) -> dict[str, Any]:
        keep = ("length", "weight_sum", "mean", "median", "std", "cv", "min", "max", "p10", "p50", "p90")
        return {"label": r["domain"], "role": r["role"], "n": r["count"], "decl_mean": r["declustered_mean"],
                **{k: r[k] for k in keep}}  # fmt: skip

    return {**_head(d), "weighted": out["weighted"], "counts": out["counts"], "rows": [row(r) for r in out["rows"]],
            "total": row(out["total"]), "decl_field": decl if out["declustered"] else None,
            "decl_hint": out["notes"][0] if out["notes"] else None, "notes": out["notes"],
            "conventions": out["conventions"]}  # fmt: skip


# --- scatter ------------------------------------------------------------------------------------------------


def _prepared(x: Any, y: Any, w: Any, flt: Any, unmatched: Any) -> tuple[Any, Any, dict[str, int]]:
    """x and y with NaN in the rows the rules leave out (as `select` does, for both fields: a pair with either
    left out is not drawn) and the counts of those rows. `unmatched` — the rows without a row of the table Y."""
    import numpy as np

    fx, fy = np.isfinite(x), np.isfinite(y)
    ok = fx & fy
    lone = fx & unmatched if unmatched is not None else np.zeros_like(fx)
    counts = {"rows": int(x.shape[0]), "missing_x": int((~fx).sum()), "missing_y": int((fx & ~fy & ~lone).sum()),
              "unmatched": int(lone.sum()), "bad_weight": 0, "negative": 0, "zero_excluded": 0}  # fmt: skip
    if w is not None:
        heavy = np.isfinite(w) & (w > 0.0)
        counts["bad_weight"] = int((ok & ~heavy).sum())
        ok &= heavy
    with np.errstate(invalid="ignore"):
        neg = ok & ((x < 0.0) | (y < 0.0))
        counts["negative"] = int(neg.sum())
        if flt.negatives == "exclude":
            ok &= ~neg
        elif flt.negatives == "half_abs" and counts["negative"]:
            x, y = np.where(x < 0.0, -x / 2.0, x), np.where(y < 0.0, -y / 2.0, y)
        if flt.exclude_zero:
            zero = ok & ((x == 0.0) | (y == 0.0))
            counts["zero_excluded"] = int(zero.sum())
            ok &= ~zero
    return np.where(ok, x, np.nan), np.where(ok, y, np.nan), counts


@rc.register("scatter")
def scatter_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    from . import select, source
    from .scatter import scatter_chart

    regression = settings.get("regression")
    if regression is not None and regression not in REGRESSIONS:
        raise rc._bad("regression", f"Линия регрессии «{str(regression)[:40]}» неизвестна.",
                   "Допустимо: ols — МНК, rma — RMA, weighted — взвешенная, none — без линии.")  # fmt: skip
    flt = select.SampleFilter.from_settings({k: settings.get(k) for k in ("weight", "weight_field", "negatives",
                                                                          "exclude_zero")})  # fmt: skip
    set_id = rc._set_id(settings)
    t, infos = rc.table_of(project, set_id, rc._name(settings, "table"))
    x = _number(t, infos, settings, "x")
    y_set = set_id if settings.get("y_set_id") is None else rc._set_id(settings, "y_set_id")
    y_table = rc._name(settings, "y_table")
    yt, yinfos = (t, infos) if y_set == set_id and y_table is None else rc.table_of(project, y_set, y_table)
    same = yt.id == t.id
    y = _number(yt, yinfos, settings, "y", (x,) if same else ())
    weight_field = _weight_field(t, infos, flt)
    color = _color_field(t, infos, settings)
    read = [x, source.LENGTH, *(n for n in (weight_field, color and color.name) if n), *([y] if same else [])]
    cols = rc.only_holes(source.read(project, set_id, t.id, read), rc._holes(settings, "holes"))
    unmatched = None
    if same:
        yv = cols.number(y)
    else:
        found = source.paired(cols, source.read(project, y_set, yt.id, [y]), y)
        yv, unmatched = found.values, found.rows < 0
    args = cols.select_args(x, weight_field)
    w = select.sample_weights(flt.weight, args["length"], args["weight_values"])
    xs, ys, counts = _prepared(args["values"], yv, w, flt, unmatched)
    colors, labels = _color_column(cols, color)
    log_x, log_y = (False if settings.get(k) is None else settings[k] for k in ("log_x", "log_y"))
    out = scatter_chart(xs, ys, w, log_x=log_x, log_y=log_y, color=colors, row_ids=cols.row_ids)
    counts.update(nonpositive_log=out["counts"]["nonpositive"], used=out["counts"]["used"])
    st, rng = out["stats"], out["range"]
    answer: dict[str, Any] = {
        "set_id": set_id, "y_set_id": y_set, "x_field": x, "y_field": y, "color_field": color and color.name,
        "log_x": out["log_x"], "log_y": out["log_y"], "weighted": out["weighted"], "counts": counts,
        "mode": out["mode"],
    }  # fmt: skip
    if out["points"] is not None:
        p = out["points"]
        c, info = _color_info(p["c"], color, labels)
        answer.update(x=p["x"], y=p["y"], row=p["row"], c=c, color=info)
    else:
        g = out["density"]
        answer["density"] = {"nx": len(g["x_edges"]) - 1, "ny": len(g["y_edges"]) - 1,
                             "x": [g["x_edges"][0], g["x_edges"][-1]], "y": [g["y_edges"][0], g["y_edges"][-1]],
                             "counts": [n for line in g["counts"] for n in line], "max": g["max"]}  # fmt: skip
        answer.update(c=None, color=None)
    wf = st["weighted"]
    answer.update(
        pearson=st["pearson"], spearman=st["spearman"], n=st["n"], weight_sum=st["weight_sum"],
        fits={"ols": st["ols"], "rma": st["rma"], "weighted": None if wf is None else {"a": wf["a"], "b": wf["b"]}},
        weighted_pearson=None if wf is None else wf["pearson"],
        x_stats={**st["x"], "min": rng["x"][0], "max": rng["x"][1]},
        y_stats={**st["y"], "min": rng["y"][0], "max": rng["y"][1]}, conventions=out["conventions"],
    )  # fmt: skip
    return answer


# --- ternary ------------------------------------------------------------------------------------------------


@rc.register("ternary")
def ternary_of(project: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """The shares of `ternary.shares`; more than MAX_TERNARY rows are thinned evenly along the rows (the window
    draws points, not the density of `ternary_chart`), `total` — all the rows drawn."""
    import numpy as np

    from . import source
    from .ternary import CONVENTIONS, shares

    set_id = rc._set_id(settings)
    t, infos = rc.table_of(project, set_id, rc._name(settings, "table"))
    names: list[str] = []
    for param in ("a", "b", "c"):
        names.append(_number(t, infos, settings, param, tuple(names)))
    color = _color_field(t, infos, settings)
    cols = rc.only_holes(source.read(project, set_id, t.id, [*names, *([color.name] if color else [])]),
                      rc._holes(settings, "holes"))  # fmt: skip
    s = shares(*(cols.number(n) for n in names)).require()
    at = np.unique(np.rint(np.linspace(0.0, s.n - 1.0, MAX_TERNARY)).astype(np.int64)) if s.n > MAX_TERNARY else None

    def pick(a: Any) -> Any:
        return a if at is None else a[at]

    rows = pick(s.rows)
    colors, labels = _color_column(cols, color)
    values = None
    if colors is not None:
        got = np.asarray(colors)[rows]
        values = got.tolist() if labels is not None else [v if np.isfinite(v) else None for v in got.tolist()]
    c, info = _color_info(values, color, labels)
    k = s.counts
    counts = {"rows": k["rows"], "missing": k["missing"], "nonpositive_sum": k["negative"] + k["zero_sum"],
              "negative": k["negative"], "zero_sum": k["zero_sum"], "used": k["used"]}  # fmt: skip
    return {
        "set_id": set_id, "fields": names, "color_field": color and color.name, "counts": counts,
        "a": pick(s.a).tolist(), "b": pick(s.b).tolist(), "c": pick(s.c).tolist(),
        "row": cols.row_ids[rows].tolist(), "total": s.n, "color_values": c, "color": info,
        "conventions": CONVENTIONS,
    }  # fmt: skip


# --- the routes ---------------------------------------------------------------------------------------------


def _route(kind: str) -> Any:
    calc = {"scatter": scatter_of, "box": box_of, "probplot": probplot_of, "domains": domains_of,
            "ternary": ternary_of}[kind]  # fmt: skip

    def post(svc: Svc, body: Settings) -> JSONResponse:
        project = svc.require_current()
        settings = rc.checked(kind, body)
        return JSONResponse(rc.run(lambda: calc(project, settings)))

    post.__name__ = f"post_{kind}"
    return post


for _kind in ("scatter", "box", "probplot", "domains", "ternary"):
    router.add_api_route(f"/{_kind}", _route(_kind), methods=["POST"])
