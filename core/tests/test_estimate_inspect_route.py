"""The route `POST /api/v1/geostat/estimate/inspect` (ТЗ 7.35 Д-30; Э18, 18.Д3): the answer of the function with the
errors of the core in Russian, ≤ 200 ms warm, the overtaking of a request of a channel, and the fixture of the window
«Блок · i, j, k» (`BF_REGEN_INSPECT=1`)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate import inspect as ins
from bf_core.geostat.estimate.spec import parse_inspect
from tests import estinspect as ei
from tests.estinspect import Ctx

DATA = Path(__file__).parent / "data" / "estimate" / "inspect_answers.json"
REGEN = os.environ.get("BF_REGEN_INSPECT") == "1"
API = "/api/v1"
LIMIT_S = 0.2


@pytest.fixture()
def fresh_ctx(tmp_path: Path) -> Iterator[Ctx]:
    yield from ei.ctx_of(tmp_path)


@pytest.fixture()
def routed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    from fastapi.testclient import TestClient

    from bf_core.api.app import CoreState, create_app
    from bf_core.geostat import router as gr
    from bf_core.geostat.estimate.routes_inspect import router as r

    if not any(getattr(x, "path", "") == "/geostat/estimate/inspect" for x in gr.router.routes):
        monkeypatch.setattr(gr.router, "routes", list(gr.router.routes))
        gr.router.include_router(r)
    monkeypatch.setenv("BF_LOCAL_DIR", str(tmp_path / "local"))
    token = "t" * 32
    state = CoreState(token=token, projects_dir=str(tmp_path / "Проекты"), data_dir=str(tmp_path / "roaming"))
    with TestClient(create_app(state), base_url="http://127.0.0.1:5555", headers={"X-BF-Token": token}) as c:
        c.core = state  # type: ignore[attr-defined]
        yield c


def routed_world(client: Any) -> Ctx:
    from bf_core.projects.service import projects_of

    assert client.post(f"{API}/projects", json={"name": "Инспектор"}).status_code == 200
    project = projects_of(client.core).require_current()
    w = ei.world_of(project)
    eid = ei.stored_world(w)
    return Ctx(w, eid, ei.write_fields(w, eid))


def test_the_route_answers_as_the_function_does_with_the_errors_of_the_core(routed: Any) -> None:
    c = routed_world(routed)
    ins.forget()
    url = f"{API}/geostat/estimate/inspect"
    r = int(np.flatnonzero(c.cols["AU_OK_STATUS"] == 0)[40])
    body = {"model_id": c.w.big, "row": r, "channel": "inspector", "seq": 1}
    cold = routed.post(url, json=body)
    assert cold.status_code == 200, cold.text
    here = c.run(row=r)
    assert cold.json() == json.loads(json.dumps(here))
    times = []
    for seq in range(2, 12):
        t0 = time.perf_counter()
        warm = routed.post(url, json={**body, "seq": seq, "row": r + seq})
        times.append(time.perf_counter() - t0)
        assert warm.status_code == 200 and warm.json()["block"]["row"] == r + seq
    print(f"\n[inspect] POST: median {1000 * float(np.median(times)):.1f} ms, max {1000 * max(times):.1f} ms")
    # a request is 20–50 ms; one in ten may meet a pause of the machine (the garbage collector, the disk)
    assert float(np.percentile(times, 90)) <= LIMIT_S and max(times) <= 3 * LIMIT_S, times
    for bad, status, code, param in [
        ({"model_id": c.w.big, "row": 10_000_000}, 422, "bad_param", "row"),
        ({"model_id": c.w.big}, 422, "bad_param", "block"),
        ({"model_id": c.w.big, "row": 1, "channel": ""}, 422, "bad_param", "channel"),
        ({"model_id": c.w.big, "row": 1, "seq": -1}, 422, "bad_param", "seq"),
        ({"model_id": 999_999, "row": 1}, 404, "block_model_not_found", None),
        ({"model_id": c.w.big, "row": 1, "params_id": 999_999}, 404, "estimation_not_found", None),
        ({"model_id": c.w.model, "row": 1}, 409, "not_estimated", "model_id"),
    ]:
        got = routed.post(url, json=bad)
        assert got.status_code == status and got.json()["code"] == code, (bad, got.text)
        assert got.json()["message"] and got.json()["hint"] and (param is None or got.json().get("param") == param)
    assert routed.post(url, json=[1, 2]).status_code == 422


def test_an_overtaken_request_answers_409_superseded(routed: Any) -> None:
    from bf_core.blocks import viz_spec as vs

    c = routed_world(routed)
    url = f"{API}/geostat/estimate/inspect"
    # a newer request of the channel is running (seq 9): the older one (seq 3) is refused at once
    tok = vs.GATE.enter("inspector", 9)
    try:
        got = routed.post(url, json={"model_id": c.w.big, "row": 5, "channel": "inspector", "seq": 3})
        assert got.status_code == 409 and got.json()["code"] == "superseded"
    finally:
        vs.GATE.leave(tok)
    assert routed.post(url, json={"model_id": c.w.big, "row": 5, "channel": "inspector", "seq": 3}).status_code == 200


# --- the fixture of the window --------------------------------------------------------------------------------------


def rounded(x: Any) -> Any:
    if isinstance(x, dict):  # the hash of the copy of the variogram model changes with every build of the world
        return {k: "<sha256>" if k in ("sha", "sha256") else rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}")
    return x


def close(a: Any, b: Any, path: str = "") -> None:
    """The same structure, the same strings and integers, the numbers to 1e−7 relative (another machine's BLAS)."""
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), path
        for k in a:
            close(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            close(x, y, f"{path}/{i}")
    elif isinstance(a, float) and isinstance(b, float):
        assert a == pytest.approx(b, rel=1e-7, abs=1e-9), (path, a, b)
    else:
        assert a == b, (path, a, b)


def test_the_answers_the_window_is_read_against(fresh_ctx: Ctx) -> None:
    """Real answers of the core for the window «Блок · i, j, k» (`ui/test/…/inspect_answers.json`): a block of the
    first pass, a block of the last one, a block with too few samples, a block out of the domain asked for by its
    parameters, a block after an edit of the table of the samples, and the refusals. Regenerate with
    `BF_REGEN_INSPECT=1 uv run pytest -q tests/test_estimate_inspect.py -k the_answers_the_window`."""
    c = fresh_ctx
    status, pas = c.cols["AU_OK_STATUS"], c.cols["AU_OK_PASS"]
    ok = np.flatnonzero(status == 0)
    pick = {
        "pass_1": int(ok[pas[ok] == 1][50]), "pass_3": int(ok[pas[ok] == 3][5]),
        "few_samples": int(np.flatnonzero(status == 2)[0]), "out_of_domain": int(np.flatnonzero(status == 1)[7]),
    }  # fmt: skip
    got: dict[str, Any] = {"rows": pick, "answers": {}, "errors": {}}
    for name, r in pick.items():
        got["answers"][name] = c.run(row=r, params_id=c.eid)
    first = got["answers"]["pass_1"]["results"][0]
    used = next(x for x in first["candidates"] if x["reason"] == "selected")
    ei.edit_sample(c.w, 12.5, row_id=used["row_id"])
    got["answers"]["changed"] = c.run(row=pick["pass_1"], params_id=c.eid)
    asked = {
        "no_block": {"model_id": c.w.big},
        "no_such_row": {"model_id": c.w.big, "row": 10_000_000},
        "no_model": {"model_id": 999_999, "row": 1},
        "no_params": {"model_id": c.w.big, "row": 1, "params_id": 999_999},
    }
    for name, body in asked.items():
        with pytest.raises(ApiError) as e:
            ins.run(c.project, parse_inspect(body))
        got["errors"][name] = {
            "status": e.value.status,
            "code": e.value.code,
            "message": e.value.message,
            "hint": e.value.hint,
            "param": e.value.extra.get("param") if e.value.extra else None,
        }
    got = rounded(json.loads(json.dumps(got)))
    # the shape the window relies on
    assert got["answers"]["changed"]["results"][0]["warning"] == "Параметры или входы изменились"
    assert got["answers"]["pass_1"]["results"][0]["warning"] is None
    if REGEN:
        DATA.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    saved = json.loads(DATA.read_text(encoding="utf-8"))
    close(rounded(got), saved)
