"""The rows, the curve and the totals of a resource report from the accumulators of a pass (подшаг 20.3; ТЗ 8.10;
ADR 0027 decisions 4–6, 10–12; contract — docs/progress/e20-api.md §3).

    spec = parse_params(body).resolved(model.fields, z_range_of(model))     # resolved: fields checked, benches known
    plan = core_from_spec(model, spec)                  # `Plan`: the `compute.Core` + the cut-offs of the table
    sums = compute.run(model, plan.core, restrictor, chunk=, tick=)
    res  = build(sums, plan, max_rows=None)             # `Result`: rows, curve, totals, warnings, excluded
    for row in iter_rows(sums, plan): ...               # the same rows one by one (the writer of the Parquet `data`)
    flat = flat_row(row, nkeys)                         # {g1…g3, kind, cutoff, …, "<e>:wt", …}: `forms.build`, Parquet

A row (ADR 0027 decision 15; `None` — no value, never 0):

    {"g": ["Измеренные", "(пусто)"] | [None, None] (the total) | [] (no keys), "total": bool,
     "kind": "cutoff" | "all" | "balance" | "offbalance", "cutoff": c | None, "cutoff_to": c_б | None (offbalance),
     "blocks": n, "volume": m³, "tonnes": t,
     "elements": {name: {"wt", "sum", "grade", "metal", "missing_t", "missing_n"}}}

`wt` — the tonnes of the blocks with the grade (the denominator), `sum` = Σ t·g, `grade` = sum / wt (None if wt = 0),
`metal` = sum·k_e in the base unit of the element (g for g/t, ppm, ppb; t for %; None — the unit is unknown or wt = 0),
`missing_t`, `missing_n` — the tonnes and blocks with no grade (not in the mean, not in the metal).

Order: the rows «all» («без борта»), the cut-offs as asked, `balance`, `offbalance`; in each — the total, then the
groups by ascending code. A group is in the report if a block was counted in it (BF `grade_tonnage` skips the
others); then it has a row for every cut-off (a row with no blocks: T = 0, ḡ empty). The bins and the order of the
sums are those of `kernels.py`: a row «cutoff c» is `range_sums(cutoff_bin(c) … E+1)`, «all» — every bin, the
balance — the same bins as the table, so a number of the table is the same bit as the number of the curve at the
same cut-off. The curve: 100 cut-offs (2–500) from the minimum to the maximum of the cut-off field by the statistics
of the model (as Э17), the total only: the bins + the suffix sums of `kernels.suffix_sums`, no sorting of the blocks.

The labels of a group — `spec.group_labels(code)` (the bench: «+250…+260»; a block with no value — «(пусто)»).
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.blocks.model import Model

from . import compute as cp
from . import kernels as rk
from .spec import EMPTY_LABEL, ReportSpec, metal_k, unit_label

__all__ = ["EMPTY_LABEL", "Plan", "Result", "RowRange", "build", "core_from_spec", "curve_of", "elements_of",
           "flat_row", "iter_rows", "roles_of", "row_count", "row_ranges", "totals_of", "z_range_of"]  # fmt: skip


def _warning(code: str, message: str, hint: str, param: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message, "hint": hint}
    if param is not None:
        out["param"] = param
    return out


@dataclass(frozen=True)
class RowRange:
    """One table of rows: its kind and cut-off, the bins of the total (`lo`, `hi`) and of a group (`glo`, `ghi`)."""

    kind: str
    cutoff: float | None
    cutoff_to: float | None
    lo: int
    hi: int
    glo: int
    ghi: int


@dataclass(frozen=True)
class Plan:
    """What the pass counts (`core`) and what the rows are: the resolved `spec`, the elements' names and k_e (grams or
    tonnes of metal per tonne × grade; None — the unit is unknown), the cut-offs of the table (None — «без борта»),
    the balance (c_б, c_з | None), the cut-offs of the curve (None — no curve)."""

    spec: ReportSpec
    core: cp.Core
    names: tuple[str, ...]
    ks: tuple[float | None, ...]
    cutoffs: tuple[float | None, ...]
    balance: tuple[float, float | None] | None
    curve: np.ndarray | None
    warnings: tuple[dict[str, Any], ...] = ()

    @property
    def nkeys(self) -> int:
        return len(self.spec.keys)


@dataclass
class Result:
    rows: list[dict[str, Any]]
    rows_total: int
    totals: dict[str, Any] | None
    curve: dict[str, Any] | None
    excluded: dict[str, int]
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return len(self.rows) < self.rows_total


# --- the plan ---------------------------------------------------------------------------------------------------------


def z_range_of(model: Model) -> tuple[float, float]:
    """The extent of the model along Z (a turn is about Z only, ADR 0023): the range of `spec.resolved`."""
    lz = float(model.grid.lengths()[2])
    z0 = float(model.grid.corner[2])
    return z0, z0 + lz


def _field(model: Model, name: str) -> Mapping[str, Any] | None:
    return next((f for f in model.fields if str(f["name"]).casefold() == name.casefold()), None)


def curve_cutoffs(model: Model, spec: ReportSpec) -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    """The cut-offs of the curve: `spec.curve` points from the minimum to the maximum of the cut-off field (the stats
    of the model, as Э17); None — no curve (`spec.curve` = 0, or the field has no value: a warning)."""
    if not spec.curve or spec.cutoff_field is None:
        return None, []
    f = _field(model, spec.cutoff_field)
    st = (f or {}).get("stats") or {}
    lo, hi = st.get("min"), st.get("max")
    if lo is None or hi is None or not (math.isfinite(float(lo)) and math.isfinite(float(hi))):
        return None, [_warning("curve_no_values", f"В поле «{spec.cutoff_field}» нет значений: кривая не построена.",
                               "Выберите поле борта с содержаниями.", "cutoff_field")]  # fmt: skip
    pts = np.linspace(float(lo), float(hi), spec.curve)
    return np.unique(pts), []


def core_from_spec(model: Model, spec: ReportSpec) -> Plan:
    """The adapter `ReportSpec` (resolved) → `compute.Core` and the cut-offs of the table. The bins of the total are the
    union of the table, the balance and the curve; the bins of a group — the table and the balance only."""
    if not spec.resolved_:
        raise cp.bad_param("params", "Параметры отчёта не разобраны по модели.", "Вызовите spec.resolved(...).")
    assert spec.cutoff_field is not None and spec.density is not None
    table = [c for c in spec.cutoffs if c is not None]
    bal = [] if spec.balance is None else [c for c in (spec.balance.cutoff, spec.balance.off_cutoff) if c is not None]
    curve, warns = curve_cutoffs(model, spec)
    group_edges = np.unique(np.array([*table, *bal], dtype=np.float64))
    edges = np.unique(np.concatenate([group_edges, np.empty(0) if curve is None else curve]))
    keys: list[cp.Key] = []
    for k in spec.keys:
        if k.kind == "bench":
            b = spec.bench
            assert b is not None and b.z0 is not None and b.j0 is not None and b.n is not None
            keys.append(cp.Key(None, bench=cp.Bench(b.z0 + b.j0 * b.h, b.h, b.n)))
        else:
            keys.append(cp.Key(k.field, tuple(k.labels or ())))
    dens = spec.density
    core = cp.Core(
        [e.field for e in spec.elements],
        spec.cutoff_field,
        dens if isinstance(dens, str) else None,
        None if isinstance(dens, str) else float(dens),
        edges,
        group_edges,
        keys,
        cp.default_frac(model),
    )
    core.check()
    ks = tuple(metal_k(e.unit) for e in spec.elements)
    for i, (e, km) in enumerate(zip(spec.elements, ks, strict=True)):
        if km is None:
            what = f"«{e.unit}»" if e.unit else "не задана"
            msg = f"У элемента «{e.field}» единица содержания {what}: металл не считается."
            warns.append(_warning("unit_unknown", msg, "Задайте единицу г/т, ppm, ppb или % в окне отчёта.",
                                  f"elements.{i}.unit"))  # fmt: skip
    balance = None if spec.balance is None else (spec.balance.cutoff, spec.balance.off_cutoff)
    names = tuple(e.field for e in spec.elements)
    return Plan(spec, core, names, ks, tuple(spec.cutoffs), balance, curve, tuple(warns))


def elements_of(spec: ReportSpec) -> list[dict[str, Any]]:
    """`[{name, unit}]` for `forms.build`: the unit of the grade as the user reads it (г/т, ppm, ppb, %)."""
    return [{"name": e.field, "unit": unit_label(e.unit) or e.unit} for e in spec.elements]


def roles_of(spec: ReportSpec, fields: Sequence[Mapping[str, Any]]) -> list[str]:
    """The role of each key of the group for `forms.build` (`roles`): the role of the field («class», …), «bench» for
    the bench, an empty text for a field with no role."""
    by = {str(f["name"]).casefold(): f for f in fields}
    return [
        "bench" if k.kind == "bench" else str((by.get((k.field or "").casefold()) or {}).get("role") or "")
        for k in spec.keys
    ]


def row_ranges(plan: Plan) -> list[RowRange]:
    """The tables of rows in the order of the report: «all», the cut-offs as asked, the balance, the off-balance."""
    core = plan.core
    e, eg = len(core.edges), len(core.group_edges)
    out: list[RowRange] = []
    if None in plan.cutoffs:
        out.append(RowRange("all", None, None, 0, e + 2, 0, eg + 2))
    for c in plan.cutoffs:
        if c is not None:
            out.append(RowRange("cutoff", c, None, rk.cutoff_bin(core.edges, c), e + 1,
                                rk.cutoff_bin(core.group_edges, c), eg + 1))  # fmt: skip
    if plan.balance is not None:
        cb, cz = plan.balance
        out.append(RowRange("balance", cb, None, rk.cutoff_bin(core.edges, cb), e + 1,
                            rk.cutoff_bin(core.group_edges, cb), eg + 1))  # fmt: skip
        if cz is not None:
            out.append(RowRange("offbalance", cz, cb, rk.cutoff_bin(core.edges, cz), rk.cutoff_bin(core.edges, cb),
                                rk.cutoff_bin(core.group_edges, cz), rk.cutoff_bin(core.group_edges, cb)))  # fmt: skip
    return out


# --- the rows ---------------------------------------------------------------------------------------------------------


def present_groups(sums: cp.Sums) -> np.ndarray:
    """The codes of the groups with a counted block (any bin): they have rows."""
    if sums.core.n_groups() == 0:
        return np.empty(0, dtype=np.int64)
    n = sums.group_view()[2]
    return np.flatnonzero(n[:, :, 0].sum(axis=1) > 0)


def _fin(x: float) -> float | None:
    """A number that is not finite (an overflow, a mean of a weight that is not a number) is a gap: null, never inf."""
    return x if math.isfinite(x) else None


def _cells(q: np.ndarray, n: np.ndarray, plan: Plan) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for i, (name, k) in enumerate(zip(plan.names, plan.ks, strict=True)):
        wt, total, miss = float(q[2 + 3 * i]), float(q[3 + 3 * i]), float(q[4 + 3 * i])
        has = wt > 0.0
        grade = total / wt if has else None
        metal = total * k if has and k is not None else None
        out[name] = {"wt": _fin(wt), "sum": _fin(total), "grade": None if grade is None else _fin(grade),
                     "metal": None if metal is None else _fin(metal), "missing_t": _fin(miss),
                     "missing_n": int(n[1 + i])}  # fmt: skip
    return out


def _row(g: list[str | None], total: bool, r: RowRange, q: np.ndarray, n: np.ndarray, plan: Plan) -> dict[str, Any]:
    return {"g": g, "total": total, "kind": r.kind, "cutoff": r.cutoff, "cutoff_to": r.cutoff_to, "blocks": int(n[0]),
            "volume": _fin(float(q[0])), "tonnes": _fin(float(q[1])), "elements": _cells(q, n, plan)}  # fmt: skip


def not_finite(sums: cp.Sums, plan: Plan) -> list[str]:
    """What of the report would be not finite (the names: the elements, «масса», «объём»), by the arrays of every
    table at once — the rows themselves show it as gaps (`_fin`)."""
    present = present_groups(sums)
    names: dict[str, None] = {}
    with np.errstate(all="ignore"):
        for r in row_ranges(plan):
            tables = [sums.range_of(False, r.lo, r.hi)[0]]
            if len(present):
                tables.append(sums.range_of(True, r.glo, r.ghi)[0][present])
            for q in tables:
                if not np.isfinite(q[:, 0]).all():
                    names["объём"] = None
                if not np.isfinite(q[:, 1]).all():
                    names["масса"] = None
                for i, (name, k) in enumerate(zip(plan.names, plan.ks, strict=True)):
                    wt, total = q[:, 2 + 3 * i], q[:, 3 + 3 * i]
                    has = wt > 0.0
                    grade = np.where(has, total / np.where(has, wt, 1.0), 0.0)
                    metal = np.where(has, total * (0.0 if k is None else k), 0.0)
                    cols = (wt, total, q[:, 4 + 3 * i], grade, metal)
                    if not all(np.isfinite(c).all() for c in cols):
                        names[name] = None
    return list(names)


def row_count(sums: cp.Sums, plan: Plan) -> int:
    return len(row_ranges(plan)) * (1 + len(present_groups(sums)))


def iter_rows(sums: cp.Sums, plan: Plan) -> Iterator[dict[str, Any]]:
    """The rows of the report one by one (the order is in the module text)."""
    spec = plan.spec
    present = present_groups(sums)
    labels: list[list[str | None]] = [list(spec.group_labels(int(c))) for c in present]  # type: ignore[arg-type]
    for r in row_ranges(plan):
        q, n = sums.range_of(False, r.lo, r.hi)  # the sums of the tables are taken once (`Sums.range_of`)
        yield _row([None] * plan.nkeys, True, r, q[0], n[0], plan)
        if len(present) == 0:
            continue
        qg, ng = sums.range_of(True, r.glo, r.ghi)
        for code, lab in zip(present, labels, strict=True):
            yield _row(lab, False, r, qg[code], ng[code], plan)


def flat_row(row: Mapping[str, Any], nkeys: int) -> dict[str, Any]:
    """A row as the flat mapping of `forms.build` and of the Parquet `data` (ADR 0027 decision 15): `g1…g3` (None —
    no key or the total), `kind`, `total`, `cutoff`, `cutoff_to`, `blocks`, `volume`, `tonnes` and for each element
    `<e>:wt`, `<e>:sum`, `<e>:metal`, `<e>:grade`, `<e>:missing_t`, `<e>:missing_n`."""
    g = list(row["g"]) + [None] * (3 - len(row["g"]))
    out: dict[str, Any] = {"g1": g[0], "g2": g[1], "g3": g[2], "total": row["total"], "kind": row["kind"],
                           "cutoff": row["cutoff"], "cutoff_to": row["cutoff_to"], "blocks": row["blocks"],
                           "volume": row["volume"], "tonnes": row["tonnes"]}  # fmt: skip
    for name, c in row["elements"].items():
        for key in ("wt", "sum", "metal", "grade", "missing_t", "missing_n"):
            out[f"{name}:{key}"] = c[key]
    return out


def totals_of(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The total row of the first table (for the tree of the project and the comparison): `{kind, cutoff, blocks,
    volume, tonnes, elements: {e: {grade, metal}}}`; None — no rows."""
    r = next((x for x in rows if x["total"]), None)
    if r is None:
        return None
    els = {k: {"grade": v["grade"], "metal": v["metal"]} for k, v in r["elements"].items()}
    return {"kind": r["kind"], "cutoff": r["cutoff"], "blocks": r["blocks"], "volume": r["volume"],
            "tonnes": r["tonnes"], "elements": els}  # fmt: skip


# --- the curve --------------------------------------------------------------------------------------------------------


def curve_of(sums: cp.Sums, plan: Plan) -> dict[str, Any] | None:
    """The curve «tonnage — grade» of the total (decision 11): per cut-off S(c) = {g_б ≥ c}: blocks, volume, tonnes and
    for each element ḡ and the metal. None — no curve."""
    if plan.curve is None:
        return None
    core = plan.core
    suf, cnt = sums.suffix(False)  # the value bins only (no «нет g_б»)
    idx = np.array([rk.cutoff_bin(core.edges, float(x)) for x in plan.curve], dtype=np.int64)
    q, k = suf[0, idx], cnt[0, idx]  # [points, Q], [points, K]
    elements: dict[str, dict[str, list[float | None]]] = {}
    for i, (name, km) in enumerate(zip(plan.names, plan.ks, strict=True)):
        wt, total = q[:, 2 + 3 * i], q[:, 3 + 3 * i]
        has = wt > 0.0
        with np.errstate(all="ignore"):
            elements[name] = {
                "grade": [_fin(float(t / w)) if h else None for t, w, h in zip(total, wt, has, strict=True)],
                "metal": [
                    _fin(float(t * km)) if h and km is not None else None for t, h in zip(total, has, strict=True)
                ],  # fmt: skip
            }
    return {"field": core.cutoff_field, "cutoffs": [float(x) for x in plan.curve],
            "blocks": [int(x) for x in k[:, 0]], "volume": [float(x) for x in q[:, 0]],
            "tonnes": [float(x) for x in q[:, 1]], "elements": elements}  # fmt: skip


def build(sums: cp.Sums, plan: Plan, *, max_rows: int | None = None) -> Result:
    """The result of the pass: the rows (the first `max_rows`, a preview; `rows_total` — all), the totals, the curve,
    the counters of the excluded blocks and the warnings of the plan (the control adds its own, `control.py`)."""
    rows: list[dict[str, Any]] = []
    for row in iter_rows(sums, plan):
        if max_rows is not None and len(rows) >= max_rows:
            break
        rows.append(row)
    total = row_count(sums, plan)
    totals = totals_of(rows) if rows else None
    warns = list(plan.warnings)
    if max_rows is not None and total > len(rows):
        warns.append(_warning("rows_truncated", f"Показано {len(rows)} строк из {total}.",
                              "Сохраните отчёт, чтобы смотреть все строки."))  # fmt: skip
    bad = not_finite(sums, plan)
    if bad:
        warns.append(_warning("value_not_finite", f"Значения вне диапазона чисел показаны пустыми: {', '.join(bad)}.",
                              "Проверьте содержания, плотность и объёмы блоков: слишком большие или слишком малые "
                              "значения."))  # fmt: skip
    return Result(rows, total, totals, curve_of(sums, plan), dict(sums.excluded), warns)
