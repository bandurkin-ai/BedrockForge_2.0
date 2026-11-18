"""The order of the file and the source rows of an imported wireframe (ADR 0013 §3): the blob `seq` gives the
triangles back in the order of their file bit for bit, a wireframe without it comes in the order of storage,
`meta.source` and `seq` survive a `.bfggis` cycle, a real TRIDB goes file → project → file with every geometry
BLOB byte for byte and every row equal. Tolerance: none."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects
from bf_core.formats.meshset import MeshSet, name_in_file, source_of
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import source as src
from bf_core.wireframes import store as ws
from tests.formats.tridb_builder import table_rows
from tests.test_archive import export
from tests.test_import_job import API, wait_job
from tests.test_viz_mesh import cube, grid, hills

pytestmark = pytest.mark.windows

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"


@pytest.fixture()
def project(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects", json={"name": "Источник"})
    assert r.status_code == 200, r.text
    return projects_of(pclient.core).require_current()


def put(project: Any, mesh: MeshSet, **kw: Any) -> int:
    store = BlobStore(project.folder)
    staged = src.stage_mesh(store, mesh)
    try:
        with dialect.write_tx(project.engine) as conn:
            return src.commit_mesh(conn, store, staged, mesh, **kw)
    finally:
        staged.discard()


def read(project: Any, wf_id: int) -> MeshSet:
    with dialect.read_tx(project.engine) as conn:
        return src.read_mesh(conn, BlobStore(project.folder), wf_id)


def roles(project: Any, wf_id: int) -> dict[str, str]:
    with dialect.read_tx(project.engine) as conn:
        rows = conn.execute(
            sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
                blob_owners.c.owner_kind == "object", blob_owners.c.owner_id == wf_id
            )
        ).all()
    return {str(r.role): str(r.sha256) for r in rows}


def tmp_files(project: Any) -> list[Any]:
    tmp = BlobStore(project.folder).tmp_dir
    return list(tmp.iterdir()) if tmp.exists() else []


def relief() -> MeshSet:
    v, t = grid(130, 110, hills)  # 28 600 shuffled triangles on UTM coordinates
    return MeshSet(
        "Рельеф",
        v,
        t,
        color="#8C9A6B",
        attrs=[{"name": "Участок", "value": "Север"}, {"name": "Год", "value": 2026}, {"name": "Пусто", "value": None}],
        source=source_of("obj", "Рельеф", "Рельеф", layer="TOPO", numbers=[1.5, 466219.1275449963, None]),
    )


# --- seq -------------------------------------------------------------------------------------------------


def test_seq_gives_back_the_order_of_the_file(project) -> None:  # type: ignore[no-untyped-def]
    mesh = relief()
    wf = put(project, mesh)
    assert tmp_files(project) == []
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        v, stored = ws.read_wireframe(conn, store, wf)
        assert set(ws.blob_shas(conn, wf)) == {"xyz", "tri"}  # the key of the levels of detail is as it was
        seq = src.read_seq(conn, store, wf)
    assert seq is not None and seq.dtype == np.dtype("<u4") and len(seq) == len(mesh.tri)
    assert np.array_equal(np.sort(seq), np.arange(len(seq)))  # a permutation of the triangles
    assert not np.array_equal(stored, mesh.tri) and np.array_equal(stored, mesh.tri[seq])  # stored in Z-order
    assert set(roles(project, wf)) == {"xyz", "tri", "seq"}
    back = read(project, wf)
    assert back.tri.tobytes() == mesh.tri.tobytes() and back.xyz.tobytes() == mesh.xyz.tobytes() == v.tobytes()
    assert (back.name, back.color, back.attrs, back.source) == (mesh.name, mesh.color, mesh.attrs, mesh.source)
    assert back.stats is not None and back.stats["closed"] is False and back.stats["area"] > 14_300.0
    with dialect.read_tx(project.engine) as conn:
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == wf)).scalar_one()
    assert meta == {"color": "#8c9a6b", "source": mesh.source}


def test_wireframe_without_seq_has_the_order_of_storage(project) -> None:  # type: ignore[no-untyped-def]
    v, t = cube(12, 10.0)
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        built = ws.put_wireframe(conn, store, v, t, name="Построенный")  # as `model.dtm` stores its result
        _v, stored = ws.read_wireframe(conn, store, built)
        assert src.read_seq(conn, store, built) is None
    mesh = read(project, built)
    assert mesh.tri.tobytes() == stored.tobytes() and mesh.source == {} and mesh.name == "Построенный"
    assert mesh.stats is not None and mesh.stats["volume"] == pytest.approx(1000.0, rel=1e-12)
    # a file that is in the spatial order already needs no blob: no blob — the order of storage
    again = put(project, MeshSet("Упорядоченный", v, stored, source=source_of("obj", "Упорядоченный", "Упорядоченный")))
    assert set(roles(project, again)) == {"xyz", "tri"}
    assert read(project, again).tri.tobytes() == stored.tobytes()
    assert src.in_order(np.arange(5, dtype="<u4")) and src.in_order(np.zeros(0, dtype="<u4"))
    assert not src.in_order(np.array([0, 2, 1], dtype="<u4")) and not src.in_order(np.array([1, 2], dtype="<u4"))


def test_broken_seq_is_an_error_not_a_wrong_file(project) -> None:  # type: ignore[no-untyped-def]
    v, t = grid(20, 20, hills)
    wf = put(project, MeshSet("Рельеф", v, t))
    store = BlobStore(project.folder)
    good = roles(project, wf)["seq"]
    for bad in (
        np.arange(len(t) - 1, dtype="<u4"),  # too short
        np.zeros(len(t), dtype="<u4"),  # not a permutation
        np.arange(1, len(t) + 1, dtype="<u4"),  # out of range
    ):
        with dialect.write_tx(project.engine) as conn:
            conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_id == wf, blob_owners.c.role == "seq"))
            store.put_bytes(conn, bad.tobytes(), media_type="x", owner_kind="object", owner_id=wf, role="seq")
        with pytest.raises(ApiError) as e:
            read(project, wf)
        assert e.value.code == "wireframe_broken" and e.value.message and e.value.hint
    with dialect.write_tx(project.engine) as conn:  # the file itself is gone
        conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_id == wf, blob_owners.c.role == "seq"))
        conn.execute(sa.insert(blob_owners).values(sha256=good, owner_kind="object", owner_id=wf, role="seq"))
    assert read(project, wf).tri.tobytes() == t.tobytes()
    store.path_of(good).unlink()
    with pytest.raises(ApiError) as e:
        read(project, wf)
    assert e.value.code == "wireframe_broken"


def test_cancelled_staging_leaves_no_files(project) -> None:  # type: ignore[no-untyped-def]
    mesh = relief()

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, _message: str) -> None:
        if fraction >= 1.0:  # the geometry is staged, `seq` is next
            raise Cancelled

    with pytest.raises(Cancelled):
        src.stage_mesh(BlobStore(project.folder), mesh, progress=cancel)
    assert tmp_files(project) == []


def test_deleting_takes_seq_along(project) -> None:  # type: ignore[no-untyped-def]
    wf = put(project, relief())
    with dialect.write_tx(project.engine) as conn:
        ws.delete_wireframe(conn, wf)
    assert roles(project, wf) == {}


# --- meta.source and the name ----------------------------------------------------------------------------


def test_the_name_of_the_file_stays_until_the_user_renames(project) -> None:  # type: ignore[no-untyped-def]
    v, t = cube(2, 10.0)
    raw = "  Рудное   тело "  # the file has the name with its spaces; the project cleans them
    first = put(project, MeshSet(raw, v, t, source=source_of("tridb", raw, "Рудное тело")))
    second = put(project, MeshSet(raw, v, t, source=source_of("tridb", raw, "Рудное тело")))
    unnamed = put(project, MeshSet("Каркас 7", v, t, source=source_of("tridb", None, "Каркас 7")))
    a, b, c = read(project, first), read(project, second), read(project, unnamed)
    assert (a.name, b.name, c.name) == ("Рудное тело", "Рудное тело (2)", "Каркас 7")
    assert "as" not in a.source and b.source["as"] == "Рудное тело (2)" and c.source["as"] == "Каркас 7"
    assert (name_in_file(a), name_in_file(b), name_in_file(c)) == (raw, raw, None)  # as the file had them
    with dialect.write_tx(project.engine) as conn:
        ws.update_wireframe(conn, second, 1, {"name": "Тело Б"})
        ws.update_wireframe(conn, unnamed, 1, {"name": "Каркас 8"})
    assert name_in_file(read(project, second)) == "Тело Б" and name_in_file(read(project, unnamed)) == "Каркас 8"
    assert name_in_file(MeshSet("Новый", v, t)) == "Новый"  # no source: the name of the mesh
    # the caller may give the name itself (an import that made the names of a whole file free at once)
    given = put(project, MeshSet(raw, v, t, source=source_of("tridb", raw, "Рудное тело")), name="Тело В")
    assert read(project, given).name == "Тело В" and name_in_file(read(project, given)) == raw


def test_bfggis_cycle_keeps_seq_and_source(pclient, project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c = pclient
    mesh = relief()
    wf = put(project, mesh)
    before = read(project, wf)
    job = export(c, tmp_path / "каркасы.bfggis")
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    new = int(job["result"]["id_maps"]["objects"][str(wf)])
    assert c.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    copy = projects_of(c.core).require_current()
    assert copy.uid != project.uid and set(roles(copy, new)) == {"xyz", "tri", "seq"}
    after = read(copy, new)
    assert after.tri.tobytes() == mesh.tri.tobytes() and after.xyz.tobytes() == mesh.xyz.tobytes()
    assert (after.name, after.color, after.attrs, after.source, after.stats) == (
        before.name, before.color, before.attrs, before.source, before.stats,
    )  # fmt: skip
    assert after.source["numbers"] == [1.5, 466219.1275449963, None]  # float64 survive the JSON of the archive


# --- a real TRIDB through the project --------------------------------------------------------------------


@pytest.mark.parametrize(("name", "count"), [("ORE SEP 2025.tridb", 66), ("Rock Model.tridb", 55)])
def test_real_tridb_through_the_project_byte_for_byte(project, tmp_path: Path, name: str, count: int) -> None:  # type: ignore[no-untyped-def]
    from bf_core.formats.tridb import read_tridb
    from bf_core.formats.tridb_write import write_tridb

    ids = [put(project, mesh) for mesh in read_tridb(REAL / name)]
    assert len(ids) == count and tmp_files(project) == []
    with_seq = sum("seq" in roles(project, wf) for wf in ids)
    assert with_seq > count // 2  # the files are not in the spatial order: the cycle goes through `seq`
    out = tmp_path / "из проекта.tridb"
    written = write_tridb(out, (read(project, wf) for wf in ids))
    assert written.meshes == count and written.warnings == []
    assert table_rows(out) == table_rows(REAL / name)  # the geometry BLOBs among them, byte for byte
