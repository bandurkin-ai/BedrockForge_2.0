"""The job `ground.classify` — «Фильтр земли» of a cloud (ADR 0014 §4, §6; `lines/ground_job.py`): parameters by
profile and their ranges; what the kernel is given (the columns of the cloud, the threads) and what is written
(`class`, `h`, `meta.cloud.ground`, a new version) — with a kernel put in its place, so the test does not depend
on how the ground is found; a cancel at any step leaves the cloud as it was and a repeat works; a cloud that
changed meanwhile is not written over. Then the real kernel in a worker: the stored classes are those of
`classify_ground` called here, bit for bit. Tolerance: none."""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import line_layers, objects
from bf_core.formats.lineset import from_points
from bf_core.geo import ground
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import HANDLER_MODULES, load_handlers
from bf_core.jobs.service import jobs_of
from bf_core.lines import cloud as lc
from bf_core.lines import ground_job as gj
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import cloud_source as cs
from bf_core.wireframes import jobs as dtm_jobs
from tests.test_cloud_dtm import add_cloud, ok, opened, scene, set_values, tmp_files
from tests.test_import_job import API, wait_job

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen import cloud as gen  # noqa: E402

pytestmark = pytest.mark.windows


@pytest.fixture(scope="module")
def numba_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("numba")


@pytest.fixture()
def api(pclient, numba_cache: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_cache))
    r = pclient.post(f"{API}/projects", json={"name": "Фильтр земли"})
    assert r.status_code == 200, r.text
    return pclient, projects_of(pclient.core).require_current()


def refusal(fn: Any, status: int, code: str) -> ApiError:
    with pytest.raises(ApiError) as err:
        fn()
    assert err.value.status == status and err.value.code == code and err.value.message and err.value.hint
    return err.value


def request(project: Any, cloud_id: int, **kw: Any) -> dict[str, Any]:
    with dialect.read_tx(project.engine) as conn:
        return gj.request(conn, cloud_id, **kw)


class Kernel:
    """Stands for `geo.ground.classify_ground`: the ground is what lies below the median Z, every 50th point is
    noise without a height. Remembers what it was given."""

    def __init__(self, during: Any = None, fail: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.during, self.fail = during, fail

    @staticmethod
    def answer(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        z = np.asarray(xyz[:, 2], dtype=np.float64)
        cls = np.where(z <= np.median(z), 2, 5).astype(np.uint8)
        h = (z - z.min()).astype(np.float32)
        cls[::50] = 7
        h[::50] = np.nan
        return cls, h

    def __call__(self, xyz: Any, ret: Any = None, params: Any = None, *, threads: int = 1, tick: Any = None) -> Any:
        self.calls.append({"xyz": np.array(xyz), "ret": None if ret is None else np.array(ret), "params": params})
        self.calls[-1].update(threads=threads, mapped=isinstance(xyz, np.memmap))
        for share, stage in ((0.0, "Индекс ячеек"), (0.4, "Ткань"), (0.8, "Классы"), (1.0, "Грани")):
            if tick is not None:
                tick(share, stage)
            if stage == "Ткань" and self.during is not None:
                self.during()
        if self.fail is not None:
            raise self.fail
        cls, h = self.answer(xyz)
        counts = np.bincount(cls, minlength=8)
        report = {
            "points": len(cls),
            "blocks": 3,
            "threads": threads,
            "classes": {int(code): int(counts[code]) for code in (1, 2, 3, 4, 5, 7)},
            "seconds": {"Ткань": 0.25},
        }
        return ground.GroundResult(cls, h, report)


@pytest.fixture()
def kernel(monkeypatch: pytest.MonkeyPatch) -> Kernel:
    fake = Kernel()
    monkeypatch.setattr(ground, "classify_ground", fake)
    monkeypatch.setattr(ground, "warm", lambda: None)
    return fake


def run_job(project: Any, params: dict[str, Any], cancel_at: str | None = None) -> tuple[Any, list[str], list[str]]:
    """The handler itself, in this process: (result or None when cancelled, messages of the progress, journal)."""
    seen: list[str] = []
    journal: list[str] = []

    class Ctx(JobContext):
        def tick(self, progress: float | None = None, message: str | None = None) -> None:
            assert progress is None or 0.0 <= progress <= 1.0
            seen.append(str(message))
            if message == cancel_at:
                raise JobCancelled()

        def log(self, text: str, level: str = "info") -> None:
            journal.append(f"{level}: {text}")

    ctx = Ctx({"id": 1, "params": params, "project_folder": str(project.folder)}, lambda _m: None, threading.Event())
    try:
        if cancel_at is not None:
            with pytest.raises(JobCancelled):
                gj.ground_classify(ctx)
            return None, seen, journal
        return gj.ground_classify(ctx), seen, journal
    finally:
        ctx.close()


def state(project: Any, cloud_id: int) -> dict[str, Any]:
    """What a filter may change in a cloud."""
    cloud = opened(project, cloud_id)
    with dialect.read_tx(project.engine) as conn:
        schema = conn.execute(
            sa.select(line_layers.c.attr_schema).where(line_layers.c.object_id == cloud_id)
        ).scalar_one()
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == cloud_id)).scalar_one()
    return {
        "version": cloud.version,
        "shas": dict(cloud.shas),
        "classes": dict(cloud.classes),
        "ground": meta["cloud"]["ground"],
        "attrs": [item["name"] for item in schema],
    }


# --- parameters ------------------------------------------------------------------------------------------


def test_the_job_is_registered_by_an_explicit_import() -> None:
    assert "bf_core.lines.ground_job" in HANDLER_MODULES
    kind = load_handlers()["ground.classify"]
    assert kind.queue == "heavy" and kind.title == "Фильтр земли" and kind.run is gj.ground_classify
    assert not kind.public and not kind.instance
    from bf_core.jobs.scheduler import OBJECT_JOBS

    assert "ground.classify" in OBJECT_JOBS  # its end is announced: the cloud is read again
    # 10 million points within the gigabyte and 50 million within the 4 GB of ТЗ 7.35; never less than the kernels
    assert 512 <= kind.estimate_memory({"points": 100_000}) <= 1024
    assert kind.estimate_memory({"points": 10_000_000}) <= 1536
    assert kind.estimate_memory({"points": 50_000_000}) <= 4096


def test_request_checks_the_cloud_and_the_parameters(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    cloud_id = add_cloud(project, scene(51, 4000))
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name="пикеты.csv", meta={"format": "csv"})
        pts = ls_store.put_layer(conn, store, from_points("Пикеты", scene(52, 300).xyz), file_id=file_id)
    monkeypatch.setattr(gj.os, "cpu_count", lambda: 6)
    # «Карьер» by default
    assert request(project, cloud_id) == {
        "cloud_id": cloud_id,
        "profile": "quarry",
        "cr": 1.0,
        "ri": 2,
        "ct": 0.5,
        "r_b": 5.0,
        "noise": {"r": 2.0, "k_min": 3},
        "threads": 6,
        "points": 4700,
        "name": "Съёмка",
    }
    # «Равнина»: a stiff cloth, no faces
    plain = request(project, cloud_id, profile="plain", heavy_workers=2)
    assert (plain["profile"], plain["ri"], plain["r_b"], plain["threads"]) == ("plain", 3, None, 3)
    # «Свой»: the numbers of the window over the constants of a profile; false switches a stage off
    own = request(project, cloud_id, cr=0.5, ri=1, ct=0.25, r_b=False, noise=False, threads=99)
    assert (own["cr"], own["ri"], own["ct"], own["r_b"], own["noise"], own["threads"]) == (0.5, 1, 0.25, None, False, 8)
    own = request(project, cloud_id, profile="plain", r_b=7.5, noise={"r": 1.5}, threads=1, heavy_workers=64)
    assert (own["r_b"], own["noise"], own["threads"]) == (7.5, {"r": 1.5, "k_min": 3}, 1)
    assert request(project, cloud_id, noise={"k_min": 5})["noise"] == {"r": 2.0, "k_min": 5}
    assert request(project, cloud_id, heavy_workers=64)["threads"] == 1
    # what is stored gives the same parameters of the kernel again
    assert gj.kernel_params(own) == ground.make_params("plain", r_b=7.5, noise={"r": 1.5})
    assert gj.kernel_params(request(project, cloud_id)) == ground.PROFILES["quarry"]
    assert gj.kernel_params(plain) == ground.PROFILES["plain"]
    # the ranges of ТЗ 7.35 Д-22
    for bad in ({"cr": 0.05}, {"cr": 51}, {"ri": 4}, {"ri": 0}, {"ct": 0.01}, {"ct": 6}, {"r_b": 0.5}, {"r_b": 21}):
        refusal(lambda bad=bad: request(project, cloud_id, **bad), 422, "bad_param")
    for bad in ({"noise": {"r": 0.1}}, {"noise": {"k_min": 21}}, {"profile": "луна"}, {"ri": 2.5}, {"ri": "два"}):
        refusal(lambda bad=bad: request(project, cloud_id, **bad), 422, "bad_param")
    # numbers of the window: text with a comma
    typed = request(project, cloud_id, cr="0,5", ct=" 0.25 ", r_b="7,5", noise={"r": "1,5", "k_min": 4}, ri="3")
    assert (typed["cr"], typed["ct"], typed["r_b"], typed["ri"]) == (0.5, 0.25, 7.5, 3)
    assert typed["noise"] == {"r": 1.5, "k_min": 4}
    assert request(project, cloud_id, cr="", r_b=True, noise=True) == request(project, cloud_id)  # empty: the profile
    for bad in ({"cr": "мелко"}, {"ct": float("nan")}, {"r_b": "5 м"}, {"noise": {"r": "близко"}}):
        refusal(lambda bad=bad: request(project, cloud_id, **bad), 422, "not_a_number")
    refusal(lambda: request(project, 999_999), 404, "line_layer_not_found")
    refusal(lambda: request(project, pts), 409, "not_a_cloud")


# --- the job with a kernel put in its place ----------------------------------------------------------------


def test_job_gives_the_kernel_the_cloud_and_writes_classes_and_heights(api, kernel: Kernel) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    chunk = scene(53, 6000)
    rng = np.random.default_rng(54)
    ret = lc.pack_returns(rng.integers(1, 4, len(chunk.xyz)), rng.integers(1, 5, len(chunk.xyz)))
    cloud_id = add_cloud(project, lc.CloudChunk(chunk.xyz, chunk.cls, ret))
    before = state(project, cloud_id)
    cloud = opened(project, cloud_id)
    xyz = cloud.read("xyz")
    assert before["ground"] is None and "Высота над землёй, м" not in before["attrs"]

    params = request(project, cloud_id, cr=3.0, ct=0.25, noise={"r": 1.5, "k_min": 4}, threads=3)
    res, seen, journal = run_job(project, params)

    # the kernel got the columns of the cloud in the storage order — the coordinates as a map of the file
    (call,) = kernel.calls
    assert call["mapped"] and call["xyz"].dtype == np.float64 and call["xyz"].tobytes() == xyz.tobytes()
    assert call["ret"].dtype == np.uint8 and call["ret"].tobytes() == cloud.read("ret").tobytes()
    assert call["threads"] == 3
    assert call["params"] == ground.make_params("quarry", cr=3.0, ct=0.25, noise={"r": 1.5, "k_min": 4})
    # its answer is the new columns, bit for bit; the rest of the cloud is as it was
    cls, h = Kernel.answer(xyz)
    after = state(project, cloud_id)
    done = opened(project, cloud_id)
    assert done.read("class").tobytes() == cls.tobytes() and done.read("h").tobytes() == h.tobytes()
    assert after["version"] == before["version"] + 1 == res["version"]
    assert {k: after["shas"][k] for k in ("xyz", "seq", "ret")} == {k: before["shas"][k] for k in ("xyz", "seq", "ret")}
    assert after["shas"]["class"] != before["shas"]["class"] and "h" in after["shas"]
    counts = {int(code): int(n) for code, n in zip(*np.unique(cls, return_counts=True), strict=True)}
    by_code = {str(code): n for code, n in counts.items()}
    assert after["classes"] == counts and res["classes"] == by_code
    assert res["ground"] == counts[2] and res["points"] == 6700
    assert res["cloud_id"] == cloud_id and res["noise"] == counts[7] == 134
    assert "Высота над землёй, м" in after["attrs"] and "Класс" in after["attrs"]
    # `meta.cloud.ground`: what the classes were found with
    g = after["ground"]
    assert g["params"] == {"profile": "quarry", **asdict(call["params"])}  # with the constants of the profile
    asked = {"cr": 3.0, "ri": 2, "ct": 0.25, "r_b": 5.0, "noise_r": 1.5, "noise_k": 4, "alpha_deg": 55.0}
    assert {k: g["params"][k] for k in asked} == asked
    assert g["counts"] == by_code and g["algo"] == ground.ALGO
    assert g["blocks"] == 3 and g["threads"] == 3 and g["at"].startswith("20") and g["seconds"] >= 0
    json.dumps(g)
    # the journal: profile, parameters, classes before and after, blocks, threads, time
    text = "\n".join(journal)
    for piece in ("«Съёмка»", "6 700", "«Карьер»", "CR 3 м", "RI 2", "CT 0,25 м", "R_б 5 м", "шум: радиус 1,5 м"):
        assert piece in text, piece
    for piece in ("До фильтра:", "После фильтра:", "земля (2)", "шум (7) — 134", "блоков 3", "потоков 3", "время"):
        assert piece in text, piece
    assert seen[0] == "Чтение облака" and "Подготовка ядер" in seen and "Ткань" in seen and seen[-1] == "Готово"
    assert seen.index("Запись классов") < seen.index("Сохранение классов в проект")
    assert tmp_files(project) == []
    # «Построить ЦТМ из облака» follows the filter: half its CR a cell, 5 CR the longest edge, the median of h
    assert cs.default_cell(done) == 1.5
    with dialect.read_tx(project.engine) as conn:
        dtm = dtm_jobs.request_from_points(conn, cloud_id=cloud_id)
    assert dtm["max_edge"] == 15.0 and cs.thin(done, [2]).by == "h"


def test_stages_switched_off_are_written_as_off(api, kernel: Kernel) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    cloud_id = add_cloud(project, scene(55, 3000))
    _res, _seen, journal = run_job(project, request(project, cloud_id, profile="plain", noise=False))
    assert kernel.calls[0]["params"] == ground.make_params("plain", noise=False)
    g = state(project, cloud_id)["ground"]
    assert g["params"]["profile"] == "plain" and g["params"]["r_b"] is None and g["params"]["noise_r"] is None
    text = "\n".join(journal)
    assert "«Равнина»" in text and "грани выключены" in text and "шум не ищется" in text


@pytest.mark.parametrize("stage", ["Подготовка ядер", "Ткань", "Запись классов", "Сохранение классов в проект"])
def test_cancel_leaves_the_cloud_as_it_was_and_a_repeat_works(api, kernel: Kernel, stage: str) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    cloud_id = add_cloud(project, scene(56, 5000))
    before = state(project, cloud_id)
    params = request(project, cloud_id, threads=2)
    _res, seen, _log = run_job(project, params, cancel_at=stage)
    assert stage in seen and "Готово" not in seen
    assert state(project, cloud_id) == before and tmp_files(project) == []
    # the repeat
    res, _seen, _log = run_job(project, params)
    first = state(project, cloud_id)
    assert res["version"] == first["version"] == before["version"] + 1 and first["ground"] is not None
    # and once more over the classes of the filter itself: the same columns, a new version, nothing left behind
    again, _seen, journal = run_job(project, params)
    second = state(project, cloud_id)
    assert again["version"] == before["version"] + 2 and again["classes"] == res["classes"]
    assert second["shas"] == first["shas"] and second["classes"] == first["classes"]
    assert any("До фильтра:" in line and "земля (2)" in line for line in journal)
    assert tmp_files(project) == []


def test_a_cloud_that_changed_meanwhile_is_not_written_over(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    _c, project = api
    cloud_id = add_cloud(project, scene(57, 3000))
    cloud = opened(project, cloud_id)
    other = np.full(cloud.count, 9, dtype=np.uint8)

    def another_job() -> None:
        set_values(project, cloud, **{"class": other})

    monkeypatch.setattr(ground, "classify_ground", Kernel(during=another_job))
    monkeypatch.setattr(ground, "warm", lambda: None)
    with pytest.raises(JobError) as err:
        run_job(project, request(project, cloud_id))
    assert err.value.code == "cloud_changed" and err.value.hint and not err.value.retry
    after = opened(project, cloud_id)
    assert after.version == cloud.version + 1 and after.classes == {9: cloud.count} and not after.has("h")
    assert tmp_files(project) == []


def test_refusals_of_the_kernel_and_a_cloud_that_is_gone(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    cloud_id = add_cloud(project, scene(58, 3000))
    before = state(project, cloud_id)
    params = request(project, cloud_id)
    refused = ground.GroundError("too_many_points", "В облаке больше 2 млрд точек.", "Проредите облако при загрузке.")
    monkeypatch.setattr(ground, "classify_ground", Kernel(fail=refused))
    monkeypatch.setattr(ground, "warm", lambda: None)
    with pytest.raises(JobError) as err:
        run_job(project, params)
    assert (err.value.code, err.value.message, err.value.hint) == (refused.code, refused.message, refused.hint)
    assert state(project, cloud_id) == before and tmp_files(project) == []
    with pytest.raises(JobError) as err:
        run_job(project, {**params, "ri": 9})  # parameters stored by hand
    assert err.value.code == "bad_param"
    with pytest.raises(JobError) as err:
        run_job(project, {**params, "cloud_id": 999_999})
    assert err.value.code == "line_layer_not_found"


# --- the real kernel, in a worker --------------------------------------------------------------------------


def test_filter_in_a_worker_stores_what_the_kernel_gives(api) -> None:  # type: ignore[no-untyped-def]
    c, project = api
    s = gen.plain(59, side=120.0, density=3.0, buildings=2, trees=30, outliers=12)
    cloud_id = add_cloud(project, lc.CloudChunk(s.xyz, None, s.ret), "Равнина")
    cloud = opened(project, cloud_id)
    assert cloud.classes == {0: len(s)}
    params = request(project, cloud_id, profile="plain", threads=2)
    key, job_id = jobs_of(c.core).submit("ground.classify", params, title="Фильтр земли")
    job = wait_job(c, key, job_id, timeout=300)
    assert job["status"] == "done", job
    done = opened(project, cloud_id)
    want = ground.classify_ground(cloud.read("xyz"), cloud.read("ret"), ground.make_params("plain"), threads=1)
    assert done.read("class").tobytes() == want.cls.tobytes()  # 2 threads there, 1 here: bit for bit
    assert done.read("h").tobytes() == want.h.tobytes()
    assert done.version == cloud.version + 1 and job["result"]["version"] == done.version
    assert job["result"]["ground"] == done.classes[2] > 0.5 * len(s) and set(done.classes) <= {1, 2, 3, 4, 5, 7}
    assert done.meta["ground"]["params"]["profile"] == "plain" and done.meta["ground"]["algo"] == ground.ALGO
    log = [row["text"] for row in ok(c.get(f"{API}/jobs/{key}/{job_id}/log"))["lines"]]
    assert any("«Равнина»" in line for line in log) and any("После фильтра:" in line for line in log)
    happened = [(name, json.loads(data)) for _id, name, data in c.core.events._recent]
    assert ("objects.changed", {"kind": "job", "job": "ground.classify", "id": job_id}) in happened
    assert tmp_files(project) == []
    # the ground of the filter is what «Построить ЦТМ из облака» takes
    started = ok(c.post(f"{API}/wireframes/from-points", json={"cloud_id": cloud_id}))
    built = wait_job(c, started["source"], started["id"], timeout=240)
    assert built["status"] == "done" and built["result"]["points"] > 5000, built
    w = ok(c.get(f"{API}/wireframes/{built['result']['wireframe_id']}"))
    assert w["built"]["sources"][0]["classes"] == [2] and w["built"]["params"]["max_edge"] == 5.0
