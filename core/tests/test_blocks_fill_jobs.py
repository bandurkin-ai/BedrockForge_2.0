"""The jobs `blocks.fill` and `blocks.code`, the routes and the leaf «каркас» of a filter (подшаг 17.20; ТЗ 8.7.3,
8.7.5; ADR 0023 §6, §8, решения 8, 9, 11, 12): the stages and the events of the job, a cancel at any tick leaves
nothing, priorities and the modes inside / outside / both, `source {op: fill}` with `inputs` («устарел») and
«Перестроить» in place, coding by the centre (new fields of the same model) and with splitting (a new model, the
fields of the parent inherited), the routes (checks before the job, a job in a `spawn` worker), the leaf «каркас» (by
the share and by the centre), the same on PostgreSQL (`-m db`)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen.mesh import uv_sphere  # noqa: E402

from bf_core.api.errors import ApiError  # noqa: E402
from bf_core.blocks import geometry, restrict  # noqa: E402
from bf_core.blocks import prototype as pr  # noqa: E402
from bf_core.blocks import schema as bs  # noqa: E402
from bf_core.blocks import store as bm  # noqa: E402
from bf_core.blocks.fields_store import FieldsWriter, commit_fields  # noqa: E402
from bf_core.blocks.fill import columns as fc  # noqa: E402
from bf_core.blocks.fill import commit, inputs, leaf, octree  # noqa: E402, F401
from bf_core.blocks.fill import jobs as fj  # noqa: E402
from bf_core.blocks.fill.spec import parse_code, parse_fill  # noqa: E402
from bf_core.blocks.model import Model  # noqa: E402
from bf_core.db import dialect, migrate  # noqa: E402
from bf_core.db.schema import blob_owners, objects  # noqa: E402
from bf_core.jobs.context import JobCancelled  # noqa: E402
from bf_core.projects.service import projects_of  # noqa: E402
from bf_core.storage.blobs import BlobStore  # noqa: E402
from bf_core.wireframes import store as wf  # noqa: E402
from tests.dbutil import make_engine  # noqa: E402
from tests.test_fill_octree import box  # noqa: E402
from tests.test_import_job import wait_job  # noqa: E402

API = "/api/v1"
BL = API + "/blocks"
STAGES = ["Каркасы", "Столбцы", "Граничные родители", "Дробление", "Слияние", "Запись"]


# --- helpers ---------------------------------------------------------------------------------------------------------


class P:
    """A project of a test: the engine, the folder, the store."""

    def __init__(self, engine: sa.Engine, folder: Path) -> None:
        self.engine, self.folder, self.store = engine, folder, BlobStore(folder)


@pytest.fixture()
def proj(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    yield P(engine, tmp_path)
    engine.dispose()


def prototype(p: P, counts: tuple[int, int, int] = (6, 5, 4), size: float = 10.0, levels: Any = (1, 1, 1),
              rotation: float = 0.0) -> int:  # fmt: skip
    ext = [0.0, 0.0, 0.0, counts[0] * size, counts[1] * size, counts[2] * size]
    plan = pr.make_plan([size, size, size], ext, rotation=rotation, levels=levels)
    with dialect.write_tx(p.engine) as conn:
        return int(pr.create_prototype(conn, p.store, plan, name="Прототип")["model_id"])


def body(p: P, lo: tuple[float, ...], hi: tuple[float, ...], name: str = "Тело") -> int:
    v, t = box(lo, hi)
    with dialect.write_tx(p.engine) as conn:
        return wf.put_wireframe(conn, p.store, v, t, name=name)


def request(proto: int, *wires: Any, **kw: Any) -> dict[str, Any]:
    return {"prototype_id": proto, "wireframes": [w if isinstance(w, dict) else {"id": w} for w in wires], **kw}


def params(req: dict[str, Any], rebuild: Any = None) -> dict[str, Any]:
    return {"spec": parse_fill(req).to_params(), "rebuild": rebuild}


def run(p: P, req: dict[str, Any], seen: list[Any] | None = None, cancel_at: int | None = None, rebuild: Any = None,
        **kw: Any) -> dict[str, Any]:  # fmt: skip
    ticks: list[Any] = [] if seen is None else seen

    def tick(_p: float | None = None, m: str | None = None) -> None:
        ticks.append(m)
        if cancel_at is not None and len(ticks) >= cancel_at:
            raise JobCancelled()

    return fj.run_fill(p.engine, p.folder, params(req, rebuild), tick=tick, **kw)


def code_params(model_id: int, req: dict[str, Any], rebuild: Any = None) -> dict[str, Any]:
    return {"spec": parse_code(model_id, req).to_params(), "rebuild": rebuild}


def run_code(p: P, model_id: int, req: dict[str, Any], seen: list[Any] | None = None, cancel_at: int | None = None,
             rebuild: Any = None, **kw: Any) -> dict[str, Any]:  # fmt: skip
    ticks: list[Any] = [] if seen is None else seen

    def tick(_p: float | None = None, m: str | None = None) -> None:
        ticks.append(m)
        if cancel_at is not None and len(ticks) >= cancel_at:
            raise JobCancelled()

    return fj.run_code(p.engine, p.folder, code_params(model_id, req, rebuild), tick=tick, **kw)


def stages_of(seen: list[Any]) -> list[str]:
    return [m for k, m in enumerate(seen) if m is not None and m not in seen[:k]]


def model(p: P, mid: int) -> Model:
    with dialect.read_tx(p.engine) as conn:
        return Model.open(conn, p.store, mid)


def info(p: P, mid: int) -> dict[str, Any]:
    with dialect.read_tx(p.engine) as conn:
        return bm.block_model_info(conn, mid)


def block_models(p: P) -> list[int]:
    with dialect.read_tx(p.engine) as conn:
        return sorted(int(i) for i in conn.execute(sa.select(objects.c.id).where(objects.c.kind == bs.KIND)).scalars())


def tmp_clean(p: P) -> bool:
    return not p.store.tmp_dir.exists() or not any(p.store.tmp_dir.iterdir())


def volumes(m: Model, code_name: str = "CODE") -> dict[str, float]:
    """Σ V·FRAC by the labels of CODE, from the stored model."""
    cols = m.columns([code_name])
    code = cols[code_name]
    labels = m.labels(code_name)
    vol = m.volume()
    return {
        lab: float(np.nansum(np.where(code == k, vol, 0.0))) for k, lab in enumerate(labels)
    }  # fmt: skip


def add_field(p: P, mid: int, name: str, values: np.ndarray, dtype: str = "f8") -> int:
    m = model(p, mid)
    with FieldsWriter(p.store, m, m.version) as w:  # type: ignore[arg-type]
        w.add(name, dtype)
        w.write({name: values})
        staged = w.finish()
    with dialect.write_tx(p.engine) as conn:
        return commit_fields(conn, p.store, staged, source={"op": "calc", "params": {"expr": name}})


# --- the fill: stages, the model, = the kernel ---------------------------------------------------------------------


def test_stages_events_and_the_new_model(proj: P) -> None:
    proto = prototype(proj)
    wid = body(proj, (12.5, 12.5, 7.5), (37.5, 32.5, 27.5), "Руда")  # on the sub-cells (5 m): exact volumes
    seen: list[Any] = []
    out = run(proj, request(proto, wid, levels=[1, 1, 1], m=4), seen=seen)
    assert stages_of(seen) == STAGES
    assert set(out["stages"]) == {"wireframes", "columns", "boundary", "split", "merge", "write", "total"}
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": out["model_id"]}]]
    assert out["name"] == "Прототип (заполнение)" and out["dropped"] == [] and out["warnings"] == []
    mid = out["model_id"]
    m = model(proj, mid)
    assert m.version == 1 and m.sub and m.rows == out["rows"] and m.parents == out["parents"]
    assert m.grid.sub.div == (2, 2, 2) and m.meta["source"]["op"] == "fill"
    assert m.meta["source"]["params"]["wireframes"] == [{"id": wid, "code": None, "priority": None}]
    assert m.meta["algo"] == commit.ALGO
    with dialect.read_tx(proj.engine) as conn:
        want = [{"id": wid, "sha": inputs.wireframe_sha(conn, wid)}]
        assert m.meta["inputs"] == {"wireframes": want} and want[0]["sha"] and len(want[0]["sha"]) == 64
        assert inputs.stale_reason(conn, m.meta["inputs"]) is None
    code, frac = m.field("CODE"), m.field("FRAC")
    assert (code["dtype"], code["role"], code["labels"], code["kind"]) == ("cat", "code", ["Руда"], "category")
    assert (frac["dtype"], frac["role"]) == ("f8", "frac") and code["source"]["op"] == "fill"
    cols = m.columns(["CODE", "FRAC"])
    assert (cols["CODE"] == 0).all() and cols["FRAC"].min() > 0 and cols["FRAC"].max() == 1.0
    # the volumes: Σ V·FRAC of the code = the volume of the body; the counts = the rows
    assert volumes(m)["Руда"] == pytest.approx(25.0 * 20.0 * 20.0, abs=1e-9)
    assert out["volume"] == {"Руда": pytest.approx(10000.0)} and out["codes"] == {"Руда": m.rows}
    # the same rows as the kernel gives (the job adds nothing, takes nothing)
    grid = m.grid
    v, t = box((12.5, 12.5, 7.5), (37.5, 32.5, 27.5))
    ref = octree.fill_grid(grid, [fc.Mesh.of_project(grid, v, t, "Руда")], mode="inside", m=4)
    assert np.array_equal(m.p(), ref.p)
    s, w = m.subs() or (None, None)
    assert np.array_equal(s, ref.s) and np.array_equal(w, ref.w) and np.array_equal(cols["FRAC"], ref.frac)
    assert info(proj, mid)["source"]["op"] == "fill" and tmp_clean(proj)


def test_cancel_at_any_tick_leaves_nothing(proj: P) -> None:
    proto = prototype(proj, counts=(4, 3, 3))
    wid = body(proj, (7.5, 7.5, 7.5), (27.5, 17.5, 17.5))
    seen: list[Any] = []
    run(proj, request(proto, wid), seen=seen)  # the whole run compiles the kernels and counts the ticks
    done = block_models(proj)
    owners = len(_owners(proj))
    assert len(done) == 2 and len(seen) > 8
    for at in range(1, len(seen) + 1):
        with pytest.raises(JobCancelled):
            run(proj, request(proto, wid), cancel_at=at)
        assert block_models(proj) == done and tmp_clean(proj) and len(_owners(proj)) == owners, at


def _owners(p: P) -> list[Any]:
    with dialect.read_tx(p.engine) as conn:
        return list(conn.execute(sa.select(blob_owners.c.owner_id, blob_owners.c.role)))


# --- priorities, the modes, the codes ------------------------------------------------------------------------------

A_BOX = ((10.0, 10.0, 10.0), (50.0, 40.0, 30.0))  # 40 × 30 × 20 = 24 000 m³, on the sub-cells (5 m)
B_BOX = ((20.0, 20.0, 10.0), (40.0, 30.0, 30.0))  # 20 × 10 × 20 = 4 000 m³, inside A
GRID_VOLUME = 60.0 * 50.0 * 40.0


def test_priorities_decide_the_overlap_equal_ones_go_by_the_list(proj: P) -> None:
    proto = prototype(proj)
    a, b = body(proj, *A_BOX, name="A"), body(proj, *B_BOX, name="B")
    out = run(proj, request(proto, {"id": a, "priority": 1}, {"id": b, "priority": 5}))
    assert out["volume"] == {"A": pytest.approx(20000.0), "B": pytest.approx(4000.0)}
    first = run(proj, request(proto, a, b))  # equal: the first of the list is the main one
    assert first["volume"] == {"A": pytest.approx(24000.0), "B": 0.0}
    second = run(proj, request(proto, b, a))
    assert second["volume"] == {"B": pytest.approx(4000.0), "A": pytest.approx(20000.0)}
    m = model(proj, second["model_id"])
    assert m.labels("CODE") == ["B", "A"]  # the labels go in the order of the request
    shared = run(proj, request(proto, {"id": a, "code": "Руда"}, {"id": b, "code": "руда", "priority": 3}))
    assert model(proj, shared["model_id"]).labels("CODE") == ["Руда"]  # one code of two bodies is one label
    assert shared["volume"] == {"Руда": pytest.approx(24000.0)}


def test_modes_inside_outside_both(proj: P) -> None:
    proto = prototype(proj)
    a = body(proj, *A_BOX, name="A")
    inside = run(proj, request(proto, a, mode="inside", outside_code="за пределами"))
    assert list(inside["volume"]) == ["A"] and inside["volume"]["A"] == pytest.approx(24000.0)
    outside = run(proj, request(proto, a, mode="outside", outside_code="за пределами"))
    assert outside["volume"] == {"A": 0.0, "за пределами": pytest.approx(GRID_VOLUME - 24000.0)}
    assert outside["codes"]["A"] == 0
    m = model(proj, outside["model_id"])
    assert m.labels("CODE") == ["A", "за пределами"] and (m.columns(["CODE"])["CODE"] == 1).all()
    both = run(proj, request(proto, a, mode="both"))
    assert both["volume"] == {"A": pytest.approx(24000.0), "вне": pytest.approx(GRID_VOLUME - 24000.0)}
    assert sum(both["volume"].values()) == pytest.approx(GRID_VOLUME, abs=1e-6)
    mb = model(proj, both["model_id"])
    assert mb.rows == both["rows"] and mb.parents == 6 * 5 * 4  # every parent of the prototype is in a row
    assert both["rows"] == sum(both["codes"].values())


def test_refusals_of_the_request_and_of_the_bodies(proj: P) -> None:
    proto = prototype(proj)
    a = body(proj, *A_BOX, name="вне")
    for bad, param in (
        (request(proto, a, mode="сбоку"), "mode"), (request(proto, a, m=0), "m"), (request(proto, a, m=17), "m"),
        (request(proto, a, m=2.5), "m"), (request(proto), "wireframes"), (request(proto, a, a), "wireframes.1.id"),
        (request(proto, a, levels=[1, 8, 0]), "levels.1"), (request(proto, a, levels=[1, 1]), "levels"),
        (request(proto, a, frac_field=None), "frac_field"),
        (request(proto, a, code_field="CODE", frac_field="code"), "frac_field"),
        (request(proto, a, code_field="sx"), "code_field"),
        (request(proto, {"id": a, "priority": "много"}), "wireframes.0.priority"),
        (request(proto, {"id": a, "code": "вне"}, mode="both"), "outside_code"),
        (request(proto, a, folder=5), "folder"),
    ):  # fmt: skip
        with pytest.raises(ApiError) as e:
            parse_fill(bad)
        assert e.value.status == 422 and e.value.extra.get("param") == param, (bad, e.value.message)
    # the name of the body is the label: «вне» is taken by the outside code in the mode «оба»
    with pytest.raises(ApiError) as e2:
        run(proj, request(proto, a, mode="both"))
    assert e2.value.code == "bad_param" and e2.value.extra["param"] == "outside_code" and tmp_clean(proj)
    # a surface is no body; a missing prototype, a model that is no prototype
    open_id = _surface(proj)
    with pytest.raises(ApiError) as e3:
        run(proj, request(proto, open_id))
    assert (e3.value.code, e3.value.extra["param"]) == ("wireframe_open", "wireframes.0.id")
    done = run(proj, request(proto, a, mode="inside", outside_code="x"))
    with pytest.raises(ApiError) as e4:
        run(proj, request(done["model_id"], a))
    assert e4.value.code == "not_prototype" and e4.value.status == 422
    with pytest.raises(ApiError) as e5:
        run(proj, request(777_777, a))
    assert e5.value.status == 404 and e5.value.code == "block_model_not_found"
    with pytest.raises(ApiError) as e6:
        run(proj, request(proto, 777_777))
    assert e6.value.status == 404 and e6.value.extra["param"] == "wireframes.0.id"


def _surface(p: P, name: str = "Поверхность") -> int:
    xyz = np.array([[-5.0, -5.0, 1.0], [75.0, -5.0, 1.0], [75.0, 65.0, 1.0], [-5.0, 65.0, 1.0]])
    with dialect.write_tx(p.engine) as conn:
        return wf.put_wireframe(conn, p.store, xyz, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32), name=name)


def test_a_body_off_the_grid_is_a_warning_and_the_levels_are_the_requests(proj: P) -> None:
    proto = prototype(proj, levels=(1, 1, 1))
    far = body(proj, (500.0, 500.0, 500.0), (520.0, 520.0, 520.0), "Далеко")
    out = run(proj, request(proto, far, levels=[0, 0, 0]))
    assert out["rows"] == 0 and out["parents"] == 0
    assert any("«Далеко»" in w for w in out["warnings"]) and any("ни один блок" in w for w in out["warnings"])
    m = model(proj, out["model_id"])
    assert m.rows == 0 and m.grid.sub.div == (1, 1, 1) and not m.sub and m.meta["warnings"] == out["warnings"]
    near = body(proj, (13.0, 13.0, 13.0), (33.0, 33.0, 33.0), "Рядом")
    out2 = run(proj, request(proto, near, levels=[0, 0, 0], m=4))  # no sub-cells: the boundary by the m³ points
    m2 = model(proj, out2["model_id"])
    assert not m2.sub and m2.grid.sub.div == (1, 1, 1) and m2.subs() is None
    assert 0.0 < m2.columns(["FRAC"])["FRAC"].min() < 1.0
    assert volumes(m2)["Рядом"] == pytest.approx(20.0**3, rel=0.03)  # Т8.7-3: within the error of the points


def test_the_threads_do_not_change_the_bits(proj: P) -> None:
    proto = prototype(proj, counts=(5, 5, 4))
    v, t = uv_sphere(400, radius=19.0, centre=(25.0, 25.0, 20.0))
    with dialect.write_tx(proj.engine) as conn:
        wid = wf.put_wireframe(conn, proj.store, v, t, name="Шар")
    got = []
    for n in (1, 2, 4):
        m = model(proj, run(proj, request(proto, wid, m=5), threads=n)["model_id"])
        cols = m.columns(["CODE", "FRAC"])
        s, w = m.subs() or (np.zeros(0), np.zeros(0))
        got.append((m.p().tobytes(), s.tobytes(), w.tobytes(), cols["FRAC"].tobytes(), cols["CODE"].tobytes()))
    assert got[0] == got[1] == got[2]


# --- «устарел» and «Перестроить» -------------------------------------------------------------------------------------


def swap_geometry(p: P, target: int, donor: int) -> None:
    """The wireframe `target` gets the geometry files of `donor` (an edit in place: new immutable blobs)."""
    with dialect.write_tx(p.engine) as conn:
        for role in ("xyz", "tri"):
            sha = conn.execute(
                sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_id == donor, blob_owners.c.role == role)
            ).scalar_one()
            conn.execute(
                sa.update(blob_owners)
                .where(blob_owners.c.owner_id == target, blob_owners.c.role == role)
                .values(sha256=sha)
            )


def stale(p: P, mid: int) -> str | None:
    m = model(p, mid)
    with dialect.read_tx(p.engine) as conn:
        return inputs.stale_reason(conn, m.meta["inputs"], inputs.names_in(m.meta))


def test_stale_by_the_inputs_and_rebuild_in_place(proj: P) -> None:
    proto = prototype(proj)
    wid = body(proj, *A_BOX, name="Руда")
    donor = body(proj, *B_BOX, name="Донор")
    out = run(proj, request(proto, wid))
    mid = out["model_id"]
    assert stale(proj, mid) is None and out["volume"]["Руда"] == pytest.approx(24000.0)
    assert model(proj, mid).meta["source"]["params"]["names"] == {"wireframes": {str(wid): "Руда"}}
    old = model(proj, mid)
    version = add_field(proj, mid, "X", np.arange(old.rows, dtype=np.float64))  # a field of another maker
    assert version == 2 and {f["name"] for f in model(proj, mid).fields} == {"CODE", "FRAC", "X"}
    swap_geometry(proj, wid, donor)  # the body is edited: «изменён каркас»
    assert stale(proj, mid) == "изменён каркас «Руда»"
    # the job on the same params: the same model, version + 1, the fields of the other makers are dropped
    params_ = model(proj, mid).meta["source"]["params"]
    redo = fj.run_fill(proj.engine, proj.folder, {"spec": params_, "rebuild": {"model_id": mid, "expected_version": 2}})
    assert redo["model_id"] == mid and redo["version"] == 3 and redo["dropped"] == ["X"] and tmp_clean(proj)
    assert redo["name"] == "Прототип (заполнение)" and redo["volume"]["Руда"] == pytest.approx(4000.0)
    new = model(proj, mid)
    assert new.version == 3 and [f["name"] for f in new.fields] == ["CODE", "FRAC"] and stale(proj, mid) is None
    assert block_models(proj) == sorted([proto, mid])  # no new model
    assert volumes(new)["Руда"] == pytest.approx(4000.0) and new.rows == redo["rows"] != old.rows
    # a model changed since → 409 `version_conflict {version}`; nothing is written
    with pytest.raises(ApiError) as e:
        fj.run_fill(proj.engine, proj.folder, {"spec": params_, "rebuild": {"model_id": mid, "expected_version": 2}})
    assert e.value.status == 409 and e.value.extra == {"version": 3} and tmp_clean(proj)
    assert model(proj, mid).version == 3
    # the body is deleted: «удалён каркас «Руда»» (the name comes from the params)
    with dialect.write_tx(proj.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == wid))
    assert stale(proj, mid) == "удалён каркас «Руда»"
    assert stale(proj, proto) is None  # a prototype has no inputs


def test_the_owners_of_a_rebuilt_model(proj: P) -> None:
    proto = prototype(proj)
    wid = body(proj, *A_BOX)
    mid = run(proj, request(proto, wid))["model_id"]
    add_field(proj, mid, "X", np.zeros(model(proj, mid).rows))
    before = {r for i, r in _owners(proj) if i == mid}
    assert len(before) == 2 and "cells" in before
    params_ = model(proj, mid).meta["source"]["params"]
    fj.run_fill(proj.engine, proj.folder, {"spec": params_, "rebuild": {"model_id": mid, "expected_version": 2}})
    after = {r for i, r in _owners(proj) if i == mid}
    assert after == {"cells"}  # CODE and FRAC are in `cells` of a fill; the file of X is no more the model's


# --- coding ----------------------------------------------------------------------------------------------------------


def plain_model(p: P, counts: tuple[int, int, int] = (6, 5, 4), size: tuple[float, ...] = (10.0, 10.0, 10.0),
                levels: Any = (1, 1, 1), rotation: float = 0.0, name: str = "Модель") -> int:  # fmt: skip
    """A model of whole parents (no columns of sub-blocks) with a field AU (a gap at every seventh)."""
    grid = bs.GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": list(counts), "size": list(size), "rotation": rotation,
                         "div": [1 << x for x in levels]})  # fmt: skip
    n = grid.cells
    au = np.arange(n, dtype=np.float64)
    au[::7] = np.nan
    with bm.CellsWriterV2(
        p.store, grid, [{"name": "AU", "dtype": "f8", "unit": "г/т", "role": "grade"}], sub=False
    ) as w:
        w.write(np.arange(n, dtype=np.uint64), {"AU": au})
        staged = w.finish()
    try:
        with dialect.write_tx(p.engine) as conn:
            return bm.commit_block_model(conn, p.store, staged, name=name, source={"format": "dat", "file": "m.dat"})
    finally:
        staged.discard()


def creq(wires: Any, version: int = 1, **kw: Any) -> dict[str, Any]:
    ws = [w if isinstance(w, dict) else {"id": w} for w in (wires if isinstance(wires, (list, tuple)) else [wires])]
    return {"expected_version": version, "wireframes": ws, **kw}


def reference(m: Model, box_: Any, mode: str = "inside", mm: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """The kernel over the boxes of the rows: (code, FRAC) by the m³ points of every box."""
    from bf_core.blocks.fill import frac as ff

    v, t = box_
    bodies = ff.Bodies.of(m.grid, [fc.Mesh.of_project(m.grid, v, t, "B")])
    return ff.box_codes(bodies, m.boxes(), m=mm, mode=mode)


def test_coding_by_the_centre_adds_fields_to_the_same_model(proj: P) -> None:
    mid = plain_model(proj)
    cut = body(proj, (15.0, 15.0, 5.0), (47.0, 33.0, 31.0), "Зона")  # crosses the parents: the boxes by the points
    seen: list[Any] = []
    sha_before = model(proj, mid).shas()["cells"]  # the input of a new field is the file of the rows (a real blob)
    out = run_code(proj, mid, creq(cut, frac_field="ZF", code_field="ZONE"), seen=seen)
    assert stages_of(seen) == ["Каркасы", "Граничные родители", "Дробление", "Запись"]
    assert out["model_id"] == mid and out["version"] == 2 and out["fields"] == ["ZONE", "ZF"]
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": mid}]] and tmp_clean(proj)
    m = model(proj, mid)
    assert m.version == 2 and m.rows == 120 and not m.sub and m.meta["source"] is not None  # the rows are the same
    z, f = m.field("ZONE"), m.field("ZF")
    assert (z["role"], z["dtype"], z["labels"], f["role"], f["dtype"]) == ("code", "cat", ["Зона"], "frac", "f8")
    assert z["source"]["op"] == "code" and z["source"]["params"]["names"]["wireframes"] == {str(cut): "Зона"}
    assert z["source"]["inputs"] == [sha_before] and len(sha_before) == 64
    cols = m.columns(["ZONE", "ZF", "AU"])
    code, frac = reference(m, box((15.0, 15.0, 5.0), (47.0, 33.0, 31.0)))
    assert np.array_equal(cols["ZONE"], np.where(code == 0, 0, -1)) and np.array_equal(cols["ZF"], frac, equal_nan=True)
    assert (cols["ZONE"] == -1).any() and (cols["ZONE"] == 0).any() and (0 < cols["ZF"][cols["ZONE"] == 0]).all()
    assert np.isnan(cols["ZF"][cols["ZONE"] == -1]).all()  # a row of no class has no share: a gap, not a zero
    assert np.array_equal(cols["AU"], np.where(np.arange(120) % 7 == 0, np.nan, np.arange(120.0)), equal_nan=True)
    # the volume of the model counts the shares: the rows with no share are not in it
    assert out["volume"]["Зона"] == pytest.approx(float(np.nansum(m.volume())), rel=1e-9)
    assert out["codes"] == {"Зона": int((cols["ZONE"] == 0).sum())}
    # again — the field is there (409), the second share field (422), a stale version (409)
    for req, status, code_ in (
        (creq(cut, code_field="ZONE", version=2), 409, "field_exists"),
        (creq(cut, code_field="Z2", frac_field="ZF", version=2), 409, "field_exists"),
        (creq(cut, code_field="Z2", frac_field="F3", version=2), 422, "frac_exists"),
        (creq(cut, code_field="Z2", version=1), 409, "version_conflict"),
    ):
        with pytest.raises(ApiError) as e:
            run_code(proj, mid, req)
        assert (e.value.status, e.value.code) == (status, code_), req
    assert model(proj, mid).version == 2 and tmp_clean(proj)


def test_coding_by_the_centre_on_a_whole_parents_box_and_without_a_share(proj: P) -> None:
    mid = plain_model(proj)
    cut = body(proj, (20.0, 10.0, 10.0), (40.0, 30.0, 30.0), "Зона")  # on the parents: no box is crossed
    out = run_code(proj, mid, creq(cut))  # no `frac_field` — the code only
    assert out["fields"] == ["CODE"] and out["version"] == 2
    m = model(proj, mid)
    assert [f["name"] for f in m.fields] == ["AU", "CODE"] and m.role_field("frac") is None
    code = m.columns(["CODE"])["CODE"]
    c = m.centres()
    inside = (c >= [20, 10, 10]).all(axis=1) & (c <= [40, 30, 30]).all(axis=1)
    assert np.array_equal(code == 0, inside) and (code[~inside] == -1).all()
    assert out["volume"]["Зона"] == pytest.approx(20.0 * 20.0 * 20.0) and out["codes"]["Зона"] == int(inside.sum())
    both = run_code(proj, mid, creq(cut, version=2, code_field="C2", mode="both", outside_code="вне"))
    m2 = model(proj, mid)
    assert m2.labels("C2") == ["Зона", "вне"] and (m2.columns(["C2"])["C2"] == np.where(inside, 0, 1)).all()
    assert both["volume"] == {"Зона": pytest.approx(8000.0), "вне": pytest.approx(120000.0 - 8000.0)}


def test_coding_cancel_leaves_nothing(proj: P) -> None:
    mid = plain_model(proj)
    cut = body(proj, (15.0, 15.0, 5.0), (47.0, 33.0, 31.0), "Зона")
    for split in (False, True):
        seen: list[Any] = []
        run_code(proj, mid, creq(cut, version=model(proj, mid).version, split=split, code_field=f"A{split}"), seen=seen)
        version, models, owners = model(proj, mid).version, block_models(proj), len(_owners(proj))
        assert len(seen) > 4
        for at in range(1, len(seen) + 1):
            req = creq(cut, version=version, split=split, code_field=f"B{split}", frac_field=None)
            with pytest.raises(JobCancelled):
                run_code(proj, mid, req, cancel_at=at)
            assert tmp_clean(proj) and model(proj, mid).version == version, (split, at)
            assert block_models(proj) == models and len(_owners(proj)) == owners, (split, at)


def test_coding_with_splitting_makes_a_new_model_and_the_children_inherit(proj: P) -> None:
    mid = plain_model(proj, levels=(2, 2, 2))  # the sub-cell is 2.5 m
    cut = body(proj, (15.0, 15.0, 5.0), (47.5, 32.5, 30.0), "Зона")  # on the sub-cells
    out = run_code(proj, mid, creq(cut, split=True, code_field="ZONE", frac_field=None))
    assert out["model_id"] != mid and out["name"] == "Модель (кодирование)" and out["dropped"] == []
    new = model(proj, out["model_id"])
    old = model(proj, mid)
    assert new.sub and new.grid == old.grid and new.meta["source"]["op"] == "code" and new.version == 1
    assert new.meta["source"]["params"]["split"] is True and new.meta["algo"] == commit.ALGO
    with dialect.read_tx(proj.engine) as conn:
        assert new.meta["inputs"] == inputs.inputs_of(conn, [cut], mid) and stale(proj, new.id) is None
    assert [f["name"] for f in new.fields] == ["AU", "ZONE"] and new.field("AU")["role"] == "grade"
    assert new.field("AU")["unit"] == "г/т" and new.rows > old.rows and new.parents == old.parents == 120
    s, w = new.subs() or (None, None)
    assert s is not None and w is not None and not bs.GridV2.of(new.meta["grid"]).sub.bad_rows(s, w).any()
    # the order of the file, the fields of the parent inherited: AU of a child = AU of its parent
    assert bs.ascending(new.p(), s)
    cols = new.columns(["AU", "ZONE"])
    au_old = old.columns(["AU"])["AU"][new.p().astype(np.intp)]  # the old rows are the parents in order of P
    assert np.array_equal(cols["AU"], au_old, equal_nan=True)
    # the volume of the zone = the box (exact: on the sub-cells), every row of the parents is whole or its pieces
    assert out["volume"]["Зона"] == pytest.approx(32.5 * 17.5 * 25.0, rel=1e-12)
    vol = geometry.row_volumes(new.grid, new.p(), s, w)
    assert vol.sum() == pytest.approx(120000.0, rel=1e-12)  # the rows still tile the grid
    assert float(vol[cols["ZONE"] == 0].sum()) == pytest.approx(out["volume"]["Зона"], rel=1e-12)
    assert (cols["ZONE"] == -1).any() and (cols["ZONE"] == 0).any()


def test_splitting_the_rows_of_one_parent_interleaves_into_the_order_of_the_file(proj: P) -> None:
    grid = bs.GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [2, 1, 1], "size": [10.0, 10.0, 10.0], "div": [2, 2, 2]})
    # two rows of the parent 0 (x 0–5 and 5–10, the whole y and z) and the whole parent 1
    p = np.array([0, 0, 1], dtype=np.uint64)
    s = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 0]], dtype=np.uint8)
    w = np.array([[1, 2, 2], [1, 2, 2], [2, 2, 2]], dtype=np.uint8)
    with bm.CellsWriterV2(proj.store, grid, [{"name": "AU", "dtype": "f8"}]) as wr:
        wr.write(p, {"AU": np.array([1.0, 2.0, 3.0])}, s, w)
        staged = wr.finish()
    with dialect.write_tx(proj.engine) as conn:
        mid = bm.commit_block_model(conn, proj.store, staged, name="Строки", source={"format": "dat", "file": "m"})
    cut = body(proj, (-5.0, -5.0, -5.0), (30.0, 15.0, 5.0), "Низ")  # the lower half of every row
    out = run_code(proj, mid, creq(cut, split=True, frac_field=None))
    new = model(proj, out["model_id"])
    ns, nw = new.subs()  # type: ignore[misc]
    assert bs.ascending(new.p(), ns) and new.rows == 8 + 8  # every row of the parent 0 into 4, the parent 1 into 8
    cols = new.columns(["AU", "CODE"])
    # the parent 0 rows interleave: (x0, y0, z0), (x1, y0, z0), (x0, y1, z0), (x1, y1, z0), then z1
    assert new.p()[:8].tolist() == [0] * 8 and ns[:4].tolist() == [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]
    assert cols["AU"][:4].tolist() == [1.0, 2.0, 1.0, 2.0] and cols["AU"][4:8].tolist() == [1.0, 2.0, 1.0, 2.0]
    assert cols["CODE"][:4].tolist() == [0, 0, 0, 0] and cols["CODE"][4:8].tolist() == [-1] * 4
    assert out["volume"]["Низ"] == pytest.approx(2 * 5.0 * 10.0 * 5.0 + 10.0 * 10.0 * 5.0)
    assert float(geometry.row_volumes(new.grid, new.p(), ns, nw).sum()) == pytest.approx(2000.0)


def test_rebuild_of_a_coding_with_splitting(proj: P) -> None:
    mid = plain_model(proj, levels=(1, 1, 1))
    cut = body(proj, (15.0, 15.0, 5.0), (35.0, 35.0, 25.0), "Зона")
    donor = body(proj, (5.0, 5.0, 5.0), (25.0, 25.0, 25.0), "Донор")
    out = run_code(proj, mid, creq(cut, split=True, code_field="ZONE", frac_field=None))
    nid = out["model_id"]
    assert stale(proj, nid) is None
    add_field(proj, nid, "X", np.zeros(model(proj, nid).rows))
    swap_geometry(proj, cut, donor)
    assert stale(proj, nid) == "изменён каркас «Зона»"
    spec = model(proj, nid).meta["source"]["params"]
    redo = fj.run_code(proj.engine, proj.folder, {"spec": spec, "rebuild": {"model_id": nid, "expected_version": 2}})
    assert redo["model_id"] == nid and redo["version"] == 3 and redo["dropped"] == ["X"] and tmp_clean(proj)
    new = model(proj, nid)
    assert [f["name"] for f in new.fields] == ["AU", "ZONE"] and stale(proj, nid) is None
    assert redo["volume"]["Зона"] == pytest.approx(20.0**3)
    # the model coded changes: «изменена модель»
    add_field(proj, mid, "Y", np.zeros(120))
    assert stale(proj, nid) == "изменена модель «Модель»"
    with dialect.write_tx(proj.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == mid))
    assert stale(proj, nid) == "удалена модель «Модель»"


# --- the leaf «каркас» of a filter ---------------------------------------------------------------------------------


def leaf_rule(oid: Any, side: str = "inside", by: str = "frac", m: int = 4) -> dict[str, Any]:
    return {"wireframe": {"id": oid, "side": side, "by": by, "m": m}}


def shares(p: P, mid: int, rules: Any, chunk: int = 1 << 20) -> tuple[np.ndarray, list[str]]:
    m = model(p, mid)
    with dialect.read_tx(p.engine) as conn:
        r = restrict.prepare(m, rules, conn=conn, store=p.store)
    out = np.empty(m.rows)
    for ch, f in r.iter(m, chunk=chunk):
        out[ch.start : ch.start + ch.n] = f
    return out, r.warnings


class Proj:
    """What `restrict.test_rules` takes of a project: the engine and the folder."""

    def __init__(self, p: P) -> None:
        self.engine, self.folder = p.engine, p.folder


GRID_F = {"counts": (4, 3, 2), "size": (5.0, 5.0, 2.5), "levels": (0, 0, 0)}  # as the tests of the restrictor
SLAB = ((3.0, -5.0, -5.0), (13.0, 20.0, 10.0))  # only x counts: the columns 0–5, 5–10, 10–15, 15–20 hold 2, 5, 3, 0 m


def slab_share(lo: float, hi: float, i: int, size: float, m: int) -> float:
    """The share of the m points of the column i (their x are (i + (k + ½)/m)·size) in [lo; hi]."""
    x = (i + (np.arange(m) + 0.5) / m) * size
    return float(((x >= lo) & (x <= hi)).mean())


def test_the_leaf_is_registered_and_gives_the_share_of_the_block_in_the_body(proj: P) -> None:
    assert restrict.LEAVES["wireframe"] is leaf.build
    mid = plain_model(proj, **GRID_F)
    wid = body(proj, *SLAB, name="Плита")
    for m_ in (1, 2, 4, 5, 16):
        want = np.tile([slab_share(3.0, 13.0, i, 5.0, m_) for i in range(4)], 6)
        f, warnings = shares(proj, mid, leaf_rule(wid, "inside", "frac", m_))
        assert warnings == [] and f.dtype == np.float64 and np.array_equal(f, want), m_
        out = shares(proj, mid, leaf_rule(wid, "outside", "frac", m_))[0]
        assert np.array_equal(out, 1.0 - want), m_
    assert np.tile([0.5, 1.0, 0.5, 0.0], 6).tolist() == shares(proj, mid, leaf_rule(wid))[0].tolist()  # m = 4
    c, _ = shares(proj, mid, leaf_rule(wid, "inside", "centre"))  # the centres 2.5, 7.5, 12.5, 17.5
    assert np.array_equal(c, np.tile([0.0, 1.0, 1.0, 0.0], 6))
    assert np.array_equal(shares(proj, mid, leaf_rule(wid, "outside", "centre"))[0], 1.0 - c)
    assert np.array_equal(shares(proj, mid, leaf_rule(wid, "inside", "frac", 1))[0], c)  # m = 1 is the centre
    # the chunks do not matter
    assert np.array_equal(shares(proj, mid, leaf_rule(wid), chunk=5)[0], shares(proj, mid, leaf_rule(wid))[0])
    # the tree: «И» is the product with a condition; «Проверить на модели» → the count and the weight
    from tests.test_filters_store import cond, group

    au = model(proj, mid).columns(["AU"])["AU"]
    row = np.tile([0.5, 1.0, 0.5, 0.0], 6)
    both, _ = shares(proj, mid, group("and", leaf_rule(wid), cond("AU", ">", 10)))
    assert np.allclose(both, row * (au > 10))
    tested = restrict.test_rules(Proj(proj), mid, leaf_rule(wid))
    assert tested["total"] == 24 and tested["passed"] == 18 and tested["weight"] == pytest.approx(float(row.sum()))
    assert tested["warnings"] == []


def test_the_leaf_in_the_axes_of_a_turned_model_and_on_a_model_with_sub_blocks(proj: P) -> None:
    mid = plain_model(proj, counts=(5, 4, 3), size=(7.0, 6.0, 5.0), levels=(0, 0, 0), rotation=30.0)
    cut = ((4.1, 3.3, 1.7), (23.9, 19.1, 9.3))  # a box in the axes of the project, the faces off the centres
    wid = body(proj, *cut, name="Коробка")
    m = model(proj, mid)
    c = m.centres()  # the project's axes
    inside = ((c >= cut[0]) & (c <= cut[1])).all(axis=1)
    assert inside.any() and not inside.all()
    assert np.array_equal(shares(proj, mid, leaf_rule(wid, "inside", "centre"))[0], inside.astype(np.float64))
    f = shares(proj, mid, leaf_rule(wid, "inside", "frac", 6))[0]
    assert ((f > 0) & (f < 1)).any() and f[inside].mean() > f[~inside].mean()
    # the volume of the body inside the grid, by a dense lattice of points (independent of the rays), to ~5 %
    axes = [np.linspace(cut[0][a], cut[1][a], 2 * n + 1)[1::2] for a, n in enumerate((60, 50, 30))]
    dense = np.array(np.meshgrid(*axes, indexing="ij")).reshape(3, -1).T
    q = m.grid.to_local(dense)
    in_grid = ((q >= 0) & (q <= np.asarray(m.grid.lengths()))).all(axis=1)
    want = float(in_grid.mean()) * float(np.prod(np.subtract(cut[1], cut[0])))
    assert 0.3 < in_grid.mean() < 1.0 and float((f * m.volume()).sum()) == pytest.approx(want, rel=0.06)
    # a model with sub-blocks: the leaf takes the box of every row
    proto = prototype(proj)
    zone = body(proj, *A_BOX, name="A")
    filled = run(proj, request(proto, zone, levels=[2, 2, 2]))["model_id"]
    probe = body(proj, (22.0, 12.0, 8.0), (38.0, 52.0, 32.0), "Проба")
    ms = model(proj, filled)
    cs = ms.centres()
    want = ((cs >= (22.0, 12.0, 8.0)) & (cs <= (38.0, 52.0, 32.0))).all(axis=1)
    assert np.array_equal(shares(proj, filled, leaf_rule(probe, "inside", "centre"))[0], want.astype(np.float64))
    fs = shares(proj, filled, leaf_rule(probe))[0]
    assert ((fs > 0) & (fs < 1)).any() and (fs[want] > 0).all() and (fs[fs == 1] == 1).all()


def test_the_leaf_refuses_an_open_body_and_forgives_a_lost_one(proj: P) -> None:
    mid = plain_model(proj, **GRID_F)
    surface = _surface(proj)  # a surface is no body
    with pytest.raises(ApiError) as e:
        shares(proj, mid, leaf_rule(surface))
    assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "wireframe_open", "rules.wireframe.id")
    from tests.test_filters_store import group

    deep = group("or", leaf_rule(777_777), leaf_rule(None, "outside"))
    f, warnings = shares(proj, mid, deep)  # a body the project has lost: share 0 and a warning (as a surface)
    assert not f.any() and len(warnings) == 2 and all("недоступно" in w for w in warnings)
    f2, _ = shares(proj, mid, {"op": "not", "items": [leaf_rule(777_777)]})
    assert (f2 == 1).all()
    far = body(proj, (500.0, 500.0, 500.0), (520.0, 520.0, 520.0), "Далеко")
    f3, w3 = shares(proj, mid, leaf_rule(far))
    assert not f3.any() and len(w3) == 1 and "«Далеко»" in w3[0]
    assert (shares(proj, mid, leaf_rule(far, "outside"))[0] == 1).all()
    # a grid of a variable step has no rays of ours
    grid = bs.GridV2.of({"corner": [0, 0, 0], "counts": [2, 1, 1], "tensors": {"x": [1, 2], "y": [3], "z": [4]}})
    with bm.CellsWriterV2(proj.store, grid, [{"name": "AU", "dtype": "f8"}], sub=False) as w:
        w.write(np.arange(2, dtype=np.uint64), {"AU": np.array([1.0, 2.0])})
        staged = w.finish()
    with dialect.write_tx(proj.engine) as conn:
        var = bm.commit_block_model(conn, proj.store, staged, name="Переменный", source={"format": "omf", "file": "m"})
    with pytest.raises(ApiError) as e2:
        shares(proj, var, leaf_rule(far))
    assert e2.value.code == "variable_grid"


# --- the routes ------------------------------------------------------------------------------------------------------

MODULE = "bf_core.blocks.fill.jobs"


@pytest.fixture()
def rc(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A core with an open project and the routes of the fill (until `blocks/router.py` includes them); the jobs
    registered here and in the worker until the line of the orchestrator (`jobs/registry.py`)."""
    import importlib

    from bf_core.blocks.fill import routes
    from bf_core.jobs.registry import HANDLER_MODULES

    if MODULE not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", MODULE)
    importlib.import_module(MODULE)
    if not any(getattr(r, "path", "") == f"{BL}/fill" for r in pclient.app.routes):
        pclient.app.include_router(routes.router, prefix=BL)
    assert pclient.post(f"{API}/projects", json={"name": "Заполнение"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    pclient.p = P(project.engine, project.folder)
    return pclient


def job_of(c: Any, r: Any, timeout: float = 240) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=timeout)
    return job  # type: ignore[no-any-return]


def test_the_routes_check_the_request_before_the_job(rc) -> None:  # type: ignore[no-untyped-def]
    c, p = rc, rc.p
    proto = prototype(p)
    a = body(p, *A_BOX, name="A")
    plain = plain_model(p)
    surface = _surface(p)
    fill_ = BL + "/fill"
    for payload, status, code, param in (
        (request(proto, a, mode="сбоку"), 422, "fill_mode", "mode"),
        (request(proto, a, m=99), 422, "bad_param", "m"),
        (request(proto), 422, "bad_param", "wireframes"),
        (request(777_777, a), 404, "block_model_not_found", None),
        (request(plain, a), 422, "not_prototype", "prototype_id"),
        (request(proto, 777_777), 404, "wireframe_not_found", "wireframes.0.id"),
        (request(proto, a, surface), 422, "wireframe_open", "wireframes.1.id"),
        (request(proto, a, levels=[1, 9, 1]), 422, "bad_param", "levels.1"),
    ):  # fmt: skip
        r = c.post(fill_, json=payload)
        assert r.status_code == status and r.json()["code"] == code and r.json()["hint"], (payload, r.text)
        assert param is None or r.json()["param"] == param, (payload, r.text)
    assert c.post(fill_, json=[1]).status_code == 422
    # the coding
    base = creq(a, version=1)
    stale_ = c.post(f"{BL}/{plain}/code", json={**base, "expected_version": 5})
    assert stale_.status_code == 409 and stale_.json()["version"] == 1  # the current version goes with the 409
    for model_id, payload, status, code in (
        (plain, {**base, "expected_version": 5}, 409, "version_conflict"),
        (plain, {**base, "code_field": "AU"}, 409, "field_exists"),
        (plain, {k: v for k, v in base.items() if k != "expected_version"}, 409, "version_conflict"),
        (plain, {**base, "split": "да"}, 422, "bad_param"),
        (plain, creq(777_777), 404, "wireframe_not_found"),
        (proto, base, 422, "block_model_empty"),
        (777_777, base, 404, "block_model_not_found"),
    ):  # fmt: skip
        r = c.post(f"{BL}/{model_id}/code", json=payload)
        assert (r.status_code, r.json()["code"]) == (status, code), (payload, r.text)
    # «Перестроить»
    for model_id, payload, status, code in (
        (plain, {"expected_version": 1}, 422, "not_rebuildable"),  # an imported model
        (proto, {"expected_version": 1}, 422, "not_rebuildable"),  # a prototype is not made by a fill
        (plain, {"expected_version": 7}, 409, "version_conflict"),
        (plain, {}, 409, "version_conflict"),
        (777_777, {"expected_version": 1}, 404, "block_model_not_found"),
    ):  # fmt: skip
        r = c.post(f"{BL}/{model_id}/rebuild", json=payload)
        assert (r.status_code, r.json()["code"]) == (status, code), (model_id, r.text)
    assert block_models(p) == sorted([proto, plain]) and tmp_clean(p)  # nothing was started


def test_jobs_in_a_worker_fill_code_and_rebuild(rc) -> None:  # type: ignore[no-untyped-def]
    c, p = rc, rc.p
    proto = prototype(p)
    a = body(p, *A_BOX, name="Руда")
    cut = body(p, (15.0, 15.0, 5.0), (47.5, 32.5, 30.0), "Зона")
    job = job_of(c, c.post(BL + "/fill", json=request(proto, a, mode="both")))
    assert job["status"] == "done", job
    res = job["result"]
    mid = res["model_id"]
    assert res["volume"] == {"Руда": pytest.approx(24000.0), "вне": pytest.approx(GRID_VOLUME - 24000.0)}
    assert set(res["stages"]) == {"wireframes", "columns", "boundary", "split", "merge", "write", "total"}
    assert res["events"] == [["objects.changed", {"kind": "block_model", "id": mid}]]
    got = c.get(f"{BL}/{mid}").json()
    assert got["version"] == 1 and got["cells"] == res["rows"]
    assert model(p, mid).meta["source"]["op"] == "fill" and model(p, mid).meta["inputs"]["wireframes"][0]["id"] == a
    # the coding by the centre: new fields of the same model
    coded = job_of(c, c.post(f"{BL}/{mid}/code", json=creq(cut, version=got["version"], code_field="ZONE",
                                                          frac_field=None)))  # fmt: skip
    assert coded["status"] == "done" and coded["result"]["version"] == 2 and coded["result"]["fields"] == ["ZONE"]
    # the coding with splitting: a new model
    split = job_of(c, c.post(f"{BL}/{mid}/code", json=creq(cut, version=2, split=True, code_field="Z2",
                                                          frac_field=None)))  # fmt: skip
    assert split["status"] == "done" and split["result"]["model_id"] != mid
    sid = split["result"]["model_id"]
    assert model(p, sid).meta["source"]["op"] == "code"
    # «Перестроить» a fill: the field ZONE of another maker goes
    redo = job_of(c, c.post(f"{BL}/{mid}/rebuild", json={"expected_version": 2}))
    assert redo["status"] == "done" and redo["result"]["dropped"] == ["ZONE"] and redo["result"]["version"] == 3
    assert [f["name"] for f in model(p, mid).fields] == ["CODE", "FRAC"]
    stale_ = c.post(f"{BL}/{mid}/rebuild", json={"expected_version": 2})
    assert stale_.status_code == 409 and stale_.json()["version"] == 3
    # … and a coding with splitting; the body goes → 409 `source_lost`, nothing is started
    again = job_of(c, c.post(f"{BL}/{sid}/rebuild", json={"expected_version": 1}))
    assert again["status"] == "done" and again["result"]["model_id"] == sid and again["result"]["version"] == 2
    with dialect.write_tx(p.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == cut))
    lost = c.post(f"{BL}/{sid}/rebuild", json={"expected_version": 2})
    assert lost.status_code == 409 and lost.json()["code"] == "source_lost" and lost.json()["hint"]
    assert block_models(p) == sorted([proto, mid, sid]) and tmp_clean(p)


def test_a_job_that_fails_reports_the_code_of_the_refusal(rc) -> None:  # type: ignore[no-untyped-def]
    c, p = rc, rc.p
    proto = prototype(p)
    # two shells that cross: the normals of the rays disagree (the stored stats say «closed», the rays do not)
    v1, t1 = box((10.0, 10.0, 10.0), (40.0, 40.0, 30.0))
    v2, t2 = box((30.0, 30.0, 20.0), (55.0, 50.0, 40.0))
    v = np.concatenate([v1, v2])
    t = np.concatenate([t1, t2 + len(v1)])
    with dialect.write_tx(p.engine) as conn:
        both = wf.put_wireframe(conn, p.store, v, t, name="Две оболочки")
    job = job_of(c, c.post(BL + "/fill", json=request(proto, both)))
    assert job["status"] == "failed" and job["error"]["code"] == "wireframe_orientation", job
    assert job["error"]["hint"] and block_models(p) == [proto] and tmp_clean(p)


# --- PostgreSQL (BF_TEST_PG; SQLite always) ------------------------------------------------------------------------


@pytest.mark.db
def test_fill_and_coding_on_both_databases(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path)
    proto = prototype(p)
    a = body(p, *A_BOX, name="Руда")
    out = run(p, request(proto, a, mode="both"))
    mid = out["model_id"]
    assert out["volume"]["Руда"] == pytest.approx(24000.0) and stale(p, mid) is None
    coded = run_code(p, mid, creq(a, version=1, code_field="ZONE", frac_field=None, split=False))
    assert coded["version"] == 2
    split = run_code(p, mid, creq(a, version=2, split=True, code_field="Z2", frac_field=None))
    assert model(p, split["model_id"]).meta["source"]["op"] == "code"
    redo = fj.run_fill(engine, tmp_path, {"spec": model(p, mid).meta["source"]["params"],
                                          "rebuild": {"model_id": mid, "expected_version": 2}})  # fmt: skip
    assert redo["version"] == 3 and redo["dropped"] == ["ZONE"] and tmp_clean(p)
    with pytest.raises(JobCancelled):
        run(p, request(proto, a), cancel_at=3)
    assert block_models(p) == sorted([proto, mid, split["model_id"]]) and tmp_clean(p)
    engine.dispose()
