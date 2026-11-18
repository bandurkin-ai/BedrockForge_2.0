"""Wireframes in «Импорт файлов» (ТЗ 7.8, 7.21; ADR 0013 §3, §5; 8.10, 8.15): the composition with wireframes
and the job `import.objects` — every wireframe of a file an object of its own in a folder named after the file,
`meta.source`, the order of the file (`seq`), the checks at loading, the journal, a cancel that leaves nothing.

Reference values. The real files of `tests/data/real`: 66 wireframes / 1 925 vertices / 3 590 triangles and
55 / 64 991 / 128 994 (TRIDB), one polyface of 7 444 / 7 417 (`TST-FAULTS.DXF`); the geometry of every wireframe
against the reader of its format bit for bit (no tolerance); `Volume` and `SurfaceArea` of the TRIDB against the
measures of the stored geometry — 1e-9 relative (ТЗ 9.12). A cube of side 10: 1000 m³, 600 m², closed."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, import_log, objects, wireframes
from bf_core.formats.meshset import MeshSet
from bf_core.jobs.context import JobCancelled, JobError
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import source as src
from bf_core.wireframes import store as ws
from tests.formats.test_datamine_objects import write_pair
from tests.formats.test_registry_meshes import drawing, solids
from tests.formats.tridb_builder import build as build_tridb
from tests.meshutil import cube
from tests.test_import_job import API, wait_job
from tests.test_import_objects import run_job

pytestmark = pytest.mark.windows

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"
REAL_TRIDB = [("ORE SEP 2025.tridb", 66, 1925, 3590), ("Rock Model.tridb", 55, 64_991, 128_994)]


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "projects", "Каркасы", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    yield SimpleNamespace(folder=folder, engine=engine)
    engine.dispose()


@pytest.fixture()
def files(tmp_path: Path) -> dict[str, Path]:
    """A selection of the wizard: a TRIDB of two wireframes, a drawing with a line and two meshes, a Surpac pair,
    an OBJ, and the two tables of a Datamine wireframe under names that do not tell they are a pair."""
    from bf_core.formats.dtm_surpac import write_dtm
    from bf_core.formats.obj import write_obj
    from bf_core.formats.tridb_write import write_tridb

    folder = tmp_path / "исходные"
    folder.mkdir()
    out = {name: folder / name for name in ("Тела.tridb", "mixed.dxf", "pit.dtm", "pit.str", "one.obj")}
    write_tridb(out["Тела.tridb"], solids(), now=1_795_000_000, author="test")
    drawing(out["mixed.dxf"])
    write_dtm(out["pit.dtm"], solids()[1:])
    write_obj(out["one.obj"], solids()[:1])
    v, t = cube(2, 10.0)
    out["ore.dm"], out["nodes.dm"] = write_pair(folder, v + 300.0, t, names=("ore.dm", "nodes.dm"))
    return out


def paths(files: dict[str, Path], *names: str) -> list[str]:
    return [str(files[n]) for n in (names or files)]


def bits(a: Any) -> bytes:
    return np.ascontiguousarray(a, dtype=np.float64).tobytes()


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_of(engine: sa.Engine, folder: Path) -> dict[str, Any]:
    """Everything an import may leave behind."""
    with dialect.read_tx(engine) as conn:
        count = lambda t: conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one()  # noqa: E731
        tmp = folder / "blobs" / ".tmp"
        return {
            "objects": [tuple(r) for r in conn.execute(sa.select(objects.c.kind, objects.c.name, objects.c.state))],
            "wireframes": count(wireframes),
            "owners": count(blob_owners),
            "log": count(import_log),
            "tmp": sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else [],
        }


def stored(project: Any) -> list[dict[str, Any]]:
    """The wireframes of the project in the order they were written, with `meta` and their blob roles."""
    with dialect.read_tx(project.engine) as conn:
        rows = conn.execute(sa.select(objects).where(objects.c.kind == "wireframe").order_by(objects.c.id)).mappings()
        out = [dict(r) for r in rows]
        for row in out:
            own = sa.and_(blob_owners.c.owner_kind == "object", blob_owners.c.owner_id == row["id"])
            row["roles"] = set(conn.execute(sa.select(blob_owners.c.role).where(own)).scalars())
            row["info"] = ws.wireframe_info(conn, row["id"]) if row["state"] == "ready" else None
    return out


def mesh_of(project: Any, wf_id: int) -> MeshSet:
    with dialect.read_tx(project.engine) as conn:
        return src.read_mesh(conn, BlobStore(project.folder), wf_id)


# --- the composition (inspect) -------------------------------------------------------------------------


def test_composition_with_wireframes(files: dict[str, Path], project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.importing.object_files import inspect_files, plan

    was = state_of(project.engine, project.folder)
    chosen = [*paths(files), str(REAL / "ORE SEP 2025.tridb"), str(REAL / "TST-FAULTS.DXF")]
    out = inspect_files(chosen)
    by = {f["file"]: f for f in out["files"]}
    # the `.str` goes with its `.dtm`, the points table — with its triangles: neither is a file of its own
    assert list(by) == [
        "Тела.tridb", "mixed.dxf", "pit.dtm", "one.obj", "ore.dm", "ORE SEP 2025.tridb", "TST-FAULTS.DXF",
    ]  # fmt: skip
    assert out["rest"] == [] and all(f["ok"] for f in out["files"])

    tridb = by["Тела.tridb"]
    assert (tridb["format"], tridb["kind"], tridb["label"]) == ("tridb", "wireframes", "Каркасы Micromine (.tridb)")
    assert [(w["name"], w["points"], w["triangles"], w["color"], w["error"]) for w in tridb["wireframes"]] == [
        ("Куб", 98, 192, "#3366cc", None),
        ("Рельеф", 48, 70, "#88aa44", None),
    ]
    assert tridb["wireframes"][0]["attrs"] == [{"name": "Руда", "value": "Au"}] and tridb["layers"] == []
    assert tridb["totals"] == {
        "layers": 0, "lines": 0, "points": 0, "vertices": 0, "rasters": 0, "wireframes": 2, "triangles": 262,
    }  # fmt: skip
    mixed = by["mixed.dxf"]  # a drawing: its lines and its wireframes at once
    assert mixed["kind"] == "lines" and [layer["name"] for layer in mixed["layers"]] == ["FAULT"]
    assert [(w["name"], w["points"], w["triangles"]) for w in mixed["wireframes"]] == [("ORE", 8, 12), ("TOPO", 12, 12)]
    # (a point among the lines of a layer is a line of one vertex)
    assert mixed["totals"]["lines"] == 2 and mixed["totals"]["vertices"] == 3 and mixed["totals"]["wireframes"] == 2
    assert mixed["left_out"] == []  # the meshes of a DXF are loaded now
    assert by["pit.dtm"]["sidecars"] == ["pit.str"] and by["pit.dtm"]["totals"]["triangles"] == 70
    assert by["one.obj"]["wireframes"][0]["points"] is None  # an OBJ tells its vertices only when it is read
    dm = by["ore.dm"]  # Datamine: preliminary, the field to divide by is chosen from the fields of the file
    assert dm["format"] == "datamine" and dm["preliminary"] and dm["choices"] == {"split_by": []}
    assert dm["sidecars"] == ["nodes.dm"] and dm["totals"]["wireframes"] == 1 and not tridb["preliminary"]
    assert any("предварительно" in w for w in dm["warnings"])
    real = by["ORE SEP 2025.tridb"]
    assert real["totals"]["wireframes"] == 66 and real["totals"]["triangles"] == 3590 and real["unread"] == 0
    assert by["TST-FAULTS.DXF"]["kind"] == "wireframes"  # a drawing that holds nothing but a mesh
    assert by["TST-FAULTS.DXF"]["wireframes"][0]["triangles"] == 7417
    assert out["totals"] == {
        "files": 7, "failed": 0, "layers": 1, "lines": 2, "points": 0, "vertices": 3, "rasters": 0,
        "wireframes": 2 + 2 + 1 + 1 + 1 + 66 + 1, "triangles": 262 + 24 + 70 + 192 + 48 + 3590 + 7417,
    }  # fmt: skip
    assert state_of(project.engine, project.folder) == was  # nothing is written

    # the pair in either order of choosing is one group; the points table alone is read by itself
    for order in (("ore.dm", "nodes.dm"), ("nodes.dm", "ore.dm")):
        groups, rest = plan(paths(files, *order))
        assert [(g.main.name, {s: p.name for s, p in g.sidecars.items()}) for g in groups] == [
            ("ore.dm", {".dm": "nodes.dm"})
        ] and rest == []
    assert [g.main.name for g in plan(paths(files, "nodes.dm"))[0]] == ["nodes.dm"]
    # what does not load says why: a later stage, a DWG without the converter (the text of Д-13)
    later = files["one.obj"].with_name("model.omf")
    later.write_bytes(b"OMF")
    seen = inspect_files([str(later)])
    assert seen["files"] == [] and "OMF" in seen["rest"][0]["message"] and ".tridb" in seen["rest"][0]["hint"]


def test_dwg_without_the_converter_is_told(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.formats import dwg
    from bf_core.importing.object_files import inspect_files

    monkeypatch.delenv(dwg.ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing here"))
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(name, str(tmp_path / "Program Files"))
    path = tmp_path / "Карьер.dwg"
    path.write_bytes(b"AC1027".ljust(128, b"\x00"))
    (entry,) = inspect_files([str(path)])["files"]
    assert not entry["ok"] and entry["error"]["code"] == "oda_not_found"
    assert "ODA File Converter" in entry["error"]["hint"] and "DXF" in entry["error"]["hint"]


# --- the job ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "count", "points", "triangles"), REAL_TRIDB)
def test_real_tridb_is_imported(project, name: str, count: int, points: int, triangles: int) -> None:  # type: ignore[no-untyped-def]
    """Every wireframe of the file is an object: geometry and order bit for bit, names, colours, attributes,
    `meta.source`, measures against the file, a row of the journal each, the file itself kept once."""
    from bf_core.formats.tridb import declared, read_tridb

    ctx = run_job(project.folder, {"files": [str(REAL / name)]})
    res = ctx.result
    (entry,) = res["files"]
    assert (entry["file"], entry["format"], entry["kind"]) == (name, "tridb", "wireframes")
    assert (entry["wireframes"], entry["triangles"], entry["mesh_points"], entry["skipped"]) == (
        count, triangles, points, 0,
    )  # fmt: skip
    assert res["wireframes"] == count and res["triangles"] == triangles and res["failed"] == []
    assert res["objects"] == entry["object_ids"] == res["wireframe_ids"] and len(res["objects"]) == count

    rows = stored(project)
    assert [r["id"] for r in rows] == res["objects"]
    assert all(r["state"] == "ready" and r["job_id"] is None and r["parent_id"] is None for r in rows)
    assert {r["folder"] for r in rows} == {name}  # the folder of the tree is the name of the file
    worst = 0.0
    for row, ref in zip(rows, read_tridb(REAL / name), strict=True):
        got = mesh_of(project, row["id"])
        assert (got.name, got.color, got.attrs) == (ref.name, ref.color, ref.attrs)
        assert bits(got.xyz) == bits(ref.xyz) and got.tri.tobytes() == ref.tri.tobytes()  # the order of the file
        assert row["meta"]["source"] == ref.source and got.source == ref.source
        assert (row["meta"]["format"], row["meta"]["filename"]) == ("tridb", name)
        assert {"xyz", "tri"} <= row["roles"] <= {"xyz", "tri", "seq"}
        stats, file = row["info"]["stats"], declared(ref.source)
        assert {"area", "volume", "closed", "open_edges", "nonmanifold_edges", "degenerate"} <= set(stats)
        for key in ("volume", "area"):
            if stats[key] is not None and file[key] is not None:
                worst = max(worst, abs(stats[key] - file[key]) / max(abs(file[key]), 1e-300))
    assert worst <= 1e-9, worst  # ТЗ 9.12; measured ≤ 1.4e-13
    assert any("seq" in r["roles"] for r in rows)  # the store keeps another order: the order of the file is kept

    sha = sha_of(REAL / name)
    with dialect.read_tx(project.engine) as conn:
        log = conn.execute(sa.select(import_log).order_by(import_log.c.id)).mappings().all()
        assert [(r["object_id"], r["file_name"], r["format"], r["target"]) for r in log] == [
            (i, name, "tridb", "objects") for i in res["objects"]
        ]
        assert all(r["sha256"] == sha and r["job_id"] == ctx.id for r in log)
        assert log[0]["counts"] == {
            "kind": "wireframe", "name": rows[0]["name"], "points": rows[0]["info"]["point_count"],
            "triangles": rows[0]["info"]["triangle_count"],
        }  # fmt: skip
        own = conn.execute(sa.select(blob_owners).where(blob_owners.c.owner_kind == "import")).mappings().all()
        assert sorted(r["owner_id"] for r in own) == [r["id"] for r in log]
        assert {(r["sha256"], r["role"]) for r in own} == {(sha, "source")}  # one blob, an owner per wireframe
    assert BlobStore(project.folder).path_of(sha).read_bytes() == (REAL / name).read_bytes()
    text = "\n".join(ctx.lines)
    assert f"{name}: каркасов — {count}" in text and "треугольников — " in text
    assert ctx.progress == sorted(ctx.progress) and ctx.progress[-1] == 0.98
    assert not list((project.folder / "blobs" / ".tmp").iterdir())


def test_drawing_gives_a_line_file_and_wireframes(project, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    """DXF: the lines and the wireframes of one pass; `TST-FAULTS.DXF` — one wireframe, no folder of its own."""
    from bf_core.formats.dxf_lines import read_dxf
    from bf_core.lines import store as ls

    real = REAL / "TST-FAULTS.DXF"
    ctx = run_job(project.folder, {"files": [str(files["mixed.dxf"]), str(real)], "folder": "Проект 2026"})
    res = ctx.result
    assert [(f["file"], f["kind"]) for f in res["files"]] == [
        ("mixed.dxf", "lines"), ("mixed.dxf", "wireframes"), ("TST-FAULTS.DXF", "wireframes"),
    ]  # fmt: skip
    assert (res["layers"], res["lines"], res["vertices"], res["wireframes"], res["triangles"]) == (1, 2, 3, 3, 7441)
    rows = stored(project)
    assert [(r["name"], r["folder"]) for r in rows] == [
        ("ORE", "Проект 2026/mixed.dxf"), ("TOPO", "Проект 2026/mixed.dxf"), ("FLT-Combined_TST", "Проект 2026"),
    ]  # fmt: skip
    want = read_dxf(files["mixed.dxf"]).wireframes + read_dxf(real).wireframes
    for row, ref in zip(rows, want, strict=True):
        got = mesh_of(project, row["id"])
        assert bits(got.xyz) == bits(ref.xyz) and got.tri.tobytes() == ref.tri.tobytes()
        assert got.color == ref.color and row["meta"]["source"] == ref.source
    fault = rows[2]["info"]
    assert (fault["point_count"], fault["triangle_count"]) == (7444, 7417) and not fault["stats"]["closed"]
    ore = rows[0]["info"]["stats"]  # the checks at loading: the cube of 3DFACE records is closed, 1000 m³
    assert ore["closed"] and ore["orientation"] == "outward" and ore["volume"] == pytest.approx(1000.0, rel=1e-12)
    assert ore["area"] == pytest.approx(600.0, rel=1e-12) and ore["open_edges"] == 0 and ore["degenerate"] == 0

    sha = sha_of(files["mixed.dxf"])
    with dialect.read_tx(project.engine) as conn:
        file_id = res["files"][0]["object_id"]
        line_file = conn.execute(sa.select(objects).where(objects.c.id == file_id)).mappings().one()
        assert (line_file["kind"], line_file["name"], line_file["folder"]) == ("line_file", "mixed.dxf", "Проект 2026")
        assert len(ls.layer_ids(conn, file_id)) == 1
        owners = conn.execute(sa.select(blob_owners).where(blob_owners.c.sha256 == sha)).mappings().all()
        log = {r["id"]: r for r in conn.execute(sa.select(import_log)).mappings()}
        # the drawing is kept once: the line file owns it, and so does the journal row of each wireframe
        assert sorted((r["owner_kind"], r["role"]) for r in owners) == [
            ("import", "source"), ("import", "source"), ("object", "source"),
        ]  # fmt: skip
        mine = [log[r["owner_id"]]["object_id"] for r in owners if r["owner_kind"] == "import"]
        assert sorted(mine) == [rows[0]["id"], rows[1]["id"]]
        assert [r["counts"]["kind"] for r in log.values()] == ["lines", "wireframe", "wireframe", "wireframe"]
    text = "\n".join(ctx.lines)
    assert "mixed.dxf: слоёв — 1" in text and "mixed.dxf: каркасов — 2 (замкнутых — 1)" in text
    assert "TST-FAULTS.DXF: каркасов — 1 (замкнутых — 0), треугольников — 7 417, вершин — 7 444" in text


def test_pairs_names_and_sources(project, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    """A Surpac pair and a Datamine pair keep both files; a single wireframe gets no folder; a name taken in
    the project gets a number and `meta.source.as` remembers it."""
    params = {"files": paths(files, "pit.dtm", "pit.str", "ore.dm", "nodes.dm", "one.obj", "Тела.tridb")}
    first = run_job(project.folder, params)
    assert [(f["file"], f["wireframes"]) for f in first.result["files"]] == [
        ("pit.dtm", 1), ("ore.dm", 1), ("one.obj", 1), ("Тела.tridb", 2),
    ]  # fmt: skip
    rows = stored(project)
    assert [(r["name"], r["folder"]) for r in rows] == [
        ("pit", None), ("ore", None), ("Куб", None), ("Куб (2)", "Тела.tridb"), ("Рельеф", "Тела.tridb"),
    ]  # fmt: skip
    assert rows[2]["meta"]["source"] == {"format": "obj", "name": "Куб"}
    assert rows[3]["meta"]["source"]["as"] == "Куб (2)" and rows[3]["meta"]["source"]["name"] == "Куб"
    assert rows[3]["info"]["color"] == "#3366cc" and rows[3]["info"]["attrs"] == [{"name": "Руда", "value": "Au"}]
    assert rows[1]["meta"]["source"]["points_file"] == "nodes.dm"
    assert any("предварительно" in w for w in rows[1]["meta"]["warnings"])  # Datamine says so in the properties
    v, t = cube(2, 10.0)
    ore = mesh_of(project, rows[1]["id"])
    assert bits(ore.xyz) == bits(v + 300.0) and ore.tri.tolist() == t.tolist()
    with dialect.read_tx(project.engine) as conn:
        log = {r["object_id"]: r for r in conn.execute(sa.select(import_log)).mappings()}
        roles = {
            (log_row["file_name"], str(r.role)): str(r.sha256)
            for log_row in log.values()
            for r in conn.execute(
                sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
                    blob_owners.c.owner_kind == "import", blob_owners.c.owner_id == log_row["id"]
                )
            )
        }
    assert roles == {
        ("pit.dtm", "source"): sha_of(files["pit.dtm"]), ("pit.dtm", "sidecar:str"): sha_of(files["pit.str"]),
        ("ore.dm", "source"): sha_of(files["ore.dm"]), ("ore.dm", "sidecar:dm"): sha_of(files["nodes.dm"]),
        ("one.obj", "source"): sha_of(files["one.obj"]), ("Тела.tridb", "source"): sha_of(files["Тела.tridb"]),
    }  # fmt: skip
    assert log[rows[0]["id"]]["mapping"]["sidecars"] == ["pit.str"]

    # deleting the wireframes of a file one by one: the file stays in the project until the last one goes
    from bf_core.storage.gc import collect

    store, sha = BlobStore(project.folder), sha_of(files["Тела.tridb"])
    for wf_id, left in ((rows[3]["id"], True), (rows[4]["id"], False)):
        with dialect.write_tx(project.engine) as conn:
            ws.delete_wireframe(conn, wf_id)
        collect(project.engine, store)
        assert store.exists(sha) is left


def test_cancel_at_any_step_leaves_nothing(project, files: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """ТЗ 7.8: «Отмена до последнего шага не оставляет объекта в дереве» — nor rows, file references or
    temporary files; what was in the project before stays as it was."""
    from bf_core.storage.gc import collect

    monkeypatch.setattr("bf_core.importing.object_meshes.PORTION", 1)  # a transaction per wireframe
    names = ("Тела.tridb", "mixed.dxf", "pit.dtm", "pit.str")
    run_job(project.folder, {"files": paths(files, "one.obj")})  # something already in the project
    was = state_of(project.engine, project.folder)
    with dialect.read_tx(project.engine) as conn:
        kept = set(conn.execute(sa.select(blobs.c.sha256)).scalars())
    ticks = 0
    for at in range(1, 400):
        try:
            ctx = run_job(project.folder, {"files": paths(files, *names)}, cancel_at=at)
        except JobCancelled:
            ticks = at
            assert state_of(project.engine, project.folder) == was, f"cancelled at tick {at}"
            continue
        break
    else:
        raise AssertionError("the job never finished")
    assert ticks >= 30, ticks  # reading, the checks, every wireframe, the source files: each can be cancelled
    now = state_of(project.engine, project.folder)
    assert ctx.result["wireframes"] == 5 and len(now["objects"]) == len(was["objects"]) + 5 + 2  # + file and layer
    assert all(state == "ready" for _kind, _name, state in now["objects"]) and now["tmp"] == []
    with pytest.raises(JobCancelled):
        run_job(project.folder, {"files": paths(files, *names)}, cancel_at=ticks)
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        owned = set(conn.execute(sa.select(blob_owners.c.sha256)).scalars())
    assert kept <= owned
    collect(project.engine, store)  # what a cancelled attempt put into the storage has no owner: it goes
    with dialect.read_tx(project.engine) as conn:
        assert set(conn.execute(sa.select(blobs.c.sha256)).scalars()) == owned
    assert all(store.exists(sha) for sha in owned)


def test_broken_wireframes_and_broken_files(  # type: ignore[no-untyped-def]
    project, files: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wireframe that cannot be read is skipped and said so; a file that breaks half-way leaves nothing of
    itself — its first solid is in the project as `building` by then; the other files are loaded. When nothing
    loads, the job fails with the first reason."""
    monkeypatch.setattr("bf_core.importing.object_meshes.PORTION", 1)  # a transaction per wireframe
    v, t = cube(1, 10.0)
    three = build_tridb(
        tmp_path / "three.tridb",
        [
            {"id": 1, "name": "Первый", "xyz": v, "tri": t, "general": {"Volume": 1000.0, "SurfaceArea": 601.0}},
            {"id": 2, "name": "Битый", "xyz": v, "tri": t, "blob": b"\xff\xff\xff\xff" + b"\x00" * 20},
            {"id": 3, "name": "Лист", "xyz": v, "tri": t[:6], "general": {"Volume": 12.5}},
        ],
    )
    text = "solid first\n" + "".join(
        "facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {x!r} {y!r} {z!r}\n" for x, y, z in v[tri].tolist())
        + "endloop\nendfacet\n"
        for tri in t
    ) + "endsolid first\nsolid second\nfacet normal 0 0 0\nouter loop\nvertex 1 2 3\nvertex 4 5"
    cut = tmp_path / "cut.stl"
    cut.write_text(text, encoding="ascii")
    was = state_of(project.engine, project.folder)
    ctx = run_job(project.folder, {"files": [str(cut), str(three), str(files["one.obj"])]})
    res = ctx.result
    assert [(f["file"], f["wireframes"], f["skipped"]) for f in res["files"]] == [
        ("three.tridb", 2, 1), ("one.obj", 1, 0),
    ]  # fmt: skip
    assert [(f["file"], bool(f["message"]), bool(f["hint"])) for f in res["failed"]] == [("cut.stl", True, True)]
    rows = stored(project)
    assert [r["name"] for r in rows] == ["Первый", "Лист", "Куб"]  # nothing of the broken file: not its first solid
    assert all(r["state"] == "ready" for r in rows)
    log = "\n".join(ctx.lines)
    assert "«cut.stl» не загружен" in log and "каркас «Битый» не загружен" in log
    assert "three.tridb: каркасов — 2 (замкнутых — 1), треугольников — 18, вершин — 16; не загружено — 1" in log
    # the checks at loading: the area of the file is not that of the geometry; a volume without a closed solid
    assert "каркас «Первый»: Площадь поверхности каркаса в файле (601.0)" in log
    assert "каркас «Лист»: в файле указан объём, но каркас не замкнут" in log
    assert any("Площадь поверхности" in w for w in rows[0]["meta"]["warnings"])  # kept with the wireframe
    assert rows[0]["info"]["stats"]["volume"] == pytest.approx(1000.0, rel=1e-12)
    assert rows[1]["info"]["stats"]["volume"] is None  # never 0

    with dialect.read_tx(project.engine) as conn:  # the first solid of the cut file was written, then removed
        assert conn.execute(sa.select(sa.func.max(objects.c.id))).scalar_one() == len(rows) + 1
        assert conn.execute(sa.select(sa.func.count()).select_from(wireframes)).scalar_one() == len(rows)
    before = state_of(project.engine, project.folder)
    for bad, code in (([cut], "truncated"), ([tmp_path / "нет такого.tridb"], "file_unreadable")):
        with pytest.raises(JobError) as err:
            run_job(project.folder, {"files": [str(p) for p in bad]})
        assert err.value.code == code and bad[0].name in err.value.message and err.value.hint
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"files": [str(files["one.obj"].with_suffix(".omf"))]})
    assert err.value.code == "nothing_to_import" and ".tridb" in err.value.hint
    assert state_of(project.engine, project.folder) == before and len(before["objects"]) == len(was["objects"]) + 3


def test_a_retry_after_the_commit_changes_nothing(project, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    """The worker died after the final transaction: the second attempt finds the import done."""
    params = {"files": paths(files, "mixed.dxf", "Тела.tridb", "pit.dtm", "pit.str")}
    first = run_job(project.folder, params, job_id=7)
    was = state_of(project.engine, project.folder)
    second = run_job(project.folder, params, job_id=7)
    assert second.result == first.result and state_of(project.engine, project.folder) == was
    assert [(f["kind"], f.get("wireframes")) for f in second.result["files"]] == [
        ("lines", None), ("wireframes", 2), ("wireframes", 2), ("wireframes", 1),
    ]  # fmt: skip
    assert any("уже записаны" in line for line in second.lines)


def test_many_small_wireframes_go_in_portions(project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Sixty wireframes with one name: a few transactions, names that differ, the order of the file."""
    from bf_core.formats.obj import write_obj
    from bf_core.importing import object_meshes as om

    v, t = cube(1, 1.0)
    path = tmp_path / "много.obj"
    write_obj(path, [MeshSet("Линза", v + [3.0 * k, 0.0, 0.0], t) for k in range(60)])
    flushes: list[int] = []
    real = dialect.write_tx
    monkeypatch.setattr(om.dialect, "write_tx", lambda engine: flushes.append(1) or real(engine))
    ctx = run_job(project.folder, {"files": [str(path)]})
    assert ctx.result["wireframes"] == 60 and len(flushes) <= 60 // om.PORTION + 4  # portions, the folder, the end
    rows = stored(project)
    assert [r["name"] for r in rows] == ["Линза"] + [f"Линза ({k})" for k in range(2, 61)]
    assert [r["sort_key"] for r in rows] == [float(k) for k in range(60)]
    assert {r["folder"] for r in rows} == {"много.obj"}
    assert bits(mesh_of(project, rows[59]["id"]).xyz) == bits(v + [177.0, 0.0, 0.0])


# --- through the API and the scheduler -------------------------------------------------------------------


def test_wizard_through_the_api(pclient, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    """inspect → start → the job in a worker process → the tree of wireframes and of lines."""
    pclient.post(f"{API}/projects", json={"name": "Каркасы"})
    body = {"files": paths(files), "options": {str(files["ore.dm"]): {"split_by": ""}}, "folder": "Импорт"}
    seen = pclient.post(f"{API}/import/objects/inspect", json=body)
    assert seen.status_code == 200, seen.text
    assert seen.json()["totals"]["wireframes"] == 7 and seen.json()["totals"]["files"] == 5
    r = pclient.post(f"{API}/import/objects", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["result"]["wireframes"] == 7, job
    assert len(job["result"]["wireframe_ids"]) == 7 and job["result"]["failed"] == []
    tree = pclient.get(f"{API}/wireframes").json()["wireframes"]
    assert sorted((w["name"], w["folder"]) for w in tree) == sorted([
        ("Куб", "Импорт/Тела.tridb"), ("Рельеф", "Импорт/Тела.tridb"), ("ORE", "Импорт/mixed.dxf"),
        ("TOPO", "Импорт/mixed.dxf"), ("pit", "Импорт"), ("Куб (2)", "Импорт"), ("ore (2)", "Импорт"),
    ])  # fmt: skip  (names differ without case: «ore» of Datamine comes after the layer «ORE» of the drawing)
    assert [f["name"] for f in pclient.get(f"{API}/lines/files").json()["files"]] == ["mixed.dxf"]
    first = tree[0]
    assert pclient.get(f"{API}/viz/wireframes/{first['id']}").status_code == 200  # the 3D manifest of an imported one
