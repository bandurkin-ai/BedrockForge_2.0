"""The answers of the core the tab «Статистика» is read against (подшаг 16.26; ТЗ 7.29; ADR 0022 §11, решение 21;
e16-api.md «Вкладка «Статистика»»): `POST /stats/tab/variogram` (four directions with a model of BF; omni),
`/variogram-model` (no data), `/map`, `/local` (with a threshold), a table with missing values (counted, never 0),
20 001 intervals (422 `too_many_samples`) and a wrong parameter (422 `bad_param`) — through the real routes on the
demo project. The file `tests/data/stats/tab_vario_answers.json` is read by vitest (`ui/test/stats-vario.test.ts`).
Regenerate: `BF_REGEN_STATS_TAB_VARIO=1 uv run pytest -q tests/test_stats_tab_vario_answers.py`. Numbers are
compared to 1e−9 relative (threads, libm)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.drillholes import store as dh
from tests.test_stats_tab_vario_api import MODEL, T, demo  # noqa: F401 — the fixture

DATA = Path(__file__).parent / "data" / "stats" / "tab_vario_answers.json"
REGEN = os.environ.get("BF_REGEN_STATS_TAB_VARIO") == "1"


def _post(c: Any, path: str, body: dict[str, Any]) -> Any:
    r = c.post(f"{T}/{path}", json=body)
    return {"status": r.status_code, "body": r.json()}


def _ok(c: Any, path: str, body: dict[str, Any]) -> Any:
    out = _post(c, path, body)
    assert out["status"] == 200, out
    return out["body"]


def build(c: Any, set_id: int, project: Any) -> dict[str, Any]:
    from bf_core.stats import source

    holes = source.read(project, set_id, None, []).holes
    n = 120
    k = np.arange(n)
    values: list[Any] = [None if i % 4 == 0 else float(i % 7 + 1) for i in range(n)]
    dh.write_table(project.engine, set_id, dh.TableData(
        name="Редкие", kind="interval", holes=[holes[i % 12] for i in range(n)], from_m=((k // 12) * 2.0).tolist(),
        to_m=((k // 12) * 2.0 + 2.0).tolist(), columns=[dh.ColumnSpec("V", "number", None, "component")],
        values=[values]), job_id=None, tick=lambda _p, _m: None)  # fmt: skip
    m = 20_001
    per = m // len(holes) + 1
    kk = np.arange(m)
    dh.write_table(project.engine, set_id, dh.TableData(
        name="Мелкие", kind="interval", holes=[holes[i // per] for i in range(m)], from_m=((kk % per) * 0.5).tolist(),
        to_m=((kk % per) * 0.5 + 0.5).tolist(), columns=[dh.ColumnSpec("V", "number", None, "component")],
        values=[(kk % 97 + 1.0).tolist()]), job_id=None, tick=lambda _p, _m: None)  # fmt: skip
    base = {"set_id": set_id, "table": None, "field": "AU"}
    bodies = {
        "variogram": {**base, "lag": 25, "nlag": 8, "directions": "four", "min_pairs": 10, "model": MODEL},
        "variogram_omni": {**base, "lag": 25},
        "variogram_model": {"nugget": 0.1, "structures": MODEL["structures"], "n": 11, "directions": "four"},
        "map": {**base, "cell": 20, "n": 6, "min_pairs": 3},
        "local": {**base, "grid": {"nx": 12, "ny": 10}, "k": 6, "realizations": 20, "seed": 11, "threshold": 0.5},
        "sparse": {"set_id": set_id, "table": "Редкие", "field": "V", "lag": 20},
        "gaps": {"set_id": set_id, "table": "Редкие", "field": "V", "lag": 2, "nlag": 15, "min_pairs": 2},
        "map_sparse": {"set_id": set_id, "table": "Редкие", "field": "V", "cell": 10, "n": 8, "min_pairs": 3},
        "local_tight": {**base, "grid": {"nx": 5, "ny": 4}, "realizations": 5, "seed": 3, "radius": 1},
    }
    return {
        "rules": "ответы настоящего ядра /stats/tab/{variogram, variogram-model, map, local}",
        "bodies": bodies,
        "variogram": _ok(c, "variogram", bodies["variogram"]),
        "variogram_omni": _ok(c, "variogram", bodies["variogram_omni"]),
        "variogram_model": _ok(c, "variogram-model", bodies["variogram_model"]),
        "map": _ok(c, "map", bodies["map"]),
        "local": _ok(c, "local", bodies["local"]),
        "sparse": _ok(c, "variogram", bodies["sparse"]),
        "gaps": _ok(c, "variogram", bodies["gaps"]),
        "map_sparse": _ok(c, "map", bodies["map_sparse"]),
        "local_tight": _ok(c, "local", bodies["local_tight"]),
        "too_many": _post(c, "map", {"set_id": set_id, "table": "Мелкие", "field": "V"}),
        "bad_param": _post(c, "local", {**base, "k": 65}),
        "model_no_h_max": _post(c, "variogram-model", {"nugget": 0.0, "structures": []}),
    }


def masked(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: masked(v) for k, v in x.items()}
    if isinstance(x, list):
        return [masked(v) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}")
    return x


def _numbers(x: Any) -> Any:
    if isinstance(x, dict | list):
        for v in x.values() if isinstance(x, dict) else x:
            yield from _numbers(v)
    elif isinstance(x, int | float) and not isinstance(x, bool):
        yield float(x)


def test_tab_vario_answers_are_what_the_core_answers(demo) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, project = demo
    built = json.loads(json.dumps(build(c, set_id, project), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert masked(stored) == masked(built)
    # what the tab relies on
    v = stored["variogram"]
    assert [d["az"] for d in v["directions"]] == [0.0, 45.0, 90.0, 135.0] and v["model"]["structures"][0]["it"]
    assert all(len(d["model_gamma"]) == v["nlag"] for d in v["directions"])
    assert all((g is None) == (n == 0) for d in v["directions"] for g, n in zip(d["gamma"], d["n"], strict=True))
    om = stored["variogram_omni"]
    assert om["model"] is None and len(om["directions"]) == 1 and om["directions"][0]["omni"] is True
    mp = stored["map"]
    assert len(mp["u"]) == len(mp["v"]) == 13 and len(mp["gamma"]) == 13 and len(mp["gamma"][0]) == 13
    ms = stored["map_sparse"]
    assert any(g is None for row in ms["gamma"] for g in row) and any(g is not None for row in ms["gamma"] for g in row)
    lt = stored["local_tight"]
    assert lt["empty"] > 0 and any(x is None for row in lt["mean"] for x in row) and lt["prob"] is None
    lo = stored["local"]
    assert len(lo["mean"]) == lo["grid"]["ny"] == len(lo["grid"]["y"]) and len(lo["mean"][0]) == lo["grid"]["nx"]
    assert lo["prob"] is not None and lo["seed"] == 11
    gaps = stored["gaps"]["directions"][0]
    assert None in gaps["gamma"] and 0 in gaps["n"] and any(g is not None for g in gaps["gamma"])
    sp = stored["sparse"]
    assert sp["counts"]["missing"] == 30 and sp["counts"]["used"] == sp["count"] == 90
    assert stored["too_many"]["status"] == 422 and stored["too_many"]["body"]["code"] == "too_many_samples"
    assert stored["bad_param"]["body"]["param"] == "k"
    assert stored["model_no_h_max"]["body"]["param"] == "h_max"
    assert all(math.isfinite(x) for x in _numbers(stored))
