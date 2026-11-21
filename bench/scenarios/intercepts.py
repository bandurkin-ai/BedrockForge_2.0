"""Ore intercepts by the rule 8.5.3 — the whole job `intercept.run` (ADR 0021 «Замеры», решения 10, 20, 21; ТЗ
8.5.4: «рудные пересечения, 50 тыс. скважин, 5 млн проб — ≤ 20 с»; ci — 5 тыс., 500 тыс., ориентир ≤ 4 с).

The set of `_samples`; the grade — AU of «Пробы» (г/т): heavy-tailed, with nuggets, ties at the detection limit,
1 % of the samples and whole runs of 20 without a value («пустая порода с g = 0», their length in MISS_LEN),
gaps between the samples, holes without a collar (no trajectory: TT is empty, the candidates are accepted by
their length). The hard variant of the rule: the cut-off c `cutoff` near the median — ore and waste alternate
every few samples, many short series to join through waste of ≤ L_п `waste_max`; acceptance by the true
thickness (`min_by` "tt": TT of every candidate ≥ m_min `l_min`) under the chain «по домену → глобальная» of
`true_thickness` (the plane of a candidate — of its rock by length); «в том числе» by c2 `include_cutoff`; the
means by the density DENSITY (3 % of the samples have none); the other 7 grades averaged into the table. The job
is the function its handler calls — `jobs_run.run_intercepts`: the request, the samples read from the snapshot
(решение 21) while the trajectories are built, SHA-256 of the inputs, the rule with the true thickness, the
table «Рудные пересечения» built aside, one final transaction with the table, its display by the hole and the
record of the result:
  icpt_s          the job from its start to the end of the final transaction — what the target of ТЗ counts;
  icpt_read_s, icpt_traj_s, icpt_hash_s, icpt_kernel_s, icpt_write_s   the stages the job reports itself
                  (`job.result.stages`: read, trajectories, hash, kernel, write);
  icpt_rule_s     the rule alone: `rule.run` less the true thickness of its candidates (ADR: ≤ 2 с);
  icpt_cand_tt_s  TT of the candidates (the acceptance);  icpt_tt_s — `tt.compute` of the rows of the table;
  icpt_build_s    the columns: the means of the other fields, the middles, LEN_G, the labels, the summary;
  icpt_table_s    the rows written (`sink.build_table`);  icpt_commit_s — the final transaction;
  icpt_display_s  the display «Рудные пересечения» inside it;  icpt_snapshot_s — the snapshot, after it;
  icpt_rows, icpt_main, icpt_included   rows of the table, main intercepts, «в том числе»;
  icpt_mb         the peak of the process at the end of the job (VmHWM);  icpt_tick_gap_s — the longest wait
                  between two ticks.
After the job (not in `icpt_s`):
  hashes          different SHA-256 among the job itself and `jobs_run.compute` on the same samples with 1, 2, 4
                  and 8 threads (every column of the table and the summary): 1 (`limits`);  hash — its first 16
                  digits;  kernel_t1_s … kernel_t8_s — that computation;
  balance_len_g   |Σ LEN_G of the main rows − the reference| over the reference (`limits`: 1e−9). The reference
                  — from the arrays of the generator and not by the kernels: for every main row the samples and
                  the gaps inside [FROM; TO], LENGTH = Σ l, g = Σ l·ρ·g / Σ l·ρ (no grade — 0; no density — the
                  mean one of the row), LEN_G = LENGTH·g;  grade_error — the largest relative difference of a
                  row;  balance_len, balance_miss — the same for Σ LENGTH and Σ MISS_LEN;
  tt_error_m      the largest |TT − reference| of the rows, metres (`limits`: 1e−9): the parts of ≤ 1 m of the
                  very [FROM; TO] of a row with the plane of its ALPHA and DIPDIR (`true_thickness`);
  preview_s       `POST /run/preview` of one hole, the median of `shown` holes;  check_s — the table read back.
What is checked (a failure stops the run): the samples came from the snapshot; a main row begins and ends with
an ore sample and its grade is ≥ c; it is not thinner than m_min — or has no TT and is not shorter; a «в том
числе» row lies inside its main one with a grade ≥ c2; an empty TT is NaN (never 0) and only of the holes
without a collar; the table read back from `dh_rows` has the rows and the columns of the kernel; the display
and the report are there.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

from . import _samples
from . import true_thickness as tt_ref

FIELD = "AU"
NAME = "Замер: рудные пересечения"
THREADS = (1, 2, 4, 8)
REL = 1e-9  # of the comparisons of the rule (ADR 0021 §2)
STAGES = tt_ref.STAGES
CLOCKS = {"cand_tt": ("cand_tt",), "build": ("build",), "table": ("table",), "commit": ("commit",),
          "display": ("display",), "snapshot": ("snapshot",)}  # fmt: skip


def body(meta: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    return {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD,
            "cutoff": float(params.get("cutoff", 0.5)), "include_cutoff": params.get("include_cutoff", 1.5),
            "l_min": float(params.get("l_min", 2.0)), "min_by": params.get("min_by", "tt"),
            "waste_max": float(params.get("waste_max", 2.0)), "missing": "waste", "density": _samples.DENSITY,
            "orientation": tt_ref.orientation("domain"), "eps": tt_ref.EPS, "step": tt_ref.STEP,
            "name": NAME}  # fmt: skip


def job(
    meta: dict[str, Any], engine: Any, folder: Path, params: dict[str, Any], job_id: int = 1
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks, list[Any], list[str]]:
    """`jobs_run.run_intercepts` with clocks inside its stages: (its result, the clocks, the ticks, the calls of
    `jobs_run.compute` — what it was given and what it made, where the samples were read from)."""
    from bf_core.composites import sink
    from bf_core.intercepts import display, jobs, jobs_run, rule, table, tt

    read_from: list[str] = []
    ticks = _samples.Ticks()
    with _samples.captured(jobs_run, "compute") as calls, _samples.stages(
        (jobs, "read", "read"), (rule, "run", "rule"), (table, "_thickness", "cand_tt"), (tt, "compute", "tt"),
        (table, "build", "build"), (sink, "build_table", "table"), (sink, "commit", "commit"),
        (display, "put_display", "display"), (sink, "write_snapshot", "snapshot"),
        note=lambda key, got: read_from.append(got[0].source) if key == "read" else None,
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = jobs_run.run_intercepts(engine, folder, {"body": body(meta, params), "rows": meta["rows"]},
                                      job_id=job_id, tick=ticks)  # fmt: skip
        took["done"] = took["@commit"] - t0
    return out, took, ticks, calls, read_from


def digest(t: Any) -> str:
    """SHA-256 of an `InterceptTable`: the bytes of its rows, of every column and the summary."""
    import numpy as np

    h = hashlib.sha256()
    for a in (t.hole, t.frm, t.to, *(c.values for c in t.columns)):
        if a.dtype == object:
            h.update("\x00".join(map(str, a.tolist())).encode())
            continue
        a = np.ascontiguousarray(a)
        h.update(f"{a.dtype.str}{a.shape}".encode())
        h.update(memoryview(a).cast("B"))
    h.update(json.dumps(t.summary, sort_keys=True, ensure_ascii=False, default=float).encode())
    return h.hexdigest()


def threads_hashes(call: Any) -> tuple[set[str], dict[str, float]]:
    """The result of the job and of `jobs_run.compute` on its samples with 1, 2, 4, 8 threads: their hashes and
    the seconds of every computation."""
    from bf_core.intercepts import jobs_run

    (spec, src, tables, st, project), _kw, (_lines, made, _o) = call
    found = {digest(made)}
    took: dict[str, float] = {}
    for n in THREADS:
        t0 = time.perf_counter()
        again = jobs_run.compute(spec, src, tables, st, project, keep=False, threads=n)[1]
        took[f"kernel_t{n}_s"] = round(time.perf_counter() - t0, 3)
        found.add(digest(again))
        del again
    return found, took


def keep_reference(work: Path, d: dict[str, Any]) -> None:
    """The arrays of the generator the checks of `run` need — `work/ref.npz` — and the station directions."""
    import numpy as np

    np.savez(work / "ref.npz", hole=d["a_hole"].astype(np.int32), frm=d["a_from"], to=d["a_to"],
             au=d["grades"][FIELD], density=d["density"], dirs=tt_ref.station_dirs(d),
             lost=np.isnan(d["x"]))  # fmt: skip


def check_rows(work: Path, t: Any, request: dict[str, Any]) -> dict[str, Any]:
    """The table of the kernel against the arrays of the generator (module docstring); the numbers of the info."""
    import numpy as np

    ref = np.load(work / "ref.npz")
    hole, frm, to, au, rho = (ref[k] for k in ("hole", "frm", "to", "au", "density"))
    cut, cut2, least = request["cutoff"], request["include_cutoff"], request["l_min"]
    names = ("LENGTH", "TT", "LEN_G", FIELD, "MISS_LEN", "INCL", "ID", "ALPHA", "DIPDIR")
    col = {name: t.column(name) for name in names}
    main = col["INCL"] == 0
    assert main.any() and (~main).any() and t.n == len(main)
    far = 1e6  # holes one after another on one axis of depth
    row_from = t.hole[main] * far + t.frm[main]
    assert (np.diff(row_from) > 0.0).all()  # the main rows: by hole and depth, apart
    at = np.searchsorted(row_from, hole * far + 0.5 * (frm + to), side="right") - 1
    inside = (at >= 0) & (hole == t.hole[main][np.maximum(at, 0)]) & (to <= t.to[main][np.maximum(at, 0)] + 1e-9)
    row = np.where(inside, at, -1)  # the main row of every sample
    n = int(main.sum())
    # the samples and the gaps between them (no grade, no density) as the pieces of their rows
    gap = (row[1:] == row[:-1]) & (row[1:] >= 0) & (frm[1:] - to[:-1] > 1e-6)
    p_row = np.concatenate([row[inside], row[1:][gap]])
    p_len = np.concatenate([(to - frm)[inside], (frm[1:] - to[:-1])[gap]])
    p_g = np.concatenate([au[inside], np.full(int(gap.sum()), np.nan)])
    p_rho = np.concatenate([rho[inside], np.full(int(gap.sum()), np.nan)])
    g0 = np.where(np.isfinite(p_g), p_g, 0.0)
    dense = np.isfinite(p_rho) & (p_rho > 0.0)
    w = np.where(dense, p_len * p_rho, 0.0)

    def total(x: Any, mask: Any = None) -> Any:
        return np.bincount(p_row if mask is None else p_row[mask], weights=x if mask is None else x[mask], minlength=n)

    length, l_dense, lr = total(p_len), total(p_len, dense), total(w)
    mean_rho = np.divide(lr, l_dense, out=np.ones(n), where=l_dense > 0.0)
    weight = lr + mean_rho * total(p_len, ~dense)
    grade = (total(w * g0) + mean_rho * total(p_len * g0, ~dense)) / weight
    got = {k: v[main] for k, v in col.items()}
    grade_error = float((np.abs(got[FIELD] - grade) / grade).max())
    assert grade_error <= 1e-9, grade_error
    miss = total(p_len, ~np.isfinite(p_g))

    def balance(name: str, want: Any) -> float:
        return float(abs(got[name].sum() - want.sum()) / max(abs(want.sum()), 1.0))

    out = {"balance_len_g": balance("LEN_G", length * grade), "balance_len": balance("LENGTH", length),
           "balance_miss": balance("MISS_LEN", miss), "grade_error": grade_error}  # fmt: skip
    assert max(out.values()) <= 1e-9, out
    # the rule: the ends of a main row are ore samples, its grade is ≥ c, it is thick or long enough
    first = np.flatnonzero(np.append(True, row[1:] != row[:-1]) & inside)
    last = np.flatnonzero(np.append(row[1:] != row[:-1], True) & inside)
    assert len(first) == len(last) == n and (au[first] >= cut * (1 - REL)).all() and (au[last] >= cut * (1 - REL)).all()
    assert np.array_equal(frm[first], t.frm[main]) and np.array_equal(to[last], t.to[main])
    assert (got[FIELD] >= cut * (1 - REL)).all()
    blind = np.isnan(got["TT"])
    assert (got["TT"][~blind] >= least * (1 - REL)).all() and (got["LENGTH"][blind] >= least * (1 - REL)).all()
    assert blind.any() and np.array_equal(blind, ref["lost"][t.hole[main]])  # no TT: no trajectory, nothing else
    parent = np.flatnonzero(main)[np.searchsorted(np.flatnonzero(main), np.flatnonzero(~main)) - 1]
    assert np.array_equal(col["INCL"][~main], col["ID"][parent]) and np.array_equal(t.hole[~main], t.hole[parent])
    assert (t.frm[~main] >= t.frm[parent]).all() and (t.to[~main] <= t.to[parent]).all()
    assert (col[FIELD][~main] >= cut2 * (1 - REL)).all()
    # TT of every row: the sum along the hole with the plane the row was given
    normal = np.stack([tt_ref.plane_normal(a, b) for a, b in zip(col["ALPHA"], col["DIPDIR"], strict=True)])
    want = tt_ref.sum_along(ref["dirs"], ref["lost"], t.hole, t.frm, t.to, normal)
    have = ~np.isnan(want)
    assert np.array_equal(~np.isnan(col["TT"]), have)
    out["tt_error_m"] = float(np.abs(col["TT"][have] - want[have]).max())
    assert out["tt_error_m"] <= 1e-9, out["tt_error_m"]
    return {**out, "icpt_main": n, "icpt_included": int((~main).sum()), "no_tt": int(blind.sum()),
            "longest_m": float(got["LENGTH"].max())}  # fmt: skip


def check_stored(meta: dict[str, Any], engine: Any, out: dict[str, Any], t: Any) -> None:
    """The table read back from `dh_rows` is the table of the kernel; its display is there."""
    import numpy as np

    from bf_core.composites import source
    from bf_core.db import dialect
    from bf_core.intercepts import display

    names = ["LENGTH", "TT", FIELD, "LEN_G", "TT_G", "INCL", "ID", "MISS_LEN"]
    back = source.read(engine, meta["set_id"], out["table_id"], names)
    assert back.rows == t.n == out["rows"], (back.rows, t.n)
    order = np.lexsort((t.column("INCL") != 0, t.frm, t.hole))  # as the table is read: by hole and FROM
    at = np.lexsort((back.numbers["INCL"] != 0, back.from_m, back.hole_of()))
    for name in names:
        assert np.array_equal(back.numbers[name][at], t.column(name)[order], equal_nan=True), name
    assert out["columns"][:3] == ["HOLE", "FROM", "TO"] and set(names) <= set(out["columns"])
    with dialect.read_tx(engine) as conn:
        assert display.chart_of(conn, meta["set_id"], out["name"]) == out["chart_id"]


def previews(meta: dict[str, Any], engine: Any, folder: Path, request: dict[str, Any], shown: int) -> dict[str, Any]:
    """`POST /run/preview` of `shown` holes of the set, one after another."""
    from bf_core.intercepts import routes_preview

    from .composite_preview import hole_ids

    svc = _samples.service(engine, folder)
    took = []
    for hole in hole_ids(engine, meta["set_id"], shown):
        t0 = time.perf_counter()
        got = routes_preview.run_preview(svc, {"params": request, "hole_id": hole})
        took.append(time.perf_counter() - t0)
        assert got["hole"] and len(got["samples"]["from"]) > 0 and got["candidates"], got["hole"]
    return {"preview_first_s": round(took[0], 4), "preview_s": round(statistics.median(took[1:]), 4),
            "preview_max_s": round(max(took[1:]), 4), "shown": len(took)}  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)
    keep_reference(work, tt_ref.generated(params))


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        threads_hashes(job(meta, engine, folder, {})[3][0])
        previews(meta, engine, folder, body(meta, {}), 3)

    _samples.warm(once)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder, restore_s = _samples.fresh(work)
    try:
        out, took, ticks, calls, read_from = job(meta, engine, folder, params)
        peak = _samples.peak_mb()
        report = _samples.report_of(engine, out["result_id"])
        made = calls[0][2][1]
        found, kernels = threads_hashes(calls[0])
        t0 = time.perf_counter()
        check_stored(meta, engine, out, made)
        check_s = time.perf_counter() - t0
        shown = previews(meta, engine, folder, body(meta, params), int(params.get("shown", 30)))
    finally:
        engine.dispose()
    assert read_from == [_samples.read_from(meta["rows"])], read_from
    assert not out["fresh"] and not out["replaced"] and out["name"] == NAME, out["name"]
    checked = check_rows(work, made, body(meta, params))
    assert out["rows"] == report["rows"] == checked["icpt_main"] + checked["icpt_included"], out["rows"]
    assert report["intercepts"] == checked["icpt_main"] and report["included"] == checked["icpt_included"], report
    assert report["field"] == FIELD and report["unit"] == "г/т" and not report["excluded"], report
    return {
        "icpt_s": round(took["done"], 3),
        **{f"icpt_{name}_s": out["stages"][stage] for name, stage in STAGES.items()},
        "icpt_rule_s": round(took["rule"] - took.get("cand_tt", 0.0), 3),
        "icpt_tt_s": round(took["tt"] - took.get("cand_tt", 0.0), 3),
        **_samples.timings(took, CLOCKS, "icpt_"),
        "icpt_rows": out["rows"],
        "icpt_mb": peak,
        "icpt_tick_gap_s": round(ticks.gap, 3),
        "hashes": len(found),
        "hash": sorted(found)[0][:16],
        **kernels,
        **checked,
        **shown,
        "check_s": round(check_s, 3),
        "rows": meta["rows"],
        "restore_s": round(restore_s, 3),
    }
