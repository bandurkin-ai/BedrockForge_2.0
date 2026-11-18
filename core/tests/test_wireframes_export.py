"""Export of wireframes (ТЗ 5.4, 7.21, 9.4, 9.12; ADR 0013 §2, §5; 8.18): the job `export.wireframes` and
`POST /wireframes/export`.

Reference values — none of them a tolerance: «импорт → экспорт → импорт» keeps every coordinate and the order of
the triangles bit for bit; the second export is the first one. For the real TRIDB of `tests/data/real` — every
geometry BLOB of the written file byte for byte and the rows of every table equal to the file read (the file
itself is another SQLite layout). A binary STL holds float32: the change is told, not hidden. A DWG goes
through a fake ODA File Converter (`tests/formats/test_dwg.py`); the real one is checked by hand on Windows."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import export_log
from bf_core.formats import mesh_io
from bf_core.formats.meshset import MeshSet
from bf_core.formats.registry import read_objects
from bf_core.jobs.context import JobCancelled, JobError
from bf_core.jobs.registry import load_handlers
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import export as we
from bf_core.wireframes import source as src
from bf_core.wireframes import store as ws
from tests.formats.tridb_builder import table_rows
from tests.meshutil import UTM, cube, grid
from tests.test_import_job import API, wait_job
from tests.test_import_objects import run_job
from tests.test_lines_export import EveryTick

pytestmark = pytest.mark.windows

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"
MAGIC = b"AC1027".ljust(128, b"\x00")  # the "DWG" of the fake converter: this and the DXF it was made from


def bits(a: Any) -> bytes:
    return np.ascontiguousarray(a, dtype=np.float64).tobytes()


class Project:
    """A project of the test core; `load` — what `import.objects` does with files, `run` — the handler of
    `export.wireframes` as a worker runs it."""

    def __init__(self, pclient: Any) -> None:
        r = pclient.post(f"{API}/projects", json={"name": "Экспорт каркасов"})
        assert r.status_code == 200, r.text
        self.client = pclient
        self.project = projects_of(pclient.core).require_current()
        self.store = BlobStore(self.project.folder)
        self.job = 1000

    def load(self, *files: Path) -> list[int]:
        return list(run_job(self.project.folder, {"files": [str(f) for f in files]}).result["wireframe_ids"])

    def put(self, mesh: MeshSet) -> int:
        staged = src.stage_mesh(self.store, mesh)
        try:
            with dialect.write_tx(self.project.engine) as conn:
                return src.commit_mesh(conn, self.store, staged, mesh)
        finally:
            staged.discard()

    def mesh(self, wf_id: int) -> MeshSet:
        with dialect.read_tx(self.project.engine) as conn:
            return src.read_mesh(conn, self.store, wf_id)

    def run(self, cancel_at: float | None = None, **params: Any) -> tuple[dict[str, Any], list[tuple[Any, ...]]]:
        sent: list[tuple[Any, ...]] = []
        stop = threading.Event()

        def send(message: tuple[Any, ...]) -> None:
            sent.append(message)
            if cancel_at is not None and message[0] == "progress" and (message[2] or 0.0) >= cancel_at:
                stop.set()

        self.job += 1
        ctx = EveryTick({"id": self.job, "params": params, "project_folder": str(self.project.folder)}, send, stop)
        try:
            out = load_handlers()["export.wireframes"].run(ctx)
        finally:
            ctx.close()
        assert out is not None
        return out, sent

    def request(self, **kw: Any) -> dict[str, Any]:
        with dialect.read_tx(self.project.engine) as conn:
            return we.request(conn, **kw)

    def log_rows(self) -> list[dict[str, Any]]:
        with dialect.read_tx(self.project.engine) as conn:
            return [dict(r) for r in conn.execute(sa.select(export_log).order_by(export_log.c.id)).mappings()]


@pytest.fixture()
def proj(pclient):  # type: ignore[no-untyped-def]
    return Project(pclient)


@pytest.fixture()
def out_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "выгрузка"
    folder.mkdir()
    return folder


def models() -> list[MeshSet]:
    """A closed cube and an open surface on UTM coordinates with awkward doubles, the triangles shuffled (the
    store keeps another order: `seq` has to bring the order of the file back)."""
    v, t = cube(6, 10.0)
    gv, gt = grid(40, 30, lambda x, y: 3.0 * np.sin(x / 7.0) + 0.1 * y, step=1.0 / 3.0, offset=UTM + [50.0, 0.0, 0.0])
    return [
        MeshSet("Руда 1", v, t, color="#cc3311", attrs=[{"name": "Сорт", "value": "богатая"}]),
        MeshSet("Рельеф", gv, gt, color="#88aa44"),
    ]


def messages(sent: list[tuple[Any, ...]], level: str | None = None) -> list[str]:
    return [str(m[3]) for m in sent if m[0] == "log" and (level is None or m[2] == level)]


def blobs_of(path: Path) -> dict[int, bytes]:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
        return {int(i): bytes(b) for i, b in conn.execute("SELECT ID, Geometry FROM Geometry ORDER BY ID")}


# --- TRIDB: byte for byte --------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "count"), [("ORE SEP 2025.tridb", 66), ("Rock Model.tridb", 55)])
def test_real_tridb_cycle_byte_for_byte(proj: Project, out_dir: Path, name: str, count: int) -> None:
    """file → `import.objects` → `export.wireframes` → file: every geometry BLOB byte for byte, every row
    equal; the file read again gives the same wireframes; exported again — the same file."""
    source = REAL / name
    ids = proj.load(source)
    assert len(ids) == count
    out, sent = proj.run(path=str(out_dir / "снова.tridb"), format="tridb", wireframe_ids=ids)
    target = Path(out["path"])
    assert target == out_dir / "снова.tridb" and out["files"] == ["снова.tridb"] and out["wireframes"] == count
    assert messages(sent, "warn") == [] and out["warnings"] == []
    assert blobs_of(target) == blobs_of(source)  # coordinates and the order of the triangles: byte for byte
    assert table_rows(target) == table_rows(source)  # ids, GUIDs, names, colours, measures, attributes
    with sqlite3.connect(target) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert not list(out_dir.glob("*.part"))

    again = proj.load(target)  # the written file as a source: the same geometry in the project, bit for bit
    for a, b in zip(ids, again, strict=True):
        one, two = proj.mesh(a), proj.mesh(b)
        assert bits(one.xyz) == bits(two.xyz) and one.tri.tobytes() == two.tri.tobytes()
        assert (two.color, two.attrs, two.source["tridb"]) == (one.color, one.attrs, one.source["tridb"])
    second, _ = proj.run(path=str(out_dir / "ещё раз.tridb"), format="tridb", wireframe_ids=again)
    assert blobs_of(Path(second["path"])) == blobs_of(source)
    assert table_rows(Path(second["path"])) == table_rows(source)
    rows = proj.log_rows()
    assert [(r["file_name"], r["format"], r["object_id"]) for r in rows] == [
        ("снова.tridb", "tridb", ids[0]), ("ещё раз.tridb", "tridb", again[0]),
    ]  # fmt: skip
    assert rows[0]["counts"]["wireframes"] == count and rows[0]["settings"]["wireframe_ids"] == ids


# --- every format: import → export → import bit for bit ---------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "options", "numbering"),
    [("tridb", {}, True), ("obj", {}, True), ("dtm", {}, True), ("dxf", {}, False), ("stl", {"binary": False}, False)],
)
def test_cycle_bit_for_bit(proj: Project, out_dir: Path, fmt: str, options: dict[str, Any], numbering: bool) -> None:
    """Wireframes of the project → file → project → file. `numbering` — the format has a vertex list, so the
    numbers of the vertices come back too; a DXF and an STL number them as the corners come."""
    from bf_core.formats.tridb_write import write_tridb

    first = out_dir / "модель.tridb"  # a format with names, colours and attributes
    write_tridb(first, models(), now=1_795_000_000, author="test")
    ids = proj.load(first)
    want = [proj.mesh(i) for i in ids]
    assert [m.name for m in want] == ["Руда 1", "Рельеф"]
    for a, b in zip(want, models(), strict=True):
        assert bits(a.xyz) == bits(b.xyz) and a.tri.tolist() == b.tri.tolist() and a.attrs == b.attrs

    out, sent = proj.run(path=str(out_dir / "цикл"), format=fmt, wireframe_ids=ids, options=options)
    target = Path(out["path"])
    assert target.name == "цикл" + we.FORMATS[fmt].suffix and target.is_file()  # the extension of the format
    assert (out["wireframes"], out["triangles"]) == (2, sum(m.triangle_count for m in want))
    assert out["files"] == ([target.name, "цикл.str"] if fmt == "dtm" else [target.name])
    seen = [m[2] for m in sent if m[0] == "progress" and m[2] is not None]
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0
    back = proj.load(target)
    got = [proj.mesh(i) for i in back]
    assert len(got) == 2
    for a, b in zip(want, got, strict=True):
        assert bits(a.xyz[a.tri]) == bits(b.xyz[b.tri])  # every corner of every triangle, in the order of the file
        if numbering:
            assert bits(a.xyz) == bits(b.xyz) and a.tri.tobytes() == b.tri.tobytes()
        if fmt != "dtm":  # a Surpac pair has no names
            assert a.name + " (2)" == b.name  # the name is taken in the project: the file had the name itself
            assert b.source["name"] == a.name
        if fmt in ("tridb", "obj", "dxf"):
            assert b.color == a.color
    if fmt == "tridb":
        assert got[0].attrs == want[0].attrs and messages(sent, "warn") == []
        assert blobs_of(target) == blobs_of(first) and table_rows(target) == table_rows(first)
    else:  # the format has no place for attributes: said so
        assert any("трибут" in w for w in out["warnings"]) and messages(sent, "warn") == out["warnings"]
    # the second export — of what the first one gave back — is the first one (the names of the file itself:
    # the wireframes are still called as the import called them)
    (out_dir / "повтор").mkdir()
    second, _ = proj.run(path=str(out_dir / "повтор" / "цикл"), format=fmt, wireframe_ids=back, options=options)
    again = Path(second["path"])
    if fmt == "tridb":
        assert blobs_of(again) == blobs_of(target) and table_rows(again) == table_rows(target)
    elif fmt == "dxf":  # the frame of a drawing carries GUIDs and the time: its wireframes are compared
        one, two = read_objects(target).meshes, read_objects(again).meshes
        assert isinstance(one, list) and isinstance(two, list) and len(one) == len(two) == 2
        for a, b in zip(one, two, strict=True):
            assert (a.name, a.color) == (b.name, b.color)
            assert bits(a.xyz) == bits(b.xyz) and a.tri.tobytes() == b.tri.tobytes()
    else:
        assert [(again.parent / n).read_bytes() for n in second["files"]] == [
            (target.parent / n).read_bytes() for n in out["files"]
        ]


def test_binary_stl_tells_what_float32_costs(proj: Project, out_dir: Path) -> None:
    ids = [proj.put(m) for m in models()]
    out, sent = proj.run(path=str(out_dir / "тело.stl"), format="stl", wireframe_ids=ids[:1])
    assert Path(out["path"]).stat().st_size == 84 + 50 * 432  # binary by default: a cube of 6 × 6 cells a face
    assert any("float32" in w for w in out["warnings"])
    (mesh,) = list(read_objects(Path(out["path"])).meshes or [])
    want = proj.mesh(ids[0])
    assert mesh.triangle_count == 432 and mesh.source["precision"] == "float32"
    # northings of 6.2e6 m: a float32 step is 0.5 m — the coordinates moved by no more than half of it
    assert 0.0 < np.abs(mesh.xyz[mesh.tri] - want.xyz[want.tri]).max() <= 0.25


def test_built_and_renamed_wireframes(proj: Project, out_dir: Path) -> None:
    """A wireframe made in the project has no file order and no source; a renamed one is written under its new
    name; the order of the wireframes in the file is the order asked for."""
    from bf_core.formats.obj import read_obj

    ids = [proj.put(m) for m in models()]
    with dialect.write_tx(proj.project.engine) as conn:
        ws.update_wireframe(conn, ids[0], 1, {"name": "Рудное тело"})
    out, _ = proj.run(path=str(out_dir / "o.obj"), format="obj", wireframe_ids=[ids[1], ids[0]])
    got = list(read_obj(Path(out["path"])))
    assert [m.name for m in got] == ["Рельеф", "Рудное тело"]
    for mesh, wf_id in zip(got, (ids[1], ids[0]), strict=True):
        ref = proj.mesh(wf_id)
        assert bits(mesh.xyz) == bits(ref.xyz) and mesh.tri.tobytes() == ref.tri.tobytes()


# --- DWG through ODA File Converter (a fake one) ----------------------------------------------------------


def test_dwg_through_the_converter(
    proj: Project, out_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ezdxf.addons import odafc

    from bf_core.formats import dwg
    from tests.formats.test_dwg import Oda, _no_display

    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.delenv(dwg.ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing here"))
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(name, str(tmp_path / "Program Files"))
    monkeypatch.setattr(odafc, "_linux_dummy_display", _no_display, raising=False)
    ids = [proj.put(m) for m in models()]
    path = str(out_dir / "Карьер.dwg")
    with pytest.raises(ApiError) as err:  # no converter: refused at once, with the text of Д-13
        proj.request(path=path, format="dwg", wireframe_ids=ids)
    assert (err.value.status, err.value.code) == (422, "oda_not_found") and "ODA File Converter" in err.value.hint
    with pytest.raises(JobError) as failed:  # … and by the job, if the converter went away meanwhile
        proj.run(path=path, format="dwg", wireframe_ids=ids, options={"version": "R2013"})
    assert failed.value.code == "oda_not_found" and list(out_dir.iterdir()) == []

    oda = Oda(tmp_path / "oda")
    with pytest.raises(ApiError) as err:
        proj.request(path=path, format="dwg", wireframe_ids=ids, options={"version": "R14"}, oda=str(oda.exe))
    assert (err.value.status, err.value.code) == (422, "bad_version") and "R2013" in err.value.hint
    params = proj.request(path=path, format="dwg", wireframe_ids=ids, options={"version": "r2018"}, oda=str(oda.exe))
    assert params["options"] == {"version": "R2018"} and params["oda"] == str(oda.exe)
    out, sent = proj.run(**params)
    target = Path(out["path"])
    assert target.name == "Карьер.dwg" and target.read_bytes().startswith(MAGIC) and out["files"] == ["Карьер.dwg"]
    assert oda.calls[-1]["argv"][2] == "ACAD2018" and oda.calls[-1]["input"] == ["drawing.dxf"]  # no Cyrillic there
    assert any("ODA File Converter" in str(m[3]) for m in sent if m[0] == "progress" and m[3])
    back = read_objects(target, settings={"converter": str(oda.exe)})
    for mesh, wf_id in zip(back.meshes or [], ids, strict=True):
        ref = proj.mesh(wf_id)
        assert bits(mesh.xyz[mesh.tri]) == bits(ref.xyz[ref.tri]) and mesh.name == ref.name
    oda.mode("nothing")  # the converter made no file: an error, and the DWG written before stays as it was
    before = target.read_bytes()
    with pytest.raises(JobError) as failed:
        proj.run(**{**params, "overwrite": True})
    assert failed.value.code == "oda_failed" and target.read_bytes() == before
    assert sorted(p.name for p in out_dir.iterdir()) == ["Карьер.dwg"] and len(proj.log_rows()) == 1


# --- files of other people, parameters, cancel -----------------------------------------------------------


def test_files_are_not_replaced_without_asking(proj: Project, out_dir: Path) -> None:
    """The save window asked about the chosen name only: another name the export writes is a 409 with its
    name, by the endpoint and once more by the job."""
    ids = [proj.put(m) for m in models()]
    chosen = out_dir / "тела"  # no extension: the file written is «тела.tridb»
    (out_dir / "тела.tridb").write_bytes(b"old")
    with pytest.raises(ApiError) as err:
        proj.request(path=str(chosen), format="tridb", wireframe_ids=ids)
    assert (err.value.status, err.value.code, err.value.extra) == (409, "files_exist", {"files": ["тела.tridb"]})
    assert "тела.tridb" in err.value.message and err.value.hint
    # the file chosen itself was asked about by the window: no 409
    assert proj.request(path=str(out_dir / "тела.tridb"), format="tridb", wireframe_ids=ids)["overwrite"] is False
    # a Surpac DTM comes with its string file: strings of somebody under that name are not replaced
    (out_dir / "pit.str").write_text("чужие стринги", encoding="utf-8")
    with pytest.raises(ApiError) as err:
        proj.request(path=str(out_dir / "pit.dtm"), format="dtm", wireframe_ids=ids)
    assert err.value.code == "files_exist" and err.value.extra == {"files": ["pit.str"]}
    params = proj.request(path=str(out_dir / "pit.dtm"), format="dtm", wireframe_ids=ids, overwrite=True)
    assert params["overwrite"] is True

    # the job checks again: the file appeared after the endpoint had looked
    late = proj.request(path=str(out_dir / "новый"), format="obj", wireframe_ids=ids)
    (out_dir / "новый.obj").write_bytes(b"appeared")
    with pytest.raises(JobError) as failed:
        proj.run(**late)
    assert failed.value.code == "files_exist" and "новый.obj" in failed.value.message
    assert (out_dir / "новый.obj").read_bytes() == b"appeared" and proj.log_rows() == []
    out, _ = proj.run(**params)  # agreed: both files of the pair are written
    assert out["files"] == ["pit.dtm", "pit.str"] and "чужие" not in (out_dir / "pit.str").read_text(errors="replace")
    assert sorted(p.name for p in out_dir.iterdir()) == ["pit.dtm", "pit.str", "новый.obj", "тела.tridb"]


def test_request_checks_the_parameters(proj: Project, out_dir: Path) -> None:
    ids = [proj.put(m) for m in models()]
    path = str(out_dir / "out.tridb")
    ok = proj.request(path=path, format="tridb", wireframe_ids=[ids[1], ids[0], ids[1]])
    assert ok == {
        "path": path, "format": "tridb", "wireframe_ids": [ids[1], ids[0]], "options": {}, "overwrite": False,
        "oda": None, "triangles": 432 + 2400, "largest": 24 * 1271 + 12 * 2400,
    }  # fmt: skip
    assert proj.request(path=path, format="stl", wireframe_ids=ids)["options"] == {"binary": True}
    assert we._memory(ok) == 512 and we._memory({"largest": 600 << 20}) > 2000
    from bf_core.lines import store as ls_store

    with dialect.write_tx(proj.project.engine) as conn:  # an object that is not a wireframe
        line_file = ls_store.create_file(conn, proj.store, name="линии", meta={})
    for kw, status, code in (
        ({"path": path, "format": "shp", "wireframe_ids": ids}, 422, "bad_format"),
        ({"path": "out.tridb", "format": "tridb", "wireframe_ids": ids}, 422, "path_not_absolute"),
        ({"path": str(out_dir / "нет" / "o.obj"), "format": "obj", "wireframe_ids": ids}, 404, "folder_not_found"),
        ({"path": path, "format": "tridb", "wireframe_ids": []}, 404, "wireframe_not_found"),
        ({"path": path, "format": "tridb", "wireframe_ids": [ids[0], 999_999]}, 404, "wireframe_not_found"),
        ({"path": path, "format": "tridb", "wireframe_ids": [line_file]}, 404, "wireframe_not_found"),
        ({"path": path, "format": "tridb", "wireframe_ids": list(range(1, 5002))}, 422, "too_many_wireframes"),
    ):
        with pytest.raises(ApiError) as err:
            proj.request(**kw)
        assert (err.value.status, err.value.code) == (status, code) and err.value.hint, kw


def test_cancel_and_failures_leave_no_file(proj: Project, out_dir: Path) -> None:
    v, t = grid(500, 300, lambda x, y: np.hypot(x, y) / 9.0)  # 300 000 triangles: every writer goes in portions
    big = proj.put(MeshSet("Большой", v, t))
    for fmt in ("tridb", "dxf", "obj", "stl", "dtm"):
        with pytest.raises(JobCancelled):
            proj.run(cancel_at=0.3, path=str(out_dir / "big"), format=fmt, wireframe_ids=[big])
        assert list(out_dir.iterdir()) == [], fmt
    assert proj.log_rows() == []
    # a cancel that comes when the file is already in place does not undo the export
    late, _ = proj.run(cancel_at=1.0, path=str(out_dir / "поздно"), format="obj", wireframe_ids=[big])
    assert Path(late["path"]).is_file() and len(proj.log_rows()) == 1
    Path(late["path"]).unlink()
    # a wireframe deleted while the job waited; a folder under the name of the file: Russian errors, no file
    gone = proj.put(models()[0])
    params = proj.request(path=str(out_dir / "нет.obj"), format="obj", wireframe_ids=[big, gone])
    with dialect.write_tx(proj.project.engine) as conn:
        ws.delete_wireframe(conn, gone)
    with pytest.raises(JobError) as failed:
        proj.run(**params)
    assert failed.value.code == "wireframe_not_found" and failed.value.hint and list(out_dir.iterdir()) == []
    (out_dir / "папка.dxf").mkdir()
    with pytest.raises(JobError) as failed:
        proj.run(path=str(out_dir / "папка.dxf"), format="dxf", wireframe_ids=[big])
    assert failed.value.code == "file_unwritable" and failed.value.hint
    assert [p.name for p in out_dir.iterdir()] == ["папка.dxf"] and len(proj.log_rows()) == 1


def test_retry_replaces_what_the_failed_job_wrote(
    proj: Project, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Повторить» (`retry_of` of the scheduler): the files changed after the first job started are its own."""
    ids = [proj.put(m) for m in models()]
    params = proj.request(path=str(out_dir / "pit"), format="dtm", wireframe_ids=ids)  # «pit.dtm», «pit.str»
    since = dialect.utcnow().isoformat()
    real = os.replace

    def replace(a: Any, b: Any) -> None:
        if str(b).endswith(".str"):
            raise OSError(28, "No space left on device")
        real(a, b)

    monkeypatch.setattr(mesh_io.os, "replace", replace)
    with pytest.raises(JobError) as failed:
        proj.run(**params)
    assert failed.value.code == "file_unwritable" and sorted(p.name for p in out_dir.iterdir()) == ["pit.dtm"]
    monkeypatch.setattr(mesh_io.os, "replace", real)
    with pytest.raises(JobError) as failed:  # not a retry: the .dtm is not asked about
        proj.run(**params)
    assert failed.value.code == "files_exist" and "pit.dtm" in failed.value.message
    out, _ = proj.run(**params, retry_of={"id": 1, "since": since})
    assert out["files"] == ["pit.dtm", "pit.str"] and len(proj.log_rows()) == 1
    # a file older than the first job is somebody else's: a retry does not replace it
    (out_dir / "чужой.str").write_text("чужие стринги", encoding="utf-8")
    time.sleep(3 * we.CLOCK_S)
    late = proj.request(path=str(out_dir / "чужой.dtm"), format="dtm", wireframe_ids=ids, overwrite=True)
    with pytest.raises(JobError) as failed:  # its first job started after the file was there
        proj.run(**{**late, "overwrite": False}, retry_of={"id": 2, "since": dialect.utcnow().isoformat()})
    assert failed.value.code == "files_exist" and "чужой.str" in failed.value.message
    assert (out_dir / "чужой.str").read_text(encoding="utf-8") == "чужие стринги"


# --- through the API and the scheduler -------------------------------------------------------------------


def test_export_through_the_api(proj: Project, out_dir: Path) -> None:
    """`POST /wireframes/export`: 409 with the names, then the job in a worker process, its journal and log."""
    c = proj.client
    ids = proj.load(REAL / "ORE SEP 2025.tridb")
    body = {"path": str(out_dir / "руда"), "format": "tridb", "wireframe_ids": ids}
    (out_dir / "руда.tridb").write_bytes(b"old")
    r = c.post(f"{API}/wireframes/export", json=body)
    assert r.status_code == 409 and r.json()["code"] == "files_exist" and r.json()["files"] == ["руда.tridb"]
    assert c.post(f"{API}/wireframes/export", json={**body, "format": "ifc"}).status_code == 422
    assert c.post(f"{API}/wireframes/export", json={**body, "wireframe_ids": []}).status_code == 422
    r = c.post(f"{API}/wireframes/export", json={**body, "overwrite": True})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["title"] == "Экспорт каркасов (Micromine TRIDB)", job
    target = Path(job["result"]["path"])
    assert target == out_dir / "руда.tridb" and job["result"]["wireframes"] == 66
    assert blobs_of(target) == blobs_of(REAL / "ORE SEP 2025.tridb")
    log = c.get(f"{API}/jobs/{r.json()['source']}/{r.json()['id']}/log").json()
    assert any("Записан файл «руда.tridb» (Micromine TRIDB): каркасов 66" in row["text"] for row in log["lines"]), log
    rows = proj.log_rows()
    assert len(rows) == 1 and rows[0]["job_id"] == r.json()["id"] and rows[0]["object_id"] == ids[0]
