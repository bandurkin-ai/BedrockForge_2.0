"""The parts of the bodies of an implicit model (подшаг 19.13; ТЗ 8.9.2 «хронология», 8.9.3; ADR 0026 решения 13–15):
the fit of one group (patches → solutions → field) and the tiling of the boundary by the bodies of the domains.

    fit_group(spec, group, pts, ...) -> Fit              data → patches → η → the solutions → the field (PatchField)
    Tiling(boundary).add(v, t, name) -> Tile              the body of a domain: (surface ∩ boundary) minus the younger
    Tiling.rest() -> Tile                                 «Прочее»: the boundary minus all the bodies
    box_solid(box) -> (v, t)                              the closed solid of an extent, normals outward
    h_bar(y) -> float                                     the median distance to the nearest point (8.9.2)

The tiling. A domain `i` (groups go from the youngest) is the surface f_i = 0 closed beyond the model (+H, 8.9.3 п. 6)
cut by the boundary, C_i = S_i ∩ B; its body is D_i = C_i minus C_j of every group j that stands before it. So the
bodies do not overlap (a point of two C belongs to the younger one), «Прочее» = B minus every C_i, and the volumes of
all of them add up to the volume of B — to the rounding of manifold3d. The sheet of the contact of a group is the
part of D_i that came from the faces of S_i itself (marked by the original id of the run): the contact f_i = 0 inside
the boundary and not hidden by a younger body. Everything is done on `Manifold` objects in the local coordinates of
one whole-number origin (ТЗ 17 п. 5); the output is `solids.canonical` — the same bytes whatever the threads of
manifold3d (решение 14).

manifold3d, scipy and numba are imported inside the functions that need them.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .octree import PatchSet, build_patches, transform_points
from .spec_parts import eta as eta_of_s
from .spec_parts import refuse
from .system import RbfSingular

if TYPE_CHECKING:
    from manifold3d import Manifold

    from .data import Points
    from .evaluate import PatchField
    from .spec import Group, ModelSpec
    from .system import PatchSolution

F8 = NDArray[np.float64]
Tick = Callable[[float, str], None]
#: the solver of the patches (default: `solve.solve_patches`; the seam of the cache of 19.12)
Solver = Callable[..., "list[PatchSolution]"]
#: η of auto smoothing (Д-25, 19.10): (group, patches, xyz, f, h_bar[, matrices=A_j]) -> one η or one per patch
EtaOf = Callable[..., "float | Sequence[float]"]
#: A_j of the patches of a structural trend (19.9, Д-15): (centres of the patches, label of the group for the journal,
#: tick(fraction) | None) -> f8[m, 3, 3]; `job_stages.Structural`
MatricesOf = Callable[..., Any]

RESIDUAL_SAMPLE = 200_000  # the data points the residual of the fit is measured at (a regular sample above this)
SOURCES_SHOWN = 20  # the names of data sources in a `rbf_singular` (контракт §7)


def box_solid(box: Sequence[float]) -> tuple[F8, NDArray[np.int32]]:
    """The closed solid of `box` = (xmin, ymin, zmin, xmax, ymax, zmax): 8 vertices, 12 triangles, normals outward."""
    x0, y0, z0, x1, y1, z1 = (float(c) for c in box)
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=np.float64)  # fmt: skip
    t = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]], dtype=np.int32)  # fmt: skip
    return v, t


def h_bar(y: F8) -> float:
    """h̄ of 8.9.2: the median distance from a point to its nearest neighbour (coincident points give 0 and count)."""
    from scipy.spatial import cKDTree

    if y.shape[0] < 2:
        return 0.0
    d, _ = cKDTree(y).query(y, k=2, workers=1)
    return float(np.median(d[:, 1]))


def source_label(pts: Points, row: int) -> str:
    """«A-1 12,0–14,0 м»: the source of a data point and its interval along the hole (контракт §7)."""
    name = pts.sources[int(pts.src[row])] if pts.sources else "точка"
    lo, hi = float(pts.span[row, 0]), float(pts.span[row, 1])
    if np.isnan(lo) or np.isnan(hi):
        return str(name)
    fmt = f"{lo:.1f}" if lo == hi else f"{lo:.1f}–{hi:.1f}"
    return f"{name} {fmt.replace('.', ',')} м"


@dataclass(frozen=True)
class Fit:
    """The solved field of one group (what 19.12 stores: `patches`, `solutions`) and its figures."""

    group: Group
    patches: PatchSet
    solutions: list[PatchSolution]
    field: PatchField
    eta: float
    h_bar: float
    stats: dict[str, Any] = dc_field(default_factory=dict)


def _singular(err: RbfSingular, group: Group, pts: Points, n: int) -> ApiError:
    """`rbf_singular` of a patch with the group, the sources and their names (контракт §7)."""
    rows = err.extra.get("sources") or err.extra.get("points") or []
    flat = [int(r) for item in rows for r in (item if isinstance(item, list) else [item])]
    labels = [source_label(pts, r) for r in flat[:SOURCES_SHOWN] if 0 <= r < pts.n]
    where = f"Группа «{group.name}»: {err.message}"
    return refuse("rbf_singular", where, err.hint, group=group.id, wing=None, patch=err.extra.get("patch"),
                  sources=labels, points=int(n))  # fmt: skip


def fit_group(
    spec: ModelSpec,
    group: Group,
    pts: Points,
    *,
    extent: tuple[F8, F8],
    matrix: F8 | None = None,
    matrices_of: MatricesOf | None = None,
    eta_of: EtaOf | None = None,
    solver: Solver | None = None,
    threads: int = 1,
    tick: Tick | None = None,
    span: tuple[float, float] = (0.0, 1.0),
    need_contacts: bool = True,
) -> Fit:
    """The points of the group → patches → η → the solution of every patch → the field. `extent` is the box of the
    model (the octree covers it +10 %); `matrix` is the global anisotropy A (None — identity); `matrices_of` — the
    structural one (19.9: the octree and W stay in the project coordinates, every patch has its A_j at its centre; the
    same list goes to the solver, to η, to the field and to the hashes of the cache; h̄ is that of the project
    coordinates), not together with `matrix`. 422 `bad_anisotropy` {patches}, `no_contacts`
    (a domain needs a change of sign among its values; a shell, `need_contacts=False`, only some data),
    `rbf_singular` {group, patch, sources, points} and the errors of `octree`; `tick(fraction, text)` inside `span`."""
    from .evaluate import PatchField
    from .solve import solve_patches

    def beat(share: float, text: str) -> None:
        if tick is not None:
            tick(span[0] + (span[1] - span[0]) * share, text)

    n = pts.n
    if n == 0 or (need_contacts and not (bool((pts.f < 0.0).any()) and bool((pts.f > 0.0).any()))):
        raise refuse("no_contacts", f"В данных группы «{group.name}» нет контактов: поверхность не определена.",
                     group=group.id)  # fmt: skip
    if matrix is not None and matrices_of is not None:
        raise ApiError(422, "bad_anisotropy", "Задана и общая, и структурная анизотропия.",
                       "Это ошибка вызова: выберите одну из них.", {"param": "anisotropy"})  # fmt: skip
    beat(0.0, f"Участки: «{group.name}», октодерево")
    started = time.perf_counter()
    pou = spec.pou
    patches = build_patches(
        pts.xyz, n_patch=pou.n_patch, n_min=pou.n_min, delta=pou.delta, extent=extent, matrix=matrix,
        tick=lambda: beat(0.0, f"Участки: «{group.name}»"),
    )  # fmt: skip
    mats: list[F8] | None = None
    if matrices_of is not None:
        axes = f"Участки: «{group.name}», оси структурного тренда"
        beat(0.02, axes)
        mats = list(matrices_of(patches.centers, f"Группа «{group.name}»", lambda s: beat(0.02 + 0.02 * s, axes)))
    extra: dict[str, Any] = {} if mats is None else {"matrices": mats}
    t_patches = time.perf_counter() - started
    y = pts.xyz if matrix is None else transform_points(pts.xyz, matrix)
    hb = h_bar(y)
    if spec.smoothing.mode == "auto":
        if eta_of is None:
            raise ApiError(422, "bad_param", "Автоматическое сглаживание (Д-25) здесь не подключено.",
                           "Выберите ручное сглаживание.", {"param": "smoothing.mode"})  # fmt: skip
        eta_value: float | Sequence[float] = eta_of(group, patches, pts.xyz, pts.f, hb, **extra)
    else:
        eta_value = eta_of_s(spec.kernel, spec.s_of(group), hb)
    radius: float | list[float] | None = None
    if spec.kernel == "wendland":
        radius = spec.kernel_radius if spec.kernel_radius is not None else [2.0 * float(r) for r in patches.radii]
    m = patches.n_patches
    done = [0]

    def solved_one() -> None:
        done[0] += 1
        beat(0.05 + 0.9 * done[0] / max(m, 1), f"Участки: «{group.name}», {done[0]} из {m}")

    started = time.perf_counter()
    try:
        solutions = (solver or solve_patches)(pts.xyz, pts.f, patches, kind=spec.kernel, radius=radius,
                                              eta=eta_value, threads=threads, tick=solved_one,
                                              sources=np.arange(n), **extra)  # fmt: skip
    except RbfSingular as err:
        raise _singular(err, group, pts, n) from None
    t_solve = time.perf_counter() - started
    pf = PatchField(patches, solutions, matrices=mats, threads=threads)
    sample = np.arange(n) if n <= RESIDUAL_SAMPLE else np.linspace(0, n - 1, RESIDUAL_SAMPLE).astype(np.int64)
    residual = float(np.nanmax(np.abs(pf(pts.xyz[sample], threads=threads) - pts.f[sample]))) if n else 0.0
    etas = [float(s.eta) for s in solutions]
    stats: dict[str, Any] = {"points": n, "patches": m, "eta": float(max(etas)) if etas else 0.0, "h_bar": hb,
             "cond_max": float(max((s.cond1 for s in solutions), default=0.0)), "max_residual": residual,
             "time": {"patches": t_patches, "solve": t_solve}}  # fmt: skip
    beat(1.0, f"Участки: «{group.name}», готово")
    return Fit(group, patches, solutions, pf, float(stats["eta"]), hb, stats)


# --- the tiling --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    """A body of the tiling and, for a domain, the sheet of its contact (canonical meshes, project coordinates)."""

    vertices: F8
    triangles: NDArray[np.int32]
    volume: float
    area: float
    sheet: tuple[F8, NDArray[np.int32]] | None = None
    sheet_area: float = 0.0
    parts: int = 1

    @property
    def empty(self) -> bool:
        return len(self.triangles) == 0


class Tiling:
    """The boundary and the bodies cut from it, in the order of the groups (see the module text)."""

    def __init__(self, boundary: tuple[Any, Any], name: str = "Граница модели") -> None:
        from bf_core.geo import solids

        v, t = solids.mesh_arrays(boundary, name)
        self.origin = solids.local_origin(v)
        self.boundary, info = solids.closed_solid(v, t, self.origin, name, "Тела модели")
        self.volume = float(info["volume"])
        self.cut: list[Manifold] = []  # C_j of the groups that had a body
        self.total = 0.0  # the sum of the volumes of the tiles made so far

    def _tile(self, man: Manifold, run: int | None) -> Tile:
        from bf_core.geo import solids

        mesh = man.to_mesh64()
        v, t = solids.from_manifold(mesh)
        if len(t) == 0:
            return Tile(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 0.0, 0.0)
        volume = abs(solids.signed_volume(v, t))
        area = solids.surface_area(v, t)
        if volume <= float(man.get_tolerance()) * area:  # touching along a face: no thickness
            return Tile(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 0.0, 0.0)
        sheet = None
        sheet_area = 0.0
        if run is not None:
            st = solids.run_triangles(mesh, t, run)
            if len(st):
                sv, st = solids.compact(v, st)
                sheet_area = solids.surface_area(sv, st)
                sheet = solids.canonical(*solids.to_project(sv, st, self.origin))
        cv, ct = solids.canonical(*solids.to_project(v, t, self.origin))
        return Tile(cv, ct, volume, area, sheet, sheet_area, solids.part_count(t, len(v)))

    def add(self, v: Any, t: Any, name: str) -> Tile:
        """The body of the next (older) group of the closed outward surface (v, t): C = S ∩ B, D = C − the younger C.
        An empty body (nothing inside the boundary, or all of it under the younger) is an empty `Tile`."""
        import manifold3d
        from manifold3d import Manifold

        from bf_core.geo import solids

        vv, tt = solids.mesh_arrays((v, t), name)
        if len(tt) == 0:
            return Tile(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 0.0, 0.0)
        run = int(Manifold.reserve_ids(1))
        marked = solids.to_manifold(
            vv - self.origin, tt, name,
            run_index=np.array([0, 3 * len(tt)], dtype=np.uint64),
            run_original_id=np.array([run], dtype=np.uint32),
            face_id=np.arange(len(tt), dtype=np.uint64),
        )  # fmt: skip
        cut = Manifold.batch_boolean([marked, self.boundary], manifold3d.OpType.Intersect)
        solids.check_status(cut, name)
        if cut.is_empty():
            return Tile(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 0.0, 0.0)
        body = Manifold.batch_boolean([cut, *self.cut], manifold3d.OpType.Subtract) if self.cut else cut
        solids.check_status(body, name)
        self.cut.append(cut)
        tile = self._tile(body, run)
        self.total += tile.volume
        return tile

    def rest(self) -> Tile:
        """«Прочее»: the boundary minus the bodies of all the groups (empty when they fill it)."""
        import manifold3d
        from manifold3d import Manifold

        from bf_core.geo import solids

        rest = Manifold.batch_boolean([self.boundary, *self.cut], manifold3d.OpType.Subtract) if self.cut \
            else self.boundary  # fmt: skip
        solids.check_status(rest, "Прочее")
        tile = self._tile(rest, None)
        self.total += tile.volume
        return tile


__all__ = ["Fit", "Tile", "Tiling", "box_solid", "fit_group", "h_bar", "source_label"]
