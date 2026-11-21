"""The variogram model: a nugget and nested anisotropic structures (ТЗ 8.6.3; ADR 0022 §6). numpy, float64; no
numba. The window mirrors the formulas in `ui/src/modules/variography/model.ts`; both read the shared answers
`core/tests/data/variography/model_curves.json` (`rules` there say how every number is computed).

A structure s has a type, a contribution c ≥ 0, the ranges a1, a2, a3 > 0 along its axes and the angles ang1–ang3
of GSLIB (`geostat.rotation`): u₁, u₂, u₃ — the rows of R, the axes in the world.

    h_r = √((u₁·Δ / a1)² + (u₂·Δ / a2)² + (u₃·Δ / a3)²)          = ‖diag(1, a1/a2, a1/a3)·R·Δ‖ / a1
    spherical     g = 1.5·h_r − 0.5·h_r³ at h_r < 1, else 1
    exponential   g = 1 − e^(−3·h_r)                              the range is practical (95 % of c), as in BF
    gaussian      g = 1 − e^(−3·h_r²)
    γ(Δ) = C0·[Δ ≠ 0] + Σ_s c_s·g_s(h_r,s)        C(Δ) = C0 + Σ_s c_s − γ(Δ)        γ(0) = 0, C(0) — the sill

A dot product is x·u_x + y·u_y + z·u_z left to right; 1 − e^(−t) is −expm1(−t); the sums run over the structures
in their order from C0. A NaN in Δ gives NaN — a missing lag is not a zero.

«Общие оси» (`common_axes`): the angles of every structure are those of the first. a2 > a1 and c = 0 are allowed
(ADR 0022 §16). The model of the tab «Статистика» (BF `VariogramModel`: `it, cc, hmaj, hmin, azi`, up to 4
structures) is the case ang1 = azi, ang2 = ang3 = 0, a1 = hmaj, a2 = a3 = hmin — `model_2d`.

    Structure(type, c, a1, a2, a3, ang1=0, ang2=0, ang3=0)            .axes -> R 3×3    .as_dict()
    Model(c0=0, structures=(), common_axes=True)                      .sill             .as_dict()
        .gamma(delta [..., 3]) -> γ [...]       .cov(delta)
        .gamma_along(h, azimuth=0, plunge=0)    .cov_along(…)      Δ = h·u, u = `rotation.direction`
    parse_model({c0, structures: [{type, c, ang1, ang2, ang3, a1, a2, a3}], common_axes}) -> Model
    model_2d(nug, [{it, cc, hmaj, hmin, azi}]) -> Model
    reduced_distance(structure, delta) -> h_r     shape(type, h_r) -> g     shape_slope(type, h_r) -> dg/dh_r

Errors — `StatsError {code, message, hint}` with `param` (`c0`, `structures`, `structures.<i>.<key>`, i from 0):
`bad_param` (API — 422).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.geostat.rotation import bad_param, normalize_angles, number, positive, rotation_matrix
from bf_core.geostat.rotation import direction as _direction

TYPES = ("spherical", "exponential", "gaussian")
GSLIB_TYPES = {1: "spherical", 2: "exponential", 3: "gaussian"}  # `it` of GSLIB
MAX_STRUCTURES = 3  # ТЗ 8.6.1
MAX_STRUCTURES_2D = 4  # the model of the tab «Статистика» (ADR 0022 §11)
TYPE_HINT = (
    "Выберите spherical (сферическая), exponential (экспоненциальная) или gaussian (гауссова); в GSLIB — 1, 2, 3."
)


def type_of(value: Any) -> str:
    """The name of a structure type: one of `TYPES` or the code 1, 2, 3 of GSLIB."""
    if isinstance(value, str):
        name = value.strip().lower()
        if name in TYPES:
            return name
        if name in ("1", "2", "3"):
            return GSLIB_TYPES[int(name)]
    elif not isinstance(value, bool) and isinstance(value, int | float | np.integer | np.floating):
        if value in GSLIB_TYPES:
            return GSLIB_TYPES[int(value)]
    raise bad_param("type", f"Неизвестный тип структуры «{value}».", TYPE_HINT)


def _share(value: Any, param: str, label: str) -> float:
    x = number(value, param, label)
    if x < 0.0:
        raise bad_param(param, f"{label}: значение должно быть не меньше нуля.", "Задайте ноль или число больше нуля.")
    return x + 0.0


@dataclass(frozen=True)
class Structure:
    """One nested structure. The values are checked and normalised on creation (`bad_param`)."""

    type: str
    c: float
    a1: float
    a2: float
    a3: float
    ang1: float = 0.0
    ang2: float = 0.0
    ang3: float = 0.0

    def __post_init__(self) -> None:
        angles = normalize_angles(self.ang1, self.ang2, self.ang3)
        values = {
            "type": type_of(self.type),
            "c": _share(self.c, "c", "Вклад структуры"),
            "a1": positive(self.a1, "a1", "Дальность a1"),
            "a2": positive(self.a2, "a2", "Дальность a2"),
            "a3": positive(self.a3, "a3", "Дальность a3"),
            "ang1": angles[0],
            "ang2": angles[1],
            "ang3": angles[2],
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def angles(self) -> tuple[float, float, float]:
        return self.ang1, self.ang2, self.ang3

    @property
    def ranges(self) -> tuple[float, float, float]:
        return self.a1, self.a2, self.a3

    @property
    def axes(self) -> np.ndarray:
        """R 3×3: the rows are the axes a1, a2, a3 in the world."""
        return rotation_matrix(self.ang1, self.ang2, self.ang3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "c": self.c,
            "ang1": self.ang1,
            "ang2": self.ang2,
            "ang3": self.ang3,
            "a1": self.a1,
            "a2": self.a2,
            "a3": self.a3,
        }


def shape(kind: str, hr: Any) -> np.ndarray:
    """g(h_r) of a structure with the contribution 1: 0 at 0, 1 on the sill."""
    h = np.asarray(hr, dtype=np.float64)
    if kind == "spherical":
        return np.where(h < 1.0, h * (1.5 - 0.5 * h * h), np.where(np.isnan(h), np.nan, 1.0))
    if kind == "exponential":
        return -np.expm1(-3.0 * h)
    if kind == "gaussian":
        return -np.expm1(-3.0 * h * h)
    raise bad_param("type", f"Неизвестный тип структуры «{kind}».", TYPE_HINT)


def shape_slope(kind: str, hr: Any) -> np.ndarray:
    """dg/dh_r — for the Jacobian of the fit."""
    h = np.asarray(hr, dtype=np.float64)
    if kind == "spherical":
        return np.where(h < 1.0, 1.5 - 1.5 * h * h, np.where(np.isnan(h), np.nan, 0.0))
    if kind == "exponential":
        return 3.0 * np.exp(-3.0 * h)
    if kind == "gaussian":
        return 6.0 * h * np.exp(-3.0 * h * h)
    raise bad_param("type", f"Неизвестный тип структуры «{kind}».", TYPE_HINT)


def _delta(delta: Any) -> np.ndarray:
    d = np.asarray(delta, dtype=np.float64)
    if d.shape[-1:] != (3,):
        raise ValueError(f"lag vectors must have 3 components, got the shape {d.shape}")
    return d


def reduced_distance(structure: Structure, delta: Any) -> np.ndarray:
    """h_r of the lag vectors Δ [..., 3] in the structure: 1 on its ellipsoid."""
    d = _delta(delta)
    u = structure.axes
    x, y, z = d[..., 0], d[..., 1], d[..., 2]
    q1 = (x * u[0, 0] + y * u[0, 1] + z * u[0, 2]) / structure.a1
    q2 = (x * u[1, 0] + y * u[1, 1] + z * u[1, 2]) / structure.a2
    q3 = (x * u[2, 0] + y * u[2, 1] + z * u[2, 2]) / structure.a3
    return np.asarray(np.sqrt(q1 * q1 + q2 * q2 + q3 * q3))


@dataclass(frozen=True)
class Model:
    """Nugget + nested structures. With `common_axes` every structure takes the angles of the first."""

    c0: float = 0.0
    structures: tuple[Structure, ...] = ()
    common_axes: bool = True

    def __post_init__(self) -> None:
        items = tuple(self.structures)
        if not all(isinstance(s, Structure) for s in items):
            raise bad_param("structures", "Структуры модели заданы неверно.", "Ожидается список структур.")
        if self.common_axes and items:
            first = items[0].angles
            items = tuple(s if s.angles == first else Structure(s.type, s.c, s.a1, s.a2, s.a3, *first) for s in items)
        object.__setattr__(self, "c0", _share(self.c0, "c0", "Самородок (C0)"))
        object.__setattr__(self, "structures", items)
        object.__setattr__(self, "common_axes", bool(self.common_axes))

    @property
    def sill(self) -> float:
        """C0 + Σ c_s, summed in the order of the structures."""
        total = self.c0
        for s in self.structures:
            total += s.c
        return total

    def as_dict(self) -> dict[str, Any]:
        """The form of `meta` of a stored model and of the API (ADR 0022 §12)."""
        return {
            "c0": self.c0,
            "structures": [s.as_dict() for s in self.structures],
            "common_axes": self.common_axes,
        }

    def gamma(self, delta: Any) -> np.ndarray:
        """γ of the lag vectors Δ [..., 3]; 0 at Δ = 0, NaN where Δ has one."""
        d = _delta(delta)
        total: Any = self.c0
        for s in self.structures:
            total = total + s.c * shape(s.type, reduced_distance(s, d))
        zero = (d[..., 0] == 0.0) & (d[..., 1] == 0.0) & (d[..., 2] == 0.0)
        missing = np.isnan(d).any(axis=-1)
        return np.asarray(np.where(zero, 0.0, np.where(missing, np.nan, total)), dtype=np.float64)

    def cov(self, delta: Any) -> np.ndarray:
        """C(Δ) = the sill − γ(Δ); the sill at Δ = 0."""
        return np.asarray(self.sill - self.gamma(delta))

    def gamma_along(self, h: Any, azimuth: Any = 0.0, plunge: Any = 0.0) -> np.ndarray:
        """γ at the distances h along the direction (azimuth clockwise from north, plunge positive down)."""
        return self.gamma(np.asarray(h, dtype=np.float64)[..., None] * _direction(azimuth, plunge))

    def cov_along(self, h: Any, azimuth: Any = 0.0, plunge: Any = 0.0) -> np.ndarray:
        return np.asarray(self.sill - self.gamma_along(h, azimuth, plunge))


# --- from a request ------------------------------------------------------------------------------------------------

_KEYS_2D = {"type": "it", "c": "cc", "a1": "hmaj", "a2": "hmin", "a3": "hmin", "ang1": "azi"}


def _prefixed(e: Exception, index: int, names: Mapping[str, str] | None = None) -> ValueError:
    """The refusal of structure `index` (from 0): its number in the message, its place in `param`."""
    param = getattr(e, "param", None) or "structures"
    key = (names or {}).get(param, param)
    message = f"Структура {index + 1}: {getattr(e, 'message', e)}"
    return bad_param(f"structures.{index}.{key}", message, getattr(e, "hint", ""))


def _item(item: Any, index: int, required: tuple[str, str, str]) -> Mapping[str, Any]:
    """A structure of a request: a mapping with its type, contribution and major range."""
    names = ", ".join(required)
    if not isinstance(item, Mapping):
        raise bad_param(
            f"structures.{index}", f"Структура {index + 1} задана неверно.", f"Ожидается объект с полями {names}."
        )
    for key in required:
        if item.get(key) is None:
            raise bad_param(
                f"structures.{index}.{key}",
                f"Структура {index + 1}: не задано поле «{key}».",
                f"У структуры должны быть тип, вклад и дальность ({names}).",
            )
    return item


def _list(value: Any, limit: int) -> Sequence[Any]:
    if value is None:
        return ()
    if isinstance(value, str | bytes | Mapping) or not isinstance(value, Sequence):
        raise bad_param("structures", "Структуры модели заданы неверно.", "Ожидается список структур.")
    if len(value) > limit:
        raise bad_param("structures", f"Структур в модели не больше {limit}.", "Уберите лишние структуры.")
    return value


def parse_model(obj: Any, *, max_structures: int = MAX_STRUCTURES) -> Model:
    """The model of a request or of `meta`: `{c0, structures: [{type, c, ang1, ang2, ang3, a1, a2, a3}],
    common_axes}`. a2 and a3 default to a1, the angles — to 0; `type` may be the code of GSLIB. Without
    `common_axes` the axes are common when the angles of all structures are equal."""
    if not isinstance(obj, Mapping):
        raise bad_param("model", "Модель вариограммы задана неверно.", "Ожидается объект с полями c0 и structures.")
    structures = []
    for i, raw in enumerate(_list(obj.get("structures"), max_structures)):
        item = _item(raw, i, ("type", "c", "a1"))
        a2, a3 = (item["a1"] if item.get(k) is None else item[k] for k in ("a2", "a3"))
        angles = (0.0 if item.get(k) is None else item[k] for k in ("ang1", "ang2", "ang3"))
        try:
            structures.append(Structure(item["type"], item["c"], item["a1"], a2, a3, *angles))
        except ValueError as e:
            raise _prefixed(e, i) from e
    common = obj.get("common_axes")
    if common is None:
        common = all(s.angles == structures[0].angles for s in structures)
    elif not isinstance(common, bool):
        raise bad_param("common_axes", "Признак «общие оси» задан неверно.", "Ожидается true или false.")
    return Model(0.0 if obj.get("c0") is None else obj["c0"], tuple(structures), common)


def model_2d(nug: Any, structures: Any = (), *, max_structures: int = MAX_STRUCTURES_2D) -> Model:
    """The model of the tab «Статистика» in the form of BF: `[{it, cc, hmaj, hmin, azi}]` — `it` a name or the
    code of GSLIB, `hmin` defaults to `hmaj`, `azi` (clockwise from north) — to 0. In 3D: a3 = hmin."""
    c0 = _share(nug, "nug", "Самородок")
    parsed = []
    for i, raw in enumerate(_list(structures, max_structures)):
        item = _item(raw, i, ("it", "cc", "hmaj"))
        hmin = item["hmaj"] if item.get("hmin") is None else item["hmin"]
        azi = 0.0 if item.get("azi") is None else item["azi"]
        try:
            parsed.append(Structure(item["it"], item["cc"], item["hmaj"], hmin, hmin, azi))
        except ValueError as e:
            raise _prefixed(e, i, _KEYS_2D) from e
    return Model(c0, tuple(parsed), all(s.angles == parsed[0].angles for s in parsed))
