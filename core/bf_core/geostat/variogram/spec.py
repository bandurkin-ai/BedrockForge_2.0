"""Parameters of the variography (ТЗ 8.6.4; ADR 0022 §2–8, §14): the bodies of `POST /variography/experimental`,
`/preview`, `/map` and the model of `/fit`, `/curve`, `/models` checked against the project and the ranges
(docs/progress/e16-api.md).

    spec = parse_experimental(conn, body)   # ExperimentalSpec; ApiError 422 `bad_param` {param}; 404
    spec = parse_map(conn, body)            # MapSpec
    model = parse_model(body["model"])      # canonical dict of the model; 422 `bad_param` {param: "model.…"}
    full = spec.resolved(defaults_for(median_length))   # every number filled in, the ranges relative to L checked

A parameter the body leaves out (or null) takes its default; those that follow the lag L (lag tolerance L / 2,
bands 3L and 2L, the cell of the map L, …) and L itself (5 × the median length of the intervals) are known only
after the points are read (`points.py`): `parse_*` keeps them None, `resolved` fills them in and checks the
ranges relative to L (lag tolerance 0–L). `canonical()` of a resolved spec is the body of the calculation as it
is hashed and stored (`calc` of a model): every number filled in, tables as ids (text), fields by their stored
names, ∞ as "inf". `param` of an error is a key of the body or a path: "directions.0.az", "map.cell",
"model.structures.1.a2". No numpy here (the router loads without it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import objects

LEN_MAX = 100_000.0  # m: the longest lag, band, cell or range accepted
DIRECTIONS_MAX = 72
DOMAIN_VALUES_MAX = 1000
STRUCTURES_MAX = 3
PREVIEW_MAX = 20_000  # points of `/preview` (Д-14)
PAIRS_MAX = 2_000_000_000  # checks of pairs above which the points are sampled (8.6.2)
SEED = 12345
DECL_W = "DECL_W"
TRANSFORMS = ("none", "nscore")
INDICATOR = "indicator"  # `indicator:<t>` — the indicator of the threshold t (ТЗ 8.8.1; ADR 0024 № 85)
WHAT = ("fan", "map")
MODEL_TYPES = ("semivariogram", "correlogram", "pairwise", "nscore")
STRUCTURE_TYPES = ("spherical", "exponential", "gaussian")
INF = "inf"


@dataclass(frozen=True)
class Range:
    lo: float
    hi: float
    label: str
    unit: str = ""
    lo_open: bool = False  # (lo; …
    integer: bool = False
    inf: bool = False  # ∞ allowed ("inf", "∞")


#: 8.6.4 and ADR 0022 §2, §5: key → range; the relative ones (lag tolerance ≤ L) — in `resolved`.
RANGES: dict[str, Range] = {
    "lag": Range(0.0, LEN_MAX, "Лаг L", " м", lo_open=True),
    "nlag": Range(1, 100, "Число лагов", integer=True),
    "lag_tol": Range(0.0, LEN_MAX, "Допуск лага", " м"),
    "az_tol": Range(0.0, 90.0, "Допуск азимута", "°", lo_open=True),
    "dip_tol": Range(0.0, 90.0, "Допуск погружения", "°", lo_open=True),
    "band_h": Range(0.0, LEN_MAX, "Горизонтальная полоса", " м", lo_open=True, inf=True),
    "band_v": Range(0.0, LEN_MAX, "Вертикальная полоса", " м", lo_open=True, inf=True),
    "min_pairs": Range(1, 1000, "Наименьшее число пар", integer=True),
    "dh_lag": Range(0.0, LEN_MAX, "Лаг вдоль скважин", " м", lo_open=True),
    "dh_nlag": Range(1, 100, "Число лагов вдоль скважин", integer=True),
    "dh_lag_tol": Range(0.0, LEN_MAX, "Допуск лага вдоль скважин", " м"),
    "seed": Range(0, 2**63 - 1, "Зерно выборки", integer=True),
    "ang1": Range(0.0, 360.0, "Азимут ang1", "°"),
    "ang2": Range(-90.0, 90.0, "Погружение ang2", "°"),
    "ang3": Range(-180.0, 180.0, "Поворот ang3", "°"),
    "az": Range(0.0, 360.0, "Азимут направления", "°"),
    "plunge": Range(-90.0, 90.0, "Погружение направления", "°"),
    "fan.n": Range(4, 72, "Число направлений веера", integer=True),
    "fan.tol": Range(0.0, 90.0, "Допуск направления в плоскости", "°", lo_open=True),
    "fan.q": Range(0.5, 1.2, "Уровень дальности розы q", ""),
    "map.nu": Range(1, 100, "Ячеек карты по первой оси (в каждую сторону)", integer=True),
    "map.nv": Range(1, 100, "Ячеек карты по второй оси (в каждую сторону)", integer=True),
    "map.cell": Range(0.0, LEN_MAX, "Ячейка карты", " м", lo_open=True),
    "map.thickness": Range(0.0, LEN_MAX, "Толщина слоя карты", " м", lo_open=True, inf=True),
    "map.min_pairs": Range(1, 1000, "Наименьшее число пар в ячейке", integer=True),
    "c0": Range(0.0, math.inf, "Самородок C0"),
    "c": Range(0.0, math.inf, "Вклад структуры c"),
    "a": Range(0.0, LEN_MAX, "Дальность", " м", lo_open=True),
}


def bad(code: str, message: str, hint: str, param: str | None = None, status: int = 422) -> ApiError:
    return ApiError(status, code, message, hint, {"param": param} if param else None)


def _fmt(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _span(r: Range) -> str:
    hi = "∞" if math.isinf(r.hi) else _fmt(r.hi)
    lo = f"больше {_fmt(r.lo)}" if r.lo_open else f"от {_fmt(r.lo)}"
    tail = " или ∞" if r.inf else ""
    return f"{lo} до {hi}{r.unit}{tail}"


def _number(raw: Any, label: str, param: str, allow_inf: bool = False) -> float:
    """A number or a text «0,5» / «0.5» (∞ — "inf" / "∞" where allowed); 422 `bad_param` otherwise."""
    value = math.nan
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip().replace(",", ".")
        if allow_inf and text.casefold() in (INF, "∞"):
            return math.inf
        try:
            value = float(text)
        except ValueError:
            value = math.nan
    if not math.isfinite(value):
        hint = "Введите число, например 0,5" + (", или ∞." if allow_inf else ".")
        raise bad("bad_param", f"{label}: нужно число.", hint, param)
    return value


def ranged(raw: Any, key: str, param: str | None = None) -> float | None:
    """`raw` in the range RANGES[key]; absent, null or "" — None (the default). Integers come back as whole
    floats (15.0); the caller takes `int`."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    r = RANGES[key]
    p = param or key
    value = _number(raw, r.label, p, r.inf)
    if math.isinf(value):
        return value
    if r.integer and value != math.floor(value):
        raise bad("bad_param", f"{r.label}: нужно целое число.", f"Задайте целое число {_span(r)}.", p)
    if not (r.lo < value if r.lo_open else r.lo <= value) or not value <= r.hi:
        raise bad("bad_param", f"{r.label}: значение вне диапазона {_span(r)}.", f"Задайте {_span(r)}.", p)
    return value + 0.0  # −0.0 → 0.0


def _int(raw: Any, key: str, default: int, param: str | None = None) -> int:
    v = ranged(raw, key, param)
    return default if v is None else int(v)


def _angle(raw: Any, key: str, default: float, param: str) -> float:
    """ang1 / az 360 → 0, ang3 −180 → 180 (the ranges [0; 360), (−180; 180] of ADR 0022 §6)."""
    v = ranged(raw, key, param)
    if v is None:
        return default
    if key in ("ang1", "az") and v == 360.0:
        return 0.0
    if key == "ang3" and v == -180.0:
        return 180.0
    return v


def choice(raw: Any, choices: tuple[str, ...], default: str, label: str, param: str) -> str:
    if raw in (None, ""):
        return default
    if raw not in choices:
        raise bad("bad_param", f"{label}: неизвестное значение «{str(raw)[:40]}».",
                  f"Допустимо: {', '.join(choices)}.", param)  # fmt: skip
    return str(raw)


def _flag(raw: Any, default: bool, label: str, param: str) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise bad("bad_param", f"{label}: ожидается «да» или «нет».", "Отметьте или снимите галочку.", param)
    return raw


def _obj(raw: Any, label: str, param: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise bad("bad_param", f"{label}: ожидается набор параметров.", "Проверьте форму запроса.", param)
    return raw


def _len(x: float | None) -> Any:
    """A length of the canonical body: ∞ as "inf" (JSON has no infinity)."""
    return INF if x is not None and math.isinf(x) else x


# --- the data of the calculation ----------------------------------------------------------------------


@dataclass(frozen=True)
class DomainSel:
    table_id: int
    table_name: str
    field: str  # stored name
    kind: str  # text | number
    values: tuple[str, ...]  # text as given (trimmed); a number by `format(x, ".15g")`


@dataclass(frozen=True)
class Data:
    set_id: int
    table_id: int
    table_name: str
    field: str  # stored name of the value
    unit: str | None
    domain: DomainSel | None
    weights: str | None  # stored name of the weight field; None — no weights
    transform: str  # none | nscore | indicator:<t> (t — `format(t, ".15g")`, see `parse_transform`)

    @property
    def threshold(self) -> float | None:
        """The threshold t of the indicator transform; None — another transform."""
        return indicator_threshold(self.transform)

    def tables(self) -> list[tuple[int, list[str]]]:
        """(table id, fields read): the samples first, then the table of the domain."""
        own = [self.field, *([self.weights] if self.weights else [])]
        d = self.domain
        if d is not None and d.table_id == self.table_id:
            return [(self.table_id, [*own, d.field])]
        return [(self.table_id, own), *([(d.table_id, [d.field])] if d is not None else [])]

    def canonical(self) -> dict[str, Any]:
        d = self.domain
        dom = None if d is None else {"table": str(d.table_id), "field": d.field, "values": list(d.values)}
        return {"set_id": self.set_id, "table": str(self.table_id), "field": self.field, "domain": dom,
                "weights": self.weights, "transform": self.transform}  # fmt: skip


def _in_param(fn: Any, param: str) -> Any:
    """The errors of the resolvers carry `param` (they do not know the key of the body)."""
    try:
        return fn()
    except ApiError as e:
        if "param" not in e.extra:
            e.extra = {**e.extra, "param": param}
        raise


def _set_id(conn: sa.Connection, body: dict[str, Any]) -> int:
    set_id = body.get("set_id")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise bad("set_not_found", "Не задан набор скважин.", "Выберите набор скважин.", "set_id", 404)
    q = sa.select(objects.c.id).where(objects.c.id == set_id, objects.c.kind == "drillhole_set")
    if conn.execute(q).first() is None:
        raise bad("set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.", "set_id", 404)
    return set_id


def _field(conn: sa.Connection, table_id: int, raw: Any, label: str, param: str, number: bool) -> Any:
    from bf_core.composites.source import _fields

    if not isinstance(raw, str) or not raw.strip():
        raise bad("bad_param", f"{label}: не выбрано поле.", "Выберите поле таблицы.", param)
    f = _in_param(lambda: next(iter(_fields(conn, table_id, [raw.strip()]).values())), param)
    if number and f.kind != "number":
        raise bad("bad_param", f"{label}: поле «{f.name}» не числовое.", "Выберите числовое поле.", param)
    return f


def _domain_value(raw: Any, kind: str, param: str) -> str:
    if kind == "number":
        return format(_number(raw, "Значение домена", param), ".15g")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)) or not str(raw).strip():
        raise bad("bad_param", "Значение домена: пустое.", "Отметьте значения домена из списка.", param)
    return str(raw).strip()


def _domain(conn: sa.Connection, set_id: int, table: Any, raw: Any) -> DomainSel | None:
    from bf_core.composites import jobs as cj

    if raw is None or raw == "":
        return None
    raw = _obj(raw, "Домен", "domain")
    if raw.get("wireframe_ids"):
        raise bad("bad_param", "Домен каркасами здесь не задаётся.",
                  "Закодируйте пробы каркасами («Кодировать каркасами») и выберите поле домена.", "domain")  # fmt: skip
    t = _in_param(lambda: cj.resolve_table(conn, set_id, raw.get("table") or table.id), "domain.table")
    f = _field(conn, int(t.id), raw.get("field"), "Домен", "domain.field", number=False)
    given = raw.get("values")
    if not isinstance(given, list) or not given:
        raise bad("bad_param", "Домен: не выбраны значения.", "Отметьте одно или несколько значений домена.",
                  "domain.values")  # fmt: skip
    if len(given) > DOMAIN_VALUES_MAX:
        raise bad("bad_param", "Домен: слишком много значений.", f"Не больше {DOMAIN_VALUES_MAX}.", "domain.values")
    vals = [_domain_value(v, f.kind, f"domain.values.{i}") for i, v in enumerate(given)]
    return DomainSel(int(t.id), str(t.name), f.name, f.kind, tuple(dict.fromkeys(vals)))


def _weights(conn: sa.Connection, table_id: int, raw: Any) -> str | None:
    """null / absent — DECL_W when the table has a numeric field of that name; false or "" — none; a name."""
    from bf_core.composites.source import _fields

    if raw is False or raw == "":
        return None
    if raw is None:
        try:
            f = next(iter(_fields(conn, table_id, [DECL_W]).values()))
        except ApiError:
            return None
        return f.name if f.kind == "number" else None
    return str(_field(conn, table_id, raw, "Веса", "weights", number=True).name)


def indicator_threshold(transform: str) -> float | None:
    """t of `indicator:<t>` (a canonical string of `parse_transform`); None — another transform."""
    return float(transform[len(INDICATOR) + 1 :]) if transform.startswith(INDICATOR + ":") else None


def _threshold(raw: Any) -> float:
    """A finite number — a JSON number or a text (a decimal comma too); bool, NaN, ±∞ are not."""
    t = float("nan")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        t = float(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            t = float(raw.strip().replace(",", "."))
        except ValueError:
            pass
    if not math.isfinite(t):
        raise bad("bad_param", f"Порог индикатора: «{str(raw)[:40]}» — не число.",
                  "Укажите конечное число в единицах поля, например 0,5.", "transform")  # fmt: skip
    return t + 0.0  # −0 → 0


def parse_transform(raw: Any, threshold: Any = None) -> str:
    """`transform` of a request as the canonical string: `none`, `nscore`, `indicator:<t>`. The threshold is in
    the string (`indicator:0.5`: so the query of `GET /defaults` has it too) or in `threshold` beside `indicator`;
    the indicator is i = 1 for z ≤ t, else 0 (`points.indicators`). 422 `bad_param {param: "transform"}`."""
    if isinstance(raw, str) and raw.startswith(INDICATOR):
        rest = raw[len(INDICATOR) :]
        if rest == "" and threshold is not None:
            return f"{INDICATOR}:{format(_threshold(threshold), '.15g')}"
        if rest.startswith(":"):
            return f"{INDICATOR}:{format(_threshold(rest[1:]), '.15g')}"
        if rest == "":
            raise bad("bad_param", "Индикатор: не задан порог.", "Укажите порог: indicator:<число>.", "transform")
    return choice(raw, TRANSFORMS, "none", "Преобразование", "transform")


def parse_data(conn: sa.Connection, body: dict[str, Any]) -> Data:
    """set_id, table, field, domain, weights, transform — common to every calculation."""
    from bf_core.composites import jobs as cj

    set_id = _set_id(conn, body)
    t = _in_param(lambda: cj.resolve_table(conn, set_id, body.get("table")), "table")
    f = _field(conn, int(t.id), body.get("field"), "Поле", "field", number=True)
    return Data(set_id=set_id, table_id=int(t.id), table_name=str(t.name), field=f.name, unit=f.unit,
                domain=_domain(conn, set_id, t, body.get("domain")),
                weights=_weights(conn, int(t.id), body.get("weights")),
                transform=parse_transform(body.get("transform"), body.get("threshold")))  # fmt: skip


# --- defaults --------------------------------------------------------------------------------------------


def sig2(x: float) -> float:
    """Two significant digits (the lag 5 × median: 2 m → 10 m, 1,37 m → 6,8 m)."""
    return float(f"{x:.2g}")


@dataclass(frozen=True)
class Defaults:
    lag: float  # L
    dh_lag: float  # the median length of the intervals


def defaults_for(median_length: float) -> Defaults:
    if not (math.isfinite(median_length) and median_length > 0.0):
        raise bad("no_lengths", "Не определена длина интервалов точек расчёта.",
                  "Задайте лаг вручную или проверьте FROM и TO таблицы.", "lag")  # fmt: skip
    return Defaults(lag=sig2(5.0 * median_length), dh_lag=median_length)


def defaults_json(d: Defaults) -> dict[str, Any]:
    """What `GET /variography/defaults` gives under `defaults` (and the window fills its form with)."""
    lag = d.lag
    return {"lag": lag, "nlag": 15, "lag_tol": lag / 2.0, "az_tol": 22.5, "dip_tol": 22.5, "band_h": 3.0 * lag,
            "band_v": 2.0 * lag, "min_pairs": 30, "downhole": True, "dh_lag": d.dh_lag, "dh_nlag": 15,
            "dh_lag_tol": d.dh_lag / 2.0, "seed": SEED,
            "fan": {"n": 12, "tol": 7.5, "q": 1.0},
            "map": {"nu": 20, "nv": 20, "cell": lag, "thickness": 2.0 * lag, "min_pairs": 5}}  # fmt: skip


def ranges_json() -> dict[str, Any]:
    """The ranges for the form: key → {lo, hi, lo_open, integer, inf, unit}."""
    return {k: {"lo": r.lo, "hi": None if math.isinf(r.hi) else r.hi, "lo_open": r.lo_open, "integer": r.integer,
                "inf": r.inf, "unit": r.unit.strip()} for k, r in RANGES.items()}  # fmt: skip


# --- lags and directions -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Lags:
    lag: float | None  # None — the default (resolved)
    nlag: int
    tol: float | None

    def resolved(self, lag: float, key: str) -> Lags:
        """`key` — the param of the tolerance: "lag_tol" / "dh_lag_tol"; its range is 0–L."""
        L = lag if self.lag is None else self.lag
        tol = L / 2.0 if self.tol is None else self.tol
        if not tol <= L:
            label = RANGES[key].label
            raise bad("bad_param", f"{label}: значение больше лага ({_fmt(L)} м).", f"Задайте от 0 до {_fmt(L)} м.",
                      key)  # fmt: skip
        return Lags(L, self.nlag, tol)

    def h_max(self) -> float:
        assert self.lag is not None
        return (self.nlag + 0.5) * self.lag


@dataclass(frozen=True)
class Direction:
    az: float
    plunge: float  # down positive (ang2 = −plunge)
    az_tol: float | None  # None — the common one of the body
    dip_tol: float | None
    band_h: float | None
    band_v: float | None

    def resolved(self, common: Direction) -> Direction:
        return Direction(self.az, self.plunge, *(c if s is None else s for s, c in (
            (self.az_tol, common.az_tol), (self.dip_tol, common.dip_tol), (self.band_h, common.band_h),
            (self.band_v, common.band_v))))  # fmt: skip

    def canonical(self) -> dict[str, Any]:
        return {"az": self.az, "plunge": self.plunge, "az_tol": self.az_tol, "dip_tol": self.dip_tol,
                "band_h": _len(self.band_h), "band_v": _len(self.band_v)}  # fmt: skip


def _tolerances(raw: dict[str, Any], where: str) -> tuple[float | None, ...]:
    return tuple(ranged(raw.get(k), k, f"{where}{k}") for k in ("az_tol", "dip_tol", "band_h", "band_v"))


def _angles(raw: Any, where: str) -> tuple[float, float, float]:
    raw = _obj(raw, "Углы", where.rstrip("."))
    return (_angle(raw.get("ang1"), "ang1", 0.0, f"{where}ang1"), _angle(raw.get("ang2"), "ang2", 0.0, f"{where}ang2"),
            _angle(raw.get("ang3"), "ang3", 0.0, f"{where}ang3"))  # fmt: skip


def _directions(raw: Any) -> list[Direction]:
    if not isinstance(raw, list) or not raw:
        raise bad("bad_param", "Не заданы направления.", "Задайте оси (углы) или 1–72 направления.", "directions")
    if len(raw) > DIRECTIONS_MAX:
        raise bad("bad_param", "Слишком много направлений.", f"Не больше {DIRECTIONS_MAX}.", "directions")
    out = []
    for i, d in enumerate(raw):
        where = f"directions.{i}."
        d = _obj(d, "Направление", where.rstrip("."))
        az = _angle(d.get("az"), "az", 0.0, f"{where}az")
        plunge = ranged(d.get("plunge"), "plunge", f"{where}plunge")
        out.append(Direction(az, 0.0 if plunge is None else plunge, *_tolerances(d, where)))
    return out


# --- the experimental variogram -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentalSpec:
    data: Data
    axes: tuple[float, float, float] | None  # ang1–ang3: the three axes a1, a2, a3 of R; None — `directions`
    directions: list[Direction]  # given ones (empty with `axes`)
    common: Direction  # az, plunge unused: the common tolerances and bands
    lags: Lags
    min_pairs: int
    downhole: bool
    dh: Lags
    seed: int
    body: dict[str, Any] = field(default_factory=dict)  # canonical — after `resolved`

    def resolved(self, d: Defaults) -> ExperimentalSpec:
        lags = self.lags.resolved(d.lag, "lag_tol")
        L = lags.lag
        assert L is not None
        c = self.common
        common = Direction(0.0, 0.0, 22.5 if c.az_tol is None else c.az_tol,
                           22.5 if c.dip_tol is None else c.dip_tol, 3.0 * L if c.band_h is None else c.band_h,
                           2.0 * L if c.band_v is None else c.band_v)  # fmt: skip
        dh = self.dh.resolved(d.dh_lag, "dh_lag_tol")
        dirs = [x.resolved(common) for x in self.directions]
        out = replace(self, directions=dirs, common=common, lags=lags, dh=dh)
        return replace(out, body=out._canonical())

    def _canonical(self) -> dict[str, Any]:
        c = self.common
        return {**self.data.canonical(),
                "axes": None if self.axes is None else dict(zip(("ang1", "ang2", "ang3"), self.axes, strict=True)),
                "directions": [x.canonical() for x in self.directions],
                "lag": self.lags.lag, "nlag": self.lags.nlag, "lag_tol": self.lags.tol, "az_tol": c.az_tol,
                "dip_tol": c.dip_tol, "band_h": _len(c.band_h), "band_v": _len(c.band_v), "min_pairs": self.min_pairs,
                "downhole": self.downhole, "dh_lag": self.dh.lag, "dh_nlag": self.dh.nlag, "dh_lag_tol": self.dh.tol,
                "seed": self.seed}  # fmt: skip

    def hash_params(self) -> dict[str, Any]:
        """What the key of the result hashes besides the inputs (`docs/progress/e16-api.md`)."""
        assert self.body, "resolve the spec first"
        return {"op": "experimental", **self.body}


def _lags(body: dict[str, Any], prefix: str = "") -> Lags:
    return Lags(ranged(body.get(f"{prefix}lag"), f"{prefix}lag"), _int(body.get(f"{prefix}nlag"), f"{prefix}nlag", 15),
                ranged(body.get(f"{prefix}lag_tol"), f"{prefix}lag_tol"))  # fmt: skip


def parse_experimental(conn: sa.Connection, body: dict[str, Any]) -> ExperimentalSpec:
    """The body of `POST /variography/experimental` and `/preview`: the data, `axes` {ang1, ang2, ang3} (three
    directions — the axes of the model; the default when `directions` is absent) or `directions` (1–72)."""
    data = parse_data(conn, body)
    has_dirs = body.get("directions") is not None
    if has_dirs and body.get("axes") is not None:
        raise bad("bad_param", "Заданы и оси, и направления.", "Оставьте что-то одно.", "directions")
    axes = None if has_dirs else _angles(body.get("axes"), "axes.")
    dirs = _directions(body["directions"]) if has_dirs else []
    common = Direction(0.0, 0.0, *_tolerances(body, ""))
    return ExperimentalSpec(data=data, axes=axes, directions=dirs, common=common, lags=_lags(body),
                            min_pairs=_int(body.get("min_pairs"), "min_pairs", 30),
                            downhole=_flag(body.get("downhole"), True, "Вдоль скважин", "downhole"),
                            dh=_lags(body, "dh_"), seed=_int(body.get("seed"), "seed", SEED))  # fmt: skip


# --- fan, rose, map ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MapSpec:
    data: Data
    plane: tuple[float, float, float]  # ang1–ang3: the plane of a1, a2; the normal a3
    what: tuple[str, ...]  # fan (with the rose) and / or map
    lags: Lags
    dip_tol: float | None  # out of the plane (fan)
    band_v: float | None
    min_pairs: int
    fan_n: int
    fan_tol: float | None  # None — 90° / N
    q: float
    nu: int
    nv: int
    cell: float | None  # None — L
    thickness: float | None  # None — 2L; ∞ — the projection
    map_min_pairs: int
    seed: int
    body: dict[str, Any] = field(default_factory=dict)

    def resolved(self, d: Defaults) -> MapSpec:
        lags = self.lags.resolved(d.lag, "lag_tol")
        L = lags.lag
        assert L is not None
        out = replace(self, lags=lags, dip_tol=22.5 if self.dip_tol is None else self.dip_tol,
                      band_v=2.0 * L if self.band_v is None else self.band_v,
                      fan_tol=90.0 / self.fan_n if self.fan_tol is None else self.fan_tol,
                      cell=L if self.cell is None else self.cell,
                      thickness=2.0 * L if self.thickness is None else self.thickness)  # fmt: skip
        return replace(out, body=out._canonical())

    def _canonical(self) -> dict[str, Any]:
        return {**self.data.canonical(), "plane": dict(zip(("ang1", "ang2", "ang3"), self.plane, strict=True)),
                "what": list(self.what), "lag": self.lags.lag, "nlag": self.lags.nlag, "lag_tol": self.lags.tol,
                "dip_tol": self.dip_tol, "band_v": _len(self.band_v), "min_pairs": self.min_pairs,
                "fan": {"n": self.fan_n, "tol": self.fan_tol, "q": self.q},
                "map": {"nu": self.nu, "nv": self.nv, "cell": self.cell, "thickness": _len(self.thickness),
                        "min_pairs": self.map_min_pairs}, "seed": self.seed}  # fmt: skip

    def hash_params(self) -> dict[str, Any]:
        assert self.body, "resolve the spec first"
        return {"op": "map", **self.body}


def _what(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return WHAT
    items = raw if isinstance(raw, list) else [raw]
    if not items or any(x not in WHAT for x in items):
        raise bad("bad_param", "Что рассчитать: неизвестное значение.", "Допустимо: fan, map.", "what")
    return tuple(x for x in WHAT if x in items)


def parse_map(conn: sa.Connection, body: dict[str, Any]) -> MapSpec:
    """The body of `POST /variography/map`: the data, `plane` {ang1–ang3}, `what`, the lags, `fan` {n, tol, q},
    `map` {nu, nv, cell, thickness, min_pairs}."""
    data = parse_data(conn, body)
    fan, mp = _obj(body.get("fan"), "Веер", "fan"), _obj(body.get("map"), "Карта", "map")
    q = ranged(fan.get("q"), "fan.q")
    return MapSpec(data=data, plane=_angles(body.get("plane"), "plane."), what=_what(body.get("what")),
                   lags=_lags(body), dip_tol=ranged(body.get("dip_tol"), "dip_tol"),
                   band_v=ranged(body.get("band_v"), "band_v"),
                   min_pairs=_int(body.get("min_pairs"), "min_pairs", 30),
                   fan_n=_int(fan.get("n"), "fan.n", 12), fan_tol=ranged(fan.get("tol"), "fan.tol"),
                   q=1.0 if q is None else q, nu=_int(mp.get("nu"), "map.nu", 20), nv=_int(mp.get("nv"), "map.nv", 20),
                   cell=ranged(mp.get("cell"), "map.cell"), thickness=ranged(mp.get("thickness"), "map.thickness"),
                   map_min_pairs=_int(mp.get("min_pairs"), "map.min_pairs", 5),
                   seed=_int(body.get("seed"), "seed", SEED))  # fmt: skip


# --- the model ------------------------------------------------------------------------------------------


def _required(raw: Any, key: str, param: str) -> float:
    v = ranged(raw, key, param)
    if v is None:
        r = RANGES[key]
        raise bad("bad_param", f"{r.label}: не задано.", f"Задайте {_span(r)}.", param)
    return v


def parse_model(raw: Any, param: str = "model") -> dict[str, Any]:
    """The model {type, c0, structures [{type, c, ang1, ang2, ang3, a1, a2, a3}] (0–3), common_axes} → its
    canonical dict (8.6.1; ADR 0022 §6): with `common_axes` (true by default) the angles of every structure are
    those of the first. A sill above 1,2·σ² is allowed (a warning of the window, ADR §16)."""
    m = _obj(raw, "Модель", param)
    if not m:
        raise bad("bad_param", "Модель не задана.", "Задайте самородок и структуры.", param)
    kind = choice(m.get("type"), MODEL_TYPES, "semivariogram", "Тип вариограммы", f"{param}.type")
    c0 = ranged(m.get("c0"), "c0", f"{param}.c0")
    common = _flag(m.get("common_axes"), True, "Общие оси", f"{param}.common_axes")
    given = m.get("structures") if m.get("structures") is not None else []
    if not isinstance(given, list) or len(given) > STRUCTURES_MAX:
        raise bad("bad_param", "Структур может быть от 0 до 3.", "Удалите лишние структуры.", f"{param}.structures")
    out: list[dict[str, Any]] = []
    for i, s in enumerate(given):
        where = f"{param}.structures.{i}."
        s = _obj(s, "Структура", where.rstrip("."))
        angles = _angles(s, where) if not (common and out) else tuple(out[0][k] for k in ("ang1", "ang2", "ang3"))
        out.append({"type": choice(s.get("type"), STRUCTURE_TYPES, "spherical", "Тип структуры", f"{where}type"),
                    "c": _required(s.get("c"), "c", f"{where}c"),
                    **dict(zip(("ang1", "ang2", "ang3"), angles, strict=True)),
                    **{a: _required(s.get(a), "a", f"{where}{a}") for a in ("a1", "a2", "a3")}})  # fmt: skip
    return {"type": kind, "c0": 0.0 if c0 is None else c0, "structures": out, "common_axes": common}
