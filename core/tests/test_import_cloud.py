"""Point clouds in «Импорт файлов» (ADR 0014 §1, §3, §6; `importing/object_cloud.py`, `formats/points_stream.py`):
a LAS of 1 млн points and a table XYZ become a line file with one layer — the cloud: the points bit for bit,
`seq` back to the order of the file, the classes, the CRS label, the source kept; «каждая k-я запись»; the
composition of the wizard; a LAZ refused with its hint; a cancel at any step leaves nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import import_log, objects
from bf_core.formats.las import open_las
from bf_core.formats.las_write import geokey_vlrs, write_las
from bf_core.jobs.context import JobCancelled, JobError
from bf_core.lines import cloud as lc
from bf_core.storage.blobs import BlobStore
from tests.test_import_objects import project, roles_of, run_job, sha_of, state_of  # noqa: F401

pytestmark = pytest.mark.windows

BASE = np.array([512_000.0, 6_234_000.0, 300.0])


def scene(n: int, seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Points over 600 × 400 m with classes 1, 2, 5 and returns «k of m»; UTM, millimetres."""
    rng = np.random.default_rng(seed)
    xyz = BASE + rng.uniform([0, 0, 0], [600, 400, 80], (n, 3))
    xyz = np.round(xyz, 3)
    cls = rng.choice(np.array([1, 2, 5], dtype=np.uint8), n)
    count = rng.integers(1, 4, n)
    number = np.minimum(rng.integers(1, 4, n), count)
    return xyz, cls, (number | (count << 4)).astype(np.uint8)


@pytest.fixture(scope="module")
def las_1m(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("облака")
    xyz, cls, ret = scene(1_000_000)
    path = folder / "съёмка карьера.las"
    write_las(path, xyz, cls, ret, vlrs=geokey_vlrs(32637))
    return path


def cloud_of(project: Any, layer_id: int) -> lc.Cloud:  # noqa: F811
    with dialect.read_tx(project.engine) as conn:
        return lc.open_cloud(conn, BlobStore(project.folder), layer_id)


def test_las_of_a_million_points_becomes_a_cloud(project: Any, las_1m: Path) -> None:  # noqa: F811
    ctx = run_job(project.folder, {"files": [str(las_1m)]})
    out = ctx.result  # type: ignore[attr-defined]
    assert not out["failed"] and len(out["files"]) == 1 and out["points"] == 1_000_000
    entry = out["files"][0]
    assert entry["kind"] == "cloud" and entry["format"] == "las" and entry["layers"] == 1
    assert out["cloud_ids"] == [entry["cloud_id"]] and entry["parts"] >= 2 and entry["every"] == 1
    cloud = cloud_of(project, entry["cloud_id"])
    source = open_las(las_1m).read()
    assert cloud.count == 1_000_000 and cloud.file_id == entry["object_id"]
    seq = cloud.read("seq")
    back = np.empty(cloud.count, dtype=np.int64)
    back[seq] = np.arange(cloud.count)  # storage order → the order of the file
    assert np.array_equal(cloud.read("xyz")[back], source.xyz)  # bit for bit
    assert np.array_equal(cloud.read("class")[back], source.cls) and np.array_equal(cloud.read("ret")[back], source.ret)
    assert cloud.classes == {int(k): int(v) for k, v in zip(*np.unique(source.cls, return_counts=True), strict=True)}
    src = cloud.meta["source"]
    assert src["kind"] == "las" and src["every"] == 1 and src["las"]["epsg"] == 32637 and src["las"]["version"] == "1.2"
    with dialect.read_tx(project.engine) as conn:
        file = conn.execute(sa.select(objects).where(objects.c.id == cloud.file_id)).mappings().one()
        log = conn.execute(sa.select(import_log)).mappings().one()
        roles = roles_of(conn, cloud.file_id)
    assert file["state"] == "ready" and file["name"] == las_1m.name and "32637" in (file["crs"] or "")
    assert file["meta"]["cloud"] is True and file["meta"]["epsg"] == 32637
    assert roles == {"source": sha_of(las_1m)} and log["sha256"] == sha_of(las_1m)
    assert log["counts"]["kind"] == "cloud" and log["counts"]["cloud_id"] == cloud.layer_id
    assert any("облако точек — точек 1 000 000" in line for line in ctx.lines)
    assert state_of(project.engine, project.folder)["tmp"] == []
    # a retry after the final transaction finds the import done
    again = run_job(project.folder, {"files": [str(las_1m)]}, job_id=ctx.id).result  # type: ignore[attr-defined]
    assert again["files"][0]["cloud_id"] == cloud.layer_id


def test_every_kth_record_and_the_composition(project: Any, las_1m: Path) -> None:  # noqa: F811
    from bf_core.importing.object_files import inspect_files

    seen = inspect_files([str(las_1m)], {str(las_1m): {"every": 3}})
    entry = seen["files"][0]
    assert entry["ok"] and entry["kind"] == "cloud" and entry["format"] == "las"
    card = entry["cloud"]
    assert card["format"] == "las" and card["point_count"] == 1_000_000 and card["points"] == 333_334
    assert card["every_chosen"] == 3 and card["crs"]["epsg"] == 32637 and card["sampled"] == 1_000_000
    assert set(card["classes"]) == {"1", "2", "5"} and "интенсивность" in card["not_loaded"]
    assert seen["totals"]["points"] == 333_334 and seen["totals"]["files"] == 1 and entry["crs"]["epsg"] == 32637
    out = run_job(project.folder, {"files": [str(las_1m)], "options": {str(las_1m): {"every": 3}}}).result  # type: ignore[attr-defined]
    cloud = cloud_of(project, out["cloud_ids"][0])
    assert cloud.count == 333_334 and cloud.meta["source"]["every"] == 3
    assert np.array_equal(np.sort(cloud.read("seq")), np.arange(0, 1_000_000, 3))
    bad = inspect_files([str(las_1m)], {str(las_1m): {"every": "две"}})["files"][0]
    assert not bad["ok"] and bad["error"]["code"] == "bad_every" and bad["error"]["hint"]


def test_xyz_table_as_a_cloud(project: Any, tmp_path: Path) -> None:  # noqa: F811
    """Spaces and tabs, a decimal comma, an empty Z (skipped and counted, not a zero), the class column."""
    from bf_core.importing.object_files import inspect_files

    xyz, cls, _ret = scene(30_000, seed=3)
    rows = [
        f"{x!r}\t{y!r}  {z!r}  {c}  7".replace(".", ",")
        for (x, y, z), c in zip(xyz.tolist(), cls.tolist(), strict=True)
    ]
    rows[10] = "512010,5  6234010,5    2  7"  # four cells: another number of cells
    path = tmp_path / "точки.txt"
    path.write_text("Восток  Север  Отметка  Класс  Интенсивность\n" + "\n".join(rows) + "\n", encoding="cp1251")
    options = {str(path): {"cloud": True}}
    entry = inspect_files([str(path)], options)["files"][0]
    card = entry["cloud"]
    assert entry["kind"] == "cloud" and card["decimal"] == "," and card["delimiter"] == "whitespace"
    assert card["fields"] == {"x": "Восток", "y": "Север", "z": "Отметка", "class": "Класс"}
    assert card["not_loaded"] == ["колонка «Интенсивность»"] and len(card["preview"]) == 20
    assert abs(card["estimate"] - 30_000) < 1500 and entry["encoding"] == "cp1251"
    assert inspect_files([str(path)])["files"][0]["kind"] == "points"  # without `cloud` — an ordinary layer
    ctx = run_job(project.folder, {"files": [str(path)], "options": options})
    out = ctx.result  # type: ignore[attr-defined]
    cloud = cloud_of(project, out["cloud_ids"][0])
    keep = np.ones(len(xyz), dtype=bool)
    keep[10] = False
    assert cloud.count == len(xyz) - 1 and cloud.meta["source"]["kind"] == "xyz"
    order = np.argsort(cloud.read("seq"))
    assert np.array_equal(cloud.read("xyz")[order], xyz[keep]) and np.array_equal(cloud.read("class")[order], cls[keep])
    assert any("другим числом колонок: 1" in line for line in ctx.lines)
    empty_z = tmp_path / "без высот.csv"
    empty_z.write_text("X;Y;Z\n1,5;2,5;3,5\n4;5;\n6;7;8\n", encoding="utf-8")
    out = run_job(project.folder, {"files": [str(empty_z)], "options": {str(empty_z): {"cloud": True}}})
    cloud = cloud_of(project, out.result["cloud_ids"][0])  # type: ignore[attr-defined]
    assert cloud.count == 2 and np.array_equal(np.sort(cloud.read("seq")), [0, 2])
    assert any("Строк без Z: 1" in line for line in out.lines)


def test_laz_is_refused_with_its_hint(project: Any, tmp_path: Path, las_1m: Path) -> None:  # noqa: F811
    from bf_core.importing.object_files import inspect_files

    laz = tmp_path / "сжатое.laz"
    laz.write_bytes(las_1m.read_bytes()[:4096])
    seen = inspect_files([str(laz)])["files"][0]
    assert not seen["ok"] and seen["error"]["code"] == "laz_not_supported" and "laszip" in seen["error"]["hint"]
    with pytest.raises(JobError) as info:
        run_job(project.folder, {"files": [str(laz)]})
    assert info.value.code == "laz_not_supported" and "LAZ" in info.value.message
    assert state_of(project.engine, project.folder)["objects"] == []


def test_cancel_at_any_step_leaves_nothing(project: Any, tmp_path: Path) -> None:  # noqa: F811
    xyz, cls, ret = scene(40_000, seed=5)
    path = tmp_path / "малое.las"
    write_las(path, xyz, cls, ret)
    empty = state_of(project.engine, project.folder)
    ticks = run_job(project.folder, {"files": [str(path)]}).ticks
    with dialect.write_tx(project.engine) as conn:  # start again from an empty project
        conn.execute(sa.delete(objects))
        conn.execute(sa.delete(import_log))
    for at in range(1, ticks, max(1, ticks // 12)):
        with pytest.raises(JobCancelled):
            run_job(project.folder, {"files": [str(path)]}, cancel_at=at)
        state = state_of(project.engine, project.folder)
        assert {**state, "owners": 0} == {**empty, "owners": 0} and state["tmp"] == [], at


def test_utf16_table_and_the_hint_for_a_big_layer(
    project: Any,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A UTF-16 table goes through the reader of Э3; a layer of points bigger than a cloud hint is warned of."""
    from bf_core.importing import object_files
    from bf_core.importing.object_files import inspect_files

    path = tmp_path / "юникод.csv"
    path.write_text("X;Y;Z;CLASS\n1,25;2,5;3,5;2\n4;5;6;300\n7;8;9;1\n", encoding="utf-16")
    out = run_job(project.folder, {"files": [str(path)], "options": {str(path): {"cloud": True}}})
    cloud = cloud_of(project, out.result["cloud_ids"][0])  # type: ignore[attr-defined]
    order = np.argsort(cloud.read("seq"))
    assert np.array_equal(cloud.read("xyz")[order], [[1.25, 2.5, 3.5], [4, 5, 6], [7, 8, 9]])
    assert np.array_equal(cloud.read("class")[order], [2, 0, 1])  # 300 is not a class: 0, counted
    assert any("целым числом 0–255: 1" in line for line in out.lines)
    monkeypatch.setattr(object_files, "CLOUD_HINT_POINTS", 2)
    plain = inspect_files([str(path)])["files"][0]
    assert plain["kind"] == "points" and any("загрузите его как облако" in w for w in plain["warnings"])
