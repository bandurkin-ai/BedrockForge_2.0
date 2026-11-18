"""Export of rasters (ADR 0011 §3 `export.raster`): the source file byte for byte with a world file that puts
every corner where the project has it (≤ 1e−9 m) and a `.prj`; a GeoTIFF with the same pixels, the transform bit
for bit and the level; a vertical raster keeps its own georeference files and is refused as a GeoTIFF; a cancel
leaves no file; the job runs in a worker and writes the journal and `export_log`."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa
from PIL import Image

from bf_core.api.errors import ApiError
from bf_core.crs import describe
from bf_core.db import dialect
from bf_core.db.schema import export_log, objects, rasters
from bf_core.formats import georef as gr
from bf_core.formats.raster_image import read_raster
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import load_handlers
from bf_core.projects.service import projects_of
from bf_core.rasters import export as re_
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

TOL = 1e-9
X0, Y0 = 466570.58600000001, 4584699.697000001
W, H = 700, 500
ARRAY_MEDIA = "application/octet-stream"


def _picture(width: int = W, height: int = H, seed: int = 5) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (height, width, 3), dtype=np.uint8)


def _world(pixel: float = 2.02, degrees: float = 0.2) -> str:
    """A world file of a slightly turned image: A, D, B, E and the centre of the top-left pixel."""
    t = math.radians(degrees)
    values = (pixel * math.cos(t), pixel * math.sin(t), pixel * math.sin(t), -pixel * math.cos(t), X0, Y0)
    return "".join(f"{v!r}\n" for v in values)


class EveryTick(JobContext):
    """The context of a job that sends every tick (the real one sends five a second), so a test can ask for a
    cancel at a given progress. The cancel itself works as in a worker: the next tick raises."""

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        if self._cancelled.is_set():
            raise JobCancelled()
        self._send(("progress", self.id, progress, message))


class Project:
    """A project of the test core and what `import.objects` (7.12) does with an image: the raster object, its
    row with the georeference as `georef.describe` gives it, the `source` and `sidecar:<ext>` blobs."""

    def __init__(self, pclient: Any) -> None:
        r = pclient.post(f"{API}/projects", json={"name": "Растры"})
        assert r.status_code == 200, r.text
        self.client = pclient
        self.project = projects_of(pclient.core).require_current()
        self.store = BlobStore(self.project.folder)
        self.job = 0

    def load(self, image: Path, *, georef: bool = True) -> int:
        read = read_raster(image, require_georef=georef, displays=False)
        g = read.georef
        now = dialect.utcnow()
        with dialect.write_tx(self.project.engine) as conn:
            values = dict(kind="raster", name=read.name, name_key=dialect.name_key(read.name), sort_key=0.0,
                          state="ready", version=1, meta={}, created_at=now, updated_at=now)  # fmt: skip
            raster_id = int(conn.execute(sa.insert(objects).values(**values)).inserted_primary_key[0])
            conn.execute(
                sa.insert(rasters).values(
                    object_id=raster_id,
                    width=read.width,
                    height=read.height,
                    georef=None if g is None else gr.describe(g, read.width, read.height, read.checks),
                    elevation=None if g is None else g.elevation,
                    vertical=g is not None and gr.is_vertical(g, read.width, read.height),
                    opacity=1.0,
                    version=1,
                )
            )
            owner = {"owner_kind": "object", "owner_id": raster_id, "media_type": ARRAY_MEDIA}
            self.store.put_file(conn, image, role="source", **owner)
            for suffix, path in read.sidecars.items():
                self.store.put_file(conn, path, role="sidecar:" + suffix.lstrip("."), **owner)
        return raster_id

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
            out = load_handlers()["export.raster"].run(ctx)
        finally:
            ctx.close()
        assert out is not None
        return out, sent

    def set(self, raster_id: int, **values: Any) -> None:
        with dialect.write_tx(self.project.engine) as conn:
            conn.execute(sa.update(rasters).where(rasters.c.object_id == raster_id).values(**values))

    def georef(self, raster_id: int) -> dict[str, Any]:
        with dialect.read_tx(self.project.engine) as conn:
            return dict(conn.execute(sa.select(rasters.c.georef).where(rasters.c.object_id == raster_id)).scalar())

    def log_rows(self) -> list[dict[str, Any]]:
        with dialect.read_tx(self.project.engine) as conn:
            return [dict(r) for r in conn.execute(sa.select(export_log).order_by(export_log.c.id)).mappings()]


@pytest.fixture()
def proj(pclient):  # type: ignore[no-untyped-def]
    return Project(pclient)


def _logs(sent: list[tuple[Any, ...]], level: str | None = None) -> list[str]:
    return [m[3] for m in sent if m[0] == "log" and (level is None or m[2] == level)]


def _ortho(folder: Path, suffix: str = ".jpg", world: str = ".jgw", prj: bool = True) -> Path:
    image = folder / f"ортофото{suffix}"
    Image.fromarray(_picture()).save(image)
    image.with_suffix(world).write_text(_world(), encoding="ascii")
    if prj:
        image.with_suffix(".prj").write_text(describe("EPSG:32638").wkt, encoding="utf-8")
    return image


# --- the source file -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("suffix", "world"), [(".jpg", ".jgw"), (".png", ".pgw"), (".tif", ".tfw"), (".bmp", ".bpw")])
def test_source_with_a_world_file_comes_back_where_it_was(
    proj: Project, tmp_path: Path, suffix: str, world: str
) -> None:
    src = tmp_path / "in"
    src.mkdir()
    image = _ortho(src, suffix, world)
    raster_id = proj.load(image)
    stored = proj.georef(raster_id)
    out, sent = proj.run(raster_id=raster_id, path=str(tmp_path / "out" / "копия"), format="source")
    target = Path(out["path"])
    assert target == tmp_path / "out" / f"копия{suffix}" and target.read_bytes() == image.read_bytes()
    assert sorted(out["files"]) == sorted([f"копия{suffix}", f"копия{world}", "копия.prj"]) and out["warnings"] == []
    assert sorted(p.name for p in target.parent.iterdir()) == sorted(out["files"])  # no .part, nothing else
    assert out["size"] == sum(p.stat().st_size for p in target.parent.iterdir())
    back = read_raster(target, displays=False)
    assert back.georef is not None and back.georef.source == gr.WORLD and (back.width, back.height) == (W, H)
    corners = np.array(stored["corners3d"], dtype=float)[:, :2]
    assert np.abs(gr.corners3d(back.georef, W, H)[:, :2] - corners).max() <= TOL
    assert np.abs(np.array(back.georef.affine) - np.array(stored["affine"])).max() <= TOL
    assert back.georef.crs is not None and back.georef.crs.epsg == 32638
    assert "записан (исходный файл, 700 × 500 пикселей)" in _logs(sent)[-1] and not _logs(sent, "warn")
    row = proj.log_rows()[-1]
    assert (row["object_id"], row["file_name"], row["format"]) == (raster_id, f"копия{suffix}", "source")
    assert row["counts"] == {"width": W, "height": H, "files": 3, "size": out["size"], "warnings": 0}
    # the extension of the image kind is kept when it is already there, in any case
    again, _ = proj.run(raster_id=raster_id, path=str(tmp_path / f"Снимок{suffix.upper()}"), format="source")
    assert Path(again["path"]).name == f"Снимок{suffix.upper()}"
    assert (tmp_path / f"Снимок{world}").read_text() == target.with_suffix(world).read_text()


def test_world_file_follows_the_project_not_the_loaded_file(proj: Project, tmp_path: Path) -> None:
    image = _ortho(tmp_path, prj=False)
    raster_id = proj.load(image)
    stored = proj.georef(raster_id)
    moved = dict(stored, affine=[*stored["affine"][:2], stored["affine"][2] + 1000.25, *stored["affine"][3:]])
    proj.set(raster_id, georef=moved, elevation=412.5)
    out, sent = proj.run(raster_id=raster_id, path=str(tmp_path / "o" / "moved.jpg"), format="source")
    assert sorted(out["files"]) == ["moved.jgw", "moved.jpg"]  # no CRS: no .prj
    back = read_raster(Path(out["path"]), displays=False).georef
    assert back is not None and np.abs(np.array(back.affine) - np.array(moved["affine"])).max() <= TOL
    assert back.elevation is None  # a world file has no level: the journal says where to keep it
    assert any("412,5 м" in line and "GeoTIFF" in line for line in _logs(sent)) and out["warnings"] == []


def test_raster_without_a_georeference_is_its_source_only(proj: Project, tmp_path: Path) -> None:
    image = tmp_path / "скан.png"
    Image.fromarray(_picture(64, 48)).save(image)
    raster_id = proj.load(image, georef=False)
    out, sent = proj.run(raster_id=raster_id, path=str(tmp_path / "o" / "скан"), format="source")
    assert out["files"] == ["скан.png"] and Path(out["path"]).read_bytes() == image.read_bytes()
    assert "нет привязки" in out["warnings"][0] and _logs(sent, "warn") == out["warnings"]
    with pytest.raises(JobError) as err:
        proj.run(raster_id=raster_id, path=str(tmp_path / "o" / "скан.tif"), format="geotiff")
    assert err.value.code == "no_georef" and "исходный файл" in err.value.hint
    assert [p.name for p in (tmp_path / "o").iterdir()] == ["скан.png"]


def test_vertical_raster_keeps_its_own_georeference_files(proj: Project, tmp_path: Path) -> None:
    image = tmp_path / "разрез.png"
    Image.fromarray(_picture(400, 200)).save(image)
    corners = [(0.0, 0.0, 1000.0, 5000.0, 600.0), (400.0, 0.0, 1300.0, 5400.0, 600.0),
               (400.0, 200.0, 1300.0, 5400.0, 350.0), (0.0, 200.0, 1000.0, 5000.0, 350.0)]  # fmt: skip
    head = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    grf = "\n".join([*(repr(v) for v in head), *(" ".join(repr(v) for v in (*p, 1.0)) for p in corners)]) + "\n"
    image.with_suffix(".grf").write_text(grf, encoding="ascii")
    raster_id = proj.load(image)
    assert proj.georef(raster_id)["vertical"] is True
    out, sent = proj.run(raster_id=raster_id, path=str(tmp_path / "o" / "section"), format="source")
    assert sorted(out["files"]) == ["section.grf", "section.png"]
    assert (tmp_path / "o" / "section.grf").read_text() == grf and Path(out["path"]).read_bytes() == image.read_bytes()
    assert ".grf" in out["warnings"][0] and "сдвиг" in out["warnings"][0] and _logs(sent, "warn") == out["warnings"]
    back = read_raster(Path(out["path"]), displays=False).georef
    assert back is not None and np.abs(gr.corners3d(back, 400, 200) - np.array([c[2:] for c in corners])).max() <= TOL
    with pytest.raises(JobError) as err:
        proj.run(raster_id=raster_id, path=str(tmp_path / "o" / "section.tif"), format="geotiff")
    assert err.value.code == "unrepresentable" and "исходный файл" in err.value.hint.lower()
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == ["section.grf", "section.png"]


# --- GeoTIFF -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_geotiff_keeps_the_pixels_the_transform_and_the_level(proj: Project, tmp_path: Path, suffix: str) -> None:
    import tifffile

    src = tmp_path / "in"
    src.mkdir()
    image = _ortho(src, suffix, {".png": ".pgw", ".jpg": ".jgw"}[suffix])
    raster_id = proj.load(image)
    proj.set(raster_id, elevation=-35.75)  # what POST /rasters/{id}/shift leaves in the row
    stored = proj.georef(raster_id)
    out, sent = proj.run(raster_id=raster_id, path=str(tmp_path / "o" / "карта"), format="geotiff")
    target = Path(out["path"])
    assert target.name == "карта.tif" and out["files"] == ["карта.tif"] and out["warnings"] == []
    assert [p.name for p in target.parent.iterdir()] == ["карта.tif"]
    assert np.array_equal(tifffile.imread(target), np.asarray(Image.open(image)))  # nothing is lost
    back = gr.read_geotiff_tags(target)
    assert back is not None and list(back.affine) == stored["affine"]  # bit for bit
    assert back.elevation == -35.75 and back.crs is not None and back.crs.epsg == 32638
    corners = np.array(stored["corners3d"], dtype=float)[:, :2]
    assert np.abs(gr.corners3d(back, W, H)[:, :2] - corners).max() <= TOL
    seen = [m[2] for m in sent if m[0] == "progress" and m[2] is not None]
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0
    assert proj.log_rows()[-1]["format"] == "geotiff"
    # the GeoTIFF loaded again and exported as its source: the same file
    again = proj.load(target)
    copy, _ = proj.run(raster_id=again, path=str(tmp_path / "o2" / "копия.tiff"), format="source")
    assert Path(copy["path"]).name == "копия.tiff" and Path(copy["path"]).read_bytes() == target.read_bytes()


# --- failures ------------------------------------------------------------------------------------------


def test_errors_are_russian_and_leave_no_file(proj: Project, tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    raster_id = proj.load(_ortho(tmp_path / "in"))
    out_dir = tmp_path / "o"
    out_dir.mkdir()
    (out_dir / "занято.jpg").mkdir()
    cases: list[tuple[dict[str, Any], str]] = [
        ({"raster_id": 999_999, "path": str(out_dir / "x"), "format": "source"}, "raster_not_found"),
        ({"raster_id": raster_id, "path": str(out_dir / "x"), "format": "ecw"}, "bad_format"),
        ({"raster_id": raster_id, "path": str(out_dir / "занято"), "format": "source"}, "file_unwritable"),
    ]
    for params, code in cases:
        with pytest.raises(JobError) as err:
            proj.run(**params)
        e = err.value
        assert e.code == code and e.message and e.hint and any("а" <= ch <= "я" for ch in e.message), params
    tall = tmp_path / "in" / "tall.png"  # six rows of GeoTIFF tiles, three blocks of the source file
    Image.fromarray(_picture(300, 2600)).save(tall)
    tall.with_suffix(".pgw").write_text(_world(), encoding="ascii")
    tall_id = proj.load(tall)
    for fmt in ("source", "geotiff"):
        with pytest.raises(JobCancelled):
            proj.run(cancel_at=0.3, raster_id=tall_id, path=str(out_dir / "отмена"), format=fmt)
    assert [p.name for p in out_dir.iterdir()] == ["занято.jpg"] and proj.log_rows() == []
    # a cancel that comes when the files are already in place does not undo the export
    for fmt, files in (("source", ["поздно.pgw", "поздно.png"]), ("geotiff", ["поздно.tif"])):
        late, _ = proj.run(cancel_at=1.0, raster_id=tall_id, path=str(out_dir / "поздно"), format=fmt)
        assert sorted(late["files"]) == files and all((out_dir / name).is_file() for name in files)
    assert len(proj.log_rows()) == 2 and not list(out_dir.glob("*.part"))
    for name in ("поздно.pgw", "поздно.png", "поздно.tif"):
        (out_dir / name).unlink()
    with dialect.write_tx(proj.project.engine) as conn:  # an import still in progress is not exported
        conn.execute(sa.update(objects).where(objects.c.id == raster_id).values(state="building"))
    with pytest.raises(JobError) as err:
        proj.run(raster_id=raster_id, path=str(out_dir / "x"), format="source")
    assert err.value.code == "raster_not_found"


def test_request_checks_the_parameters(proj: Project, tmp_path: Path) -> None:
    raster_id = proj.load(_ortho(tmp_path))
    path = str(tmp_path / "out.tif")
    with dialect.read_tx(proj.project.engine) as conn:
        ok = re_.request(conn, raster_id=raster_id, path=path, format="geotiff")
        assert ok == {"raster_id": raster_id, "path": path, "format": "geotiff", "width": W, "height": H}
        assert re_._memory(ok) == 512 and re_._memory({**ok, "width": 20_000, "height": 20_000}) > 1500
        assert re_._memory({**ok, "format": "source", "width": 20_000, "height": 20_000}) == 256
        for kw, status, code in (
            ({"raster_id": raster_id, "path": path, "format": "ecw"}, 422, "bad_format"),
            ({"raster_id": raster_id, "path": "out.tif", "format": "source"}, 422, "path_not_absolute"),
            (
                {"raster_id": raster_id, "path": str(tmp_path / "нет" / "o.tif"), "format": "source"},
                404,
                "folder_not_found",
            ),
            ({"raster_id": 999_999, "path": path, "format": "source"}, 404, "raster_not_found"),
        ):
            with pytest.raises(ApiError) as err:
                re_.request(conn, **kw)  # type: ignore[arg-type]
            assert (err.value.status, err.value.code) == (status, code) and err.value.hint, kw


# --- the job in a worker -----------------------------------------------------------------------------


def test_job_runs_in_a_worker_with_journal_and_log(proj: Project, tmp_path: Path) -> None:
    from bf_core.jobs.service import jobs_of

    image = _ortho(tmp_path)
    raster_id = proj.load(image)
    c = proj.client
    (tmp_path / "o").mkdir()
    with dialect.read_tx(proj.project.engine) as conn:
        params = re_.request(conn, raster_id=raster_id, path=str(tmp_path / "o" / "из задания"), format="source")
    key, job_id = jobs_of(c.core).submit("export.raster", params, title="Экспорт растра")
    job = wait_job(c, key, job_id)
    assert job["status"] == "done", job
    assert Path(job["result"]["path"]).read_bytes() == image.read_bytes()
    assert sorted(job["result"]["files"]) == ["из задания.jgw", "из задания.jpg", "из задания.prj"]
    log = c.get(f"{API}/jobs/{key}/{job_id}/log").json()
    assert any("Растр «ортофото» записан" in row["text"] for row in log["lines"]), log
    rows = proj.log_rows()
    assert len(rows) == 1 and rows[0]["job_id"] == job_id and rows[0]["object_id"] == raster_id
