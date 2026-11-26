"""What the scenarios `resources_*` share (ADR 0027 «Замеры», подшаг 20.11; ТЗ 8.10 «Время»).

    prepare_model(ctx, rows, dz=5.0, nz=200)   a regular model of `rows` blocks: AU, CU, AG (3 % and 5 % gaps), DENS,
                                               the categories DOM (4) and GEO (5); the blocks are 10 × 10 × `dz` m
    reference(model, cutoffs, ...)             the sums of the report by numpy over the portions (an independent
                                               code: no kernel of `resources`): per group and cut-off bin
    Ref.rows(...)                              the sums of a row: the blocks, the volume, the tonnes, the elements

The scenarios run the body of the job `resources.report` (`job.run_report`, a preview: nothing is written) — the time
of ТЗ is «от запроса до ответа при готовой модели»: the read of the model and the rules, the pass, the control, the rows
and the curve. The answer is checked against `reference`: the blocks exactly, the volume, the tonnes, the grades and
the metal-sums 1e-9 (the sums of the report are Neumaier's, in the order of the rows; numpy's are pairwise).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import blocks_common as bc

ELEMENTS = ("AU", "CU", "AG")
DOM = ("A", "B", "C", "D")
GEO = ("g1", "g2", "g3", "g4", "g5")
EMPTY = "(пусто)"
CUTS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]  # the 10 cut-offs of the report (g/t of AU)
CU_MIN = 0.3  # the restrictor «CU > 0,3» — an expression, so the share f is 0 or 1
RESTRICT = {"cond": {"field": "CU", "op": ">", "value": CU_MIN}}
SIZE_XY = bc.SIZE[:2]
CORNER_Z = bc.CORNER[2]
SLIVER = 1e-12
REL = 1e-9


def fields(with_status: bool = False) -> list[dict[str, Any]]:
    """`with_status` — the fields the control makes its second pass for: `DENS_SRC` (the density of the domain by
    default) and `STATUS` of an estimate (`prepare_model` marks AU, CU, AG and STATUS as the fields of an estimate)."""
    out = [
        {"name": "AU", "dtype": "f8", "unit": "g/t"},
        {"name": "CU", "dtype": "f4", "unit": "%"},
        {"name": "AG", "dtype": "f8", "unit": "g/t"},
        {"name": "DENS", "dtype": "f8", "unit": "т/м³"},
        {"name": "DOM", "dtype": "cat", "labels": list(DOM)},
        {"name": "GEO", "dtype": "cat", "labels": list(GEO)},
    ]
    if with_status:
        out += [{"name": "DENS_SRC", "dtype": "i1"}, {"name": "STATUS", "dtype": "i1"}]
    return out


def values(rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
    au = rng.lognormal(0.0, 1.0, n)
    au[rng.random(n) < 0.03] = np.nan  # 3 % of the blocks have no AU: a gap is not a number
    ag = rng.lognormal(1.0, 0.8, n)
    ag[rng.random(n) < 0.05] = np.nan
    return {"AU": au, "CU": rng.random(n).astype(np.float32), "AG": ag, "DENS": rng.normal(2.7, 0.1, n),
            "DOM": rng.integers(-1, 4, n).astype(np.int32), "GEO": rng.integers(-1, 5, n).astype(np.int32)}  # fmt: skip


def status_values(rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
    """DENS_SRC 0 / 1 (a half) and STATUS 0 (none) … 3 (the draws come after those of `values`: the same grades)."""
    return {"DENS_SRC": rng.integers(0, 2, n).astype(np.int8), "STATUS": rng.integers(0, 4, n).astype(np.int8)}


def mark_estimates(ctx: bc.Ctx, model_id: int) -> None:
    """AU, CU, AG and STATUS are the fields an estimate wrote (`source.op = estimate`): the control then counts the
    blocks of each status (Д-27) in its second pass."""
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import objects

    est = {"op": "estimate", "params": {}, "inputs": [], "algo": "estimate-1"}
    with dialect.write_tx(ctx.engine) as conn:
        meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == model_id)).scalar_one())
        meta["fields"] = [{**f, "source": est} if f["name"] in (*ELEMENTS, "STATUS") else f for f in meta["fields"]]
        conn.execute(sa.update(objects).where(objects.c.id == model_id).values(meta=meta))


def prepare_model(
    ctx: bc.Ctx, rows: int, *, dz: float = 5.0, nz: int = 200, with_status: bool = False
) -> dict[str, Any]:  # fmt: skip
    """The model of the scenario written into the project; `ctx.keep` is the caller's. → {model_id, rows, counts}.
    `with_status` adds `DENS_SRC` and `STATUS` of an estimate: the control makes its second pass over the model."""
    size = [bc.SIZE[0], bc.SIZE[1], float(dz)]
    make = values if not with_status else lambda rng, n: {**values(rng, n), **status_values(rng, n)}
    out = bc.regular_model(ctx, rows, fields(with_status), make, nz=nz, size=size)
    if with_status:
        mark_estimates(ctx, int(out["model_id"]))
    return out


def volume_of(dz: float) -> float:
    return float(bc.SIZE[0] * bc.SIZE[1] * dz)


# --- the reference ---------------------------------------------------------------------------------------------------


@dataclass
class Ref:
    """Sums per (group, bin) — `n`, `t` (tonnes), `v` (volume), per element `wt`, `sum`, `miss` (blocks with no grade).
    The group `total` is the whole block once (the last row of every array)."""

    edges: np.ndarray
    groups: int
    elements: tuple[str, ...]
    n: np.ndarray = field(init=False)
    v: np.ndarray = field(init=False)
    t: np.ndarray = field(init=False)
    wt: dict[str, np.ndarray] = field(init=False)
    sums: dict[str, np.ndarray] = field(init=False)
    miss: dict[str, np.ndarray] = field(init=False)

    def __post_init__(self) -> None:
        shape = (self.groups + 1) * (len(self.edges) + 2)
        self.n = np.zeros(shape, dtype=np.int64)
        self.v, self.t = np.zeros(shape), np.zeros(shape)
        self.wt = {e: np.zeros(shape) for e in self.elements}
        self.sums = {e: np.zeros(shape) for e in self.elements}
        self.miss = {e: np.zeros(shape, dtype=np.int64) for e in self.elements}

    @property
    def total(self) -> int:
        return self.groups

    def _view(self, x: np.ndarray) -> np.ndarray:
        return x.reshape(self.groups + 1, len(self.edges) + 2)

    def row(self, group: int, cutoff: float | None) -> dict[str, Any]:
        """The sums of the blocks of the group with AU ≥ cutoff (None — every counted block, the gaps of AU too)."""
        lo, hi = (0, len(self.edges) + 2) if cutoff is None else (int(np.searchsorted(self.edges, cutoff)) + 1,
                                                                 len(self.edges) + 1)  # fmt: skip

        def pick(x: np.ndarray) -> Any:
            return self._view(x)[group, lo:hi].sum()

        out: dict[str, Any] = {"blocks": int(pick(self.n)), "volume": float(pick(self.v)),
                               "tonnes": float(pick(self.t)), "elements": {}}  # fmt: skip
        for e in self.elements:
            wt, s = float(pick(self.wt[e])), float(pick(self.sums[e]))
            out["elements"][e] = {"wt": wt, "sum": s, "grade": s / wt if wt > 0 else None,
                                  "missing_n": int(pick(self.miss[e]))}  # fmt: skip
        return out

    def present(self) -> list[int]:
        """The groups with a counted block (any bin): they have rows."""
        return [g for g in range(self.groups) if self._view(self.n)[g].sum() > 0]

    def curve(self, cutoff: float) -> tuple[int, float, float, float]:
        """{AU ≥ cutoff} of the total: blocks, volume, tonnes, ḡ of AU."""
        r = self.row(self.total, cutoff)
        return r["blocks"], r["volume"], r["tonnes"], r["elements"]["AU"]["grade"]


Shares = Callable[[Any], list[tuple[np.ndarray, np.ndarray]]]


def reference(
    model: Any, edges: Sequence[float], groups: int, shares: Shares, *, vol: float, restrict: bool,
    elements: Sequence[str] = ELEMENTS,
) -> Ref:  # fmt: skip
    """The sums of the model by numpy. `shares(chunk)` → [(code of the group per row (−1: none), share of the row in
    it)] — one pair for a plain key, up to three for the benches. The bins of the cut-off AU are
    `searchsorted(edges, AU, "right")` (a block with no AU: the last)."""
    edges_a = np.array(sorted({float(x) for x in edges}), dtype=np.float64)
    elems = tuple(elements)
    ref = Ref(edges_a, groups, elems)
    nb = len(edges_a) + 2
    size = (groups + 1) * nb
    names = list(dict.fromkeys([*elems, "AU", "CU", "DENS", "DOM", "GEO"]))
    for ch in model.iter(names, bc.PORTION):
        cols = {k: v if k in ("DOM", "GEO") else np.asarray(v, dtype=np.float64) for k, v in ch.cols.items()}
        dens = cols["DENS"]
        ok = np.isfinite(dens) & (dens >= 0.0)
        if restrict:
            ok &= cols["CU"] > CU_MIN
        t = vol * dens
        b = np.searchsorted(edges_a, cols["AU"], side="right")
        b[np.isnan(cols["AU"])] = len(edges_a) + 1
        grade_ok = {e: np.isfinite(cols[e]) for e in elems}
        whole = (np.full(ch.n, groups, dtype=np.int64), np.ones(ch.n))
        for code, share in [*shares(ch), whole]:
            sel = ok & (code >= 0) & (share > 0.0)
            idx = code[sel] * nb + b[sel]
            w = t[sel] * share[sel]
            ref.n += np.bincount(idx, minlength=size)
            ref.v += np.bincount(idx, vol * share[sel], minlength=size)
            ref.t += np.bincount(idx, w, minlength=size)
            for e in elems:
                has = grade_ok[e][sel]
                ref.wt[e] += np.bincount(idx[has], w[has], minlength=size)
                ref.sums[e] += np.bincount(idx[has], (w * cols[e][sel])[has], minlength=size)
                ref.miss[e] += np.bincount(idx[~has], minlength=size)
    return ref


def close(a: float | None, b: float | None, rel: float = REL) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


def same_row(got: Mapping[str, Any], want: Mapping[str, Any], where: Any) -> None:
    """A row of the answer against a row of the reference: the blocks and the counts of the gaps exactly, the rest 1e-9
    (the grade of a row with no tonnes with a grade is None, not 0)."""
    assert got["blocks"] == want["blocks"], (where, got["blocks"], want["blocks"])
    assert close(got["volume"], want["volume"]) and close(got["tonnes"], want["tonnes"]), (where, got, want)
    for e in want["elements"]:
        g, w = got["elements"][e], want["elements"][e]
        assert close(g["grade"], w["grade"]), (where, e, g["grade"], w["grade"])
        assert close(g["wt"], w["wt"]) and close(g["sum"], w["sum"]), (where, e, g, w)
        assert g["missing_n"] == w["missing_n"], (where, e, g["missing_n"], w["missing_n"])


def request(model_id: int, **over: Any) -> dict[str, Any]:
    """The body of `resources.report`: a preview (nothing is saved); `over` — the parameters of the report."""
    body = {"model_id": model_id, "elements": [{"field": e} for e in ELEMENTS], "cutoff_field": "AU",
            "density": "DENS", "cutoffs": CUTS, "groups": [], "curve": 0, **over}  # fmt: skip
    return {"params": body, "save": False}


def check_rows(ans: Mapping[str, Any], ref: Ref, cutoffs: Sequence[float], labels: Callable[[int], list[str]],
               nkeys: int) -> int:  # fmt: skip
    """Every row of the table against the reference: the total of each cut-off and each present group (`labels(code)` —
    the labels of its keys); no row more, none less. → the number of the rows."""
    rows = {(r["cutoff"], tuple(r["g"])): r for r in ans["rows"] if r["kind"] == "cutoff"}
    present = ref.present()
    want = len(cutoffs) * (1 + len(present))
    assert ans["rows_total"] == want == len(ans["rows"]) and not ans["truncated"], (ans["rows_total"], want)
    for c in cutoffs:
        same_row(rows[(c, (None,) * nkeys)], ref.row(ref.total, c), ("total", c))
        for g in present:
            same_row(rows[(c, tuple(labels(g)))], ref.row(g, c), (labels(g), c))
    return want


def check_curve(ans: Mapping[str, Any], ref: Ref, points: int) -> None:
    """The curve «tonnage — grade» (`points` cut-offs): per point the blocks exactly, the volume, the tonnes, ḡ(AU)."""
    cv = ans["curve"]
    assert cv is not None and len(cv["cutoffs"]) == points, None if cv is None else len(cv["cutoffs"])
    grades = cv["elements"]["AU"]["grade"]
    for i, c in enumerate(cv["cutoffs"]):
        n, v, t, g = ref.curve(c)
        assert cv["blocks"][i] == n, (c, cv["blocks"][i], n)
        assert close(cv["volume"][i], v) and close(cv["tonnes"][i], t) and close(grades[i], g), (c, i)


def same_bits(a: Any, b: Any) -> bool:
    """The answers of two runs agree bit by bit (the rows, the curve, the control — not the seconds)."""
    import json

    def clean(x: Mapping[str, Any]) -> str:
        return json.dumps({k: v for k, v in x.items() if k != "seconds"}, sort_keys=True, default=str)

    return clean(a) == clean(b)


def run_job(ctx: bc.Ctx, body: dict[str, Any]) -> tuple[dict[str, Any], float]:
    import time

    from bf_core.resources import job

    t0 = time.perf_counter()
    out = job.run_report(ctx.engine, ctx.folder, body)
    return out, time.perf_counter() - t0


def load() -> None:
    """The kernels compiled or loaded from NUMBA_CACHE_DIR (`load` of a scenario, not timed): the steps `blocks` and
    `resources` of the warm-up of the workers, what `ops/warm.py` runs."""
    bc.warm()
    from bf_core.resources.warm import warm

    warm()
