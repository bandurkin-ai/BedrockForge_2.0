"""True thickness by planes — the whole job `intercept.tt` (ADR 0021 «Замеры», решения 20, 21; ТЗ 8.5.4: «TT для
5 млн интервалов — ≤ 10 с»; ci — 5 тыс. скважин, 500 тыс. интервалов, ориентир ≤ 2,5 с).

The set of `_samples`: «Пробы» of unequal length and with gaps, holes that curve (a station every 50 m), nearly
flat ones, holes without a survey (vertical) and without a collar (no trajectory: TT is empty, never a zero).
Two jobs, each the function its handler calls — `intercepts.jobs.run_tt` — on the project as `prepare` left it,
the fields TT, TT_FLAG, TT_SRC, ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ written in place into the 500 thousand / 5
million rows of «Пробы»:
  global  one plane for the set (α 35°, Aп 120°);
  domain  a plane for every rock of the field ROCK of the same table (a flat bed, a vertical dyke the vertical
          holes run along — TT of 1e−16 m and bit 1, steep veins), then the global one for the rows without a rock and
          for the rock that has no plane (the chain of sources).
Per job `j` (решение 21: from the start of the job to the end of the final transaction, the snapshot of the
table ready):
  j_s           what the target of ТЗ counts;  tt_s — the slower of the two jobs (`limits`);
  j_read_s, j_traj_s, j_hash_s, j_kernel_s, j_write_s   the stages the job reports itself (`job.result.stages`:
                read, trajectories, hash, kernel, write);  tt_kernel_s — the slower kernel (`limits`);
  j_orient_s    `orient.resolve` — the plane of every row;  j_sum_s — `directions.thickness`, Σ l·|n·d|;
  j_fields_s    the fields written into the rows (`sink.write_columns`);  j_commit_s — the final transaction
                with them;
  j_mb          the peak of the process by the end of the job (VmHWM: of the second job — with the first
                one and its checks before it);  tt_mb — after the first job (`limits`);  j_tick_gap_s — the
                longest wait between two ticks.
After the jobs (not in `j_s`):
  tt_error_m    the largest |TT − reference| of both jobs, metres (`limits`: 1e−9). The reference is made in
                `prepare` from the arrays of the generator and not by the kernels: the directions of the survey
                stations, the tangent of the minimum curvature at the middle of every part of ≤ 1 m of a sample
                (ТЗ 8.5.1), Σ l·|n·d| with the normal of the plane of the row; a hole without a collar — NaN;
  hashes        different SHA-256 among the job `domain` itself and `jobs.compute` on its rows with 1, 2, 4 and
                8 threads (every column and the summary): 1 — the result does not depend on the threads
                (`limits`);  hash — its first 16 digits;  kernel_t1_s … kernel_t8_s — that computation;
  preview_s     `POST /tt/preview` of one hole, the median of `shown` holes (ADR: ≤ 0,3 с);  preview_max_s,
                preview_first_s (with the trajectories of the set built);
  check_s       the fields read back from `dh_rows`;  cold_read_s — the columns of the job read without the
                snapshot (in `prepare`; no budget).
What is checked (a failure stops the run): the rows came from the snapshot, in the order of the generator; TT_SRC
is «домен» for the rows whose rock has a plane and «глобальная» for the others; an empty TT has bit 8 (the holes
without a collar) and no other row has it; bit 1 is TT < ε_в·l of the reference; the fields read back are, row
for row, what the kernel made (an empty TT is NaN, never 0); the report is in `dh_results`.
"""

from __future__ import annotations

import gc
import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

from . import _samples

ALPHA, DIPDIR = 35.0, 120.0  # the global plane
# rock → (α, Aп); «СЛН» has none: its rows go on to the global plane
ATTITUDES = {"ПЕС": (5.0, 0.0), "АЛВ": (10.0, 270.0), "ГРН": (60.0, 45.0), "КВЦ": (75.0, 120.0), "ДИО": (90.0, 310.0)}
JOBS = ("global", "domain")
THREADS = (1, 2, 4, 8)
EPS, STEP = 0.1, 1.0  # the defaults of ТЗ 8.5.4
FIELDS = ("TT", "TT_FLAG", "TT_SRC", "ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ")
STAGES = {"read": "read", "traj": "trajectories", "hash": "hash", "kernel": "kernel", "write": "write"}
CLOCKS = {"orient": ("orient",), "sum": ("sum",), "fields": ("fields",), "commit": ("commit",)}
SEED = 12  # of `_samples.build`
CHUNK = 1 << 20  # rows of the reference at a time


# --- the reference: the arrays of the generator, numpy ------------------------------------------------------------


def generated(params: dict[str, Any]) -> dict[str, Any]:
    """The arrays `_samples.build` wrote into the project (`tools/gen/drillholes.py::arrays`, the same call)."""
    from tools.gen.drillholes import arrays

    return arrays(int(params["holes"]), int(params["per_hole"]), fields=int(params.get("fields", 10)),
                  litho_per_hole=int(params.get("litho_per_hole", 20)), seed=SEED)  # fmt: skip


def plane_normal(alpha: float, dipdir: float) -> Any:
    """The upper unit normal of a plane that dips α towards the azimuth Aп, degrees (ТЗ 8.5.1)."""
    import numpy as np

    a, b = np.radians(alpha), np.radians(dipdir)
    return np.array([np.sin(a) * np.sin(b), np.sin(a) * np.cos(b), np.cos(a)])


def station_dirs(d: dict[str, Any]) -> Any:
    """float64 [holes, stations, 3]: the unit directions down the holes at their survey stations (every
    `SURVEY_M` metres from the collar); a hole without a survey goes straight down."""
    import numpy as np

    holes = len(d["names"])
    with_survey = np.unique(d["s_hole"])
    n_st = len(d["s_hole"]) // max(1, len(with_survey))
    az = np.radians(d["s_az"]).reshape(-1, n_st)
    dip = np.radians(d["s_dip"]).reshape(-1, n_st)
    out = np.zeros((holes, n_st, 3))
    out[:, :, 2] = -1.0
    out[d["s_hole"][::n_st]] = np.stack([np.sin(az) * np.cos(dip), np.cos(az) * np.cos(dip), np.sin(dip)], axis=-1)
    return out


def tangents(dirs: Any, hole: Any, depth: Any) -> Any:
    """float64 [n, 3]: the tangent of the minimum curvature path of the holes at the depths (ТЗ 8.5.1):
    t = [sin((1 − f)·ψ)·t1 + sin(f·ψ)·t2] / sin ψ between two stations, the last station below it."""
    import numpy as np
    from tools.gen.drillholes import SURVEY_M

    last = dirs.shape[1] - 1
    s = np.minimum((depth // SURVEY_M).astype(np.int64), last)
    e = np.minimum(s + 1, last)
    f = np.where(s < last, (depth - s * SURVEY_M) / SURVEY_M, 0.0)
    t1, t2 = dirs[hole, s], dirs[hole, e]
    sin_b = np.linalg.norm(np.cross(t1, t2), axis=1)
    psi = np.arctan2(sin_b, (t1 * t2).sum(axis=1))
    arc = sin_b >= 1e-9
    with np.errstate(divide="ignore", invalid="ignore"):
        u = np.where(arc, np.sin((1.0 - f) * psi) / np.sin(psi), 1.0)
        v = np.where(arc, np.sin(f * psi) / np.sin(psi), 0.0)
    return u[:, None] * t1 + v[:, None] * t2


def sum_along(dirs: Any, lost: Any, hole: Any, frm: Any, to: Any, normal: Any, step: float = STEP) -> Any:
    """TT of the intervals [frm; to] of the holes with the unit normals `normal` ([3] or [n, 3]): k = ⌈l / step⌉
    equal parts, TT = (l / k)·Σ |n·t| at their middles (`dirs` — `station_dirs`). NaN — a hole of `lost` (no
    trajectory), a normal with a NaN, TO ≤ FROM."""
    import numpy as np

    hole, normal = np.asarray(hole), np.asarray(normal, dtype=np.float64)
    frm, to = np.asarray(frm, dtype=np.float64), np.asarray(to, dtype=np.float64)
    out = np.full(len(hole), np.nan)
    for lo in range(0, len(hole), CHUNK):
        h, a, b = hole[lo : lo + CHUNK], frm[lo : lo + CHUNK], to[lo : lo + CHUNK]
        n = normal if normal.ndim == 1 else normal[lo : lo + CHUNK]
        length = b - a
        k = np.maximum(1, np.ceil(length / step)).astype(np.int64)
        part = length / k
        total = np.zeros(len(h))
        for j in range(int(k.max(initial=1))):
            on = np.flatnonzero(k > j)
            t = tangents(dirs, h[on], a[on] + (j + 0.5) * part[on])
            total[on] += np.abs((t * (n if n.ndim == 1 else n[on])).sum(axis=1))
        tt = part * total
        tt[lost[h] | ~(length > 0.0)] = np.nan
        out[lo : lo + CHUNK] = tt
    return out


def thickness_reference(d: dict[str, Any], normal: Any) -> Any:
    """`sum_along` of the samples of the generator `d`; a hole without a collar has no trajectory."""
    import numpy as np

    return sum_along(station_dirs(d), np.isnan(d["x"]), d["a_hole"], d["a_from"], d["a_to"], normal)


def rock_normals(d: dict[str, Any]) -> tuple[Any, Any]:
    """(float64 [rows, 3] — the normal of every sample of the job `domain`; bool [rows] — by its rock)."""
    import numpy as np

    rock = d["rock"]
    normal = np.tile(plane_normal(ALPHA, DIPDIR), (len(rock), 1))
    by_rock = np.zeros(len(rock), dtype=bool)
    for code, (alpha, dipdir) in ATTITUDES.items():
        at = rock == code
        normal[at] = plane_normal(alpha, dipdir)
        by_rock |= at
    return normal, by_rock


def keep_reference(work: Path, d: dict[str, Any]) -> None:
    """The references of both jobs — `work/ref_*.npy` (made in `prepare`: the checks of `run` only load them)."""
    import numpy as np

    np.save(work / "ref_from.npy", d["a_from"])
    np.save(work / "ref_to.npy", d["a_to"])
    np.save(work / "ref_global.npy", thickness_reference(d, plane_normal(ALPHA, DIPDIR)))
    normal, by_rock = rock_normals(d)
    np.save(work / "ref_domain.npy", thickness_reference(d, normal))
    np.save(work / "ref_by_rock.npy", by_rock)


# --- the jobs -----------------------------------------------------------------------------------------------------


def orientation(how: str) -> list[dict[str, Any]]:
    plane = {"source": "global", "alpha": ALPHA, "dipdir": DIPDIR}
    if how == "global":
        return [plane]
    planes = [{"code": code, "alpha": a, "dipdir": b} for code, (a, b) in ATTITUDES.items()]
    return [{"source": "domain", "field": _samples.ROCK, "attitudes": planes}, plane]


def body(meta: dict[str, Any], how: str) -> dict[str, Any]:
    return {"set_id": meta["set_id"], "table": str(meta["samples"]), "orientation": orientation(how), "eps": EPS,
            "step": STEP, "angles": True}  # fmt: skip


def job(
    meta: dict[str, Any], engine: Any, folder: Path, request: dict[str, Any], job_id: int = 1,
    spots: tuple[tuple[Any, str, str], ...] = (), triangles: int = 0,
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks, list[Any], list[str]]:  # fmt: skip
    """`jobs.run_tt` with clocks inside its stages: (its result, the clocks, the ticks, the calls of
    `jobs.compute` — what it was given and what it made, where the rows were read from). `spots` — more clocks."""
    from bf_core.composites import sink
    from bf_core.intercepts import directions, jobs, orient

    read_from: list[str] = []
    ticks = _samples.Ticks()
    with _samples.captured(jobs, "compute") as calls, _samples.stages(
        (jobs, "read", "read"), (orient, "resolve", "orient"), (directions, "thickness", "sum"),
        (sink, "write_columns", "fields"), (sink, "commit", "commit"), *spots,
        note=lambda key, got: read_from.append(got[0].source) if key == "read" else None,
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = jobs.run_tt(engine, folder, {"body": request, "rows": meta["rows"], "triangles": triangles},
                          job_id=job_id, tick=ticks)  # fmt: skip
        took["done"] = took["@commit"] - t0
    return out, took, ticks, calls, read_from


def timings(prefix: str, out: dict[str, Any], took: dict[str, float], ticks: _samples.Ticks,
            clocks: dict[str, tuple[str, ...]] = CLOCKS) -> dict[str, Any]:  # fmt: skip
    """The info of a job: `<prefix>_s`, the stages it reports itself, the clocks, the peak, the ticks."""
    return {
        f"{prefix}_s": round(took["done"], 3),
        **{f"{prefix}_{name}_s": out["stages"][stage] for name, stage in STAGES.items()},
        **_samples.timings(took, clocks, f"{prefix}_"),
        f"{prefix}_mb": _samples.peak_mb(),
        f"{prefix}_tick_gap_s": round(ticks.gap, 3),
    }


def digest(t: Any) -> str:
    """SHA-256 of a `tt.TtTable`: the bytes of its columns and the summary."""
    import numpy as np

    h = hashlib.sha256()
    for a in (t.tt, t.flag, t.src, t.alpha, t.dipdir, t.theta, t.azimuth):
        a = np.ascontiguousarray(a)
        h.update(f"{a.dtype.str}{a.shape}".encode())
        h.update(memoryview(a).cast("B"))
    h.update(json.dumps(t.summary, sort_keys=True, ensure_ascii=False, default=float).encode())
    return h.hexdigest()


def threads_hashes(
    call: Any, threads: tuple[int, ...] = THREADS, mesh: Any = None
) -> tuple[set[str], dict[str, float]]:
    """The result of the job and of `jobs.compute` on its rows with 1, 2, 4, 8 threads: their hashes and the
    seconds of every computation. `mesh(project)` — the source «каркас» of a computation, when there is one."""
    from bf_core.intercepts import jobs

    (spec, lines, tables, st, project), _kw, made = call
    found = {digest(made)}
    took: dict[str, float] = {}
    for n in threads:
        t0 = time.perf_counter()
        again = jobs.compute(spec, lines, tables, st, project, mesh=None if mesh is None else mesh(project),
                             keep=False, threads=n)  # fmt: skip
        took[f"kernel_t{n}_s"] = round(time.perf_counter() - t0, 3)
        found.add(digest(again))
        del again
    return found, took


def check_table(work: Path, how: str, t: Any, lines: Any) -> dict[str, Any]:
    """What the kernel made against the reference of `prepare` (module docstring); the numbers of the info."""
    import numpy as np

    from bf_core.intercepts import orient
    from bf_core.intercepts.formulas import FLAG_NO_PATH, FLAG_PARALLEL

    frm, to = np.load(work / "ref_from.npy"), np.load(work / "ref_to.npy")
    assert np.array_equal(lines.from_m, frm) and np.array_equal(lines.to_m, to)  # the rows of the generator
    ref = np.load(work / f"ref_{how}.npy")
    have = ~np.isnan(ref)
    assert np.array_equal(~np.isnan(t.tt), have) and have.any() and not have.all()
    error = float(np.abs(t.tt[have] - ref[have]).max())
    assert error <= 1e-9, (how, error)
    assert np.array_equal((t.flag & FLAG_NO_PATH) != 0, ~have)  # an empty TT: no trajectory, nothing else
    along = have & (ref < EPS * (to - frm))
    sure = np.abs(ref - EPS * (to - frm)) > 1e-9  # not on the very boundary of ε_в
    assert np.array_equal(((t.flag & FLAG_PARALLEL) != 0)[sure], along[sure]) and (how == "global" or along.any())
    assert not (t.flag & ~np.uint8(FLAG_NO_PATH | FLAG_PARALLEL)).any()
    by_rock = np.load(work / "ref_by_rock.npy") if how == "domain" else np.zeros(len(ref), dtype=bool)
    assert np.array_equal(t.src, np.where(by_rock, orient.SRC_DOMAIN, orient.SRC_GLOBAL))
    assert how == "global" or (by_rock.any() and not by_rock.all())
    return {f"{how}_error_m": error, f"{how}_empty": int((~have).sum()), f"{how}_along": int(along.sum())}


def check_fields(meta: dict[str, Any], engine: Any, t: Any) -> None:
    """The fields of «Пробы» read back from `dh_rows` are what the kernel made (NaN stays NaN)."""
    import numpy as np

    from bf_core.composites import source

    back = source.read(engine, meta["set_id"], meta["samples"], list(FIELDS))
    assert back.source == "rows" and back.rows == meta["rows"], (back.source, back.rows)
    for name, made in (("TT", t.tt), ("TT_FLAG", t.flag.astype(np.float64)), ("ALPHA", t.alpha), ("DIPDIR", t.dipdir),
                       ("HOLE_THETA", t.theta), ("HOLE_AZ", t.azimuth)):  # fmt: skip
        assert np.array_equal(back.numbers[name], made, equal_nan=True), name
    names = np.array([*back.labels["TT_SRC"], ""], dtype=object)
    assert np.array_equal(names[back.codes["TT_SRC"]], t.src_text())


def previews(meta: dict[str, Any], engine: Any, folder: Path, request: dict[str, Any], shown: int) -> dict[str, Any]:
    """`POST /tt/preview` of `shown` holes of the set, one after another."""
    from bf_core.intercepts import routes_preview

    from .composite_preview import hole_ids

    svc = _samples.service(engine, folder)
    took = []
    for hole in hole_ids(engine, meta["set_id"], shown):
        t0 = time.perf_counter()
        got = routes_preview.tt_preview(svc, {"params": request, "hole_id": hole})
        took.append(time.perf_counter() - t0)
        assert got["hole"] and len(got["rows"]["TT"]) == len(got["rows"]["from"]) > 0, got["hole"]
        assert not any(v != v for v in got["rows"]["TT"] if v is not None)  # empty is null, never NaN
    return {"preview_first_s": round(took[0], 4), "preview_s": round(statistics.median(took[1:]), 4),
            "preview_max_s": round(max(took[1:]), 4), "shown": len(took)}  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.db import dialect
    from bf_core.intercepts import jobs

    meta = _samples.build(work, params)
    keep_reference(work, generated(params))
    meta, engine, folder = _samples.opened(work)
    try:
        with dialect.read_tx(engine) as conn:
            spec = jobs.parse(conn, body(meta, "domain"))
        t0 = time.perf_counter()
        lines, _tables = jobs.read(engine, spec, cache_dir=None)  # the depths and ROCK without the snapshot
        cold = time.perf_counter() - t0
        assert lines.source == "rows" and lines.rows == meta["rows"]
        (work / "cold.json").write_text(json.dumps({"cold_read_s": round(cold, 3)}))
    finally:
        engine.dispose()


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for k, how in enumerate(JOBS):
            calls = job(meta, engine, folder, body(meta, how), job_id=k + 1)[3]
        threads_hashes(calls[0])
        previews(meta, engine, folder, body(meta, "domain"), 3)

    _samples.warm(once)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    restore_s, kept, found = 0.0, {}, set()
    for how in JOBS:
        meta, engine, folder, took_s = _samples.fresh(work)
        restore_s += took_s
        try:
            out, took, ticks, calls, read_from = job(meta, engine, folder, body(meta, how))
            info.update(timings(how, out, took, ticks))
            report = _samples.report_of(engine, out["result_id"])
            assert read_from == [_samples.read_from(meta["rows"])], read_from
            assert not out["fresh"] and out["columns"] == list(FIELDS) and out["rows"] == meta["rows"], out["columns"]
            assert report["rows"] == meta["rows"] and report["written"][0]["columns"] == list(FIELDS), report["rows"]
            (_spec, lines, *_rest), _kw, made = calls[0]
            info.update(check_table(work, how, made, lines))
            assert report["with_tt"] == meta["rows"] - info[f"{how}_empty"], report["with_tt"]
            if how == JOBS[-1]:  # the last job: its project stays, the fields are there
                found, kernels = threads_hashes(calls[0])
                t0 = time.perf_counter()
                check_fields(meta, engine, made)
                info.update(kernels, check_s=round(time.perf_counter() - t0, 3))
                info.update(previews(meta, engine, folder, body(meta, how), int(params.get("shown", 30))))
            kept[how] = digest(made)
        finally:
            engine.dispose()
        del calls, lines, made, out
        gc.collect()
    assert kept["global"] != kept["domain"]  # two different calculations
    info.update(
        tt_s=max(info[f"{how}_s"] for how in JOBS),
        tt_kernel_s=max(info[f"{how}_kernel_s"] for how in JOBS),
        tt_mb=info["global_mb"],
        tt_error_m=max(info[f"{how}_error_m"] for how in JOBS),
        hashes=len(found),
        hash=sorted(found)[0][:16],
        rows=meta["rows"],
        restore_s=round(restore_s, 3),
        **json.loads((work / "cold.json").read_text()),
    )
    return info
