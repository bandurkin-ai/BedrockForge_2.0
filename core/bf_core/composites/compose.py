"""The computation of composites for the job `composite.run` and for the preview of one hole (ADR 0018 §6, решение
26): the same function on the same reading, so the preview of a hole is the job's rows of that hole.

    made = compute(spec, src, dom, ends, st, threads=…, tick=…) -> Made(out, report, journal)

`src` — the samples (`source.read`: the fields, the density, the domain field when it is in the same table);
`dom` — the lines of another interval table with the domain field, or None; `ends` — TD per hole of `src`;
`st` — the trajectories of the set (`source.trajectories`; X, Y, Z of the middles, benches); None — X, Y, Z
are empty (the summary of the set does not need them).

Methods: "length" — `length.composite_by_length` over the whole holes; "domain" — the stretches of constant
code (`domains.domain_stretches`: a field of the samples or of another table; a hole the domain table does not
have, or an empty code — stretches without a domain, DOMAIN empty) and composites by length inside each; by
wireframes — the stretches inside the bodies (`coding.body_stretches`, the geometry through the project of
`jobs_coding.reading`; `st` None — read here), DOMAIN — the name of the body, empty outside them;
"bench" — `benches.composite_by_benches`, Z_верх by default — the highest collar of the set rounded up to H
(the same for the job and for the preview of one hole); FLAG «пологая» besides «мало проб».

Columns of the table (ТЗ 8.2.3): LENGTH, X, Y, Z, the chosen fields (units and roles of the source; a field
named as one of these columns gets «_SRC»), DENSITY, SAMPLED_LEN, N_SAMPLES, DOMAIN (the label of a text domain field,
the number of a numeric one; none — empty), FLAG. Missing values stay missing (NaN, None), never 0.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, cast

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
    """TD of the holes (dh_holes.id, in the given order); NaN — not given. A few holes (the preview of one) are
    asked for by their ids; the whole set is read once."""
    ids = np.asarray(hole_db_ids, dtype=np.int64).tolist()
    q = sa.select(dh_holes.c.id, dh_holes.c.td).where(dh_holes.c.set_id == set_id)
    if len(ids) <= 200:
        q = q.where(dh_holes.c.id.in_(ids))
    td = {int(r.id): r.td for r in conn.execute(q)}
    got = [td.get(h) for h in ids]
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
    lines_of = None
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
        labels: Any = np.array([*carrier.labels[d.field], None], dtype=object)
    else:
        codes, labels = number_codes(carrier.numbers[d.field])
    codes = table.take(codes if dom is None else codes[lines_of])
    return table, codes, labels


def _wireframe_codes(spec: RunSpec, lines: Any, st: Any, tix: np.ndarray, threads: int) -> tuple[Any, ...]:
    """(the stretches inside the bodies, their codes, the DOMAIN text of a code, lines for the journal, report)."""
    from . import coding
    from .jobs_coding import read_bodies

    wf = spec.domain.wireframes  # type: ignore[union-attr]
    s = coding.body_stretches(lines, st.traj, tix[lines.holes], read_bodies(wf), n_bodies=len(wf.targets),
                              threads=threads)  # fmt: skip
    return s.lines, s.code, np.array([*s.names, None], dtype=object), s.journal, s.report


def compute(
    spec: RunSpec,
    src: source.Lines,
    dom: source.Lines | None,
    ends: Any,
    st: Any,
    *,
    threads: int = 1,
    tick: Callable[[float], None] | None = None,
    release: bool = False,
) -> Made:
    """Composites of the lines `src` (all the holes of the table, or one for the preview) by `spec.method`.
    `release` (the job: nothing reads `src` after it) — the number columns of `src` are dropped once they are
    laid into the matrix of the kernel, and the matrix once the kernel has run: 5 million samples of 10 fields
    stay under 2 ГБ (ТЗ 8.2.3). The summary «до» is counted before the kernel for that."""
    from bf_core.drillholes.store import ColumnSpec

    from . import intervals as iv
    from . import sink
    from .jobs import as_api
    from .length import FLAG_LOW, flag_column, summary

    lines = iv.build_lines(src.hole_of().astype(np.int64), src.from_m, src.to_m)
    values = iv.stack_fields(lines, [src.numbers[n] for n in spec.numbers])
    density = lines.take(src.numbers[spec.density]) if spec.density else None
    before = summary(lines.lengths(), values, spec.numbers)
    rows_in, skipped = src.rows, src.skipped

    def free() -> None:
        """Before the kernel: the columns it gets are in `values`, the domain codes are made."""
        if release:
            for name in list(src.numbers):
                del src.numbers[name]
    texts = [lines.take(src.codes[n]) for n in spec.texts]
    depth = np.asarray(ends, dtype=float)[lines.holes] if lines.n_holes else np.zeros(0)
    by_wf = spec.method == "domain" and spec.domain is not None and spec.domain.wireframes is not None
    if by_wf and st is None:  # the summary of the set: the bodies need the paths
        from .jobs_coding import reading_project
        from .source import trajectories

        st = trajectories(reading_project(), spec.set_id)
    tix = _dense(st, src.hole_db_ids)
    kw = {"density": density, "by_density": density is not None, "texts": texts, "threads": threads, "tick": tick}
    domain = None
    marks: list[tuple[str, np.ndarray]] = []
    notes: list[str] = []
    extra: dict[str, Any] = {}
    kernel = spec.kernel
    try:
        if spec.method == "bench":
            from dataclasses import replace

            from .benches import FLAG_FLAT, composite_by_benches

            if kernel.bench_top is None and kernel.bench_levels is None and st is not None:
                kernel = replace(kernel, bench_top=set_top(st, kernel.bench))
            if st is None:
                raise ValueError("benches need the trajectories")
            free()
            b = composite_by_benches(lines, values, kernel, st.traj, tix[lines.holes], ends=depth, **kw)  # type: ignore[arg-type]
            c: Any = b
            xyz = b.xyz
            marks.append((FLAG_FLAT, b.flat))
        elif spec.method == "domain":
            from .domains import composite_by_domains, domain_numbers, domain_stretches, domain_text

            if by_wf:
                table, codes, labels, notes, extra["coding"] = _wireframe_codes(spec, lines, st, tix, threads)
            else:
                table, codes, labels = _domain_codes(spec, src, dom, lines)
            free()
            d = domain_stretches(lines, table, codes, origin=kernel.origin, ends=depth, threads=threads)
            c = composite_by_domains(lines, values, kernel, d, **kw)  # type: ignore[arg-type]
            text = spec.domain is not None and spec.domain.kind in ("text", "wireframes")
            domain = domain_text(c.domain, labels[:-1]) if text else domain_numbers(c.domain, labels)
        else:
            from .length import composite_by_length

            free()
            c = composite_by_length(lines, values, kernel, ends=depth, **kw)  # type: ignore[arg-type]
    except ValueError as exc:  # CompositeError → 422
        raise as_api(exc) from None
    del values, density, kw
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
             ColumnSpec("DOMAIN", "number" if domain is not None and domain.dtype.kind == "f" else "text"),
             ColumnSpec("FLAG", "text")]  # fmt: skip
    vals += [c.values.density, c.values.sampled, c.values.count.astype(float),
             np.full(n, None, dtype=object) if domain is None else domain,
             flag_column([(FLAG_LOW, c.values.low), *marks], n)]  # fmt: skip
    holes = np.array(src.holes, dtype=object)[hole] if n else np.zeros(0, dtype=object)
    out = sink.OutTable(spec.name, cast("Sequence[str]", holes), cl.frm, cl.to, cols, vals)
    journal = list(lines.notes(spec.table_name)) + notes + list(c.journal)
    if skipped:
        journal.insert(0, f"Таблица «{spec.table_name}»: строк без «от» или «до» — {skipped}, в расчёт не вошли.")
    if no_traj and n and st is not None and spec.method != "bench":
        journal.append(f"Скважин без траектории — {no_traj}: X, Y, Z композитов пусты.")
    report = {
        **c.report,
        **extra,
        "kernel": asdict(kernel),
        "rows_in": rows_in,
        "skipped": skipped,
        "no_trajectory": no_traj,
        "summary": {
            "before": before,
            "after": summary(cl.to - cl.frm, c.values.values, spec.numbers),
        },
        "journal": journal,
        "edits": [],
    }
    return Made(out, report, journal)
