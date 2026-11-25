"""The anisotropy of the implicit model (ТЗ 8.9.2, 8.6.3; ADR 0026, decision 11; Э19, 19.9).

    f(x) = Σ λ_i·φ(‖A·(x − x_i)‖) + c0 + c·(A·x),   A = diag(1, a1/a2, a1/a3)·R = diag(1, 1/r2, 1/r3)·R

Three ways, one rule — every matrix comes from `geostat.trend.matrix` (the rotation of 8.6.3, the ratios `[1, r2, r3]`):

none        A = I; `global_matrix` is None, `solve_patches` / `PatchField` get no matrices.
global      A = `matrix_of(spec, trend)`: the matrix of a global trend (`trend_id`) or of the angles and ratios typed in
            the model (`anisotropy {ang1, ang2, ang3, ratios}` — the same form, the same function). The points are
            turned ONCE, by `octree.build_patches(…, matrix=A)` (`PatchSet.matrix`): the octree, the balls, the weight
            W and the systems all live in the space of A·x; `solve_patches` and `PatchField` apply `patches.matrix`
            themselves. The caller passes A to `build_patches` and nothing else.
structural  every patch has its own A_j = diag(1, 1/r2, 1/r3)·R_j, R_j = `trend_struct.local_axes` at the CENTRE of the
            patch. The octree, the balls and W stay in the project coordinates (decision 11): `build_patches(…)` WITHOUT
            a matrix, `A_j = local(trend_meta, patches.centers, source=…)`, then
            `solve_patches(…, matrices=list(A_j))` and `PatchField(patches, sols, matrices=list(A_j))`.

The frame of a patch with A_j (the choice of 19.9, one path in `solve.py` and `evaluate.py`). The points of the patch
and its centre are turned, `y = A_j·x`, `c = A_j·c_j`, and the system is built around `c` with the scale
`R_j·‖A_j‖₂` — the radius of the BALL of the patch after A_j: P = (1, (y − c) / scale). The field of a patch does not
depend on the scale (the polynomial space is the same, φ and η are in metres: `system.py`), so the scale only decides
the conditioning and the rounding, and the choice is measured (600 points in a ball, random A, cond₁ of the
equilibrated system, scale R_j against R_j·‖A_j‖₂): linear kernel, ratios 1 : 0,5 : 0,5 — 4,4e4 against 7,5e4; 1 : 0,2 :
0,05 — 3,2e5 against 1,9e5; 1 : 0,05 : 0,05 — 4,8e5 against 1,6e5; cubic kernel — 1,3e8 against 1,4e8, 1,2e11 against
2,1e9, 2,6e11 against 1,0e9. With R_j alone the turned cloud leaves the unit ball by the factor ‖A_j‖₂ and the
cubic block loses a hundred times in cond₁; with R_j·‖A_j‖₂ a mild anisotropy costs a factor under 2 and a strong one
gains up to 250. For a rotation (‖A_j‖₂ = 1) the scale is R_j (to the rounding of ‖A_j‖₂).
`evaluate.PatchField` reads the same `scale` of the solution and divides `A_j·(x − c_j)` by it, so the bytes of the
system and of the field are one convention. A data point evaluated differs from the point of the system only by the
rounding of `A·x − A·c` against `A·(x − c)` (`test_local_frame_at_large_coordinates`: coordinates of 7e6 m).

Checks (422, `{code, message, hint}` in Russian, `param` in `extra`): a matrix that is not finite 3 × 3, singular, or
with cond(A) above `COND_MAX` (the ratios of the axes beyond 1 : 10⁶) — `bad_anisotropy`; typed angles and ratios — the
`bad_param` of `geostat.rotation` with `anisotropy.` before its `param`; a structural trend where one matrix is needed
or an unknown kind — `bad_anisotropy`. Patches whose centre has no axes (a NaN row of `local_axes`: no triangle of the
wireframe, no coordinates) — `bad_anisotropy` with the numbers of the patches (`extra.patches`, 0-based, at most 20).
A patch centre with no measurement within R_тр takes the nearest one and `report["journal"]` says how many did.

numba is not imported here (the trend kernels come with `local_axes`).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from bf_core.api.errors import ApiError

F8 = NDArray[np.float64]

NONE, GLOBAL, STRUCTURAL = "none", "global", "structural"
#: the largest cond(A) = (the longest stretch)/(the shortest): the ratios of the axes beyond 1 : 10⁶ are a typing error.
COND_MAX = 1e6
_FIRST_PATCHES = 20  # numbers of the patches shown in `extra.patches`
_ORTHO = 1e-9  # rows of A are taken as orthogonal (A = diag·R) below this relative dot product


def _bad(message: str, hint: str, **extra: Any) -> ApiError:
    return ApiError(422, "bad_anisotropy", message, hint, extra or None)


def _api(e: ValueError, prefix: str = "anisotropy") -> ApiError:
    """A `StatsError` of `geostat.rotation` / `geostat.trend` (`bad_param` and its `param`) as the `ApiError` of the
    model: the same code and text, `param` — `anisotropy.<param>`."""
    code = str(getattr(e, "code", "bad_param"))
    param = getattr(e, "param", None)
    hint = str(getattr(e, "hint", "") or "")
    message = str(getattr(e, "message", None) or e)
    return ApiError(422, code, message, hint, {"param": f"{prefix}.{param}" if param else prefix})


# --- the matrix --------------------------------------------------------------------------------------------------


def check_matrix(a: ArrayLike, *, param: str = "anisotropy") -> F8:
    """A as a contiguous float64 3 × 3: finite, not singular, cond(A) ≤ `COND_MAX`; else 422 `bad_anisotropy`."""
    m = np.array(a, dtype=np.float64, order="C")
    if m.shape != (3, 3) or not np.isfinite(m).all():
        raise _bad("Матрица анизотропии должна быть конечной матрицей 3 × 3.",
                   "Проверьте углы и отношения осей эллипсоида.", param=param)  # fmt: skip
    s = np.linalg.svd(m, compute_uv=False)
    if not (s[-1] > 0.0 and s[0] / s[-1] <= COND_MAX):
        raise _bad("Анизотропия вырождена: оси эллипсоида отличаются более чем в миллион раз или равны нулю.",
                   "Отношения осей должны быть положительными, наибольшее к наименьшему — не более 1 000 000.",
                   param=param)  # fmt: skip
    return m


def _typed(spec: Any) -> Any:
    """The typed anisotropy of a model spec (`BuildSpec.anisotropy`), or the bare thing the caller passed."""
    return spec.anisotropy if hasattr(spec, "anisotropy") else spec


def _shape_matrix(raw: Any) -> F8:
    """A of a trend meta / the typed anisotropy: `trend.matrix` of {ang1…3, ratios} — the one path of every A."""
    from bf_core.geostat import trend as tr

    if isinstance(raw, Mapping) and raw.get("kind") == tr.STRUCTURAL:
        raise _bad("У структурного тренда нет одной матрицы осей.",
                   "Для RBF оси берутся в центрах участков (anisotropy.local).", param="trend_id")  # fmt: skip
    data = raw.canonical() if hasattr(raw, "canonical") else raw
    try:
        return tr.matrix(data)
    except ValueError as e:
        raise _api(e) from e


def kind_of(spec: Any = None, trend: Mapping[str, Any] | None = None) -> str:
    """`none`, `global` or `structural`: by the `meta` of the chosen trend, else by the typed anisotropy."""
    typed = _typed(spec)
    if trend is None:
        if getattr(spec, "trend_id", None) is not None:
            raise _bad("Тренд выбран, но не загружен.", "Это ошибка вызова: передайте метаданные тренда.",
                       param="trend_id")  # fmt: skip
        return NONE if typed is None else GLOBAL
    if typed is not None:
        raise _bad("Тренд и анизотропия вручную заданы вместе.", "Оставьте что-то одно.", param="anisotropy")
    kind = trend.get("kind", "global")
    if kind not in ("global", "structural"):
        raise _bad("У тренда неизвестный вид.", "Выберите глобальный или структурный тренд.", param="trend_id")
    return STRUCTURAL if kind == "structural" else GLOBAL


def matrix_of(spec: Any = None, trend: Mapping[str, Any] | None = None) -> F8:
    """The global A, float64 3 × 3 (contract of `e19-api.md`): of the global `trend` (its `meta`), else of the typed
    anisotropy (`spec.anisotropy`, or a bare `Anisotropy` / mapping / `trend.Shape`); I without anisotropy and for a
    structural trend (its axes are per patch: `local`)."""
    kind = kind_of(spec, trend)
    if kind != GLOBAL:
        return np.eye(3, dtype=np.float64)
    return check_matrix(_shape_matrix(trend if trend is not None else _typed(spec)))


def global_matrix(spec: Any = None, trend: Mapping[str, Any] | None = None) -> F8 | None:
    """What `octree.build_patches(…, matrix=)` takes: A of `matrix_of`, or None for no anisotropy, a structural trend
    and an A that is exactly I (no needless transform, no change of the bytes of the points and of the hashes)."""
    if kind_of(spec, trend) != GLOBAL:
        return None
    a = matrix_of(spec, trend)
    return None if np.array_equal(a, np.eye(3)) else a


# --- the structural matrices ----------------------------------------------------------------------------------------


def local(
    trend: Mapping[str, Any],
    centres: ArrayLike,
    *,
    source: Any,
    threads: int = 1,
    tick: Callable[[float], None] | None = None,
    report: dict[str, Any] | None = None,
) -> F8:
    """A_j (m × 3 × 3, float64) at the `centres` (m × 3, PROJECT coordinates: `patches.centers` of a `PatchSet` built
    without a matrix) of a structural trend: R_j = `trend_struct.local_axes`, A_j = diag(1, 1/r2, 1/r3)·R_j with the
    ratios of the trend. `source` is `trend_struct.load_source(project, trend)`. `tick(fraction)` — as `local_axes`.
    `report` gets `points`, `nearest` (centres that took the nearest measurement), `measurements`, `journal`.
    422 `bad_anisotropy`: a trend that is not structural, a centre without axes."""
    from bf_core.geostat import trend as tr
    from bf_core.geostat import trend_struct as st

    if trend.get("kind") != tr.STRUCTURAL:
        raise _bad("Это не структурный тренд.", "Выберите структурный тренд.", param="trend_id")
    try:
        ratios = tr.ratios_of(trend.get("ratios"))
    except ValueError as e:
        raise _api(e, "trend") from e
    _one, r2, r3 = ratios
    check_matrix(np.diag([1.0, 1.0 / r2, 1.0 / r3]), param="trend.ratios")
    q = np.ascontiguousarray(centres, dtype=np.float64)
    if q.ndim != 2 or q.shape[1] != 3 or not np.isfinite(q).all():
        raise _bad("Центры участков должны быть конечной таблицей n × 3.", "Это ошибка вызова.", param="centres")
    rep: dict[str, Any] = {}
    try:
        frames = st.local_axes(trend, q, source=source, threads=threads, tick=tick, report=rep)
    except ValueError as e:
        raise _api(e, "trend") from e
    if report is not None:
        report.update(rep)
    bad = np.flatnonzero(~np.isfinite(frames).all(axis=(1, 2)))
    if bad.size:
        shown = [int(k) for k in bad[:_FIRST_PATCHES]]
        raise _bad(f"У участков ({bad.size}) не определены оси структурного тренда: рядом нет замеров или каркаса "
                   f"(например, участок № {shown[0] + 1}).",
                   "Проверьте источник тренда и охват модели или выберите глобальный тренд.",
                   param="trend_id", patches=shown)  # fmt: skip
    # the same product as `trend.matrix`: R_j with the rows multiplied by (1, 1/r2, 1/r3), bit for bit
    out = frames * np.array([1.0, 1.0 / r2, 1.0 / r3])[None, :, None]
    return np.ascontiguousarray(out)


def patch_hashes(hashes: Sequence[str], matrices: Iterable[Any]) -> tuple[str, ...]:
    """The hashes of the patches (`PatchSet.hashes`, the points of the patch) with its A_j in them: a changed trend
    changes the solution of a patch whose points did not move, so the cache of 19.12 must see it. `None` / identity
    — the hash as it is. SHA-256 over the hash and the little-endian bytes of A_j."""
    out: list[str] = []
    for h, a in zip(hashes, list(matrices), strict=True):
        if a is None:
            out.append(h)
            continue
        d = hashlib.sha256(h.encode("ascii"))
        d.update(b"A")
        d.update(np.ascontiguousarray(a, dtype="<f8").tobytes())
        out.append(d.hexdigest())
    return tuple(out)


def stretch_of(a: ArrayLike) -> float:
    """‖A‖₂: the longest stretch of A (1 for a rotation) — the radius of the turned unit ball."""
    return float(np.linalg.svd(np.asarray(a, dtype=np.float64), compute_uv=False)[0])


# --- the ellipsoid of a matrix ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ellipsoid:
    """The ball ‖A·x‖ ≤ 1 scaled by `size` — the ellipsoid of 8.6.3: `semi_axes` (a1, a2, a3) in metres, the angles of
    GSLIB of its axes (`geostat.rotation`) and the ratios `(1, a2 / a1, a3 / a1)`; `stretch` = ‖A‖₂."""

    semi_axes: tuple[float, float, float]
    ang1: float
    ang2: float
    ang3: float
    ratios: tuple[float, float, float]
    stretch: float

    def as_dict(self) -> dict[str, Any]:
        return {"semi_axes": list(self.semi_axes), "ang1": self.ang1, "ang2": self.ang2, "ang3": self.ang3,
                "ratios": list(self.ratios), "stretch": self.stretch}  # fmt: skip


def ellipsoid_of(a: ArrayLike, size: float = 1.0) -> Ellipsoid:
    """The semi-axes and the angles of the ellipsoid of A (for the properties and the 3D window; no graphics).
    A = diag(1, 1/r2, 1/r3)·R (the rows orthogonal) gives the angles and ratios of `trend.shape_of` — the way back of
    `trend.matrix` (1e−12), the axes in the order of the trend (a1 = `size`); any other A — its SVD: semi-axes
    `size`/σ from the longest, the axes are the rows of Vᵀ (a right-handed frame)."""
    from bf_core.geostat import rotation as rot
    from bf_core.geostat import trend as tr

    if not (isinstance(size, int | float) and math.isfinite(size) and size > 0.0):
        raise _bad("Размер эллипсоида должен быть положительным числом.", "Задайте размер в метрах.", param="size")
    m = check_matrix(a)
    g = m @ m.T
    d = np.sqrt(np.diag(g))
    off = np.abs(g - np.diag(np.diag(g))).max() / float(d.max() ** 2)
    stretch = stretch_of(m)
    try:
        if off <= _ORTHO and abs(d[0] - 1.0) <= 1e-12:
            sh = tr.shape_of(m)
            return Ellipsoid((size, size * sh.ratios[1], size * sh.ratios[2]), sh.ang1, sh.ang2, sh.ang3,
                             sh.ratios, stretch)  # fmt: skip
        _u, s, vt = np.linalg.svd(m)
        vt = vt[::-1]  # the longest semi-axis (the smallest σ) first
        s = s[::-1]
        if np.linalg.det(vt) < 0.0:
            vt[2] = -vt[2]
        a1, a2, a3 = rot.angles_of(vt)
    except ValueError as e:
        raise _api(e) from e
    ratios = (1.0, float(s[0] / s[1]), float(s[0] / s[2]))
    return Ellipsoid(tuple(float(size / v) for v in s), a1, a2, a3, ratios, stretch)  # type: ignore[arg-type]


__all__ = ["COND_MAX", "GLOBAL", "NONE", "STRUCTURAL", "Ellipsoid", "check_matrix", "ellipsoid_of", "global_matrix",
           "kind_of", "local", "matrix_of", "patch_hashes", "stretch_of"]  # fmt: skip
