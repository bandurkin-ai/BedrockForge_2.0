"""Coding of samples by wireframes — the whole job `composite.code` (ADR 0018 «Замеры»; ТЗ 8.2.3: «Кодирование
каркасами 1 млн треугольников — ≤ 90 с» на 50 тыс. скважин, 5 млн проб; ci — 5 тыс., 500 тыс., ориентир ≤ 12 с).

The set of `_samples` and `bodies` closed solids of `triangles` triangles in all (`tools/gen/wireframes.py::
ore_body`: ellipsoids turned and plunged, as the store keeps a wireframe), placed over the field of the holes:
two large flat lenses that overlap and a rich zone inside the first one (the order of the list decides), a steep
vein, a stock that holds collars and whole holes (no crossing: the collar check), small lenses. Every body has
its poles on the vertical — hundreds of thin triangles meet where steep holes go through. The holes curve, some
are nearly flat, some have no survey, some no collar (no trajectory: no code, never a zero).
Two jobs, each the function its handler calls — `jobs_coding.run_coding` — on the project as `prepare` left it:
  split  the samples divided at the entries and exits (the default): «Пробы — коды» of the parts;
  whole  not divided: CODE by the middle of the sample and INSIDE_FRAC.
Per job `j` (the request, the samples read from the snapshot — решение 16, the trajectories, SHA-256 of the
inputs, the bodies read one at a time, the stretches inside them, the samples, the table built aside, the final
transaction, the snapshot of the result):
  j_s           from the start of the job to the end of the final transaction — what the target of ТЗ counts;
  j_read_s      `source.read`;  j_traj_s — the trajectories;  j_hash_s — SHA-256;
  j_geometry_s  the vertices and triangles of the bodies read from the blobs;  j_trees_s — their trees of boxes;
  j_check_s     the neighbouring triangles of every body wound one way;
  j_cross_s     `hole_crossings` and `inside_intervals` on threads;  j_collars_s — the collar check;
  j_samples_s   `code_samples`: the samples cut and coded;  j_kernel_s — `code_by_wireframes` as a whole;
  j_compute_s   with the columns of the table and the summary «до/после»;
  j_write_s     the table and the final transaction;  j_snapshot_s — the snapshot, after it;
  j_rows        rows of the result;  j_mb — the peak of the process so far (VmHWM);  j_tick_gap_s — the longest
                wait between two ticks.
What is checked (a failure stops the run). Against the ellipsoids themselves (`body_distance` at the middle of
every part or sample on its trajectory — `Trajectories.positions`, not the kernels of the coding): the code is
the first body of the list that holds the point, for every row that is not within the gap between a body and
its ellipsoid (`unsure` rows; the facets are inside the ellipsoid by a part of 1e-4 of its size) — `wrong` = 0;
a row of a hole without a trajectory has no code and INSIDE_FRAC NaN. `split`: the parts tile the samples — Σ of
their lengths and the metal Σ l·AU are those of the samples within 1e-9, every cut lies on a body (`cut_off` —
the farthest, as a part of the size of the body). `whole`: a row for every sample; INSIDE_FRAC is the length of
the parts of `split` with the code of the sample (without a code — with any) over its length, within 1e-9
(`frac_error`). Holes wholly inside a body are met (`inside`); the report is in `dh_results`.
"""

from __future__ import annotations

import gc
import math
import time
from pathlib import Path
from typing import Any

from . import _samples

STAGES = {"read": ("read",), "traj": ("traj",), "hash": ("hash",), "geometry": ("geometry",), "trees": ("trees",),
          "check": ("check",), "cross": ("cross",), "collars": ("collars",), "samples": ("samples",),
          "kernel": ("kernel",), "compute": ("compute",), "write": ("table", "commit"),
          "snapshot": ("snapshot",)}  # fmt: skip
NAMES = {"split": "Замер: коды, с делением", "whole": "Замер: коды, без деления"}
# name, share of the triangles, centre (parts of the side of the field; Z, m), radii (parts of the side or −m:
# along, across, the third way), azimuth and plunge of the long axis — by priority
PLAN = [
    ("Богатая зона", 0.05, (0.33, 0.42, 255.0), (0.08, 0.05, -10.0), 30.0, 5.0),
    ("Залежь 1", 0.38, (0.35, 0.40, 255.0), (0.25, 0.12, -25.0), 30.0, 5.0),
    ("Залежь 2", 0.20, (0.55, 0.45, 240.0), (0.20, 0.10, -30.0), 120.0, 10.0),
    ("Жила", 0.12, (0.70, 0.70, 250.0), (0.15, -12.0, -60.0), 60.0, 0.0),
    ("Шток", 0.10, (0.20, 0.75, 300.0), (0.06, 0.04, -150.0), 0.0, 0.0),
    ("Линза 1", 0.06, (0.80, 0.25, 230.0), (0.07, 0.04, -15.0), 80.0, 15.0),
    ("Линза 2", 0.05, (0.15, 0.20, 270.0), (0.06, 0.05, -12.0), 150.0, 0.0),
    ("Линза 3", 0.04, (0.60, 0.85, 220.0), (0.05, 0.03, -20.0), 10.0, 25.0),
]


def bodies_of(holes: int, triangles: int, count: int = len(PLAN)) -> list[dict[str, Any]]:
    """The bodies over the field of `holes` holes of `tools/gen/drillholes.py::arrays`: name, triangles and the
    ellipsoid (centre, radii, azimuth, plunge) of each."""
    from tools.gen.drillholes import GRID_M, X0, Y0

    side = math.ceil(math.sqrt(holes)) * GRID_M
    plan = PLAN[:count]
    total = sum(p[1] for p in plan)
    out = []
    for name, share, (fx, fy, z), radii, azimuth, plunge in plan:
        out.append({
            "name": name, "triangles": max(64, round(triangles * share / total)),
            "ellipsoid": {"centre": (X0 + fx * side, Y0 + fy * side, z),
                          "radii": tuple(-r if r < 0.0 else r * side for r in radii),
                          "azimuth": azimuth, "plunge": plunge},
        })  # fmt: skip
    return out


def store_bodies(params: dict[str, Any]) -> _samples.More:
    """`more` of `_samples.build`: the bodies written into the project as an import writes wireframes."""

    def more(meta: dict[str, Any], engine: Any, folder: Path) -> dict[str, Any]:
        from tools.gen.wireframes import ore_body

        from bf_core.db import dialect
        from bf_core.storage.blobs import BlobStore
        from bf_core.wireframes import store as wf_store

        made = bodies_of(meta["holes"], int(params.get("triangles", 2000)), int(params.get("bodies", len(PLAN))))
        blobs = BlobStore(folder)
        for body in made:
            v, t = ore_body(body["triangles"], **body["ellipsoid"])
            body["triangles"] = len(t)
            staged = wf_store.stage_wireframe(blobs, v, t)
            try:
                with dialect.write_tx(engine) as conn:
                    body["id"] = int(wf_store.commit_wireframe(conn, blobs, staged, name=body["name"]))
            finally:
                staged.discard()
            del v, t, staged
        gc.collect()
        return {"bodies": made}

    return more


def job(
    meta: dict[str, Any], engine: Any, folder: Path, how: str, job_id: int = 1
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks, list[Any], list[str]]:
    """`jobs_coding.run_coding` with the clocks of its stages: (its result, the clocks, the ticks, the calls of
    `code_by_wireframes` — what it was given and what it made, where the samples were read from)."""
    from bf_core.composites import coding, jobs_coding, sink, source
    from bf_core.wireframes import exact

    body = {"set_id": meta["set_id"], "table": str(meta["samples"]), "wireframe_ids": [b["id"] for b in meta["bodies"]],
            "split": how == "split", "name": NAMES[how]}  # fmt: skip
    read_from: list[str] = []
    ticks = _samples.Ticks()
    with _samples.captured(coding, "code_by_wireframes") as calls, _samples.stages(
        (source, "read", "read"), (source, "trajectories", "traj"), (source, "stations_bytes", "hash"),
        (source, "input_hash", "hash"), (exact, "geometry", "geometry"), (exact.Geometry, "target", "trees"),
        (coding, "checked_target", "check"), (coding, "_inside", "cross"), (coding, "_collar_levels", "collars"),
        (coding, "code_samples", "samples"), (coding, "code_by_wireframes", "kernel"),
        (jobs_coding, "compute_coding", "compute"), (sink, "build_table", "table"), (sink, "commit", "commit"),
        (sink, "write_snapshot", "snapshot"),
        note=lambda key, got: read_from.append(got.source) if key == "read" else None,
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = jobs_coding.run_coding(engine, folder, {"body": body, "rows": meta["rows"],
                                                      "triangles": max(b["triangles"] for b in meta["bodies"])},
                                     job_id=job_id, tick=ticks)  # fmt: skip
        took["done"] = took["@commit"] - t0
    return out, took, ticks, calls, read_from


def where(call: Any, depth: Any = None) -> tuple[Any, Any]:
    """(the rows of the result that have a trajectory, the points of their middles — or of `depth` — on it)."""
    import numpy as np

    (samples, traj, dense, *_rest), _kw, coded = call
    lines = coded.lines
    of_hole = np.searchsorted(samples.holes, lines.hole_ids())  # the holes of the samples are ascending
    which = np.asarray(dense, dtype=np.int64)[of_hole]
    length = traj.lengths()
    known = which >= 0
    known[known] = np.isfinite(length[which[known]])
    at = 0.5 * (lines.frm + lines.to) if depth is None else depth
    return known, traj.positions(which[known], at[known])


def reference(meta: dict[str, Any], points: Any) -> tuple[Any, Any, Any]:
    """Against the ellipsoids: (the first body of the list that surely holds the point, −1 — surely none; the
    points in the gap between a body and its ellipsoid — the code is not told; the distance to the nearest
    ellipsoid as a part of its size)."""
    import numpy as np
    from tools.gen.wireframes import body_distance

    code = np.full(len(points), -1, dtype=np.int32)
    unsure = np.zeros(len(points), dtype=bool)
    off = np.full(len(points), np.inf)
    for k in range(len(meta["bodies"]) - 1, -1, -1):
        body = meta["bodies"][k]
        d = body_distance(points, **body["ellipsoid"])
        rings = max(3, int(math.sqrt(body["triangles"] / 4)))  # of `tools/gen/mesh.py::uv_sphere`
        gap = 2.5 * (math.pi / rings) ** 2 / 8.0  # how far inside the ellipsoid its facets are, and a quarter more
        inside = d < 1.0 - gap
        code[inside] = k
        unsure[inside] = False  # a body before in the list decides
        unsure |= (d >= 1.0 - gap) & (d <= 1.0 + 1e-9)
        off = np.minimum(off, np.abs(d - 1.0))
    return code, unsure, off


def check_codes(meta: dict[str, Any], call: Any) -> dict[str, Any]:
    """The codes of the result against the ellipsoids (the module docstring)."""
    import numpy as np

    coded = call[2]
    known, points = where(call)
    want, unsure, _off = reference(meta, points)
    got = coded.code[known]
    wrong = int(np.count_nonzero((got != want) & ~unsure))
    assert wrong == 0, (wrong, int(unsure.sum()))
    assert (coded.code[~known] == -1).all() and (~known).any() and known.any()
    assert (np.bincount(got + 1, minlength=len(meta["bodies"]) + 1) > 0).all(), np.bincount(got + 1)
    if coded.inside_frac is not None:
        frac = coded.inside_frac
        assert np.isnan(frac[~known]).all() and not np.isnan(frac[known]).any()
        assert frac[known].min() == 0.0 and frac[known].max() == 1.0
    return {"wrong": wrong, "unsure": int(unsure.sum()), "coded": int(np.count_nonzero(coded.code >= 0))}


def check_cuts(meta: dict[str, Any], call: Any) -> float:
    """Every cut of a sample lies on a body: the farthest, as a part of the size of that body."""
    import numpy as np

    lines, rows = call[2].lines, call[2].rows
    cut = np.zeros(lines.n, dtype=bool)
    cut[1:] = rows[1:] == rows[:-1]  # the part begins where the part before it of the same sample ends
    known, points = where(call, lines.frm)
    _code, _unsure, off = reference(meta, points[cut[known]])
    assert len(off) > 0 and int(cut.sum()) == lines.n - call[0][0].n, (len(off), lines.n - call[0][0].n)
    gap = max(2.5 * (math.pi / max(3, int(math.sqrt(b["triangles"] / 4)))) ** 2 / 8.0 for b in meta["bodies"])
    assert float(off.max()) <= gap + 1e-6, (float(off.max()), gap)
    return float(off.max())


def measure(meta: dict[str, Any], engine: Any, folder: Path, how: str) -> tuple[dict[str, Any], Any]:
    out, took, ticks, calls, read_from = job(meta, engine, folder, how)
    report = _samples.report_of(engine, out["result_id"])
    ref, after = meta["ref"], report["summary"]["after"]
    assert read_from == [_samples.read_from(meta["rows"])], read_from
    assert not out["fresh"] and out["rows"] == report["rows"] == after["count"], out
    assert report["rows_in"] == meta["rows"] and report["skipped"] == 0 and report["split"] is (how == "split")
    assert _samples.close(after["length"], ref["sampled_m"], 1e-9), (after["length"], ref["sampled_m"])
    assert _samples.close(after["fields"]["AU"]["metal"], ref["metal_au"], 1e-9), (after["fields"]["AU"], ref)
    assert [b["triangles"] for b in report["bodies"]] == [b["triangles"] for b in meta["bodies"]]
    assert report["no_trajectory"]["rows"] > 0 and sum(b["inside"] for b in report["bodies"]) > 0, report["bodies"]
    info = {
        f"{how}_s": round(took["done"], 3),
        **_samples.timings(took, STAGES, f"{how}_"),
        f"{how}_rows": out["rows"],
        f"{how}_mb": _samples.peak_mb(),
        f"{how}_tick_gap_s": round(ticks.gap, 3),
    }
    if how == "split":
        assert out["rows"] - meta["rows"] >= report["cut"] > 0, (out["rows"], report["cut"])  # cut — samples
        info.update(cut=report["cut"], inside=sum(b["inside"] for b in report["bodies"]))
    else:
        assert out["rows"] == meta["rows"], out["rows"]
    return info, calls[0]


def frac_error(split: Any, whole: Any) -> float:
    """INSIDE_FRAC of `whole` against the parts of `split`: the length of the parts with the code of the sample
    (without a code — with any) over its length; the largest difference."""
    import numpy as np

    size, of = split.lines.lengths(), split.rows
    mine = np.where(whole.code[of] >= 0, split.code == whole.code[of], split.code >= 0)
    share = np.bincount(of, weights=np.where(mine, size, 0.0), minlength=whole.lines.n) / whole.lines.lengths()
    have = ~np.isnan(whole.inside_frac)
    return float(np.abs(whole.inside_frac[have] - share[have]).max())


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params, more=store_bodies(params))


def load() -> None:
    def both(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for k, how in enumerate(NAMES):
            job(meta, engine, folder, how, job_id=k + 1)

    _samples.warm(both, more=store_bodies({}))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    restore_s = 0.0
    kept: dict[str, Any] = {}
    for how in NAMES:
        meta, engine, folder, took = _samples.fresh(work)
        restore_s += took
        try:
            got, call = measure(meta, engine, folder, how)
        finally:
            engine.dispose()
        info.update(got)
        found = check_codes(meta, call)
        info.update({f"{how}_{k}": v for k, v in found.items() if k != "wrong"})
        info["wrong"] = info.get("wrong", 0) + found["wrong"]
        if how == "split":
            info["cut_off"] = check_cuts(meta, call)
        kept[how] = call[2]  # the result alone: the samples and the trajectories of the job go
        del call
        gc.collect()
    info["frac_error"] = frac_error(kept["split"], kept["whole"])
    assert info["frac_error"] <= 1e-9, info["frac_error"]
    info.update(rows=meta["rows"], triangles=sum(b["triangles"] for b in meta["bodies"]), bodies=len(meta["bodies"]),
                restore_s=round(restore_s, 3))  # fmt: skip
    return info
