"""The structural anisotropy in the jobs `implicit.build` / `implicit.update` (подшаг 19.12б; ТЗ 8.9.2, Д-15; ADR 0026
решение 11, ТД15-7, Т8.9-8; contract of 19.9): every patch has its own A_j = diag(1, 1/r2, 1/r3)·R_j at its centre,
the octree and W stay in the project coordinates, the same A_j go to the solver, to η, to the field, to the hashes of
the cache and into the files. The trend of one plane is the global A of its angles (the field, 1e−9), the update after
an edit is the full build bit for bit, a changed measurement table makes the model stale and the patches are solved
again, the threads change no byte, the cache of a patch rebuilds the frame of a solved one, `load_fields` gives
`matrices` for the check (19.17). The database tests: SQLite always, PostgreSQL with BF_TEST_PG (`-m db`)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables
from bf_core.drillholes import store as dh
from bf_core.geostat import rotation
from bf_core.geostat import trend as tr
from bf_core.geostat import trend_store as ts
from bf_core.model import anisotropy, update
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model.bodies_parts import fit_group
from bf_core.model.octree import build_patches
from bf_core.model.solve import solve_patches
from tests.test_model_data import read_csv
from tests.test_model_data_load import World, noop
from tests.test_model_job import (
    _roles,  # noqa: F401 - the autouse fixture
    geometry,
    groups_of,
    kids,
    make,
    meta_of,
    run,
    shas,
    version,
    world,  # noqa: F401 - the fixture
)
from tests.test_model_update import SCENE, edited_table, point_at, small_patches

pytestmark = pytest.mark.db
DEPTHS = (8.0, 25.0, 45.0, 70.0, 95.0)
RATIOS = [1.0, 0.5, 0.25]


def measurements(w: World, alpha: float, dipdir: float, name: str = "Замеры") -> int:
    """A table of the set with a measurement «α, Aп» of ONE plane at the depths of every hole."""
    holes = [r["HOLE"] for r in read_csv("collars.csv")]
    rows = [(h, d) for h in holes for d in DEPTHS]
    data = dh.TableData(
        name=name, kind="event", holes=[h for h, _ in rows], from_m=[d for _, d in rows], to_m=[d for _, d in rows],
        columns=[dh.ColumnSpec("ALPHA", "number", "°"), dh.ColumnSpec("AZ_DIP", "number", "°"),
                 dh.ColumnSpec("DEPTH", "number", "м")],
        values=[[alpha] * len(rows), [dipdir] * len(rows), [d for _, d in rows]])  # fmt: skip
    return int(dh.write_table(w.engine, w.set_id, data, job_id=None, tick=noop)["table_id"])


def structural(w: World, table: int, *, radius: float = 400.0, ratios: Any = RATIOS, name: str = "Структура") -> int:
    source = {"type": "measurements", "set_id": w.set_id, "table": str(table), "alpha": "ALPHA", "dipdir": "AZ_DIP",
              "depth": "DEPTH"}  # fmt: skip
    with dialect.write_tx(w.engine) as conn:
        return int(ts.create(conn, {"kind": "structural", "source": source, "radius": radius, "ratios": ratios,
                                    "name": name})["id"])  # fmt: skip


def fields(w: World, mid: int) -> dict[Any, Any]:
    with dialect.read_tx(w.engine) as conn:
        return st.load_fields(conn, w.store, mid)


def plane_matrix(dip: float, dipdir: float, ratios: Any = RATIOS) -> Any:
    ang = rotation.angles_of_plane(dip, dipdir, 0.0)
    return tr.matrix({"ang1": ang[0], "ang2": ang[1], "ang3": ang[2], "ratios": list(ratios)})


@pytest.fixture()
def scene(world: World):  # type: ignore[no-untyped-def]  # noqa: F811
    """The world, a table of measurements of one plane and a structural trend on it."""
    w = world
    table = measurements(w, 35.0, 120.0)
    return w, table, structural(w, table)


# --- the job builds it ------------------------------------------------------------------------------------------------


def test_a_structural_trend_is_built_and_the_matrices_of_the_patches_are_in_the_files(scene) -> None:  # type: ignore[no-untyped-def]
    w, _table, trend = scene
    m = make(w, "Структурная", groups_of("SS", "SH"), trend_id=trend, **small_patches())
    out = run(w, m["id"])
    assert out["state"] == "ready" and out["stats"]["patches"] > 3
    assert any("структурная анизотропия" in line for line in out["journal"])
    got = fields(w, m["id"])
    assert set(got) == {(3, None), (4, None)}
    with dialect.read_tx(w.engine) as conn:
        _name, meta = ts.meta_of(conn, trend)
    from bf_core.geostat import trend_struct as tst

    src = tst.load_source(w.project, meta)
    for data in got.values():
        mats = data.matrices
        centres = np.column_stack([data.patches["cx"], data.patches["cy"], data.patches["cz"]])
        assert mats is not None and mats.shape == (len(centres), 3, 3) and np.isfinite(mats).all()
        assert mats.tobytes() == anisotropy.local(meta, centres, source=src).tobytes()  # what the solver was given
        assert np.abs(mats - plane_matrix(35.0, 120.0)).max() <= 1e-12  # one plane: the same A_j in every patch
    plain = make(w, "Без тренда", groups_of("SS"), **small_patches())
    run(w, plain["id"])
    assert all(d.matrices is None for d in fields(w, plain["id"]).values())  # the file of a global / no A is as it was
    glob = make(w, "Глобальная", groups_of("SS"), anisotropy={"ang1": 20.0, "ang2": 0.0, "ang3": 0.0, "ratios": RATIOS},
                **small_patches())  # fmt: skip
    run(w, glob["id"])
    assert all(d.matrices is None for d in fields(w, glob["id"]).values())


def test_td15_7_the_trend_of_one_plane_is_the_global_a_of_its_angles(scene) -> None:  # type: ignore[no-untyped-def]
    """One patch (the same partition in both ways): the field of the structural fit is that of the global A (1e−9), and
    so are the bodies of the job (the volumes, the vertices to the accuracy of the geometry)."""
    w, _table, trend = scene
    ang = rotation.angles_of_plane(35.0, 120.0, 0.0)
    hand = {"ang1": ang[0], "ang2": ang[1], "ang3": ang[2], "ratios": RATIOS}
    one = {"pou": {"n_patch": 4000, "n_min": 100, "delta": 0.25}}
    a = make(w, "Тренд", groups_of("SS"), trend_id=trend, **one)
    b = make(w, "Вручную", groups_of("SS"), anisotropy=hand, **one)
    ra, rb = run(w, a["id"], threads=2), run(w, b["id"], threads=2)
    assert ra["stats"]["patches"] == rb["stats"]["patches"] == 1
    assert [o["triangles"] for o in ra["outputs"]] == [o["triangles"] for o in rb["outputs"]]
    for oa, ob in zip(ra["outputs"], rb["outputs"], strict=True):
        for key in ("volume", "area"):
            if ob[key] is not None:
                assert abs(oa[key] - ob[key]) <= 1e-9 * abs(ob[key]), (key, oa[key], ob[key])
    ga, gb = geometry(w, a["id"]), geometry(w, b["id"])
    for (_ka, va, ta), (_kb, vb, tb) in zip(ga, gb, strict=True):
        assert ta == tb
        pa, pb = np.frombuffer(va, dtype="<f8"), np.frombuffer(vb, dtype="<f8")
        assert pa.shape == pb.shape and np.abs(pa - pb).max() <= 1e-6  # metres: the vertices on the edges of the grid


def test_td15_7_the_field_of_the_fit(scene) -> None:  # type: ignore[no-untyped-def]
    """`fit_group` with `matrices_of` of one plane and with `matrix=A` (one patch): one function to 1e−9."""
    from bf_core.model import data as md
    from bf_core.model.job_stages import Structural
    from bf_core.model.spec import parse_model

    w, _table, trend = scene
    m = make(w, "Поле", groups_of("SS"), trend_id=trend, pou={"n_patch": 4000, "n_min": 100, "delta": 0.25})
    with dialect.read_tx(w.engine) as conn:
        spec = parse_model(sb.load_spec(meta_of(w, m["id"])).canonical())
        _name, meta = ts.meta_of(conn, trend)
    pts = md.load(w.project, spec).of_group(3)
    extent = (np.array([0.0, 0.0, 120.0]), np.array([420.0, 420.0, 330.0]))
    journal: list[str] = []
    loc = fit_group(spec, spec.group(3), pts, extent=extent, matrices_of=Structural(w.project, meta, 1, journal),
                    threads=2)  # fmt: skip
    glob = fit_group(spec, spec.group(3), pts, extent=extent, matrix=plane_matrix(35.0, 120.0), threads=2)
    assert loc.stats["patches"] == glob.stats["patches"] == 1 and loc.patches.matrix is None
    q = np.random.default_rng(1).uniform([20.0, 20.0, 150.0], [400.0, 400.0, 310.0], size=(3000, 3))
    fa, fb = loc.field(q), glob.field(q)
    assert np.isfinite(fa).all() and np.abs(fa - fb).max() <= 1e-9 * np.abs(pts.f).max()
    assert any("структурная анизотропия" in line for line in journal)


# --- «Обновить» -------------------------------------------------------------------------------------------------------


def test_t8_9_8_the_update_with_a_structural_trend_is_the_full_build_bit_for_bit(scene) -> None:  # type: ignore[no-untyped-def]
    w, _table, trend = scene
    m = make(w, "Основа", groups_of(*SCENE), trend_id=trend, **small_patches())
    first = run(w, m["id"])
    ids = {(o["kind"], o["group"]): o["wireframe_id"] for o in first["outputs"]}
    t2 = edited_table(w)
    point_at(w, m["id"], t2)
    upd = run(w, m["id"], update=True, threads=2)
    full = make(w, "Полное", groups_of(*SCENE), table=t2, trend_id=trend, **small_patches())
    ref = run(w, full["id"], threads=1)
    assert geometry(w, m["id"]) == geometry(w, full["id"])
    assert shas(w, m["id"]) == shas(w, full["id"])  # the points, the patches with the A_j and the hashes, the solution
    total = ref["stats"]["patches"]
    assert upd["stats"]["patches"] == total and upd["reused"]["bricks"] > 0
    assert 0 < upd["reused"]["patches"] < total
    assert {(o["kind"], o["group"]): o["wireframe_id"] for o in upd["outputs"]} == ids
    assert sorted(kids(w, m["id"])) == sorted(ids.values())
    nothing = run(w, m["id"], update=True)  # nothing changed: everything is taken, the same bits
    assert nothing["reused"]["patches"] == total and geometry(w, m["id"]) == geometry(w, full["id"])


def test_the_measurements_of_the_trend_change_the_model_is_stale_and_every_patch_is_solved_again(
    scene,
) -> None:  # type: ignore[no-untyped-def]
    w, table, trend = scene
    m = make(w, "Основа", groups_of("SS", "SH"), trend_id=trend, **small_patches())
    run(w, m["id"])
    with dialect.read_tx(w.engine) as conn:
        assert st.freshness(conn, meta_of(w, m["id"]))["stale"] is False
    with dialect.write_tx(w.engine) as conn:  # the measurements are edited: the table has a new revision
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == table).values(rev=dh_tables.c.rev + 1))
    with dialect.read_tx(w.engine) as conn:
        fresh = st.freshness(conn, meta_of(w, m["id"]))
    assert fresh["stale"] is True and fresh["stale_reason"] == "изменена таблица «Замеры»"
    # other measurements (another plane) under the trend: the trend has a new version too
    t2 = measurements(w, 70.0, 300.0, "Замеры 2")
    source = {"type": "measurements", "set_id": w.set_id, "table": str(t2), "alpha": "ALPHA", "dipdir": "AZ_DIP",
              "depth": "DEPTH"}  # fmt: skip
    with dialect.write_tx(w.engine) as conn:
        ts.update(conn, trend, {"source": source, "expected_version": 1})
    with dialect.read_tx(w.engine) as conn:
        assert st.freshness(conn, meta_of(w, m["id"]))["stale"] is True
    old = shas(w, m["id"])
    upd = run(w, m["id"], update=True)
    assert upd["reused"] == {"patches": 0, "bricks": 0}  # every A_j moved: nothing of the cache is taken
    full = make(w, "Полное", groups_of("SS", "SH"), trend_id=trend, **small_patches())
    run(w, full["id"], threads=1)
    assert geometry(w, m["id"]) == geometry(w, full["id"]) and shas(w, m["id"]) == shas(w, full["id"])
    assert shas(w, m["id"]) != old
    with dialect.read_tx(w.engine) as conn:
        assert st.freshness(conn, meta_of(w, m["id"]))["stale"] is False


def test_the_automatic_smoothing_sees_the_frames_of_the_patches(scene) -> None:  # type: ignore[no-untyped-def]
    """«Авто» (Д-25): η of the patches is measured in the space of A_j; the update is the full build."""
    w, _table, trend = scene
    over = {"smoothing": {"mode": "auto", "s": 0.01, "cond_target": 1e6}, "trend_id": trend, **small_patches()}
    m = make(w, "Авто", groups_of("SS", "SH"), **over)
    first = run(w, m["id"], threads=2)
    again = run(w, m["id"], update=True, threads=3)
    assert first["stats"]["eta_max"] > 0 and again["stats"]["eta_max"] == first["stats"]["eta_max"]
    assert again["reused"]["patches"] == first["stats"]["patches"]
    full = make(w, "Авто полное", groups_of("SS", "SH"), **over)
    run(w, full["id"], threads=1)
    assert shas(w, m["id"]) == shas(w, full["id"]) and geometry(w, m["id"]) == geometry(w, full["id"])


@pytest.mark.parametrize("threads", [1, 2, 4, 8])
def test_the_same_bits_with_1_2_4_8_threads(scene, threads: int) -> None:  # type: ignore[no-untyped-def]
    """The files and the geometry of a structural build do not depend on the threads (the reference — 1 thread)."""
    w, _table, trend = scene
    mk = lambda name: make(w, name, groups_of("SS", "SH"), trend_id=trend, **small_patches())  # noqa: E731
    ref, got = mk("Эталон"), mk("Потоки")
    run(w, ref["id"], threads=1)
    run(w, got["id"], threads=threads)
    assert shas(w, got["id"]) == shas(w, ref["id"]) and geometry(w, got["id"]) == geometry(w, ref["id"])


def test_a_shell_group_with_a_structural_trend(scene) -> None:  # type: ignore[no-untyped-def]
    from tests.test_model_job import shell

    w, _table, trend = scene
    sh = shell(7, w, thresholds=[0.2], source="table", table=str(w.assays), field="AU", mode="numeric")
    m = make(w, "Оболочки", [*groups_of("SS"), sh], trend_id=trend, **small_patches())
    out = run(w, m["id"], threads=2)
    assert any(o["group"] == 7 for o in out["outputs"])
    assert all(d.matrices is not None for d in fields(w, m["id"]).values())
    upd = run(w, m["id"], update=True, threads=3)
    assert upd["reused"]["patches"] == upd["stats"]["patches"] and upd["reused"]["bricks"] > 0


# --- the cache and the errors -----------------------------------------------------------------------------------------


def test_a_patch_of_the_cache_has_the_frame_of_a_solved_one_and_the_hash_sees_the_matrix() -> None:
    """`CachingSolver` with `matrices`: the solution taken from the cache is the solved one (the points turned, the
    centre, the scale R·‖A_j‖₂: the same bytes of the field); a changed A_j is a miss."""
    from bf_core.model.evaluate import PatchField
    from bf_core.model.spec import parse_model

    rng = np.random.default_rng(5)
    pts = rng.uniform(-80.0, 80.0, size=(400, 3))
    f = np.sin(pts[:, 0] / 30.0) + 0.01 * pts[:, 2]
    spec = parse_model({"boundary": {"box": [-100, -100, -100, 100, 100, 100]}, "groups": [], "kernel": "linear"})
    ps = build_patches(pts, n_patch=90, n_min=60)
    assert ps.n_patches > 2
    ang = np.radians(np.linspace(0.0, 50.0, ps.n_patches))
    frames = np.stack([rotation.rotation_matrix(float(a), 20.0, 0.0) for a in np.degrees(ang)])
    mats = [np.ascontiguousarray(np.diag([1.0, 2.0, 4.0]) @ r) for r in frames]
    first = update.CachingSolver(spec, update.PatchCache.empty())
    sols = first(pts, f, ps, kind="linear", eta=0.0, matrices=mats, sources=np.arange(len(pts)))
    assert first.last() is not None and first.last().matrices is not None  # type: ignore[union-attr]
    again = update.CachingSolver(spec, update.PatchCache.from_records(first.records))
    taken = again(pts, f, ps, kind="linear", eta=0.0, matrices=mats, sources=np.arange(len(pts)))
    assert again.reused() == (ps.n_patches, ps.n_patches)
    for s, t in zip(sols, taken, strict=True):
        assert (s.points.tobytes(), s.centre.tobytes(), s.scale) == (t.points.tobytes(), t.centre.tobytes(), t.scale)
    q = rng.uniform(-70.0, 70.0, size=(500, 3))
    fa = PatchField(ps, sols, matrices=mats)(q)
    fb = PatchField(ps, taken, matrices=mats)(q)
    assert fa.tobytes() == fb.tobytes()
    direct = PatchField(ps, solve_patches(pts, f, ps, kind="linear", matrices=mats), matrices=mats)(q)
    assert fa.tobytes() == direct.tobytes()
    other = [m.copy() for m in mats]
    other[1] = other[1] * np.array([[1.0], [1.0], [1.01]])
    third = update.CachingSolver(spec, update.PatchCache.from_records(first.records))
    third(pts, f, ps, kind="linear", eta=0.0, matrices=other, sources=np.arange(len(pts)))
    assert third.reused() == (ps.n_patches - 1, ps.n_patches)  # only the patch whose A_j moved is solved again


def test_the_source_of_the_trend_is_gone_the_model_goes_to_error_with_the_reason(
    scene, monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    from bf_core.geostat import trend_struct as tst

    w, _table, trend = scene
    m = make(w, "Нет замеров", groups_of("SS"), trend_id=trend, **small_patches())

    def gone(*_a: Any, **_k: Any) -> Any:
        raise tst.source_lost("Замеры")

    monkeypatch.setattr(tst, "load_source", gone)
    with pytest.raises(ApiError) as e:
        run(w, m["id"])
    assert e.value.status == 409 and e.value.code == "source_gone" and e.value.hint
    assert meta_of(w, m["id"])["state"] == "error" and kids(w, m["id"]) == [] and version(w, m["id"]) >= 1


def test_a_structural_trend_is_no_more_refused(scene) -> None:  # type: ignore[no-untyped-def]
    from bf_core.model import job_stages

    w, _table, trend = scene
    m = make(w, "Тренд", groups_of("SS"), trend_id=trend)
    with dialect.read_tx(w.engine) as conn:
        a, meta = job_stages.anisotropy_of(conn, sb.load_spec(meta_of(w, m["id"])))
    assert a is None and meta is not None and meta["kind"] == "structural"


def test_the_patch_hashes_of_a_model_with_a_frame_differ_from_those_without() -> None:
    hashes = ("a" * 64, "b" * 64)
    a = anisotropy.patch_hashes(hashes, [np.eye(3), np.diag([1.0, 2.0, 3.0])])
    assert a != hashes and len(set(a)) == 2
    assert anisotropy.patch_hashes(hashes, [None, None]) == hashes
