import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.models import Assay, Collar, Component
from tests.helpers import new_set


@pytest.fixture
def seeded(db_client, db_engine):
    """Vertical holes: a regular 6x6 grid (spacing 100) plus a dense cluster of high grades."""
    sid = new_set(db_client, "Stats")
    rng = np.random.default_rng(5)
    holes = [(100.0 * i, 100.0 * j) for i in range(6) for j in range(6)]
    holes += [(150.0 + rng.uniform(-8, 8), 150.0 + rng.uniform(-8, 8)) for _ in range(30)]
    with Session(db_engine) as s:
        for k, (x, y) in enumerate(holes):
            hole = f"H{k:03d}"
            s.add(Collar(set_id=sid, hole_id=hole, x=x, y=y, z=100.0, td=20.0, azimuth=0.0, dip=-90.0))
            near = np.hypot(x - 150, y - 150) < 20
            for i in range(2):
                value = float(rng.lognormal(0.0, 0.3) * (4.0 if near else 1.0))
                s.add(Assay(set_id=sid, hole_id=hole, from_m=10.0 * i, to_m=10.0 * (i + 1),
                            analytes={"AU": value, "CU": 1.0 if i == 0 else None}))
        s.add_all([Component(set_id=sid, name="AU", unit="g/t"), Component(set_id=sid, name="CU", unit="%")])
        s.commit()
    return db_client, sid


def url(sid, tail):
    return f"/api/sets/{sid}/stats/{tail}"


def test_declustering_cell_reduces_mean_and_reports_curve(seeded):
    client, sid = seeded

    r = client.post(url(sid, "declustering"), json={"component": "AU", "cmin": 10, "cmax": 300,
                                                   "ncell": 15, "noff": 4, "include_weights": True})

    assert r.status_code == 200, r.text
    b = r.json()
    assert b["count"] == 132 and b["method"] == "cell"
    assert b["declustered_mean"] < b["naive_mean"]                       # clustered highs are down-weighted
    assert len(b["cell_sizes"]) == len(b["means"]) == 17 and b["cell_sizes"][0] == 0.0
    assert b["best_cell_size"] is not None and 10 <= b["best_cell_size"] <= 300
    assert sum(b["weights"]) == pytest.approx(132) and len(b["assay_ids"]) == 132
    assert b["weight_min"] < 1 < b["weight_max"]


def test_declustering_polygonal_and_defaults(seeded):
    client, sid = seeded

    r = client.post(url(sid, "declustering"), json={"component": "AU", "method": "polygonal", "nx": 80, "ny": 80})

    assert r.status_code == 200, r.text
    b = r.json()
    assert b["declustered_mean"] < b["naive_mean"] and b["cell_sizes"] is None
    # default cell sizes come from the data extent
    d = client.post(url(sid, "declustering"), json={"component": "AU"}).json()
    assert d["cmin"] == pytest.approx(0.02 * 500, rel=0.05) and d["cmax"] == pytest.approx(0.5 * 500, rel=0.05)


def test_nscore_table_and_sample_scores(seeded):
    client, sid = seeded

    r = client.post(url(sid, "nscore"), json={"component": "AU"})

    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["values"]) == len(b["scores"]) == len(b["sample_scores"]) == b["count"] == 132
    assert b["values"] == sorted(b["values"]) and b["scores"] == sorted(b["scores"])
    assert abs(np.mean(b["sample_scores"])) < 0.01
    weighted = client.post(url(sid, "nscore"), json={"component": "AU", "declus": {"method": "polygonal"}}).json()
    assert weighted["scores"] != b["scores"]                             # weights change the table


def test_backtr_round_trips_sample_scores(seeded):
    client, sid = seeded
    ns = client.post(url(sid, "nscore"), json={"component": "AU"}).json()

    r = client.post(url(sid, "backtr"), json={"component": "AU", "scores": ns["sample_scores"][:20]})

    assert r.status_code == 200, r.text
    original = [v for v in ns["values"]]
    back = r.json()["values"]
    assert len(back) == 20 and all(min(original) <= v <= max(original) for v in back)
    # a table score maps back to its value
    r = client.post(url(sid, "backtr"), json={"component": "AU", "scores": ns["scores"][10:13]})
    assert r.json()["values"] == pytest.approx(ns["values"][10:13])


def test_backtr_tails_and_validation(seeded):
    client, sid = seeded
    top = client.post(url(sid, "nscore"), json={"component": "AU"}).json()["values"][-1]

    clamp = client.post(url(sid, "backtr"), json={"component": "AU", "scores": [6.0], "utail": None}).json()
    lin = client.post(url(sid, "backtr"), json={"component": "AU", "scores": [6.0], "zmax": top * 3}).json()
    hyp = client.post(url(sid, "backtr"), json={"component": "AU", "scores": [6.0], "utail": "hyperbolic",
                                                "utpar": 1.5}).json()

    assert clamp["values"] == [top] and lin["values"][0] > top and hyp["values"][0] > top
    bad = client.post(url(sid, "backtr"), json={"component": "AU", "scores": [0.0], "ltail": "power"})
    assert bad.status_code == 422                                        # power tail needs ltpar
    assert client.post(url(sid, "backtr"), json={"component": "AU", "scores": []}).status_code == 422


def test_variogram_directions_model_and_nan_json(seeded):
    client, sid = seeded

    r = client.post(url(sid, "variogram"), json={
        "component": "AU", "lag": 50, "nlag": 12, "directions": [{}, {"azimuth": 90, "azimuth_tol": 20}],
        "model": {"nug": 0.1, "structures": [{"it": "spherical", "cc": 0.5, "hmaj": 200}]}})

    assert r.status_code == 200, r.text
    b = r.json()
    assert b["count"] == 132 and b["lags"][:2] == [50.0, 100.0] and len(b["directions"]) == 2
    omni, east = b["directions"]
    assert len(omni["gamma"]) == len(omni["npairs"]) == len(omni["model_gamma"]) == 12
    assert sum(omni["npairs"]) > sum(east["npairs"]) > 0
    assert omni["model_gamma"][-1] == pytest.approx(0.6)                 # sill at hmaj
    assert None in east["gamma"] or None in omni["gamma"]                # empty lags are null, not NaN
    assert b["variance"] > 0


def test_variogram_standardize_divides_by_variance(seeded):
    client, sid = seeded
    plain = client.post(url(sid, "variogram"), json={"component": "AU", "lag": 60, "nlag": 4}).json()
    std = client.post(url(sid, "variogram"), json={"component": "AU", "lag": 60, "nlag": 4,
                                                   "standardize": True}).json()

    g, gs = plain["directions"][0]["gamma"], std["directions"][0]["gamma"]
    assert gs == pytest.approx([a / plain["variance"] for a in g])


def test_variogram_map_shape_axes_and_minnp(seeded):
    client, sid = seeded

    r = client.post(url(sid, "variogram-map"), json={"component": "AU", "nxlag": 4, "nylag": 3,
                                                    "dxlag": 50, "dylag": 50, "minnp": 5})

    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["gamma"]) == len(b["npairs"]) == 7 and len(b["gamma"][0]) == 9
    assert b["x"][0] == -200 and b["x"][-1] == 200 and b["y"][0] == -150
    assert b["npairs"][3][4] >= 132                                      # self pairs in the centre
    assert all((g is None) == (n < 5) for gr, nr in zip(b["gamma"], b["npairs"]) for g, n in zip(gr, nr))
    too_big = client.post(url(sid, "variogram-map"), json={"component": "AU", "nxlag": 100, "nylag": 100,
                                                          "dxlag": 1, "dylag": 1})
    assert too_big.status_code == 422


def test_model_curve_endpoint_needs_no_data(db_client):
    r = db_client.post("/api/stats/variogram-model", json={
        "model": {"nug": 0.1, "structures": [{"it": "exponential", "cc": 0.9, "hmaj": 100, "hmin": 50, "azi": 90}]},
        "lags": [0, 1, 100, 1000], "azimuth": 90})

    assert r.status_code == 200, r.text
    b = r.json()
    assert b["sill"] == pytest.approx(1.0) and b["gamma"][0] == 0.0 and b["cov"][0] == pytest.approx(1.0)
    assert b["gamma"][2] == pytest.approx(1.0 - 0.9 * np.exp(-3), rel=1e-9)   # major axis: practical range 100
    minor = db_client.post("/api/stats/variogram-model", json={
        "model": {"nug": 0.1, "structures": [{"it": "exponential", "cc": 0.9, "hmaj": 100, "hmin": 50, "azi": 90}]},
        "lags": [50], "azimuth": 0}).json()
    assert minor["gamma"][0] == pytest.approx(1.0 - 0.9 * np.exp(-3), rel=1e-9)  # minor axis range 50
    for bad in ({"model": {"structures": [{"it": "cubic", "cc": 1, "hmaj": 1}]}, "lags": [1]},
                {"model": {}, "lags": [-1]}, {"model": {}, "lags": []}):
        assert db_client.post("/api/stats/variogram-model", json=bad).status_code == 422


def test_errors_unknown_component_set_and_too_little_data(seeded, db_client, db_engine):
    client, sid = seeded

    assert client.post(url(sid, "nscore"), json={"component": "ZN"}).status_code == 404
    assert client.post(url(9999, "nscore"), json={"component": "AU"}).status_code == 404
    assert client.post(url(sid, "variogram"), json={"component": "AU"}).status_code == 422   # lag required
    assert client.post(url(sid, "declustering"), json={"component": "AU", "cmin": 50, "cmax": 10}).status_code == 422
    # CU has a value in only one interval per hole, still enough; an empty set is rejected cleanly
    empty = new_set(client, "Empty", "Пусто")
    with Session(db_engine) as s:
        s.add(Component(set_id=empty, name="AU", unit="g/t"))
        s.commit()
    r = client.post(url(empty, "variogram"), json={"component": "AU", "lag": 10})
    assert r.status_code == 422 and "AU" in r.json()["detail"]


# ---- uncertainty ---------------------------------------------------------
def test_bootstrap_interval_contains_observed_and_is_reproducible(seeded):
    client, sid = seeded
    body = {"component": "AU", "statistic": "mean", "n_boot": 400, "confidence": 0.9, "seed": 7}

    r = client.post(url(sid, "bootstrap"), json=body)

    assert r.status_code == 200, r.text
    b = r.json()
    assert b["count"] == 132 and b["n_boot"] == 400 and b["confidence"] == 0.9
    assert b["ci_low"] < b["observed"] < b["ci_high"]
    assert b["std"] > 0 and abs(b["mean"] - b["observed"]) < 3 * b["std"]
    assert len(b["edges"]) == len(b["counts"]) + 1 and sum(b["counts"]) == 400
    assert client.post(url(sid, "bootstrap"), json=body).json() == b             # same seed, same answer
    wider = client.post(url(sid, "bootstrap"), json={**body, "confidence": 0.99}).json()
    assert wider["ci_high"] - wider["ci_low"] > b["ci_high"] - b["ci_low"]


def test_bootstrap_declustering_weights_lower_the_mean(seeded):
    client, sid = seeded
    plain = client.post(url(sid, "bootstrap"), json={"component": "AU", "n_boot": 600, "seed": 1}).json()
    weighted = client.post(url(sid, "bootstrap"), json={"component": "AU", "n_boot": 600, "seed": 1,
                                                        "declus": {"method": "polygonal"}}).json()

    assert weighted["mean"] < plain["mean"]                                      # clustered highs drawn less often
    assert weighted["observed"] == pytest.approx(plain["observed"])              # the statistic itself stays unweighted


@pytest.mark.parametrize("statistic", ["median", "std", "cv", "p10", "p90"])
def test_bootstrap_other_statistics(seeded, statistic):
    client, sid = seeded

    r = client.post(url(sid, "bootstrap"), json={"component": "AU", "statistic": statistic, "n_boot": 100, "seed": 2})

    assert r.status_code == 200, r.text
    assert r.json()["ci_low"] <= r.json()["ci_high"]


def test_bootstrap_validation(seeded):
    client, sid = seeded
    assert client.post(url(sid, "bootstrap"), json={"component": "AU", "n_boot": 5}).status_code == 422
    assert client.post(url(sid, "bootstrap"), json={"component": "AU", "n_boot": 99999}).status_code == 422
    assert client.post(url(sid, "bootstrap"), json={"component": "AU", "statistic": "mode"}).status_code == 422
    assert client.post(url(sid, "bootstrap"), json={"component": "AU", "confidence": 1.2}).status_code == 422
    assert client.post(url(sid, "bootstrap"), json={"component": "ZN"}).status_code == 404


def test_local_uncertainty_maps(seeded):
    client, sid = seeded

    r = client.post(url(sid, "local-uncertainty"), json={"component": "AU", "nx": 12, "ny": 10, "nreal": 30,
                                                         "seed": 3, "threshold": 2.0})

    assert r.status_code == 200, r.text
    b = r.json()
    assert (len(b["x"]), len(b["y"])) == (12, 10) and b["holes"] == 66 and b["nreal"] == 30
    for key in ("mean", "std", "p10", "p90", "prob_exceed"):
        assert (len(b[key]), len(b[key][0])) == (10, 12)
    cells = [(i, j) for i in range(10) for j in range(12) if b["mean"][i][j] is not None]
    assert len(cells) > 60
    for i, j in cells:
        assert b["p10"][i][j] <= b["p90"][i][j] and b["std"][i][j] >= 0
        assert 0 <= b["prob_exceed"][i][j] <= 1
    # the dense high-grade cluster (around x=y=150) is estimated higher than the far corner
    near = min(range(12), key=lambda j: abs(b["x"][j] - 150)), min(range(10), key=lambda i: abs(b["y"][i] - 150))
    corner = b["mean"][0][-1]                                                    # far corner may be empty
    assert corner is None or b["mean"][near[1]][near[0]] > corner
    assert client.post(url(sid, "local-uncertainty"), json={"component": "AU", "nx": 12, "ny": 10, "nreal": 30,
                                                            "seed": 3}).json()["prob_exceed"] is None


def test_local_uncertainty_radius_masks_far_cells_and_validates(seeded):
    client, sid = seeded

    tight = client.post(url(sid, "local-uncertainty"), json={"component": "AU", "nx": 10, "ny": 10, "nreal": 10,
                                                             "radius": 30, "seed": 1}).json()
    wide = client.post(url(sid, "local-uncertainty"), json={"component": "AU", "nx": 10, "ny": 10, "nreal": 10,
                                                            "radius": 1000, "seed": 1}).json()

    empty = lambda m: sum(v is None for row in m for v in row)  # noqa: E731
    assert empty(tight["mean"]) > empty(wide["mean"]) == 0
    assert tight["radius"] == 30
    assert client.post(url(sid, "local-uncertainty"), json={"component": "AU", "nreal": 2}).status_code == 422
    assert client.post(url(sid, "local-uncertainty"), json={"component": "AU", "nx": 1}).status_code == 422


def test_local_uncertainty_needs_three_holes(db_client, db_engine):
    sid = new_set(db_client, "Two")
    with Session(db_engine) as s:
        for k in range(2):
            s.add(Collar(set_id=sid, hole_id=f"T{k}", x=100.0 * k, y=0.0, z=0.0, td=10.0, azimuth=0.0, dip=-90.0))
            s.add(Assay(set_id=sid, hole_id=f"T{k}", from_m=0.0, to_m=5.0, analytes={"AU": 1.0 + k}))
            s.add(Assay(set_id=sid, hole_id=f"T{k}", from_m=5.0, to_m=10.0, analytes={"AU": 2.0 + k}))
        s.add(Component(set_id=sid, name="AU", unit="g/t"))
        s.commit()

    r = db_client.post(url(sid, "local-uncertainty"), json={"component": "AU"})

    assert r.status_code == 422 and "скважин" in r.json()["detail"]
