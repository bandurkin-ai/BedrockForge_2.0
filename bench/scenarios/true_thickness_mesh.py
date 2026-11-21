"""True thickness by wireframes — the whole job `intercept.tt` with the source «каркас» (ADR 0021 «Замеры», §5,
решения 7, 20, 21; ТЗ 8.5.4: «с каркасом 10 млн треугольников — ≤ 60 с и ≤ 3 ГБ» на 5 млн интервалов; ci —
500 тыс. интервалов × 1 млн треугольников, ориентир ≤ 8 с).

The set of `_samples` and a zone of veins over the field of its holes: closed plates `THICK` m thick that dip
`ALPHA`° to `DIPDIR`°, `GAP` m apart, from the level `Z_LO` to `Z_HI`, each as long as the field is across its
strike line (43 veins of 0,9–7,2 km over 50 thousand holes) — `triangles` triangles in all, the same size
everywhere (7 m at 10 million; 8 m at 1 million over 5 thousand holes), in `bodies` wireframes by turns:
neighbouring veins are in different wireframes — the nearest triangle is sought in all of them, a job reads them
one at a time. A hole of 100 m goes through one or two veins; a sample of 1 m crosses a face (1,6 % of the
rows — «каркас: пересечение»), or takes the nearest triangle up to (GAP − THICK) / 2 = 39 m away (95 % —
«каркас: ближайшая»), or — in the part of the field the zone does not reach (`1 − ZONE` of it along the dip
direction) — has no triangle within d_max = 50 m: «нет», TT empty, bit 2. Holes without a collar have no
trajectory. The search of the nearest triangle costs more on finer triangles and farther points (подшаг 15.7: a
plate of triangles of 1 m, 96 % of the rows 20–70 m from it — 3 µs a row on four threads against 1,7 here).
The job `mesh` — `intercepts.jobs.run_tt` on «Пробы» with the chain [каркас], the fields written in place:
  mesh_s          from the start of the job to the end of the final transaction — what the target of ТЗ counts;
  mesh_read_s, mesh_traj_s, mesh_hash_s, mesh_kernel_s, mesh_write_s   the stages the job reports itself
                  (`job.result.stages`);
  mesh_geometry_s the vertices and triangles of the wireframes read from their blobs;  mesh_tree_s — their
                  trees of boxes;  mesh_cross_s — the crossings of the holes inside the intervals;
  mesh_nearest_s  the nearest triangles;  nearest_us — microseconds a row asked about (the wall time of the
                  job's threads);  mesh_orient_s — `mesh_orientation` as a whole;
  mesh_sum_s      Σ l·|n·d| along the holes;  mesh_fields_s, mesh_commit_s — the fields and the final
                  transaction;  mesh_mb — the peak of the process at the end of the job (VmHWM; `limits`);
  share_crossing, share_nearest, share_none   the rows by TT_SRC.
After it (not in `mesh_s`):
  plate_error_m   Т8.5-13 on the veins: a second job on the table «Пласт» — one interval from the entry of a
                  hole into a vein to its exit (found in `prepare` on `Trajectories.positions`, by bisection) —
                  the largest |TT − THICK|, metres (`limits`: 1e−6), with parts of the hole of `PLATE_STEP` =
                  0,1 m: the error of the sum on the arcs of these holes is then 1e−8 m (with parts of 1 m —
                  up to 5e−7 m where a survey station falls inside a part);  plate_rows, plate_s;
  tt_error_m      the largest |TT − reference| of the rows of «Пробы» with a plane, metres (`limits`: 1e−6): the
                  reference of `true_thickness` with the plane of the veins (a triangle of a vein has the normal
                  of its plane to 1e−10: its corners are rounded to float64 at UTM coordinates);
  hashes          different SHA-256 among the job and `jobs.compute` with the threads of `threads` (1 and 4):
                  1 (`limits`);  kernel_t1_s, kernel_t4_s — that computation;  mesh_t1_s — the job as it would
                  take on one thread (the risk of ADR): `mesh_s` with `kernel_t1_s` in place of its kernel;
  missed, extra   rows the wireframes are nearer to than d_max − 1 mm that got no plane; rows farther than
                  d_max + 1 mm that got one (`limits`: 0) — against the exact distance from the middle of the
                  sample to the plates;  odd — rows with a plane other than that of the veins (0).
What is checked (a failure stops the run): the rows came from the snapshot, in the order of the generator; a row
whose ends are on the two sides of a face is «каркас: пересечение», a row between two faces is not; ALPHA and
DIPDIR of every row with a plane are those of the veins (1e−6°); an empty TT has bit 2 or 8, never a zero; the
fields read back from `dh_rows` are what the kernel made; the wireframes are in `inputs` of the record.
"""

from __future__ import annotations

import gc
import math
import time
from pathlib import Path
from typing import Any

from . import _samples
from . import true_thickness as tt_ref

ALPHA, DIPDIR = 35.0, 120.0  # of every vein
THICK, GAP = 12.0, 90.0  # m: of a vein; between the middle planes of two veins
Z_LO, Z_HI, Z_MID = 100.0, 400.0, 250.0  # the levels the veins lie between; of the origin of their frame
ZONE = 0.85  # of the field, along the dip direction, has veins
MARGIN = 200.0  # m: the veins go beyond the field along the strike
D_MAX = 50.0  # the default of ТЗ 8.5.4
PLATE_STEP = 0.1  # m: the parts of a hole in the job on «Пласт»
LAYER = "Пласт"
NAMES = {"mesh": "Пробы", "plate": LAYER}
CLOCKS = {**tt_ref.CLOCKS, "geometry": ("geometry",), "tree": ("tree",), "cross": ("cross",),
          "nearest": ("nearest",), "orient": ("mesh",)}  # fmt: skip
SURE = 1e-3  # m: a sample this near to d_max or to a face may go either way


# --- the veins ----------------------------------------------------------------------------------------------------


def frame(holes: int) -> dict[str, Any]:
    """The frame of the veins over the field of `holes` holes: the origin `c0`, the unit vectors u (the strike,
    level), v (down the dip), n (the upper normal: u × v = n), the half side of the field."""
    import numpy as np
    from tools.gen.drillholes import GRID_M, X0, Y0

    half = 0.5 * math.ceil(math.sqrt(holes)) * GRID_M
    a = math.radians(DIPDIR)
    n = tt_ref.plane_normal(ALPHA, DIPDIR)
    u = np.array([math.cos(a), -math.sin(a), 0.0])
    return {"c0": np.array([X0 + half, Y0 + half, Z_MID]), "u": u, "v": np.cross(n, u), "n": n, "half": half}


def plan(holes: int) -> list[dict[str, float]]:
    """The veins: k (the middle plane is k·GAP along n from the origin) and the rectangle of its faces in the
    frame — u0 … u1 along the strike, v0 … v1 down the dip."""
    fr = frame(holes)
    sin_a, cos_a, tan_a = (f(math.radians(ALPHA)) for f in (math.sin, math.cos, math.tan))
    a = math.radians(DIPDIR)
    reach = fr["half"] * (abs(math.sin(a)) + abs(math.cos(a)))  # of the field, either way along the dip direction
    wide = 0.5 * (Z_HI - Z_LO) / tan_a  # of a vein, level, either way from its line on Z_MID
    side = fr["half"] + MARGIN
    out = []
    first, last = math.ceil((-reach - wide) * sin_a / GAP), math.floor((-reach + ZONE * 2.0 * reach) * sin_a / GAP)
    for k in range(first, last + 1):
        q = k * GAP / sin_a  # the line of the vein on Z_MID, metres along the dip direction from the origin
        lo, hi = math.inf, -math.inf
        for at in (q - wide, q + wide):  # the strike line within the field and its margin: |x|, |y| ≤ side
            spans = []
            for along, across in ((math.sin(a), math.cos(a)), (math.cos(a), -math.sin(a))):
                if abs(across) < 1e-12:
                    spans.append((-math.inf, math.inf) if abs(at * along) <= side else (1.0, -1.0))
                else:
                    ends = sorted(((-side - at * along) / across, (side - at * along) / across))
                    spans.append((ends[0], ends[1]))
            s0, s1 = max(s[0] for s in spans), min(s[1] for s in spans)
            if s0 < s1:
                lo, hi = min(lo, s0), max(hi, s1)
        if lo < hi:
            level = Z_MID + k * GAP * cos_a  # of the middle plane over the origin
            out.append({"k": k, "u0": lo, "u1": hi, "v0": (level - Z_HI) / sin_a, "v1": (level - Z_LO) / sin_a})
    return out


def plate(fr: dict[str, Any], vein: dict[str, float], cell: float) -> tuple[Any, Any]:
    """A vein as a closed mesh, normals outward: two faces of cells of about `cell` metres and the walls."""
    import numpy as np

    nu = max(1, round((vein["u1"] - vein["u0"]) / cell))
    nv = max(1, round((vein["v1"] - vein["v0"]) / cell))
    gu, gv = np.meshgrid(np.linspace(vein["u0"], vein["u1"], nu + 1), np.linspace(vein["v0"], vein["v1"], nv + 1),
                         indexing="ij")  # fmt: skip
    flat = fr["c0"] + gu.reshape(-1, 1) * fr["u"] + gv.reshape(-1, 1) * fr["v"]
    top = flat + (vein["k"] * GAP + 0.5 * THICK) * fr["n"]
    bottom = flat + (vein["k"] * GAP - 0.5 * THICK) * fr["n"]
    node = np.arange((nu + 1) * (nv + 1), dtype=np.int64).reshape(nu + 1, nv + 1)
    a, b, c, d = (x.ravel() for x in (node[:-1, :-1], node[1:, :-1], node[1:, 1:], node[:-1, 1:]))
    upper = np.concatenate([np.stack([a, b, c], axis=1), np.stack([a, c, d], axis=1)])
    # the edge of a face, counter-clockwise seen from above (u × v = n): the outside is on its right
    ring = np.concatenate([node[:-1, 0], node[-1, :-1], node[:0:-1, -1], node[0, :0:-1]])
    p, q = ring, np.roll(ring, -1)
    under = len(flat)
    walls = np.concatenate([np.stack([p, p + under, q + under], axis=1), np.stack([p, q + under, q], axis=1)])
    tri = np.concatenate([upper, upper[:, ::-1] + under, walls]).astype(np.int32)
    return np.concatenate([top, bottom]), tri


def veins(holes: int, triangles: int, bodies: int) -> list[dict[str, Any]]:
    """The wireframes: {name, xyz, tri} — the veins of `plan` in `bodies` wireframes by turns, `triangles`
    triangles in all (within a per cent from a million on; a row of cells of a face is 3 % at 40 thousand)."""
    import numpy as np

    fr, made = frame(holes), plan(holes)
    area = sum((p["u1"] - p["u0"]) * (p["v1"] - p["v0"]) for p in made)
    cell = math.sqrt(4.0 * area / max(triangles, 1))
    for _ in range(8):  # the walls and the rounding of the cells: the size is fitted to the number asked for
        count = 0
        for p in made:
            nu, nv = max(1, round((p["u1"] - p["u0"]) / cell)), max(1, round((p["v1"] - p["v0"]) / cell))
            count += 4 * nu * nv + 4 * (nu + nv)
        cell *= math.sqrt(count / max(triangles, 1))
    out = []
    for b in range(min(bodies, len(made))):
        parts = [plate(fr, p, cell) for p in made[b::bodies]]
        first = np.cumsum([0] + [len(v) for v, _t in parts])
        out.append({"name": f"Жильная зона {b + 1}", "xyz": np.concatenate([v for v, _t in parts]),
                    "tri": np.concatenate([t + np.int32(first[i]) for i, (_v, t) in enumerate(parts)])})  # fmt: skip
    return out


def local(fr: dict[str, Any], points: Any) -> tuple[Any, Any, Any]:
    """(u, v, n) of points in the frame of the veins."""
    rel = points - fr["c0"]
    return rel @ fr["u"], rel @ fr["v"], rel @ fr["n"]


def distance(made: list[dict[str, float]], pu: Any, pv: Any, pn: Any) -> Any:
    """The exact distance from points (in the frame) to the surface of the nearest vein: a vein is a box."""
    import numpy as np

    ks = np.array([p["k"] for p in made])
    box = {key: np.array([p[key] for p in made]) for key in ("u0", "u1", "v0", "v1")}
    near = np.rint(pn / GAP).astype(np.int64)
    best = np.full(len(pn), np.inf)
    for shift in (-2, -1, 0, 1, 2):  # a vein farther along n is more than 2·GAP away
        i = np.searchsorted(ks, near + shift)
        ok = (i < len(ks)) & (ks[np.minimum(i, len(ks) - 1)] == near + shift)
        i = i[ok]
        q = np.stack([np.maximum(box["u0"][i] - pu[ok], pu[ok] - box["u1"][i]),
                      np.maximum(box["v0"][i] - pv[ok], pv[ok] - box["v1"][i]),
                      np.abs(pn[ok] - ks[i] * GAP) - 0.5 * THICK])  # fmt: skip
        d = np.sqrt((np.maximum(q, 0.0) ** 2).sum(axis=0)) + np.abs(np.minimum(q.max(axis=0), 0.0))
        best[ok] = np.minimum(best[ok], d)
    return best


def faces(made: list[dict[str, float]], pu: Any, pv: Any, pn: Any, slack: float) -> tuple[Any, Any]:
    """Of points in the frame: (the number of the gap between two faces the point is in, counted along n; whether
    the point is surely inside the rectangle of the faces around it or surely outside every vein: −1 — unsure).
    A face is at k·GAP ± THICK / 2; `slack` — metres from a face or an edge within which nothing is sure."""
    import numpy as np

    ks = np.array([p["k"] for p in made])
    box = {key: np.array([p[key] for p in made]) for key in ("u0", "u1", "v0", "v1")}
    k = np.rint(pn / GAP).astype(np.int64)
    off = pn - k * GAP
    gap = 2 * k + (off > 0.5 * THICK) - (off < -0.5 * THICK)  # odd — between two veins, even — inside the vein k
    i = np.searchsorted(ks, k)
    there = (i < len(ks)) & (ks[np.minimum(i, len(ks) - 1)] == k)
    i = np.minimum(i, len(ks) - 1)
    edge = np.minimum(np.minimum(pu - box["u0"][i], box["u1"][i] - pu),
                      np.minimum(pv - box["v0"][i], box["v1"][i] - pv))  # fmt: skip
    sure = (np.abs(np.abs(off) - 0.5 * THICK) > slack) & there & (edge > slack)
    return gap, sure


def store_veins(params: dict[str, Any]) -> _samples.More:
    """`more` of `_samples.build`: the wireframes written into the project as an import writes them, and the
    table «Пласт» — the interval of every vein in every hole that goes through it."""

    def more(meta: dict[str, Any], engine: Any, folder: Path) -> dict[str, Any]:
        from bf_core.db import dialect
        from bf_core.storage.blobs import BlobStore
        from bf_core.wireframes import store as wf_store

        blobs = BlobStore(folder)
        stored = []
        for body in veins(meta["holes"], int(params.get("triangles", 4000)), int(params.get("bodies", 4))):
            staged = wf_store.stage_wireframe(blobs, body["xyz"], body["tri"])
            try:
                with dialect.write_tx(engine) as conn:
                    wf = int(wf_store.commit_wireframe(conn, blobs, staged, name=body["name"]))
            finally:
                staged.discard()
            stored.append({"id": wf, "name": body["name"], "triangles": len(body["tri"])})
            del body, staged
            gc.collect()
        return {"wireframes": stored, **layer(meta, engine, folder)}

    return more


def layer(meta: dict[str, Any], engine: Any, folder: Path) -> dict[str, Any]:
    """The table «Пласт»: [entry; exit] of every vein in every hole that goes straight through its two faces —
    the depths where the path (`Trajectories.positions`) is on the planes of the faces, by bisection."""
    import numpy as np

    from bf_core.composites import source
    from bf_core.drillholes import store

    fr, made = frame(meta["holes"]), plan(meta["holes"])
    st = source.trajectories(_samples.service(engine, folder).require_current(), meta["set_id"])
    traj, depth = st.traj, float(meta["rows"] // meta["holes"])
    have = np.flatnonzero(np.isfinite(traj.lengths()))
    grid = np.linspace(0.0, depth, 41)
    along = np.stack([local(fr, traj.positions(have, np.full(len(have), s)))[2] for s in grid], axis=1)
    step = np.diff(along, axis=1)
    one_way = (step > 0.0).all(axis=1) | (step < 0.0).all(axis=1)
    have, top, end = have[one_way], along[one_way, 0], along[one_way, -1]
    lo_n, hi_n = np.minimum(top, end), np.maximum(top, end)
    holes, levels = [], []
    for p in made:
        faces_n = (p["k"] * GAP - 0.5 * THICK, p["k"] * GAP + 0.5 * THICK)
        through = np.flatnonzero((lo_n < faces_n[0]) & (hi_n > faces_n[1]))
        holes.append(np.repeat(have[through], 2))
        levels.append(np.tile(faces_n, len(through)))
    hole, level = np.concatenate(holes), np.concatenate(levels)
    a, b = np.zeros(len(hole)), np.full(len(hole), depth)
    rising = (end > top)[np.searchsorted(have, hole)]
    for _ in range(60):
        mid = 0.5 * (a + b)
        below = (local(fr, traj.positions(hole, mid))[2] < level) == rising
        a, b = np.where(below, mid, a), np.where(below, b, mid)
    at = 0.5 * (a + b)
    pu, pv, _pn = local(fr, traj.positions(hole, at))
    box = {key: np.repeat([p[key] for p in made], [len(h) for h in holes]) for key in ("u0", "u1", "v0", "v1")}
    inside = (pu > box["u0"] + 5.0) & (pu < box["u1"] - 5.0) & (pv > box["v0"] + 5.0) & (pv < box["v1"] - 5.0)
    ok = inside.reshape(-1, 2).all(axis=1)
    pair = at.reshape(-1, 2)[ok]
    frm, to, who = pair.min(axis=1), pair.max(axis=1), hole.reshape(-1, 2)[ok, 0]
    order = np.lexsort((frm, who))
    names = np.array(st.names, dtype=object)[who[order]].tolist()
    data = store.TableData(LAYER, "interval", names, frm[order], to[order], [store.ColumnSpec("N", "number")],
                           [np.ones(len(order))])  # fmt: skip
    table = int(store.write_table(engine, meta["set_id"], data, job_id=None, tick=_samples._noop)["table_id"])
    _samples.snapshot(engine, folder, meta["set_id"], [table])
    return {"layer": table, "layer_rows": len(order)}


# --- the reference of «Пробы» ---------------------------------------------------------------------------------------


def keep_reference(work: Path, meta: dict[str, Any], engine: Any, folder: Path, d: dict[str, Any]) -> None:
    """`work/ref_*.npy` of the samples: TT with the plane of the veins; the distance from the middle of a sample
    to the veins; whether its ends are surely on the two sides of a face (1), surely between the same two (0),
    or neither is sure (−1)."""
    import numpy as np

    from bf_core.composites import source

    fr, made = frame(meta["holes"]), plan(meta["holes"])
    st = source.trajectories(_samples.service(engine, folder).require_current(), meta["set_id"])
    index = {name: i for i, name in enumerate(st.names)}
    dense = np.array([index.get(name, -1) for name in d["names"]], dtype=np.int64)[d["a_hole"]]
    frm, to = d["a_from"], d["a_to"]
    np.save(work / "ref_from.npy", frm)
    np.save(work / "ref_to.npy", to)
    np.save(work / "ref_plane.npy", tt_ref.thickness_reference(d, fr["n"]))
    far = np.full(len(frm), np.nan)
    crossing = np.full(len(frm), -1, dtype=np.int8)
    for lo in range(0, len(frm), tt_ref.CHUNK):
        part = slice(lo, lo + tt_ref.CHUNK)
        mid = traj_local(fr, st.traj, dense[part], 0.5 * (frm[part] + to[part]))
        far[part] = distance(made, *mid)
        gap_a, sure_a = faces(made, *traj_local(fr, st.traj, dense[part], frm[part]), SURE)
        gap_b, sure_b = faces(made, *traj_local(fr, st.traj, dense[part], to[part]), SURE)
        both = sure_a & sure_b
        crossing[part] = np.where(both, (gap_a != gap_b).astype(np.int8), -1)
    lost = np.isnan(d["x"])[d["a_hole"]]
    assert np.array_equal(lost, ~np.isfinite(st.traj.lengths())[np.maximum(dense, 0)] | (dense < 0))
    far[lost], crossing[lost] = np.nan, -1
    np.save(work / "ref_far.npy", far)
    np.save(work / "ref_crossing.npy", crossing)


def traj_local(fr: dict[str, Any], traj: Any, dense: Any, depth: Any) -> tuple[Any, Any, Any]:
    """`local` of the points of the paths at the depths; a hole without a trajectory — far away (masked later)."""
    import numpy as np

    return local(fr, np.nan_to_num(traj.positions(dense, depth), nan=0.0))


# --- the jobs -----------------------------------------------------------------------------------------------------


def body(meta: dict[str, Any], which: str) -> dict[str, Any]:
    table = meta["samples"] if which == "mesh" else meta["layer"]
    source = {"source": "mesh", "wireframe_ids": [w["id"] for w in meta["wireframes"]], "d_max": D_MAX, "r_n": 0}
    return {"set_id": meta["set_id"], "table": str(table), "orientation": [source], "eps": tt_ref.EPS,
            "step": tt_ref.STEP if which == "mesh" else PLATE_STEP, "angles": True}  # fmt: skip


def job(meta: dict[str, Any], engine: Any, folder: Path, which: str, job_id: int = 1) -> tuple[Any, ...]:
    """`true_thickness.job` with the clocks of the wireframes: their arrays, their trees, the crossings, the
    nearest triangles, `mesh_orientation` as a whole."""
    from bf_core.intercepts import orient_mesh
    from bf_core.wireframes import exact

    spots = ((exact, "geometry", "geometry"), (exact.Geometry, "target", "tree"),
             (orient_mesh, "_add_crossings", "cross"), (orient_mesh, "nearest_triangles", "nearest"),
             (orient_mesh, "mesh_orientation", "mesh"))  # fmt: skip
    rows = meta["rows"] if which == "mesh" else meta["layer_rows"]
    return tt_ref.job({**meta, "rows": rows}, engine, folder, body(meta, which), job_id, spots,
                      sum(w["triangles"] for w in meta["wireframes"]))  # fmt: skip


def job_mesh(project: Any) -> Any:
    from bf_core.intercepts import jobs

    return jobs.job_mesh(project, _samples._noop)


def check_rows(work: Path, t: Any, lines: Any) -> dict[str, Any]:
    """The rows of «Пробы» the kernel made against the references of `prepare` (module docstring)."""
    import numpy as np

    from bf_core.intercepts import orient
    from bf_core.intercepts.formulas import FLAG_NO_ORIENTATION, FLAG_NO_PATH, FLAG_PARALLEL

    assert np.array_equal(lines.from_m, np.load(work / "ref_from.npy"))
    assert np.array_equal(lines.to_m, np.load(work / "ref_to.npy"))
    far, crossing = np.load(work / "ref_far.npy"), np.load(work / "ref_crossing.npy")
    crossed, nearest = t.src == orient.SRC_MESH_CROSSING, t.src == orient.SRC_MESH_NEAREST
    path = ~np.isnan(far)
    none = t.src == orient.SRC_NONE
    assert (crossed | nearest | none).all() and crossed.any() and nearest.any() and none[path].any()
    assert none[~path].all() and ((t.flag[~path] & FLAG_NO_PATH) != 0).all() and (~path).any()
    assert np.array_equal(none[path], (t.flag[path] & FLAG_NO_ORIENTATION) != 0)
    assert not (t.flag[path] & ~np.uint8(FLAG_NO_ORIENTATION | FLAG_PARALLEL)).any()
    assert np.array_equal(np.isnan(t.tt), none) and not (t.tt[~none] <= 0.0).any()  # empty — NaN, never 0
    assert crossed[path & (crossing == 1)].all() and not crossed[path & (crossing == 0)].any()
    missed = int(np.count_nonzero(none & path & (far < D_MAX - SURE)))
    extra = int(np.count_nonzero(nearest & (far > D_MAX + SURE)))
    odd = int(np.count_nonzero(~none & ((np.abs(t.alpha - ALPHA) > 1e-6) | (np.abs(t.dipdir - DIPDIR) > 1e-6))))
    assert missed == extra == odd == 0, (missed, extra, odd)
    ref = np.load(work / "ref_plane.npy")
    error = float(np.abs(t.tt[~none] - ref[~none]).max())
    assert error <= 1e-6, error
    n = int(path.sum())
    return {"tt_error_m": error, "missed": missed, "extra": extra, "odd": odd,
            "share_crossing": round(float(crossed.sum()) / n, 4), "share_nearest": round(float(nearest.sum()) / n, 4),
            "share_none": round(float(none[path].sum()) / n, 4), "no_path": int((~path).sum()),
            "farthest_m": round(float(far[nearest].max()), 3)}  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    meta = _samples.build(work, params, more=store_veins(params))
    meta, engine, folder = _samples.opened(work)
    try:
        keep_reference(work, meta, engine, folder, tt_ref.generated(params))
    finally:
        engine.dispose()


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        calls = job(meta, engine, folder, "mesh")[3]
        tt_ref.threads_hashes(calls[0], (1, 2), job_mesh)
        job(meta, engine, folder, "plate", job_id=2)

    _samples.warm(once, more=store_veins({}))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder, restore_s = _samples.fresh(work)
    triangles = sum(w["triangles"] for w in meta["wireframes"])
    try:
        out, took, ticks, calls, read_from = job(meta, engine, folder, "mesh")
        info = tt_ref.timings("mesh", out, took, ticks, CLOCKS)
        report = _samples.report_of(engine, out["result_id"])
        assert read_from == [_samples.read_from(meta["rows"])], read_from
        assert not out["fresh"] and out["rows"] == report["rows"] == meta["rows"], out["rows"]
        (_spec, lines, *_rest), _kw, made = calls[0]
        info.update(check_rows(work, made, lines))
        of_mesh = report["sources"][0]
        assert of_mesh["wireframes"] == len(meta["wireframes"]) and of_mesh["triangles"] == triangles, of_mesh
        info["nearest_us"] = round(1e6 * took["nearest"] / max(1, of_mesh["asked"]), 3)
        found, kernels = tt_ref.threads_hashes(calls[0], tuple(params.get("threads", (1, 4))), job_mesh)
        t0 = time.perf_counter()
        tt_ref.check_fields(meta, engine, made)
        info.update(kernels, hashes=len(found), hash=sorted(found)[0][:16], check_s=round(time.perf_counter() - t0, 3))
        if "kernel_t1_s" in kernels:
            info["mesh_t1_s"] = round(info["mesh_s"] - info["mesh_kernel_s"] + kernels["kernel_t1_s"], 3)
        del calls, lines, made
        gc.collect()
        plate_out, plate_took, _ticks, plate_calls, _from = job(meta, engine, folder, "plate", job_id=2)
        thick = plate_calls[0][2].tt
        assert len(thick) == meta["layer_rows"] > 0 and not plate_out["fresh"]
        info.update(plate_error_m=float(abs(thick - THICK).max()), plate_rows=len(thick),
                    plate_s=round(plate_took["done"], 3))  # fmt: skip
        assert info["plate_error_m"] <= 1e-6, info["plate_error_m"]
    finally:
        engine.dispose()
    info.update(rows=meta["rows"], triangles=triangles, wireframes=len(meta["wireframes"]),
                restore_s=round(restore_s, 3))  # fmt: skip
    return info
