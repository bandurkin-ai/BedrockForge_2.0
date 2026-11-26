"""The parts of the faults of an implicit model (подшаг 19.33; ТЗ Д-24 «Разлом»; ADR 0026 решение 20): the fault with
its influence weight, the field of a wing, the field of a group cut by faults, the regions of the wings, the tiling of
the boundary by the pieces of the bodies, the offset of the wings.

    FaultFit(group, fit, contacts)          f_F of a fault, its R_F and the distance r to the nearest datum of the fault
    WingField(faults, key, terms)           g_k of one wing of a group: the blend of the fits of the wing with the fits
                                            of the undivided data by W(r / R_F) (the module text of `faults`)
    FaultedField(faults, wings)             the field of a group cut by faults: g_k where k is the wing of the point
    WingTiling(boundary)                    `bodies_parts.Tiling` with regions: the wings of the faults and the bodies
                                            cut to the region of their wing; bodies of other regions never meet
    wing_signs(faults, xyz, tol, threads)   the wing of every point for every fault: 0 (f_F < 0), 1, 2 (on the fault)
    offsets(...)                            the offset of the wings of a group across a fault, for the stats (read only)

A wing «key» of a group is a tuple of signs 0 / 1, one for every fault of the group in the order of the list of faults
of the model; the wing code is Σ sign_k · 2^k (one fault: 0 / 1). A fault with a finite R_F gives the blend

    g_k = Σ_D  Π_{i ∈ D} w_i · Π_{i ∈ finite \\ D} (1 − w_i) · f_{k | D ∪ infinite}

over the subsets D of the finite faults, where f_{k | S} is the fit of the data divided by the faults of S only; for one
finite fault g_k = w·f_k + (1 − w)·f_0 of the ТЗ. A term whose weight is 0 is not evaluated and the weight 1 gives the
bits of the term, so where every w is 0 (beyond R_F of all of them) g_k IS the undivided field, bit for bit.

manifold3d, scipy and numba are imported inside the functions that need them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from .bodies_parts import Tile, Tiling
from .evaluate import partition_weight

if TYPE_CHECKING:
    from manifold3d import Manifold

    from .bodies_parts import Fit
    from .spec_parts import Group

F8 = NDArray[np.float64]
Key = tuple[int, ...]  # the signs of the faults of a group
RKey = tuple[tuple[int, int], ...]  # a region: ((fault id, sign), …) in the order of the faults of the model
FieldFn = Callable[..., F8]

TOL_REL = 1e-9  # «on the fault»: |f_F| ≤ TOL_REL · h
SAMPLES = 2000  # the points of a fault the offset is measured at


def wing_code(key: Sequence[int]) -> int:
    """The code of a wing: the signs as bits, the first fault is the lowest bit."""
    return sum(int(s) << k for k, s in enumerate(key))


def compatible(a: Mapping[int, int], b: Mapping[int, int]) -> bool:
    """False for two regions that lie on different sides of a common fault: they have no common volume."""
    return all(b.get(f, s) == s for f, s in a.items())


class FaultFit:
    """A fault: its fit f_F (f < 0 — wing 0), the radius R_F (None — infinite) and the data on its surface."""

    def __init__(self, group: Group, fit: Fit, contacts: F8) -> None:
        self.group = group
        self.fit = fit
        radius = group.params.get("radius")
        self.radius: float | None = None if radius is None else float(radius)
        self.tree: Any = None
        if self.radius is not None and contacts.shape[0]:
            from scipy.spatial import cKDTree

            self.tree = cKDTree(np.ascontiguousarray(contacts, dtype=np.float64))

    @property
    def gid(self) -> int:
        return int(self.group.id or 0)

    @property
    def finite(self) -> bool:
        return self.radius is not None

    def weight(self, xyz: F8) -> F8:
        """W(r / R_F), r the distance to the nearest datum of the fault; exactly 0 for r ≥ R_F."""
        d = self.tree.query(np.ascontiguousarray(xyz, dtype=np.float64), workers=1)[0]
        return np.asarray(partition_weight(np.asarray(d, dtype=np.float64) / float(self.radius or 1.0)))


class WingField:
    """g_k of the wing `key` of a group: see the module text. `terms` — [(mask, field)] by the ascending mask, the
    bits of the mask are the finite faults of the group (their positions in `finite`), the field is a `PatchField`."""

    def __init__(self, faults: Sequence[FaultFit], key: Key, terms: Sequence[tuple[int, FieldFn]]) -> None:
        self.faults = tuple(faults)
        self.key = tuple(key)
        self.finite = [k for k, f in enumerate(self.faults) if f.finite]
        self.terms = list(terms)

    def __call__(self, points: Any, *, threads: int | None = None, tick: Any = None) -> F8:
        x = np.ascontiguousarray(np.asarray(points, dtype=np.float64))
        n = x.shape[0]
        if not self.finite:
            return np.asarray(self.terms[0][1](x, threads=threads))
        w = [self.faults[k].weight(x) for k in self.finite]
        out = np.full(n, np.nan)
        assigned = np.zeros(n, dtype=bool)
        for mask, fn in self.terms:
            weight = np.ones(n)
            for j, wj in enumerate(w):
                weight = weight * (wj if (mask >> j) & 1 else 1.0 - wj)
            rows = np.flatnonzero(weight > 0.0)
            if rows.size == 0:
                continue
            val = weight[rows] * np.asarray(fn(x[rows], threads=threads))
            fresh = ~assigned[rows]
            out[rows[fresh]] = val[fresh]  # the first term is assigned, not added to NaN: 1.0 · f is f bit for bit
            out[rows[~fresh]] += val[~fresh]
            assigned[rows] = True
        return out


class FaultedField:
    """The field of a group cut by faults: g_k at the points whose wing is k (f_F ≥ 0 — sign 1, a NaN of f_F too)."""

    def __init__(self, faults: Sequence[FaultFit], wings: Mapping[Key, WingField]) -> None:
        self.faults = tuple(faults)
        self.wings = dict(wings)

    def codes(self, x: F8, threads: int | None, force: Mapping[int, int] | None = None) -> NDArray[np.int64]:
        code = np.zeros(x.shape[0], dtype=np.int64)
        for k, ff in enumerate(self.faults):
            if force is not None and k in force:
                s = np.full(x.shape[0], int(force[k]), dtype=np.int64)
            else:
                s = (~(np.asarray(ff.fit.field(x, threads=threads)) < 0.0)).astype(np.int64)
            code |= s << k
        return code

    def __call__(self, points: Any, *, threads: int | None = None, tick: Any = None,
                 force: Mapping[int, int] | None = None) -> F8:  # fmt: skip
        """f at the points; `force` {position of the fault: sign} puts the points on the given wing of that fault (the
        offset across a fault); NaN for a wing without a field (an empty region) and where no patch reaches."""
        x = np.ascontiguousarray(np.asarray(points, dtype=np.float64))
        out = np.full(x.shape[0], np.nan)
        if x.shape[0] == 0:
            return out
        code = self.codes(x, threads, force)
        for key, wing in self.wings.items():
            rows = np.flatnonzero(code == wing_code(key))
            if rows.size:
                out[rows] = wing(x[rows], threads=threads)
        return out


def wing_signs(faults: Sequence[FaultFit], xyz: F8, tol: float, threads: int = 1) -> tuple[NDArray[np.int8], int]:
    """([n, m], undefined): 0 — f_F < −tol, 1 — f_F > tol, 2 — |f_F| ≤ tol or not defined (the point is on both wings);
    `undefined` is the number of points where the field of a fault is NaN (no patch reaches them)."""
    out = np.full((xyz.shape[0], len(faults)), 2, dtype=np.int8)
    nan = np.zeros(xyz.shape[0], dtype=bool)
    for k, ff in enumerate(faults):
        f = np.asarray(ff.fit.field(xyz, threads=threads), dtype=np.float64)
        nan |= np.isnan(f)
        with np.errstate(invalid="ignore"):
            out[f < -tol, k] = 0
            out[f > tol, k] = 1
    return out, int(nan.sum())


def offsets(
    faults: Sequence[FaultFit], field: FaultedField, samples: Mapping[int, F8], h: float, band: float, threads: int = 1
) -> tuple[dict[str, float] | None, dict[int, dict[str, float]]]:  # fmt: skip
    """The offset of the wings of a group across its faults (the stats, read only; ТЗ Д-24: the result of the difference
    of the fields). At the points of the surface of a fault (`samples` by fault id) both wings are asked for their value
    with the position forced: Δ = |g_0 − g_1| and the offset is Δ / |∇ḡ|, ḡ the mean of the two, the distance between
    the two level surfaces along their normal. Only the points where |ḡ| ≤ `band` (the data are within D_max of the
    surface there) count, all of them when none is. → ({mean, max} over all the faults | None, the same by fault id)."""
    every: list[F8] = []
    by_fault: dict[int, dict[str, float]] = {}
    step = 0.5 * h
    for k, ff in enumerate(faults):
        x = samples.get(ff.gid)
        if x is None or x.shape[0] == 0:
            continue

        def mean(p: F8, k: int = k) -> F8:
            return np.asarray(0.5 * (field(p, threads=threads, force={k: 0}) + field(p, threads=threads, force={k: 1})))

        a = field(x, threads=threads, force={k: 0})
        b = field(x, threads=threads, force={k: 1})
        grad = np.empty((x.shape[0], 3))
        for axis in range(3):
            e = np.zeros(3)
            e[axis] = step
            grad[:, axis] = (mean(x + e) - mean(x - e)) / (2.0 * step)
        norm = np.sqrt(np.einsum("ij,ij->i", grad, grad))
        mid = 0.5 * (a + b)
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(norm) & (norm > 1e-12)
            near = ok & (np.abs(mid) <= band)
        use = near if bool(near.any()) else ok
        if not bool(use.any()):
            continue
        d = np.abs(a[use] - b[use]) / norm[use]
        every.append(d)
        by_fault[ff.gid] = {"mean": float(d.mean()), "max": float(d.max())}
    if not every:
        return None, by_fault
    allv = np.concatenate(every)
    return {"mean": float(allv.mean()), "max": float(allv.max())}, by_fault


def sample_rows(n: int, limit: int = SAMPLES) -> NDArray[np.int64]:
    """At most `limit` rows of n, evenly (the same rows for the same n)."""
    if n <= limit:
        return np.arange(n, dtype=np.int64)
    return np.linspace(0, n - 1, limit).astype(np.int64)


# --- the regions and the pieces ----------------------------------------------------------------------------------


def _empty() -> Tile:
    return Tile(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 0.0, 0.0)


class WingTiling(Tiling):
    """The boundary cut by the faults into regions (the wings) and the bodies of the groups cut to their regions. A
    body of a piece is its cut minus the cuts of the groups before it whose regions meet its own (the others are apart),
    so the bodies do not overlap, «Прочее» of a region of the model is the region minus the cuts that meet it, and the
    volumes add up to the volume of the boundary like the plain `Tiling`."""

    def __init__(self, boundary: tuple[Any, Any], name: str = "Граница модели") -> None:
        super().__init__(boundary, name)
        self.wings: dict[int, tuple[Manifold, Manifold]] = {}
        self.regions: dict[RKey, Manifold | None] = {(): self.boundary}
        self.pieces: list[tuple[dict[int, int], Manifold]] = []

    def _marked(self, v: Any, t: Any, name: str) -> tuple[Manifold, int]:
        from manifold3d import Manifold

        from bf_core.geo import solids

        vv, tt = solids.mesh_arrays((v, t), name)
        run = int(Manifold.reserve_ids(1))
        marked = solids.to_manifold(
            vv - self.origin, tt, name,
            run_index=np.array([0, 3 * len(tt)], dtype=np.uint64),
            run_original_id=np.array([run], dtype=np.uint32),
            face_id=np.arange(len(tt), dtype=np.uint64),
        )  # fmt: skip
        return marked, run

    @staticmethod
    def _thin(man: Manifold) -> bool:
        """No volume: empty, or touching along a face (the rule of `Tiling._tile`)."""
        return bool(man.is_empty()) or float(man.volume()) <= float(man.get_tolerance()) * float(man.surface_area())

    def split(self, fid: int, v: Any, t: Any, name: str) -> tuple[Tile, Tile]:
        """The wings of fault `fid` of its closed outward surface (v, t): wing 0 = S ∩ B (f_F < 0) with the sheet of the
        fault inside B, wing 1 = B − wing 0. An empty tile is a wing the boundary does not have."""
        import manifold3d
        from manifold3d import Manifold

        from bf_core.geo import solids

        marked, run = self._marked(v, t, name)
        wing0 = Manifold.batch_boolean([marked, self.boundary], manifold3d.OpType.Intersect)
        solids.check_status(wing0, name)
        wing1 = Manifold.batch_boolean([self.boundary, wing0], manifold3d.OpType.Subtract)
        solids.check_status(wing1, name)
        self.wings[fid] = (wing0, wing1)
        return self._tile(wing0, run), self._tile(wing1, None)

    def region(self, key: RKey) -> Manifold | None:
        """The solid of the region `key` (the wings of the faults, intersected); None — no volume there."""
        import manifold3d
        from manifold3d import Manifold

        from bf_core.geo import solids

        if key in self.regions:
            return self.regions[key]
        parent = self.region(key[:-1])
        found: Manifold | None = None
        if parent is not None:
            fid, sign = key[-1]
            man = Manifold.batch_boolean([parent, self.wings[fid][sign]], manifold3d.OpType.Intersect)
            solids.check_status(man, "Крыло разлома")
            found = None if self._thin(man) else man
        self.regions[key] = found
        return found

    def box(self, key: RKey) -> tuple[F8, F8] | None:
        """The box (lo, hi) of a region in project coordinates (the box of the isosurface of its wing)."""
        man = self.region(key)
        if man is None:
            return None
        b = man.bounding_box()
        return np.array(b[:3]) + self.origin, np.array(b[3:]) + self.origin

    def piece(self, key: RKey, v: Any, t: Any, name: str) -> Tile:
        """The body of the next group in its region `key`: (S ∩ region) minus the cuts before it that meet it."""
        import manifold3d
        from manifold3d import Manifold

        from bf_core.geo import solids

        region = self.region(key)
        if region is None:
            return _empty()
        marked, run = self._marked(v, t, name)
        cut = Manifold.batch_boolean([marked, region], manifold3d.OpType.Intersect)
        solids.check_status(cut, name)
        if cut.is_empty():
            return _empty()
        sides = dict(key)
        before = [m for k, m in self.pieces if compatible(k, sides)]
        body = Manifold.batch_boolean([cut, *before], manifold3d.OpType.Subtract) if before else cut
        solids.check_status(body, name)
        self.pieces.append((sides, cut))
        tile = self._tile(body, run)
        self.total += tile.volume
        return tile

    def rest_of(self, key: RKey) -> Tile:
        """«Прочее» of the region `key`: the region minus the cuts that meet it (empty when they fill it)."""
        import manifold3d
        from manifold3d import Manifold

        from bf_core.geo import solids

        region = self.region(key)
        if region is None:
            return _empty()
        sides = dict(key)
        before = [m for k, m in self.pieces if compatible(k, sides)]
        rest = Manifold.batch_boolean([region, *before], manifold3d.OpType.Subtract) if before else region
        solids.check_status(rest, "Прочее")
        tile = self._tile(rest, None)
        self.total += tile.volume
        return tile


__all__ = ["FaultFit", "FaultedField", "WingField", "WingTiling", "compatible", "offsets", "sample_rows", "wing_code",
           "wing_signs"]  # fmt: skip
