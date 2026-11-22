"""The warm-up of the kernels of the variography (ADR 0022 решение 5; 16.5): every numba kernel of
`geostat.variogram` once on a few points, through the calls of the jobs and of the tab «Статистика» with their
dtypes — compiled into NUMBA_CACHE_DIR, or loaded from it, before the first window asks. `ops/warm.py` calls
`warm()` as its step `geostat` (in the workers — the jobs `variography.experimental`, `variography.map`,
«Перестроить» — and in the process of the API — the preview and the tab).

    warm() -> {part: seconds}
        axes      `experimental.calculate` over `points.Points` (the axes of R, a sample: `pairs._cell_checks`,
                  `pairs._strip`) and `experimental.experimental` on a plan (z = None — the tab)
        downhole  `downhole.calculate` (`downhole._holes`)
        fan       `fan.prepare` / `fan.run` in a turned plane and the rose (`fan._rays_strip`)
        map       `varmap.varmap` in a plane and in the plan of the tab, the rules round and trunc
                  (`varmap._map_strip`)
        frozen    all of it again on read-only columns, as `stats.source` gives them (the cached columns are
                  read-only, and numba compiles a kernel anew for a read-only array): every entry copies its
                  input, so no kernel gets a second specialization — the test of 16.5 holds it to that
    frozen(a) -> a read-only copy of the array

A part that fails is logged and the next goes on — a warm-up never stops a worker, the job compiles its kernel
itself then. The kernels of the normal scores (`stats.transforms`, `stats.weighted`) belong to the steps
`composites` and `stats` of `ops/warm.py`.

Time (16.5; Linux, 4 cores, one thread compiles; three runs in a fresh process): on an empty NUMBA_CACHE_DIR
6,9–7,2 s (axes 3,9–4,0 with the start of numba and the import of `stats`, the fan 1,9–2,0, the map 0,6–0,7,
along the holes 0,45, frozen 0,01 — nothing compiled again), the target 30 s (8.0.2); from the filled cache
1,1 s (axes 1,0 — the imports); the process peaks at 0,24 GB.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

PARTS = ("axes", "downhole", "fan", "map", "frozen")


def frozen(a: Any) -> Any:
    """A read-only copy of the array — what a cached column of `stats.source` is."""
    import numpy as np

    out = np.array(a)
    out.setflags(write=False)
    return out


def _cloud(read_only: bool = False) -> dict[str, Any]:
    """60 points of 6 holes (the middles of 2 m intervals) in a box of ~60 m, the origin subtracted."""
    import numpy as np

    rng = np.random.default_rng(7)
    hole = np.repeat(np.arange(6, dtype=np.int32), 10)
    depth = np.tile(np.arange(10) * 2.0 + 1.0, 6)
    x = (hole % 3) * 25.0 + 0.3 * depth + rng.normal(0.0, 0.5, 60) - 25.0
    y = (hole // 3) * 25.0 - 0.2 * depth + rng.normal(0.0, 0.5, 60) - 12.5
    z = 10.0 - 0.9 * depth
    v = np.exp(np.sin(x / 15.0) + rng.normal(0.0, 0.3, 60))
    out = {"xyz": np.stack([x, y, z], axis=1), "v": v, "hole": hole, "depth": depth, "x": x, "y": y, "z": z}
    return {k: frozen(a) for k, a in out.items()} if read_only else out


def _points(d: dict[str, Any]) -> Any:
    import numpy as np

    from .points import Points

    n = len(d["v"])
    counts = {"rows": n, "missing": 0, "no_position": 0, "outside_domain": 0, "used": n, "no_weight": 0}
    return Points(xyz=d["xyz"], origin=np.zeros(3), values=d["v"], z=d["v"], weights=None, hole=d["hole"],
                  holes=[str(h) for h in range(6)], depth=d["depth"], length=np.full(n, 2.0),
                  row_ids=np.arange(n, dtype=np.int64), counts=counts, sigma2=float(np.var(d["v"])),
                  sigma2_z=float(np.var(d["v"])), median_length=2.0)  # fmt: skip


def _axes(d: dict[str, Any]) -> None:
    from . import experimental as ex
    from .pairs import Direction

    p = _points(d)
    dirs = ex.axes(30.0, -10.0, 0.0, 22.5, 22.5, 30.0, 20.0)
    ex.calculate(p, dirs, 10.0, 6, 5.0, threads=2)
    ex.calculate(p, dirs, 10.0, 6, 5.0, limit=1.0, seed=1)  # the sample
    four = [Direction(az, 0.0, 22.5, 90.0) for az in (0.0, 45.0, 90.0, 135.0)]  # the tab: a plan
    ex.experimental(d["x"], d["y"], None, d["v"], four, 10.0, 6)


def _downhole(d: dict[str, Any]) -> None:
    from . import downhole

    downhole.calculate(_points(d), 2.0, 8, 1.0, threads=2)


def _fan(d: dict[str, Any]) -> None:
    import numpy as np

    from . import fan as fn

    p = _points(d)
    x, y, z = (np.ascontiguousarray(p.xyz[:, k]) for k in range(3))
    fp = fn.prepare(x, y, z, 10.0, 6, 5.0, plane=(30.0, -20.0, 10.0), n=12, dip_tol=22.5, band_v=20.0)
    fn.rose(fn.run(fp, p.values, threads=2), p.sigma2, 1.0, 5)


def _map(d: dict[str, Any]) -> None:
    import numpy as np

    from . import fan as fn
    from . import varmap as vm

    p = _points(d)
    x, y, z = (np.ascontiguousarray(p.xyz[:, k]) for k in range(3))
    vm.run(vm.prepare(x, y, z, 10.0, frame=fn.plane_frame((30.0, -20.0, 10.0)), nu=4, nv=4, thickness=20.0),
           p.values, 5, threads=2)  # fmt: skip
    vm.varmap(d["x"], d["y"], None, d["v"], 10.0, nu=4, nv=4)  # the tab: ΔX, ΔY
    vm.varmap(d["x"], d["y"], None, d["v"], 10.0, nu=4, nv=4, rule="trunc")


def _frozen(_d: dict[str, Any]) -> None:
    d = _cloud(read_only=True)
    for part in (_axes, _downhole, _fan, _map):
        part(d)


def warm() -> dict[str, float]:
    """Every part of `PARTS` in turn. → seconds a part (a part that failed is missing). Never raises."""
    log = logging.getLogger("bf_core.geostat.variogram.warm")
    took: dict[str, float] = {}
    runs: dict[str, Callable[[dict[str, Any]], None]] = {"axes": _axes, "downhole": _downhole, "fan": _fan,
                                                          "map": _map, "frozen": _frozen}  # fmt: skip
    data: dict[str, Any] | None = None
    for name in PARTS:
        t0 = time.perf_counter()
        try:
            if data is None:
                data = _cloud()
            runs[name](data)
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles its kernel then
            log.exception("warm-up of the variography (%s) failed", name)
            continue
        took[name] = time.perf_counter() - t0
    return took
