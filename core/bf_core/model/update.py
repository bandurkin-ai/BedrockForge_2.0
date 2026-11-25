"""«Обновить» — the caches of the implicit model by content (подшаг 19.12; ТЗ 8.9.3 «Обновление», Т8.9-8; ADR 0026
«Варианты» → «Обновить» А, решения 23, 25; docs/progress/e19-api.md §2.2).

    cache = PatchCache.from_blobs(conn, store, model_id)           # the patches of the last build, by hash
    solver = CachingSolver(spec, cache, matrix=A)                  # the `solver` seam of `bodies.build_bodies`
    bricks = BrickFiles(folder, model_id, grid, record, matrix=A, read=True, used=used)   # `isosurface(cache=…)`

The patch. Its hash is `octree.hash_patches(points, patches, values=f, salt=…)`: the centre, R, the number of points,
the points of the patch in the space of the patches (A applied) in the canonical order and their values, and the
salt — the kernel, the radius of the finite kernel, the smoothing (mode, η₀, the target of the condition number),
A; of a structural trend (19.9) the hash of the patch takes its A_j too (`anisotropy.patch_hashes`: a changed trend
changes the solution of a patch whose points stayed). The patch is a function of those alone (η is a function of the
points of the patch and η₀), so a patch whose hash is in the cache takes its λ, c, η and cond₁ from the last build:
bit for bit what its LU would give; only the patches whose hash is new are solved (`solve.solve_patches` of the
subset — each patch is solved by its own, so the bytes do not depend on the others).

The brick. Its file is `cache\\implicit\\<model>\\<hash>.npy` (float64 n × n × n, the values of the nodes): the hash is
`Grid.token()`, the index of the brick and the hashes of the patches whose ball reaches the box of the brick, in the
order of their numbers (the field at a node is the sum over exactly these patches in this order). A brick that is
asked for and found is read, the others are computed by the same field and written. `used` collects the names of the
files a run touched: `sweep` removes the rest after the run (the old keys of the changed patches).

Everything that is not taken from a cache is computed by the same code as the full build: the result is the same bits
(Т8.9-8). numba and scipy come with the functions that need them, not with this module.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import struct
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from . import store_base as sb
from . import store_blobs as bl
from .octree import PatchSet, hash_patches, transform_points
from .spec_parts import eta as eta_of_s

if TYPE_CHECKING:
    import sqlalchemy as sa

    from bf_core.geo.iso_bricks import Grid
    from bf_core.storage.blobs import BlobStore

    from .spec import Group, ModelSpec
    from .system import PatchSolution

F8 = NDArray[np.float64]
Tick = Callable[[], None]

PATCH_TAG = b"bf-implicit-patch/1"
BRICK_TAG = b"bf-implicit-brick/1"
REACH_PAD = 1e-6  # the balls of the patches are tested a little wider than the field does: a superset only
HASH_SIZE = bl.HASH_SIZE


def cache_dir(folder: Path | str, model_id: int) -> Path:
    """`cache\\implicit\\<model>` of the project folder (derived data: 6.5, it may be deleted)."""
    return Path(folder) / "cache" / "implicit" / str(int(model_id))


# --- the patches ------------------------------------------------------------------------------------------------------


@dataclass
class Record:
    """One field the solver solved or took from the cache: what 19.12 writes into `implicit_patches` / `_solution`."""

    patches: PatchSet
    hashes: list[bytes]
    solutions: list[PatchSolution]
    n_points: int
    group: int | None = None
    wing: int = -1
    reused: int = 0  # the patches taken from the cache
    matrices: list[F8] | None = None  # A_j of a structural trend, one per patch (None — global A or none)


class PatchCache:
    """The solutions of the last build by the hash of the patch: {hash: (λ, c, η, cond₁)}."""

    def __init__(self, entries: Mapping[bytes, tuple[F8, F8, float, float]] | None = None) -> None:
        self._e: dict[bytes, tuple[F8, F8, float, float]] = dict(entries or {})

    @staticmethod
    def empty() -> PatchCache:
        return PatchCache()

    @staticmethod
    def from_blobs(conn: sa.Connection, store: BlobStore, model_id: int) -> PatchCache:
        """The patches and the solution of the model (both files; a model without a result or with files that do not
        fit each other gives an empty cache: the run is then a full build)."""
        shas = bl.shas_of(conn, model_id)
        if not {sb.ROLE_PATCHES, sb.ROLE_SOLUTION} <= set(shas):
            return PatchCache()
        try:
            rows = bl.read_patches(store, shas[sb.ROLE_PATCHES])
            solution = bl.read_solution(store, shas[sb.ROLE_SOLUTION])
        except Exception:  # noqa: BLE001 - a damaged derived file is not a reason to fail: the build repeats it
            return PatchCache()
        n = rows["n"].astype(np.int64)
        off = rows["offset"].astype(np.int64)
        if len(n) and (off.min() < 0 or int((off + n + 4).max()) > len(solution)):
            return PatchCache()
        raw = np.ascontiguousarray(rows["hash"]).tobytes()  # the bytes as stored: numpy strips the zeros of a scalar
        out: dict[bytes, tuple[F8, F8, float, float]] = {}
        for i in range(len(n)):
            at = int(off[i])
            lam, c = solution[at : at + int(n[i])], solution[at + int(n[i]) : at + int(n[i]) + 4]
            out[raw[HASH_SIZE * i : HASH_SIZE * (i + 1)]] = (lam, c, float(rows["eta"][i]), float(rows["cond"][i]))
        return PatchCache(out)

    @staticmethod
    def from_records(records: Sequence[Record]) -> PatchCache:
        """The cache of the fields a solver recorded (what the files of the model would give back): tests, the bench."""
        out: dict[bytes, tuple[F8, F8, float, float]] = {}
        for r in records:
            for h, s in zip(r.hashes, r.solutions, strict=True):
                out[h] = (s.lam, s.poly, float(s.eta), float(s.cond1))
        return PatchCache(out)

    def get(self, key: bytes) -> tuple[F8, F8, float, float] | None:
        return self._e.get(key)

    def __len__(self) -> int:
        return len(self._e)


def _matrix_bytes(matrix: F8 | None) -> bytes:
    return b"-" if matrix is None else np.ascontiguousarray(matrix, dtype="<f8").tobytes()


def salt_of(spec: ModelSpec, eta0: float, matrix: F8 | None) -> bytes:
    """The parameters a solution of a patch depends on besides its points and values (the module text)."""
    sm = spec.smoothing
    target = math.nan if sm.mode != "auto" or sm.cond_target is None else float(sm.cond_target)
    a = math.nan if spec.kernel_radius is None else float(spec.kernel_radius)
    return b"|".join([PATCH_TAG, spec.kernel.encode("ascii"), struct.pack("<d", a), sm.mode.encode("ascii"),
                      struct.pack("<2d", float(eta0), target), _matrix_bytes(matrix)])  # fmt: skip


def _per_patch(value: float | Sequence[float] | None, m: int) -> list[float | None]:
    if value is None or isinstance(value, (int, float, np.floating, np.integer)):
        return [None if value is None else float(value)] * m
    return [None if v is None else float(v) for v in value]


def subset(ps: PatchSet, rows: Sequence[int]) -> PatchSet:
    """The patches `rows` of `ps` as a `PatchSet` of their own (the indices stay the numbers of the points)."""
    r = np.asarray(rows, dtype=np.int64)
    sizes = ps.sizes[r]
    indptr = np.zeros(len(r) + 1, dtype=np.int64)
    np.cumsum(sizes, out=indptr[1:])
    indices = np.concatenate([ps.patch(int(j)) for j in r]) if len(r) else np.empty(0, dtype=np.int64)
    return replace(ps, centers=np.ascontiguousarray(ps.centers[r]), radii=np.ascontiguousarray(ps.radii[r]),
                   leaf_min=np.ascontiguousarray(ps.leaf_min[r]), leaf_max=np.ascontiguousarray(ps.leaf_max[r]),
                   leaf_counts=np.ascontiguousarray(ps.leaf_counts[r]), indptr=indptr, indices=indices,
                   hashes=tuple(ps.hashes[int(j)] for j in r) if ps.hashes else ())  # fmt: skip


class CachingSolver:
    """The `solver` seam of `bodies.build_bodies` (and of `shells`): solves the patches whose hash the cache does not
    have, takes the others from it, and records every field it was asked for (`records`) for the files of the model.
    `eta_of` is the `eta_of` seam of the automatic smoothing (the patches of the cache keep their η)."""

    def __init__(self, spec: ModelSpec, cache: PatchCache, *, matrix: F8 | None = None, threads: int = 1) -> None:
        self.spec, self.cache, self.matrix, self.threads = spec, cache, matrix, threads
        self.records: list[Record] = []
        self._stash: tuple[PatchSet, float, list[bytes]] | None = None

    # -- the hashes ------------------------------------------------------------------------------------------------

    def _hashes(self, points: Any, values: Any, patches: PatchSet, eta0: float,
                matrices: Sequence[Any] | None = None) -> list[bytes]:  # fmt: skip
        from .anisotropy import patch_hashes

        salt = salt_of(self.spec, eta0, patches.matrix)
        hexes = hash_patches(points, patches, values=values, salt=salt)
        if matrices is not None:
            hexes = list(patch_hashes(hexes, matrices))
        return [bytes.fromhex(h) for h in hexes]

    def eta_of(self, group: Group, patches: PatchSet, xyz: Any, f: Any, hb: float, *,
               matrices: Sequence[Any] | None = None) -> list[float]:  # fmt: skip
        """η of the patches in the mode «Авто» (Д-25): measured for the patches that are not in the cache, taken from
        it for the others. Called by `fit_group` right before the solver: the hashes are kept for it."""
        from . import smoothing

        eta0 = eta_of_s(self.spec.kernel, self.spec.s_of(group), hb)
        hashes = self._hashes(xyz, f, patches, eta0, matrices)
        miss = [j for j, h in enumerate(hashes) if self.cache.get(h) is None]
        got = smoothing.eta_for_patches(xyz, f, patches, self.spec, group=group, h_bar=hb, only=miss,
                                        matrices=matrices, threads=self.threads)  # fmt: skip
        out = [float(v) for v in got]
        for j, h in enumerate(hashes):
            hit = self.cache.get(h)
            if hit is not None:
                out[j] = hit[2]
        self._stash = (patches, float(eta0), hashes)
        return out

    # -- the solver ------------------------------------------------------------------------------------------------

    def __call__(
        self, points: Any, values: Any, patches: PatchSet, *, kind: str | int = "linear",
        radius: float | Sequence[float] | None = None, eta: float | Sequence[float] = 0.0, threads: int = 1,
        tick: Tick | None = None, sources: Any = None, matrices: Sequence[Any] | None = None, **rest: Any,
    ) -> list[PatchSolution]:  # fmt: skip
        from .anisotropy import stretch_of
        from .solve import solve_patches
        from .system import PatchSolution, RbfSingular

        m = patches.n_patches
        if self._stash is not None and self._stash[0] is patches:
            hashes = self._stash[2]
        else:
            if not isinstance(eta, (int, float, np.floating, np.integer)):
                raise ValueError("the patches of a per-patch η must be prepared by `eta_of`")
            hashes = self._hashes(points, values, patches, float(eta), matrices)
        self._stash = None
        etas, radii = _per_patch(eta, m), _per_patch(radius, m)
        pts = np.asarray(points, dtype=np.float64)
        space = pts if patches.matrix is None else transform_points(pts, patches.matrix)
        out: list[PatchSolution | None] = [None] * m
        miss: list[int] = []
        for j, h in enumerate(hashes):
            hit = self.cache.get(h)
            if hit is None:
                miss.append(j)
                continue
            lam, c, eta_j, cond = hit
            x = space[patches.patch(j)]
            centre, scale = patches.centers[j], float(patches.radii[j])
            a = None if matrices is None else matrices[j]
            if a is not None:  # the frame of `solve_patches`: the points and the centre turned, the ball's radius
                x = transform_points(x, a)
                centre = transform_points(np.asarray(centre, dtype=np.float64).reshape(1, 3), a)[0]
                scale *= stretch_of(a)
            out[j] = PatchSolution(points=np.array(x, dtype=np.float64, order="C", copy=True), lam=lam.copy(),
                                   poly=c.copy(), centre=np.array(centre, dtype=np.float64), scale=scale,
                                   kind=str(kind), radius=radii[j], eta=eta_j, cond1=cond)  # fmt: skip
            if tick is not None:
                tick()
        if miss:
            sub = subset(patches, miss)
            try:
                got = solve_patches(points, values, sub, kind=kind,
                                    radius=None if radius is None else [float(radii[j] or 0.0) for j in miss],
                                    eta=[etas[j] or 0.0 for j in miss], threads=threads, tick=tick, sources=sources,
                                    matrices=None if matrices is None else [matrices[j] for j in miss])
            except RbfSingular as err:
                raise _renumbered(err, miss) from None
            for j, s in zip(miss, got, strict=True):
                out[j] = s
        done = [s for s in out if s is not None]
        assert len(done) == m
        mats = None if matrices is None else [np.array(a, dtype=np.float64) for a in matrices]
        self.records.append(Record(patches, hashes, done, int(pts.shape[0]), None, -1, m - len(miss), mats))
        return done

    def last(self) -> Record | None:
        return self.records[-1] if self.records else None

    def record_of(self, patches: PatchSet) -> Record | None:
        for r in reversed(self.records):
            if r.patches is patches:
                return r
        return None

    def reused(self) -> tuple[int, int]:
        """(patches taken from the cache, patches in all)."""
        return sum(r.reused for r in self.records), sum(len(r.hashes) for r in self.records)


def _renumbered(err: Any, miss: Sequence[int]) -> Any:
    """`rbf_singular` of a patch of the subset: the number of the patch (field and text) is the one of the whole."""
    sub = int(err.extra.get("patch", 0))
    whole = int(miss[sub]) if 0 <= sub < len(miss) else sub
    err.extra["patch"] = whole
    err.message = err.message.replace(f"Участок № {sub + 1} ", f"Участок № {whole + 1} ", 1)
    err.args = (err.message,)
    return err


# --- the bricks -------------------------------------------------------------------------------------------------------


@dataclass
class BrickStats:
    hits: int = 0
    puts: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class BrickFiles:
    """`iso_bricks.BrickCache` of one field on files `<hash>.npy` (the module text). `read` False — the full build:
    it writes the bricks (for the next «Обновить») and never takes one."""

    def __init__(self, folder: Path | str, model_id: int, grid: Grid, record: Record, *, matrix: F8 | None = None,
                 read: bool = True, used: set[str] | None = None, stats: BrickStats | None = None,
                 extra: bytes = b"") -> None:  # fmt: skip
        self.dir = cache_dir(folder, model_id)
        self.grid, self.read, self.matrix = grid, read, matrix
        self.used = set() if used is None else used
        self.stats = stats or BrickStats()
        ps = record.patches
        self._centres = np.ascontiguousarray(ps.centers, dtype=np.float64)
        reach = np.asarray(ps.radii, dtype=np.float64) * (1.0 + REACH_PAD)
        self._reach2 = reach * reach
        self._hashes = np.frombuffer(b"".join(record.hashes), dtype=np.uint8).reshape(-1, HASH_SIZE)
        self._shape = (grid.n, grid.n, grid.n)
        self._token = grid.token().encode("ascii") + b"|" + extra  # `extra`: what else the field of a shell depends on
        self._made = False
        blank = io.BytesIO()
        np.save(blank, np.zeros(self._shape, dtype="<f8"), allow_pickle=False)
        self._head = blank.getvalue()[: -8 * grid.n**3]  # the magic and the header of this shape
        self._size = len(self._head) + 8 * grid.n**3

    def key(self, index: tuple[int, int, int]) -> str:
        """The name of the file of a brick: the grid, the index and the patches that reach its box, by their numbers."""
        g = self.grid
        b = np.asarray(index, dtype=np.int64)
        size = g.step * g.h
        lo = np.asarray(g.lo, dtype=np.float64) + b * size
        corners = np.array([[lo[0] + a * size, lo[1] + c * size, lo[2] + d * size]
                            for a in (0, 1) for c in (0, 1) for d in (0, 1)])  # fmt: skip
        if self.matrix is not None:
            corners = transform_points(corners, self.matrix)
        gap = np.maximum(np.maximum(corners.min(axis=0) - self._centres, self._centres - corners.max(axis=0)), 0.0)
        sel = np.flatnonzero(np.einsum("ij,ij->i", gap, gap) <= self._reach2)
        h = hashlib.sha256(BRICK_TAG)
        h.update(self._token)
        h.update(b.astype("<i8").tobytes())
        h.update(struct.pack("<q", len(sel)))
        h.update(self._hashes[sel].tobytes())
        return h.hexdigest()

    def get(self, index: tuple[int, int, int]) -> F8 | None:
        if not self.read:
            return None
        name = self.key(index)
        try:
            with open(self.dir / f"{name}.npy", "rb") as fh:
                raw = fh.read(self._size + 1)
        except OSError:
            return None
        # the file is the `.npy` this class wrote: the header of the same shape, then the values (no parser)
        if len(raw) != self._size or not raw.startswith(self._head):
            return None
        a = np.frombuffer(raw, dtype="<f8", offset=len(self._head)).reshape(self._shape)
        with self.stats.lock:
            self.stats.hits += 1
            self.used.add(name)
        return a  # type: ignore[no-any-return]

    def put(self, index: tuple[int, int, int], values: F8) -> None:
        name = self.key(index)
        if not self._made:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._made = True
        try:
            # written in place: a torn file does not pass the check of `get` (shape and dtype), and is written again
            with open(self.dir / f"{name}.npy", "wb") as fh:
                fh.write(self._head)
                fh.write(np.ascontiguousarray(values, dtype="<f8").tobytes())
        except OSError:
            return  # derived data: a full disk or a locked file is not a failure of the run
        with self.stats.lock:
            self.stats.puts += 1
            self.used.add(name)


def sweep(folder: Path | str, model_id: int, used: set[str]) -> int:
    """After a run: the files of the bricks that no run touched (the keys of the patches that changed) are removed.
    Returns how many."""
    d = cache_dir(folder, model_id)
    gone = 0
    try:
        with os.scandir(d) as it:
            entries = list(it)
    except OSError:
        return 0
    for e in entries:
        if e.name.endswith(".npy") and e.name[:-4] in used:
            continue
        try:
            os.unlink(e.path)
            gone += 1
        except OSError:
            pass
    return gone


__all__ = ["BrickFiles", "BrickStats", "CachingSolver", "PatchCache", "Record", "cache_dir", "salt_of", "subset",
           "sweep"]  # fmt: skip
