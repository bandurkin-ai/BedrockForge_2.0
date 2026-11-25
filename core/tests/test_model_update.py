"""«Обновить» (подшаг 19.12; ТЗ 8.9.3, Т8.9-8; ADR 0026 «Варианты» → «Обновить» А, решения 23, 25; e19-api §2.2): the
update after an edit of a few holes is bit for bit the full build of the same data — the geometry of the outputs and
the three files — whatever the threads; it solves only the patches whose content changed and reads the bricks whose
patches did not; the outputs keep their ids; an update with nothing to take is a full build; the keys of the bricks
name the patches that reach them; a damaged or foreign cache is a miss, never a wrong brick. The database tests:
SQLite always, PostgreSQL with BF_TEST_PG (`-m db`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.model import octree, update
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model.spec import parse_model
from tests.test_model_data import read_csv
from tests.test_model_data_load import World
from tests.test_model_job import (
    _roles,  # noqa: F401 - the autouse fixture: the roles of the files until `db/schema.py` has them
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

pytestmark = pytest.mark.db
SCENE = ("INT", "ALV", "SS", "SH", "GR")


def edit_rows(count: int, shift: float = 3.0) -> list[dict[str, str]]:
    """The rows of the lithology with the contacts of the `count` holes nearest to (60, 60) moved by `shift` metres: a
    few neighbours of a campaign, not holes spread over the model."""
    rows = read_csv("lithology.csv")
    at = {r["HOLE"]: (float(r["X"]), float(r["Y"])) for r in read_csv("collars.csv")}
    pick = set(sorted(at, key=lambda k: (at[k][0] - 60.0) ** 2 + (at[k][1] - 60.0) ** 2)[:count])
    by: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        by.setdefault(r["HOLE"], []).append(dict(r))
    out: list[dict[str, str]] = []
    for hole, lst in by.items():
        if hole in pick:
            for a, b in zip(lst, lst[1:], strict=False):
                if a["TO"] == b["FROM"] and float(b["TO"]) - float(a["TO"]) > 4.0:
                    a["TO"] = b["FROM"] = f"{float(a['TO']) + shift:.2f}"
        out.extend(lst)
    return out


def edited_table(w: World, count: int = 5) -> int:
    """A new table of the same name with the edited rows (the old one goes: the project has the data of a campaign)."""
    return w._table("Литология", "interval", edit_rows(count), "CODE", "text")  # noqa: SLF001


def point_at(w: World, mid: int, table: int) -> None:
    """The model reads its codes from `table` now (a PUT of the window: the version goes up)."""
    data = {"set_id": w.set_id, "table": str(table), "field": "CODE", "d_max": 30, "step": 5}
    with dialect.write_tx(w.engine) as conn:
        st.update(conn, w.store, w.project, mid, {"data": data}, version(w, mid))


def small_patches() -> dict[str, Any]:
    return {"pou": {"n_patch": 300, "n_min": 100, "delta": 0.25}}  # smaller patches: an edit touches few of them


@pytest.fixture()
def built(world: World):  # type: ignore[no-untyped-def]  # noqa: F811
    """A model of the five layers built; the same layers on the edited table are built by the tests."""
    w = world
    m = make(w, "Основа", groups_of(*SCENE), **small_patches())
    first = run(w, m["id"])
    return w, m["id"], first


# --- Т8.9-8 -----------------------------------------------------------------------------------------------------------


def test_the_update_after_an_edit_is_the_full_build_bit_for_bit_t898(built) -> None:  # type: ignore[no-untyped-def]
    w, mid, first = built
    ids = {(o["kind"], o["group"]): o["wireframe_id"] for o in first["outputs"]}
    t2 = edited_table(w)
    point_at(w, mid, t2)
    upd = run(w, mid, update=True, threads=2)  # another number of threads than the build: still the same bits
    full = make(w, "Полное", groups_of(*SCENE), table=t2, **small_patches())
    ref = run(w, full["id"], threads=1)
    assert upd["mode"] == "update" and ref["mode"] == "build"
    assert geometry(w, mid) == geometry(w, full["id"])  # the outputs: vertices and triangles, byte for byte
    assert shas(w, mid) == shas(w, full["id"])  # the points, the patches with their hashes, the solution
    figures = ("kind", "group", "threshold", "volume", "area", "triangles")
    assert [[o[k] for k in figures] for o in upd["outputs"]] == [[o[k] for k in figures] for o in ref["outputs"]]
    total = ref["stats"]["patches"]
    assert upd["stats"]["patches"] == total and upd["reused"]["bricks"] > 0
    assert 0 < upd["reused"]["patches"] < total  # the patches of the edited holes were solved again, the rest not
    assert any(f"{upd['reused']['patches']} из {total}" in line for line in upd["journal"])
    assert {(o["kind"], o["group"]): o["wireframe_id"] for o in upd["outputs"]} == ids  # replaced in place
    assert sorted(kids(w, mid)) == sorted(ids.values())
    meta = meta_of(w, mid)
    assert meta["last_run"]["mode"] == "update" and meta["state"] == "ready"
    assert meta["stats"]["reused"] == upd["reused"]


def test_an_update_with_nothing_changed_takes_everything_and_gives_the_same_bits(built) -> None:  # type: ignore[no-untyped-def]
    w, mid, first = built
    before, files = geometry(w, mid), shas(w, mid)
    again = run(w, mid, update=True)
    assert again["reused"]["patches"] == first["stats"]["patches"]
    assert again["reused"]["bricks"] == sum(1 for _ in update.cache_dir(w.project.folder, mid).glob("*.npy")) > 0
    assert geometry(w, mid) == before and shas(w, mid) == files
    names = {p.stem for p in update.cache_dir(w.project.folder, mid).glob("*.npy")}
    assert len(names) == again["reused"]["bricks"]  # the files of the keys nobody asked for are swept


def test_the_cache_files_of_the_changed_patches_go_after_the_update(built) -> None:  # type: ignore[no-untyped-def]
    w, mid, _first = built
    d = update.cache_dir(w.project.folder, mid)
    old = {p.name for p in d.glob("*.npy")}
    point_at(w, mid, edited_table(w))
    out = run(w, mid, update=True)
    now = {p.name for p in d.glob("*.npy")}
    assert now - old and old - now  # new keys were written, the keys that name the changed patches were swept
    assert len(now & old) == out["reused"]["bricks"]  # exactly the bricks that were read stay


def test_an_update_without_a_result_is_a_full_build(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    w = world
    a, b = (make(w, n, groups_of("ALV", "SS")) for n in ("Раз", "Два"))
    full = run(w, a["id"])
    upd = run(w, b["id"], update=True)  # no file, no cache: nothing to take
    assert upd["reused"] == {"patches": 0, "bricks": 0} and upd["mode"] == "update"
    assert geometry(w, a["id"]) == geometry(w, b["id"]) and shas(w, a["id"]) == shas(w, b["id"])
    assert upd["stats"]["patches"] == full["stats"]["patches"]


def test_a_cache_made_by_other_libraries_is_not_used(built) -> None:  # type: ignore[no-untyped-def]
    w, mid, first = built
    before = geometry(w, mid)
    with dialect.write_tx(w.engine) as conn:
        meta = dict(sb.stored(conn, mid).meta)
        meta["versions"] = {**meta["versions"], "numpy": "0.0.1"}
        conn.execute(sa.update(objects).where(objects.c.id == mid).values(meta=meta))
    out = run(w, mid, update=True)
    assert out["reused"] == {"patches": 0, "bricks": 0}  # the hashes mean what the program that made them meant
    assert geometry(w, mid) == before and first["stats"]["patches"] == out["stats"]["patches"]


def test_a_changed_parameter_takes_nothing_it_should_not(built) -> None:  # type: ignore[no-untyped-def]
    """The kernel changes every solution: no patch and no brick of the cache may be taken, and the result is the full
    build of the new parameters."""
    w, mid, _first = built
    with dialect.write_tx(w.engine) as conn:
        st.update(conn, w.store, w.project, mid, {"kernel": "cubic"}, version(w, mid))
    upd = run(w, mid, update=True)
    full = make(w, "Кубическое", groups_of(*SCENE), kernel="cubic", **small_patches())
    run(w, full["id"])
    assert upd["reused"] == {"patches": 0, "bricks": 0}
    assert geometry(w, mid) == geometry(w, full["id"]) and shas(w, mid) == shas(w, full["id"])


def test_the_automatic_smoothing_keeps_the_eta_of_the_patches_it_takes(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    w = world
    over = {"smoothing": {"mode": "auto", "s": 0.01, "cond_target": 1e6}, **small_patches()}
    m = make(w, "Авто", groups_of("ALV", "SS", "SH"), **over)
    run(w, m["id"])
    t2 = edited_table(w, 3)
    point_at(w, m["id"], t2)
    upd = run(w, m["id"], update=True)
    full = make(w, "Авто полное", groups_of("ALV", "SS", "SH"), table=t2, **over)
    ref = run(w, full["id"])
    assert upd["stats"]["eta_max"] == ref["stats"]["eta_max"] and upd["stats"]["eta_max"] > 0
    assert geometry(w, m["id"]) == geometry(w, full["id"]) and shas(w, m["id"]) == shas(w, full["id"])
    assert 0 <= upd["reused"]["patches"] <= ref["stats"]["patches"]


# --- the units ----------------------------------------------------------------------------------------------------


def cloud(n: int = 900, seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    xyz = rng.uniform(0.0, 100.0, (n, 3))
    return xyz, np.sin(xyz[:, 0] / 30.0) + 0.01 * xyz[:, 2] - 0.4


def solver_for(patches: octree.PatchSet, xyz: np.ndarray, f: np.ndarray, cache: update.PatchCache,
               **over: Any) -> tuple[update.CachingSolver, list[Any]]:  # fmt: skip
    spec = parse_model({"boundary": {"box": [0, 0, 0, 100, 100, 100]}, "h": 5.0, "pou": {"n_patch": 300, "n_min": 100},
                        "groups": [{"id": 1, "name": "Г", "type": "domain", "codes": [], "relation": "conformable"}],
                        "manual": [{"layer_id": 1, "role": "contact", "group": 1}], **over})  # fmt: skip
    solver = update.CachingSolver(spec, cache, threads=2)
    return solver, solver(xyz, f, patches, kind="linear", eta=0.0, threads=2, sources=np.arange(len(f)))


def test_a_cached_solution_is_the_one_the_lu_gives_bit_for_bit() -> None:
    xyz, f = cloud()
    ps = octree.build_patches(xyz, n_patch=200, n_min=100, delta=0.25)
    first, a = solver_for(ps, xyz, f, update.PatchCache.empty())
    assert first.reused() == (0, ps.n_patches) and ps.n_patches > 4
    cache = update.PatchCache.from_records(first.records)
    second, b = solver_for(ps, xyz, f, cache)
    assert second.reused() == (ps.n_patches, ps.n_patches)
    for x, y in zip(a, b, strict=True):
        assert [np.asarray(v).tobytes() for v in (x.points, x.lam, x.poly, x.centre)] == [
            np.asarray(v).tobytes() for v in (y.points, y.lam, y.poly, y.centre)]  # fmt: skip
        assert (x.scale, x.kind, x.radius, x.eta, x.cond1) == (y.scale, y.kind, y.radius, y.eta, y.cond1)
    f2 = f.copy()
    f2[7] += 0.5  # one value: the patches that hold the point are solved again, the others are the cache's
    third, c = solver_for(ps, xyz, f2, cache)
    hit = [j for j in range(ps.n_patches) if 7 in ps.patch(j)]
    taken, total = third.reused()
    assert total - taken == len(hit) and 0 < len(hit) < total
    for j in range(ps.n_patches):
        assert (c[j].lam.tobytes() == a[j].lam.tobytes()) == (j not in hit)


def test_a_singular_patch_of_the_subset_is_named_by_its_number_in_the_whole() -> None:
    from bf_core.model.system import RbfSingular

    xyz, f = cloud()
    ps = octree.build_patches(xyz, n_patch=200, n_min=100, delta=0.25)
    first, _ = solver_for(ps, xyz, f, update.PatchCache.empty())
    cache = update.PatchCache.from_records(first.records)
    j = ps.n_patches - 2
    bad = int(ps.patch(j)[-1])
    xyz2, f2 = xyz.copy(), f.copy()
    xyz2[bad + 0] = xyz2[int(ps.patch(j)[0])]  # two points in one place with different values: η = 0 cannot solve it
    f2[bad] = f2[int(ps.patch(j)[0])] + 1.0
    ps2 = octree.build_patches(xyz2, n_patch=200, n_min=100, delta=0.25)
    with pytest.raises(RbfSingular) as e:
        solver_for(ps2, xyz2, f2, cache)
    got = int(e.value.extra["patch"])
    assert f"Участок № {got + 1} " in e.value.message and e.value.hint
    with pytest.raises(RbfSingular) as full:
        solver_for(ps2, xyz2, f2, update.PatchCache.empty())
    assert full.value.extra["patch"] == got and full.value.message == e.value.message


def make_bricks(tmp_path: Path, hashes: list[bytes], centres: Any, radii: Any, **kw: Any) -> update.BrickFiles:
    from types import SimpleNamespace

    from bf_core.geo.iso_bricks import Grid

    rec = SimpleNamespace(patches=SimpleNamespace(centers=np.asarray(centres, dtype=float),
                                                  radii=np.asarray(radii, dtype=float)), hashes=hashes)
    return update.BrickFiles(tmp_path, 7, Grid.from_box([0, 0, 0], [64, 64, 64], 2.0, 2), rec, **kw)  # type: ignore[arg-type]


def test_the_key_of_a_brick_names_the_patches_that_reach_it(tmp_path: Path) -> None:
    near, far = (10.0, 10.0, 10.0), (500.0, 500.0, 500.0)
    h = [bytes([i]) * 32 for i in (1, 2)]
    base = make_bricks(tmp_path, h, [near, far], [20.0, 20.0])
    other_far = make_bricks(tmp_path, [h[0], bytes([9]) * 32], [near, far], [20.0, 20.0])
    other_near = make_bricks(tmp_path, [bytes([9]) * 32, h[1]], [near, far], [20.0, 20.0])
    a, b = (0, 0, 0), (7, 7, 7)  # the first brick is in the ball of the near patch, the other is not
    assert base.key(a) == other_far.key(a) != other_near.key(a)  # a patch that does not reach a brick is not in its key
    assert base.key(b) == other_near.key(b) == other_far.key(b)  # no patch reaches it: the same field (all +H)
    assert base.key(a) != base.key(b)
    assert base.key(a) != make_bricks(tmp_path, h, [near, far], [20.0, 2000.0]).key(a)  # the far one reaches it now
    token = make_bricks(tmp_path, h, [near, far], [20.0, 20.0], extra=b"x")
    assert token.key(a) != base.key(a)  # what else the field depends on (a shell's level) is in the key


def test_a_brick_is_read_only_when_whole_and_of_its_shape(tmp_path: Path) -> None:
    h = [bytes([1]) * 32]
    files = make_bricks(tmp_path, h, [(10.0, 10.0, 10.0)], [30.0], read=True)
    v = np.arange(125, dtype=np.float64).reshape(5, 5, 5) / 7.0
    assert files.get((0, 0, 0)) is None
    files.put((0, 0, 0), v)
    got = files.get((0, 0, 0))
    assert got is not None and got.tobytes() == v.tobytes() and files.stats.hits == 1 and files.stats.puts == 1
    path = update.cache_dir(tmp_path, 7) / f"{files.key((0, 0, 0))}.npy"
    assert np.load(path).tobytes() == v.tobytes()  # the file is a plain `.npy`
    path.write_bytes(path.read_bytes()[:-9])  # torn
    assert files.get((0, 0, 0)) is None
    path.write_bytes(b"not a brick")
    assert files.get((0, 0, 0)) is None
    files.put((0, 0, 0), v)  # written again
    assert files.get((0, 0, 0)) is not None
    writer = make_bricks(tmp_path, h, [(10.0, 10.0, 10.0)], [30.0], read=False)
    assert writer.get((0, 0, 0)) is None  # the full build never takes a brick
    assert update.sweep(tmp_path, 7, {path.stem}) == 0 and update.sweep(tmp_path, 7, set()) == 1
    assert update.sweep(tmp_path, 8, set()) == 0  # no folder: nothing to sweep


def test_a_model_deleted_takes_its_cache_with_it(built) -> None:  # type: ignore[no-untyped-def]
    w, mid, _first = built
    d = update.cache_dir(w.project.folder, mid)
    assert d.is_dir() and any(d.iterdir())
    with dialect.write_tx(w.engine) as conn:
        st.delete(conn, w.store, mid)
    assert not d.exists()
    with pytest.raises(ApiError):
        run(w, mid, update=True)
