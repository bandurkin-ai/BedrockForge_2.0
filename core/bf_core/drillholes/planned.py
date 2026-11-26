"""Planned holes (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29; e19-api.md §10): the parameters of the object
`objects(kind="planned_hole")` and its path. No database here, numba only inside the functions (the API process
includes the router without it).

`meta` v1: `{v, collar [x, y, z], azimuth [0; 360), dip [-90; 90] (down is negative, as 7.11), length (0; 10000] m,
change {azimuth, dip} deg per 100 m (-30…30) | null, target [x, y, z] | null, method, set_id | null}`.
The mode is one of three: a straight hole (`change` and `target` null), a hole that turns (`change`), a hole through
a target point (`target`).

  * `change`: the orientation at the depth d is `azimuth + change.azimuth · d / 100`, `dip + change.dip · d / 100`
    (the dip is clamped to ±90, the azimuth wraps); it is told as survey stations every `STATION_STEP` m, the path is
    `desurvey` of 7.11 on them (`minimum_curvature` by default; the same code as the holes of a set).
  * `target`: the hole is the straight line from the collar through the point (the arc of minimum curvature of
    zero curvature): `azimuth` and `dip` are solved from the collar and the target and stored in `meta`, what the
    user gave for them is replaced; the point is on the path at the depth |target − collar| (1e-6 m). `length` shorter
    than that distance — 422 `target_beyond_length` {need}; a hole longer than it goes on past the point; no `length`
    — the hole ends at the point; a `PUT` that moves the target of such a hole and gives no `length` ends the hole at
    the new point (the old length is the distance to the old one).

The path is given every 1 m of depth and at the end (`depth_grid`); the survey stations are what `export` writes
(`collar.csv`, `survey.csv`) — a set that takes them back has the same path.

Refusals: 422 `bad_param` {param}, `target_at_collar`, `target_beyond_length` {need}.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError

if TYPE_CHECKING:
    from .desurvey import Trajectories

KIND = "planned_hole"
META_V = 1
#: the methods of 7.11 (`desurvey.METHODS`; not imported: that module brings numba)
METHODS = ("minimum_curvature", "tangent", "balanced_tangent", "average_angle")
LENGTH_MAX = 10_000.0
CHANGE_MAX = 30.0
STATION_STEP = 10.0  # m between the survey stations of a hole that turns
GRID_STEP = 1.0  # m between the points of the path given to the window
TARGET_EPS = 1e-6  # m: the shortest distance to a target, the slack of the length


def bad(param: str, message: str, hint: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint, {"param": param})


def _number(raw: Any, what: str, param: str, lo: float, hi: float, *, open_low: bool = False,
            open_high: bool = False) -> float:  # fmt: skip
    ok = isinstance(raw, int | float) and not isinstance(raw, bool) and math.isfinite(float(raw))
    value = float(raw) if ok else math.nan
    low = value > lo if open_low else value >= lo
    high = value < hi if open_high else value <= hi
    if not (ok and low and high):
        left, right = ("(" if open_low else "["), (")" if open_high else "]")
        raise bad(param, f"{what}: нужно число {left}{lo:g}; {hi:g}{right}.", "Исправьте значение в окне.")
    return value


def point_of(raw: Any, what: str, param: str) -> tuple[float, float, float]:
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise bad(param, f"{what}: нужны три числа X, Y, Z.", "Введите координаты в проекте.")
    out = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
            raise bad(param, f"{what}: координаты — конечные числа.", "Введите координаты в проекте.")
        out.append(float(v))
    return out[0], out[1], out[2]


@dataclass(frozen=True)
class Planned:
    """The checked parameters of a planned hole (`parse`)."""

    collar: tuple[float, float, float]
    azimuth: float
    dip: float
    length: float
    change: tuple[float, float] | None = None  # (azimuth, dip) per 100 m
    target: tuple[float, float, float] | None = None
    method: str = "minimum_curvature"
    set_id: int | None = None

    def meta(self) -> dict[str, Any]:
        """The `meta` of the object: what `parse` takes back."""
        return {"v": META_V, "collar": list(self.collar), "azimuth": self.azimuth, "dip": self.dip,
                "length": self.length, "target": None if self.target is None else list(self.target),
                "change": None if self.change is None else {"azimuth": self.change[0], "dip": self.change[1]},
                "method": self.method, "set_id": self.set_id}  # fmt: skip

    @property
    def target_depth(self) -> float | None:
        """The depth of the target on the path (a straight line: the distance)."""
        if self.target is None:
            return None
        return direction_to(self.collar, self.target)[2]


def direction_to(collar: tuple[float, float, float], target: tuple[float, float, float],
                 fallback_azimuth: float = 0.0) -> tuple[float, float, float]:  # fmt: skip
    """`(azimuth, dip, distance)` of the straight line from the collar to the target (7.11: the dip down is negative;
    a vertical line keeps `fallback_azimuth`)."""
    dx, dy, dz = (t - c for t, c in zip(target, collar, strict=True))
    flat = math.hypot(dx, dy)
    dist = math.hypot(flat, dz)
    azimuth = fallback_azimuth if flat == 0.0 else math.degrees(math.atan2(dx, dy))
    if azimuth < 0.0:
        azimuth += 360.0
    if azimuth >= 360.0:
        azimuth = 0.0
    return azimuth, math.degrees(math.atan2(dz, flat)), dist


def merged(base: Mapping[str, Any] | None, raw: Mapping[str, Any]) -> dict[str, Any]:
    """The keys of `raw` over the stored `base` (a `PUT`): a mode named in `raw` takes the other one away."""
    out = dict(base or {})
    out.update({k: raw[k] for k in ("collar", "azimuth", "dip", "length", "change", "target", "method", "set_id")
                if k in raw})  # fmt: skip
    if raw.get("change") is not None and "target" not in raw:
        out["target"] = None
    if raw.get("target") is not None and "change" not in raw:
        out["change"] = None
    return out


def parse(raw: Mapping[str, Any], base: Mapping[str, Any] | None = None) -> Planned:
    """The checked parameters of `raw` over `base` (module docstring: the ranges, the mode, the refusals)."""
    m = merged(base, raw)
    collar = point_of(m.get("collar"), "Устье", "collar")
    method = m.get("method") or "minimum_curvature"
    if method not in METHODS:
        raise bad("method", "Метод расчёта траектории не поддерживается.", "Выберите метод из списка 7.11.")
    set_id = m.get("set_id")
    if set_id is not None and (isinstance(set_id, bool) or not isinstance(set_id, int) or set_id < 1):
        raise bad("set_id", "Набор скважин: нужен номер набора.", "Выберите набор из списка или оставьте пустым.")
    change_raw, target_raw = m.get("change"), m.get("target")
    if change_raw is not None and target_raw is not None:
        raise bad("change", "Задайте одно: изменения на 100 м или цель.", "Уберите одно из двух.")
    change = None
    if change_raw is not None:
        if not isinstance(change_raw, Mapping):
            raise bad("change", "Изменения на 100 м: нужны азимут и угол.", "Введите оба значения в градусах.")
        change = (_number(change_raw.get("azimuth", 0.0), "Изменение азимута на 100 м", "change.azimuth",
                          -CHANGE_MAX, CHANGE_MAX),
                  _number(change_raw.get("dip", 0.0), "Изменение угла на 100 м", "change.dip",
                          -CHANGE_MAX, CHANGE_MAX))  # fmt: skip
    if target_raw is not None:
        target = point_of(target_raw, "Цель", "target")
        wanted = None
        stored = None if base is None or base.get("target") is None else point_of(base["target"], "Цель", "target")
        moved = stored is not None and "length" not in raw and target != stored  # the old length is not the new one
        if m.get("length") is not None and not moved:
            wanted = _number(m["length"], "Длина", "length", 0.0, LENGTH_MAX, open_low=True)
        azimuth0 = 0.0
        if m.get("azimuth") is not None:
            azimuth0 = _number(m["azimuth"], "Азимут", "azimuth", 0.0, 360.0, open_high=True)
        azimuth, dip, dist = direction_to(collar, target, azimuth0)
        if dist < TARGET_EPS:
            raise bad("target", "Цель совпадает с устьем.", "Задайте цель в стороне от устья.", "target_at_collar")
        if dist > LENGTH_MAX:
            raise bad("target", f"Цель дальше {LENGTH_MAX:g} м от устья.", "Выберите цель ближе.",
                      "target_beyond_length")  # fmt: skip
        length = dist if wanted is None else wanted
        if length < dist - TARGET_EPS:
            err = bad("length", f"Длина {length:g} м не достаёт до цели ({dist:.3f} м).",
                      f"Увеличьте длину не менее чем до {math.ceil(dist * 1000) / 1000:g} м.",
                      "target_beyond_length")  # fmt: skip
            err.extra["need"] = dist
            raise err
        return Planned(collar, azimuth, dip, length, None, target, method, set_id)
    for key in ("azimuth", "dip", "length"):
        if m.get(key) is None:
            raise bad(key, "Не задано: " + {"azimuth": "азимут", "dip": "угол", "length": "длина"}[key] + ".",
                      "Введите значение в окне.")  # fmt: skip
    return Planned(collar,
                   _number(m["azimuth"], "Азимут", "azimuth", 0.0, 360.0, open_high=True),
                   _number(m["dip"], "Угол", "dip", -90.0, 90.0),
                   _number(m["length"], "Длина", "length", 0.0, LENGTH_MAX, open_low=True),
                   change, None, method, set_id)  # fmt: skip


# --- the path -----------------------------------------------------------------------------------------------------


def stations(p: Planned) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The survey stations of the hole: `(depth, azimuth, dip)`, float64. A straight hole — the collar and the end; a
    hole that turns — every `STATION_STEP` m and the end (module docstring)."""
    if p.change is None:
        depth = np.array([0.0, p.length])
        return depth, np.full(2, p.azimuth), np.full(2, p.dip)
    n = int(math.floor(p.length / STATION_STEP + 1e-12))
    depth = np.arange(n + 1, dtype=np.float64) * STATION_STEP
    if p.length - depth[-1] > 1e-9:
        depth = np.append(depth, p.length)
    else:
        depth[-1] = p.length
    azimuth = np.mod(p.azimuth + p.change[0] * depth / 100.0, 360.0)
    azimuth[azimuth >= 360.0] = 0.0
    dip = np.clip(p.dip + p.change[1] * depth / 100.0, -90.0, 90.0)
    return depth, azimuth, dip


def trajectory(p: Planned) -> Trajectories:
    """The path of the hole: `desurvey` of 7.11 (one hole, index 0) on the stations of `stations`."""
    from .desurvey import Collars, Surveys, desurvey

    depth, azimuth, dip = stations(p)
    x, y, z = p.collar
    collars = Collars(np.zeros(1, dtype=np.int64), [x], [y], [z], [p.length], [p.azimuth], [p.dip])
    surveys = Surveys(np.zeros(depth.shape[0], dtype=np.int64), depth, azimuth, dip)
    return desurvey(collars, surveys, method=p.method, n_holes=1)


def depth_grid(length: float, step: float = GRID_STEP) -> np.ndarray:
    """0, step, 2·step, … and the end `length` (bit for bit; a last step shorter than 1e-9 m is the end itself)."""
    n = int(math.floor(length / step + 1e-12))
    depth = np.arange(n + 1, dtype=np.float64) * step
    if length - depth[-1] > 1e-9:
        return np.append(depth, length)
    depth[-1] = length
    return depth


def positions(traj: Trajectories, depth: np.ndarray, hole: int = 0) -> np.ndarray:
    """float64 [n, 3]: the points of the path of the hole `hole` at the depths."""
    return traj.positions(np.full(depth.shape[0], hole, dtype=np.int64), depth)


def path(traj: Trajectories, length: float) -> dict[str, list[float]]:
    """`{depth, x, y, z}` every 1 m and at the end — the answer of the routes."""
    depth = depth_grid(length)
    xyz = positions(traj, depth)
    return {"depth": depth.tolist(), "x": xyz[:, 0].tolist(), "y": xyz[:, 1].tolist(), "z": xyz[:, 2].tolist()}


def extent_of(trajectory_path: Mapping[str, list[float]]) -> list[float]:
    """`[xmin, ymin, zmin, xmax, ymax, zmax]` of the path."""
    cols = [np.asarray(trajectory_path[k]) for k in ("x", "y", "z")]
    return [float(c.min()) for c in cols] + [float(c.max()) for c in cols]
