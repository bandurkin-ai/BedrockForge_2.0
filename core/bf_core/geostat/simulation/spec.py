"""Parameters of a simulation (подшаг 18.32; ТЗ 8.8.1; 7.35 Д-23, Д-38; ADR 0024 §9; docs/progress/e18-api.md §9): the
body of `POST /simulations`, `PUT /simulations/{id}`, `POST /simulate/run`, `POST /simulations/{id}/post`, checked
against the ranges of Д-23. Pure: no database, no numpy (the router loads it). The ids and the names of the fields are
checked against the project by `store_check.py`. The parts — those of the estimation (`estimate/spec_parts.py`):
`parse_samples`, `parse_target`, `parse_vario(…, simulation=True)`, `parse_search`.

    spec = parse_simulation(body)     # SimulationSpec; ApiError 422 `bad_param` {param}, `not_ready` (SIS, co-SGS)
    meta_part = spec.canonical()      # what `objects(kind="simulation").meta` keeps besides origin, inputs, last_run
    run = parse_run(body)             # RunSpec {simulation | simulation_id, expected_version?}
    post = parse_post(body)           # PostSpec {expected_version, prefix?, stats, thresholds, replace}

A key the body leaves out (or null, or "") takes its default (Д-23): `seed` 12345 (0 … 2⁶³−1), `n_real` 20 (1–100),
`nodes` 12 (0–64), one pass of the search with `n_max` 24 (1–128; the factor scales the radii; `n_min` is kept but SGS
takes what is there), the tails of the back-transform «linear» (`nscore.ltail`, `utail`: linear | power | none —
`hyperbolic` for the upper one; `ltpar`, `utpar` of a power tail > 0, of a hyperbolic one ≥ 1). The search comes from
the variogram model (`from: model`, the structure with the largest a1) or is manual. The kind: `sgs` (no secondary),
`cosgs` (with `secondary`), `sis`; the last two are not connected yet (18.33, 18.34) — 422 `not_ready`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate.spec_parts import (
    SampleSel,
    Search,
    Target,
    VarioRef,
    default_prefix,
    id_of,
    list_of,
    obj,
    parse_samples,
    parse_search,
    parse_target,
    parse_vario,
    search_from_model,
    text_of,
)
from bf_core.geostat.variogram.spec import Range, _number, _span, bad, choice

KINDS = ("sgs", "sis", "cosgs")
RUNNABLE = ("sgs",)  # the kinds this build runs
ALGO = "sim-1"
SEED_MAX = 2**63 - 1
RANGES: dict[str, Range] = {
    "seed": Range(0, SEED_MAX, "Зерно", integer=True),
    "n_real": Range(1, 100, "Число реализаций", integer=True),
    "nodes": Range(0, 64, "Число ранее смоделированных узлов", integer=True),
    "n_max": Range(1, 128, "Число проб в окрестности узла n_max", integer=True),
    "rho": Range(-1.0, 1.0, "Коэффициент корреляции ρ"),
}
DEFAULTS = {"seed": 12345, "n_real": 20, "nodes": 12}
LOWER = ("linear", "power", "none")
UPPER = ("linear", "power", "hyperbolic", "none")
#: what `simulate.post` can write: the key → the suffix of the field (a probability — `PROB_GT_<t>`)
STATS = ("mean", "std", "p10", "p50", "p90", "prob")
STAT_SUFFIX = {"mean": "MEAN", "std": "STD", "p10": "P10", "p50": "P50", "p90": "P90"}
STAT_DTYPE = {"mean": "f8", "std": "f8", "p10": "f8", "p50": "f8", "p90": "f8", "prob": "f4"}
BASE_STATS = ("mean", "std", "p10", "p50", "p90")
THRESHOLDS_MAX = 20
PREFIX_MAX = 40


def not_ready(kind: str) -> ApiError:
    what = "Индикаторное моделирование (SIS)" if kind == "sis" else "Ко-SGS"
    return ApiError(422, "not_ready", f"{what} ещё не подключено в этой сборке.", "Обновите программу.",
                    {"param": "kind"})  # fmt: skip


def whole(raw: Any, key: str, param: str) -> int | None:
    """An integer of `RANGES[key]`: absent, null or "" — None; a number with a fraction or off the range — 422."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    r = RANGES[key]
    if isinstance(raw, float) and not math.isfinite(raw):
        raise bad("bad_param", f"{r.label}: нужно целое число.", f"Задайте целое число {_span(r)}.", param)
    value = _number(raw, r.label, param)
    if value != math.floor(value):
        raise bad("bad_param", f"{r.label}: нужно целое число.", f"Задайте целое число {_span(r)}.", param)
    if not r.lo <= value <= r.hi:
        raise bad("bad_param", f"{r.label}: значение вне диапазона {_span(r)}.", f"Задайте {_span(r)}.", param)
    return int(value)


@dataclass(frozen=True)
class NScore:
    """The tails of the back-transform (as `stats.transforms.backtr`); `none` — no tail."""

    ltail: str = "linear"
    utail: str = "linear"
    ltpar: float | None = None
    utpar: float | None = None

    def canonical(self) -> dict[str, Any]:
        return {"ltail": self.ltail, "utail": self.utail, "ltpar": self.ltpar, "utpar": self.utpar}

    def tails(self) -> tuple[str | None, str | None]:
        """(ltail, utail) as `backtr` takes them: `none` → None."""
        return (None if self.ltail == "none" else self.ltail, None if self.utail == "none" else self.utail)


def parse_nscore(raw: Any, param: str = "nscore") -> NScore:
    n = obj(raw, "Обратное преобразование", param)
    lt = choice(n.get("ltail"), LOWER, "linear", "Нижний хвост", f"{param}.ltail")
    ut = choice(n.get("utail"), UPPER, "linear", "Верхний хвост", f"{param}.utail")
    lp = None if n.get("ltpar") in (None, "") else _number(n["ltpar"], "Показатель нижнего хвоста", f"{param}.ltpar")
    up = None if n.get("utpar") in (None, "") else _number(n["utpar"], "Показатель верхнего хвоста", f"{param}.utpar")
    if lt == "power" and (lp is None or not lp > 0.0):
        raise bad("bad_param", "Для степенного нижнего хвоста нужен показатель больше нуля.", "Задайте, например, 2.",
                  f"{param}.ltpar")  # fmt: skip
    if ut == "power" and (up is None or not up > 0.0):
        raise bad("bad_param", "Для степенного верхнего хвоста нужен показатель больше нуля.", "Задайте, например, 2.",
                  f"{param}.utpar")  # fmt: skip
    if ut == "hyperbolic" and (up is None or not up >= 1.0):
        raise bad("bad_param", "Для гиперболического верхнего хвоста нужен показатель не меньше 1.",
                  "Задайте, например, 1,5.", f"{param}.utpar")  # fmt: skip
    return NScore(lt, ut, lp if lt == "power" else None, up if ut in ("power", "hyperbolic") else None)


@dataclass(frozen=True)
class Secondary:
    """Co-SGS (18.34): the secondary field of the block model and ρ (None — computed from the collocated scores)."""

    field: str
    rho: float | None

    def canonical(self) -> dict[str, Any]:
        return {"field": self.field, "rho": self.rho, "rho_used": None}


def parse_secondary(raw: Any, param: str = "secondary") -> Secondary | None:
    if raw is None or raw == "":
        return None
    s = obj(raw, "Вторичная переменная", param)
    field = text_of(s.get("field"), "Поле вторичной переменной", f"{param}.field", 100)
    assert field is not None
    rho = None
    if s.get("rho") not in (None, ""):
        rho = _number(s["rho"], RANGES["rho"].label, f"{param}.rho")
        if not -1.0 <= rho <= 1.0:
            raise bad("bad_param", f"{RANGES['rho'].label}: значение вне диапазона {_span(RANGES['rho'])}.",
                      f"Задайте {_span(RANGES['rho'])}.", f"{param}.rho")  # fmt: skip
    return Secondary(str(field), rho)


@dataclass(frozen=True)
class SimulationSpec:
    name: str | None  # None — the store gives «‹поле› · ‹ВИД› · ‹домен›»
    kind: str  # sgs | sis | cosgs
    model_id: int  # the block model
    target: Target
    samples: SampleSel
    variogram: VarioRef | None  # the model in normal scores (`scale: y`)
    variograms: tuple[VarioRef, ...] | None  # SIS: one per threshold (18.33)
    method: dict[str, Any] | None  # SIS: {ik: {thresholds | categories, models}} (18.33)
    search: Search  # one pass
    nodes: int
    seed: int
    n_real: int
    secondary: Secondary | None
    nscore: NScore

    @property
    def trend_id(self) -> int | None:
        """A simulation has no trend; the inputs of the graph read `spec.trend_id` as those of an estimation do."""
        return None

    @property
    def resolved_search(self) -> bool:
        return self.search.angles is not None and self.search.radii is not None

    def resolved(self) -> SimulationSpec:
        """The search filled in from the snapshot of the model (unchanged without one; `manual` — as it is)."""
        s = self.search
        ref = self.variogram
        if s.source != "model" or ref is None or ref.snapshot is None:
            return self
        angles, radii = search_from_model(ref.snapshot)
        return replace(self, search=replace(s, angles=(angles[0], angles[1], angles[2]),
                                            radii=(radii[0], radii[1], radii[2])))  # fmt: skip

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "model_id": self.model_id, "target": self.target.canonical(),
                "samples": self.samples.canonical(),
                "variogram": None if self.variogram is None else self.variogram.canonical(),
                "variograms": None if self.variograms is None else [v.canonical() for v in self.variograms],
                "method": self.method, "search": self.search.canonical(), "nodes": self.nodes, "seed": self.seed,
                "n_real": self.n_real,
                "secondary": None if self.secondary is None else self.secondary.canonical(),
                "nscore": self.nscore.canonical()}  # fmt: skip

    def hash_params(self) -> dict[str, Any]:
        """What «устарел» compares besides the inputs: the parameters without the name."""
        return {"algo": ALGO, **{k: v for k, v in self.canonical().items() if k != "name"}}


#: what a stored `meta` has besides the parameters (written by the store and the job)
META_ONLY = ("v", "origin", "inputs", "algo", "versions", "last_run", "created_at")


def parse_simulation(body: Mapping[str, Any]) -> SimulationSpec:
    """The body of a simulation (or its stored `meta`) → the checked parameters; the search resolved when the
    snapshot of the model is in the body."""
    b = obj(body, "Параметры моделирования", "body")
    for key in META_ONLY:
        b.pop(key, None)
    name = text_of(b.get("name"), "Имя", "name", required=False)
    secondary = parse_secondary(b.get("secondary"))
    kind = choice(b.get("kind"), KINDS, "cosgs" if secondary is not None else "sgs", "Вид моделирования", "kind")
    if kind not in RUNNABLE:
        raise not_ready(kind)
    if secondary is not None and kind == "sgs":
        raise bad("bad_param", "Вторичная переменная задана для SGS без неё.", "Выберите ко-SGS или уберите её.",
                  "secondary")  # fmt: skip
    model_id = id_of(b.get("model_id"), "блочная модель", "model_id")
    assert model_id is not None
    samples = parse_samples(b.get("samples"))
    if b.get("variogram") in (None, ""):
        raise bad("bad_param", "Не выбрана модель вариограммы.", "Выберите сохранённую модель в нормальных оценках.",
                  "variogram.model_id")  # fmt: skip
    variogram = parse_vario(b["variogram"], "variogram", simulation=True)
    raw_search = obj(b.get("search"), "Поиск", "search")
    if raw_search.get("passes") is None:
        raw_search["passes"] = [{}]
    passes = list_of(raw_search["passes"], "Проходы", "search.passes", 1)
    if len(passes) != 1:
        raise bad("bad_param", "Для моделирования один проход поиска.", "Оставьте один проход.", "search.passes")
    search = parse_search(raw_search, has_model=True, trend_id=None)
    if search.source == "trend":
        raise bad("bad_param", "Эллипсоид по тренду для моделирования не поддерживается.",
                  "Выберите модель вариограммы или задайте радиусы вручную.", "search.from")  # fmt: skip
    n_max = search.passes[0].n_max
    if n_max > RANGES["n_max"].hi:
        raise bad("bad_param", "n_max вне диапазона.", "Задайте от 1 до 128.", "search.passes.0.n_max")
    spec = SimulationSpec(
        name=name, kind=kind, model_id=model_id, target=parse_target(b.get("target")), samples=samples,
        variogram=variogram, variograms=None, method=None, search=search,
        nodes=_def(b.get("nodes"), "nodes", "nodes"), seed=_def(b.get("seed"), "seed", "seed"),
        n_real=_def(b.get("n_real"), "n_real", "n_real"), secondary=secondary,
        nscore=parse_nscore(b.get("nscore")),
    )  # fmt: skip
    return spec.resolved()


def _def(raw: Any, key: str, param: str) -> int:
    v = whole(raw, key, param)
    return int(DEFAULTS[key]) if v is None else v


# --- the run and the post-processing ------------------------------------------------------------------------------


def _version(raw: Any, param: str, what: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw != int(raw) or raw < 1:
        raise bad("bad_param", f"Не задана версия: {what}.", "Перечитайте объект и повторите.", param)
    return int(raw)


@dataclass(frozen=True)
class RunSpec:
    simulation: dict[str, Any] | None  # a body (the job makes the row at the end) …
    simulation_id: int | None  # … or a stored row
    expected_version: int | None  # of the row `simulation_id` (optional; the job does not write the model)


def parse_run(body: Mapping[str, Any]) -> RunSpec:
    """`POST /simulate/run {simulation | simulation_id, expected_version?}`."""
    b = obj(body, "Запуск", "body")
    sim_id = id_of(b.get("simulation_id"), "моделирование", "simulation_id", required=False)
    raw = b.get("simulation")
    if (sim_id is None) == (raw in (None, "")):
        raise bad("bad_param", "Задайте параметры моделирования или номер строки — что-то одно.",
                  "Выберите сохранённое моделирование или заполните параметры.", "simulation_id")  # fmt: skip
    if raw not in (None, "") and not isinstance(raw, Mapping):
        raise bad("bad_param", "Параметры моделирования: ожидается набор параметров.", "Проверьте форму запроса.",
                  "simulation")  # fmt: skip
    ev = None
    if b.get("expected_version") not in (None, ""):
        ev = _version(b["expected_version"], "expected_version", "строка моделирования")
    return RunSpec(dict(raw) if isinstance(raw, Mapping) else None, sim_id, ev)


@dataclass(frozen=True)
class PostSpec:
    expected_version: int  # of the block model
    prefix: str | None  # None — «‹ПОЛЕ›_SGS_»
    stats: tuple[str, ...]
    thresholds: tuple[float, ...]
    replace: bool


def threshold_name(t: float) -> str:
    """`PROB_GT_<t>`: `format(t, "g")`, «.» → «_» (the name of a field has no dot)."""
    return format(t, "g").replace(".", "_")


def parse_post(body: Mapping[str, Any]) -> PostSpec:
    """`POST /simulations/{id}/post {expected_version, prefix?, stats?, thresholds?, replace?}`."""
    b = obj(body, "Постобработка", "body")
    if b.get("expected_version") in (None, ""):
        raise bad("bad_param", "Не задана версия блочной модели.", "Перечитайте модель и запустите снова.",
                  "expected_version")  # fmt: skip
    version = _version(b["expected_version"], "expected_version", "блочная модель")
    prefix = text_of(b.get("prefix"), "Префикс полей", "prefix", PREFIX_MAX, required=False)
    if prefix is not None and not _prefix_ok(prefix):
        raise bad("bad_param", "Префикс полей: допустимы буквы, цифры и «_».", "Например, AU_SGS_.", "prefix")
    raw_t = b.get("thresholds")
    given = [] if raw_t in (None, "") else list_of(raw_t, "Пороги", "thresholds", THRESHOLDS_MAX)
    thresholds = tuple(dict.fromkeys(_number(t, "Порог", f"thresholds.{i}") + 0.0 for i, t in enumerate(given)))
    seen: dict[str, float] = {}
    for t in thresholds:
        if threshold_name(t) in seen:
            raise bad("bad_param", f"Пороги {seen[threshold_name(t)]:g} и {t:g} дают одно имя поля.",
                      "Оставьте один из них.", "thresholds")  # fmt: skip
        seen[threshold_name(t)] = t
    raw_s = b.get("stats")
    if raw_s in (None, ""):
        stats = (*BASE_STATS, *(("prob",) if thresholds else ()))
    else:
        listed = list_of(raw_s, "Статистики", "stats", len(STATS))
        stats = tuple(dict.fromkeys(choice(s, STATS, "mean", "Статистика", f"stats.{i}") for i, s in enumerate(listed)))
        if not stats:
            raise bad("bad_param", "Не выбрано ни одной статистики.", "Отметьте хотя бы одну.", "stats")
    if "prob" in stats and not thresholds:
        raise bad("bad_param", "Для вероятности превышения не заданы пороги.", "Задайте хотя бы один порог.",
                  "thresholds")  # fmt: skip
    if "prob" not in stats:
        thresholds = ()
    rep = b.get("replace", False)
    if not isinstance(rep, bool):
        raise bad("bad_param", "«Заменить поля»: ожидается «да» или «нет».", "Отметьте или снимите галочку.", "replace")
    return PostSpec(version, prefix, stats, thresholds, rep)


def _prefix_ok(text: str) -> bool:
    import re

    return re.fullmatch(r"\w{1,40}", text) is not None


def post_columns(prefix: str, stats: tuple[str, ...], thresholds: tuple[float, ...],
                 unit: str | None) -> list[dict[str, Any]]:  # fmt: skip
    """The fields of the post-processing: [{name, key, dtype, role, unit, description}] in the order of `STATS`."""
    out: list[dict[str, Any]] = []
    for key in STATS:
        if key not in stats:
            continue
        if key == "prob":
            for t in thresholds:
                out.append({"name": f"{prefix}PROB_GT_{threshold_name(t)}", "key": f"prob:{t!r}", "dtype": "f4",
                            "role": None, "unit": None,
                            "description": f"Доля реализаций со значением не меньше {format(t, 'g')}"})  # fmt: skip
        else:
            out.append({"name": f"{prefix}{STAT_SUFFIX[key]}", "key": key, "dtype": STAT_DTYPE[key],
                        "role": None if key == "std" else "grade", "unit": unit,
                        "description": _STAT_TEXT[key]})  # fmt: skip
    return out


_STAT_TEXT = {"mean": "Среднее по реализациям (E-type)", "std": "Стандартное отклонение по реализациям",
              "p10": "Квантиль 10 % по реализациям", "p50": "Медиана по реализациям",
              "p90": "Квантиль 90 % по реализациям"}


def default_post_prefix(field: str, kind: str) -> str:
    """«AU_SGS_»."""
    return default_prefix(field, kind)
