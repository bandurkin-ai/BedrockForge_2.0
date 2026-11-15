"""Q-Q plot: quantiles against numpy, shifts, Kolmogorov–Smirnov distance, distribution fits, API."""

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.models import Assay, Component
from app.stats.qq import MAX_POINTS, ks_distance, probabilities, sample_vs_distribution, sample_vs_sample
from tests.helpers import new_set


def test_unweighted_quantiles_match_numpy_and_a_shift_is_exact():
    rng = np.random.default_rng(2)
    x = rng.lognormal(0, 1, 400)
    ones = np.ones(400)
    r = sample_vs_sample(x, ones, x + 1.0, ones, 99)
    qx = np.array([p[0] for p in r["points"]])
    qy = np.array([p[1] for p in r["points"]])
    assert np.allclose(qx, np.percentile(x, probabilities(99), method="hazen"), rtol=1e-12)
    assert np.allclose(qy - qx, 1.0, atol=1e-12)  # a shift moves every quantile by the same amount
    assert r["points"][49][2] == pytest.approx(50.0)
    assert r["extremes"]["y"][0] == pytest.approx(x.min() + 1)


def test_ks_distance():
    a = np.array([1.0, 2, 3, 4])
    w = np.ones(4)
    assert ks_distance(a, w, a, w) == 0
    assert ks_distance(a, w, a + 10, w) == 1.0
    assert ks_distance(a, w, np.array([2.0, 3, 4, 5]), w) == pytest.approx(0.25)


@pytest.mark.parametrize("dist", ["normal", "lognormal"])
def test_fitted_distribution_is_a_straight_line(dist):
    rng = np.random.default_rng(4)
    v = rng.normal(5, 2, 1500) if dist == "normal" else rng.lognormal(0.5, 0.8, 1500)
    r = sample_vs_distribution(v, np.ones(len(v)), dist)
    assert r["ppcc"] > 0.998
    t = np.log(v) if dist == "lognormal" else v
    assert r["distribution"]["mu"] == pytest.approx(t.mean()) and r["distribution"]["sigma"] == pytest.approx(t.std())
    # A uniform sample does not fit a normal distribution as well.
    u = sample_vs_distribution(rng.uniform(0, 1, 1500), np.ones(1500), "normal")
    assert u["ppcc"] < r["ppcc"]


def test_large_samples_are_thinned_keeping_the_ends():
    v = np.random.default_rng(5).normal(0, 1, 10_000)
    r = sample_vs_distribution(v, np.ones(len(v)), "normal")
    assert len(r["points"]) == MAX_POINTS
    assert r["points"][0][1] == v.min() and r["points"][-1][1] == v.max()


def test_lognormal_drops_nonpositive_values():
    r = sample_vs_distribution(np.array([-1.0, 0, 1, 2, 4, 8]), np.ones(6), "lognormal")
    assert r["distribution"]["dropped_nonpositive"] == 2 and len(r["points"]) == 4


@pytest.fixture
def two_sets(db_client, db_engine):
    a = new_set(db_client, "Проект Q-Q", "Скважины A")
    project = db_client.get("/api/projects").json()[0]["id"]
    b = db_client.post(f"/api/projects/{project}/sets", json={"name": "Скважины B"}).json()["id"]
    rng = np.random.default_rng(7)
    with Session(db_engine) as s:
        for sid, scale in ((a, 1.0), (b, 2.0)):
            for i, value in enumerate(rng.lognormal(0, 0.5, 200) * scale):
                s.add(Assay(set_id=sid, hole_id=f"H{i % 10}", from_m=float(i), to_m=float(i) + 1 + (i % 2),
                            analytes={"AU": float(value)}))
            s.add(Component(set_id=sid, name="AU", unit="g/t"))
        s.commit()
    return db_client, a, b


def test_qq_api_two_sets_and_theory(two_sets):
    client, a, b = two_sets
    r = client.post("/api/stats/qq", json={
        "x": {"set_id": a, "field": "comp:AU", "weight": "length"},
        "y": {"kind": "sample", "set_id": b, "field": "comp:AU", "weight": "length"},
        "quantiles": 19,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "sample" and len(body["points"]) == 19
    slope = np.median([p[1] / p[0] for p in body["points"]])
    assert 1.6 < slope < 2.5  # set B is set A's grades times two
    assert body["x"]["weighted"] and body["y"]["counts"]["used"] == 200 and body["ks"] > 0.3

    t = client.post("/api/stats/qq", json={"x": {"set_id": a, "field": "comp:AU"}, "y": {"kind": "lognormal"}}).json()
    assert t["mode"] == "theory" and t["ppcc"] > 0.98 and len(t["points"]) == 200

    bad = client.post("/api/stats/qq", json={"x": {"set_id": a, "field": "comp:AU"}, "y": {"kind": "gamma"}})
    assert bad.status_code == 422
    missing = client.post("/api/stats/qq", json={"x": {"set_id": 99999, "field": "comp:AU"}, "y": {"kind": "normal"}})
    assert missing.status_code == 404
