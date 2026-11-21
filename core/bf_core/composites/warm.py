"""The warm-up of the kernels of composites (ADR 0018 §5, решение 26): every kernel of the stage once on three
holes, with the dtypes of the job and of the preview — compiled into NUMBA_CACHE_DIR, or loaded from it, before
the first window asks. `ops/warm.py` calls `warm()` as its step `composites` (in the workers and in the API
process). Here: composites by length and the summary «до/после»; by domains (a field of the samples, another
table), by benches, merging (`_warm_tables`, 12.4–12.6); the top cut and declustering (`_warm_prep`, 12.21).
From their modules: coding and domains by wireframes, coordinates (`coding.warm`, 12.7); Д-26 (`ore_set.warm`,
12.10–12.14), the analysis of conditions (`ore_conditions.warm`, 12.16), the hand edit of ore intervals
(`ore_edit.warm`, 12.17). `bench/scenarios/composite_compile.py` names the kernels a first job would still
compile (`left` — empty).
"""

from __future__ import annotations

from typing import Any


def warm() -> None:
    import numpy as np

    from . import intervals as iv
    from .length import LengthParams, composite_by_length, summary

    holes = np.array([0, 0, 0, 1, 1, 2], dtype=np.int64)
    frm = np.array([0.0, 1.0, 3.0, 0.0, 2.5, 5.0])
    to = np.array([1.0, 3.0, 4.0, 2.5, 4.0, 7.0])
    au = np.array([1.0, 2.0, np.nan, 0.5, 4.0, 3.0])
    rho = np.array([2.5, 3.0, 2.7, np.nan, 2.6, 2.8])
    codes = np.array([0, 1, -1, 0, 0, 1], dtype=np.int32)
    lines = iv.build_lines(holes, frm, to)
    values = iv.stack_fields(lines, [au, au * 2.0])
    for remainder in ("attach", "equal"):
        p = LengthParams.of({"length": 2.0, "remainder": remainder})
        c = composite_by_length(lines, values, p, density=lines.take(rho), by_density=True,
                                texts=[lines.take(codes)], ends=np.array([5.0, np.nan, 8.0]))  # fmt: skip
        summary(c.lines.to - c.lines.frm, c.values.values, ["AU", "AU2"])
    summary(lines.lengths(), values, ["AU", "AU2"])
    _warm_tables(lines, values, lines.take(rho), lines.take(codes))
    _warm_prep(frm, to, au, codes)

    from .coding import warm as warm_coding

    warm_coding()  # the stretches inside bodies, the collar check, the division and the middle (12.7)

    from .ore_set import warm as warm_ore

    warm_ore()  # Д-26 (12.10–12.14): the five modes, the events, the nested rows, the means of other fields

    from .ore_conditions import warm as warm_conditions
    from .ore_edit import warm as warm_edit

    warm_conditions()  # the analysis of conditions (12.16)
    warm_edit()  # the hand edit of ore intervals (12.17)


def _warm_tables(lines: Any, values: Any, rho: Any, codes: Any) -> None:
    """Composites by domains and by benches and the merging of tables (12.4–12.6): the overlay of two tables and
    the runs of equal codes (`intervals_kernels`), the benches along a path (`benches`), the columns of a merge
    (`merge._gather`) — through the functions of the jobs, with their dtypes."""
    import numpy as np

    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey

    from . import intervals as iv
    from .benches import BenchParams, composite_by_benches
    from .domains import composite_by_domains, domain_stretches
    from .length import LengthParams
    from .merge import merge_tables

    ends = np.array([5.0, np.nan, 8.0])
    kw: dict[str, Any] = {"density": rho, "by_density": True, "texts": [codes]}
    # a domain: a field of the samples themselves, then a table of its own (contacts inside the samples)
    other = iv.build_lines(np.array([0, 0, 2], dtype=np.int64), np.array([0.0, 2.5, 4.0]), np.array([2.5, 5.0, 9.0]))
    p = LengthParams.of({"length": 2.0})
    for table, of_table in ((lines, codes), (other, np.array([0, 1, -1], dtype=np.int32))):
        d = domain_stretches(lines, table, of_table, origin=p.origin, ends=ends)
        composite_by_domains(lines, values, p, d, **kw)
    # benches: a vertical hole, an inclined one with a station (the root of Z on a curved course), one without a path
    collars = Collars(np.arange(2), [0.0, 50.0], [0.0, 0.0], [100.0, 97.5], [5.0, 8.0], [0.0, 90.0], [-90.0, -60.0])
    surveys = Surveys(np.array([1, 1], dtype=np.int64), [0.0, 4.0], [90.0, 95.0], [-60.0, -20.0])
    traj = desurvey(collars, surveys, n_holes=2)
    dense = np.array([0, -1, 1], dtype=np.int64)
    for raw in ({"bench": 2.0}, {"bench": 2.0, "bench_top": 101.0}, {"bench_levels": [99.0, 97.0, 96.0, 90.0]}):
        composite_by_benches(lines, values, BenchParams.of(raw), traj, dense, ends=ends, **kw)
    # merging: the common boundaries and the columns laid on them (one column and a matrix)
    m = merge_tables([lines, other], names=["A", "B"])
    m.numbers(0, values)
    m.numbers(0, np.ascontiguousarray(values[:, 0]))
    m.codes(1, np.array([0, 1, -1], dtype=np.int32))


def _warm_prep(frm: Any, to: Any, au: Any, codes: Any) -> None:
    """The top cut (and its analysis) and the declustering of 12.21 (`jobs_prep`)."""
    import numpy as np

    from bf_core.stats import declus3d, weighted

    from .topcut import TopcutParams, topcut, topcut_analysis

    weighted.warm()
    declus3d.warm()
    ln = np.asarray(to) - np.asarray(frm)
    topcut(au, ln, TopcutParams(), codes=codes, names=["A", "B"], field="AU")
    topcut_analysis(au, ln, codes=codes, names=["A", "B"], field="AU")
