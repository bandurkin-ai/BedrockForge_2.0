"""Parameters of the estimation (подшаг 18.1; ТЗ 8.8.1–8.8.2; 7.35 Д-27…Д-30, Д-38; ADR 0024 §2–7, решения 4–11):
the body of `POST /estimations`, `PUT /estimations/{id}` and of a one-off estimation in `/estimate/preview`,
`/xval`, `/kna`, checked against the ranges of 8.8.2 (docs/progress/e18-api.md §3–4). Pure: no database, no numpy
(the router loads it); the ids and the field names are checked against the project by `store.py` (18.11). The
parts — `spec_parts.py`.

    spec = parse_estimation(body)     # EstimationSpec; ApiError 422 `bad_param` {param}, `filter_rules`
                                      # (target.rules), `variogram_scale` (Д-38), `no_structures`
    spec = spec.resolved(trend=form)  # the search of `from: trend` (the form of `GET /trends/{id}`)
    meta_part = spec.canonical()      # what `objects(kind="estimation").meta` keeps besides origin, inputs, last_run
    cols = spec.columns()             # the output fields: [{name, key, dtype, role, unit, labels?}] (№ 81, Д-27)
    plan = parse_plan(body); run = parse_run(body); at = parse_at(body["at"]); ins = parse_inspect(body)

A key the body leaves out (or null, or "") takes its default (8.8.2): IDW p = 2, discretisation 4×4×2, three passes
×1, ×2, ×3 with n_min / n_max 4 / 24, n_скв, n_окт / k_окт, n_скв_min 0 (off), no rule of «ураганные пробы»
(Д-29), the search from the variogram model (its angles and the ranges of the structure with the largest a1, the
first of equal ones), the prefix «<ПОЛЕ>_<МЕТОД>_», STATUS always among the outputs (Д-27). `from: model` is
resolved as soon as the snapshot of the model is known (`parse_estimation` with `variogram.snapshot`, or the
store after it copies the model); `from: trend` — by `resolved(trend=…)`. Until then `search.ang*`, `search.r*`
are null. A key the body does not know is ignored (the stored `meta` with `origin`, `inputs`, `last_run` parses
as it is). `param` of an error is the path of the key: "disc.2", "samples.soft.dist", "method.ik.thresholds.1",
"variograms.0.model_id", "search.passes.1.hy.q", "outputs.fields.3".
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from bf_core.geostat.variogram.spec import bad, choice

from .spec_parts import (
    ALLOWED,
    DEFAULT_FIELDS,
    DOMAIN_VALUES_MAX,
    DTYPES,
    PASS_FACTORS,
    PASSES_MAX,
    POWER,
    PREFIX_MAX,
    RANGES,
    STATUS_LABELS,
    THRESHOLDS_MAX,
    Method,
    Outputs,
    Pass,
    SampleSel,
    Search,
    Target,
    VarioRef,
    _number,
    id_of,
    int_of,
    list_of,
    obj,
    parse_method,
    parse_outputs,
    parse_samples,
    parse_search,
    parse_target,
    parse_vario,
    search_from_model,
    search_from_trend,
    text_of,
)

SUBS = ("parent", "own")
DISC = (4, 4, 2)
PLAN_ITEMS_MAX = 100
ALGO = "est-1"
#: what a stored `meta` has besides the parameters (written by the store and the job)
META_ONLY = ("v", "origin", "inputs", "algo", "versions", "last_run", "created_at")
#: Д-29: the order of the selection, written into the parameters and the log
SELECTION_ORDER = ("ellipsoid", "hy", "sort_hs", "n_max_hole_octants", "n_min")


@dataclass(frozen=True)
class EstimationSpec:
    name: str | None  # None — the store gives «‹поле› · ‹метод› · ‹домен›»
    model_id: int  # the block model
    target: Target
    sub: str  # parent | own
    disc: tuple[int, int, int]  # (1, 1, 1) — point
    samples: SampleSel
    method: Method
    variogram: VarioRef | None  # SK, OK, IK `median`; optional for NN, IDW (the search only)
    variograms: tuple[VarioRef, ...] | None  # IK `per`: one per threshold, in their order
    trend_id: int | None  # object_refs role "trend"
    search: Search
    outputs: Outputs

    @property
    def point(self) -> bool:
        return self.disc == (1, 1, 1)

    @property
    def resolved_search(self) -> bool:
        return self.search.angles is not None and self.search.radii is not None

    def search_variogram(self) -> VarioRef | None:
        """The model whose ranges `from: model` takes: `variogram`, else the middle threshold of `variograms`."""
        if self.variogram is not None:
            return self.variogram
        return None if not self.variograms else self.variograms[len(self.variograms) // 2]

    def resolved(self, *, trend: Mapping[str, Any] | None = None) -> EstimationSpec:
        """The search filled in: `from: model` — from the snapshot of the model (unchanged without one), `from:
        trend` — from the form of the trend (unchanged without it); `manual` — as it is."""
        s = self.search
        if s.source == "model":
            ref = self.search_variogram()
            if ref is None or ref.snapshot is None:
                return self
            angles, radii = search_from_model(ref.snapshot)
        elif s.source == "trend":
            if trend is None:
                return self
            angles, radii = search_from_trend(trend, None if s.radii is None else s.radii[0])
        else:
            return self
        a3 = (angles[0], angles[1], angles[2])
        r3 = (radii[0], radii[1], radii[2])
        return replace(self, search=replace(s, angles=a3, radii=r3))

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "model_id": self.model_id, "target": self.target.canonical(), "sub": self.sub,
                "disc": list(self.disc), "samples": self.samples.canonical(), "method": self.method.canonical(),
                "variogram": None if self.variogram is None else self.variogram.canonical(),
                "variograms": None if self.variograms is None else [v.canonical() for v in self.variograms],
                "trend_id": self.trend_id, "search": self.search.canonical(),
                "outputs": self.outputs.canonical()}  # fmt: skip

    def hash_params(self) -> dict[str, Any]:
        """What «устарел» compares besides the inputs: the parameters without the name."""
        return {"algo": ALGO, **{k: v for k, v in self.canonical().items() if k != "name"}}

    def columns(self) -> list[dict[str, Any]]:
        """The output fields of the block model: [{name, key, dtype, role, unit, description}]; role «grade» —
        EST, ETYPE (unit of the samples); AD, DMIN — «м»; STATUS — i1 with the codes of Д-27 in `description`."""
        out: list[dict[str, Any]] = []
        for key in self.outputs.fields:
            names = [f"P{k + 1}" for k in range(len(self.method.thresholds))] if key == "P" else [key]
            grade = key in ("EST", "ETYPE")
            unit = self.samples.unit if grade else ("м" if key in ("AD", "DMIN") else None)
            desc = "; ".join(f"{i} — {x}" for i, x in enumerate(STATUS_LABELS)) if key == "STATUS" else None
            out += [{"name": f"{self.outputs.prefix}{n}", "key": n, "dtype": DTYPES[key],
                     "role": "grade" if grade else None, "unit": unit, "description": desc} for n in names]  # fmt: skip
        return out


def _disc(raw: Any) -> tuple[int, int, int]:
    if raw is None or raw == "":
        return DISC
    if not isinstance(raw, list) or len(raw) != 3:
        raise bad("bad_param", "Дискретизация: нужны три числа (X, Y, Z).", "Например, 4 × 4 × 2.", "disc")
    a, b, c = (int_of(x, "disc", DISC[i], f"disc.{i}") for i, x in enumerate(raw))
    return a, b, c


def _variograms(b: Mapping[str, Any], method: Method) -> tuple[VarioRef | None, tuple[VarioRef, ...] | None]:
    kind = method.kind
    variogram = None if b.get("variogram") in (None, "") else parse_vario(b["variogram"], "variogram")
    variograms = None
    if b.get("variograms") is not None:
        given = list_of(b["variograms"], "Модели индикаторов", "variograms", THRESHOLDS_MAX)
        if kind != "ik" or method.ik_models != "per":
            raise bad("bad_param", "Модели по порогам задаются только для IK «модель на порог».",
                      "Уберите список моделей или выберите IK.", "variograms")  # fmt: skip
        if len(given) != len(method.thresholds):
            raise bad("bad_param", f"Нужно {len(method.thresholds)} моделей — по одной на порог.",
                      "Выберите модель для каждого порога.", "variograms")  # fmt: skip
        variograms = tuple(parse_vario(v, f"variograms.{i}", method.thresholds[i]) for i, v in enumerate(given))
    needs_one = kind in ("sk", "ok") or (kind == "ik" and method.ik_models == "median")
    if needs_one and variogram is None:
        raise bad("bad_param", "Не выбрана модель вариограммы.", "Выберите сохранённую модель вариограммы.",
                  "variogram.model_id")  # fmt: skip
    if kind == "ik" and method.ik_models == "per" and variograms is None:
        raise bad("bad_param", "Не выбраны модели индикаторов.", "Выберите модель для каждого порога.", "variograms")
    return variogram, variograms


def parse_estimation(body: Mapping[str, Any]) -> EstimationSpec:
    """The body of a row of the plan (or its stored `meta`) → the checked parameters; `from: model` resolved when
    the snapshot is in the body."""
    b = obj(body, "Параметры оценки", "body")
    for key in META_ONLY:
        b.pop(key, None)
    name = text_of(b.get("name"), "Имя", "name", required=False)
    model_id = id_of(b.get("model_id"), "блочная модель", "model_id")
    assert model_id is not None
    samples = parse_samples(b.get("samples"))
    method = parse_method(b.get("method"))
    variogram, variograms = _variograms(b, method)
    trend_id = id_of(b.get("trend_id"), "тренд", "trend_id", required=False)
    has_model = variogram is not None or bool(variograms)
    search = parse_search(b.get("search"), has_model=has_model, trend_id=trend_id)
    hy = any(p.hy is not None for p in search.passes)
    spec = EstimationSpec(
        name=name, model_id=model_id, target=parse_target(b.get("target")),
        sub=choice(b.get("sub"), SUBS, "parent", "Субблоки", "sub"), disc=_disc(b.get("disc")), samples=samples,
        method=method, variogram=variogram, variograms=variograms, trend_id=trend_id, search=search,
        outputs=parse_outputs(b.get("outputs"), method.kind, samples.field, hy=hy),
    )  # fmt: skip
    return spec.resolved()


# --- the plan, the run, the block of a preview or of the inspector ---------------------------------------------


@dataclass(frozen=True)
class PlanSpec:
    name: str | None
    model_id: int
    items: tuple[int, ...]  # ids of the estimations in the order of the rows

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "model_id": self.model_id, "items": list(self.items)}


def _ids(raw: Any, label: str, param: str) -> tuple[int, ...]:
    given = list_of(raw if raw is not None else [], label, param, PLAN_ITEMS_MAX)
    out: list[int] = []
    for i, x in enumerate(given):
        v = id_of(x, "строка плана", f"{param}.{i}")
        assert v is not None
        if v in out:
            raise bad("bad_param", "Строка плана повторяется.", "Уберите повтор.", f"{param}.{i}")
        out.append(v)
    return tuple(out)


def parse_plan(body: Mapping[str, Any]) -> PlanSpec:
    b = obj(body, "План оценки", "body")
    model_id = id_of(b.get("model_id"), "блочная модель", "model_id")
    assert model_id is not None
    name = text_of(b.get("name"), "Имя", "name", required=False)
    return PlanSpec(name, model_id, _ids(b.get("items"), "Строки", "items"))


@dataclass(frozen=True)
class RunSpec:
    plan_id: int | None
    estimation_ids: tuple[int, ...]  # () with plan_id
    expected_version: int  # the version of the block model
    replace: bool  # replace the output fields that exist


def _version(raw: Any, param: str = "expected_version") -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw != int(raw) or raw < 1:
        raise bad("bad_param", "Не задана версия блочной модели.", "Перечитайте модель и запустите снова.", param)
    return int(raw)


def parse_run(body: Mapping[str, Any]) -> RunSpec:
    """`POST /estimate/run {plan_id | estimation_ids, expected_version, replace?}`."""
    b = obj(body, "Запуск", "body")
    plan_id = id_of(b.get("plan_id"), "план", "plan_id", required=False)
    ids = _ids(b.get("estimation_ids"), "Строки", "estimation_ids")
    if (plan_id is None) == (not ids):
        raise bad("bad_param", "Задайте план или строки плана — что-то одно.", "Выберите план или строки.", "plan_id")
    rep = b.get("replace", False)
    if not isinstance(rep, bool):
        raise bad("bad_param", "«Заменить поля»: ожидается «да» или «нет».", "Отметьте или снимите галочку.", "replace")
    return RunSpec(plan_id, ids, _version(b.get("expected_version")), rep)


def _index(raw: Any, label: str, param: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw != int(raw) or raw < 0:
        raise bad("bad_param", f"{label}: целое ≥ 0.", "Щёлкните блок в 3D.", param)
    return int(raw)


def parse_at(raw: Any, param: str = "at") -> dict[str, Any]:
    """The block: {row: int ≥ 0} (a row of the model), {ijk: [i, j, k]} (a parent) or {xyz: [x, y, z]} (the row that
    holds the point) — exactly one."""
    a = obj(raw, "Блок", param)
    given = [k for k in ("row", "ijk", "xyz") if a.get(k) is not None]
    if len(given) != 1:
        raise bad("bad_param", "Задайте блок: строку, индексы или точку — что-то одно.", "Щёлкните блок в 3D.", param)
    key = given[0]
    if key == "row":
        return {"row": _index(a["row"], "Номер строки блока", f"{param}.row")}
    v = a[key]
    if not isinstance(v, list) or len(v) != 3:
        raise bad("bad_param", "Блок: нужны три числа.", "Щёлкните блок в 3D.", f"{param}.{key}")
    if key == "ijk":
        return {"ijk": [_index(x, "Индекс блока", f"{param}.ijk.{i}") for i, x in enumerate(v)]}
    return {"xyz": [_number(x, "Координата", f"{param}.xyz.{i}") for i, x in enumerate(v)]}


@dataclass(frozen=True)
class InspectSpec:
    """Д-30 `estimate.inspect {model_id, block: [i, j, k] | xyz: […] | row, params_id?}`."""

    model_id: int
    at: dict[str, Any]  # the form of `parse_at`
    params_id: int | None  # None — every estimation whose fields the model has (by `source.params`)


def parse_inspect(body: Mapping[str, Any]) -> InspectSpec:
    b = obj(body, "Инспектор блока", "body")
    model_id = id_of(b.get("model_id"), "блочная модель", "model_id")
    assert model_id is not None
    given = [k for k in ("block", "xyz", "row") if b.get(k) is not None]
    if len(given) != 1:
        raise bad("bad_param", "Задайте блок: индексы, точку или строку — что-то одно.", "Щёлкните блок в 3D.", "block")
    key = given[0]
    place: dict[str, Any]
    if key == "row":
        place = {"row": _index(b["row"], "Номер строки блока", "row")}
    else:
        v = b[key]
        if not isinstance(v, list) or len(v) != 3:
            raise bad("bad_param", "Блок: нужны три числа.", "Щёлкните блок в 3D.", key)
        if key == "block":
            place = {"ijk": [_index(x, "Индекс блока", f"block.{i}") for i, x in enumerate(v)]}
        else:
            place = {"xyz": [_number(x, "Координата", f"xyz.{i}") for i, x in enumerate(v)]}
    return InspectSpec(model_id, place, id_of(b.get("params_id"), "параметры оценки", "params_id", required=False))


# --- the forms of the window ---------------------------------------------------------------------------------


def defaults_json() -> dict[str, Any]:
    """The defaults of 8.8.2 the wizard fills its form with (`GET /estimate/defaults` → `params`)."""
    return {"sub": "parent", "disc": list(DISC),
            "method": {"kind": "ok", "power": POWER, "mean": {"mode": "declus", "value": None},
                       "ik": {"models": "per", "class_means": "declus"}},
            "search": {"from": "model", "passes": [Pass(f).canonical() for f in PASS_FACTORS],
                       "order": list(SELECTION_ORDER)},
            "outputs": {"fields": {k: list(v) for k, v in DEFAULT_FIELDS.items()},
                        "allowed": {k: list(v) for k, v in ALLOWED.items()}, "dtypes": dict(DTYPES),
                        "status": list(STATUS_LABELS)},
            "limits": {"passes": PASSES_MAX, "thresholds": THRESHOLDS_MAX, "prefix": PREFIX_MAX,
                       "domain_values": DOMAIN_VALUES_MAX, "plan_items": PLAN_ITEMS_MAX}}  # fmt: skip


def ranges_json() -> dict[str, Any]:
    """The ranges for the form: key → {lo, hi, lo_open, integer, unit} (null — unbounded)."""
    return {k: {"lo": None if math.isinf(r.lo) else r.lo, "hi": None if math.isinf(r.hi) else r.hi,
                "lo_open": r.lo_open, "integer": r.integer, "unit": r.unit.strip()}
            for k, r in RANGES.items()}  # fmt: skip
