"""The inspector for every method of the plan (ТЗ 7.35 Д-30; Э18, 18.Д3): NN, IDW, SK, OK and IK (the median model) on
the model of the T8.8-7 kind — the recalculation of a block equals bit for bit the fields `kernel.run` wrote, the
weights of the selected samples are there for IDW, SK, OK (NN — one sample, weight 1; IK has none), and the rows of one
model are listed together (the inspector without `params_id`)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from bf_core.geostat.estimate import inspect as ins
from bf_core.geostat.estimate import inspect_spot as isp
from tests import estinspect as ei
from tests.estinspect import Ctx, prepared

METHODS: dict[str, dict[str, Any]] = {
    "NN": {"method": {"kind": "nn"}},
    "IDW": {"method": {"kind": "idw", "power": 2}},
    "SK": {"method": {"kind": "sk"}},
    "IK": {"method": {"kind": "ik", "ik": {"thresholds": [0.8, 1.4, 2.5], "models": "median"}}, "disc": [2, 2, 2]},
}


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Ctx, dict[str, tuple[int, dict[str, Any]]]]]:
    for c in ei.ctx_of(tmp_path_factory.mktemp("inspect_methods")):
        rows: dict[str, tuple[int, dict[str, np.ndarray]]] = {"OK": (c.eid, c.cols)}
        for name, over in METHODS.items():
            eid = ei.stored_world(c.w, name=f"{name} · руда", outputs={"prefix": f"{name}_"}, **over)
            rows[name] = (eid, ei.write_fields(c.w, eid))
        yield c, rows


@pytest.mark.parametrize("method", ["NN", "IDW", "SK", "OK", "IK"])
def test_a_block_is_recalculated_bit_for_bit_by_every_method(
    world: tuple[Ctx, dict[str, tuple[int, dict[str, Any]]]], method: str
) -> None:
    c, rows = world
    eid, cols = rows[method]
    prefix = "AU_OK_" if method == "OK" else f"{method}_"
    model, row, entry, fresh = prepared(c, eid)
    status = cols[prefix + "STATUS"]
    rng = np.random.default_rng(len(method))
    pick = sorted(set(rng.choice(model.rows, 160, replace=False).tolist()) | set(np.flatnonzero(status == 2)[:5]))
    seen = {"est": 0, "weights": 0}
    for r in pick:
        got = ins.one_row(model, isp.locate(model, {"row": int(r)}), row, entry, fresh, None, chosen=True)
        assert got is not None and got["method"] == ("ok" if method == "OK" else method.lower())
        assert got["status"] == int(status[r])
        if got["status"] == 1:
            continue
        assert got["same"] is True, (method, r, [f["name"] for f in got["fields"] if f["same"] is False])
        if got["status"] != 0:
            continue
        seen["est"] += 1
        sel = [x for x in got["candidates"] if x["reason"] in ("selected", "hy_capped")]
        assert len(sel) == got["values"]["NS"]
        lam = [x["lambda"] for x in sel]
        if method == "IK":  # the indicator system has its own weights per threshold: none are shown
            assert lam == [None] * len(sel)
            assert {"P1", "P2", "P3", "ETYPE"} <= set(got["values"])
            assert got["values"]["P1"] <= got["values"]["P2"] <= got["values"]["P3"]
            continue
        seen["weights"] += 1
        rest = 0.0  # SK: the rest of the weight, 1 − Σλ, goes to the mean m
        if method == "SK":
            rest = (1.0 - sum(lam)) * entry.rk.mean
        else:
            assert abs(sum(lam) - 1.0) < 1e-9
        est = rest + sum(w * x["z"] for w, x in zip(lam, sel, strict=True))
        assert abs(est - got["values"]["EST"]) <= 1e-9 * max(1.0, abs(got["values"]["EST"])), (method, r)
        if method == "NN":  # the search selects the neighbours, the nearest one counts: weight 1, the others 0
            assert lam[0] == 1.0 and all(w == 0.0 for w in lam[1:]) and got["values"]["EST"] == sel[0]["z"]
        if method == "IDW":
            assert all(w > 0 for w in lam)
    assert seen["est"] > 60, seen


def test_the_rows_of_one_model_are_listed_together_by_the_names(
    world: tuple[Ctx, dict[str, tuple[int, dict[str, Any]]]],
) -> None:
    c, rows = world
    r = int(np.flatnonzero(rows["IDW"][1]["IDW_STATUS"] == 0)[100])
    out = c.run(row=r)
    assert [x["name"] for x in out["results"]] == sorted([x["name"] for x in out["results"]], key=str.casefold)
    assert {x["prefix"] for x in out["results"]} == {"AU_OK_", "NN_", "IDW_", "SK_", "IK_"}
    assert all(x["same"] is True for x in out["results"] if x["status"] != 1)
    one = c.run(row=r, params_id=rows["SK"][0])
    assert [x["prefix"] for x in one["results"]] == ["SK_"]
    json_ok = ins.jsonable(one)
    assert json_ok == one  # already plain
