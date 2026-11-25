"""What the scenarios `implicit_*` share (ADR 0026 «Замеры», подшаг 19.27; ТЗ 8.9.4, 8.0.2).

    sc = Scene.of(params)                  folded layers pierced by vertical holes (by seeds); `holes` of them
    pts = sc.domain_points(k)              the data points of the domain k of the scene as `model.data` makes them
                                           (`data_holes.domain_points`: no project, no database) → (xyz, f)
    meta = write_project(work, sc, ...)    the project folder: the set, the table of codes «Литология», the table of
                                           grades «Пробы» (AU) → ids, `meta` of `keep` / `read_meta`
    ctx = Ctx.open(work)                   (`blocks_common.Ctx`) the same project in the child process
    mid = create_model(ctx, meta, name, groups, h=…) / run_job(ctx, mid, update=…, threads=…)
    domains(meta, n) / shell(meta, gid, thresholds)   the groups of the plan; fingerprint(ctx, mid) the bytes of the
                                           outputs and of the files (the bits of two runs, Т8.9-8)
    limit_threads(params) / peak_mb()      as the scenarios of the blocks and of the estimation do

The scene is the kind of data a real base has and a clean grid has not: holes on a jittered grid (collars at ±2 m, a
depth of 200 m ±2 m), five layers L1 … L5 one under another (40 m each) between folded interfaces — the harmonics of
two wavelengths and an amplitude that falls with the depth, so the interfaces are never nearer than 24 m — in the
coordinates of a mine (500 000 / 7 000 000, the numbers of a real project, not of a test), contacts rounded to a
centimetre as any table of a project has them. The grades of «Пробы» are a lognormal field with a smooth trend and
a nugget, 3 % of the samples unassayed (a missing grade is no point). Everything the scenarios check is a function of
the scene and of the seeds: the same numbers every run, in the child process and in `prepare`.

What a scenario checks (a failure stops the run) is written in its docstring; the figures that go to the report are
in `info`, those with a limit are named in `budgets.json`."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

CORNER = (500_000.0, 7_000_000.0)  # of the grid of holes, m
TOP = 300.0  # the level of the collars, m
LAYERS = 5  # the domains of the scene
THICK = 40.0  # the mean thickness of a layer, m
AMP = 8.0  # the amplitude of the fold of the first interface, m
SAMPLE = 2.0  # the length of an assay sample, m
D_MAX, STEP = 30.0, 5.0  # the reach and the step of the points along a hole (8.9.4)
NOISE = 0.15  # the nugget of the log grade
NAN_SHARE = 0.03  # the samples not assayed
KEEP = "implicit.json"  # the facts `prepare` made (`read_meta` of the child)
Tick = Callable[[float | None, str | None], None]


def limit_threads(params: dict[str, Any]) -> int:
    return bc.limit_threads(params)


def peak_mb() -> float | None:
    return bc.peak_mb()


def noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


# --- the scene --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scene:
    """`holes` vertical holes on a jittered square grid through `LAYERS` layers."""

    holes: int
    seed: int = 12345
    spacing: float = 40.0

    @classmethod
    def of(cls, params: dict[str, Any]) -> Scene:
        return cls(int(params["holes"]), int(params.get("seed", 12345)), float(params.get("spacing", 40.0)))

    @property
    def side(self) -> int:
        return max(2, int(math.ceil(math.sqrt(self.holes))))

    @property
    def n_holes(self) -> int:
        return self.side * self.side

    @property
    def box(self) -> list[float]:
        """The boundary of the model: the grid and half a spacing beyond it, a few metres over and under the holes."""
        pad = 0.75 * self.spacing
        far = self.side * self.spacing
        return [CORNER[0] - pad, CORNER[1] - pad, TOP - LAYERS * THICK - 12.0, CORNER[0] + far + pad,
                CORNER[1] + far + pad, TOP + 12.0]  # fmt: skip

    def interface(self, k: int, x: Any, y: Any) -> Any:
        """The elevation of the interface k (1 … LAYERS − 1: under the layer k)."""
        x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        a = AMP * (1.0 - 0.12 * k)
        return (TOP - k * THICK + a * np.sin(2 * np.pi * x / 520.0 + 0.5 * k) * np.cos(2 * np.pi * y / 710.0)
                + 0.4 * a * np.sin(2 * np.pi * (x - 0.5 * y) / 230.0))  # fmt: skip

    def collars(self) -> dict[str, np.ndarray]:
        rng = np.random.default_rng(self.seed)
        n, s = self.n_holes, self.spacing
        k = np.arange(n)
        x = CORNER[0] + (k % self.side + 0.5) * s + rng.uniform(-0.2 * s, 0.2 * s, n)
        y = CORNER[1] + (k // self.side + 0.5) * s + rng.uniform(-0.2 * s, 0.2 * s, n)
        z = TOP + rng.uniform(-2.0, 2.0, n)
        td = (z - (TOP - LAYERS * THICK)) + rng.uniform(-2.0, 2.0, n)
        return {"x": x, "y": y, "z": z, "td": np.round(td, 1)}

    def litho(self) -> dict[str, np.ndarray]:
        """The rows of «Литология»: (hole, from, to, layer 0 … LAYERS − 1), the contacts rounded to a centimetre."""
        c = self.collars()
        n = c["x"].size
        cut = np.zeros((n, LAYERS + 1))
        for k in range(1, LAYERS):
            cut[:, k] = np.round(c["z"] - self.interface(k, c["x"], c["y"]), 2)
        cut[:, LAYERS] = c["td"]
        hole = np.repeat(np.arange(n), LAYERS)
        layer = np.tile(np.arange(LAYERS), n)
        return {"hole": hole, "from": cut[:, :-1].ravel(), "to": cut[:, 1:].ravel(), "layer": layer}

    def grades(self) -> dict[str, np.ndarray]:
        """The rows of «Пробы»: samples of SAMPLE metres along the holes; AU is NaN where it is not assayed."""
        c = self.collars()
        rng = np.random.default_rng(self.seed + 1)
        per = (c["td"] // SAMPLE).astype(np.int64)
        hole = np.repeat(np.arange(c["x"].size), per)
        first = np.cumsum(per) - per
        k = np.arange(hole.size) - np.repeat(first, per)
        frm, to = k * SAMPLE, (k + 1) * SAMPLE
        x, y = c["x"][hole] - CORNER[0], c["y"][hole] - CORNER[1]
        z = c["z"][hole] - 0.5 * (frm + to)
        smooth = (0.9 * np.sin(2 * np.pi * x / 380.0) * np.cos(2 * np.pi * y / 520.0)
                  + 0.7 * np.sin(2 * np.pi * (z - TOP) / 150.0)
                  + 0.4 * np.cos(2 * np.pi * (x + y) / 700.0))  # fmt: skip
        au = 0.8 * np.exp(smooth + NOISE * rng.normal(0.0, 1.0, hole.size))
        au[rng.random(hole.size) < NAN_SHARE] = np.nan
        return {"hole": hole, "from": frm, "to": to, "au": au}

    # --- the data points the model makes of the codes

    def domain_points(self, k: int, *, d_max: float = D_MAX, step: float = STEP, threads: int = 1) -> tuple[Any, Any]:
        """The points of the domain `k` (0 … LAYERS − 1; the layer L(k + 1)) → (xyz [n, 3], f [n]) in the canonical
        order of `model.data` (lexicographic x, y, z, f)."""
        from bf_core.composites import intervals as iv
        from bf_core.drillholes.desurvey import Collars, desurvey
        from bf_core.model import data_holes as dhm

        c, li = self.collars(), self.litho()
        n = c["x"].size
        traj = desurvey(Collars(np.arange(n), c["x"], c["y"], c["z"], c["td"], np.zeros(n), np.full(n, -90.0)),
                        n_holes=n)  # fmt: skip
        lines = iv.build_lines(li["hole"], li["from"], li["to"])
        state = (li["layer"] == k).astype(np.int8)
        names = [f"H{i + 1:05d}" for i in range(n)]
        p = dhm.domain_points(lines, state, traj, np.arange(n), d_max=d_max, step=step, group=k + 1, names=names,
                              threads=threads)  # fmt: skip
        order = np.lexsort((p.f, p.xyz[:, 2], p.xyz[:, 1], p.xyz[:, 0]))
        return np.ascontiguousarray(p.xyz[order]), np.ascontiguousarray(p.f[order])


# --- the project ------------------------------------------------------------------------------------------------------


def write_project(work: Path, sc: Scene, *, assays: bool = True, thresholds: tuple[float, ...] = (0.7, 0.9, 0.97),
                  litho: dict[str, np.ndarray] | None = None) -> dict[str, Any]:  # fmt: skip
    """The project folder in `work`: the set with the collars, «Литология» (CODE) and, with `assays`, «Пробы» (AU).
    `thresholds` are quantiles of the grades (the thresholds of the shells). → `meta` (ids, the box, the counts);
    `keep(work, meta)` stores it for the child process."""
    from bf_core.db import dialect
    from bf_core.drillholes import store as dh

    ctx = bc.Ctx.create(work)
    try:
        c = sc.collars()
        names = np.array([f"H{i + 1:05d}" for i in range(c["x"].size)], dtype=object)
        rows = [dh.CollarRow(str(n), x=float(x), y=float(y), z=float(z), td=float(t), azimuth=0.0, dip=-90.0)
                for n, x, y, z, t in zip(names, c["x"], c["y"], c["z"], c["td"], strict=True)]  # fmt: skip
        with dialect.write_tx(ctx.engine) as conn:
            set_id = dh.create_set(conn, "Скважины")
            dh.replace_collars(conn, set_id, rows)
        meta: dict[str, Any] = {"set_id": set_id, "holes": int(names.size), "box": sc.box,
                                "params": {"holes": sc.holes, "seed": sc.seed, "spacing": sc.spacing}}
        meta["litho"] = write_litho(ctx, set_id, names, litho or sc.litho())
        if assays:
            g = sc.grades()
            data = dh.TableData(name="Пробы", kind="assay", holes=names[g["hole"]].tolist(), from_m=g["from"],
                                to_m=g["to"], columns=[dh.ColumnSpec("AU", "number", "г/т", "component")],
                                values=[g["au"].tolist()])  # fmt: skip
            meta["assays"] = int(dh.write_table(ctx.engine, set_id, data, job_id=None, tick=noop)["table_id"])
            ok = g["au"][~np.isnan(g["au"])]
            meta["thresholds"] = [round(float(np.quantile(ok, q)), 4) for q in thresholds]
            meta["samples"] = int(ok.size)
        return meta
    finally:
        ctx.close()


def write_litho(ctx: Any, set_id: int, names: np.ndarray, li: dict[str, np.ndarray], name: str = "Литология") -> int:
    """The table of codes «Литология» of the rows `li` (what `Scene.litho` and `edit_litho` give) → its id."""
    from bf_core.drillholes import store as dh

    codes = np.array([f"L{i + 1}" for i in range(LAYERS)], dtype=object)
    data = dh.TableData(name=name, kind="interval", holes=names[li["hole"]].tolist(), from_m=li["from"],
                        to_m=li["to"], columns=[dh.ColumnSpec("CODE", "text")],
                        values=[codes[li["layer"]].tolist()])  # fmt: skip
    return int(dh.write_table(ctx.engine, set_id, data, job_id=None, tick=noop)["table_id"])


def edit_litho(sc: Scene, share: float, shift: float = 3.0, *, scatter: bool = False) -> dict[str, np.ndarray]:
    """The rows of «Литология» after a campaign: the contacts of `share` of the holes moved by `shift` metres. The
    holes are the nearest to the corner of the grid (new holes of a campaign are neighbours) or, `scatter`, spread over
    the whole grid (every k-th). The bounds of the neighbours of the moved contact move with it."""
    li = sc.litho()
    n = sc.n_holes
    count = max(1, int(round(share * n)))
    if scatter:
        pick = np.linspace(0, n - 1, count).astype(np.int64)
    else:
        c = sc.collars()
        pick = np.argsort((c["x"] - CORNER[0]) ** 2 + (c["y"] - CORNER[1]) ** 2, kind="stable")[:count]
    edit = np.zeros(n, dtype=bool)
    edit[pick] = True
    frm, to = li["from"].copy(), li["to"].copy()
    inner = np.flatnonzero(edit[li["hole"]] & (li["layer"] < LAYERS - 1) & (li["layer"] % 2 == 0))
    to[inner] = np.round(to[inner] + shift, 2)  # the contact under the layer moves down
    frm[inner + 1] = to[inner]
    return {**li, "from": frm, "to": to}


def keep(work: Path, meta: dict[str, Any]) -> None:
    import json

    (work / KEEP).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def read_meta(work: Path) -> dict[str, Any]:
    import json

    return dict(json.loads((work / KEEP).read_text(encoding="utf-8")))


# --- the model and the job ----------------------------------------------------------------------------------------


def domains(meta: dict[str, Any], n: int = LAYERS) -> list[dict[str, Any]]:
    """The first `n` layers as domains, the youngest (the top one) first."""
    return [{"id": i + 1, "name": f"L{i + 1}", "type": "domain", "codes": [f"L{i + 1}"]} for i in range(n)]


def shell(meta: dict[str, Any], gid: int, thresholds: list[float] | None = None, **params: Any) -> dict[str, Any]:
    given = {"source": "table", "table": str(meta["assays"]), "field": "AU",
             "thresholds": thresholds or list(meta["thresholds"]), **params}  # fmt: skip
    return {"id": gid, "name": f"Au {gid}", "type": "shell", "codes": [], "params": given}


def create_model(ctx: Any, meta: dict[str, Any], name: str, groups: list[dict[str, Any]], *, h: float | None,
                 table: int | None = None, **over: Any) -> int:  # fmt: skip
    """A model of `groups` in the box of the scene with the step `h` (the key `data` reads «Литология» or `table`)."""
    from bf_core.db import dialect
    from bf_core.model import store as st
    from bf_core.model.spec import parse_model

    data = {"set_id": meta["set_id"], "table": str(table or meta["litho"]), "field": "CODE", "d_max": D_MAX,
            "step": STEP}  # fmt: skip
    body = {"name": name, "boundary": {"box": meta["box"]}, "h": h, "groups": groups, "manual": [], "data": data,
            **over}  # fmt: skip
    with dialect.write_tx(ctx.engine) as conn:
        return int(st.create(conn, ctx.store, ctx.project, parse_model(body))["id"])


def point_at(ctx: Any, mid: int, meta: dict[str, Any], table: int) -> None:
    """The model reads its codes from `table` now (a PUT of the window: the version goes up)."""
    from bf_core.db import dialect
    from bf_core.model import store as st

    data = {"set_id": meta["set_id"], "table": str(table), "field": "CODE", "d_max": D_MAX, "step": STEP}
    with dialect.write_tx(ctx.engine) as conn:
        st.update(conn, ctx.store, ctx.project, mid, {"data": data}, version(ctx, mid))


def version(ctx: Any, mid: int) -> int:
    from bf_core.db import dialect
    from bf_core.model import store_base as sb

    with dialect.read_tx(ctx.engine) as conn:
        return int(sb.stored(conn, mid).version)


def run_job(ctx: Any, mid: int, *, update: bool = False, threads: int = 1, tick: Tick | None = None,
            **params: Any) -> dict[str, Any]:  # fmt: skip
    """The body of `implicit.build` / `implicit.update` as the handler calls it → the answer of the job."""
    from bf_core.model import job

    p = {"model_id": mid, "expected_version": version(ctx, mid), "threads": threads, **params}
    return dict(job.run_build(ctx.engine, ctx.folder, p, tick or noop, update=update))


def fingerprint(ctx: Any, mid: int) -> dict[str, Any]:
    """What two runs of the same model must have in common bit for bit: the vertices and triangles of every output
    (its name without that of the model), the sha256 of the files of the model."""
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import blob_owners
    from bf_core.model import store as st
    from bf_core.model import store_base as sb
    from bf_core.wireframes import store as wfs

    with dialect.read_tx(ctx.engine) as conn:
        outs = st.outputs_of(conn, sb.stored(conn, mid).meta)
        geo = []
        for o in outs:
            v, t = wfs.read_wireframe(conn, ctx.store, o["wireframe_id"])
            tag = o["name"].split(" · ", 1)[-1]
            geo.append((tag, hashlib.sha256(v.tobytes()).hexdigest(), hashlib.sha256(t.tobytes()).hexdigest()))
        files = {str(r.role): str(r.sha256) for r in conn.execute(sa.select(blob_owners).where(
            blob_owners.c.owner_kind == sb.OWNER, blob_owners.c.owner_id == mid))}  # fmt: skip
    return {"geometry": geo, "files": files}


def seconds_of(fn: Callable[[], Any]) -> tuple[float, Any]:
    t0 = time.perf_counter()
    out = fn()
    return time.perf_counter() - t0, out
