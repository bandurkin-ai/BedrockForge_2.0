"""Histogram of assay fields: weighted statistics against numpy, bins, filters, API."""

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.models import Assay, Component
from app.stats.histogram import HistogramSpec, describe, histogram, weighted_percentiles
from tests.helpers import new_set


def test_unweighted_statistics_match_numpy():
    rng = np.random.default_rng(1)
    v = rng.lognormal(0, 1, 501)
    s = describe(v)
    assert s["mean"] == pytest.approx(v.mean(), rel=1e-12)
    assert s["variance"] == pytest.approx(v.var(), rel=1e-12)  # population variance
    assert s["median"] == pytest.approx(np.percentile(v, 50, method="hazen"), rel=1e-12)
    for p in ("5", "25", "75", "97.5"):
        assert s["percentiles"][p] == pytest.approx(np.percentile(v, float(p), method="hazen"), rel=1e-12)
    assert s["log"]["geometric_mean"] == pytest.approx(np.exp(np.log(v).mean()), rel=1e-12)
    assert s["cv"] == pytest.approx(v.std() / v.mean(), rel=1e-12)


def test_integer_weights_equal_repeated_samples():
    v = np.array([1.0, 2.0, 5.0, 9.0])
    w = np.array([1.0, 3.0, 2.0, 1.0])
    rep = np.repeat(v, w.astype(int))
    s = describe(v, w)
    assert s["mean"] == pytest.approx(rep.mean()) and s["variance"] == pytest.approx(rep.var())
    assert s["weight_sum"] == 7 and s["count"] == 4
    # Percentiles follow GSLIB: each value at the middle of its cumulative weight.
    pos = (np.cumsum(w) - w / 2) / w.sum()  # 0.071, 0.357, 0.714, 0.929
    assert weighted_percentiles(v, w, [50])[0] == pytest.approx(np.interp(0.5, pos, v))  # 3.2


def test_linear_bins_are_aligned_half_open_and_last_closed():
    v = np.array([0.0, 0.5, 1.0, 1.0, 1.9, 2.0])
    h = histogram(v, np.ones(6), HistogramSpec(bin_width=1.0))
    assert h.edges == [0.0, 1.0, 2.0] and h.counts == [2, 4]  # 2.0 falls into the closed last bin
    h = histogram(v, np.ones(6), HistogramSpec(bin_width=0.5, start=0.5, end=1.5))
    assert h.edges == [0.5, 1.0, 1.5] and h.counts == [1, 2]
    assert h.below == {"count": 1, "weight": 1.0} and h.above == {"count": 2, "weight": 2.0}
    h = histogram(np.array([3.2, 7.9]), np.ones(2), HistogramSpec(bins=4))
    assert len(h.counts) == 4 and h.edges[0] == 3.2 and h.edges[-1] == pytest.approx(7.9)


def test_log_bins_per_decade():
    v = np.array([0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0])
    h = histogram(v, np.ones(len(v)), HistogramSpec(log=True, bin_width=1.0))
    assert h.edges == pytest.approx([0.01, 0.1, 1.0, 10.0]) and h.counts == [2, 2, 3]


@pytest.fixture
def assays(db_client, db_engine):
    sid = new_set(db_client, "Гистограмма")
    rows = [  # from, to, AU, density
        (0, 1, 1.0, 2.0), (1, 3, 2.0, 2.5), (3, 4, -0.01, 2.7), (4, 6, 0.0, None), (6, 7, None, 2.6), (7, 9, 40.0, 3.0),
    ]
    with Session(db_engine) as s:
        for i, (a, b, au, d) in enumerate(rows):
            s.add(Assay(set_id=sid, hole_id="H1" if i < 3 else "H2", from_m=a, to_m=b,
                        analytes={"AU": au} if au is not None else {},
                        attributes={"DENSITY": d, "LITH": "QV"} if d is not None else {"LITH": "QV"}))
        s.add(Component(set_id=sid, name="AU", unit="g/t"))
        s.commit()
    return db_client, sid


def test_fields_list_components_attributes_and_length(assays):
    client, sid = assays
    fields = {f["id"]: f for f in client.get(f"/api/sets/{sid}/stats/fields").json()}
    assert fields["comp:AU"]["unit"] == "g/t" and fields["comp:AU"]["count"] == 5
    assert fields["attr:DENSITY"]["count"] == 5 and "attr:LITH" not in fields  # text is not numeric
    assert fields["length"]["count"] == 6


def test_histogram_api_weights_filters_and_counts(assays):
    client, sid = assays
    url = f"/api/sets/{sid}/stats/histogram"

    plain = client.post(url, json={"field": "comp:AU"}).json()
    assert plain["counts"]["rows"] == 6 and plain["counts"]["missing"] == 1 and plain["counts"]["used"] == 5
    assert plain["counts"]["negative"] == 1 and plain["stats"]["mean"] == pytest.approx((1 + 2 - 0.01 + 0 + 40) / 5)
    assert sum(plain["bins"]["counts"]) == 5 and plain["bins"]["cumulative_percent"][-1] == pytest.approx(100)

    by_length = client.post(url, json={"field": "comp:AU", "weight": "length", "negatives": "half_abs"}).json()
    v = np.array([1, 2, 0.005, 0, 40])
    w = np.array([1, 2, 1, 2, 2])
    assert by_length["stats"]["mean"] == pytest.approx((v * w).sum() / w.sum())
    assert by_length["stats_unweighted"]["mean"] == pytest.approx(v.mean())

    # length × density: the row without density has no weight and is left out.
    tonnage = client.post(url, json={"field": "comp:AU", "weight": "length_field", "weight_field": "attr:DENSITY"}).json()
    assert tonnage["counts"]["bad_weight"] == 1 and tonnage["counts"]["used"] == 4
    vt, wt = np.array([1, 2, -0.01, 40]), np.array([1 * 2.0, 2 * 2.5, 1 * 2.7, 2 * 3.0])
    assert tonnage["stats"]["mean"] == pytest.approx((vt * wt).sum() / wt.sum())

    cut = client.post(url, json={"field": "comp:AU", "top_cut": 10, "negatives": "exclude", "exclude_zero": True}).json()
    assert cut["counts"]["capped"] == 1 and cut["counts"]["used"] == 3 and cut["stats"]["max"] == 10

    # Cuts at both ends (values 1, 2, -0.01, 0, 40): lower excluded, upper capped by default.
    both = client.post(url, json={"field": "comp:AU", "bottom_cut": 0.5, "top_cut": 10}).json()
    assert both["counts"]["bottom_excluded"] == 2 and both["counts"]["capped"] == 1 and both["counts"]["used"] == 3
    assert both["stats"]["min"] == 1 and both["stats"]["max"] == 10
    floor = client.post(url, json={"field": "comp:AU", "bottom_cut": 0.5, "bottom_mode": "cap", "top_cut": 10, "top_mode": "exclude"}).json()
    assert floor["counts"]["bottom_capped"] == 2 and floor["counts"]["top_excluded"] == 1 and floor["counts"]["used"] == 4
    assert floor["stats"]["mean"] == pytest.approx((1 + 2 + 0.5 + 0.5) / 4)
    assert client.post(url, json={"field": "comp:AU", "bottom_cut": 5, "top_cut": 5}).status_code == 422

    log = client.post(url, json={"field": "comp:AU", "log": True, "bin_width": 0.5}).json()
    assert log["counts"]["nonpositive_log"] == 2 and sum(log["bins"]["counts"]) == 3
    one_hole = client.post(url, json={"field": "comp:AU", "holes": ["H2"]}).json()
    assert one_hole["counts"]["rows"] == 3

    assert client.post(url, json={"field": "comp:XX"}).status_code == 422
    assert client.post(url, json={"field": "nonsense"}).status_code == 422
    assert client.post(url, json={"field": "comp:AU", "weight": "field"}).status_code == 422
    assert client.post(url, json={"field": "comp:AU", "bin_width": 1e-9}).status_code == 422
