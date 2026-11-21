"""A real OMF v1 from Leapfrog (ТЗ 7.24, 10.5 п. 3; ADR 0017 §1–2, §4): `tests/data/real/topo_cut.omf` — one
`SurfaceElement` «Милькан_cut», 83 675 vertices, 164 321 triangles, the name and the description of the project
empty, no CRS, every origin zero. The composition for the wizard, «Импорт файлов» in a worker process (`spawn`),
the vertices bit for bit against the block of the file decoded here (not by the reader), OMF → import bit for bit.

Decided by ADR 0017 §2 («в папке дерева по имени файла, как TRIDB») and ADR 0013 («папка дерева = имя файла, если
каркасов больше одного»): the single surface gets no folder of its own — it goes to the folder of the wizard. The
empty name of the project shows as the name of the file («topo_cut»); without a CRS the objects load without one and
the journal says so (a warning, not an error). The sample is only read: its SHA-256 is the same after every test."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, import_log, objects
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API, wait_job
from tests.test_import_objects import run_job

SAMPLE = Path(__file__).resolve().parents[2] / "tests" / "data" / "real" / "topo_cut.omf"
NAME = "Милькан_cut"
VERTICES, TRIANGLES = 83_675, 164_321
CRS_NOTE = "Система координат в файле OMF не указана"

pytestmark = pytest.mark.skipif(not SAMPLE.is_file(), reason="the Leapfrog sample is not in tests/data/real")


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def sample() -> SimpleNamespace:
    """The arrays of the file decoded from its JSON and zlib blocks, apart from `formats/omf_*`: the vertices of the
    geometry plus its origin plus the origin of the project (float64), the triangles; the SHA-256 of the file."""
    raw = SAMPLE.read_bytes()
    assert raw[:4] == b"\x84\x83\x82\x81" and raw[4:14] == b"OMF-v0.9.0"
    (start,) = struct.unpack("<Q", raw[52:60])
    doc: dict[str, dict[str, Any]] = json.loads(raw[start:].decode("utf-8"))
    (project,) = [o for o in doc.values() if o["__class__"] == "Project"]
    (element,) = [doc[k] for k in project["elements"]]
    geometry = doc[element["geometry"]]

    def block(uid: str, width: int) -> np.ndarray:
        desc = doc[uid]["array"]
        data = zlib.decompress(raw[desc["start"] : desc["start"] + desc["length"]])
        return np.frombuffer(data, dtype=np.dtype(desc["dtype"])).reshape(-1, width)

    origin = np.asarray(project["origin"], dtype="<f8") + np.asarray(geometry["origin"], dtype="<f8")
    xyz = np.ascontiguousarray(block(geometry["vertices"], 3).astype("<f8") + origin, dtype="<f8")
    tri = block(geometry["triangles"], 3).astype(np.int64)
    return SimpleNamespace(sha=hashlib.sha256(raw).hexdigest(), project=project, element=element, xyz=xyz, tri=tri)


def triangles_of(tri: np.ndarray) -> list[tuple[int, ...]]:
    return sorted(map(tuple, np.asarray(tri, dtype=np.int64).tolist()))


def test_the_sample_is_what_the_test_knows(sample) -> None:  # type: ignore[no-untyped-def]
    assert sample.project["name"] == "" and sample.project["description"] == "" and sample.project["units"] == ""
    assert sample.element["name"] == NAME and sample.element["data"] == [] and sample.element["textures"] == []
    assert sample.xyz.shape == (VERTICES, 3) and sample.tri.shape == (TRIANGLES, 3)
    assert sample.tri.min() == 0 and sample.tri.max() == VERTICES - 1
    assert np.isfinite(sample.xyz).all()


def test_composition_for_the_wizard(sample) -> None:  # type: ignore[no-untyped-def]
    """JSON only: one surface that loads as a wireframe, nothing left out, no CRS, the project named by the file."""
    from bf_core.formats.omf_compose import compose
    from bf_core.formats.omf_file import open_omf

    with open_omf(SAMPLE) as f:
        seen = compose(f).as_dict()
    assert seen["format"] == "OMF v1" and seen["version"] == "OMF-v0.9.0" and seen["file"] == "topo_cut.omf"
    assert seen["project"] == "topo_cut" and seen["description"] == "" and seen["crs"] is None
    (e,) = seen["elements"]
    assert (e["class"], e["kind"], e["name"], e["as"]) == ("SurfaceElement", "surface", NAME, "каркас")
    assert e["skip"] is None and e["error"] is None and e["dtm"] is False
    assert e["color"] == "#ffd863" and e["data"] == [] and e["textures"] == 0
    assert seen["left_out"] == []  # «не загрузится» — пусто
    assert [w for w in seen["warnings"] if CRS_NOTE in w] and len(seen["warnings"]) == 1
    assert sha_of(SAMPLE) == sample.sha


def test_import_in_a_worker_process(pclient, sample) -> None:  # type: ignore[no-untyped-def]
    """The wizard and `import.objects` through the API, the job in a worker process (`spawn`)."""
    from bf_core.projects.service import projects_of
    from bf_core.wireframes import store as ws

    assert pclient.post(f"{API}/projects", json={"name": "Leapfrog"}).status_code == 200
    r = pclient.post(f"{API}/import/objects/inspect", json={"files": [str(SAMPLE)]})
    assert r.status_code == 200, r.text
    (seen,) = r.json()["files"]
    assert seen["kind"] == "omf" and "error" not in seen, seen
    assert seen["totals"]["wireframes"] == 1 and not seen["totals"].get("layers") and not seen["totals"].get("cells")
    assert [x["name"] for x in seen["omf"]["elements"]] == [NAME] and seen["left_out"] == []

    r = pclient.post(f"{API}/import/objects", json={"files": [str(SAMPLE)], "folder": "Рельеф"})
    assert r.status_code == 200, r.text
    key, job_id = r.json()["source"], r.json()["id"]
    job = wait_job(pclient, key, job_id, timeout=120)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["wireframes"] == 1 and res["failed"] == [] and not res.get("layers") and not res.get("block_models")
    log = pclient.get(f"{API}/jobs/{key}/{job_id}/log").json()["lines"]
    assert not [x for x in log if x["level"] == "error"], log
    assert [x["level"] for x in log if CRS_NOTE in x["text"]] == ["warn"], log  # «без СК» — запись в журнале
    tree = pclient.get(f"{API}/wireframes").json()["wireframes"]
    assert [(w["name"], w["folder"]) for w in tree] == [(NAME, "Рельеф")]  # one wireframe: no folder of the file

    project = projects_of(pclient.core).require_current()
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        (w,) = ws.list_wireframes(conn)
        xyz, tri = ws.read_wireframe(conn, store, w["id"])
        rows = conn.execute(sa.select(import_log)).mappings().all()
        owners = conn.execute(sa.select(blob_owners).where(blob_owners.c.sha256 == sample.sha)).mappings().all()
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == w["id"])).scalar_one()
    assert xyz.dtype == np.dtype("<f8") and xyz.tobytes() == sample.xyz.tobytes()  # array + origin, bit for bit
    assert tri.tolist() == sample.tri.tolist() or triangles_of(tri) == triangles_of(sample.tri)
    assert w["triangle_count"] == TRIANGLES and w["crs"] is None and w["dtm"] is None
    assert w["color"] == "#ffd863" and w["source"]["format"] == "omf"
    assert [(r["format"], r["file_name"], r["sha256"]) for r in rows] == [("omf", "topo_cut.omf", sample.sha)]
    assert len(owners) == 1  # the journal row of the wireframe owns the source file
    assert not [n for n in meta.get("warnings") or [] if "не перенос" in n or "текстур" in n], meta
    assert sha_of(SAMPLE) == sample.sha  # the sample is not changed


def test_export_to_omf_and_back_bit_for_bit(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    """10.5 п. 3: the Leapfrog file → a project → OMF of BF2 → another project — the same wireframe bit for bit."""
    from bf_core.exporting import omf_job
    from bf_core.formats.omf_read import open_reader
    from bf_core.projects.project import create_folder
    from bf_core.wireframes import store as ws

    def project(name: str) -> SimpleNamespace:
        folder = create_folder(tmp_path / name, name, {})
        return SimpleNamespace(
            folder=folder, engine=dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
        )

    def wireframes(p: SimpleNamespace) -> dict[str, tuple[bytes, list[tuple[int, ...]], Any, Any, Any]]:
        store = BlobStore(p.folder)
        with dialect.read_tx(p.engine) as conn:
            out = {}
            for w in ws.list_wireframes(conn):
                xyz, tri = ws.read_wireframe(conn, store, w["id"])
                out[w["name"]] = (xyz.tobytes(), triangles_of(tri), w["color"], w["dtm"], w["crs"])
            return out

    src, dst = project("Исходный"), project("Обратно")
    try:
        first = run_job(src.folder, {"files": [str(SAMPLE)]}).result
        assert first["wireframes"] == 1 and first["failed"] == []
        out = tmp_path / "topo_cut_bf2.omf"
        counts, warnings = omf_job.write_objects(
            out, wireframe_ids=first["wireframe_ids"], engine=src.engine, store=BlobStore(src.folder)
        )
        assert counts["size"] == out.stat().st_size and counts["wireframes"] == 1 and not warnings
        with open_reader(out) as r:
            (mesh,) = list(r.surfaces())
        assert mesh.name == NAME and mesh.xyz.tobytes() == sample.xyz.tobytes()
        back = run_job(dst.folder, {"files": [str(out)]})
        assert back.result["wireframes"] == 1 and back.result["failed"] == []
        before, after = wireframes(src), wireframes(dst)
        assert after == before and list(after) == [NAME]
        assert after[NAME][0] == sample.xyz.tobytes() and after[NAME][1] == triangles_of(sample.tri)
        assert after[NAME][2] == "#ffd863" and after[NAME][3] is None and after[NAME][4] is None
    finally:
        src.engine.dispose()
        dst.engine.dispose()
    assert sha_of(SAMPLE) == sample.sha
