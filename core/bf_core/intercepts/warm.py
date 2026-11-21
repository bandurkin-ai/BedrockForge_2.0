"""The warm-up of the kernels of «Истинная мощность» and «Рудные пересечения» (ADR 0021 решение 20): every numba
kernel of the stage once on a few rows, with the dtypes of the jobs and of the previews — compiled into
NUMBA_CACHE_DIR, or loaded from it, before the first window asks. `ops/warm.py` calls `warm()` as its step
`intercepts` (in the workers and in the process of the API).

    warm() -> {part: seconds}
        tt        `tt.warm()`: the directions of a hole and the sum along it, the core, the chain of sources, the
                  sums of the summary (their wrappers make every array plain: one specialization a kernel)
        mesh      `orient_mesh.warm()`: the orientation by a wireframe — the crossings inside an interval, the
                  nearest triangle and the normals by area (`geo.nearest`) — on a wireframe in memory and on
                  one mapped from its blobs, read-only
        table     `table.run` as the job `intercept.run` runs it: the rule 8.5.3 (steps 1–2 on the holes and
                  inside the accepted — «в том числе»), acceptance by the true thickness of the candidates under
                  a chain «по домену → глобальная» (the code of a candidate — `intervals.mode_by_length`), the
                  means of the other fields by density (`intervals.aggregate`), the middles
                  (`Trajectories.positions`) — and `rule.run` itself on read-only columns
    frozen(a) -> a read-only copy of the array

numba compiles a kernel anew for a read-only array, so both kinds are run. A wireframe — by `orient_mesh.warm()`
(the vertices and the triangles of a stored one are read-only memory maps of its blobs). The rule — here:
`table.run` makes its columns plain (a read-only one is copied), so through it the kernel of the rule has one
specialization, whatever the columns are; `rule.run` called directly takes them as they are, and is run on
read-only columns too — a cached column given as it is —, with a density and without one (the lines are those
of `build_lines`, always writable).

Nothing is warmed twice. The kernels this stage shares with the stages before — the crossings of holes with a
wireframe and its tree of boxes (`geo.intersect`, `geo.tri_kernels`: Э4, Э8), the trajectories
(`drillholes.desurvey`), the interval engine (`composites.intervals_kernels`) — belong to the steps of
`ops/warm.py` that come before this one (`view`, `composites`, …): here they are only met on the way and loaded
from the cache; when this step runs on an empty cache it compiles them itself.

Time (подшаг 15.8; Linux, 4 cores, one thread compiles; two runs). After the steps of `ops/warm.py` before this
one — the kernels of the stage beyond those warmed already: 19,5–21,5 s (tt 3,4–4,0, mesh 12,4–13,6, table
3,7–3,9). On an empty cache: 36,2–37,0 s (tt 9,4–10,3, mesh 18,5–19,0, table 7,7–8,1 — the start of numba and
the shared kernels with it). From the filled cache: 1,1–1,5 s, what every start of the program pays in the
background; the process peaks at 0,4–0,5 GB while it compiles.
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any

X0, Y0, Z0 = 512_345.678, 6_789_012.345, 250.0  # a UTM position, as the data of a deposit


def frozen(a: Any) -> Any:
    """A read-only copy of the array — what a cached column or a mapped blob is to numba."""
    import numpy as np

    out = np.array(a)
    out.setflags(write=False)
    return out


def _table() -> None:
    """«Рудные пересечения» of three holes (a vertical one, an inclined one, one without a trajectory)."""
    import numpy as np

    from bf_core.composites import intervals as iv
    from bf_core.composites import jobs as cj
    from bf_core.composites import source
    from bf_core.drillholes.desurvey import Collars, desurvey

    from . import rule, table
    from . import spec as sp
    from .rule import RuleParams

    nan = np.nan
    au = np.array([0.1, 1.5, 2.0, 0.2, 0.3, 1.8, 0.1, 0.1, 0.1, 3.0, 2.0, 2.5, nan, 1.2, 0.1, 4.0, 3.0, 2.0, 0.2])
    counts = np.array([10, 5, 4])
    frm = np.concatenate([np.arange(c, dtype=np.float64) for c in counts])
    frm[17:] += 0.5  # a stretch without samples
    rho = np.where(np.arange(19) % 5 == 4, nan, 2.7)
    cu = np.where(np.arange(19) % 7 == 3, nan, 0.4)
    codes, names = source.text_codes(np.array(["A", "B", None] * 6 + ["A"], dtype=object))
    src = source.Lines(
        set_id=0, table_id=1, name="Пробы", name_key="пробы", token="warm", rev=1, kind="assay", fields={},
        row_ids=np.arange(1, 20, dtype=np.int64), holes=["1", "2", "3"],
        hole_db_ids=np.array([10, 20, 30], dtype=np.int64), starts=np.array([0, 10, 15, 19], dtype=np.int64),
        from_m=frm, to_m=frm + 1.0, numbers={"AU": au, "RHO": rho, "CU": cu}, codes={"DOM": codes},
        labels={"DOM": names},
    )  # fmt: skip
    traj = desurvey(
        Collars(np.arange(3), [X0, X0 + 50.0, nan], [Y0, Y0, Y0], [Z0, Z0, Z0], [20.0, 20.0, 20.0],
                azimuth=[nan, 90.0, 0.0], dip=[nan, -60.0, -90.0]),
        n_holes=3,
    )  # fmt: skip
    st = SimpleNamespace(hole_ids=np.array([10, 20, 30], dtype=np.int64), traj=traj)
    by_code = sp.Source("domain", domain=cj.DomainSpec(1, "Пробы", "DOM", "text"),
                        attitudes=(sp.Attitude("A", 40.0, 90.0),))  # fmt: skip
    chain = [by_code, sp.Source("global", alpha=30.0, dipdir=120.0)]
    o = table.Orienting.of(SimpleNamespace(orientation=chain, eps=0.1, step=1.0), src, st)
    lines = iv.build_lines(src.hole_of().astype(np.int64), src.from_m, src.to_m)
    grade, density, other = (lines.take(src.numbers[k]) for k in ("AU", "RHO", "CU"))
    by_tt = RuleParams(1.0, l_min=1.0, min_by="tt", include_cutoff=1.5)
    table.run(lines, grade, by_tt, name="AU", unit="г/т", fields={"CU": other}, density=density, orienting=o)
    for w in (frozen(density), None):  # the rule itself on read-only columns
        rule.run(lines, frozen(grade), by_tt, density=w)


def warm() -> dict[str, float]:
    """Compiles (or loads from NUMBA_CACHE_DIR) every kernel of the stage — module docstring. Returns the
    seconds of every part; a part that fails is logged and missing, the next one goes on (a warm-up never stops
    a worker: the job compiles its kernel itself then)."""
    from . import orient_mesh, tt

    log = logging.getLogger(__name__)
    took: dict[str, float] = {}
    for part, fn in (("tt", tt.warm), ("mesh", orient_mesh.warm), ("table", _table)):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:  # noqa: BLE001 - as `stats/warm.py`
            log.exception("warm-up of the intercepts (%s) failed", part)
            continue
        took[part] = time.perf_counter() - t0
    return took
