"""The warm-up of the numba kernels of the pit optimisation (Э21, 21.14; the step `pit` of `ops/warm.py`; ТЗ 8.11.5:
compilation ≤ 30 s; ADR 0028 decision 28, the lesson 17.39б of Э17).

`warm()` runs the real functions of `mine` on a tiny world with the types the pass of `job.run_scenario` gives them —
ONE specialization per kernel. The kernels are called through the wrappers the job calls (`econ.block_econ`,
`econ.row_terms`, `econ_table.Table.values` and `to_int`, `template.build_template`, `graph.build_graph`,
`shells.shells` with the solver `auto`, `report._finish`) so no argument type is guessed here; the kernels the wrappers
do not reach (`econ_kernels.control`, `report_kernels.accumulate`) are called as `econ.iter_rows` and
`report.build_report` call them: every array is writable, C-contiguous and of the fixed dtype of the pass — `f8`
values and tonnes, `i4` nodes, levels and rocks, `i8` the segments and the integers of the solver, `u1` the ore and the
table of a node, `i1` the state of a row, `bool` the masks — whatever the model holds (read-only or f4 columns are
made `f8` by `econ._w` before the kernels).

The parts (`PARTS`): `imports` — what a request needs and the process of the API does not (`plan`, `report_view`,
`store_data` with pyarrow, `solid`, …); `template` — the kernels of the angle template; `graph` — the graph, the cones,
the check of the closure; `econ` — the economics of a row, the terms, the control; `shells` — the integers of the
table, the values of the nodes, HPF on the grid and the whole bisection of `shells.shells`; `arcs` — the arcs and HPF
on them, OR-Tools (the reserve of the solver `auto`: only when HPF fails, or the solver is chosen); `report` — the
pit-by-pit accumulators; `relief` — `geo.dz_kernels` (the relief of a terrain, the z-grids of the shells).
The test `test_pit_warm.py` runs `pit.run` on models of several layouts after `warm()` and counts the signatures:
no new one. A part that fails does not stop the others (the job compiles what is missing itself). numba is not
imported here: every import is inside a function, so a module that imports `warm` does not pay for it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

PARTS = ("imports", "template", "graph", "econ", "shells", "arcs", "report", "relief")
DIMS = (4, 4, 3)  # the tiny grid: nx, ny, nz
OFFSETS = ((0, 0, 1), (1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1))  # (Δi, Δj, m): a cross over the node
RF = (0.6, 1.0, 1.4)


def _imports() -> None:
    import pyarrow  # noqa: F401  # `store_data` imports it inside its functions: the first request would pay
    import pyarrow.parquet  # noqa: F401

    from . import (  # noqa: F401
        compare,
        econ,
        econ_table,
        fields,
        plan,
        report,
        report_view,
        shells,
        solid,
        store,
        store_data,
        surfaces,
    )


def _template() -> None:
    from . import template as tp

    tab = tp.angle_table([(0.0, 45.0), (180.0, 40.0)])
    tpl = tp.build_template(10.0, 10.0, 10.0, tab, k=2)
    tab.phi(10.0)
    assert tpl.tables


def _graph_of(values: Any = None) -> Any:
    """The graph of the tiny grid: the top level is «air», the table of a node is uint8, the offsets int32."""
    import numpy as np

    from . import graph as gr

    nx, ny, nz = DIMS
    n = nx * ny * nz
    v = np.arange(n, dtype=np.int64) % 7 - 3 if values is None else values
    air = np.zeros(n, dtype=np.bool_)
    air[-nx * ny :] = True  # the top level is «воздух»
    tpl = np.zeros(n, dtype=np.uint8)
    return gr.build_graph(DIMS, v.astype(np.int64), air, tpl, [np.array(OFFSETS, dtype=np.int32)])


def _graph() -> None:
    import numpy as np

    from . import graph as gr

    g = _graph_of()
    cone = gr.cone_mask(g)
    gr.closure_violations(g, np.zeros(g.n, dtype=np.bool_))
    gr.restrict_graph(g, cone)
    gr.parent_index(g)
    gr.nesting_violations(g, np.zeros(g.n, dtype=np.int16))
    nx, ny, nz = DIMS  # the bridges: an «air» pocket under a block (`graph_kernels.mark_bridges`)
    air = np.zeros(nx * ny * nz, dtype=np.bool_)
    air[nx * ny + (nx + 1) :] = True  # the levels from the second up are «воздух» but for the first column
    air[nx * ny * (nz - 1) + 1] = False  # a block high above the pocket
    b = gr.build_graph(DIMS, np.zeros(air.size, dtype=np.int64), air, None, [np.array(OFFSETS, dtype=np.int32)],
                       bridges=True)  # fmt: skip
    b.real_view()


def _inputs() -> Any:
    from . import econ

    rocks = [econ.Rock("Руда", 20.0, 0.0, (0.9,)), econ.Rock("Порода", 0.0, 0.0, (0.0,), waste_only=True)]
    return econ.Inputs([econ.Element("AU", 1.0, 60.0, 1.0, 0.02)], rocks, 2.5, 100.0, density_value=2.7)


def _rows(n: int = 8) -> Any:
    """A portion of rows as `econ.iter_rows` gives it: the economics by `block_econ`, the arrays of the pass."""
    import numpy as np

    from . import econ

    inp = _inputs()
    f, vol, z = np.ones(n), np.full(n, 1000.0), np.linspace(0.0, 70.0, n)
    rock = (np.arange(n) % 2).astype(np.int32)
    g = np.abs(np.sin(np.arange(n, dtype=np.float64))).reshape(1, n) * 3.0
    g[0, 1] = np.nan  # a gap in the grade
    e = econ.block_econ(inp, f=f, vol=vol, rock=rock, z=z, g=g)
    rc = econ.RowChunk(0, np.arange(n, dtype=np.uint64), False, f, vol, rock, g, e)
    return inp, rc


def _econ() -> None:
    from . import econ
    from . import econ_kernels as ek

    inp, rc = _rows()
    e, ctl, w = rc.econ, econ.Control.new(inp), econ._w  # noqa: SLF001
    ek.control(
        rc.n,
        rc.gap,
        e.state,
        rc.rock,
        w(rc.vol),
        w(rc.f),
        e.t,
        rc.g,
        e.ore,
        inp.tables().waste,
        ctl.cnt,
        ctl.acc,
        ctl.rock_cnt,
        ctl.rock_acc,
    )
    econ.row_terms(rc, inp, 1.0)


def _table() -> Any:
    """`econ_table.Table` of the tiny graph: a few nodes of ore (A·RF > D + C_m) in a sea of waste."""
    import numpy as np

    from . import econ, econ_table

    nx, ny, nz = DIMS
    n = nx * ny * (nz - 1)  # the nodes: the top level is «воздух»
    ore = np.arange(n) % 5 == 0
    a = np.where(ore, 60.0, 0.0)
    p, seg = np.arange(n, dtype=np.uint64), np.arange(n + 1, dtype=np.int64)
    d, cm = np.where(ore, 10.0, 0.0), np.full(n, 1.0)
    return econ_table.Table(p, seg, a, d, cm, ore.astype(np.uint8), np.arange(n, dtype=np.int32),
                            ore.astype(np.uint8), econ.Control.new(_inputs()))  # fmt: skip


def _shells() -> None:
    from . import econ_table, shells

    tab, g = _table(), _graph_of()
    assert g.n == tab.n_nodes  # the nodes of the table are the non-air nodes of the graph
    out = shells.shells(g, tab, list(RF), threads=1)
    assert out.n_shells == len(RF)
    econ_table.to_int(tab.values(1.0))


def _arcs() -> None:
    import numpy as np

    from . import flow_ref, hpf
    from . import graph as gr

    g = _graph_of(np.arange(DIMS[0] * DIMS[1] * DIMS[2], dtype=np.int64) % 7 - 3)
    indptr, indices = gr.predecessors(g)
    hpf.solve_arcs(g.value, indptr, indices)
    flow_ref.solve(g)


def _report() -> None:
    import numpy as np

    from . import econ
    from . import report as rp
    from . import report_kernels as rk

    inp, rc = _rows()
    nlev, n_sh = 3, len(RF)
    e, tm = rc.econ, econ.row_terms(rc, inp, 1.0)
    pit = (np.arange(rc.n) % (n_sh + 1)).astype(np.int32)  # the node of a row is the row itself
    lev = (np.arange(rc.n) % nlev).astype(np.int32)
    inc = np.zeros((rk.S_ELEM + rk.PER_ELEM * len(inp.elements), n_sh + 1, 2))
    ben = np.zeros((3, (n_sh + 1) * nlev, 2))
    w = econ._w  # noqa: SLF001
    rk.accumulate(rc.n, np.arange(rc.n, dtype=np.int32), pit, lev, nlev, e.state, e.ore, w(e.t), w(e.cm), w(e.cp),
                  w(rc.g), tm.m, tm.mrec, tm.gross, tm.selling, tm.royalty, inc, ben)  # fmt: skip
    rp._finish(inp, pit, np.array(RF), inc, ben, nlev, (np.arange(rc.n) % nlev).astype(np.int64))  # noqa: SLF001


def _relief() -> None:
    from bf_core.geo import dz_kernels

    dz_kernels.warm()


WARMS: dict[str, Callable[[], None]] = {
    "imports": _imports, "template": _template, "graph": _graph, "econ": _econ, "shells": _shells, "arcs": _arcs,
    "report": _report, "relief": _relief,
}  # fmt: skip


def warm() -> dict[str, float]:
    """Compiles the kernels of the pit optimisation (or loads them from NUMBA_CACHE_DIR): seconds of each part."""
    took: dict[str, float] = {}
    for name, fn in WARMS.items():
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles the kernel itself then
            continue
        took[name] = time.perf_counter() - t0
    return took
