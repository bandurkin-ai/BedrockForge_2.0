"""The fit of a variogram model to the experimental points of the axes (ТЗ 8.6.3; ADR 0022 §7; e16-api.md
`POST /variography/fit`). numpy and scipy, float64; no numba: 3 axes × 100 lags at most, the model is a few
vector operations. The angles are fixed (the axes of the model are the directions of the calc), the types of
the structures are those of the start; unknown are the nugget, the contributions and the ranges.

Points: the lags of the axes with N ≥ `min_pairs` (for `pairwise` — `n_pw`), h̄ > 0 and a value; a gap is
skipped, never a zero. The value — γ̂ (`semivariogram`, `nscore`), 1 − ρ (`correlogram`), the pairwise relative
variogram (`pairwise`). The scale S* — σ² of the calc (`semivariogram`), 1 (`correlogram`, `nscore`), the largest
value of the points (`pairwise`; трактовка вне ТЗ: its sill has no σ²).

    r = √w·(γ̂ − γ(h̄·u_d)) / S*        w = N / h̄ (`n_h`, by default), N (`n`), N / γ² (`n_g2`)

`n_g2`: γ is that of the model of the pass before; the first pass is a fit with `n_h`, then three passes, each
from the best of the pass before (трактовка: ТЗ does not say what the first γ is). The residual of the answer
is Σ r² with the weights of the last pass.

Unknowns (ADR 0022 §7): the sill S ∈ [0; 1,2·S*] (`nscore` — S = 1 fixed) and the shares f ∈ [0; 1]:
C0 = S·f₀, c₁ = S·(1 − f₀)·f₁, …, the last structure takes the rest; a range is the share t ∈ [0; 1] of the
logarithmic segment [L/2; 2·h_max] (h_max = (nlag + 0,5)·L, ТЗ 8.6.2): a = L/2·(4·h_max / L)^t; along every
axis t_s = t_(s−1) + (1 − t_(s−1))·v_s, v ∈ [0; 1] — the ranges do not decrease from a structure to the next
(nested). The structures are nested in the order of a1 of the start; the answer — by ascending a1, `order` —
their places in the request.

A fixed parameter (`fix`: `c0`, `structures.N.c`, `structures.N.a1…a3`; the angles are always fixed) keeps its
value bit for bit: the shares then run over the free contributions (S — their sum, ≤ 1,2·S* − the fixed ones),
a free range lies between the range of the structure before and that of the next fixed one.

Solver: `scipy.optimize.least_squares` (trf), the analytic Jacobian, xtol = ftol = gtol = 1e−12, at most 200
evaluations a solve (трактовка: on noisy points trf creeps along a bound up to its 100·n — beyond 3 s; the
residual of the cap differs by < 1e−6 relative); 8 starts —
2 nuggets × 4 scales of the ranges of the start (1, ½, 2, ¼): the nugget of the holes (the first three points
along the holes extrapolated to h = 0, ≥ 0; without them — C0 of the start) and C0 of the start (when equal —
half of it, or 0,1·S*); the other contributions share S* − C0 as in the start (equally, when all are 0); the
ranges of a start that do not grow from a structure to the next are spread: (s + 1)/n of the largest. The best
by the residual, on a tie — the first of the grid; equal starts are run once.

    fit_variogram(result, model, *, kind=None, weights=None, fix=None, fix_nugget=None, tick=None) -> dict
        the answer of the API: {model, residual, n, starts, warnings, order, fixed}
    points_of(result, kind) -> (FitPoints, info {lo, hi, scale, min_pairs, axes})
    fit_model(points, model, *, kind, scale, lo, hi, weights, fix, nugget, tick) -> FitResult
    downhole_nugget(downhole, kind, min_pairs=30) -> float | None       point_weights(rule, n, h, gamma=None)

Errors — `StatsError {code, message, hint}` with `param` (API — 422): `bad_param` (`key` — not a calc of the
axes, `type`, `weights`, `fix`, `fix_nugget`, `model.…`), `too_few_points`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.geostat.rotation import bad_param, direction, geostat_error

from .exchange import api_model, model_form, value_type
from .model import Model, Structure, shape, shape_slope

WEIGHTS = ("n_h", "n", "n_g2")
SILL_FACTOR = 1.2
RANGE_SCALES = (1.0, 0.5, 2.0, 0.25)
PASSES = 3  # the passes of N / γ²
TOL = 1e-12
MAX_NFEV = 200  # evaluations of a solve: noise and flat directions at a bound take trf to 100·n and > 3 s
EDGE = 1e-6  # relative: a range or a sill this close to its upper bound is on it
GAMMA_FLOOR = 1e-6  # × S*: γ of the weights N / γ² is not below it
MIN_PAIRS = 30
_FIX = re.compile(r"structures\.(\d+)\.(c|a1|a2|a3|ang1|ang2|ang3)")
FIX_HINT = "Закрепляются c0, structures.N.c и structures.N.a1 … a3 (N — номер структуры с нуля)."
_VALUES = {"semivariogram": ("gamma", "n"), "nscore": ("gamma", "n"), "correlogram": ("rho", "n"),
           "pairwise": ("pairwise", "n_pw")}  # fmt: skip
_BOUND = {"semivariogram": "1,2·σ²", "nscore": "1", "correlogram": "1,2", "pairwise": "1,2 × наибольшее значение"}


def _num(x: float) -> str:
    return f"{x:.6g}".replace(".", ",")


def _key(message: str) -> ValueError:
    return bad_param("key", message, "Рассчитайте вариограммы по осям модели и повторите подбор.")


@dataclass(frozen=True)
class FitPoints:
    """The points of a fit: h̄ > 0, the value, N, the unit direction u (K × 3), the number of the axis."""

    h: np.ndarray
    value: np.ndarray
    n: np.ndarray
    u: np.ndarray
    axis: np.ndarray

    @property
    def size(self) -> int:
        return int(self.h.size)


@dataclass
class FitResult:
    model: Model
    residual: float
    n: int
    starts: int
    order: list[int]
    fixed: list[str]
    warnings: list[str] = field(default_factory=list)


def point_weights(rule: str, n: Any, h: Any, gamma: Any = None) -> np.ndarray:
    """w of the points: N / h̄, N, or N / γ²."""
    nn = np.asarray(n, dtype=np.float64)
    if rule == "n_h":
        return nn / np.asarray(h, dtype=np.float64)
    if rule == "n":
        return nn.copy()
    g = np.asarray(gamma, dtype=np.float64)
    return nn / (g * g)


# --- the points of a calc ------------------------------------------------------------------------------------------


def _floats(d: Mapping[str, Any], name: str, size: int | None) -> np.ndarray:
    raw = d.get(name)
    if raw is None:
        return np.full(size or 0, np.nan)
    try:
        out = np.array([math.nan if v is None else v for v in raw], dtype=np.float64)
    except (TypeError, ValueError):
        raise _key(f"Результат расчёта повреждён: поле «{name}».") from None
    if out.ndim != 1 or (size is not None and out.size != size):
        raise _key(f"Результат расчёта повреждён: длина поля «{name}».")
    return out


def _series(d: Mapping[str, Any], kind: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(h̄, value, N) of a direction or of the variogram along the holes."""
    value_key, n_key = _VALUES[kind]
    h = _floats(d, "h", None)
    value = _floats(d, value_key, h.size)
    if kind == "correlogram":
        value = 1.0 - value
    return h, value, _floats(d, n_key, h.size)


def downhole_nugget(downhole: Any, kind: str, min_pairs: int = MIN_PAIRS) -> float | None:
    """C0 of the start: the line of the first three points along the holes (N ≥ `min_pairs`) at h = 0, ≥ 0;
    fewer than two points — None."""
    if not isinstance(downhole, Mapping):
        return None
    h, v, n = _series(downhole, kind)
    idx = np.flatnonzero(np.isfinite(h) & (h > 0.0) & np.isfinite(v) & (n >= min_pairs))[:3]
    if idx.size < 2:
        return None
    x, y = h[idx], v[idx]
    xm, ym = float(np.mean(x)), float(np.mean(y))
    slope = float(np.sum((x - xm) * (y - ym)) / np.sum((x - xm) ** 2))
    return max(ym - slope * xm, 0.0) + 0.0


def _int(value: Any, default: int) -> int:
    try:
        return default if value is None else int(value)
    except (TypeError, ValueError):
        return default


def points_of(result: Any, kind: str) -> tuple[FitPoints, dict[str, Any]]:
    """The points of the axes of a calc (the form of `job.result`) and the bounds of the fit."""
    if not isinstance(result, Mapping) or not isinstance(result.get("directions"), Sequence):
        raise _key("Результат расчёта вариограмм не найден или повреждён.")
    directions = list(result["directions"])
    if not directions or not all(isinstance(d, Mapping) for d in directions):
        raise _key("В результате расчёта нет вариограмм по осям.")
    if any(d.get("axis") not in (1, 2, 3) for d in directions):
        raise _key("Подбор — по осям модели: расчёт по списку направлений подбирать нельзя.")
    if kind == "nscore" and result.get("transform") != "nscore":
        raise bad_param(
            "type", "Подбор нормальных оценок — только для расчёта с преобразованием nscore.", "Выберите величину."
        )
    params: Mapping[str, Any] = result["params"] if isinstance(result.get("params"), Mapping) else {}
    min_pairs = _int(params.get("min_pairs"), MIN_PAIRS)
    parts: list[tuple[np.ndarray, ...]] = []
    for d in directions:
        h, value, n = _series(d, kind)
        ok = np.isfinite(h) & (h > 0.0) & np.isfinite(value) & (n >= min_pairs)
        try:
            u = direction(d.get("az"), d.get("plunge"))
        except ValueError:
            raise _key(f"Результат расчёта повреждён: направление оси {d['axis']}.") from None
        k = int(np.count_nonzero(ok))
        parts.append((h[ok], value[ok], n[ok], np.tile(u, (k, 1)), np.full(k, int(d["axis"]))))
    h, value, n, u = (np.concatenate([p[i] for p in parts]) for i in range(4))
    pts = FitPoints(h, value, n, u, np.concatenate([p[4] for p in parts]).astype(np.int64))
    lags = _floats(directions[0], "lags", None)
    lag = float(params.get("lag") or (lags[0] if lags.size else math.nan))
    nlag = _int(params.get("nlag"), lags.size)
    if not (math.isfinite(lag) and lag > 0.0 and nlag > 0):
        raise _key("В результате расчёта нет лага.")
    h_max = (nlag + 0.5) * lag
    sigma2 = result.get("sigma2")
    top = float(np.max(pts.value)) if pts.size else 0.0
    if kind in ("correlogram", "nscore"):
        scale = 1.0
    elif kind == "semivariogram" and isinstance(sigma2, int | float) and math.isfinite(sigma2) and sigma2 > 0:
        scale = float(sigma2)
    else:
        scale = top if top > 0.0 else 1.0
    axes = sorted({int(d["axis"]) for d in directions})
    return pts, {"lo": lag / 2.0, "hi": 2.0 * h_max, "scale": scale, "min_pairs": min_pairs, "axes": axes}


# --- the unknowns -----------------------------------------------------------------------------------------------------


class _Layout:
    """θ ↔ the parameters of the model p = (C0, c_1…c_n, a_11, a_12, a_13, …, a_n3) and dp/dθ."""

    def __init__(self, model: Model, fixed: set[int], *, sill_fixed: float | None, scale: float, lo: float,
                 hi: float) -> None:  # fmt: skip
        ns = len(model.structures)
        self.ns, self.scale, self.lo, self.span = ns, scale, lo, math.log(hi / lo)
        self.p0 = np.array([model.c0, *[s.c for s in model.structures],
                            *[a for s in model.structures for a in s.ranges]], dtype=np.float64)  # fmt: skip
        self.fixed = fixed
        self.sills = [j for j in range(ns + 1) if j not in fixed]
        cf = 0.0
        for j in range(ns + 1):
            if j in fixed:
                cf += float(self.p0[j])
        self.cf = cf
        limit = (sill_fixed if sill_fixed is not None else SILL_FACTOR * scale) - cf
        self.s_top = max(limit, 0.0) / scale
        self.s_free = bool(self.sills) and sill_fixed is None and limit > 0.0
        self.s_value = max(limit, 0.0)  # S when it is not free
        self.f0 = int(self.s_free)
        self.v_index: dict[tuple[int, int], int] = {}
        nf = self.f0 + max(len(self.sills) - 1, 0)
        for s in range(ns):
            for i in range(3):
                if 1 + ns + 3 * s + i not in fixed:
                    self.v_index[(s, i)] = nf + len(self.v_index)
        self.size = nf + len(self.v_index)
        self.lb = np.zeros(self.size)
        self.ub = np.ones(self.size)
        if self.s_free:
            self.ub[0] = self.s_top
        self.tau = np.log(np.maximum(self.p0[1 + ns :], 1e-300) / lo) / self.span  # t of a fixed range
        self.upper = np.ones((ns, 3))
        for s in range(ns):
            for i in range(3):
                later = [t for t in range(s + 1, ns) if 1 + ns + 3 * t + i in fixed]
                if later:
                    self.upper[s, i] = min(1.0, float(self.tau[3 * later[0] + i]))

    def a_index(self, s: int, i: int) -> int:
        return 1 + self.ns + 3 * s + i

    def natural(self, theta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        p, dp = self.p0.copy(), np.zeros((self.p0.size, self.size))
        m = len(self.sills)
        if m:
            if self.s_free:
                total, d_total = self.scale * float(theta[0]), np.zeros(self.size)
                d_total[0] = self.scale
            else:
                total, d_total = self.s_value, np.zeros(self.size)
            rem, d_rem = 1.0, np.zeros(self.size)
            for k, j in enumerate(self.sills):
                if k < m - 1:
                    f, at = float(theta[self.f0 + k]), self.f0 + k
                    phi, d_phi = rem * f, d_rem * f
                    d_phi[at] += rem
                    d_next = d_rem * (1.0 - f)
                    d_next[at] -= rem
                    rem, d_rem = rem * (1.0 - f), d_next
                else:
                    phi, d_phi = rem, d_rem
                p[j] = total * phi
                dp[j] = d_total * phi + total * d_phi
        for i in range(3):
            t_prev, d_prev = 0.0, np.zeros(self.size)
            for s in range(self.ns):
                j = self.a_index(s, i)
                if j in self.fixed:
                    t, dt = float(self.tau[3 * s + i]), np.zeros(self.size)
                else:
                    lower, d_lower = (t_prev, d_prev) if s > 0 and t_prev > 0.0 else (0.0, np.zeros(self.size))
                    width = float(self.upper[s, i]) - lower
                    if width > 0.0:
                        v = float(theta[self.v_index[(s, i)]])
                        t, dt = lower + width * v, d_lower * (1.0 - v)
                        dt[self.v_index[(s, i)]] += width
                    else:
                        t, dt = lower, d_lower.copy()
                    a = self.lo * math.exp(self.span * t)
                    p[j], dp[j] = a, a * self.span * dt
                t_prev, d_prev = t, dt
        return p, dp

    def start(self, c0: float | None, k: float, model: Model) -> np.ndarray:
        """θ of a start: the nugget `c0` (free C0 only), the ranges of `model` × k."""
        theta = np.zeros(self.size)
        m = len(self.sills)
        free_total = min(max(self.scale - self.cf, 0.0), self.s_top * self.scale) if self.s_free else self.s_value
        if self.s_free:
            theta[0] = free_total / self.scale
        own = [float(self.p0[j]) for j in self.sills if j > 0]
        x: list[float] = []
        rest = free_total
        if 0 in self.sills:
            x.append(min(max(c0 or 0.0, 0.0), free_total))
            rest -= x[0]
        share = sum(own)
        x += [rest * (c / share if share > 0.0 else 1.0 / len(own)) for c in own]
        rem = free_total
        for q in range(m - 1):
            theta[self.f0 + q] = min(max(x[q] / rem, 0.0), 1.0) if rem > 0.0 else 0.5
            rem -= x[q]
        for i in range(3):
            base = [s.ranges[i] for s in model.structures]
            free = [base[s] for s in range(self.ns) if (s, i) in self.v_index]
            if any(b <= a for a, b in zip(free, free[1:], strict=False)):  # not growing: spread
                top = max(base)
                base = [top * (s + 1) / self.ns for s in range(self.ns)]
            t_prev = 0.0
            for s in range(self.ns):
                if (s, i) not in self.v_index:
                    t_prev = float(self.tau[3 * s + i])
                    continue
                a = min(max(base[s] * k, self.lo), self.lo * math.exp(self.span))
                t = math.log(a / self.lo) / self.span
                lower = t_prev if s > 0 and t_prev > 0.0 else 0.0
                width = float(self.upper[s, i]) - lower
                v = min(max((t - lower) / width, 0.0), 1.0) if width > 0.0 else 0.5
                theta[self.v_index[(s, i)]] = v
                t_prev = lower + width * v if width > 0.0 else lower
        return theta


class _Problem:
    """The residuals of the points and their Jacobian for a layout."""

    def __init__(self, points: FitPoints, model: Model, layout: _Layout) -> None:
        self.layout, self.types = layout, [s.type for s in model.structures]
        x, y, z = (points.h * points.u[:, i] for i in range(3))
        self.proj = [np.array([x * u[i, 0] + y * u[i, 1] + z * u[i, 2] for i in range(3)])
                     for u in (s.axes for s in model.structures)]  # fmt: skip
        self.value, self.n, self.h = points.value, points.n, points.h
        self.sw = np.ones(points.size)

    def weigh(self, w: np.ndarray) -> None:
        self.sw = np.sqrt(w) / self.layout.scale

    def gamma(self, p: np.ndarray, grad: bool = False) -> tuple[np.ndarray, np.ndarray | None]:
        ns = self.layout.ns
        total: Any = float(p[0])
        g_p = np.zeros((self.h.size, p.size)) if grad else None
        if g_p is not None:
            g_p[:, 0] = 1.0
        for s, kind in enumerate(self.types):
            a = p[1 + ns + 3 * s : 4 + ns + 3 * s]
            q = self.proj[s] / a[:, None]
            hr = np.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2])
            g = shape(kind, hr)
            total = total + float(p[1 + s]) * g
            if g_p is not None:
                g_p[:, 1 + s] = g
                inv = np.where(hr > 0.0, 1.0 / np.maximum(hr, 1e-300), 0.0)
                slope = float(p[1 + s]) * shape_slope(kind, hr) * inv
                for i in range(3):
                    g_p[:, 1 + ns + 3 * s + i] = -slope * q[i] * q[i] / a[i]
        return np.asarray(total + np.zeros(self.h.size)), g_p

    def residuals(self, theta: np.ndarray) -> np.ndarray:
        p, _ = self.layout.natural(theta)
        return np.asarray(self.sw * (self.value - self.gamma(p)[0]))

    def jacobian(self, theta: np.ndarray) -> np.ndarray:
        p, dp = self.layout.natural(theta)
        g_p = self.gamma(p, grad=True)[1]
        assert g_p is not None
        return np.asarray(-self.sw[:, None] * (g_p @ dp))

    def solve(self, theta0: np.ndarray) -> tuple[np.ndarray, float]:
        if self.layout.size == 0:
            r = self.residuals(theta0)
            return theta0, float(np.dot(r, r))
        from scipy.optimize import least_squares

        res = least_squares(self.residuals, theta0, jac=self.jacobian, bounds=(self.layout.lb, self.layout.ub),
                            method="trf", xtol=TOL, ftol=TOL, gtol=TOL, max_nfev=MAX_NFEV)  # fmt: skip
        r = self.residuals(res.x)
        return np.asarray(res.x), float(np.dot(r, r))


# --- the fit ----------------------------------------------------------------------------------------------------------


def _fix_set(fix: Any, fix_nugget: Any, ns: int) -> set[tuple[str, int]]:
    """{("c0", -1), ("c", s), ("a1", s) …} of `fix` — the indices of the request."""
    if fix is None:
        items: Sequence[Any] = ()
    elif isinstance(fix, str | bytes | Mapping) or not isinstance(fix, Sequence):
        raise bad_param("fix", "Закреплённые параметры заданы неверно: ожидается список путей.", FIX_HINT)
    else:
        items = fix
    out: set[tuple[str, int]] = set()
    for path in items:
        key = path.strip().removeprefix("model.") if isinstance(path, str) else ""
        match = _FIX.fullmatch(key)
        if key == "c0":
            out.add(("c0", -1))
        elif match and int(match[1]) < ns:
            if not match[2].startswith("ang"):  # the angles are always fixed
                out.add((match[2], int(match[1])))
        else:
            raise bad_param("fix", f"Неизвестный закреплённый параметр «{path}».", FIX_HINT)
    if fix_nugget is not None and not isinstance(fix_nugget, bool):
        raise bad_param("fix_nugget", "Признак «закрепить самородок» задан неверно.", "Ожидается true или false.")
    if fix_nugget:
        out.add(("c0", -1))
    return out


def _weights(rule: Any) -> str:
    if rule is None or rule == "":
        return WEIGHTS[0]
    if rule not in WEIGHTS:
        raise bad_param("weights", f"Неизвестное правило весов «{rule}».",
                        "Веса подбора: n_h (N / h, по умолчанию), n (N) или n_g2 (N / γ²).")  # fmt: skip
    return str(rule)


def _paths(fixed: set[tuple[str, int]], place: list[int]) -> list[str]:
    """The paths of the fixed parameters in the answer; `place[i]` — the answer index of request structure i."""
    keys = [(-1, 0, "c0")] if ("c0", -1) in fixed else []
    keys += sorted((place[s], ("c", "a1", "a2", "a3").index(k), f"structures.{place[s]}.{k}")
                   for k, s in fixed if s >= 0)  # fmt: skip
    return [k[2] for k in keys]


def fit_model(points: FitPoints, model: Model, *, kind: str = "semivariogram", scale: float, lo: float, hi: float,
              weights: str = "n_h", fix: set[tuple[str, int]] | None = None, nugget: float | None = None,
              tick: Callable[[], None] | None = None) -> FitResult:  # fmt: skip
    """The fit of `model` (its types and angles) to `points`; `fix` — of `_fix_set`, the indices of `model`."""
    fix = fix or set()
    ns = len(model.structures)
    order = sorted(range(ns), key=lambda s: model.structures[s].a1)  # stable: a tie keeps the request order
    work = Model(model.c0, tuple(model.structures[s] for s in order), model.common_axes)
    place = [order.index(s) for s in range(ns)]
    index = {"a1": 1 + ns, "a2": 2 + ns, "a3": 3 + ns}
    fixed = {0 if k == "c0" else (1 + place[s] if k == "c" else index[k] + 3 * place[s]) for k, s in fix}
    layout = _Layout(work, fixed, sill_fixed=1.0 if kind == "nscore" else None, scale=scale, lo=lo, hi=hi)
    problem = _Problem(points, work, layout)
    warnings: list[str] = []
    if points.size < layout.size:
        warnings.append(f"Точек меньше, чем неизвестных: {points.size} < {layout.size} — модель определена"
                        " неоднозначно; закрепите часть параметров.")  # fmt: skip
    if layout.cf > (1.0 if kind == "nscore" else SILL_FACTOR * scale) * (1.0 + EDGE):
        warnings.append(f"Сумма закреплённых вкладов больше порога {_BOUND[kind]}: свободные вклады равны нулю.")
    c_start = nugget if nugget is not None else work.c0
    other = work.c0 if abs(work.c0 - c_start) > 1e-9 * scale else (0.5 * c_start if c_start > 0 else 0.1 * scale)
    starts: list[np.ndarray] = []
    for c0 in (c_start, other) if 0 not in fixed else (None,):
        for k in RANGE_SCALES:
            theta = layout.start(c0, k, work)
            if not any(np.array_equal(theta, t) for t in starts):
                starts.append(theta)
    problem.weigh(point_weights("n_h" if weights == "n_g2" else weights, points.n, points.h))
    best, best_cost = starts[0], math.inf
    for theta0 in starts:
        if tick is not None:
            tick()
        theta, cost = problem.solve(theta0)
        if cost < best_cost:
            best, best_cost = theta, cost
    for _ in range(PASSES if weights == "n_g2" else 0):
        if tick is not None:
            tick()
        prior = problem.gamma(layout.natural(best)[0])[0]
        problem.weigh(point_weights("n_g2", points.n, points.h, np.maximum(prior, GAMMA_FLOOR * scale)))
        best, best_cost = problem.solve(best)
    p, _ = layout.natural(best)
    structures = []
    for i, s in enumerate(work.structures):
        a1, a2, a3 = (float(a) for a in p[1 + ns + 3 * i : 4 + ns + 3 * i])
        structures.append(Structure(s.type, float(p[1 + i]), a1, a2, a3, *s.angles))
    final = sorted(range(ns), key=lambda s: structures[s].a1)
    if layout.s_free and layout.s_top > 0.0 and float(best[0]) >= layout.s_top * (1.0 - EDGE):
        warnings.append(f"Порог на границе {_BOUND[kind]} = {_num(layout.s_top * scale + layout.cf)}: данные выше"
                        " порога; проверьте величину и лаги.")  # fmt: skip
    edge = sorted((final.index(s) + 1, i + 1) for s, i in layout.v_index if p[layout.a_index(s, i)] >= hi * (1 - EDGE))
    if edge:
        names = "; ".join(f"структура {s}, ось {i}" for s, i in edge)
        warnings.append(f"Дальность на границе 2·h_max = {_num(hi)} м: {names}.")
    fitted = Model(float(p[0]), tuple(structures[s] for s in final), work.common_axes)
    place_out = [final.index(place[s]) for s in range(ns)]
    return FitResult(fitted, best_cost, points.size, len(starts), [order[s] for s in final], _paths(fix, place_out),
                     warnings)  # fmt: skip


def fit_variogram(result: Any, model: Any, *, kind: Any = None, weights: Any = None, fix: Any = None,
                  fix_nugget: Any = None, tick: Callable[[], None] | None = None) -> dict[str, Any]:  # fmt: skip
    """`POST /variography/fit`: the fit of `model` (the start) to the axes of a calc `result` (`job.result`);
    `kind` — the quantity (`type` of the body; by default that of the model)."""
    model_kind, start = api_model(model)
    kind = model_kind if kind is None else value_type(kind)
    rule = _weights(weights)
    fixed = _fix_set(fix, fix_nugget, len(start.structures))
    points, info = points_of(result, kind)
    if points.size == 0:
        raise geostat_error(
            "too_few_points",
            f"Нет точек для подбора: ни один лаг осей не набрал {info['min_pairs']} пар.",
            "Увеличьте лаг или допуски, уменьшите порог числа пар или выберите другие оси.",
        )
    params: Mapping[str, Any] = result["params"] if isinstance(result.get("params"), Mapping) else {}
    nugget = None if not params.get("downhole", True) else downhole_nugget(result.get("downhole"), kind,
                                                                           info["min_pairs"])  # fmt: skip
    got = fit_model(points, start, kind=kind, scale=info["scale"], lo=info["lo"], hi=info["hi"], weights=rule,
                    fix=fixed, nugget=nugget, tick=tick)  # fmt: skip
    few = info["min_pairs"]
    empty = [f"Ось {a}: нет точек с числом пар не меньше {few} — по этой оси модель не подбиралась."
             for a in info["axes"] if not np.any(points.axis == a)]  # fmt: skip
    return {"model": model_form(kind, got.model), "residual": got.residual, "n": got.n, "starts": got.starts,
            "warnings": empty + got.warnings, "order": got.order, "fixed": got.fixed}  # fmt: skip
