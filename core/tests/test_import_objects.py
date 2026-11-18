"""Wizard «Импорт файлов» (ТЗ 7.8, 7.18, 7.19; ADR 0011 §1, §3): the composition of a selection and the job
`import.objects` — lines, points and rasters with their georeference. What is checked: the geometry bit for bit,
JGW at the centre of the top-left pixel, «Уровень растров по Z», the section fallback, blobs and their roles, the
operation log, cancellation at every step, a retry after the commit, and the job through the scheduler."""

from __future__ import annotations

import hashlib
import itertools
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa
from PIL import Image

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, import_log, line_layers, objects, rasters
from bf_core.formats.lineset import from_lines
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.projects.service import projects_of
from tests.formats.test_georef import grf_text, tab_text
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

# UTM coordinates: one float64 step of Y is 9.3e-10 m.
SQUARE = np.array(
    [
        [512_345.678_901_234, 5_912_345.123_456_789, 412.345_678_9],
        [512_371.913, 5_912_345.123_456_789, 412.345_678_9],
        [512_371.913, 5_912_390.335, 412.345_678_9],
        [512_345.678_901_234, 5_912_390.335, 412.345_678_9],
        [512_345.678_901_234, 5_912_345.123_456_789, 412.345_678_9],
    ]
)
OPEN = np.array(
    [[512_500.25, 5_912_100.5, 1200.0], [512_530.25, 5_912_140.5, 1188.5], [512_533.0, 5_912_150.0, 1180.25]]
)
HIGH = SQUARE + [100.0, 0.0, 50.0]
WORLD = (0.5, 0.0, 0.0, -0.5, 512_000.25, 5_912_000.75)  # A, D, B, E, C, F: C and F — the centre of the first pixel
SCAN = np.array([[1000.0, 5000.0, 600.0], [1300.0, 5400.0, 600.0], [1300.0, 5400.0, 350.0], [1000.0, 5000.0, 350.0]])
_ids = itertools.count(100)


class Ctx(JobContext):
    """The context of a worker, run in the test process; `cancel_at` — the tick at which «Отменить» arrives."""

    def __init__(self, folder: Path, params: dict[str, Any], job_id: int, cancel_at: int | None = None) -> None:
        self.sent: list[tuple[Any, ...]] = []
        self.ticks = 0
        self.cancel_at = cancel_at
        self.progress: list[float] = []
        job = {"id": job_id, "params": params, "project_folder": str(folder)}
        super().__init__(job, self.sent.append, threading.Event())

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        self.ticks += 1
        if progress is not None:
            self.progress.append(progress)  # every report, before the scheduler's five-a-second limit
        if self.cancel_at is not None and self.ticks >= self.cancel_at:
            self._cancelled.set()
        super().tick(progress, message)

    @property
    def lines(self) -> list[str]:
        return [str(m[3]) for m in self.sent if m[0] == "log"]


def run_job(folder: Path, params: dict[str, Any], *, job_id: int | None = None, cancel_at: int | None = None) -> Ctx:
    """What the worker and the scheduler do with a job: run the handler; drop what a stopped job was building."""
    from bf_core.importing.objects import import_objects
    from bf_core.jobs.store import finish_cleanup

    ctx = Ctx(folder, params, next(_ids) if job_id is None else job_id, cancel_at)
    try:
        ctx.result = import_objects(ctx)  # type: ignore[attr-defined]
    except (JobCancelled, JobError):
        finish_cleanup(ctx.engine(), False, ctx.id)
        raise
    finally:
        ctx.close()
    return ctx


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "projects", "Объекты", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    yield SimpleNamespace(folder=folder, engine=engine)
    engine.dispose()


def picture(width: int, height: int) -> np.ndarray:
    x, y = np.meshgrid(np.arange(width, dtype=np.uint8), np.arange(height, dtype=np.uint8))
    return np.dstack([x * 3, y * 5, np.full_like(x, 128)]).astype(np.uint8)


def make_files(folder: Path) -> dict[str, Path]:
    """A selection of the wizard: two string files, points, three rasters, and what will not load."""
    from bf_core.formats.str_micromine import write_str

    folder.mkdir(parents=True, exist_ok=True)
    out = {name: folder / name for name in (
        "contours.str", "points.csv", "map.jpg", "map.jgw", "scan.png", "scan.grf", "plan.tif", "plan.tfw",
        "plan.tab", "lonely.tfw", "model.obj", "bad.str",
    )}  # fmt: skip
    write_str(out["contours.str"], [from_lines("100", [SQUARE, OPEN]), from_lines("200", [HIGH])])
    out["points.csv"].write_text(
        "X;Y;Z;NAME\n512000,5;5912000,25;101,5;Репер 1\n512001;5912001;;Репер 2\n", encoding="utf-8"
    )
    Image.fromarray(picture(64, 48)).save(out["map.jpg"], quality=95)
    out["map.jgw"].write_text("".join(f"{v!r}\n" for v in WORLD))
    Image.fromarray(picture(40, 20)).save(out["scan.png"])
    corners = [(0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (0.0, 20.0)]
    points = [(c, r, *xyz, 1.0) for (c, r), xyz in zip(corners, SCAN.tolist(), strict=True)]
    out["scan.grf"].write_text(grf_text([0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], points))
    Image.fromarray(picture(30, 20)).save(out["plan.tif"])
    out["plan.tfw"].write_text("2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n")  # corners: (1000, 3000) … (1060, 2960)
    # the TAB disagrees by 0.5 m in X at one point: the world file wins, the deviation goes to the properties
    out["plan.tab"].write_text(
        tab_text([(1000.0, 3000.0, 0.0, 0.0), (1060.5, 3000.0, 30.0, 0.0), (1060.0, 2960.0, 30.0, 20.0)])
    )
    out["lonely.tfw"].write_text("1\n0\n0\n-1\n0\n0\n")
    out["model.obj"].write_text("v 0 0 0\n")
    out["bad.str"].write_text("это не стринги\n", encoding="utf-8")
    return out


@pytest.fixture()
def files(tmp_path: Path) -> dict[str, Path]:
    return make_files(tmp_path / "исходные")


def paths(files: dict[str, Path], *names: str) -> list[str]:
    return [str(files[n]) for n in (names or files)]


def state_of(engine: sa.Engine, folder: Path) -> dict[str, Any]:
    """Everything an import may leave behind."""
    with dialect.read_tx(engine) as conn:
        count = lambda t: conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one()  # noqa: E731
        tmp = folder / "blobs" / ".tmp"
        return {
            "objects": [tuple(r) for r in conn.execute(sa.select(objects.c.kind, objects.c.name, objects.c.state))],
            "layers": count(line_layers),
            "rasters": count(rasters),
            "owners": count(blob_owners),
            "log": count(import_log),
            "tmp": sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else [],
        }


def roles_of(conn: sa.Connection, object_id: int) -> dict[str, str]:
    own = sa.and_(blob_owners.c.owner_kind == "object", blob_owners.c.owner_id == object_id)
    return {
        str(r.role): str(r.sha256) for r in conn.execute(sa.select(blob_owners.c.role, blob_owners.c.sha256).where(own))
    }


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the composition (inspect) -------------------------------------------------------------------------


def test_composition_of_a_selection(files: dict[str, Path], project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.importing.object_files import inspect_files

    was = state_of(project.engine, project.folder)
    out = inspect_files(paths(files), raster_z={"elevation": "250,5", "shift": "−5"})
    by = {f["file"]: f for f in out["files"]}
    assert list(by) == ["contours.str", "points.csv", "map.jpg", "scan.png", "plan.tif", "bad.str"]

    strings = by["contours.str"]
    assert strings["ok"] and strings["format"] == "micromine_str" and strings["kind"] == "lines"
    assert [(s["name"], s["lines"], s["vertices"], s["closed"]) for s in strings["layers"]] == [
        ("100", 2, 8, 1),
        ("200", 1, 5, 1),
    ]
    assert strings["totals"] == {"layers": 2, "lines": 3, "points": 0, "vertices": 13, "rasters": 0}
    assert strings["layers"][0]["length"] == pytest.approx(
        2 * (26.234_098_766 + 45.211_543_211) + np.sqrt((np.diff(OPEN, axis=0) ** 2).sum(axis=1)).sum(), abs=1e-6
    )
    pts = by["points.csv"]
    assert pts["kind"] == "points" and pts["totals"]["points"] == 2 and pts["totals"]["vertices"] == 0
    assert pts["layers"][0]["extent"][2] == 101.5 and pts["layers"][0]["attributes"][0]["name"] == "NAME"

    plan = by["map.jpg"]["rasters"][0]  # a world file has no height: the level of the wizard, then the shift
    assert by["map.jpg"]["kind"] == "raster" and by["map.jpg"]["sidecars"] == ["map.jgw"]
    assert (plan["width"], plan["height"], plan["image"]) == (64, 48, "JPEG")
    assert plan["georef"]["source"] == "world" and plan["georef"]["source_label"] == "World-файл"
    assert plan["elevation"] == 245.5 and not plan["vertical"] and plan["georef"]["pixel"] == 0.5
    assert "Сдвиг по Z: −5 м" in by["map.jpg"]["warnings"]
    scan = by["scan.png"]["rasters"][0]  # vertical: the level does not apply, the shift moves every corner
    assert scan["vertical"] and scan["elevation"] is None and scan["georef"]["source"] == "grf"
    assert np.abs(np.array(scan["georef"]["corners3d"]) - (SCAN + [0.0, 0.0, -5.0])).max() <= 1e-9
    both = by["plan.tif"]["rasters"][0]  # TFW and TAB: the world file is used, the TAB gives deviations
    assert both["georef"]["source"] == "world" and by["plan.tif"]["sidecars"] == ["plan.tab", "plan.tfw"]
    tab = next(c for c in both["georef"]["checks"] if c["source"] == "tab")
    assert tab["max"] == pytest.approx(0.5, abs=1e-9) and len(tab["points"]) == 3

    bad = by["bad.str"]  # what will not load, and why — in Russian with a hint
    assert not bad["ok"] and bad["error"]["code"] == "unsupported" and "Surpac" in bad["error"]["hint"]
    rest = {r["file"]: r for r in out["rest"]}
    assert set(rest) == {"lonely.tfw", "model.obj"}
    assert rest["lonely.tfw"]["code"] == "sidecar" and "вспомогательный" in rest["lonely.tfw"]["message"]
    assert "каркасы" in rest["model.obj"]["message"] and rest["model.obj"]["hint"]
    assert out["totals"] == {
        "files": 5, "failed": 1, "layers": 3, "lines": 3, "points": 2, "vertices": 13, "rasters": 3,
    }  # fmt: skip
    assert out["raster_z"] == {"applies": True, "elevation": 250.5, "shift": -5.0}
    assert state_of(project.engine, project.folder) == was  # nothing is written


def test_level_numbers_take_a_comma_and_refuse_text(files: dict[str, Path]) -> None:
    from bf_core.importing.object_files import inspect_files, parse_level

    assert parse_level({"elevation": " 1 250,75 ", "shift": "-0.5"}) == (1250.75, -0.5)
    assert parse_level({"elevation": "", "shift": None}) == (None, 0.0) and parse_level(None) == (None, 0.0)
    for bad in ("abc", "1,2,3", True, float("nan"), "inf"):
        with pytest.raises(ValueError, match="Высота растров"):
            parse_level({"elevation": bad})
    # a plan raster without a height is not shifted (never to 0): it stays under the lowest object, with a note
    out = inspect_files(paths(files, "map.jpg", "map.jgw"), raster_z={"shift": 12})
    assert out["files"][0]["rasters"][0]["elevation"] is None and not out["files"][0]["rasters"][0]["has_elevation"]
    assert any("Сдвиг по Z не применён" in w for w in out["files"][0]["warnings"])
    assert out["files"][0]["rasters"][0]["georef"]["corners3d"][0][2] is None


# --- the job ---------------------------------------------------------------------------------------------


def test_lines_points_and_rasters_are_written(project, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    from bf_core.lines import store as ls
    from bf_core.rasters import store as rs
    from bf_core.storage.blobs import BlobStore

    ctx = run_job(project.folder, {"files": paths(files), "folder": "Съёмка 2026"})
    res = ctx.result
    assert [(f["file"], f["kind"]) for f in res["files"]] == [
        ("contours.str", "lines"), ("points.csv", "points"), ("map.jpg", "raster"), ("scan.png", "raster"),
        ("plan.tif", "raster"),
    ]  # fmt: skip
    assert (res["layers"], res["lines"], res["points"], res["vertices"], res["rasters"]) == (3, 3, 2, 13, 3)
    assert [f["file"] for f in res["failed"]] == ["bad.str"] and len(res["rest"]) == 2
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        rows = conn.execute(sa.select(objects).order_by(objects.c.id)).mappings().all()
        assert all(r["state"] == "ready" and r["job_id"] is None for r in rows)
        assert [r["kind"] for r in rows] == [
            "line_file", "line_layer", "line_layer", "line_file", "line_layer", "raster", "raster", "raster",
        ]  # fmt: skip
        assert [r["id"] for r in rows if r["parent_id"] is None] == res["objects"]
        strings = rows[0]
        assert strings["name"] == "contours.str" and strings["folder"] == "Съёмка 2026"
        meta = strings["meta"]
        assert meta["format"] == "micromine_str" and meta["filename"] == "contours.str" and meta["encoding"]
        names = [f["name"] for f in meta["fields"]]  # the field schema of the source, in the order of the file
        assert names[:3] == ["EAST", "NORTH", "RL"] and {"JOIN", "STRING"} <= set(names)
        assert all({"type", "width", "decimals", "description"} <= set(f) for f in meta["fields"])
        assert roles_of(conn, strings["id"]) == {"source": sha_of(files["contours.str"])}  # byte for byte

        ids = ls.layer_ids(conn, strings["id"])
        first, second = (ls.read_layer(conn, store, i) for i in ids)
        assert (first.name, second.name) == ("100", "200") and first.color != second.color
        assert first.xyz.tobytes() == np.vstack([SQUARE, OPEN]).tobytes()  # float64 bit for bit
        assert second.xyz.tobytes() == HIGH.tobytes()
        assert first.offsets.tolist() == [0, 5, 8] and first.closed.tolist() == [1, 0]
        # two small layers of one portion: entries of one pack (`lines/pack.py`) with all their files in it
        one, two = roles_of(conn, ids[0]), roles_of(conn, ids[1])
        assert list(one) == ["pack:0"] and list(two) == ["pack:1"] and one["pack:0"] == two["pack:1"]
        assert set(ls.layer_files(store, one).parts) >= {"xyz", "offsets", "closed", "seq"}
        info = ls.layer_info(conn, ids[0])
        assert info["line_count"] == 2 and info["closed_count"] == 1 and info["color"].startswith("#")
        assert strings["extent"][:2] == [SQUARE[0, 0], OPEN[0, 1]] and strings["extent"][5] == 1200.0

        points = ls.read_layer(conn, store, ls.layer_ids(conn, rows[3]["id"])[0])
        assert points.points_only and points.offsets is None and len(points.xyz) == 2
        assert points.xyz[0].tolist() == [512000.5, 5912000.25, 101.5] and np.isnan(points.xyz[1, 2])  # empty, not 0
        assert points.attrs.column("NAME").to_pylist() == ["Репер 1", "Репер 2"]

        by_name = {r["name"]: rs.raster_info(conn, r["id"]) for r in rows if r["kind"] == "raster"}
        plan, scan, both = by_name["map"], by_name["scan"], by_name["plan"]
        # JPG + JGW: the centre of the top-left pixel is exactly where the world file puts it (ТЗ 7.19)
        a, b, c, d, e, f = plan["georef"]["affine"]
        assert abs(a * 0.5 + b * 0.5 + c - WORLD[4]) <= 1e-9 and abs(d * 0.5 + e * 0.5 + f - WORLD[5]) <= 1e-9
        assert plan["georef"]["corners3d"] == [
            [512000.0, 5912001.0, None], [512032.0, 5912001.0, None], [512032.0, 5911977.0, None],
            [512000.0, 5911977.0, None],
        ]  # fmt: skip
        assert plan["elevation"] is None and not plan["vertical"] and plan["opacity"] == 1.0 and plan["version"] == 1
        assert plan["extent"] == [512000.0, 5911977.0, None, 512032.0, 5912001.0, None]
        roles = roles_of(conn, plan["id"])
        assert set(roles) == {"source", "sidecar:jgw", "display0", "display1"}
        assert roles["source"] == sha_of(files["map.jpg"]) and roles["sidecar:jgw"] == sha_of(files["map.jgw"])
        shown = Image.open(store.path_of(roles["display0"]))
        assert shown.size == (64, 48) and plan["displays"][0] == {"level": 0, "width": 64, "height": 48,
                                                                 "mime": "image/jpeg"}  # fmt: skip
        assert scan["vertical"] and scan["elevation"] is None and scan["has_elevation"]
        assert np.abs(np.array(scan["georef"]["corners3d"]) - SCAN).max() <= 1e-9
        assert scan["extent"][2] == pytest.approx(350.0) and scan["extent"][5] == pytest.approx(600.0)
        assert set(roles_of(conn, scan["id"])) == {"source", "sidecar:grf", "display0", "display1"}
        assert both["georef"]["source"] == "world" and both["sidecars"] == ["plan.tab", "plan.tfw"]
        assert [c["source"] for c in both["georef"]["checks"]] == ["tab"]
        assert set(roles_of(conn, both["id"])) == {"source", "sidecar:tfw", "sidecar:tab", "display0", "display1"}

        log = conn.execute(sa.select(import_log).order_by(import_log.c.id)).mappings().all()
        assert [(r["file_name"], r["target"], r["object_id"]) for r in log] == [
            (f["file"], "objects", f["object_id"]) for f in res["files"]
        ]
        assert all(r["job_id"] == ctx.id for r in log) and log[0]["sha256"] == sha_of(files["contours.str"])
        assert log[0]["counts"] == {"kind": "lines", "layers": 2, "lines": 3, "points": 0, "vertices": 13, "rasters": 0}
    # the operation log: what was loaded, what was not and why
    text = "\n".join(ctx.lines)
    assert "contours.str: слоёв — 2, линий — 3, вершин — 13" in text and "map.jpg: растр 64 × 48" in text
    assert "«bad.str» не загружен" in text and "lonely.tfw" in text and "model.obj" in text
    assert ctx.progress == sorted(ctx.progress) and ctx.progress[-1] == 0.98  # never goes back
    assert not list((project.folder / "blobs" / ".tmp").iterdir())


def test_raster_level_of_the_wizard(project, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    """«Уровень растров по Z»: the height replaces the one of the georeference (plan rasters), the shift moves
    every raster — all four corners of a vertical one (ТЗ 7.19)."""
    from bf_core.rasters import store as rs

    names = ("map.jpg", "map.jgw", "scan.png", "scan.grf")
    ctx = run_job(project.folder, {"files": paths(files, *names), "raster_z": {"elevation": "250,5", "shift": "-5"}})
    with dialect.read_tx(project.engine) as conn:
        plan, scan = (rs.raster_info(conn, i) for i in ctx.result["objects"])
    assert plan["elevation"] == 245.5 and [p[2] for p in plan["georef"]["corners3d"]] == [245.5] * 4
    assert plan["extent"][2] == plan["extent"][5] == 245.5
    moved = np.array(scan["georef"]["corners3d"])
    assert np.abs(moved - (SCAN + [0.0, 0.0, -5.0])).max() <= 1e-9 and scan["elevation"] is None
    assert any("Сдвиг по Z: −5 м" in line for line in ctx.lines)
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"files": paths(files, *names), "raster_z": {"shift": "пять"}})
    assert err.value.code == "not_a_number" and "Сдвиг растров по Z" in err.value.message


def test_image_without_georef_stands_on_the_section(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.importing.object_files import inspect_files
    from bf_core.rasters import store as rs

    image = tmp_path / "section.png"
    Image.fromarray(picture(50, 25)).save(image)
    out = inspect_files([str(image)])  # no georeference and no section line: it will not load, and why
    assert not out["files"][0]["ok"] and out["files"][0]["error"]["code"] == "no_georef"
    assert "линию разреза" in out["files"][0]["error"]["hint"]
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"files": [str(image)]})
    assert err.value.code == "no_georef" and "section.png" in err.value.message
    assert state_of(project.engine, project.folder)["objects"] == []

    section = {"x1": 1000.0, "y1": 5000.0, "x2": 1300.0, "y2": 5400.0, "z_top": 600.0, "z_bottom": 350.0}
    seen = inspect_files([str(image)], section=section)["files"][0]
    assert seen["ok"] and seen["rasters"][0]["vertical"] and seen["rasters"][0]["georef"]["source"] == "section"
    ctx = run_job(project.folder, {"files": [str(image)], "section": section})
    with dialect.read_tx(project.engine) as conn:
        info = rs.raster_info(conn, ctx.result["objects"][0])
    assert info["vertical"] and np.abs(np.array(info["georef"]["corners3d"]) - SCAN).max() <= 1e-9
    assert any("плоскость линии разреза" in w for w in info["warnings"])
    assert any("плоскость линии разреза" in line for line in ctx.lines)


def test_cancel_at_any_step_leaves_nothing(project, files: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """ТЗ 7.8: «Отмена до последнего шага не оставляет объекта в дереве» — nor rows, file references or
    temporary files; what was in the project before stays as it was."""
    from bf_core.db.schema import blobs
    from bf_core.storage.blobs import BlobStore
    from bf_core.storage.gc import collect

    monkeypatch.setattr("bf_core.importing.objects.PORTION", 1)  # a transaction per layer: a cancel between them
    names = ("contours.str", "points.csv", "map.jpg", "map.jgw", "scan.png", "scan.grf")
    run_job(project.folder, {"files": paths(files, "plan.tif", "plan.tfw")})  # something already in the project
    params = {"files": paths(files, *names), "raster_z": {"shift": -5}}
    was = state_of(project.engine, project.folder)
    with dialect.read_tx(project.engine) as conn:
        kept = set(conn.execute(sa.select(blobs.c.sha256)).scalars())
    ticks = 0
    for at in range(1, 400):
        try:
            ctx = run_job(project.folder, params, cancel_at=at)
        except JobCancelled:
            ticks = at
            assert state_of(project.engine, project.folder) == was, f"cancelled at tick {at}"
            continue
        break
    else:
        raise AssertionError("the job never finished")
    assert ticks >= 16, ticks  # reading, every layer, display copies, source files: each stage can be cancelled
    now = state_of(project.engine, project.folder)
    assert len(ctx.result["objects"]) == 4 and len(now["objects"]) == len(was["objects"]) + 7
    assert all(state == "ready" for _kind, _name, state in now["objects"]) and now["tmp"] == []
    # the files of a cancelled attempt have no owner: the storage cleanup removes them, and only them
    with pytest.raises(JobCancelled):
        run_job(project.folder, {"files": paths(files, "bad.str", *names)}, cancel_at=ticks)
    with dialect.read_tx(project.engine) as conn:
        owned = set(conn.execute(sa.select(blob_owners.c.sha256)).scalars())
    assert kept <= owned
    collect(project.engine, BlobStore(project.folder))
    with dialect.read_tx(project.engine) as conn:
        assert set(conn.execute(sa.select(blobs.c.sha256)).scalars()) == owned
    assert all(BlobStore(project.folder).exists(sha) for sha in owned)


def test_errors_are_told_in_russian_and_leave_nothing(project, files: dict[str, Path], tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    was = state_of(project.engine, project.folder)
    with pytest.raises(JobError) as err:  # nothing to load at all
        run_job(project.folder, {"files": paths(files, "lonely.tfw", "model.obj")})
    assert err.value.code == "nothing_to_import" and ".str" in err.value.hint
    with pytest.raises(JobError) as err:  # every file failed: the first reason is the error of the job
        run_job(project.folder, {"files": paths(files, "bad.str")})
    assert err.value.code == "unsupported" and "bad.str" in err.value.message and err.value.hint
    bomb = tmp_path / "bomb.png"  # 401 megapixels in the header: refused before anything is decoded
    from tests.formats.test_raster_image import png_header

    bomb.write_bytes(png_header(20_050, 20_000))
    bomb.with_suffix(".pgw").write_text("1\n0\n0\n-1\n0\n0\n")
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"files": [str(bomb)]})
    assert err.value.code == "too_large"
    gone = tmp_path / "нет такого.dxf"
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"files": [str(gone)]})
    assert err.value.code == "file_unreadable" and "нет такого.dxf" in err.value.message
    assert state_of(project.engine, project.folder) == was


def test_a_retry_after_the_commit_changes_nothing(project, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    """The worker died after the final transaction: the second attempt finds the import done."""
    params = {"files": paths(files, "contours.str", "map.jpg", "map.jgw", "bad.str", "model.obj")}
    first = run_job(project.folder, params, job_id=7)
    was = state_of(project.engine, project.folder)
    second = run_job(project.folder, params, job_id=7)
    assert second.result["files"] == first.result["files"] and second.result["objects"] == first.result["objects"]
    assert [f["file"] for f in second.result["failed"]] == ["bad.str"] and second.result["rest"] == first.result["rest"]
    assert state_of(project.engine, project.folder) == was
    assert any("уже записаны" in line for line in second.lines)


def test_same_file_twice_gets_a_numbered_name(project, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    for _ in range(3):
        run_job(project.folder, {"files": paths(files, "contours.str", "map.jpg", "map.jgw")})
    with dialect.read_tx(project.engine) as conn:
        names = conn.execute(
            sa.select(objects.c.name).where(objects.c.parent_id.is_(None)).order_by(objects.c.id)
        ).scalars()
        assert list(names) == ["contours.str", "map", "contours.str (2)", "map (2)", "contours.str (3)", "map (3)"]
        assert conn.execute(sa.select(sa.func.count()).select_from(blob_owners)).scalar_one() > 0


def test_companions_pair_within_their_folder(files: dict[str, Path], tmp_path: Path) -> None:
    """`map.jgw` of another folder is not the georeference of this `map.jpg`; a chosen file beats a found one."""
    from bf_core.importing.object_files import inspect_files, plan

    other = tmp_path / "другая"
    other.mkdir()
    (other / "map.jgw").write_text("9\n0\n0\n-9\n0\n0\n")
    groups, rest = plan([str(files["map.jpg"]), str(other / "map.jgw"), str(files["map.jpg"])])
    assert [g.main.name for g in groups] == ["map.jpg"] and groups[0].sidecars == {}
    assert [r["path"] for r in rest] == [str(other / "map.jgw")]
    # the image alone: its world file is found next to it
    seen = inspect_files([str(files["map.jpg"])])["files"][0]
    assert seen["ok"] and seen["sidecars"] == ["map.jgw"] and seen["rasters"][0]["georef"]["pixel"] == 0.5


# --- through the API and the scheduler -------------------------------------------------------------------


def test_wizard_through_the_api(pclient, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    """inspect → start → the job in a worker process (the handler is registered by an explicit import) → the
    tree of lines and rasters."""
    assert pclient.post(f"{API}/import/objects/inspect", json={"files": paths(files)}).status_code == 409  # no project
    pclient.post(f"{API}/projects", json={"name": "Объекты"})
    body = {"files": paths(files), "raster_z": {"elevation": "250,5", "shift": ""}}
    r = pclient.post(f"{API}/import/objects/inspect", json=body)
    assert r.status_code == 200, r.text
    seen = r.json()
    assert seen["totals"]["files"] == 5 and seen["totals"]["rasters"] == 3 and len(seen["rest"]) == 2
    assert seen["raster_z"] == {"applies": True, "elevation": 250.5, "shift": 0.0} and seen["section"] is None

    bad = pclient.post(f"{API}/import/objects", json={**body, "raster_z": {"shift": "вниз"}})
    assert bad.status_code == 422 and bad.json()["code"] == "not_a_number" and bad.json()["hint"]
    missing = pclient.post(f"{API}/import/objects", json={"files": [str(files["map.jpg"]) + ".нет"]})
    assert missing.status_code == 404 and missing.json()["code"] == "file_not_found"
    nothing = pclient.post(f"{API}/import/objects", json={"files": paths(files, "lonely.tfw", "model.obj")})
    assert nothing.status_code == 422 and nothing.json()["code"] == "nothing_to_import"
    assert [x["file"] for x in nothing.json()["rest"]] == ["lonely.tfw", "model.obj"]

    r = pclient.post(f"{API}/import/objects", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["title"].startswith("Импорт файлов: contours.str"), job
    assert job["result"]["rasters"] == 3 and job["result"]["lines"] == 3 and len(job["result"]["failed"]) == 1
    log = str(pclient.get(f"{API}/jobs/{r.json()['source']}/{r.json()['id']}/log").json())
    assert "contours.str: слоёв — 2" in log and "bad.str" in log

    tree = pclient.get(f"{API}/lines/files").json()["files"]
    assert [(f["name"], [x["name"] for x in f["layers"]]) for f in tree] == [
        ("contours.str", ["100", "200"]),
        ("points.csv", ["points"]),
    ]
    shown = pclient.get(f"{API}/rasters").json()["rasters"]
    assert [(x["name"], x["vertical"], x["elevation"]) for x in shown] == [
        ("map", False, 250.5),
        ("scan", True, None),
        ("plan", False, 250.5),
    ]
    layer = tree[0]["layers"][0]
    assert pclient.get(f"{API}/viz/lines/{layer['id']}").status_code == 200  # the 3D manifest of an imported layer


def test_imported_objects_survive_bfggis(pclient, files: dict[str, Path], tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """What the job wrote — layers, rasters with their copies and sidecars, the import history — comes back from
    a `.bfggis` with new ids: the same tree, the same geometry and pictures (ТЗ 7.4)."""
    pclient.post(f"{API}/projects", json={"name": "Объекты"})
    r = pclient.post(f"{API}/import/objects", json={"files": paths(files, "contours.str", "scan.png", "scan.grf")})
    assert wait_job(pclient, r.json()["source"], r.json()["id"])["status"] == "done"

    def tree() -> tuple[list[Any], list[Any], str, bytes]:
        lines = pclient.get(f"{API}/lines/files").json()["files"]
        shown = pclient.get(f"{API}/rasters").json()["rasters"]
        image = pclient.get(f"{API}/rasters/{shown[0]['id']}/image")
        part = pclient.get(f"{API}/viz/lines/{lines[0]['layers'][0]['id']}/parts/0")
        assert image.status_code == 200 and part.status_code == 200
        plain = [
            (f["name"], f["format"], [(x["name"], x["line_count"], x["point_count"], x["length"]) for x in f["layers"]])
            for f in lines
        ]
        georefs = [(x["name"], x["georef"], x["vertical"], sorted(x["images"].items())) for x in shown]
        return plain, georefs, image.headers["ETag"], part.content

    before = tree()
    r = pclient.post(f"{API}/projects/export", json={"path": str(tmp_path / "objects.bfggis"), "password": None})
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    r = pclient.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    pclient.post(f"{API}/projects/{job['result']['uid']}/open")
    assert tree() == before
    project = projects_of(pclient.core).require_current()
    with dialect.read_tx(project.engine) as conn:
        log = conn.execute(sa.select(import_log.c.file_name, import_log.c.target).order_by(import_log.c.id)).all()
        assert [tuple(row) for row in log] == [("contours.str", "objects"), ("scan.png", "objects")]
        scan = conn.execute(sa.select(objects.c.id).where(objects.c.kind == "raster")).scalar_one()
        assert set(roles_of(conn, scan)) == {"source", "sidecar:grf", "display0", "display1"}
