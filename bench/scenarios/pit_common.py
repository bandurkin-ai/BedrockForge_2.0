"""What the scenarios `pit_*` share (ADR 0028 decision 15 and «Замеры», подшаг 21.6; ТЗ 8.11.3, 8.11.5).

    s = synth(1_000_000)                  a synthetic deposit: an ellipsoidal ore body with lognormal grades (the seed),
                                          one rock of density 2,7 and the «air» above a hilly relief; cubes of 10 m
    tpl = template()                      the real template of `mine/template.py` (45°, K = 8, one table, 17 offsets)
    v = cell_values(s, rf)                float64 per cell: $ of a block at the factor RF (air — 0)
    g, ids = graph(s, rf, cones=True)     the `PitGraph` of the integers of `econ_table.to_int`; `cones` — the union of
                                          the cones over v′ > 0 only (`ids` — its nodes in the full graph)
    save_graph(path, g, ids) / load_graph(path)
    check_closed(g, offsets, mined)       an INDEPENDENT closure check (numpy shifts of the dense box of the graph;
                                          shares no code with the kernels of `mine`)
    solve_files(solver, [graph.npz, …], work, threads=)   the solutions in a CHILD process (its own peak memory)

The economics is a stand-in of `econ.py` kept to what the solvers feel: a block is `TONNES` = 1000 m³ · 2,7 t; ore
pays `TONNES · max(RF · g · PRICE − C_P, 0)`, every block costs `TONNES · C_M(depth)` (the cost grows with the depth
below the relief). The scale is the one of production (`to_int`: Σ|v′| ≈ 10¹⁶): the capacities are of real size.

Run as `python -m bench.scenarios.pit_common solve …` it is the child process of the scenarios: one solver, one or
more graphs, prints one JSON line {results: [{seconds, mined, value, …}], peak_mb, rss_before_mb}.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

SIZE = 10.0  # m, the cube
DENSITY = 2.7  # t/m³
TONNES = SIZE**3 * DENSITY
ANGLE = 45.0
K = 8
SEED = 7
RF_MIN, RF_MAX, RF_STEP = 0.30, 1.50, 0.05  # ТЗ 8.11.5: 25 shells
PRICE = 55.0  # $ per (g/t) per tonne: price · recovery · (1 − royalty), the gold of 2000 $/oz
C_P = 20.0  # $/t of ore
C_M0, C_M_DEPTH = 2.5, 0.005  # $/t: at the relief and per metre of depth below it
G0 = 1.4  # g/t: the median grade of the centre of the body
HALO = 0.05  # g/t: the median grade of the rock round it (a few blocks above the cut-off: lone ore)
SIGMA = 0.9  # of the log of the grade
# the deposits: (G0, HALO, SIGMA, the body). `body` is the default; the others are the «hard» data of the scenarios:
#   marginal — the body hardly above the cut-off (ties, many blocks near zero), nugget — a wild grain (σ = 1,6),
#   noise    — no body at all: grades of one lognormal law everywhere, scattered ore above the cut-off
KINDS: dict[str, tuple[float, float, float, bool]] = {
    "body": (G0, HALO, SIGMA, True),
    "marginal": (0.45, 0.04, 0.5, True),
    "nugget": (0.9, 0.03, 1.6, True),
    "noise": (0.0, 0.10, 1.0, False),
}
AIR_SHARE = 0.2  # of the height: the highest point of the relief above the lowest

Mask = NDArray[np.bool_]
STAT_KEYS = ("roots", "mergers", "relabels", "work", "arcs")  # the counters that go to the report


def rfs() -> list[float]:
    """The 25 factors 0,30 … 1,50 step 0,05 (rounded: the same floats as the parameters of a scenario)."""
    return [round(RF_MIN + i * RF_STEP, 2) for i in range(round((RF_MAX - RF_MIN) / RF_STEP) + 1)]


# --- the deposit -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Synth:
    """The cells (P = i + nx·(j + ny·k), k = 0 — the bottom): `grade` g/t (float64), `air` (above the relief), `depth`
    m below the relief (the centre of the block)."""

    dims: tuple[int, int, int]
    grade: NDArray[np.float64]
    air: Mask
    depth: NDArray[np.float64]
    seed: int

    @property
    def nodes(self) -> int:
        return int(self.air.size - np.count_nonzero(self.air))


def _relief(nx: int, ny: int, nz: int) -> NDArray[np.int64]:
    """The top level of every column (the highest block that is not «air»); a hill of two harmonics."""
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny))
    f = 0.5 + 0.5 * np.sin(2 * math.pi * 1.3 * ii / nx + 0.5) * np.cos(2 * math.pi * 0.9 * jj / ny)
    return (nz - 1 - np.floor(AIR_SHARE * nz * f)).astype(np.int64)  # (ny, nx)


def dims_for(nodes: int) -> tuple[int, int, int]:
    """(nx, ny, nz) with `nx = ny` and nz ≈ 0.4 nx whose number of non-air blocks is the closest to `nodes`."""
    a0 = round((nodes / 0.34) ** (1.0 / 3.0))
    best: tuple[int, tuple[int, int, int]] | None = None
    for a in range(max(8, a0 - 3), a0 + 4):
        for nz in range(max(6, round(0.4 * a) - 3), round(0.4 * a) + 4):
            n = int((_relief(a, a, nz) + 1).sum())
            if best is None or abs(n - nodes) < best[0]:
                best = (abs(n - nodes), (a, a, nz))
    assert best is not None
    return best[1]


def synth(nodes: int, seed: int = SEED, kind: str = "body") -> Synth:
    """A deposit of about `nodes` blocks: the body is an ellipsoid (semi-axes 0,26·nx, 0,20·ny, 0,24·nz, the centre at
    0,40 nz) of grades G0·(1 − 0,6 r²) round it a halo; every block has a lognormal factor of its own (the seed).
    `kind` — one of `KINDS`."""
    g0, halo, sigma, body = KINDS[kind]
    nx, ny, nz = dims_for(nodes)
    rng = np.random.default_rng(seed)
    kt = _relief(nx, ny, nz)
    k = np.arange(nz).reshape(nz, 1, 1)
    j = np.arange(ny).reshape(1, ny, 1)
    i = np.arange(nx).reshape(1, 1, nx)
    air = (k > kt[None, :, :]).reshape(-1)
    depth = ((kt[None, :, :] - k + 0.5) * SIZE).reshape(-1)
    r2 = ((i + 0.5 - 0.5 * nx) / (0.26 * nx)) ** 2 + ((j + 0.5 - 0.5 * ny) / (0.20 * ny)) ** 2 \
        + ((k + 0.5 - 0.40 * nz) / (0.24 * nz)) ** 2  # fmt: skip
    r2 = r2.reshape(-1)
    base = np.where(r2 < 1.0, g0 * (1.0 - 0.6 * r2), halo) if body else np.full(r2.shape, halo)
    z = rng.standard_normal(nx * ny * nz)
    grade = base * np.exp(sigma * z)  # the median of the factor is 1
    return Synth((nx, ny, nz), grade.astype(np.float64), air, depth.astype(np.float64), seed)


@lru_cache(maxsize=4)
def template(k: int = K, angle: float = ANGLE) -> Any:
    """The real template of 21.3: cubes 10 m, one table of the constant angle, K levels (45°, K = 8: 17 offsets)."""
    from bf_core.mine import template as T

    return T.build_template(SIZE, SIZE, SIZE, T.angle_table([(0.0, angle)]), k=k)


def offsets(k: int = K, angle: float = ANGLE) -> NDArray[np.int32]:
    return np.ascontiguousarray(template(k, angle).tables[0].offsets)


def cell_values(s: Synth, rf: float) -> NDArray[np.float64]:
    """$ of every block at the factor `rf`: the better of «ore» and «waste», minus the mining; air — 0."""
    ore = np.maximum(rf * s.grade * PRICE - C_P, 0.0)
    v = TONNES * (ore - (C_M0 + C_M_DEPTH * np.maximum(s.depth, 0.0)))
    v[s.air] = 0.0
    return v


# --- the graph -------------------------------------------------------------------------------------------------------


def graph(s: Synth, rf: float, *, cones: bool = False, tab: NDArray[np.int32] | None = None) -> tuple[Any, Any]:
    """(`PitGraph`, `ids`) at `rf`. `ids` is None for the full graph; with `cones` the graph is the union of the cones
    over the blocks with v′ > 0 and `ids` are its nodes in the full graph (the shells of 21.7 solve this one)."""
    from bf_core.mine import graph as G
    from bf_core.mine.econ_table import to_int

    v = cell_values(s, rf)
    iv = to_int(v[~s.air])
    full = np.zeros(v.shape[0], dtype=np.int64)
    full[~s.air] = iv.v
    g = G.build_graph(s.dims, full, s.air, None, [offsets() if tab is None else tab])
    if not cones:
        return g, None
    return G.restrict_graph(g, G.cone_mask(g))


def save_graph(path: Path, g: Any, ids: Any = None) -> None:
    np.savez(path, dims=np.asarray(g.dims), origin=np.asarray(g.origin), shape=np.asarray(g.shape), pos=g.pos,
             index=g.index, value=g.value, tpl=g.tpl, tab_ptr=g.tab_ptr, tab_off=g.tab_off, level_ptr=g.level_ptr,
             ids=np.zeros(0, dtype=np.int64) if ids is None else ids, has_ids=np.asarray(ids is not None))  # fmt: skip


def load_graph(path: Path) -> tuple[Any, Any]:
    from bf_core.mine.graph import PitGraph

    z = np.load(path)

    def t3(name: str) -> tuple[int, int, int]:
        a, b, c = (int(x) for x in z[name])
        return a, b, c

    g = PitGraph(t3("dims"), t3("origin"), t3("shape"), z["pos"], z["index"], z["value"], z["tpl"], z["tab_ptr"],
                 z["tab_off"], z["level_ptr"])  # fmt: skip
    return g, (z["ids"] if bool(z["has_ids"]) else None)


# --- checks ----------------------------------------------------------------------------------------------------------


def value_of(g: Any, mined: Mask) -> int:
    return int(g.value[mined].sum())


def check_closed(g: Any, tab: NDArray[np.int32], mined: Mask) -> int:
    """The number of violations: a mined block u with a predecessor q = u + (Δi, Δj, m) that is a node and is not
    mined. Independent of the kernels: the dense box of the graph and numpy shifts, one per offset (0 — closed)."""
    mx, my, mz = g.shape
    dense = np.zeros(mx * my * mz, dtype=np.bool_)
    node = np.zeros(mx * my * mz, dtype=np.bool_)
    dense[g.pos] = mined
    node[g.pos] = True
    dense, node = dense.reshape(mz, my, mx), node.reshape(mz, my, mx)
    bad = 0
    for di, dj, m in tab.tolist():
        src = (slice(max(0, -m), mz - max(0, m)), slice(max(0, -dj), my - max(0, dj)),
               slice(max(0, -di), mx - max(0, di)))  # fmt: skip
        dst = (slice(max(0, m), mz - max(0, -m)), slice(max(0, dj), my - max(0, -dj)),
               slice(max(0, di), mx - max(0, -di)))  # fmt: skip
        # src[u] and dst[u] are the block and its predecessor; a predecessor beyond the box is free
        if all(sl.stop > sl.start for sl in src):
            bad += int(np.count_nonzero(dense[src] & node[dst] & ~dense[dst]))
    return bad


# --- the solvers in a child process ----------------------------------------------------------------------------------


def _rss_mb() -> tuple[float, float]:
    """(the resident set, the peak of it) of this process, MB."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            vals = {line.split(":")[0]: int(line.split()[1]) for line in fh if line.startswith(("VmHWM", "VmRSS"))}
        return vals["VmRSS"] / 1024.0, vals["VmHWM"] / 1024.0
    except (OSError, KeyError):
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        return peak, peak


def _warm() -> None:
    """The kernels compiled or loaded from the cache and the solver library imported (not timed, not in the peak)."""
    from bf_core.mine import flow_ref as F
    from bf_core.mine import graph as G
    from bf_core.mine import hpf as H

    tab = offsets()
    g = G.build_graph((6, 6, 6), np.arange(-100, 116, dtype=np.int64) * 7 - 3, np.zeros(216, dtype=bool), None, [tab])
    H.solve(g)
    H.solve_arcs(g.value, *G.predecessors(g))
    F.solve(g)
    G.cone_mask(g)


def child_solve(solver: str, graphs: list[Path], out: Path | None, threads: int) -> dict[str, Any]:
    """`solver`: `hpf` (the grid graph), `hpf_arcs` / `ortools` (explicit arcs from `predecessors`; the time of the
    arcs is separate). One result per graph file; the masks of all the graphs go to `out` (npz)."""
    from bench.scenarios import blocks_common as bc

    bc.limit_threads({"threads": threads})
    _warm()
    from bf_core.mine import flow_ref as F
    from bf_core.mine import graph as G
    from bf_core.mine import hpf as H

    results = []
    masks: dict[str, Any] = {}
    rss_before = 0.0
    for n, path in enumerate(graphs):
        g, ids = load_graph(path)
        tab = np.ascontiguousarray(g.tab_off)
        rss_before = max(rss_before, _rss_mb()[0]) if n else _rss_mb()[0]
        t0 = time.perf_counter()
        arcs_s = 0.0
        stats: dict[str, Any] = {}
        if solver == "hpf":
            mined = H.solve(g, stats=stats)
        else:
            indptr, indices = G.predecessors(g)
            arcs_s = time.perf_counter() - t0
            mined = (H.solve_arcs(g.value, indptr, indices, stats=stats) if solver == "hpf_arcs"
                     else F.solve_arcs(g.value, indptr, indices))  # fmt: skip
            stats["arcs"] = int(indices.shape[0])
            del indptr, indices
        seconds = time.perf_counter() - t0
        res = {"file": path.name, "nodes": g.n, "seconds": seconds, "arcs_s": arcs_s,
               "mined": int(np.count_nonzero(mined)), "value": value_of(g, mined),
               "violations": int(G.closure_violations(g, mined)[0]), "violations_numpy": check_closed(g, tab, mined),
               "stats": {k: v for k, v in stats.items() if k in STAT_KEYS}}  # fmt: skip
        results.append(res)
        if out is not None:
            masks[f"mined{n}"] = mined
            masks[f"ids{n}"] = np.zeros(0, dtype=np.int64) if ids is None else ids
    if out is not None:
        np.savez(out, **masks)
    rss, peak = _rss_mb()
    return {"solver": solver, "results": results, "rss_before_mb": round(rss_before, 1), "peak_mb": round(peak, 1)}


def solve_files(solver: str, graphs: list[Path], work: Path, *, threads: int = 1, tag: str = "") -> dict[str, Any]:
    """The child process (`-m bench.scenarios.pit_common solve`) solves every graph file: the peak memory of the
    solver is its own. Returns its JSON; `["mask"]` is the npz of the masks (`load_mask`)."""
    out = work / f"mask_{solver}{tag}.npz"
    root = Path(__file__).resolve().parents[2]
    paths = [str(root), str(root / "core"), os.environ.get("PYTHONPATH", "")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in paths if p)}
    cmd = [sys.executable, "-m", "bench.scenarios.pit_common", "solve", "--solver", solver, "--threads", str(threads),
           "--out", str(out), *map(str, graphs)]  # fmt: skip
    p = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"pit child ({solver}) failed (exit {p.returncode}):\n{p.stderr[-4000:]}")
    ans = json.loads(p.stdout.strip().splitlines()[-1])
    ans["mask"] = out
    return ans


def load_mask(path: Path, n: int = 0) -> tuple[Mask, Any]:
    """(mined, ids) of the graph number `n` of a child's call (`ids` None — a full graph)."""
    z = np.load(path)
    return z[f"mined{n}"], (z[f"ids{n}"] if z[f"ids{n}"].size else None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pit_common")
    ap.add_argument("cmd", choices=["solve"])
    ap.add_argument("--solver", required=True, choices=["hpf", "hpf_arcs", "ortools"])
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--out", type=Path)
    ap.add_argument("graphs", nargs="+", type=Path)
    a = ap.parse_args(argv)
    from bf_core.paths import configure_numba_cache

    configure_numba_cache()
    print(json.dumps(child_solve(a.solver, a.graphs, a.out, a.threads)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
