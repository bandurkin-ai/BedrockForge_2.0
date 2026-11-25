"""Octree and patches of the RBF partition of unity (ADR 0026, decision 8; ТЗ 8.9.2, 8.9.4).

The model extent (+10 %, in the space after the global anisotropy matrix A) is divided into octree leaves until each
leaf holds at most `n_patch` points; a patch is the ball around a leaf centre with R = (1 + delta) * half the leaf
diagonal, grown by x1.25 until it holds at least `n_min` points. A second splitting criterion bounds the work of a
patch (19.4b): a leaf is split further while the ball of its patch (before the growth for `n_min`) holds more than
`ball_factor * n_patch` points, so a big sparse leaf next to a dense area does not give a patch of tens of thousands of
points; the interpolant is the same in kind, only the patches are smaller and more numerous. The result depends
only on the points and the parameters: not on the input order of canonically ordered points, not on threads, no
global state, no side effects.

Contract with the callers
- `points` are float64 (n, 3), already in the canonical order (x, y, z, f, type) set by the caller; the numbers in the
  result (`indices`) are positions in this array.
- With `matrix=None` the points and the extent are already in the interpolation space (the caller transformed them
  once). With `matrix=A` they are in project coordinates and the module applies `transform_points` once; `indices`
  still point into the caller's array, `centers`/`radii`/leaf boxes are in the transformed space. `hash_patches`
  applies the same transform, so the hashes of one `PatchSet` are always over the points of the system.
- Patches are numbered in Morton depth-first order of the leaves (child = 4*[x >= mid] + 2*[y >= mid] + [z >= mid]);
  empty leaves are patches too (their ball is grown to `n_min` points), so the patches cover the whole extent.

numba is not needed here and must not be imported at module level (the job-handler modules stay light).
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import ArrayLike, NDArray

from bf_core.api.errors import ApiError

if TYPE_CHECKING:
    from scipy.spatial import cKDTree

F8 = NDArray[np.float64]
I8 = NDArray[np.int64]
Tick = Callable[[], None]

#: R is multiplied by this until the ball holds `n_min` points (ТЗ 8.9.2).
GROWTH = 1.25
#: The extent grows by this share of its size on each side (ТЗ 8.9.2: «охват +10 %»).
MARGIN = 0.10
#: A leaf is split while its ball holds more than BALL_FACTOR * n_patch points (an engineering bound, not in the UI).
BALL_FACTOR = 3.0
#: Octree depth cap: coincident or near-coincident points beyond `n_patch` cannot be split further.
MAX_DEPTH = 20
#: A flat extent (planar or collinear data) gets this share of the largest span along its thin axes.
_FLAT_SPAN = 1e-3
_HASH_TAG = b"bf-rbf-patch/1"
_TICK_EVERY = 64


@dataclass(frozen=True, eq=False)
class PatchSet:
    """Patches of one model; CSR layout, arrays are read-only. Patch j holds `indices[indptr[j]:indptr[j+1]]`."""

    centers: F8  #: (m, 3) centres of the leaves (= of the balls), interpolation space
    radii: F8  #: (m,) R_j after growth
    leaf_min: F8  #: (m, 3) leaf box, lower corner
    leaf_max: F8  #: (m, 3) leaf box, upper corner
    leaf_counts: I8  #: (m,) points inside the leaf box itself (0 for an empty leaf); sum = n
    indptr: I8  #: (m + 1,)
    indices: I8  #: (k,) canonical point numbers, ascending inside a patch
    hashes: tuple[str, ...]  #: (m,) hex SHA-256 of the patch parameters and its points (no values)
    extent_min: F8  #: (3,) extent +10 % (root of the octree)
    extent_max: F8  #: (3,)
    matrix: F8 | None  #: A applied by `build_patches` (None: the caller transformed the points)
    n_patch: int
    n_min: int
    delta: float

    @property
    def n_patches(self) -> int:
        return int(self.radii.shape[0])

    @property
    def sizes(self) -> I8:
        return np.diff(self.indptr)

    def patch(self, j: int) -> I8:
        """Canonical point numbers of patch j (ascending view)."""
        return self.indices[self.indptr[j] : self.indptr[j + 1]]


def _bad(code: str, message: str, hint: str, **extra: object) -> ApiError:
    return ApiError(422, code, message, hint, dict(extra))


def _frozen(arr: NDArray) -> NDArray:  # type: ignore[type-arg]
    arr.setflags(write=False)
    return arr


def transform_points(points: ArrayLike, matrix: ArrayLike) -> F8:
    """x' = A·x for every row, with a fixed summation order (no BLAS: bitwise independent of its threading)."""
    p = np.asarray(points, dtype=np.float64)
    a = np.asarray(matrix, dtype=np.float64)
    return np.ascontiguousarray(p[:, 0:1] * a[:, 0] + p[:, 1:2] * a[:, 1] + p[:, 2:3] * a[:, 2])


def transform_extent(extent_min: ArrayLike, extent_max: ArrayLike, matrix: ArrayLike) -> tuple[F8, F8]:
    """Box of the eight transformed corners of an axis-aligned extent (the extent in the space after A)."""
    lo = np.asarray(extent_min, dtype=np.float64)
    hi = np.asarray(extent_max, dtype=np.float64)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    moved = transform_points(corners, matrix)
    return moved.min(axis=0), moved.max(axis=0)


def _check_params(n_patch: int, n_min: int, delta: float, margin: float, max_depth: int,
                  ball_factor: float | None = None) -> None:  # fmt: skip
    """Sanity only: the ranges of ТЗ 8.9.4 (N_уч 300–4000, N_min 20–500, δ 0,1–1) are checked by `spec`."""
    if int(n_patch) < 1 or int(n_min) < 1:
        raise _bad("bad_param", "Число точек участка и минимум точек должны быть положительными.",
                   "Задайте N_уч ≥ 1 и N_min ≥ 1 (по умолчанию 1000 и 100).", param="n_patch/n_min")  # fmt: skip
    if not (np.isfinite(delta) and delta >= 0.0):
        raise _bad("bad_param", "Запас радиуса участка δ должен быть числом не меньше нуля.",
                   "Задайте δ от 0,1 до 1 (по умолчанию 0,25).", param="delta")  # fmt: skip
    if not (np.isfinite(margin) and margin >= 0.0):
        raise _bad("bad_param", "Поле вокруг охвата модели должно быть числом не меньше нуля.",
                   "Задайте долю от 0 (по умолчанию 0,1 — десять процентов).", param="margin")  # fmt: skip
    if int(max_depth) < 1:
        raise _bad("bad_param", "Предельная глубина октодерева должна быть не меньше 1.",
                   "Оставьте значение по умолчанию.", param="max_depth")  # fmt: skip
    if ball_factor is not None and not (np.isfinite(ball_factor) and ball_factor >= 1.0):
        raise _bad("bad_param", "Предел числа точек в шаре участка должен быть числом не меньше 1 (в долях N_уч).",
                   "Оставьте значение по умолчанию.", param="ball_factor")  # fmt: skip


def _check_points(points: ArrayLike, n_min: int) -> F8:
    pts = np.asarray(points, dtype=np.float64)
    if pts.size == 0:
        raise _bad("rbf_no_points", "Для построения неявной модели нет ни одной точки данных.",
                   "Проверьте домен, набор данных и группу кодов: контакты и точки не найдены.")  # fmt: skip
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise _bad("rbf_bad_points", "Точки данных должны быть таблицей из трёх колонок (x, y, z).",
                   "Это ошибка подготовки данных: сообщите разработчикам.", shape=list(pts.shape))  # fmt: skip
    if not np.isfinite(pts).all():
        bad = int(np.flatnonzero(~np.isfinite(pts).all(axis=1))[0])
        raise _bad("rbf_bad_points", f"В координатах точки № {bad} есть пропуск или бесконечность.",
                   "Удалите такие точки из данных: пропуск координаты не заменяется нулём.", index=bad)  # fmt: skip
    if pts.shape[0] < n_min:
        raise _bad("rbf_few_points",
                   f"Точек данных ({pts.shape[0]}) меньше минимума для участка ({int(n_min)}).",
                   "Добавьте данные (контакты, внутренние и внешние точки) или уменьшите N_min (допустимо от 20).",
                   n_points=int(pts.shape[0]), n_min=int(n_min))  # fmt: skip
    return pts


def _check_matrix(matrix: ArrayLike | None) -> F8 | None:
    if matrix is None:
        return None
    a = np.array(matrix, dtype=np.float64)
    if a.shape != (3, 3) or not np.isfinite(a).all():
        raise _bad("bad_param", "Матрица анизотропии должна быть конечной матрицей 3 × 3.",
                   "Проверьте углы и отношения полуосей эллипсоида.", param="matrix")  # fmt: skip
    if abs(float(np.linalg.det(a))) < 1e-12 * max(1.0, float(np.abs(a).max()) ** 3):
        raise _bad("bad_param", "Матрица анизотропии вырождена (определитель равен нулю).",
                   "Отношения полуосей эллипсоида должны быть положительными.", param="matrix")  # fmt: skip
    return a


def _root_box(pts: F8, extent: tuple[ArrayLike, ArrayLike] | None, margin: float) -> tuple[F8, F8]:
    """Extent ∪ points, a flat axis widened, `margin` of the size added on each side."""
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    if extent is not None:
        e_lo = np.asarray(extent[0], dtype=np.float64)
        e_hi = np.asarray(extent[1], dtype=np.float64)
        if e_lo.shape != (3,) or e_hi.shape != (3,) or not (np.isfinite(e_lo).all() and np.isfinite(e_hi).all()) \
                or (e_hi < e_lo).any():  # fmt: skip
            raise _bad("bad_param", "Охват модели задан неверно: нужны два конечных угла, нижний не больше верхнего.",
                       "Задайте границу модели заново.", param="extent")  # fmt: skip
        lo, hi = np.minimum(lo, e_lo), np.maximum(hi, e_hi)
    span = hi - lo
    big = float(span.max())
    if big <= 0.0:
        big = 1.0
    span = np.maximum(span, _FLAT_SPAN * big)
    mid = 0.5 * (lo + hi)
    lo, hi = mid - 0.5 * span, mid + 0.5 * span
    return lo - margin * span, hi + margin * span


def _split(a: F8, b: F8, idx: I8, pts: F8) -> list[tuple[F8, F8, I8]]:
    """The eight children of a box in Morton order (child = 4*[x >= mid] + 2*[y >= mid] + [z >= mid])."""
    mid = 0.5 * (a + b)
    p = pts[idx]
    code = (p[:, 0] >= mid[0]).astype(np.int8) * 4 + (p[:, 1] >= mid[1]).astype(np.int8) * 2 \
        + (p[:, 2] >= mid[2]).astype(np.int8)  # fmt: skip
    order = np.argsort(code, kind="stable")
    bounds = np.searchsorted(code[order], np.arange(9))
    out = []
    for c in range(8):
        up = np.array([(c >> 2) & 1, (c >> 1) & 1, c & 1], dtype=bool)
        out.append((np.where(up, mid, a), np.where(up, b, mid), idx[order[bounds[c] : bounds[c + 1]]]))
    return out


def _leaves(pts: F8, tree: cKDTree, lo: F8, hi: F8, n_patch: int, delta: float, ball_factor: float | None,
            max_depth: int, tick: Tick | None) -> tuple[F8, F8, I8]:  # fmt: skip
    """Octree split; returns leaf boxes and point counts in Morton order (empty leaves included).

    Pass 1 (depth first): a leaf holds at most `n_patch` points. Pass 2 (`ball_factor`): level by level, the fresh
    leaves whose ball R0 = (1 + delta) * half the diagonal holds more than `ball_factor * n_patch` points are replaced
    in place by their eight children (empty ones too: they are patches that cover the extent). The counts are made
    in one batch per level by the tree, so the result depends neither on the order nor on threads."""
    leaves: list[tuple[F8, F8, I8, int]] = []
    stack: list[tuple[F8, F8, I8, int]] = [(lo, hi, np.arange(pts.shape[0], dtype=np.int64), 0)]
    while stack:
        if tick is not None:
            tick()
        a, b, idx, depth = stack.pop()
        if idx.size <= n_patch or depth >= max_depth:
            leaves.append((a, b, idx, depth))
            continue
        for ca, cb, ci in reversed(_split(a, b, idx, pts)):  # child 0 is processed first
            stack.append((ca, cb, ci, depth + 1))
    if ball_factor is not None:
        limit = float(ball_factor) * n_patch
        fresh = np.arange(len(leaves))
        while fresh.size:
            if tick is not None:
                tick()
            boxes = [leaves[i] for i in fresh]
            a = np.array([x[0] for x in boxes])
            b = np.array([x[1] for x in boxes])
            r0 = (1.0 + delta) * 0.5 * np.linalg.norm(b - a, axis=1)
            cnt = np.asarray(tree.query_ball_point(0.5 * (a + b), r0, return_length=True, workers=1), dtype=np.int64)
            split = {int(i) for i, k in zip(fresh, cnt, strict=True) if k > limit and leaves[int(i)][3] < max_depth}
            if not split:
                break
            nxt: list[tuple[F8, F8, I8, int]] = []
            fresh_new: list[int] = []
            for i, leaf in enumerate(leaves):
                if i not in split:
                    nxt.append(leaf)
                    continue
                for ca, cb, ci in _split(leaf[0], leaf[1], leaf[2], pts):
                    fresh_new.append(len(nxt))
                    nxt.append((ca, cb, ci, leaf[3] + 1))
            leaves, fresh = nxt, np.array(fresh_new, dtype=np.int64)
    return (np.array([x[0] for x in leaves]), np.array([x[1] for x in leaves]),
            np.array([x[2].size for x in leaves], dtype=np.int64))  # fmt: skip


def _balls(tree: cKDTree, centers: F8, r0: F8, n_min: int, tick: Tick | None) -> F8:
    """R_j = R0_j · 1.25^k with the least k giving at least `n_min` points (multiplications in a fixed order)."""
    radii = r0.copy()
    counts = np.asarray(tree.query_ball_point(centers, radii, return_length=True, workers=1), dtype=np.int64)
    for _ in range(4000):
        short = np.flatnonzero(counts < n_min)
        if short.size == 0:
            return radii
        radii[short] *= GROWTH
        counts[short] = tree.query_ball_point(centers[short], radii[short], return_length=True, workers=1)
        if tick is not None:
            tick()
    raise _bad("rbf_patch_radius", "Не удалось подобрать радиус участка: данные вырождены.",
               "Проверьте, что точки не совпадают целиком и их координаты конечны.")  # fmt: skip


def build_patches(
    points: ArrayLike,
    *,
    n_patch: int = 1000,
    n_min: int = 100,
    delta: float = 0.25,
    extent: tuple[ArrayLike, ArrayLike] | None = None,
    matrix: ArrayLike | None = None,
    margin: float = MARGIN,
    max_depth: int = MAX_DEPTH,
    ball_factor: float | None = BALL_FACTOR,
    tick: Tick | None = None,
) -> PatchSet:
    """Octree leaves, patches and their point lists.

    `points` — (n, 3) float64 in the canonical order; `extent` — (min, max) of the model (default: of the points);
    the tree covers extent ∪ points, enlarged by `margin` of its size on each side. `matrix` — global anisotropy A,
    see the module docstring. `ball_factor` — the bound of the points in the ball of a patch in `n_patch` (None: off).
    `tick` is called regularly (cancellation: it raises). Errors are `ApiError` 422 with
    `{code, message, hint}` in Russian: `rbf_no_points`, `rbf_few_points`, `rbf_bad_points`, `bad_param`.
    """
    from scipy.spatial import cKDTree  # lazy: the import is not paid by modules that only load this one

    _check_params(n_patch, n_min, delta, margin, max_depth, ball_factor)
    pts = _check_points(points, int(n_min))
    a = _check_matrix(matrix)
    if a is not None:
        pts = transform_points(pts, a)
        if extent is not None:
            extent = transform_extent(extent[0], extent[1], a)
    lo, hi = _root_box(pts, extent, float(margin))

    tree = cKDTree(pts)
    bound = None if ball_factor is None else float(ball_factor)
    box_lo, box_hi, leaf_counts = _leaves(pts, tree, lo, hi, int(n_patch), float(delta), bound, int(max_depth), tick)
    centers = 0.5 * (box_lo + box_hi)
    r0 = (1.0 + float(delta)) * 0.5 * np.linalg.norm(box_hi - box_lo, axis=1)

    radii = _balls(tree, centers, r0, int(n_min), tick)
    lists = tree.query_ball_point(centers, radii, return_sorted=True, workers=1)
    sizes = np.fromiter((len(x) for x in lists), dtype=np.int64, count=len(lists))
    indptr = np.zeros(len(lists) + 1, dtype=np.int64)
    np.cumsum(sizes, out=indptr[1:])
    indices = np.concatenate([np.asarray(x, dtype=np.int64) for x in lists]) if len(lists) else np.empty(0, np.int64)

    ps = PatchSet(
        centers=_frozen(centers), radii=_frozen(radii), leaf_min=_frozen(box_lo), leaf_max=_frozen(box_hi),
        leaf_counts=_frozen(leaf_counts), indptr=_frozen(indptr), indices=_frozen(indices), hashes=(),
        extent_min=_frozen(lo), extent_max=_frozen(hi), matrix=None if a is None else _frozen(a.copy()),
        n_patch=int(n_patch), n_min=int(n_min), delta=float(delta),
    )  # fmt: skip
    return _with_hashes(ps, _hash_all(pts, ps, None, b"", tick))


def _with_hashes(ps: PatchSet, hashes: list[str]) -> PatchSet:
    from dataclasses import replace

    return replace(ps, hashes=tuple(hashes))


def _hash_all(pts: F8, ps: PatchSet, values: F8 | None, salt: bytes, tick: Tick | None) -> list[str]:
    """`pts` are already in the space of the patches. Little-endian float64 bytes: stable across platforms."""
    pts_b = np.ascontiguousarray(pts, dtype="<f8")
    vals_b = None if values is None else np.ascontiguousarray(values, dtype="<f8")
    head = _HASH_TAG + struct.pack("<q", len(salt)) + salt
    out: list[str] = []
    for j in range(ps.n_patches):
        idx = ps.patch(j)
        h = hashlib.sha256(head)
        h.update(np.ascontiguousarray(ps.centers[j], dtype="<f8").tobytes())
        h.update(struct.pack("<dq", float(ps.radii[j]), int(idx.size)))
        h.update(pts_b[idx].tobytes())
        if vals_b is None:
            h.update(b"n")
        else:
            h.update(b"v")
            h.update(vals_b[idx].tobytes())
        out.append(h.hexdigest())
        if tick is not None and j % _TICK_EVERY == 0:
            tick()
    return out


def hash_patches(
    points: ArrayLike,
    patches: PatchSet,
    *,
    values: ArrayLike | None = None,
    salt: bytes = b"",
    tick: Tick | None = None,
) -> list[str]:
    """Hex SHA-256 per patch for the cache of updates (19.12).

    Input of the hash: a version tag, `salt` (the caller's parameters: kernel, η, A_j, …), the centre, R and the point
    count of the patch, the bytes of its points (float64, canonical order, in the space of the patches) and, when
    given, the bytes of their `values` (float64, same order). `points` are the same array that was passed to
    `build_patches` (project coordinates when `patches.matrix` is set). `hash_patches(points, ps)` equals `ps.hashes`.
    """
    pts = np.asarray(points, dtype=np.float64)
    if patches.matrix is not None:
        pts = transform_points(pts, patches.matrix)
    vals: F8 | None = None
    if values is not None:
        vals = np.asarray(values, dtype=np.float64)
        if vals.shape != (pts.shape[0],):
            raise _bad("bad_param", "Число значений не совпадает с числом точек.",
                       "Передайте по одному значению на каждую точку в том же порядке.", param="values")  # fmt: skip
    if pts.ndim != 2 or pts.shape[1] != 3 or (patches.indices.size and int(patches.indices.max()) >= pts.shape[0]):
        raise _bad("rbf_bad_points", "Точки не соответствуют участкам: другое число точек.",
                   "Передайте тот же массив точек, по которому строились участки.", param="points")  # fmt: skip
    return _hash_all(pts, patches, vals, bytes(salt), tick)
