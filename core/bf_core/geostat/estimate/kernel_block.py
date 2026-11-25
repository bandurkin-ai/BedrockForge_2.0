"""One block of the estimation in detail (ТЗ 8.8.4; Д-30; Э18, 18.7): what the preview and the inspector show. It is
`kernel.estimate` of a portion of one block (the same code), plus the candidates of the search with the reason of each
(`Searcher.explain`) and the weights of the selected samples. `kernel.estimate_block` and `kernel.BlockDetail` are
these names (the module `kernel` hands them out on demand)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .kernel import ST_OK, ST_OUT, Portion, RowKernel, _one_portion, _Trace
from .search import Candidates


@dataclass
class BlockDetail:
    """One block, as the preview and the inspector show it (e18-api.md §7)."""

    centre: np.ndarray  # f8 [3] project
    size: tuple[float, float, float] | None
    status: int
    pass_: int  # 1…P; 0 — not estimated
    values: dict[str, float]  # every key the method gives (EST, KV, …, NS, …, PASS, STATUS); NaN — none
    mu: float  # LAGR (OK); NaN otherwise
    sel: np.ndarray  # i8 [n] numbers of the selected samples (positions in the pool's samples of the row)
    lam: np.ndarray  # f8 [n] their weights (IDW — normalized, NN — 1, SK / OK — λ)
    candidates: Candidates  # of the pass that estimated, else of the last one tried
    cand_lam: np.ndarray  # f8 [k] the weight of every candidate (NaN — not selected)
    ellipsoid: dict[str, Any]  # {centre (local), centre_xyz, ang1–3, r1–3 (× factor), pass}
    passes: list[dict[str, Any]]  # [{pass, ok, n, reason: None | "few" | "singular"}] of the passes tried
    counts: dict[str, int]  # {candidates, selected}


def _empty_candidates() -> Candidates:
    z = np.empty(0)
    return Candidates(np.empty(0, np.int64), z, z.copy(), np.empty(0, np.int8), z.copy(), np.empty(0, np.uint8))


def estimate_block(
    rk: RowKernel,
    centre: np.ndarray,
    size: Any = None,
    *,
    active: bool = True,
    local: bool = False,
) -> BlockDetail:
    """One block by the same code as `estimate` (a portion of one block): the values, the pass, the selected samples
    with their weights, every candidate with the reason it was taken or not (`search.Searcher.explain`, Д-30), the
    ellipsoid of the pass. The candidates are those of the samples of the row (`rk.samples`: for the inspector the row
    is compiled on `samples.load(all_domains=True)`, so the other domains are among them). `size` — the size of the
    block (`own`)."""
    c = np.asarray(centre, dtype=np.float64).reshape(1, 3)
    sz = None if size is None else np.asarray(size, dtype=np.float64).reshape(1, 3)
    trace = _Trace()
    res = _one_portion(rk, Portion(c, sz, np.array([active])), local, trace)
    status = int(res.status[0])
    k = trace.k_last
    cl = c[0] if local else c[0] - rk.samples.origin
    keys = (*rk.keys, "STATUS")
    values = {key: float(res.values[key][0]) if key in res.values else math.nan for key in keys}
    for key, arr in res.values.items():
        values.setdefault(key, float(arr[0]))
    if status == ST_OUT or not np.isfinite(cl).all():
        cand = _empty_candidates()
        k = 0
    else:
        cand = rk.searcher.explain(cl, k)
    sel = trace.sel if trace.sel is not None else np.empty(0, np.int64)
    lam = trace.lam if trace.lam is not None else np.empty(0)
    cand_lam = np.full(cand.idx.shape[0], math.nan)
    if sel.size and lam.size:
        where = {int(s): float(w) for s, w in zip(sel, lam, strict=True)}
        for i, s in enumerate(cand.idx):
            if cand.reason[i] in (0, 1) and int(s) in where:
                cand_lam[i] = where[int(s)]
    fp = rk.searcher.fp_of(k)
    ang = rk.ell.angles
    rad = rk.ell.radii
    f = float(rk.searcher.passes[k].factor)
    ellipsoid = {"centre": cl.tolist(), "centre_xyz": (cl + rk.samples.origin).tolist(), "ang1": ang[0],
                 "ang2": ang[1], "ang3": ang[2], "r1": float(fp[0]), "r2": rad[1] * f, "r3": rad[2] * f,
                 "pass": k + 1}  # fmt: skip
    mu = values.get("LAGR", math.nan)
    return BlockDetail(
        centre=c[0].copy(), size=None if sz is None else (float(sz[0, 0]), float(sz[0, 1]), float(sz[0, 2])),
        status=status, pass_=int(res.values["PASS"][0]) if status == ST_OK else 0, values=values, mu=mu, sel=sel,
        lam=lam, candidates=cand, cand_lam=cand_lam, ellipsoid=ellipsoid, passes=trace.passes,
        counts={"candidates": int(cand.total), "selected": int(sel.size)},
    )  # fmt: skip
