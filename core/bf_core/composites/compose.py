"""The computation of composites for the job `composite.run` and for the preview of one hole (ADR 0018 §6, решение
26): the same function on the same reading, so the preview of a hole is the job's rows of that hole.

    made = compute(spec, src, dom, ends, st, threads=…, tick=…) -> Made(out, report, journal)

`src` — the samples (`source.read`: the fields, the density, the domain field when it is in the same table);
`dom` — the lines of another interval table with the domain field, or None; `ends` — TD per hole of `src`;
`st` — the trajectories of the set (`source.trajectories`; X, Y, Z of the middles, benches); None — X, Y, Z
are empty (the summary of the set does not need them).

Methods: "length" — `length.composite_by_length` over the whole holes; "domain" — the stretches of constant
code (`domains.domain_stretches`: a field of the samples or of another table; a hole the domain table does not
have, or an empty code — stretches without a domain, DOMAIN empty) and composites by length inside each;
"bench" — `benches.composite_by_benches`, Z_верх by default — the highest collar of the set rounded up to H
(the same for the job and for the preview of one hole); FLAG «пологая» besides «мало проб».

Columns of the table (ТЗ 8.2.3): LENGTH, X, Y, Z, the chosen fields (units and roles of the source; a field
named as one of these columns gets «_SRC»), DENSITY, SAMPLED_LEN, N_SAMPLES, DOMAIN (text: the label, or the
number of a numeric domain field), FLAG. Missing values stay missing (NaN, None), never 0.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.db.schema import dh_holes

if TYPE_CHECKING:
    from . import sink, source
    from .jobs import RunSpec

OWN = ("LENGTH", "X", "Y", "Z", "DENSITY", "SAMPLED_LEN", "N_SAMPLES", "DOMAIN", "FLAG")


@dataclass
class Made:
    out: sink.OutTable
    report: dict[str, Any]
    journal: list[str]


def hole_depths(conn: sa.Connection, set_id: int, hole_db_ids: Any) -> np.ndarray:
    """TD of the holes (dh_holes.id, in the given order); NaN — not given."""
    td = {
        int(r.id): r.td
        for r in conn.execute(sa.select(dh_holes.c.id, dh_holes.c.td).where(dh_holes.c.set_id == set_id))
    }
    got = [td.get(int(h)) for h in np.asarray(hole_db_ids, dtype=np.int64).tolist()]
    return np.array([math.nan if v is None else float(v) for v in got], dtype=float)


def _dense(st: Any, hole_db_ids: np.ndarray) -> np.ndarray:
    """The index of every hole in the trajectories; −1 — none (no collar coordinates)."""
    if st is None:
        return np.full(len(hole_db_ids), -1, dtype=np.int64)
    tix = np.array([st.index.get(int(h), -1) for h in hole_db_ids.tolist()], dtype=np.int64)
    tix[np.isin(tix, np.asarray(st.traj.skipped, dtype=np.int64))] = -1
    return tix


def _midpoints(tix: np.ndarray, st: Any, hole_of: np.ndarray, depth: np.ndarray) -> np.ndarray:
    xyz = np.full((len(depth), 3), np.nan)
    rows = np.flatnonzero(tix[hole_of] >= 0) if len(hole_of) else np.zeros(0, dtype=np.int64)
    if len(rows):
        xyz[rows] = st.traj.positions(tix[hole_of[rows]], depth[rows])
    return xyz


def set_top(st: Any, height: float) -> float | None:
    """Z_верх by default: the highest collar of the set with a trajectory, rounded up to H; None — no collar."""
    from .benches import default_top

    traj = st.traj
    n = traj.n_holes
    if not n:
        return None
    built = traj.offsets[1:] > traj.offsets[:-1]
    if not built.any():
        return None
    return default_top(traj.xyz[traj.offsets[:-1][built], 2], height)


def _domain_codes(spec: RunSpec, src: source.Lines, dom: source.Lines | None, lines: Any) -> tuple[Any, Any, Any]:
    """(the lines of the domain rows, their codes, the DOMAIN text of a code) — see the module docstring."""
    from . import intervals as iv
    from .domains import number_codes

    d = spec.domain
    assert d is not None
    if dom is None:  # a field of the samples
        table, carrier = lines, src
    else:  # another table: its holes in the index of the samples (holes the samples do not have — left out)
        rank = {name: i for i, name in enumerate(src.holes)}
        h = np.array([rank.get(name, -1) for name in dom.holes], dtype=np.int64)[dom.hole_of()]
        keep = h >= 0
        table = iv.build_lines(h[keep], dom.from_m[keep], dom.to_m[keep])
        carrier = dom
        lines_of = np.flatnonzero(keep)
    if d.kind == "text":
        codes = carrier.codes[d.field]
        labels = np.array([*carrier.labels[d.field], None], dtype=object)
    else:
        codes, uniques = number_codes(carrier.numbers[d.field])
        labels = np.array([*(f"{u:g}" for u in uniques.tolist()), None], dtype=object)
    codes = table.take(codes if dom is None else codes[lines_of])
    return table, codes, labels


def compute(
    spec: RunSpec,
    src: source.Lines,
    dom: source.Lines | None,
    ends: Any,
    st: Any,
    *,
    threads: int = 1,
    tick: Callable[[float], None] | None = None,
) -> Made:
    """Composites of the lines `src` (all the holes of the table, or one for the preview) by `spec.method`."""
    from bf_core.drillholes.store import ColumnSpec

    from . import intervals as iv
    from . import sink
    from .jobs import as_api
    from .length import FLAG_LOW, flag_column, summary

    lines = iv.build_lines(src.hole_of().astype(np.int64), src.from_m, src.to_m)
    values = iv.stack_fields(lines, [src.numbers[n] for n in spec.numbers])
    density = lines.take(src.numbers[spec.density]) if spec.density else None
    texts = [lines.take(src.codes[n]) for n in spec.texts]
    depth = np.asarray(ends, dtype=float)[lines.holes] if lines.n_holes else np.zeros(0)
    tix = _dense(st, src.hole_db_ids)
    kw = {"density": density, "by_density": density is not None, "texts": texts, "threads": threads, "tick": tick}
    domain = None
    marks: list[tuple[str, np.ndarray]] = []
    kernel = spec.kernel
    try:
        if spec.method == "bench":
            from dataclasses import replace

            from .benches import FLAG_FLAT, composite_by_benches

            if kernel.bench_top is None and kernel.bench_levels is None and st is not None:
                kernel = replace(kernel, bench_top=set_top(st, kernel.bench))
            if st is None:
                raise ValueError("benches need the trajectories")
            b = composite_by_benches(lines, values, kernel, st.traj, tix[lines.holes], ends=depth, **kw)  # type: ignore[arg-type]
            c: Any = b
            xyz = b.xyz
            marks.append((FLAG_FLAT, b.flat))
        elif spec.method == "domain":
            from .domains import composite_by_domains, domain_stretches

            table, codes, labels = _domain_codes(spec, src, dom, lines)
            d = domain_stretches(lines, table, codes, origin=kernel.origin, ends=depth, threads=threads)
            c = composite_by_domains(lines, values, kernel, d, **kw)  # type: ignore[arg-type]
            domain = labels[c.domain]  # −1 → None
        else:
            from .length import composite_by_length

            c = composite_by_length(lines, values, kernel, ends=depth, **kw)  # type: ignore[arg-type]
    except ValueError as exc:  # CompositeError → 422
        raise as_api(exc) from None
    cl = c.lines
    hole = cl.hole_ids().astype(np.int64)  # dense index of src.holes
    n = cl.n
    if spec.method != "bench":
        xyz = _midpoints(tix, st, hole, (cl.frm + cl.to) / 2.0)
    no_traj = int(np.count_nonzero(tix < 0))
    taken = {o.casefold() for o in OWN}

    def own(name: str) -> str:
        return f"{name}_SRC" if name.casefold() in taken else name

    cols = [ColumnSpec("LENGTH", "number", "м"), ColumnSpec("X", "number", "м"), ColumnSpec("Y", "number", "м"),
            ColumnSpec("Z", "number", "м")]  # fmt: skip
    vals: list[np.ndarray] = [cl.to - cl.frm, xyz[:, 0], xyz[:, 1], xyz[:, 2]]
    for k, name in enumerate(spec.numbers):
        role = src.fields[name].role if src.fields[name].role in ("component", "value") else "value"
        cols.append(ColumnSpec(own(name), "number", spec.units.get(name), role))
        vals.append(np.ascontiguousarray(c.values.values[:, k]))
    for k, name in enumerate(spec.texts):
        labels_k = np.array([*src.labels[name], None], dtype=object)
        cols.append(ColumnSpec(own(name), "text"))
        vals.append(labels_k[c.values.texts[k]])  # −1 → the last one: None
    cols += [ColumnSpec("DENSITY", "number", spec.units.get(spec.density) if spec.density else None),
             ColumnSpec("SAMPLED_LEN", "number", "м"), ColumnSpec("N_SAMPLES", "number"),
             ColumnSpec("DOMAIN", "text"), ColumnSpec("FLAG", "text")]  # fmt: skip
    vals += [c.values.density, c.values.sampled, c.values.count.astype(float),
             np.full(n, None, dtype=object) if domain is None else domain,
             flag_column([(FLAG_LOW, c.values.low), *marks], n)]  # fmt: skip
    out = sink.OutTable(spec.name, [src.holes[i] for i in hole.tolist()], cl.frm, cl.to, cols, vals)
    journal = list(lines.notes(spec.table_name)) + list(c.journal)
    if src.skipped:
        journal.insert(0, f"Таблица «{spec.table_name}»: строк без «от» или «до» — {src.skipped}, в расчёт не вошли.")
    if no_traj and n and st is not None and spec.method != "bench":
        journal.append(f"Скважин без траектории — {no_traj}: X, Y, Z композитов пусты.")
    report = {
        **c.report,
        "kernel": asdict(kernel),
        "rows_in": src.rows,
        "skipped": src.skipped,
        "no_trajectory": no_traj,
        "summary": {
            "before": summary(lines.lengths(), values, spec.numbers),
            "after": summary(cl.to - cl.frm, c.values.values, spec.numbers),
        },
        "journal": journal,
        "edits": [],
    }
    return Made(out, report, journal)
