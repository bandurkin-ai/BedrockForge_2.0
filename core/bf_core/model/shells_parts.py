"""The parts of the shells of grades (подшаг 19.14; ТЗ 8.9.2 «оболочки содержаний», 8.9.3; ADR 0026 решения 6, 16):
the levels of the thresholds, the field of a threshold, its closed surface and the nesting of the bodies.

    shell_levels(group, data) -> list[float]       the level of every threshold in the space of the values (T(t) / t)
    unit_of(values) -> float                        1/σ of the values: the field is made of the order of 1
    ThresholdField(pf, level, unit)(x)              f_k = (level − RBF(x))·unit, f < 0 where the grade is above
    shell_surface(...) -> (v, t) | None             the closed surface f = 0, post-processed and checked
    Nesting(boundary).add(v, t, name) -> Tile       the body of shell k: (S_k ∩ B) ∩ the body of shell k − 1

The field. A numeric shell is ONE RBF of the values T(g); the field of the threshold t_k is T(t_k) − RBF (the inside —
the grade above the threshold — is f < 0, as for the domains). An indicator shell is an RBF of [g ≥ t_k] − 0,5 for
every threshold and its field is (p − 0,5) − RBF. A positive factor `unit` = 1/σ of the data values brings the field to
the order of 1 whatever the unit of the grade (the sign, so the surface, is not changed; the nodes of `geo.iso` take
|f| against `scale` and 1e-9·h). A grade field is not a distance: `near` of the isosurface is not used, a cell is in
the band by its change of sign, so a shell smaller than a cell of the coarse grid (H = 2^L·h) between its nodes is
left out (the same limit as a body under V_min).

The nesting (ТЗ 8.9.2, Т8.9-6): the body of the shell k is cut by the body of the shell k − 1 by `Manifold` (an
intersection), so the shell k lies in the shell k − 1 to the rounding of manifold3d whatever the noise of the two RBF.
The first shell is cut by the boundary only.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .bodies_parts import Tile, Tiling
from .spec_parts import refuse

if TYPE_CHECKING:
    from manifold3d import Manifold

    from bf_core.geo.iso_bricks import BrickCache

    from .data import Points
    from .evaluate import PatchField
    from .spec import ModelSpec
    from .spec_parts import Group

F8 = NDArray[np.float64]
Tick = Callable[[float, str], None]

NEAR = None  # a grade field is not a distance: the band of the isosurface is the change of sign only


def shell_levels(group: Group, data: Points) -> list[float]:
    """The level of every threshold of `group` in the space of the data values: T(t) for a numeric shell (log, nscore
    — `data.levels`), t for an indicator one. NaN — the threshold has no level (nscore with no data). 422 `bad_param`
    when `data` does not carry them and they cannot be made from the group (a transform other than the linear one)."""
    thresholds = [float(t) for t in group.params["thresholds"]]
    if group.id is not None and data.levels.get(int(group.id)):
        levels = [float(x) for x in data.levels[int(group.id)]]
        if len(levels) == len(thresholds):
            return levels
    if group.params["mode"] == "indicator" or group.params["transform"] == "linear":
        return thresholds
    raise ApiError(422, "bad_param", f"Для оболочки «{group.name}» нет уровней порогов в данных.",
                   "Загрузите данные модели заново (пересчёт точек) и повторите.", {"param": "groups"})  # fmt: skip


def unit_of(values: F8) -> float:
    """1/σ of the values (1 when they are all equal): the factor that makes the field of the order of 1."""
    if values.size < 2:
        return 1.0
    sigma = float(np.std(values))
    return 1.0 / sigma if np.isfinite(sigma) and sigma > 0.0 else 1.0


class ThresholdField:
    """f_k(x) = (level − RBF(x))·unit: negative where the interpolated value is above the level (inside the shell);
    NaN where the RBF has no value (beyond the patches: the isosurface takes it as +H). Same bits for the same x."""

    def __init__(self, pf: PatchField, level: float, unit: float, threads: int = 1) -> None:
        self.pf, self.level, self.unit, self.threads = pf, float(level), float(unit), int(threads)

    def __call__(self, x: F8) -> F8:
        return (self.level - self.pf(x, threads=self.threads)) * self.unit  # type: ignore[no-any-return]


def with_values(data: Points, values: F8) -> Points:
    """`data` with other values (the indicator of a threshold): the places and the order stay."""
    return replace(data, f=np.ascontiguousarray(values, dtype=np.float64))


def shell_surface(
    spec: ModelSpec, group: Group, label: str, fn: Callable[[F8], F8], lo: F8, hi: F8, h: float, levels: int,
    threads: int, tick: Tick | None, cache: BrickCache | None, journal: list[str], timing: dict[str, float],
    sub: Callable[[Tick | None, float, float], Tick | None],
) -> tuple[Any, Any] | None:  # fmt: skip
    """The closed surface f = 0 of one threshold in the box [lo, hi], post-processed and checked (V_min, pyfqmr, the
    checks of 8.9.3 п. 7). None — there is no surface in the box (the shell is empty). `label` names it in the texts."""
    from bf_core.geo.iso import isosurface
    from bf_core.geo.iso_post import IsoError, postprocess

    from .bodies import _solids_error

    started = time.perf_counter()
    try:
        mesh = isosurface(fn, lo, hi, h, levels=levels, near=NEAR, threads=threads, tick=sub(tick, 0.0, 0.75),
                          cache=cache)  # fmt: skip
    except IsoError as err:
        raise _solids_error(err, group) from None
    timing["iso"] = timing.get("iso", 0.0) + time.perf_counter() - started
    if mesh.empty:
        journal.append(f"Оболочка «{label}»: в границе модели поверхности нет — тела нет.")
        return None
    started = time.perf_counter()
    try:
        result = postprocess(mesh.vertices, mesh.triangles, h=h, post=replace(spec.post, clip="none"),
                             threads=threads, tick=sub(tick, 0.75, 1.0))  # fmt: skip
    except IsoError as err:
        if err.code == "iso_check":
            why = err.message.removeprefix("Поверхность не прошла проверку: ").rstrip(".")
            raise refuse("iso_check", f"Поверхность оболочки «{label}» не прошла проверки: {why}.",
                         "Увеличьте шаг h или сглаживание s, проверьте данные на противоречия и повторите: "
                         "каркас не записан.", group=group.id) from None  # fmt: skip
        raise _solids_error(err, group) from None
    timing["post"] = timing.get("post", 0.0) + time.perf_counter() - started
    journal.extend(f"Оболочка «{label}»: {line}" for line in result.journal)
    if result.empty:
        journal.append(f"Оболочка «{label}»: после постобработки поверхность пуста — тела нет.")
        return None
    return result.vertices, result.triangles


class Nesting(Tiling):
    """The boundary and the bodies of the shells of one group, each inside the one before it (see the module text)."""

    def __init__(self, boundary: tuple[Any, Any], name: str = "Граница модели") -> None:
        super().__init__(boundary, name)
        self.prev: Manifold | None = None
        self.dead = False  # a shell came out empty: the higher ones are empty too

    def add(self, v: Any, t: Any, name: str) -> Tile:
        """The body of the next (higher) threshold of the closed outward surface (v, t): C = S ∩ B ∩ the body before.
        An empty body (nothing inside the boundary or inside the shell before it) is an empty `Tile`."""
        import manifold3d
        from manifold3d import Manifold

        from bf_core.geo import solids

        empty = Tile(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 0.0, 0.0)
        vv, tt = solids.mesh_arrays((v, t), name)
        if len(tt) == 0 or self.dead:
            self.dead = True
            return empty
        run = int(Manifold.reserve_ids(1))
        marked = solids.to_manifold(
            vv - self.origin, tt, name,
            run_index=np.array([0, 3 * len(tt)], dtype=np.uint64),
            run_original_id=np.array([run], dtype=np.uint32),
            face_id=np.arange(len(tt), dtype=np.uint64),
        )  # fmt: skip
        parts: Sequence[Manifold] = [marked, self.boundary] if self.prev is None else [marked, self.boundary, self.prev]
        cut = Manifold.batch_boolean(list(parts), manifold3d.OpType.Intersect)
        solids.check_status(cut, name)
        if cut.is_empty():
            self.dead = True
            return empty
        tile = self._tile(cut, run)
        if tile.empty:
            self.dead = True
            return tile
        self.prev = cut
        self.total += tile.volume
        return tile


__all__ = ["NEAR", "Nesting", "ThresholdField", "shell_levels", "shell_surface", "unit_of", "with_values"]
