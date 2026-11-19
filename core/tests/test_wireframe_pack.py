"""Small wireframes of one portion in a shared blob (ADR 0015 §8, 9.17): 3 000 boxes of a DXF go into packs
(`pack:<n>`), and everything that reads a wireframe — the parts of the 3D view and their levels, the click, the
section, the check, the export, the delete with the collection of the storage, a `.bfggis` cycle — sees the
same geometry bit for bit as with blobs of its own; a big wireframe keeps its own; the real TRIDB keep their
cycle byte for byte (`test_wireframes_export.py`, its wireframes now packed: `test_import_meshes.py`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs
from bf_core.formats.dxf_write_mesh import write_dxf_meshes
from bf_core.formats.meshset import MeshSet
from bf_core.formats.registry import read_objects
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.storage.gc import collect
from bf_core.viz import binary
from bf_core.viz import meshes as vm
from bf_core.wireframes import files as wf_files
from bf_core.wireframes import lod_job
from bf_core.wireframes import source as src
from bf_core.wireframes import store as ws
from tests.meshutil import cube, grid
from tests.test_archive import export
from tests.test_import_job import API, wait_job
from tests.test_import_objects import run_job
from tests.test_viz_meshes import Ctx

pytestmark = pytest.mark.windows

COUNT = 3000
F32 = 2.5e-4


def boxes(count: int = COUNT, seed: int = 17) -> list[MeshSet]:
    """Closed boxes of 12, 48 and 108 triangles scattered over 5 km on UTM coordinates (awkward doubles)."""
    rng = np.random.default_rng(seed)
    out = []
    for k in range(count):
        v, t = cube(int(rng.integers(1, 4)), float(rng.uniform(2.0, 40.0)))
        shift = rng.uniform([0.0, 0.0, -300.0], [5_000.0, 5_000.0, 300.0]) + rng.uniform(0, 1, 3) / 3.0
        out.append(MeshSet(f"Выработка {k + 1}", v + shift, t))
    return out


def corners(v: np.ndarray, t: np.ndarray) -> set[bytes]:
    """Every triangle by its corners, bit for bit, from its smallest one (the winding kept)."""
    out = set()
    for tri in np.ascontiguousarray(np.asarray(v, dtype=np.float64)[t]):
        rows = [tri[i].tobytes() for i in range(3)]
        k = rows.index(min(rows))
        out.add(b"".join(rows[k:] + rows[:k]))
    return out


def roles_of(engine: Any, ids: list[int]) -> dict[int, dict[str, str]]:
    with dialect.read_tx(engine) as conn:
        return {i: ws.blob_roles(conn, i) for i in ids}


@pytest.fixture()
def many(pclient, tmp_path: Path):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects", json={"name": "Мелкие каркасы"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    given = boxes()
    path = tmp_path / "выработки.dxf"
    write_dxf_meshes(path, given)
    ids = [int(i) for i in run_job(project.folder, {"files": [str(path)]}).result["wireframe_ids"]]
    assert len(ids) == COUNT
    with dialect.read_tx(project.engine) as conn:
        names = {i: str(ws.wireframe_info(conn, i)["name"]) for i in ids}
    by_name = {m.name: m for m in given}
    return pclient, project, ids, {i: by_name[names[i]] for i in ids}, path


def test_3000_wireframes_go_into_packs_and_read_back_bit_for_bit(many) -> None:  # type: ignore[no-untyped-def]
    _c, project, ids, given, path = many
    roles = roles_of(project.engine, ids)
    assert all(len(r) == 1 and wf_files.is_pack(next(iter(r))) for r in roles.values())
    entries = {next(iter(r.items())) for r in roles.values()}
    assert len(entries) == COUNT  # an entry each
    packs = {sha for _role, sha in entries}
    with dialect.read_tx(project.engine) as conn:
        n_blobs = int(conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar_one())
    assert len(packs) == COUNT // 500 and n_blobs == len(packs) + 1  # the packs and the file itself
    ref = {m.name: m for m in read_objects(path).meshes}  # the wireframes as the reader gives them
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        for wf_id in ids:
            v, t = ws.read_wireframe(conn, store, wf_id)
            assert corners(v, t) == corners(given[wf_id].xyz, given[wf_id].tri)
            mesh = src.read_mesh(conn, store, wf_id)  # the order of the file: `seq` in the entry
            assert mesh.xyz.tobytes() == ref[mesh.name].xyz.tobytes()
            assert mesh.tri.tobytes() == ref[mesh.name].tri.tobytes()


def test_parts_levels_pick_section_and_check_of_packed_wireframes(many) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, given, _path = many
    sample = [ids[0], ids[1], ids[777], ids[1500], ids[-1]]
    for wf_id in sample:
        m = given[wf_id]
        head = binary.decode(c.get(f"{API}/viz/wireframes/{wf_id}").content)[0]
        (p,) = head["parts"]
        r = c.get(f"{API}/viz/wireframes/{wf_id}/parts/0", params={"lod": 0})
        ph, pa = binary.decode(r.content)
        assert ph["triangles"] == p["n"] == len(m.tri) and r.headers["etag"] == p["etags"][0]
        world = pa["xyz"].reshape(-1, 3).astype(np.float64) + np.array(ph["origin"])
        assert np.abs(np.sort(world, axis=0) - np.sort(m.xyz, axis=0)).max() <= F32  # its vertices, in float32
        level = binary.decode(c.get(f"{API}/viz/wireframes/{wf_id}/parts/0", params={"lod": 2}).content)[0]
        assert level.get("standin", False) == (len(m.tri) >= 64)  # a tiny part is its own level
        # the click: a ray down through the centre of the box meets its top face
        lo, hi = m.xyz.min(axis=0), m.xyz.max(axis=0)
        centre = (lo + hi) / 2.0
        body = {"origin": [float(centre[0]), float(centre[1]), float(hi[2]) + 100.0], "direction": [0.0, 0.0, -1.0]}
        hit = c.post(f"{API}/wireframes/pick", json={**body, "wireframe_ids": [wf_id]}).json()["hit"]
        assert hit["wireframe_id"] == wf_id and abs(hit["point"][2] - hi[2]) <= 1e-9
        # the section: a vertical plane through the centre cuts one closed loop around the box
        plane = {"point": ",".join(repr(float(x)) for x in centre), "normal": "1,0,0"}
        r = c.get(f"{API}/viz/wireframes/{wf_id}/section", params=plane)
        assert r.status_code == 200, r.text
        cut = binary.decode(r.content)[1]
        assert len(cut["closed"]) == 1 and bool(cut["closed"].all())
    lod_job._run(Ctx(project, {"wireframe_id": ids[1]}))  # type: ignore[arg-type]  # the levels of a packed one
    r = c.get(f"{API}/viz/wireframes/{ids[1]}/parts/0", params={"lod": 2})
    assert "standin" not in binary.decode(r.content)[0] and r.headers["etag"]
    started = c.post(f"{API}/wireframes/check", json={"wireframe_ids": sample}).json()
    job = wait_job(c, started["source"], started["id"], timeout=120)
    assert job["status"] == "done" and job["result"]["checked"] == len(sample), job
    assert job["result"]["with_defects"] == []  # closed boxes, normals outward


def test_export_delete_collect_and_bfggis_of_packed_wireframes(many, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, given, _path = many
    store = BlobStore(project.folder)
    out = tmp_path / "выгрузка.obj"
    started = c.post(f"{API}/wireframes/export", json={"path": str(out), "format": "obj", "wireframe_ids": ids[:600]})
    assert started.status_code == 200, started.text
    job = wait_job(c, started.json()["source"], started.json()["id"], timeout=120)
    assert job["status"] == "done", job
    written = list(read_objects(out).meshes)
    assert len(written) == 600
    for wf_id, mesh in zip(ids[:600], written, strict=True):
        assert corners(mesh.xyz, mesh.tri) == corners(given[wf_id].xyz, given[wf_id].tri)

    roles = roles_of(project.engine, ids)
    first = next(iter(roles[ids[0]].values()))
    of_first = [i for i in ids if next(iter(roles[i].values())) == first]
    assert len(of_first) == 500
    with dialect.write_tx(project.engine) as conn:  # all of the first pack but one: it stays for that one
        assert not any(ws.delete_wireframe(conn, wf_id)["shared"] for wf_id in of_first[:-1])
    collect(project.engine, store, now=1e12)
    assert store.exists(first)
    with dialect.read_tx(project.engine) as conn:
        v, t = ws.read_wireframe(conn, store, of_first[-1])
    assert corners(v, t) == corners(given[of_first[-1]].xyz, given[of_first[-1]].tri)
    assert c.delete(f"{API}/wireframes/{of_first[-1]}").status_code == 200
    collect(project.engine, store, now=1e12)
    assert not store.exists(first)  # the last of its wireframes is gone: the pack goes with the collection
    left = [i for i in ids if i not in set(of_first)]

    job = export(c, tmp_path / "мелкие.bfggis")
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"], timeout=120)
    assert job["status"] == "done", job
    new = {int(k): v for k, v in job["result"]["id_maps"]["objects"].items()}
    assert c.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    copy = projects_of(c.core).require_current()
    copied = roles_of(copy.engine, [new[i] for i in left])
    assert all(copied[new[i]] == roles[i] for i in left)  # the same packs and entries
    with dialect.read_tx(copy.engine) as conn:
        for wf_id in left:
            v, t = ws.read_wireframe(conn, BlobStore(copy.folder), new[wf_id])
            assert corners(v, t) == corners(given[wf_id].xyz, given[wf_id].tri)
    r = c.get(f"{API}/viz/wireframes/{new[left[0]]}/parts/0", params={"lod": 0})
    assert r.status_code == 200 and binary.decode(r.content)[0]["triangles"] == len(given[left[0]].tri)


def test_a_big_wireframe_keeps_its_own_blobs_and_a_portion_shares_one(pclient) -> None:  # type: ignore[no-untyped-def]
    from bf_core.lines import pack as packs

    r = pclient.post(f"{API}/projects", json={"name": "Порция"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    store = BlobStore(project.folder)
    big_v, big_t = grid(300, 200, lambda x, y: np.sin(x / 9.0) * y / 50.0)  # 120 000 triangles: 4.3 MB
    meshes = [MeshSet("Малый 1", *cube(2, 5.0)), MeshSet("Большой", big_v, big_t), MeshSet("Малый 2", *cube(1, 3.0))]
    staged = [src.stage_mesh(store, m, write=False) for m in meshes]
    assert all(not s.files and not s.shas for s in staged) and all(s.keep_seq for s in staged)
    wf_files.stage_files(store, staged)
    shared = staged[0].pack
    assert shared is not None and staged[2].pack is shared and (staged[0].entry, staged[2].entry) == (0, 1)
    assert set(staged[1].files) == {"xyz", "tri", "seq"} and set(staged[1].shas) == {"xyz", "tri"}
    assert staged[0].shas == {"pack:0": shared.file[1]} and staged[2].shas == {"pack:1": shared.file[1]}
    keys = [vm.cache_key(tuple(sorted(s.shas.items())), s.render) for s in staged]
    with dialect.write_tx(project.engine) as conn:
        ids = [src.commit_mesh(conn, store, s, m) for s, m in zip(staged, meshes, strict=True)]
    for s in staged:
        s.discard()  # in place: nothing of them goes
    assert not list(store.tmp_dir.iterdir())
    roles = roles_of(project.engine, ids)
    assert set(roles[ids[1]]) == {"xyz", "tri", "seq"} and set(roles[ids[0]]) == {"pack:0"}
    with dialect.read_tx(project.engine) as conn:
        for wf_id, mesh, key in zip(ids, meshes, keys, strict=True):
            back = src.read_mesh(conn, store, wf_id)
            assert back.xyz.tobytes() == mesh.xyz.tobytes() and back.tri.tobytes() == mesh.tri.tobytes()
            assert vm.mesh_state(conn, wf_id).cache_key() == key  # the key of the levels: the staged one
        assert ws.geometry_shared(conn, {"pack:1": shared.file[1]})
        assert not ws.geometry_shared(conn, {"pack:2": shared.file[1]})  # another entry is another geometry
        n_owned = conn.execute(sa.select(sa.func.count()).select_from(blob_owners)).scalar_one()
    assert n_owned == 2 + 3  # an entry each for the small ones, three blobs of the big one
    path = store.path_of(shared.file[1])
    path.chmod(0o644)
    path.write_bytes(path.read_bytes()[:20])  # a damaged pack: a broken wireframe, never a wrong one
    packs.forget()
    with dialect.read_tx(project.engine) as conn:
        r = pclient.get(f"{API}/viz/wireframes/{ids[0]}/parts/0")
        assert r.status_code == 500 and r.json()["code"] == "wireframe_broken" and r.json()["hint"]
        assert ws.read_wireframe(conn, store, ids[1])[0].tobytes() == big_v.tobytes()  # the big one is whole
    packs.forget()


@pytest.mark.parametrize("name", ["ORE SEP 2025.tridb", "Rock Model.tridb"])
def test_real_tridb_is_packed_and_its_cycle_stays_byte_for_byte(pclient, tmp_path: Path, name: str) -> None:  # type: ignore[no-untyped-def]
    from tests.formats.tridb_builder import table_rows
    from tests.test_wireframes_export import REAL, Project, blobs_of

    proj = Project(pclient)
    ids = proj.load(REAL / name)
    roles = roles_of(proj.project.engine, ids)
    assert all(len(r) == 1 and wf_files.is_pack(next(iter(r))) for r in roles.values())  # all of them small
    out, _sent = proj.run(path=str(tmp_path / "снова.tridb"), format="tridb", wireframe_ids=ids)
    target = Path(out["path"])
    assert blobs_of(target) == blobs_of(REAL / name) and table_rows(target) == table_rows(REAL / name)
