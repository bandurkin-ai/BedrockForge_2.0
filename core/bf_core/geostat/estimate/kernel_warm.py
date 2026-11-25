"""The warm-up of the estimation kernel (lesson 17.39б): every kernel of the methods, the kriging, the search and IK is
compiled (one signature each) and a few blocks of every method go through a row — also on read-only samples."""

from __future__ import annotations

from typing import Any

import numpy as np

from . import methods as mt
from .kernel import compile_row, estimate
from .kernel_block import estimate_block


def warm_kernel() -> None:
    """Compiles the kernels of the methods, the kriging, the search and IK, and runs a few blocks of every method
    through a row — also on read-only samples (lesson 17.39б). Every kernel has one signature."""
    from .indicator import warm_indicator
    from .krige import warm_krige
    from .samples import prepare
    from .search import warm_search
    from .spec import parse_estimation

    mt.warm_methods()
    warm_krige()
    warm_search()
    warm_indicator()
    rng = np.random.default_rng(1)
    n = 60
    xyz = rng.uniform(-30.0, 30.0, (n, 3))
    hole = rng.integers(0, 6, n).astype(np.int32)
    z = rng.uniform(0.0, 5.0, n)
    vg = {"c0": 0.1, "structures": [{"type": "spherical", "c": 0.9, "a1": 40, "a2": 30, "a3": 20, "ang1": 20}]}
    centres = rng.uniform(-20.0, 20.0, (8, 3))
    for freeze in (False, True):
        s = prepare(xyz, z, None, None, hole, [f"h{i}" for i in range(6)], np.zeros(n, np.int16),
                    np.arange(n, dtype=np.int64), np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip
        if freeze:
            for arr in (s.local, s.z, s.xyz):
                arr.setflags(write=False)
        for kind in ("nn", "idw", "sk", "ok", "ik"):
            for disc in ((1, 1, 1), (2, 2, 2)):
                body: dict[str, Any] = {
                    "model_id": 1, "samples": {"set_id": 1, "table": "t", "field": "z"}, "disc": list(disc),
                    "method": {"kind": kind, "ik": {"thresholds": [1.0, 2.5], "models": "median"}},
                    "search": {"from": "manual", "r1": 40.0, "r2": 30.0, "r3": 20.0, "ang1": 20.0,
                               "passes": [{"factor": 1, "n_min": 3, "n_max": 12, "n_hole": 4, "n_oct": 4},
                                          {"factor": 2, "n_min": 2, "n_max": 12}]},
                }  # fmt: skip
                if kind in ("sk", "ok", "ik"):
                    body["variogram"] = {"model_id": 1, "snapshot": vg}
                spec = parse_estimation(body)
                rk = compile_row(spec, s, block_size=(4.0, 4.0, 2.0), rotation_deg=10.0)
                estimate(rk, centres, active=np.arange(8) != 3)
                estimate(rk, centres[:2], sizes=np.array([[4.0, 4.0, 2.0], [2.0, 2.0, 1.0]]),
                         exclude=("sample", np.array([0, 1])))  # fmt: skip
                estimate_block(rk, centres[0], pool=s)
