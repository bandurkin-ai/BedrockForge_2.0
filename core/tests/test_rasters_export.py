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
        assert ok == {"raster_id": raster_id, "path": path, "format": "geotiff", "overwrite": False,
                      "width": W, "height": H}  # fmt: skip
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


# --- files that are there already (Э7 review) ----------------------------------------------------------


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_files_next_to_the_chosen_one_are_not_replaced_unasked(proj: Project, tmp_path: Path) -> None:
    """The save window asks about the chosen name only. The companions and an image that got a second
    extension are other files: without `overwrite` nothing is written and they are named."""
    (tmp_path / "in").mkdir()
    raster_id = proj.load(_ortho(tmp_path / "in"))
    image = (tmp_path / "in" / "ортофото.jpg").read_bytes()
    out = tmp_path / "o"
    out.mkdir()
    theirs = {"карта.jgw": "чужая привязка\n", "карта.prj": "чужая система координат\n"}
    for name, text in theirs.items():
        (out / name).write_text(text, encoding="utf-8")
    chosen = str(out / "карта.jpg")

    def untouched() -> bool:
        return all((out / name).read_text(encoding="utf-8") == text for name, text in theirs.items())

    with pytest.raises(JobError) as err:
        proj.run(raster_id=raster_id, path=chosen, format="source")
    e = err.value
    assert e.code == "files_exist" and "«карта.jgw», «карта.prj»" in e.message and "карта.jpg" not in e.message
    assert "Подтвердите замену" in e.hint and untouched()
    assert _names(out) == sorted(theirs) and proj.log_rows() == []  # no image, no .part, no record
    with dialect.read_tx(proj.project.engine) as conn:  # the endpoint tells before the job is started
        with pytest.raises(ApiError) as refused:
            re_.request(conn, raster_id=raster_id, path=chosen, format="source", store=proj.store)
        assert (refused.value.status, refused.value.code) == (409, "files_exist")
        assert refused.value.extra == {"files": ["карта.jgw", "карта.prj"]} and refused.value.hint
        agreed = re_.request(conn, raster_id=raster_id, path=chosen, format="source", overwrite=True, store=proj.store)
    assert agreed["overwrite"] is True and untouched()

    # the chosen file itself was asked about by the save window: it is replaced, a folder is not a file to ask about
    for name in theirs:
        (out / name).unlink()
    (out / "карта.jpg").write_bytes(b"old")
    (out / "карта.jgw").mkdir()
    with dialect.read_tx(proj.project.engine) as conn:
        re_.request(conn, raster_id=raster_id, path=chosen, format="source", store=proj.store)
    with pytest.raises(JobError) as err:
        proj.run(raster_id=raster_id, path=chosen, format="source")
    assert err.value.code == "file_unwritable" and (out / "карта.jpg").read_bytes() == b"old"
    (out / "карта.jgw").rmdir()
    done, _ = proj.run(raster_id=raster_id, path=chosen, format="source")
    assert (
        sorted(done["files"]) == ["карта.jgw", "карта.jpg", "карта.prj"] and (out / "карта.jpg").read_bytes() == image
    )

    # with the permission the companions are replaced
    (out / "карта.jgw").write_text("чужая привязка\n", encoding="utf-8")
    done, _ = proj.run(raster_id=raster_id, path=chosen, format="source", overwrite=True)
    assert sorted(done["files"]) == ["карта.jgw", "карта.jpg", "карта.prj"]
    assert read_raster(out / "карта.jpg", displays=False).georef is not None  # a real world file again

    # a second extension: «plan.tif» chosen for a JPEG is written as «plan.tif.jpg» — a name nobody asked about
    (out / "plan.tif").write_bytes(b"their tiff")
    (out / "plan.tif.jpg").write_bytes(b"their jpeg")
    with pytest.raises(JobError) as err:
        proj.run(raster_id=raster_id, path=str(out / "plan.tif"), format="source")
    assert err.value.code == "files_exist" and "«plan.tif.jpg»" in err.value.message
    assert "«plan.tif»" not in err.value.message and (out / "plan.tif.jpg").read_bytes() == b"their jpeg"
    done, _ = proj.run(raster_id=raster_id, path=str(out / "plan.tif"), format="source", overwrite=True)
    assert Path(done["path"]).name == "plan.tif.jpg" and Path(done["path"]).read_bytes() == image
    assert (out / "plan.tif").read_bytes() == b"their tiff"  # the chosen name is not what is written: it stays

    # GeoTIFF: «dem» becomes «dem.tif»; «dem.tif» chosen as it is was asked about
    (out / "dem.tif").write_bytes(b"their dem")
    with pytest.raises(JobError) as err:
        proj.run(raster_id=raster_id, path=str(out / "dem"), format="geotiff")
    assert err.value.code == "files_exist" and "«dem.tif»" in err.value.message
    assert (out / "dem.tif").read_bytes() == b"their dem"
    with dialect.read_tx(proj.project.engine) as conn, pytest.raises(ApiError) as refused:
        re_.request(conn, raster_id=raster_id, path=str(out / "dem"), format="geotiff", store=proj.store)
    assert refused.value.extra == {"files": ["dem.tif"]}
    done, _ = proj.run(raster_id=raster_id, path=str(out / "dem.tif"), format="geotiff")
    assert done["files"] == ["dem.tif"] and gr.read_geotiff_tags(out / "dem.tif") is not None
    assert not list(out.glob("*.part"))


def test_export_endpoint_asks_before_replacing(proj: Project, tmp_path: Path) -> None:
    raster_id = proj.load(_ortho(tmp_path))
    out = tmp_path / "o"
    out.mkdir()
    (out / "снимок.prj").write_text("чужой файл", encoding="utf-8")
    c = proj.client
    body = {"path": str(out / "снимок.jpg"), "format": "source"}
    r = c.post(f"{API}/rasters/{raster_id}/export", json=body)
    assert r.status_code == 409 and r.json()["code"] == "files_exist" and r.json()["files"] == ["снимок.prj"]
    assert "«снимок.prj»" in r.json()["message"] and r.json()["hint"]
    assert _names(out) == ["снимок.prj"] and (out / "снимок.prj").read_text(encoding="utf-8") == "чужой файл"
    r = c.post(f"{API}/rasters/{raster_id}/export", json={**body, "overwrite": True})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and sorted(job["result"]["files"]) == ["снимок.jgw", "снимок.jpg", "снимок.prj"]
    assert describe((out / "снимок.prj").read_text(encoding="utf-8")).epsg == 32638
    # a job started without the permission (not through the endpoint) refuses on its own
    from bf_core.jobs.service import jobs_of

    key, job_id = jobs_of(c.core).submit("export.raster", {"raster_id": raster_id, **body}, title="Экспорт растра")
    job = wait_job(c, key, job_id)
    assert job["status"] == "failed" and job["error"]["code"] == "files_exist", job


TAB = (
    "!table\r\n!version 300\r\n!charset WindowsCyrillic\r\n\r\nDefinition Table\r\n"
    '  File "C:\\Съёмка\\Разрез 12.png"\r\n  Type "RASTER"\r\n'
    '  (1000.0,5000.0) (0.0,0.0) Label "Левый верх",\r\n  (1400.0,5000.0) (400.0,0.0) Label "Правый верх",\r\n'
    '  (1400.0,4800.0) (400.0,200.0) Label "Правый низ"\r\n  Units "m"\r\n'
)


def test_tab_of_a_vertical_raster_names_the_exported_image(proj: Project, tmp_path: Path) -> None:
    """A raster loaded with a TAB and then put on a section is exported with that TAB (a world file cannot hold
    a vertical plane). The TAB named the image it was made for: now it names the exported one."""
    (tmp_path / "in").mkdir()
    image = tmp_path / "in" / "Разрез 12.png"
    Image.fromarray(_picture(400, 200)).save(image)
    image.with_suffix(".tab").write_bytes(TAB.encode("cp1251"))
    raster_id = proj.load(image)
    r = proj.client.get(f"{API}/rasters/{raster_id}").json()
    edges = {"x1": 1000.0, "y1": 5000.0, "x2": 1300.0, "y2": 5400.0, "z_top": 600.0, "z_bottom": 350.0}
    r = proj.client.patch(f"{API}/rasters/{raster_id}", json={"expected_version": r["version"], "vertical": edges})
    assert r.status_code == 200 and r.json()["vertical"] is True, r.text

    out, sent = proj.run(raster_id=raster_id, path=str(tmp_path / "o" / "Профиль 5"), format="source")
    assert sorted(out["files"]) == ["Профиль 5.png", "Профиль 5.tab"]
    written = (tmp_path / "o" / "Профиль 5.tab").read_bytes()
    assert written == TAB.replace("C:\\Съёмка\\Разрез 12.png", "Профиль 5.png").encode("cp1251")  # nothing else moved
    assert len(out["warnings"]) == 1 and ".tab" in out["warnings"][0] and _logs(sent, "warn") == out["warnings"]
    back = read_raster(tmp_path / "o" / "Профиль 5.png", displays=False)  # the pair reads as a pair again
    assert back.georef is not None and back.georef.source == gr.TAB

    # the code pages of a TAB and what cannot be done
    name = "Разрез 7.png"
    for codec, charset in (("cp1251", "WindowsCyrillic"), ("utf-8", "Neutral"), ("utf-8-sig", "UTF-8"),
                           ("utf-16", "WindowsCyrillic"), ("utf-16-be", "WindowsCyrillic")):  # fmt: skip
        text = TAB.replace("WindowsCyrillic", charset)
        raw = (b"\xfe\xff" if codec == "utf-16-be" else b"") + text.encode(codec)
        got, done = re_.retarget_tab(raw, name)
        assert done and got.decode(codec.replace("-be", "")) == text.replace("C:\\Съёмка\\Разрез 12.png", name), codec
    western = TAB.replace("WindowsCyrillic", "WindowsLatin1").replace("Съёмка\\Разрез 12", "data\\plan")
    for label, plain in (("Левый верх", "Top left"), ("Правый верх", "Top right"), ("Правый низ", "Bottom right")):
        western = western.replace(label, plain)
    latin = western.encode("cp1252")
    assert re_.retarget_tab(latin, "Разрез 7.png") == (latin, False)  # no Cyrillic in that code page
    assert re_.retarget_tab(latin, "plan 7.png")[0] == latin.replace(b"C:\\data\\plan.png", b"plan 7.png")
    world = b"1.0\r\n0.0\r\n0.0\r\n-1.0\r\n10.5\r\n20.5\r\n"
    assert re_.retarget_tab(world, "a.png") == (world, False)  # no File line: as it was
    old_mac = TAB.replace("\r\n", "\r").encode("cp1251")
    assert re_.retarget_tab(old_mac, "a.png")[0] == old_mac.replace(
        "C:\\Съёмка\\Разрез 12.png".encode("cp1251"), b"a.png"
    )
    # a TAB that cannot name the image is still exported, with a warning that says what to fix
    with dialect.write_tx(proj.project.engine) as conn:
        from bf_core.db.schema import blob_owners

        owner = {"owner_kind": "object", "owner_id": raster_id, "media_type": ARRAY_MEDIA}
        conn.execute(
            sa.delete(blob_owners).where(blob_owners.c.owner_id == raster_id, blob_owners.c.role == "sidecar:tab")
        )
        proj.store.put_bytes(conn, latin, role="sidecar:tab", **owner)
    out, sent = proj.run(raster_id=raster_id, path=str(tmp_path / "o2" / "Профиль 5"), format="source")
    assert (tmp_path / "o2" / "Профиль 5.tab").read_bytes() == latin
    assert len(out["warnings"]) == 2 and "«Профиль 5.png»" in out["warnings"][0] and "вручную" in out["warnings"][0]


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
