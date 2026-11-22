"""The answers of the core the window «Вариография» is read against (подшаг 16.23; ADR 0022 §15 «Урок Э14–Э15»,
решение 23; e16-api.md): `job.result` of `variography.experimental` (axes; directions with normal scores), the
failure of a job (`job.error`), `POST /preview` with and without `sampled`, the refusal of the preview (422 with
`counts`) — all through the real routes and a job in a `spawn` worker. The file
`tests/data/variography/window_answers.json` is read by vitest (`ui/test/variography-window.test.ts`,
`ui/test/variogram-chart.test.ts`). Regenerate: `BF_REGEN_VARIO_WINDOW=1 uv run pytest -q
tests/test_vario_window_answers.py`. What changes from run to run (the key — of the tokens of the tables, the
seconds of the stages) is compared by its form only."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from tests.test_import_job import wait_job

API = "/api/v1"
GS = API + "/geostat/variography"
MODULE = "bf_core.geostat.variogram.jobs"
DATA = Path(__file__).parent / "data" / "variography" / "window_answers.json"
REGEN = os.environ.get("BF_REGEN_VARIO_WINDOW") == "1"


def noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def grid_table(engine: Any, set_id: int, name: str, side: int, per_hole: int, gaps: bool, seed: int) -> int:
    """side² vertical holes 20 m apart from (500 000, 6 700 000), `per_hole` samples of 2 m; every 17th value
    missing when `gaps` (counted, never 0)."""
    rng = np.random.Generator(np.random.PCG64(seed))
    names = [f"{name[0]}-{i:03d}" for i in range(side * side)]
    with dialect.write_tx(engine) as conn:
        dh.replace_collars(conn, set_id, [dh.CollarRow(h, x=500_000.0 + 20.0 * (i % side),
                                                       y=6_700_000.0 + 20.0 * (i // side), z=300.0,
                                                       td=2.0 * per_hole, azimuth=0.0, dip=-90.0)
                                          for i, h in enumerate(names)])  # fmt: skip
    holes = [h for h in names for _ in range(per_hole)]
    depth = np.tile(np.arange(per_hole) * 2.0, side * side)
    au = np.exp(np.sin(depth / 37.0) + 0.5 * rng.standard_normal(depth.size)).round(4)
    values: list[float | None] = au.tolist()
    if gaps:
        values = [None if k % 17 == 5 else v for k, v in enumerate(values)]
    rock = ["гранит" if k % 3 else "диорит" for k in range(depth.size)]
    out = dh.write_table(engine, set_id, dh.TableData(name=name, kind="assay", holes=holes, from_m=depth.tolist(),
                                                      to_m=(depth + 2.0).tolist(),
                                                      columns=[dh.ColumnSpec("AU", "number", "г/т", "component"),
                                                               dh.ColumnSpec("ROCK", "text")],
                                                      values=[values, rock]), job_id=None, tick=noop)  # fmt: skip
    return int(out["table_id"])


@pytest.fixture()
def core(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES

    if MODULE not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", MODULE)
    importlib.import_module(MODULE)
    assert pclient.post(API + "/projects", json={"name": "Вариография"}).status_code == 200
    engine = projects_of(pclient.core).require_current().engine
    with dialect.write_tx(engine) as conn:
        small = dh.create_set(conn, "Скважины")
        big = dh.create_set(conn, "Большой набор")
    pclient.small = (small, grid_table(engine, small, "Пробы", side=4, per_hole=30, gaps=True, seed=7))
    pclient.big = (big, grid_table(engine, big, "Композиты", side=10, per_hole=210, gaps=False, seed=3))
    return pclient


def job_of(c: Any, body: dict[str, Any]) -> dict[str, Any]:
    r = c.post(GS + "/experimental", json=body)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"source", "id"}
    return wait_job(c, r.json()["source"], r.json()["id"], timeout=240)  # type: ignore[no-any-return]


def build(c: Any) -> dict[str, Any]:
    set_id, table = c.small
    base = {"set_id": set_id, "table": str(table), "field": "AU"}
    axes = {**base, "axes": {"ang1": 30, "ang2": 0, "ang3": 0}, "nlag": 6, "min_pairs": 800}
    done = job_of(c, axes)
    assert done["status"] == "done", done
    nscore = {**base, "transform": "nscore", "nlag": 4, "downhole": False,
              "directions": [{"az": 0, "plunge": 0}, {"az": 90, "plunge": 0, "az_tol": 45}]}  # fmt: skip
    ns = job_of(c, nscore)
    assert ns["status"] == "done", ns
    failed = job_of(c, {**base, "domain": {"field": "ROCK", "values": ["нет такой"]}})
    assert failed["status"] == "failed", failed
    pv = c.post(GS + "/preview", json=axes)
    assert pv.status_code == 200, pv.text
    big_set, big_table = c.big
    sampled = c.post(GS + "/preview", json={"set_id": big_set, "table": str(big_table), "field": "AU",
                                            "axes": {"ang1": 0, "ang2": 0, "ang3": 0}, "nlag": 5})  # fmt: skip
    assert sampled.status_code == 200, sampled.text
    refused = c.post(GS + "/preview", json={**base, "domain": {"field": "ROCK", "values": ["нет такой"]}})
    assert refused.status_code == 422, refused.text
    bad_param = c.post(GS + "/preview", json={**base, "nlag": 0})
    assert bad_param.status_code == 422, bad_param.text
    return {
        "rules": "ответы настоящего ядра: job.result, job.error, POST /preview, 422 (test_vario_window_answers.py)",
        "bodies": {"axes": axes, "nscore": nscore},
        "job_axes": done["result"],
        "job_nscore": ns["result"],
        "job_failed": {"status": failed["status"], "error": failed["error"]},
        "preview": pv.json(),
        "preview_sampled": sampled.json(),
        "preview_refused": {"status": refused.status_code, "body": refused.json()},
        "preview_bad_param": {"status": bad_param.status_code, "body": bad_param.json()},
    }


def masked(x: Any, path: str = "") -> Any:
    """The key — 64 hex digits; the stages — their names; other numbers to 1e−9 relative (threads, libm)."""
    if isinstance(x, dict):
        out: dict[str, Any] = {}
        for k, v in x.items():
            if k == "key" and isinstance(v, str):
                out[k] = len(v) == 64 and set(v) <= set("0123456789abcdef")
            elif k == "stages":
                out[k] = sorted(v)
            else:
                out[k] = masked(v, f"{path}.{k}")
        return out
    if isinstance(x, list):
        return [masked(v, path) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}")
    return x


def test_window_answers_are_what_the_core_answers(core) -> None:  # type: ignore[no-untyped-def]
    built = json.loads(json.dumps(build(core), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert masked(stored) == masked(built)
    # what the window relies on
    job = stored["job_axes"]
    assert job["fresh"] is False
    assert set(job["stages"]) == {"read", "trajectories", "pairs", "downhole", "write", "total"}
    assert [d["axis"] for d in job["directions"]] == [1, 2, 3] and job["downhole"] is not None
    assert job["sampled"] is None and job["counts"]["missing"] > 0 and job["warnings"] == []
    axis1 = job["directions"][0]
    assert None in axis1["gamma"] and 0 in axis1["n"]  # a lag without pairs: null, never 0
    assert any(f and n > 0 for f, n in zip(axis1["few"], axis1["n"], strict=True)) and not all(axis1["few"])
    assert all((g is None) == (n == 0) for g, n in zip(axis1["gamma"], axis1["n"], strict=True))
    ns = stored["job_nscore"]
    assert ns["transform"] == "nscore" and ns["sigma2"] == 1.0 and ns["sigma2_z"] != 1.0 and ns["downhole"] is None
    assert [d["axis"] for d in ns["directions"]] == [None, None] and "nscore" in ns["stages"]
    assert set(stored["job_failed"]["error"]) >= {"code", "message", "hint"}
    pv = stored["preview"]
    assert pv["key"] is None and pv["sampled"] is None and "fresh" not in pv and "stages" not in pv
    assert pv["directions"] == job["directions"]
    big = stored["preview_sampled"]
    assert big["sampled"] == {"n": 20_000, "of": 21_000, "seed": big["params"]["seed"]} and big["key"] is None
    ref = stored["preview_refused"]["body"]
    assert ref["code"] == "no_values" and ref["counts"]["used"] == 0 and ref["hint"]
    assert stored["preview_bad_param"]["body"]["param"] == "nlag"
    assert all(math.isfinite(v) for v in _numbers(stored))


def _numbers(x: Any) -> Any:
    if isinstance(x, dict | list):
        for v in x.values() if isinstance(x, dict) else x:
            yield from _numbers(v)
    elif isinstance(x, int | float) and not isinstance(x, bool):
        yield float(x)
