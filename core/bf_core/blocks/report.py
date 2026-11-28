"""The grade–tonnage report of a block model (подшаг 17.28; ТЗ 7.31, 8.7.5; ADR 0023 §10, decision 15; the form
— `docs/progress/e17-api.md` §10; transfer of BF `api/app/blocks/report.py`).

    spec  = parse(model_id, body)                      # the body of `POST /blocks/{id}/report` (422 `bad_param`)
    plan  = make_plan(model, spec)                     # the fields, the density, the cut-offs (404 / 422)
    out   = grade_tonnage(model, plan, restrictor)     # the table, the curve, the counters (and the CSV)
    out   = run_report(engine, folder, params, tick=)  # the job `blocks.report`: the model, the restrictor, the report

Tonnes of a block = volume × FRAC × f × density: the volume of `Model` rows (`list_layout.volumes`, the FRAC field of
the model is in it), `f` ∈ (0; 1] the share the restrictor lets pass (the filter `filter_id` and the quick `restrict`
together as AND; a block with f = 0 is out — № 65), the density a field or a number. A grade is the mean weighted by
the tonnes of the blocks that HAVE the grade, the metal Σ tonnes × grade, `missing_tonnes` — the tonnes of the blocks
without the grade (a gap is not a zero: such a block is outside the mean and the metal; `missing_blocks` counts it).
A block is above a cut-off when its value of the cut-off field is ≥ the cut-off (as in BF); a block with no value there
is above none of them, a cut-off of 0 included (the calculator's rule: a gap compares false). The cut-off 0 therefore
gives the full tonnage of the blocks that have the value (the test of 7.31).

What is not counted (`excluded`, each block once, in this order): `filtered` — f = 0 or not among the selected
categories; `no_volume` — an empty FRAC; `no_density` — no density (empty or negative); `no_cutoff` — no value of the
cut-off field.

Defaults (7.31): `grades` — the fields `*_CUT`, else those with the role `grade`; `density` — the varying field `SG`,
else a varying field with the role `density` (a constant one — `DENSITY` = 2.6 — is skipped; name it to use it);
`cutoff_field` — the first of the grades (a field named but not among the grades is reported too); `cutoffs` — [0].
The curve — `curve` points from the minimum to the maximum of the cut-off field (the stats of the model; the whole
field, not the filter), of the total.

Metal: g/t and ppm → thousand troy ounces (31,1034768 g), % → t, ppb → kg; another unit — `metal` null.

Determinism (ТЗ 8.0.2): the sums are Neumaier accumulators per cut-off bin and group, fed in the order of the rows
(`report_kernels.py`), the bins are summed from the top by Neumaier too — the result is the same bit for bit with any
size of the portions. Stages of the job: «Чтение» (the model and the restrictor), «Расчёт». Errors — {code, message,
hint} in Russian: 422 `bad_param {param}`, `report_too_big`; 404 `block_field_not_found {param}`, `filter_not_found`.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import list_layout as ll
from . import restrict
from . import schema as bs
from .model import ANY, CHUNK, Model, field_not_found

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

KIND = "blocks.report"
TITLE = "Отчёт «Содержание–тоннаж»"
TROY_OUNCE_G = 31.1034768
STAGE_READ, STAGE_CALC = "Чтение", "Расчёт"
MAX_GRADES, MAX_CUTOFFS, MAX_CURVE, MAX_GROUPS, MAX_CELLS = 20, 200, 500, 1000, 2_000_000
EMPTY_GROUP = "(пусто)"
UNITS = {"volume": "м³", "tonnes": "т"}
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def bad_param(param: str, message: str, hint: str = "Проверьте параметры отчёта.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- units ----------------------------------------------------------------------------------------

_GT = {"g/t", "г/т", "ppm", "gpt"}


def _unit_kind(unit: str | None) -> str | None:
    u = (unit or "").strip().casefold().replace(" ", "")
    if u in _GT:
        return "gt"
    return {"%": "pct", "ppb": "ppb"}.get(u)


def metal_unit(unit: str | None) -> str | None:
    """The unit of the metal for the unit of a grade: g/t, ppm — «тыс. унц»; % — «т»; ppb — «кг»; else None."""
    return {"gt": "тыс. унц", "pct": "т", "ppb": "кг"}.get(_unit_kind(unit) or "")


def metal(total: float, unit: str | None) -> float | None:
    """The metal in the unit of `metal_unit` from Σ(tonnes × grade): g/t → grams → thousand ounces, % → tonnes,
    ppb (1e−9) → kilograms. None — the unit is unknown."""
    kind = _unit_kind(unit)
    if kind == "gt":
        return total / TROY_OUNCE_G / 1000.0
    if kind == "pct":
        return total / 100.0
    if kind == "ppb":
        return total * 1e-6
    return None


# --- the request ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    model_id: int
    grades: list[str] | None
    density: str | float | None
    cutoffs: list[float]
    cutoff_field: str | None
    filter_id: int | None
    restrict: dict[str, Any] | None
    categories: tuple[str, list[str]] | None
    groups: str | None
    curve: int
    csv: bool


def _number(raw: Any, param: str, what: str) -> float:
    if isinstance(raw, bool):
        raise bad_param(param, f"{what}: нужно число.")
    if isinstance(raw, str):
        try:
            raw = float(raw.strip().replace(",", "."))
        except ValueError:
            raise bad_param(param, f"{what}: нужно число.", "Например 0,5.") from None
    if not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise bad_param(param, f"{what}: нужно конечное число.")
    return float(raw)


def _name(raw: Any, param: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise bad_param(param, "Нужно имя поля.")
    return raw.strip()


def parse(model_id: int, body: Mapping[str, Any]) -> Spec:
    """The body of `POST /blocks/{id}/report` (e17-api §10) checked for its shapes; the fields are checked against
    the model by `make_plan`."""
    grades = body.get("grades")
    if grades is not None:
        if not isinstance(grades, list):
            raise bad_param("grades", "Содержания: нужен список полей.")
        if len(grades) > MAX_GRADES:
            raise bad_param("grades", f"Не больше {MAX_GRADES} содержаний.")
        grades = [_name(g, f"grades.{i}") for i, g in enumerate(grades)]
    density: str | float | None = body.get("density")
    if isinstance(density, str):
        text = density.strip()
        try:
            density = float(text.replace(",", "."))
        except ValueError:
            density = _name(text, "density")
    if density is not None and not isinstance(density, str):
        density = _number(density, "density", "Плотность")
        if density <= 0:
            raise bad_param("density", "Плотность должна быть больше нуля.", "Например 2,7 или имя поля SG.")
    cutoffs = body.get("cutoffs")
    if cutoffs is None:
        cutoffs = [0.0]
    if not isinstance(cutoffs, list) or not cutoffs or len(cutoffs) > MAX_CUTOFFS:
        raise bad_param("cutoffs", f"Борта: список из 1–{MAX_CUTOFFS} чисел.", "Например [0; 0,5; 1].")
    cuts = list(dict.fromkeys(_number(c, f"cutoffs.{i}", "Борт") for i, c in enumerate(cutoffs)))
    cutoff_field = body.get("cutoff_field")
    cutoff_field = None if cutoff_field is None else _name(cutoff_field, "cutoff_field")
    filter_id = body.get("filter_id")
    if filter_id is not None and (isinstance(filter_id, bool) or not isinstance(filter_id, int)):
        raise bad_param("filter_id", "Номер фильтра — целое число.")
    quick = body.get("restrict")
    if quick is not None and not isinstance(quick, dict):
        raise bad_param("restrict", "Ограничитель: нужен объект.")
    cats = body.get("categories")
    categories = None
    if cats is not None:
        values = cats.get("values") if isinstance(cats, dict) else None
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            raise bad_param("categories", "Отбор по категориям: {field, values: [подписи]}.")
        categories = (_name(cats.get("field"), "categories.field"), list(dict.fromkeys(values)))
    groups = body.get("groups")
    groups = None if groups is None else _name(groups, "groups")
    curve = body.get("curve", 50)
    if curve is None:
        curve = 0
    if isinstance(curve, bool) or not isinstance(curve, int) or curve < 0 or curve == 1 or curve > MAX_CURVE:
        raise bad_param("curve", f"Точек кривой: 0 (без кривой) или 2–{MAX_CURVE}.")
    want_csv = body.get("csv", False)
    if not isinstance(want_csv, bool):
        raise bad_param("csv", "CSV: да или нет.")
    return Spec(model_id, grades, density, cuts, cutoff_field, filter_id, quick, categories, groups, curve, want_csv)


# --- the plan -------------------------------------------------------------------------------------


@dataclass
class Plan:
    """What the report of one model counts (`make_plan`)."""

    grades: list[dict[str, Any]]  # {name, unit}; the cut-off field is among them
    cutoff_field: str
    cutoff_k: int  # its index among the grades
    density_field: str | None
    density_value: float | None
    cutoffs: list[float]  # as asked, without repeats
    user_edges: np.ndarray  # sorted, float64
    all_edges: np.ndarray  # user cut-offs and the points of the curve, sorted
    curve: np.ndarray | None  # the cut-offs of the curve
    groups: tuple[str, list[str]] | None
    categories: tuple[str, np.ndarray] | None  # field, codes
    frac: str | None
    reads: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _spec_of(model: Model, name: str, param: str, *, kind: str) -> dict[str, Any]:
    try:
        spec = model.field(name)
    except ApiError as e:
        if e.code != "block_field_not_found":
            raise
        raise field_not_found(name, param) from None
    if spec["kind"] != kind:
        what = "числовое" if kind == bs.NUMBER else "категорийное"
        raise bad_param(param, f"Поле «{spec['name']}» не {what}.", f"Выберите {what} поле.")
    return spec


def _varies(spec: Mapping[str, Any]) -> bool:
    st = spec.get("stats") or {}
    lo, hi = st.get("min"), st.get("max")
    return not (lo is not None and lo == hi)  # unknown stats — it may vary


def default_grades(model: Model) -> list[str]:
    nums = [f for f in model.fields if f["kind"] == bs.NUMBER]
    cut = [str(f["name"]) for f in nums if str(f["name"]).casefold().endswith("_cut")]
    return cut or [str(f["name"]) for f in nums if f.get("role") == "grade"]


def default_density(model: Model) -> str | None:
    """The varying field SG, else a varying field with the role `density`; a constant one is skipped."""
    nums = [f for f in model.fields if f["kind"] == bs.NUMBER and _varies(f)]
    for f in nums:
        if str(f["name"]).casefold() == "sg":
            return str(f["name"])
    return next((str(f["name"]) for f in nums if f.get("role") == "density"), None)


def _range_of(model: Model, name: str) -> tuple[float, float] | None:
    st = model.field(name).get("stats") or {}
    if st.get("min") is not None and st.get("max") is not None:
        return float(st["min"]), float(st["max"])
    lo, hi = math.inf, -math.inf
    for ch in model.iter([name]):
        v = ch.cols[name]
        v = v[np.isfinite(v)]
        if len(v):
            lo, hi = min(lo, float(v.min())), max(hi, float(v.max()))
    return (lo, hi) if lo <= hi else None


def make_plan(model: Model, spec: Spec) -> Plan:
    """The fields, the density and the cut-offs of `spec` on `model`; 404 `block_field_not_found {param}`, 422."""
    names = spec.grades if spec.grades is not None else default_grades(model)
    if not names:
        raise bad_param("grades", "В модели не найдено поле содержания.",
                        "Укажите содержания (например AU_CUT) или задайте полю роль «содержание».")  # fmt: skip
    grades = [_spec_of(model, n, f"grades.{i}", kind=bs.NUMBER) for i, n in enumerate(names)]
    cut_name = spec.cutoff_field or str(grades[0]["name"])
    cut_spec = _spec_of(model, cut_name, "cutoff_field", kind=bs.NUMBER)
    if str(cut_spec["name"]) not in [str(g["name"]) for g in grades]:
        grades.append(cut_spec)
    grades = [{"name": str(g["name"]), "unit": g.get("unit")} for g in grades]
    grades = list({g["name"]: g for g in grades}.values())  # each field once, in the order asked
    cutoff_k = [g["name"] for g in grades].index(str(cut_spec["name"]))
    dens_field: str | None = None
    dens_value: float | None = None
    density = spec.density if spec.density is not None else default_density(model)
    if density is None:
        raise bad_param("density", "Не найдена плотность: нет меняющегося поля SG или поля с ролью «плотность».",
                        "Укажите поле плотности или число, например 2,7.")  # fmt: skip
    if isinstance(density, str):
        dens_field = str(_spec_of(model, density, "density", kind=bs.NUMBER)["name"])
    else:
        dens_value = float(density)
    groups = None
    if spec.groups is not None:
        g = _spec_of(model, spec.groups, "groups", kind=bs.CATEGORY)
        labels = [str(x) for x in g.get("labels") or []]
        if len(labels) > MAX_GROUPS:
            raise bad_param("groups", f"В поле «{g['name']}» больше {MAX_GROUPS} категорий.", "Выберите другое поле.")
        groups = (str(g["name"]), labels)
    categories = None
    if spec.categories is not None:
        c = _spec_of(model, spec.categories[0], "categories.field", kind=bs.CATEGORY)
        labels = [str(x) for x in c.get("labels") or []]
        codes = []
        for i, v in enumerate(spec.categories[1]):
            if v not in labels:
                raise bad_param(f"categories.values.{i}", f"В поле «{c['name']}» нет категории «{v[:80]}».",
                                "Выберите подписи из списка категорий поля.")  # fmt: skip
            codes.append(labels.index(v))
        categories = (str(c["name"]), np.array(sorted(codes), dtype=np.int32))
    plan = Plan(grades, str(cut_spec["name"]), cutoff_k, dens_field, dens_value, list(spec.cutoffs),
                np.unique(np.array(spec.cutoffs, dtype=np.float64)), np.empty(0), None, groups, categories,
                None, [], [])  # fmt: skip
    frac = model.role_field("frac")
    plan.frac = None if frac is None else str(frac["name"])
    curve: np.ndarray | None = None
    if spec.curve:
        span = _range_of(model, plan.cutoff_field)
        if span is None:
            plan.notes.append(f"В поле «{plan.cutoff_field}» нет значений: кривая не построена.")
        else:
            curve = np.linspace(span[0], span[1], spec.curve)
    plan.curve = curve
    plan.all_edges = np.unique(np.concatenate([plan.user_edges, [] if curve is None else curve]))
    n_groups = 0 if groups is None else len(groups[1]) + 1
    if n_groups * (len(plan.user_edges) + 1) * (2 + 3 * len(grades)) > MAX_CELLS:
        raise ApiError(422, "report_too_big", "Слишком много групп, бортов и содержаний для одного отчёта.",
                       "Уменьшите число бортов или содержаний, или группируйте по другому полю.")  # fmt: skip
    wanted = [g["name"] for g in grades]
    for extra in (plan.density_field, plan.frac, None if categories is None else categories[0],
                  None if groups is None else groups[0]):  # fmt: skip
        if extra is not None:
            wanted.append(extra)
    plan.reads = list(dict.fromkeys(wanted))
    return plan


# --- the sums -------------------------------------------------------------------------------------


def _suffix(s: np.ndarray, c: np.ndarray, n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Σ of the bins ≥ j for j = 0…B (the bin B holds nothing): s, c [G, B, Q] (Neumaier sums and carries), n
    [G, B, K] counts → floats [G, B+1, Q], counts [G, B+1, K]. The bins are added from the top by Neumaier, so the
    answer does not depend on how the portions were cut."""
    g, b, q = s.shape
    out = np.zeros((g, b + 1, q))
    run_s, run_c = np.zeros((g, q)), np.zeros((g, q))
    for j in range(b - 1, -1, -1):
        for x in (s[:, j, :], c[:, j, :]):
            t = run_s + x
            run_c += np.where(np.abs(run_s) >= np.abs(x), (run_s - t) + x, (x - t) + run_s)
            run_s = t
        out[:, j, :] = run_s + run_c
    counts = np.zeros((g, b + 1, n.shape[2]), dtype=np.int64)
    counts[:, :b, :] = np.cumsum(n[:, ::-1, :], axis=1)[:, ::-1, :]
    return out, counts


def _entry(plan: Plan, q: np.ndarray, n: np.ndarray) -> dict[str, Any]:
    """The numbers of a table row from its sums q [Q] and counts n [K]."""
    grades: dict[str, Any] = {}
    for k, g in enumerate(plan.grades):
        wt, total, missing = float(q[2 + 3 * k]), float(q[3 + 3 * k]), float(q[4 + 3 * k])
        grades[g["name"]] = {
            "grade": total / wt if wt > 0 else None,
            "unit": g["unit"],
            "metal": metal(total, g["unit"]) if wt > 0 else None,
            "metal_unit": metal_unit(g["unit"]),
            "missing_tonnes": missing,
            "missing_blocks": int(n[1 + k]),
        }
    return {"blocks": int(n[0]), "volume": float(q[0]), "tonnes": float(q[1]), "grades": grades}


def grade_tonnage(
    model: Model,
    plan: Plan,
    r: restrict.Restrictor,
    *,
    chunk: int = CHUNK,
    tick: Tick = _noop,
    csv_text: bool = False,
) -> dict[str, Any]:
    """The report of `model` for `plan` and the restrictor `r` (module docstring): the result of the job."""
    from . import report_kernels as rk

    ng = len(plan.grades)
    q = 2 + 3 * ng
    b0, bg = len(plan.all_edges) + 1, len(plan.user_edges) + 1
    n_groups = 0 if plan.groups is None else len(plan.groups[1]) + 1
    s0, c0 = np.zeros((b0, q)), np.zeros((b0, q))
    n0 = np.zeros((b0, 1 + ng), dtype=np.int64)
    rows_g = max(n_groups * bg, 1)
    sg, cg = np.zeros((rows_g, q)), np.zeros((rows_g, q))
    cnt_g = np.zeros((rows_g, 1 + ng), dtype=np.int64)
    excluded = {"filtered": 0, "no_volume": 0, "no_density": 0, "no_cutoff": 0}
    names = [g["name"] for g in plan.grades]
    for ch, f in r.iter(model, extra=plan.reads, chunk=chunk):
        tick(0.05 + 0.9 * (ch.start + ch.n) / max(model.rows, 1), None)
        cols, n = ch.cols, ch.n
        vol = ll.volumes(model.grid, ch, cols[plan.frac] if plan.frac else None)
        ok = f > 0.0
        if plan.categories is not None:
            ok &= np.isin(cols[plan.categories[0]], plan.categories[1])
        excluded["filtered"] += n - int(np.count_nonzero(ok))
        keep = ok & ~np.isnan(vol)
        excluded["no_volume"] += int(np.count_nonzero(ok & ~keep))
        dens = cols[plan.density_field] if plan.density_field else np.full(n, plan.density_value)
        has = np.isfinite(dens) & (dens >= 0.0)
        excluded["no_density"] += int(np.count_nonzero(keep & ~has))
        keep &= has
        cut = cols[plan.cutoff_field]
        has = np.isfinite(cut)
        excluded["no_cutoff"] += int(np.count_nonzero(keep & ~has))
        keep &= has
        veff = np.where(keep, vol * f, 0.0)
        tonnes = np.where(keep, veff * np.where(keep, dens, 0.0), 0.0)
        bin0 = np.where(keep, np.searchsorted(plan.all_edges, cut, side="right"), -1).astype(np.int32)
        bing = np.searchsorted(plan.user_edges, cut, side="right").astype(np.int32)
        if plan.groups is None:
            grp = np.full(n, -1, dtype=np.int32)
        else:
            code = cols[plan.groups[0]]
            grp = np.where(code >= 0, code, len(plan.groups[1])).astype(np.int32)
        grades = np.ascontiguousarray(np.stack([cols[x] for x in names]), dtype=np.float64)
        rk.accumulate(bin0, bing, grp, veff, tonnes, grades, s0, c0, n0, sg, cg, cnt_g, bg)
    tick(0.97, None)
    tot, tot_n = _suffix(s0[None], c0[None], n0[None])
    rows: list[dict[str, Any]] = []
    for c in plan.cutoffs:
        j = int(np.searchsorted(plan.all_edges, c)) + 1
        rows.append({"group": None, "cutoff": c, **_entry(plan, tot[0, j], tot_n[0, j])})
    if plan.groups is not None:
        sub, sub_n = _suffix(sg.reshape(n_groups, bg, q), cg.reshape(n_groups, bg, q), cnt_g.reshape(n_groups, bg, -1))
        labels = [*plan.groups[1], EMPTY_GROUP]
        for gi, label in enumerate(labels):
            if sub_n[gi, 0, 0] == 0:
                continue  # no block of the group is counted
            for c in plan.cutoffs:
                j = int(np.searchsorted(plan.user_edges, c)) + 1
                rows.append({"group": label, "cutoff": c, **_entry(plan, sub[gi, j], sub_n[gi, j])})
    out: dict[str, Any] = {
        "model_id": model.id,
        "density": {"field": plan.density_field, "value": plan.density_value},
        "cutoff_field": plan.cutoff_field,
        "units": dict(UNITS),
        "rows": rows,
        "curve": _curve(plan, tot[0], tot_n[0]),
        "excluded": excluded,
        "warnings": list(dict.fromkeys([*plan.notes, *r.warnings])),
        "csv": None,
    }
    if csv_text:
        out["csv"] = to_csv(out)
    return out


def _curve(plan: Plan, tot: np.ndarray, tot_n: np.ndarray) -> dict[str, Any] | None:
    if plan.curve is None:
        return None
    k = plan.cutoff_k
    unit = plan.grades[k]["unit"]
    grade: list[float | None] = []
    tonnes: list[float] = []
    mt: list[float | None] = []
    for c in plan.curve:
        j = int(np.searchsorted(plan.all_edges, c)) + 1
        wt, total = float(tot[j, 2 + 3 * k]), float(tot[j, 3 + 3 * k])
        tonnes.append(float(tot[j, 1]))
        grade.append(total / wt if wt > 0 else None)
        mt.append(metal(total, unit) if wt > 0 else None)
    return {"field": plan.cutoff_field, "unit": unit, "cutoffs": [float(c) for c in plan.curve], "tonnes": tonnes,
            "grade": grade, "metal": mt, "metal_unit": metal_unit(unit)}  # fmt: skip


# --- CSV ------------------------------------------------------------------------------------------


def _fmt(x: float | int | None) -> str:
    if x is None:
        return ""
    return str(x) if isinstance(x, int) else repr(float(x)).replace(".", ",")


def to_csv(result: Mapping[str, Any]) -> str:
    """The table as text: «;» between the cells, the decimal comma, CRLF; an empty cell is a gap (not a zero)."""
    rows = result["rows"]
    names = list(rows[0]["grades"]) if rows else []
    head = ["Группа", "Борт", "Блоков", "Объём, м³", "Тоннаж, т"]
    for name in names:
        g = rows[0]["grades"][name]
        unit, mu = g["unit"], g["metal_unit"]
        head += [f"{name}, содержание" + (f" ({unit})" if unit else ""), f"{name}, металл" + (f" ({mu})" if mu else ""),
                 f"{name}, без содержания, т", f"{name}, блоков без содержания"]  # fmt: skip
    buf = io.StringIO()
    out = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    out.writerow(head)
    for row in rows:
        line: list[Any] = ["" if row["group"] is None else row["group"], _fmt(row["cutoff"]), _fmt(row["blocks"]),
                           _fmt(row["volume"]), _fmt(row["tonnes"])]  # fmt: skip
        for name in names:
            g = row["grades"][name]
            line += [_fmt(g["grade"]), _fmt(g["metal"]), _fmt(g["missing_tonnes"]), _fmt(g["missing_blocks"])]
        out.writerow(line)
    return buf.getvalue()


# --- the job --------------------------------------------------------------------------------------


def run_report(
    engine: sa.Engine, folder: Any, params: Mapping[str, Any], *, tick: Tick = _noop, chunk: int = CHUNK
) -> dict[str, Any]:
    """The body of `blocks.report` (the handler and the tests). `params` — the body of the request and `model_id`."""
    from . import calc_job

    spec = parse(int(params["model_id"]), params)
    store = BlobStore(folder)
    tick(0.02, STAGE_READ)
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, spec.model_id, ANY)
        plan = make_plan(model, spec)
        tree = calc_job.rules_tree(conn, spec.filter_id, spec.restrict)
        r = restrict.prepare(model, tree, conn=conn, store=store)
    tick(0.05, STAGE_CALC)
    return grade_tonnage(model, plan, r, chunk=chunk, tick=tick, csv_text=spec.csv)


def memory_mb(params: dict[str, Any]) -> int:
    """A portion of 2²⁰ rows: the columns read, the volumes, the weights and the bins."""
    return 640


def warm() -> dict[str, float]:
    """Compiles the kernel of the report (`blocks/warm.py` calls it as a step)."""
    import time

    from . import report_kernels as rk

    t0 = time.perf_counter()
    z = np.zeros((1, 5))
    n = np.zeros((1, 2), dtype=np.int64)
    one = np.zeros(1)
    rk.accumulate(np.zeros(1, np.int32), np.zeros(1, np.int32), np.full(1, -1, np.int32), one, one,
                  np.zeros((1, 1)), z.copy(), z.copy(), n.copy(), z.copy(), z.copy(), n.copy(), 1)  # fmt: skip
    return {"report": time.perf_counter() - t0}


@handler(KIND, TITLE, queue="quick", estimate_memory=memory_mb)
def report_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        return run_report(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None


__all__: Sequence[str] = ("KIND", "Spec", "Plan", "parse", "make_plan", "grade_tonnage", "run_report", "to_csv")
