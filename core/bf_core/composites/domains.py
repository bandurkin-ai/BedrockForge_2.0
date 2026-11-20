"""Composites by domains (ТЗ 8.2.2, ADR 0018 §6): a composite does not cross a change of the domain code.

    d = domain_stretches(samples, table, codes, origin="domain", ends=depths, threads=…, tick=…) -> Domains
        d.lines — the stretches [a; b] of every hole of the samples; d.code — int32 [n], −1 — no domain
    c = composite_by_domains(samples, values, params, d, density=rho, by_density=True, texts=[…],
                             threads=…, tick=…) -> DomainComposites
        the `Composites` of `length.py` (lines, stretch, values, report, journal) + c.domain (int32 [n])

Where the code comes from — `table` are interval rows of the set (`intervals.build_lines`), `codes` — int32 in
their line order (text — `source.text_codes`, numbers — `number_codes`; empty — −1):
    a field of the samples themselves     domain_stretches(samples, samples, samples.take(codes))
    a field of another interval table     domain_stretches(samples, litho, litho.take(codes))
    ready stretches (inside solids, 12.7) domain_stretches(samples, stretches, code_of_every_stretch)

Stretches. The hole is taken as composites by length take it (`length.hole_stretches`): [начало; забой] — from
the collar (origin "first" — from the top of the first sample) to the depth of the hole (`ends`) or the bottom
of the last sample. It is cut at every depth where the code of the covering row of `table` changes
(`intervals.overlay` of the hole with the rows, then `intervals.runs` by the code): consecutive rows with one
code are one stretch, whatever their own boundaries. A part of the hole the table does not cover and a row
with an empty code have no domain: they make stretches of code −1, apart from their neighbours — the samples
there are composited too, DOMAIN stays empty, and the journal counts such stretches. A hole of the samples
the table does not have is one stretch without a domain; a hole without samples gets nothing.

Composites — `length.composite_by_length` over the stretches: every stretch [a; b] is cut on its own by the
rule of ТЗ 8.2.2 («равные», «остаток», f_min, the grid from the stretch, the collar or the first sample), so
a composite lies inside one stretch and DOMAIN is the code of that stretch. Boundaries of the table nearer
than `DEPTH_EPS` (1e−6 m) are one boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import intervals as iv
from . import length as cl
from .strips import Tick


@dataclass(frozen=True)
class Domains:
    lines: iv.Lines  # the stretches [a; b], ordered by (hole, a); they tile the holes of the samples
    code: np.ndarray  # int32 [n]: the domain of every stretch, −1 — none
    n_empty: int  # stretches without a domain
    len_empty: float  # … their total length, m


@dataclass(frozen=True)
class DomainComposites(cl.Composites):
    domain: np.ndarray  # int32 [n]: the code of the stretch of every composite (→ DOMAIN), −1 — none


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    return None if tick is None else (lambda f: tick(lo + (hi - lo) * f))


def domain_stretches(
    samples: iv.Lines,
    table: iv.Lines,
    codes: Any,
    *,
    origin: str = "domain",
    ends: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> Domains:
    """The stretches of constant domain code of every hole of the samples (module docstring). `table` — the
    rows that carry the code (the samples themselves, another interval table, ready stretches); `codes` —
    int32 in the line order of `table`, −1 — empty; `origin`, `ends` (per hole of the samples) — as
    `length.hole_stretches`. The result does not depend on `threads`."""
    code = np.ascontiguousarray(codes, dtype=np.int32)
    if code.shape[0] != table.n:
        raise ValueError("codes are not in the line order of the table")
    holes = cl.hole_stretches(samples, origin, ends)
    ov = iv.overlay(holes, table, "left", threads=threads, tick=_part(tick, 0.0, 0.6))
    sub = np.full(ov.lines.n, -1, dtype=np.int32)
    have = ov.ib >= 0
    sub[have] = code[ov.ib[have]]
    r = iv.runs(ov.lines, sub, threads=threads, tick=_part(tick, 0.6, 1.0))
    none = r.code < 0
    return Domains(r.lines, r.code, int(np.count_nonzero(none)), float(r.lines.lengths()[none].sum()))


def composite_by_domains(
    lines: iv.Lines,
    values: Any,
    params: cl.LengthParams,
    domains: Domains,
    *,
    density: Any = None,
    by_density: bool = True,
    texts: Sequence[Any] = (),
    threads: int = 1,
    tick: Tick | None = None,
) -> DomainComposites:
    """Composites by length inside the stretches of `domains` — arguments as `length.composite_by_length`.
    `report` gets `domains` (codes met), `no_domain`, `no_domain_len`; the journal — the stretches without a
    domain."""
    c = cl.composite_by_length(
        lines,
        values,
        params,
        density=density,
        by_density=by_density,
        texts=texts,
        stretches=domains.lines,
        threads=threads,
        tick=tick,
    )
    coded = domains.code[domains.code >= 0]
    report = {
        **c.report,
        "domains": int(np.unique(coded).shape[0]),
        "no_domain": domains.n_empty,
        "no_domain_len": domains.len_empty,
    }
    journal = list(c.journal)
    if domains.n_empty:
        length = f"{domains.len_empty:.2f}".replace(".", ",")
        journal.insert(0, f"Участков без домена — {domains.n_empty} (всего {length} м): у их композитов DOMAIN пуст.")
    return DomainComposites(c.lines, c.stretch, c.values, report, journal, domains.code[c.stretch])


# --- codes and the column DOMAIN -----------------------------------------------------------------------------


def number_codes(values: Any) -> tuple[np.ndarray, np.ndarray]:
    """int32 codes of a numeric domain field into its distinct values, ascending; NaN — −1 (no domain)."""
    import pandas as pd

    codes, uniques = pd.factorize(np.asarray(values, dtype=np.float64), sort=True, use_na_sentinel=True)
    return codes.astype(np.int32), np.asarray(uniques, dtype=np.float64)


def domain_text(code: np.ndarray, labels: Sequence[str]) -> np.ndarray:
    """DOMAIN of a text field: the label of every code; None — no domain."""
    out = np.full(code.shape[0], None, dtype=object)
    have = code >= 0
    out[have] = np.asarray(labels, dtype=object)[code[have]]
    return out


def domain_numbers(code: np.ndarray, uniques: Any) -> np.ndarray:
    """DOMAIN of a numeric field (`number_codes`): the value of every code; NaN — no domain."""
    out = np.full(code.shape[0], np.nan, dtype=np.float64)
    have = code >= 0
    out[have] = np.asarray(uniques, dtype=np.float64)[code[have]]
    return out
