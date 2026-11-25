"""What the scenarios `estimate_*` share (ADR 0024 «Замеры», подшаг 18.25; ТЗ 8.8, 8.0.2).

    dep = Deposit.of(params)                 the grid of the model and the samples of a synthetic deposit (by seeds)
    keep(work, dep) / open_samples(work)     the samples of the deposit in the work folder; `RowSamples` of them
    rk = row(dep, "ok", s, variogram=TWO)    a row of the plan compiled (`kernel.compile_row`, a search of 3 passes)
    out = run_blocks(rk, dep, threads, …)    the portions of the model through `kernel.run`: the tallies, the probes
    reference_krige / reference_idw / assert_probes   the probes against the independent NumPy kriging of the tests
    drive(params, work, kind, …)             the whole scenario of the kernel (IDW, OK of a point, OK of a block)

The deposit is the kind of data a real base has and a clean grid has not: holes on a jittered grid with infill drilling
of a rich corner, intervals of unequal length, 3 % of them unassayed, twin samples at one point (averaged by
`prepare`), a lognormal grade with a smooth trend and a nugget, an outlier. The model reaches 5 % beyond the samples on
every side — the blocks of the edge fall to the later passes (ADR 0024: «90 % в 1-м проходе»; here 83–91 %). The
radius of the search follows the density of the samples, so that the first pass finds n_max of them.

A scenario checks its result (a failure stops the run): the estimates of `PROBES` blocks spread over the model, as the
run gave them (the portions of 2¹⁶ blocks on several threads), against the brute-force NumPy kriging of
`core/tests/reference/krige3d_numpy.py` (it imports nothing from `bf_core`; 1e−9 relative, the same pass, the same
blocks left unestimated) and the tallies of the statuses."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SIZE = (10.0, 10.0, 5.0)  # of a block of the model, m
CORNER = (500_000.0, 7_000_000.0, -200.0)
PORTION = 1 << 16  # blocks of a portion (`job_plan.PORTION`)
PAD = 0.05  # the share of the model beyond the samples on every side
STEP_Z = 10.0  # the mean length of an interval along a hole, m
ANG1 = 20.0  # the azimuth of the search and of the models, degrees
PROBES = 40  # the blocks checked against NumPy
REL_NUMPY = 1e-9  # Т8.8-7
SPH = {"c0": 0.1, "common_axes": True,
       "structures": [{"type": "spherical", "c": 0.9, "a1": 90.0, "a2": 70.0, "a3": 40.0, "ang1": ANG1}]}  # fmt: skip
TWO = {"c0": 0.1, "common_axes": True,
       "structures": [{"type": "spherical", "c": 0.5, "a1": 70.0, "a2": 50.0, "a3": 30.0, "ang1": ANG1},
                      {"type": "exponential", "c": 0.4, "a1": 200.0, "a2": 140.0, "a3": 60.0,
                       "ang1": ANG1}]}  # fmt: skip
PASSES = [{"factor": 1.0, "n_min": 8, "n_max": 24, "n_hole": 6}, {"factor": 2.0, "n_min": 4, "n_max": 24},
          {"factor": 3.0, "n_min": 2, "n_max": 24}]  # fmt: skip
SAMPLES_FILE = "samples.npz"


def limit_threads(params: dict[str, Any]) -> int:
    from . import blocks_common as bc

    return bc.limit_threads(params)


def peak_mb() -> float | None:
    from . import blocks_common as bc

    return bc.peak_mb()


# --- the deposit ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Deposit:
    """The model (counts of blocks) and the samples (their number) of a scenario."""

    counts: tuple[int, int, int]
    n_samples: int
    seed: int = 1

    @classmethod
    def of(cls, params: dict[str, Any]) -> Deposit:
        """`rows` blocks (a model of `nz` layers, the rest of it square) and about `samples` samples."""
        nz = int(params.get("nz", 100))
        nxy = int(math.ceil(math.sqrt(int(params["rows"]) / nz)))
        return cls((nxy, nxy, nz), int(params["samples"]), int(params.get("seed", 1)))

    @property
    def rows(self) -> int:
        return self.counts[0] * self.counts[1] * self.counts[2]

    @property
    def extent(self) -> np.ndarray:
        """The box of the model, project axes: (x0, y0, z0, x1, y1, z1)."""
        c = np.asarray(CORNER)
        return np.concatenate([c, c + np.asarray(self.counts) * np.asarray(SIZE)])

    def radius(self, n_max: int = 24) -> float:
        """The first radius: the ellipsoid (1 : 0.8 : 0.5) of it holds ≈ 3.2·n_max samples of the mean density."""
        rho = self.n_samples / (self.rows * float(np.prod(SIZE)) * (1 - 2 * PAD) ** 3)
        return float((3.2 * n_max / (4.0 / 3.0 * math.pi * rho * 0.8 * 0.5)) ** (1 / 3))

    # --- the holes and the samples

    def holes(self) -> dict[str, np.ndarray]:
        """Vertical holes: collars (x, y, top), the intervals along them (from, to), the value (NaN — unassayed)."""
        rng = np.random.default_rng(self.seed)
        x0, y0, z0, x1, y1, z1 = self.extent
        px, py, pz = (x1 - x0) * PAD, (y1 - y0) * PAD, (z1 - z0) * PAD
        lx, ly, lz = (x1 - x0) - 2 * px, (y1 - y0) - 2 * py, (z1 - z0) - 2 * pz
        per = max(2, int(lz / STEP_Z))
        n_holes = max(2, int(self.n_samples / (0.925 * per * 0.97)))
        main = int(n_holes * 0.8)  # the rest is infill of the rich corner
        side = max(1, int(math.ceil(math.sqrt(main))))
        side_y = max(1, int(math.ceil(main / side)))
        gx, gy = np.meshgrid((np.arange(side) + 0.5) / side, (np.arange(side_y) + 0.5) / side_y)
        hx = x0 + px + lx * (gx.ravel() + rng.normal(0, 0.25 / side, gx.size))
        hy = y0 + py + ly * (gy.ravel() + rng.normal(0, 0.25 / side_y, gy.size))
        n_inf = max(0, n_holes - hx.size)
        hx = np.concatenate([hx, x0 + px + lx * rng.uniform(0.0, 0.25, n_inf)])
        hy = np.concatenate([hy, y0 + py + ly * rng.uniform(0.0, 0.25, n_inf)])
        n_h = hx.size
        top = np.full(n_h, z1 - pz)
        counts = np.maximum(2, (rng.uniform(0.85, 1.0, n_h) * per).astype(np.int64))
        hole = np.repeat(np.arange(n_h, dtype=np.int32), counts)
        length = STEP_Z * rng.uniform(0.7, 1.3, hole.size)  # the intervals are of unequal length, one after another
        start = np.cumsum(counts) - counts
        cum = np.cumsum(length)
        before = np.repeat(cum[start] - length[start], counts)
        a = cum - length - before
        b = cum - before
        x, y = hx[hole], hy[hole]
        mid = 0.5 * (a + b)
        smooth = np.sin((x - x0) / 380.0) + np.cos((y - y0) / 270.0) + 0.7 * np.sin((top[hole] - mid - z0) / 90.0)
        z = np.exp(0.55 * smooth + 0.45 * rng.normal(0.0, 1.0, hole.size))
        z[rng.integers(hole.size)] = float(z.max() * 40.0)  # an outlier: a very rich sample
        z[rng.random(hole.size) < 0.03] = np.nan  # not assayed
        return {"hx": hx, "hy": hy, "top": top, "hole": hole, "from": a, "to": b, "z": z, "counts": counts}

    def arrays(self) -> dict[str, np.ndarray]:
        """xyz, z, hole of the samples with a value, and the twins of 1 % of them (at the same point)."""
        h = self.holes()
        ok = ~np.isnan(h["z"])
        hole = h["hole"][ok]
        xyz = np.column_stack([h["hx"][hole], h["hy"][hole], h["top"][hole] - 0.5 * (h["from"][ok] + h["to"][ok])])
        z = h["z"][ok]
        rng = np.random.default_rng(self.seed + 1)
        twins = rng.random(z.size) < 0.01  # `prepare` averages them
        return {"xyz": np.concatenate([xyz, xyz[twins]]), "z": np.concatenate([z, z[twins] * rng.uniform(0.8, 1.2)]),
                "hole": np.concatenate([hole, hole[twins]]).astype(np.int32)}  # fmt: skip

    # --- the blocks

    def grid(self) -> dict[str, Any]:
        return {"corner": list(CORNER), "counts": list(self.counts), "size": list(SIZE), "rotation": 0.0}

    def centres(self, lo: int, hi: int) -> np.ndarray:
        """The centres of the rows lo … hi of the model (X fastest, as the model keeps them)."""
        return self.centres_of(np.arange(lo, hi, dtype=np.int64))

    def centres_of(self, p: np.ndarray) -> np.ndarray:
        nx, ny, _ = self.counts
        i, j, k = p % nx, (p // nx) % ny, p // (nx * ny)
        return np.column_stack([CORNER[0] + (i + 0.5) * SIZE[0], CORNER[1] + (j + 0.5) * SIZE[1],
                                CORNER[2] + (k + 0.5) * SIZE[2]])  # fmt: skip

    def chunks(self, rows: int | None = None, portion: int = PORTION) -> Iterator[np.ndarray]:
        total = self.rows if rows is None else min(rows, self.rows)
        for lo in range(0, total, portion):
            yield self.centres(lo, min(lo + portion, total))

    def probes(self, n: int = PROBES) -> np.ndarray:
        """`n` rows of the model, spread over it (the edge too): the blocks checked against NumPy."""
        pick = np.random.default_rng(self.seed + 2).choice(self.rows, size=min(n, self.rows), replace=False)
        return np.sort(pick)


# --- the samples in the folder of the work ----------------------------------------------------------------------------


def keep(work: Path, dep: Deposit) -> int:
    """`prepare` of a scenario: the arrays of the deposit in the work folder (the child reads them; the timing is
    of the run) → the number of samples."""
    a = dep.arrays()
    np.savez(work / SAMPLES_FILE, xyz=a["xyz"], z=a["z"], hole=a["hole"])
    return int(a["z"].size)


def open_samples(work: Path) -> Any:
    """`RowSamples` of the kept arrays as `samples.load` builds them (`samples.prepare`: the order, the twins
      averaged)."""
    from bf_core.geostat.estimate.samples import prepare

    with np.load(work / SAMPLES_FILE) as f:
        xyz, z, hole = f["xyz"], f["z"], f["hole"]
    n = z.size
    names = [f"H{i}" for i in range(int(hole.max()) + 1)]
    return prepare(xyz, z, None, None, hole, names, np.zeros(n, np.int16), np.arange(n, dtype=np.int64),
                   np.zeros((n, 2)), origin=None)  # fmt: skip


# --- a row of the plan ------------------------------------------------------------------------------------------------


def search(dep: Deposit, passes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    r1 = dep.radius()
    return {"from": "manual", "r1": r1, "r2": 0.8 * r1, "r3": 0.5 * r1, "ang1": ANG1, "passes": passes or PASSES}


def spec(dep: Deposit, kind: str, *, variogram: dict[str, Any] | None = None, disc: tuple[int, int, int] = (1, 1, 1),
         passes: list[dict[str, Any]] | None = None, **extra: Any) -> Any:
    """The row of the plan as the job parses it (`spec.parse_estimation`), the search `manual` of the deposit."""
    from bf_core.geostat.estimate.spec import parse_estimation

    body: dict[str, Any] = {
        "model_id": 1, "samples": {"set_id": 1, "table": "t", "field": "z"}, "disc": list(disc),
        "method": {"kind": kind, "power": 2.0, **extra.pop("method", {})}, "search": search(dep, passes), **extra,
    }  # fmt: skip
    if variogram is not None:
        body["variogram"] = {"model_id": 1, "snapshot": variogram}
    return parse_estimation(body)


def row(dep: Deposit, kind: str, samples: Any, **kw: Any) -> Any:
    from bf_core.geostat.estimate import kernel as kn

    return kn.compile_row(spec(dep, kind, **kw), samples, block_size=SIZE, rotation_deg=0.0)


# --- the run --------------------------------------------------------------------------------------------------------


def run_blocks(rk: Any, dep: Deposit, threads: int, *, rows: int | None = None, probes: np.ndarray | None = None,
               keys: tuple[str, ...] = ("EST", "PASS", "NS"), mark: dict[int, float] | None = None,
               each: Callable[[Any], None] | None = None) -> dict[str, Any]:  # fmt: skip
    """`kernel.run` over the portions of the model → {blocks, by_status, by_pass, singular, first_s, probe}.
    `probes` — rows of the model: their `keys` values go to `probe[key]`; `mark` {blocks: seconds} gets the seconds
    since the call at which that many blocks were done (the 5 млн of ТЗ); `each(result)` sees every portion."""
    from bf_core.geostat.estimate import kernel as kn

    t0 = time.perf_counter()
    done = 0
    by_status = np.zeros(4, np.int64)
    by_pass = np.zeros(rk.n_passes, np.int64)
    singular = np.zeros(rk.n_passes, np.int64)
    first = math.nan
    want = sorted(mark) if mark else []
    probe: dict[str, list[float]] = {k: [] for k in keys}
    for res in kn.run(rk, dep.chunks(rows), threads=threads):
        if math.isnan(first):
            first = time.perf_counter() - t0
        n = res.status.shape[0]
        if probes is not None:
            at = probes[(probes >= done) & (probes < done + n)] - done
            for k in keys:
                probe[k].extend(res.values[k][at].tolist() if k in res.values else [math.nan] * at.size)
        done += n
        by_status += res.counts
        by_pass += res.by_pass
        singular += res.singular
        if each is not None:
            each(res)
        while want and done >= want[0]:
            assert mark is not None
            mark[want.pop(0)] = time.perf_counter() - t0
    return {"blocks": done, "by_status": by_status.tolist(), "by_pass": by_pass.tolist(),
            "singular": singular.tolist(), "first_s": first,
            "probe": {k: np.asarray(v) for k, v in probe.items()}}  # fmt: skip


def shares(out: dict[str, Any]) -> dict[str, Any]:
    """The tallies of a run as the shares of the blocks (`late` — estimated in the 2nd and 3rd pass, `lost` — not)."""
    total = max(sum(out["by_status"]), 1)
    got = max(out["by_status"][0], 1)
    return {"first_share": round(out["by_pass"][0] / got, 4), "late_share": round(1.0 - out["by_pass"][0] / got, 4),
            "lost_share": round(1.0 - out["by_status"][0] / total, 5), "by_status": out["by_status"],
            "by_pass": out["by_pass"], "singular": int(sum(out["singular"]))}  # fmt: skip


def cost(seconds: float, blocks: int, target: int = 20_000_000) -> dict[str, float]:
    """Seconds per block and the forecast for `target` blocks (a straight line: the run is a loop over portions)."""
    return {"us_per_block": round(1e6 * seconds / max(blocks, 1), 3),
            "forecast_20m_s": round(seconds * target / max(blocks, 1), 1)}  # fmt: skip


# --- the independent check ------------------------------------------------------------------------------------------


def _numpy() -> Any:
    from tests.reference import krige3d_numpy

    return krige3d_numpy


def rel_err(a: np.ndarray, b: np.ndarray) -> float:
    """The largest |a − b| relative to max(|b|, 1e−3·max|b|) (the measure of Т8.8-7)."""
    if a.size == 0:
        return 0.0
    return float(np.max(np.abs(a - b) / np.maximum(np.abs(b), 1e-3 * max(float(np.abs(b).max()), 1e-300))))


def _pass(p: Any) -> dict[str, Any]:
    return {"factor": p.factor, "n_min": p.n_min, "n_max": p.n_max, "n_hole": p.n_hole, "n_oct": p.n_oct,
            "k_oct": p.k_oct, "n_holes_min": p.n_holes_min}  # fmt: skip


def _ellipsoid(rk: Any) -> tuple[tuple[float, ...], tuple[float, ...], list[dict[str, Any]]]:
    return (tuple(float(a) for a in rk.ell.angles), tuple(float(r) for r in rk.ell.radii),
            [_pass(p) for p in rk.spec.search.passes])  # fmt: skip


def reference_krige(dep: Deposit, s: Any, rk: Any, kind: str, model: dict[str, Any], rows: np.ndarray,
                    z: np.ndarray | None = None) -> dict[str, np.ndarray]:  # fmt: skip
    """The NumPy kriging (`kind` "ok") of the blocks `rows` of the model on the samples of the row `s` (their order:
    the number of a sample is its position), with the search and the discretization of the row `rk`; `z` — another
    variable at the samples (the indicators of IK)."""
    ang, rad, passes = _ellipsoid(rk)
    return _numpy().krige_blocks(
        s.xyz, s.z if z is None else z, s.hole, dep.centres_of(rows), model=model, kind=kind, size=SIZE, disc=rk.disc,
        phi_deg=0.0, angles=ang, radii=rad, passes=passes)  # fmt: skip


def reference_idw(s: Any, rk: Any, centres: np.ndarray, power: float = 2.0) -> dict[str, np.ndarray]:
    """IDW by brute force: the selection of the passes of the NumPy reference, w = 1 / h_s^p over the selected."""
    ref = _numpy()
    ang, rad, passes = _ellipsoid(rk)
    r = ref.axes(*ang)
    stretch = np.array([1.0, rad[0] / rad[1], rad[0] / rad[2]])
    est = np.full(centres.shape[0], np.nan)
    kpass = np.zeros(centres.shape[0], np.int64)
    for b, c in enumerate(centres):
        k, idx, _, _ = ref.select(s.xyz, s.hole, c, ang, rad, passes)
        if k == 0:
            continue
        h = np.sqrt((((s.xyz[idx] - c) @ r.T * stretch) ** 2).sum(axis=1))
        w = 1.0 / np.maximum(h, 1e-300) ** power
        est[b] = float((w * s.z[idx]).sum() / w.sum())
        kpass[b] = k
    return {"EST": est, "PASS": kpass}


def assert_probes(got: dict[str, np.ndarray], ref: dict[str, np.ndarray], what: str, keys: tuple[str, ...] = ("EST",),
                  limit: float = REL_NUMPY) -> float:  # fmt: skip
    """The probes of a run against the reference: the same blocks estimated, the same pass, the values within `limit`.
    Returns the largest relative difference."""
    done = ref["PASS"] > 0
    assert done.any(), f"{what}: the probes have no estimated block"
    assert np.array_equal(~np.isnan(got["EST"]), done), f"{what}: the estimated blocks differ from the reference"
    assert np.array_equal(np.nan_to_num(got["PASS"]).astype(np.int64), ref["PASS"]), f"{what}: the pass differs"
    worst = 0.0
    for k in keys:
        err = rel_err(got[k][done], ref[k][done])
        assert err <= limit, f"{what}: {k} differs from the reference by {err:.2e} (> {limit:.0e})"
        worst = max(worst, err)
    return worst


# --- the scenarios of the kernel: IDW, OK of a point, OK of a block ---------------------------------------------------


def drive(params: dict[str, Any], work: Path, kind: str, *, variogram: dict[str, Any] | None = None,
          disc: tuple[int, int, int] = (1, 1, 1), marks: tuple[int, ...] = ()) -> dict[str, Any]:  # fmt: skip
    """A row of `kind` over the blocks of the model (`kernel.run`, `params["threads"]` cores, the portions of the job),
    the probes against NumPy. → info: samples_s, row_s, run_s (the blocks: the timing of ТЗ), first_s (the first
    result: the tree of the search is built then), us_per_block, forecast_20m_s, the shares of the passes, ref_err,
    t<M>m_s — the seconds at which `marks` (blocks) were done (those the model has)."""
    threads = limit_threads(params)
    dep = Deposit.of(params)
    t0 = time.perf_counter()
    s = open_samples(work)
    t1 = time.perf_counter()
    rk = row(dep, kind, s, variogram=variogram, disc=disc)
    t2 = time.perf_counter()
    probes = dep.probes()
    krige = kind in ("sk", "ok")
    mark = {m: math.nan for m in marks if m <= dep.rows}
    out = run_blocks(rk, dep, threads, probes=probes, keys=("EST", "PASS", "NS", *(("KV",) if krige else ())),
                     mark=mark)  # fmt: skip
    run_s = time.perf_counter() - t2
    info: dict[str, Any] = {"threads": threads, "samples": s.n, "model_blocks": out["blocks"],
                            "samples_s": round(t1 - t0, 3), "row_s": round(t2 - t1, 3), "run_s": round(run_s, 3),
                            "first_s": round(out["first_s"], 3), **cost(run_s, out["blocks"]),
                            **shares(out)}  # fmt: skip
    info.update({f"t{m // 1_000_000}m_s": round(v, 1) for m, v in mark.items()})
    assert out["blocks"] == dep.rows and sum(out["by_status"]) == dep.rows, out["by_status"]
    assert out["by_status"][0] >= 0.99 * dep.rows, f"too many blocks left unestimated: {out['by_status']}"
    assert info["first_share"] >= 0.8, f"the first pass took only {info['first_share']:.0%} of the blocks"
    if kind == "idw":
        ref = reference_idw(s, rk, dep.centres_of(probes))
        info["ref_err"] = assert_probes(out["probe"], ref, "idw")
    else:
        ref = reference_krige(dep, s, rk, kind, variogram or {}, probes)
        info["ref_err"] = assert_probes(out["probe"], ref, kind, ("EST", "KV"))
    info["probes"] = int(probes.size)
    return info
