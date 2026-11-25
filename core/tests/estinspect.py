"""A world for the tests of the inspector of a block (подшаг 18.Д3): a model of 20 × 16 × 8 parents (turned by 30°, a
domain field DOM), 28 inclined holes through it with two zones, a variogram model of three structures with its own axes,
a row of the plan of the T8.8-7 kind (an ellipsoid with three angles, three passes with the limits of a hole and of an
octant, a soft boundary), and what the job of 18.12 does with the row: the fields of `spec.columns()` written into the
model by `kernel.run` (the same portions, the same dtypes)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.blocks import geometry
from bf_core.blocks import store as bm
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.drillholes import store as dh
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate import samples as sm
from bf_core.geostat.estimate import store as st
from bf_core.geostat.estimate import store_base as sb
from bf_core.geostat.estimate.kna_blocks import Blocks
from tests.estutil import W, fill_world, make, vmodel
from tests.test_vario_store import fill, noop

GRID = {"corner": [1000.5, 2000.25, -300.0], "counts": [20, 16, 8], "size": [5.0, 5.0, 2.5], "rotation": 30}
SUB_GRID = {"corner": [500.0, 700.0, -50.0], "counts": [8, 6, 4], "size": [10.0, 10.0, 5.0], "rotation": 25,
            "div": [2, 2, 2]}  # fmt: skip
LABELS = ["руда", "порода"]
MODEL3 = {"type": "semivariogram", "c0": 0.12, "common_axes": False,
          "structures": [{"type": "spherical", "c": 0.35, "a1": 40, "a2": 28, "a3": 12, "ang1": 30, "ang2": -8,
                          "ang3": 15},
                         {"type": "exponential", "c": 0.30, "a1": 70, "a2": 45, "a3": 18, "ang1": 48, "ang2": 6,
                          "ang3": -12},
                         {"type": "gaussian", "c": 0.23, "a1": 110, "a2": 80, "a3": 30, "ang1": 22, "ang2": 0,
                          "ang3": 28}]}  # fmt: skip
PASSES = [
    {"factor": 1, "n_min": 6, "n_max": 16, "n_hole": 3, "n_oct": 4, "k_oct": 3, "n_holes_min": 3},
    {"factor": 1.6, "n_min": 4, "n_max": 20, "n_hole": 5, "n_oct": 0, "k_oct": 0, "n_holes_min": 2},
    {"factor": 2.4, "n_min": 5, "n_max": 24, "n_hole": 0, "n_oct": 0, "k_oct": 0, "n_holes_min": 2},
]
SEARCH = {"from": "manual", "r1": 18.0, "r2": 13.0, "r3": 7.0, "ang1": 35.0, "ang2": -10.0, "ang3": 20.0,
          "passes": PASSES}  # fmt: skip
SOFT = {"domains": ["сульфид"], "dist": 8.0}
SOURCE = {"op": "estimate", "algo": "est-1", "inputs": ["e" * 64]}


def big_model(w: W, grid: dict[str, Any] = GRID, seed: int = 3) -> int:
    """A full model of whole parents with the category DOM (about one block in five is «порода»)."""
    g = dict(grid)
    n = int(np.prod(g["counts"]))
    rng = np.random.default_rng(seed)
    dom = (rng.random(n) < 0.2).astype(np.int32)
    with bm.CellsWriterV2(w.store, g, [{"name": "DOM", "dtype": "cat", "labels": LABELS, "role": "code"}]) as wr:
        wr.write(np.arange(n, dtype=np.uint64), {"DOM": dom})
        staged = wr.finish()
    with dialect.write_tx(w.project.engine) as conn:
        return bm.commit_block_model(conn, w.store, staged, name="Крупная модель")


def sub_model(w: W) -> int:
    """A model with sub-blocks: a parent is whole or split into eight, by the parity of i + j + k."""
    grid = SUB_GRID
    nx, ny, nz = grid["counts"]
    rows = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                p = i + nx * (j + ny * k)
                if (i + j + k) % 2 == 0:
                    rows += [(p, sz, sy, sx, 1) for sz in (0, 1) for sy in (0, 1) for sx in (0, 1)]
                else:
                    rows.append((p, 0, 0, 0, 2))
    rows.sort()
    pp = np.array([r[0] for r in rows], dtype=np.uint64)
    s = np.array([[r[3], r[2], r[1]] for r in rows], dtype=np.uint8)
    wd = np.array([[r[4]] * 3 for r in rows], dtype=np.uint8)
    rng = np.random.default_rng(9)
    dom = (rng.random(len(rows)) < 0.15).astype(np.int32)
    with bm.CellsWriterV2(w.store, grid, [{"name": "DOM", "dtype": "cat", "labels": LABELS}]) as wr:
        wr.write(pp, {"DOM": dom}, s, wd)
        staged = wr.finish()
    with dialect.write_tx(w.project.engine) as conn:
        return bm.commit_block_model(conn, w.store, staged, name="Модель с субблоками")


def add_holes(w: W, model_id: int, n_holes: int = 28, seed: int = 11, name: str = "Для инспектора") -> tuple[int, int]:
    """Inclined holes through a model, a sample every 2.5 m; the zone («окисл» / «сульфид») is a trait of a hole;
    two columns: AU (lognormal) and ZONE. → (set_id, table_id)."""
    rng = np.random.default_rng(seed)
    with dialect.read_tx(w.project.engine) as conn:
        model = Model.open(conn, w.store, model_id)
    grid = model.grid
    ext = [float(np.sum(grid.steps(a))) for a in range(3)]
    names = [f"H{i:03d}" for i in range(n_holes)]
    top = np.array([[rng.uniform(0.0, ext[0]), rng.uniform(0.0, ext[1]), ext[2]] for _ in names])
    q = grid.to_project(top)
    collars = [dh.CollarRow(n, x=float(p[0]), y=float(p[1]), z=float(p[2]), td=ext[2] + 5.0,
                            azimuth=float(rng.uniform(0.0, 360.0)), dip=-float(rng.uniform(55.0, 90.0)))
               for n, p in zip(names, q, strict=True)]  # fmt: skip
    with dialect.write_tx(w.project.engine) as conn:
        set_id = dh.create_set(conn, name)
        dh.replace_collars(conn, set_id, collars)
    steps = int((ext[2] + 5.0) // 2.5)
    holes = [n for n in names for _ in range(steps)]
    frm = [2.5 * k for _ in names for k in range(steps)]
    zone = [("окисл" if i % 3 else "сульфид") for i in range(n_holes) for _ in range(steps)]
    au = [float(rng.lognormal(0.3, 0.7)) for _ in holes]
    table = dh.write_table(w.project.engine, set_id, dh.TableData(
        name="Пробы инспектора", kind="assay", holes=holes, from_m=frm, to_m=[f + 2.5 for f in frm],
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component"), dh.ColumnSpec("ZONE", "text")],
        values=[au, zone]), job_id=None, tick=noop)  # fmt: skip
    return set_id, int(table["table_id"])


def row_body(w: W, model_id: int, set_id: int, table: int, **over: Any) -> dict[str, Any]:
    """The body of a row of the plan: OK over 3×3×2 points of a block, the T8.8-7 search, the zone «окисл» with the
    soft boundary of 8 m to «сульфид», the blocks of the domain «руда»."""
    body: dict[str, Any] = {
        "model_id": model_id, "name": "AU · OK · руда", "disc": [3, 3, 2], "search": SEARCH,
        "target": {"domain_field": "DOM", "label": "руда"},
        "samples": {"set_id": set_id, "table": str(table), "field": "AU", "unit": "г/т",
                    "domain": {"table": str(table), "field": "ZONE", "values": ["окисл"]}, "soft": SOFT},
        "variogram": {"model_id": w.vz3}, "method": {"kind": "ok"},
    }  # fmt: skip
    body.update(over)
    return body


def stored_world(w: W, **over: Any) -> int:
    """A row of the plan on the big model; → its id."""
    return int(make(w, **row_body(w, w.big, w.big_set, w.big_table, **over))["id"])


def world_of(project: Any) -> W:
    """The world of `estutil` (the project, the store, a variogram model) with the big model, its holes and the
    variogram model of three structures (`w.vz3`)."""
    set_id, a, b = fill(project.engine)
    w = fill_world(project, set_id, a, b)
    w.vz3 = vmodel(w, "Три структуры", model=MODEL3)  # type: ignore[attr-defined]
    w.big = big_model(w)  # type: ignore[attr-defined]
    w.big_set, w.big_table = add_holes(w, w.big)  # type: ignore[attr-defined]
    return w


# --- the job of 18.12, as the tests write it --------------------------------------------------------------------------


def cast(values: np.ndarray, dtype: str) -> np.ndarray:
    """The values the way the field keeps them (the writer refuses a value a type cannot keep exactly)."""
    if dtype == "f4":
        return values.astype(np.float32).astype(np.float64)
    return values


def written(w: W, estimation_id: int) -> dict[str, np.ndarray]:
    """What `estimate.run` writes for the row: the fields of `spec.columns()` of the model, by `kernel.run` in portions
    (a parent — once, the result goes to every row of it; a block out of the domain or of the restrictor — STATUS 1).
    Returns {field name: column in the memory form}."""
    with dialect.read_tx(w.project.engine) as conn:
        row = st.get(conn, estimation_id)
        meta = row["meta"]
        spec = sb.load_spec(meta)
        model = Model.open(conn, w.store, spec.model_id)
        bl = Blocks.open(conn, w.store, spec)
    org = np.asarray(meta["origin"], dtype=np.float64)
    s = sm.load(w.project, spec, origin=org)
    rk = kn.compile_row(spec, s, rotation_deg=float(model.grid.rotation))
    p = model.p()
    sw = model.subs()
    if spec.sub == "parent":
        uniq, inverse = np.unique(p, return_inverse=True)
        inverse = np.asarray(inverse).reshape(-1)
        centres = geometry.centres(model.grid, uniq)
        sizes = geometry.row_widths(model.grid, uniq)
        active = np.zeros(uniq.shape[0], dtype=bool)
        np.logical_or.at(active, inverse, bl.mask)
    else:
        inverse = None
        sv, wv = (None, None) if sw is None else sw
        centres = geometry.centres(model.grid, p, sv, wv)
        sizes = geometry.row_widths(model.grid, p, sv, wv)
        active = bl.mask
    step = 700
    portions = [kn.Portion(centres[a : a + step], sizes[a : a + step], active[a : a + step])
                for a in range(0, centres.shape[0], step)]  # fmt: skip
    results = list(kn.run(rk, portions, threads=2))
    out: dict[str, np.ndarray] = {}
    for c in spec.columns():
        col = np.concatenate([r.values[c["key"]] for r in results])
        col = cast(col if inverse is None else col[inverse], c["dtype"])
        out[c["name"]] = col
    return out


def write_fields(w: W, estimation_id: int) -> dict[str, np.ndarray]:
    """`written` into the block model as the job does it (one `fld:` file, the source of the fields); → the columns."""
    from tests.test_blocks_fields_store import add_fields

    with dialect.read_tx(w.project.engine) as conn:
        spec = sb.load_spec(st.get(conn, estimation_id)["meta"])
    cols = written(w, estimation_id)
    adds = [{"name": c["name"], "dtype": c["dtype"], "unit": c["unit"], "role": c["role"],
             "description": c["description"], "replace": True} for c in spec.columns()]  # fmt: skip
    source = {**SOURCE, "params": {"estimation_ids": [estimation_id]}}
    add_fields(w.project, w.store, spec.model_id, adds, cols, source=source)
    return cols


def table_rows(w: W) -> list[int]:
    """The ids of the rows of the table of the samples (`dh_rows.id`, ascending)."""
    import sqlalchemy as sa

    from bf_core.db.schema import dh_rows

    with dialect.read_tx(w.project.engine) as conn:
        return [int(r) for r in conn.execute(sa.select(dh_rows.c.id).where(dh_rows.c.table_id == w.big_table)
                                             .order_by(dh_rows.c.id)).scalars()]  # fmt: skip


def edit_sample(w: W, value: float, row_id: int) -> None:
    """A real edit of the value AU of a row of the table of the samples (the table moves: the estimation is
    «устарел»)."""
    import sqlalchemy as sa

    from bf_core.db.schema import dh_columns
    from bf_core.drillholes import actions

    with dialect.read_tx(w.project.engine) as conn:
        au = conn.execute(sa.select(dh_columns.c.id).where(dh_columns.c.table_id == w.big_table,
                                                           dh_columns.c.name == "AU")).scalar_one()  # fmt: skip
    actions.update_cell(w.project, w.big_set, str(w.big_table), row_id, f"c{int(au)}", value, None)


# --- a world with the fields written, for the tests ---------------------------------------------------------------


class Ctx:
    """A world with a row of the plan whose fields are written (as the job does): the project, the row, the columns."""

    def __init__(self, w: W, eid: int, cols: dict[str, np.ndarray]) -> None:
        self.w, self.eid, self.cols = w, eid, cols

    @property
    def project(self) -> Any:
        return self.w.project

    def model(self) -> Model:
        with dialect.read_tx(self.project.engine) as conn:
            return Model.open(conn, self.w.store, self.w.big)

    def run(self, **body: Any) -> dict[str, Any]:
        from bf_core.geostat.estimate import inspect as ins
        from bf_core.geostat.estimate.spec import parse_inspect

        return ins.run(self.project, parse_inspect({"model_id": self.w.big, **body}))


def make_ctx(tmp_path: Path, **over: Any) -> Ctx:
    from types import SimpleNamespace

    from bf_core.db import migrate
    from tests.dbutil import make_engine

    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    project = SimpleNamespace(engine=engine, uid=f"test:{tmp_path}", folder=tmp_path)
    w = world_of(project)
    eid = stored_world(w, **over)
    return Ctx(w, eid, write_fields(w, eid))


def ctx_of(tmp_path: Path) -> Iterator[Ctx]:
    """The body of the fixtures `ctx` (a module's) and `fresh_ctx` (a test's) of the test files: the cache of the
    inspector is emptied, the world is built, the engine is closed at the end."""
    from bf_core.geostat.estimate import inspect as ins

    ins.forget()
    c = make_ctx(tmp_path)
    yield c
    c.project.engine.dispose()


def prepared(c: Ctx, eid: int | None = None) -> tuple[Model, Any, Any, dict[str, Any]]:
    """(the model, the stored row, the compiled row, `freshness`) as `inspect.run` takes them."""
    import sqlalchemy as sa

    from bf_core.db.schema import objects
    from bf_core.geostat.estimate import inspect as ins
    from bf_core.geostat.estimate.store_check import freshness

    eid = c.eid if eid is None else eid
    with dialect.read_tx(c.project.engine) as conn:
        model = Model.open(conn, c.w.store, c.w.big)
        row = conn.execute(sa.select(objects).where(objects.c.id == eid)).first()
        meta = dict(row.meta or {})
        fps = ins._fingerprints(conn, meta)  # noqa: SLF001
        fresh = freshness(conn, meta)
    return model, row, ins._entry(c.project, c.w.store, model, row, fps), fresh  # noqa: SLF001


def stored_of(c: Ctx, field: str, row: int) -> float:
    return float(c.cols[field][row])
