"""A world for the tests of the job `estimate.run` (подшаг 18.12): a set of 49 vertical holes with 20 samples each (AU,
text zones «окисл» above 20 m and «перв» below), a variogram model, a plain block model and one with sub-blocks, both
with the field DOM (руда — x < 36, порода). Small enough to estimate in a second, big enough for every pass."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.blocks import geometry
from bf_core.blocks import store as bm
from bf_core.blocks.model import Model
from bf_core.db import dialect, migrate
from bf_core.drillholes import store as dh
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate import samples as sm
from bf_core.geostat.estimate import spec as es
from bf_core.geostat.estimate import store as st
from bf_core.geostat.estimate import store_base as sb
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.estutil import vmodel

MODEL = {"type": "semivariogram", "c0": 0.1, "common_axes": True,
         "structures": [{"type": "spherical", "c": 0.9, "ang1": 0, "ang2": 0, "ang3": 0, "a1": 40, "a2": 40,
                         "a3": 25}]}  # fmt: skip
GRID = {"corner": [-6.0, -6.0, 60.0], "counts": [14, 14, 10], "size": [6.0, 6.0, 4.0], "rotation": 0}
GRID_SUB = {"corner": [-6.0, -6.0, 60.0], "counts": [7, 7, 5], "size": [12.0, 12.0, 8.0], "rotation": 20,
            "div": [2, 2, 2]}  # fmt: skip
FIELDS = [{"name": "DOM", "dtype": "cat", "labels": ["руда", "порода"], "role": "code"}]
XCUT = 36.0  # руда — the centre of the block west of it


@dataclass
class JW:
    project: Any
    store: BlobStore
    set_id: int
    a: int
    b: int
    model: int  # plain
    sub: int  # with sub-blocks
    vz: int


def noop(_p: float | None, _m: str | None) -> None:
    pass


def fill_set(engine: sa.Engine) -> tuple[int, int, int]:
    rng = np.random.default_rng(11)
    holes = [(f"H{i}-{j}", 12.0 * i, 12.0 * j) for i in range(7) for j in range(7)]
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, [dh.CollarRow(h, x=x, y=y, z=100.0, td=40.0, azimuth=0.0, dip=-90.0)
                                          for h, x, y in holes])  # fmt: skip
    names, lo, hi, au = [], [], [], []
    for h, x, y in holes:
        for k in range(20):
            names.append(h)
            lo.append(2.0 * k)
            hi.append(2.0 * k + 2.0)
            v = 1.0 + 0.5 * np.sin(x / 20.0) + 0.5 * np.cos(y / 15.0) + 0.3 * np.sin(k / 3.0) + rng.lognormal(-2, 0.5)
            au.append(None if rng.random() < 0.03 else float(v))
    a = dh.write_table(engine, set_id, dh.TableData(
        name="Пробы", kind="assay", holes=names, from_m=lo, to_m=hi,
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component")], values=[au]), job_id=None, tick=noop)  # fmt: skip
    lh, lf, lt, lz = [], [], [], []
    for h, _x, _y in holes:
        for f, t, z in ((0.0, 20.0, "окисл"), (20.0, 40.0, "перв")):
            lh.append(h)
            lf.append(f)
            lt.append(t)
            lz.append(z)
    b = dh.write_table(engine, set_id, dh.TableData(
        name="Литология", kind="interval", holes=lh, from_m=lf, to_m=lt,
        columns=[dh.ColumnSpec("ZONE", "text")], values=[lz]), job_id=None, tick=noop)  # fmt: skip
    return set_id, int(a["table_id"]), int(b["table_id"])


def _model(project: Any, store: BlobStore, grid: dict[str, Any], name: str) -> int:
    from bf_core.blocks.schema import GridV2

    g = GridV2.of(grid)
    parents = g.cells
    if g.sub.sub:
        ps, ss, ws = [], [], []
        for par in range(parents):
            if par % 3 == 0:
                ps.append(par)
                ss.append((0, 0, 0))
                ws.append((2, 2, 2))
            else:
                for c in range(8):  # (sz, sy, sx) ascending
                    ps.append(par)
                    ss.append((c & 1, (c >> 1) & 1, c >> 2))
                    ws.append((1, 1, 1))
        p = np.asarray(ps, np.uint64)
        s, w = np.asarray(ss, np.int64), np.asarray(ws, np.int64)
        centre = geometry.centres(g, p, s, w)
    else:
        p, s, w = np.arange(parents, dtype=np.uint64), None, None
        centre = geometry.centres(g, p)
    dom = np.where(centre[:, 0] < XCUT, 0, 1).astype(np.int32)
    with bm.CellsWriterV2(store, grid, FIELDS, row_group=700) as wr:
        half = len(p) // 2
        wr.write(p[:half], {"DOM": dom[:half]}, None if s is None else s[:half], None if w is None else w[:half])
        wr.write(p[half:], {"DOM": dom[half:]}, None if s is None else s[half:], None if w is None else w[half:])
        staged = wr.finish()
    try:
        with dialect.write_tx(project.engine) as conn:
            return bm.commit_block_model(conn, store, staged, name=name)
    finally:
        staged.discard()


def world(tmp_path: Path, url: str = "sqlite") -> JW:
    engine = make_engine(url, tmp_path)
    migrate.upgrade(engine, "project")
    return fill(SimpleNamespace(engine=engine, uid=f"job:{tmp_path}:{url}", folder=tmp_path))


def fill(project: Any) -> JW:
    """The set, the models and the variogram model in a project that is open already (`engine`, `folder`)."""
    set_id, a, b = fill_set(project.engine)
    store = BlobStore(Path(project.folder))
    jw = JW(project, store, set_id, a, b, 0, 0, 0)
    jw.model = _model(project, store, GRID, "Плоская")
    jw.sub = _model(project, store, GRID_SUB, "Субблоки")
    jw.vz = vmodel(jw, "Золото Z", model=MODEL)  # type: ignore[arg-type]  # the helper needs these attributes only
    return jw


def body(jw: JW, *, model: int | None = None, label: str = "руда", zone: str = "окисл", **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model_id": jw.model if model is None else model,
        "target": {"domain_field": "DOM", "label": label},
        "samples": {"set_id": jw.set_id, "table": str(jw.a), "field": "AU",
                    "domain": {"table": str(jw.b), "field": "ZONE", "values": [zone]}},
        "variogram": {"model_id": jw.vz}, "disc": [2, 2, 2]}  # fmt: skip
    out.update(over)
    return out


def make(jw: JW, **over: Any) -> int:
    spec = es.parse_estimation(body(jw, **over))
    with dialect.write_tx(jw.project.engine) as conn:
        return int(st.create(conn, jw.store, jw.project, spec)["id"])


def plan(jw: JW, items: list[int], model: int | None = None) -> int:
    with dialect.write_tx(jw.project.engine) as conn:
        return int(st.plan_create(conn, es.parse_plan({"model_id": jw.model if model is None else model,
                                                       "items": items}))["id"])  # fmt: skip


def put(jw: JW, ident: int, **raw: Any) -> None:
    with dialect.read_tx(jw.project.engine) as conn:
        version = int(sb.stored(conn, sb.KIND, ident).version)
    with dialect.write_tx(jw.project.engine) as conn:
        st.update(conn, jw.store, jw.project, ident, raw, version)


def version_of(jw: JW, model: int | None = None) -> int:
    with dialect.read_tx(jw.project.engine) as conn:
        return Model.open(conn, jw.store, jw.model if model is None else model).version


def open_model(jw: JW, model: int | None = None) -> Model:
    with dialect.read_tx(jw.project.engine) as conn:
        return Model.open(conn, jw.store, jw.model if model is None else model)


def direct(jw: JW, ident: int, model: int | None = None, *, own: bool = False) -> dict[str, np.ndarray]:
    """What the kernel gives for every row of the model alone (no job): the columns by key, NaN where it gives none.
    `sub: parent` — the parent's centre; `own` — the sub-block's centre and size."""
    from bf_core.geostat.estimate import store_check as sc

    with dialect.read_tx(jw.project.engine) as conn:
        spec = sb.load_spec(sb.stored(conn, sb.KIND, ident).meta or {})
        m = Model.open(conn, jw.store, jw.model if model is None else model)
        if spec.search.source == "trend" and spec.trend_id is not None:
            spec = spec.resolved(trend=sc.trend_form(conn, spec.trend_id))
    samples = sm.load(jw.project, spec)
    grid = m.grid
    rk = kn.compile_row(spec, samples, block_size=grid.regular(), rotation_deg=grid.rotation)
    p, sw = m.p(), m.subs()
    s, w = (None, None) if sw is None else sw
    if own and s is not None:
        res = kn.estimate(rk, geometry.centres(grid, p, s, w), sizes=geometry.row_widths(grid, p, s, w))
        return dict(res.values)
    up, back = np.unique(p, return_inverse=True)
    res = kn.estimate(rk, geometry.centres(grid, up))
    return {k: v[back] for k, v in res.values.items()}
