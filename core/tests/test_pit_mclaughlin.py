"""McLaughlin (MineLib) as a block model of BF (подшаг 21.13; ТЗ 7.35 Д-21, 8.11; ADR 0028 finding 7, decision 30;
questions № 114, № 116): the OMF 2 of Incline is read in the tests only (`tests/ref/mclaughlin.py`: the product
refuses this dialect), the model and the scenario are what `tests/data/pit/mclaughlin.json` says; and — a `slow` test,
off by default (`BF_PIT_SLOW=1`) — HPF = OR-Tools on the real 2,1 million blocks of rock (the graph of the 45° template,
K = 8), the cones change nothing."""

from __future__ import annotations

import os

import numpy as np
import pytest

from bf_core.mine import flow_ref as F
from bf_core.mine import graph as G
from bf_core.mine import hpf as H
from tests.ref import mclaughlin as M

pytestmark = pytest.mark.skipif(not M.OMF.is_file(), reason="файл McLaughlin_tutorial.omf не найден (tests/data/ext)")


@pytest.fixture(scope="module")
def model():
    return M.read_omf2()


@pytest.fixture(scope="module")
def sc():
    return M.scenario()


def test_the_model_is_what_the_scenario_says(model, sc):
    M.sanity(model)
    assert M.describe(model) == sc["model"]
    assert model.dims == (140, 296, 68) and model.blocks == 2_817_920 and model.rock_names == ("rock", "air")
    assert model.au.dtype == np.float64 and model.density.dtype == np.float64
    air = model.air.reshape(68, 296, 140)
    assert not air[0].any() and air[-1].any()  # the air is above the relief: k = 0 is the bottom
    assert not (air[:-1] & ~air[1:]).any()  # a column: rock, then air — never air under rock


def test_the_scenario_is_the_one_of_incline(sc):
    assert sc["costs"] == {"ore_haulage": 2.1, "waste_haulage": 1.21, "process": 20.9}
    assert sc["recovery"] == 0.84 and sc["slope_angle"] == 45.0 and sc["template_k"] == 8
    r = M.rfs(sc)
    assert len(r) == sc["revenue_factor"]["count"] == 61 and r[0] == 0.2 and r[-1] == 1.7
    assert np.allclose(np.diff(r), 0.025)
    assert "допущение" in sc["assumptions"]["status"] and "116" in sc["assumptions"]["question"]


def test_the_values_of_the_blocks(model, sc):
    lo, mid, hi = (M.cell_values(model, sc, rf) for rf in (0.2, 1.0, 1.7))
    air = model.air
    assert not lo[air].any() and not hi[air].any() and lo.dtype == np.float64
    assert bool(np.all(hi >= mid)) and bool(np.all(mid >= lo))  # a block never loses value when RF grows
    assert (lo > 0).sum() < (mid > 0).sum() < (hi > 0).sum()
    # waste: the cost of the haulage of the tonnes; ore: the better of the two
    t = model.size[0] * model.size[1] * model.size[2] * model.density
    waste = (model.au == 0) & ~air
    assert np.allclose(mid[waste], -t[waste] * sc["costs"]["waste_haulage"])
    price_g = sc["assumptions"]["price_usd_per_troy_oz"] / M.GRAMS_PER_TROY_OZ
    j = int(np.argmax(model.au))
    want = t[j] * (model.au[j] * 0.84 * price_g - 20.9 - 2.1)
    assert mid[j] == pytest.approx(want, rel=1e-12) and mid[j] > 0


def test_the_template_of_the_model(model, sc):
    tab = M.offsets(model, sc)
    assert tab.dtype == np.int32 and tab.ndim == 2 and tab.shape[1] == 3 and len(tab) >= 9
    assert (tab[:, 2] >= 1).all()  # every predecessor is at least one level up


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("BF_PIT_SLOW") != "1", reason="медленный тест: BF_PIT_SLOW=1 (≈ 20 с, ≈ 1 ГБ)")
def test_hpf_equals_ortools_on_mclaughlin_and_the_cones_change_nothing(model, sc):
    for rf in sc["check_rf"]:
        g, ids = M.graph(model, sc, rf)  # the cones of the ore: the graph OR-Tools can take
        assert 0 < g.n < int((~model.air).sum())
        h, o = H.solve(g), F.solve(g)
        assert np.array_equal(h, o), f"RF {rf}: HPF and OR-Tools differ"
        assert F.optimum(g.value, h) == F.optimum(g.value, o) > 0 and G.is_closed(g, h)
        if rf == sc["check_rf"][0]:
            full, _ = M.graph(model, sc, rf, cones=False)  # the whole 2,1 million: only HPF takes it
            hf = H.solve(full)
            back = np.zeros(full.n, dtype=bool)
            back[ids] = h
            assert np.array_equal(hf, back), "the cones changed the answer"
            assert G.is_closed(full, hf)
