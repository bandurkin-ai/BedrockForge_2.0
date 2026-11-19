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
from dataclasses import asdict, replace
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
    # the scene «карьер», 20 points/m² at CR = 1 m: 10 million points (2 million cells) within 1.5 GB, 50 million
    # (8 million cells) within the 4 GB of ТЗ 7.35; never less than the kernels
    assert 512 <= kind.estimate_memory({"points": 100_000, "cells": 50_000}) <= 1024
    assert 711 <= kind.estimate_memory({"points": 10_000_000, "cells": 2_010_000}) <= 1536  # measured: 711 MB
    assert kind.estimate_memory({"points": 50_000_000, "cells": 8_000_000}) <= 4096
    # the grids of a block (7.1.18): 6 million points in a block of 16 million cells — 1.16 GB measured, and
    # the estimate was 0.66 GB; a block of CR = 0.1 m with its margin of 64 m is 44 million cells
    assert 1160 <= kind.estimate_memory({"points": 6_000_000, "cells": 16_100_000}) <= 1600
    small = kind.estimate_memory({"points": 6_000_000, "cells": 4_000_000})
    assert kind.estimate_memory({"points": 6_000_000, "cells": 44_302_336}) - small == pytest.approx(2000, abs=5)
    # parameters stored before `cells`: four cells a point (the sparsest cloud the filter takes), a block at most
    assert kind.estimate_memory({"points": 100_000}) == kind.estimate_memory({"points": 100_000, "cells": 400_000})
    full = {"points": 50_000_000, "cells": gj.FULL_BLOCK_CELLS}
    assert kind.estimate_memory({"points": 50_000_000}) == kind.estimate_memory(full) <= 4096
    assert gj.FULL_BLOCK_CELLS == ground.block_cells(1e6, 1e6, ground.PROFILES["quarry"]) == (4096 + 2 * 128) ** 2


def test_request_checks_the_cloud_and_the_parameters(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    cloud_id = add_cloud(project, scene(51, 4000))
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name="пикеты.csv", meta={"format": "csv"})
        pts = ls_store.put_layer(conn, store, from_points("Пикеты", scene(52, 300).xyz), file_id=file_id)
    monkeypatch.setattr(gj.os, "cpu_count", lambda: 6)
    # «Карьер» by default
    box = opened(project, cloud_id).bbox
    cells = ground.block_cells(box[3] - box[0], box[4] - box[1], ground.PROFILES["quarry"])
    assert 4 * 100 * 80 <= cells <= 4 * 120 * 100  # cells of 0.5 m over 100 × 80 m and the empty lattice around
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
        "cells": cells,
        "name": "Съёмка",
    }
    assert 3.5 * cells < request(project, cloud_id, cr=0.5)["cells"] < 4 * cells  # a finer step — more cells
    # «Равнина»: a stiff cloth, no faces — a stage that is off is false in the stored parameters too
    plain = request(project, cloud_id, profile="plain", heavy_workers=2)
    assert (plain["profile"], plain["ri"], plain["r_b"], plain["threads"]) == ("plain", 3, False, 3)
    # «Свой»: the numbers of the window over the constants of a profile; false switches a stage off
    own = request(project, cloud_id, cr=0.5, ri=1, ct=0.25, r_b=False, noise=False, threads=99)
    assert (own["cr"], own["ri"], own["ct"], own["threads"]) == (0.5, 1, 0.25, 8)
    assert own["r_b"] is False and own["noise"] is False
    # one meaning everywhere (7.1.18): null and an empty text — as the profile has it, never «off»
    for empty in (None, "", "  "):
        assert request(project, cloud_id, r_b=empty)["r_b"] == 5.0
        assert request(project, cloud_id, profile="plain", r_b=empty)["r_b"] is False
    assert request(project, cloud_id, noise=None)["noise"] == {"r": 2.0, "k_min": 3}
    # … and so it is in the body of the request (JSON false is not the number 0, null is not «off»)
    from bf_core.lines.router import ClassifyBody

    def posted(raw: str) -> dict[str, Any]:
        return request(project, cloud_id, **ClassifyBody.model_validate_json(raw).model_dump())

    assert posted('{"r_b": null}')["r_b"] == posted("{}")["r_b"] == 5.0
    assert posted('{"r_b": false}')["r_b"] is False and posted('{"noise": false}')["noise"] is False
    assert posted('{"profile": "plain", "r_b": null}')["r_b"] is False  # «Равнина» has no faces
    assert posted('{"profile": "plain", "r_b": true}')["r_b"] == 5.0 and posted('{"r_b": "7,5"}')["r_b"] == 7.5
    assert posted('{"noise": {"r": null, "k_min": 4}}')["noise"] == {"r": 2.0, "k_min": 4}
    # true — the stage is on: the number of the profile, or the default of the kernel over «Равнина»
    assert request(project, cloud_id, r_b=True)["r_b"] == 5.0
    assert request(project, cloud_id, profile="plain", r_b=True)["r_b"] == ground.GroundParams().r_b == 5.0
    assert request(project, cloud_id, profile="plain", r_b=True, noise=True)["noise"] == {"r": 2.0, "k_min": 3}
    own = request(project, cloud_id, profile="plain", r_b=7.5, noise={"r": 1.5}, threads=1, heavy_workers=64)
    assert (own["r_b"], own["noise"], own["threads"]) == (7.5, {"r": 1.5, "k_min": 3}, 1)
    assert request(project, cloud_id, noise={"k_min": 5})["noise"] == {"r": 2.0, "k_min": 5}
    assert request(project, cloud_id, heavy_workers=64)["threads"] == 1
    # what is stored gives the same parameters of the kernel again — and reads as a request does
    assert gj.kernel_params(own) == ground.make_params("plain", r_b=7.5, noise={"r": 1.5})
    assert gj.kernel_params(request(project, cloud_id)) == ground.PROFILES["quarry"]
    assert gj.kernel_params(plain) == ground.PROFILES["plain"]
    off = request(project, cloud_id, r_b=False, noise=False)
    assert gj.kernel_params(off) == ground.make_params("quarry", r_b=False, noise=False)
    assert gj.kernel_params(off).r_b is None and gj.kernel_params(off).noise_r is None  # what the kernel runs with
    assert gj.kernel_params({**off, "r_b": None, "noise": None}) == ground.PROFILES["quarry"]  # null — the profile
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


def test_the_profiles_of_the_kernel_are_what_the_window_is_given(api) -> None:  # type: ignore[no-untyped-def]
    """7.1.18: `GET /lines/ground/profiles` — the window «Фильтр земли» had a copy of `PROFILES`."""
    c, _project = api
    got = ok(c.get(f"{API}/lines/ground/profiles"))
    # the answer the tests of the interface hold its spare values to (ui/test/groundFilter.test.ts): when the
    # kernel changes, write the file anew — `json.dumps(ground_job.profiles(), ensure_ascii=False, indent=2)`
    shared = Path(__file__).parent / "data" / "ground" / "profiles.json"
    assert got == json.loads(shared.read_text(encoding="utf-8"))
    assert got["default"] == ground.DEFAULT_PROFILE == "quarry"
    by_id = {p["id"]: p for p in got["profiles"]}
    assert list(by_id) == ["quarry", "plain"] and set(by_id) == set(ground.PROFILES)  # the default goes first
    for name, p in by_id.items():
        k = ground.PROFILES[name]
        assert p["title"] == ground.PROFILE_TITLES[name]
        assert (p["cr"], p["ri"], p["ct"], p["noise"]) == (k.cr, k.ri, k.ct, {"r": k.noise_r, "k_min": k.noise_k})
        assert (p["alpha_deg"], p["face_deg"], p["edge_areas"]) == (k.alpha_deg, k.face_deg, k.edge_areas)
        assert f"рост по склону до {k.alpha_deg:g}°" in p["hint"]  # the slope of the hint is the constant
        # the fields of a profile are those of a request: sent back they give the profile, whatever the base
        for base in by_id:
            fields = {key: p[key] for key in ("cr", "ri", "ct", "r_b", "noise")}
            assert ground.make_params(base, **fields) == replace(
                ground.PROFILES[base],
                **{key: getattr(k, key) for key in ("cr", "ri", "ct", "r_b", "noise_r", "noise_k")},
            )
    assert by_id["quarry"]["r_b"] == 5.0 and by_id["plain"]["r_b"] is False  # false — off; never null
    assert (by_id["quarry"]["title"], by_id["plain"]["title"]) == ("Карьер", "Равнина")
    assert "до 55°" in by_id["quarry"]["hint"] and "до 30°" in by_id["plain"]["hint"]
    assert got["defaults"] == {"cr": 1.0, "ri": 2, "ct": 0.5, "r_b": 5.0, "noise": {"r": 2.0, "k_min": 3}}
    # the ranges are the ones the kernel checks: their ends are taken, a step outside is refused
    ranges = got["ranges"]
    assert set(ranges) == {"cr", "ct", "r_b", "noise_r", "ri", "noise_k"}
    assert (ranges["cr"]["lo"], ranges["cr"]["hi"], ranges["cr"]["unit"]) == (0.1, 50.0, "м")
    assert ranges["ri"] == {"lo": 1, "hi": 3, "title": "Жёсткость ткани RI", "unit": "", "whole": True}

    def params(key: str, value: float) -> Any:
        noise = {"noise_r": {"r": value}, "noise_k": {"k_min": value}}.get(key)
        return ground.make_params("quarry", **({"noise": noise} if noise else {key: value}))

    for key, r in ranges.items():
        assert r["title"] and r["whole"] is (key in ("ri", "noise_k")), key
        step = 1 if r["whole"] else 1e-6
        for value in (r["lo"], r["hi"]):
            params(key, value)
        for value in (r["lo"] - step, r["hi"] + step):
            with pytest.raises(ground.GroundError) as err:
                params(key, value)
            assert err.value.code == "bad_param" and r["title"] in err.value.message, (key, value)


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
    params = request(project, cloud_id, profile="plain", noise=False)
    assert params["r_b"] is False and params["noise"] is False
    _res, _seen, journal = run_job(project, params)
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


# --- the real kernel: a sparse cloud ---------------------------------------------------------------------


def test_a_sparse_cloud_is_warned_of_in_the_journal_and_a_too_sparse_one_refused(api) -> None:  # type: ignore[no-untyped-def]
    """7.1.18: the warnings of the kernel are {code, message, hint} — the journal got the text of the dict,
    at a level the interface does not know."""
    _c, project = api
    rng = np.random.default_rng(62)
    xy = rng.random((14_400, 2)) * 120.0  # a point a square metre
    xyz = np.column_stack([xy, 0.02 * xy[:, 0] + rng.normal(0.0, 0.02, len(xy))]) + gen.ORIGIN
    cloud_id = add_cloud(project, lc.CloudChunk(xyz), "Редкое")
    before = state(project, cloud_id)
    # CR = 1.2 m: a point in a third of the cells of 0.6 m — done, with a warning
    res, _seen, journal = run_job(project, request(project, cloud_id, cr=1.2, threads=2))
    warned = [line for line in journal if line.startswith("warn: ")]
    assert len(warned) == 1 and not any(line.startswith("warning") for line in journal), journal
    assert warned[0].startswith("warn: Облако редкое для шага ткани CR = 1,2 м (точки в 3")
    assert "земля под растительностью может быть найдена не полностью. Задайте шаг ткани CR не меньше 1," in warned[0]
    assert "{" not in warned[0] and "code" not in warned[0]  # the text, not the dict
    assert res["ground"] > 0.9 * len(xyz) and state(project, cloud_id)["version"] == before["version"] + 1
    # CR = 0.5 m: a point in a tenth of the cells — refused with the step to take, right after the index
    first = state(project, cloud_id)
    with pytest.raises(JobError) as err:
        run_job(project, request(project, cloud_id, cr=0.5))
    assert err.value.code == "cloud_too_sparse" and "CR = 0,5 м" in err.value.message
    assert err.value.hint.startswith("Задайте шаг ткани CR не меньше 1,") and not err.value.retry
    assert state(project, cloud_id) == first and tmp_files(project) == []


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


def test_the_filter_is_started_through_the_api_of_the_layer(api) -> None:  # type: ignore[no-untyped-def]
    c, project = api
    s = gen.plain(61, side=60.0, density=2.0, buildings=1, trees=5, outliers=3)
    cloud_id = add_cloud(project, lc.CloudChunk(s.xyz, None, s.ret), "Через API")
    started = ok(c.post(f"{API}/lines/layers/{cloud_id}/classify", json={"profile": "plain", "cr": "1,5"}))
    job = wait_job(c, started["source"], started["id"], timeout=300)
    assert job["status"] == "done" and job["title"] == "Фильтр земли", job
    assert job["params"]["profile"] == "plain" and opened(project, cloud_id).classes.get(2, 0) > 0
    bad = c.post(f"{API}/lines/layers/{cloud_id}/classify", json={"profile": "lake"})
    assert bad.status_code == 422
