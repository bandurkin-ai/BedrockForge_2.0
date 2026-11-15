"""Dendrogram of fields: correlations against numpy / scipy, clustering, API."""

import numpy as np
import pytest
from scipy.stats import spearmanr
from sqlalchemy.orm import Session

from app.models import Assay, Component
from app.stats.dendrogram import cluster, correlation_matrix, weighted_corr
from tests.helpers import new_set


def test_correlations_match_numpy_and_scipy():
    rng = np.random.default_rng(1)
    a = rng.normal(size=300)
    b = a * 0.7 + rng.normal(size=300) * 0.5
    c = rng.normal(size=300)
    v = np.column_stack([a, b, c])
    r, n = correlation_matrix(v, np.ones(300))
    assert np.allclose(r, np.corrcoef(v.T), atol=1e-12) and (n == 300).all()
    rs, _ = correlation_matrix(v, np.ones(300), "spearman")
    assert rs[0, 1] == pytest.approx(spearmanr(a, b).statistic, abs=1e-12)


def test_integer_weights_equal_repeated_rows_and_missing_values():
    a, b, w = np.array([1.0, 2, 3, 5]), np.array([2.0, 1, 4, 4]), np.array([1.0, 2, 1, 3])
    rep = np.repeat(np.arange(4), w.astype(int))
    assert weighted_corr(a, b, w) == pytest.approx(np.corrcoef(a[rep], b[rep])[0, 1])
    v = np.array([[1, 2, np.nan], [2, 3, 1], [3, 5, 2], [4, 4, 3], [5, 7, np.nan]], dtype=float)
    r, n = correlation_matrix(v, np.ones(5), missing="pairwise")
    assert n[0, 1] == 5 and n[0, 2] == 3
    r2, n2 = correlation_matrix(v, np.ones(5), missing="complete")
    assert n2[0, 1] == 3 and r2[0, 1] != pytest.approx(r[0, 1])


def test_correlated_fields_join_first():
    r = np.array([[1, 0.9, 0.1, -0.8], [0.9, 1, 0.2, -0.7], [0.1, 0.2, 1, 0.0], [-0.8, -0.7, 0.0, 1]])
    t = cluster(r, ["AU", "AG", "CU", "SIO2"])
    assert t["linkage"][0][:2] == [0.0, 1.0] and t["linkage"][0][2] == pytest.approx(0.1)
    # 1 - |r|: the anti-correlated SIO2 is close to AU and AG.
    t2 = cluster(r, ["AU", "AG", "CU", "SIO2"], distance="1-|r|")
    assert t2["leaves"][-1] == "CU" or t2["leaves"][0] == "CU"
    assert len(t["icoord"]) == 3 and sorted(t["leaves"]) == ["AG", "AU", "CU", "SIO2"]


def test_dendrogram_api(db_client, db_engine):
    sid = new_set(db_client, "Дендрограмма")
    rng = np.random.default_rng(3)
    with Session(db_engine) as s:
        for i in range(120):
            au = float(rng.lognormal(0, 1))
            s.add(Assay(set_id=sid, hole_id="H1", from_m=i, to_m=i + 1 + i % 2, analytes={
                "AU": au, "AG": au * float(rng.lognormal(1, 0.2)), "CU": float(rng.lognormal(0, 1)),
                "PB": -0.01 if i % 10 == 0 else float(rng.lognormal(0, 1)),
            }))
        s.add_all([Component(set_id=sid, name=c, unit="g/t") for c in ("AU", "AG", "CU", "PB")])
        s.commit()
    url = f"/api/sets/{sid}/stats/dendrogram"
    r = db_client.post(url, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["names"]) == ["AG", "AU", "CU", "PB"] and body["rows"] == 120
    pair = sorted(body["names"][int(i)] for i in body["linkage"][0][:2])
    assert pair == ["AG", "AU"]  # AG follows AU
    ia, ig = body["names"].index("AU"), body["names"].index("AG")
    assert body["correlation"][ia][ig] > 0.9
    some = db_client.post(url, json={"fields": ["comp:AU", "comp:CU", "length"], "method": "spearman", "log": False, "linkage": "complete"})
    assert some.status_code == 200 and some.json()["names"] == ["AU", "CU", "Длина интервала"]
    assert db_client.post(url, json={"fields": ["comp:AU"]}).status_code == 422
    assert db_client.post(url, json={"linkage": "ward"}).status_code == 422


def test_saved_charts(db_client):
    sid = new_set(db_client, "Графики")
    c = db_client.post(f"/api/sets/{sid}/charts", json={"kind": "histogram", "name": "Гистограмма AU", "settings": {"field": "comp:AU"}})
    assert c.status_code == 201
    cid = c.json()["id"]
    # Settings change: the automatic name follows them.
    p = db_client.patch(f"/api/charts/{cid}", json={"settings": {"field": "comp:CU"}, "auto_name": "Гистограмма CU"}).json()
    assert p["name"] == "Гистограмма CU" and p["settings"] == {"field": "comp:CU"} and p["auto_name"]
    # Renamed by the user: the automatic name no longer replaces it.
    db_client.patch(f"/api/charts/{cid}", json={"name": "Золото, весь участок"})
    p = db_client.patch(f"/api/charts/{cid}", json={"settings": {"field": "comp:AU"}, "auto_name": "Гистограмма AU"}).json()
    assert p["name"] == "Золото, весь участок" and not p["auto_name"]
    tree = next(d for d in db_client.get(f"/api/projects/{db_client.get('/api/projects').json()[0]['id']}/datasets").json() if d["id"] == sid)
    assert tree["charts"] == [{"id": cid, "kind": "histogram", "name": "Золото, весь участок"}]
    assert db_client.post(f"/api/sets/{sid}/charts", json={"kind": "pie", "name": "x"}).status_code == 422
    assert db_client.delete(f"/api/charts/{cid}").status_code == 204
    assert db_client.get(f"/api/sets/{sid}/charts").json() == []
    assert db_client.get(f"/api/charts/{cid}").status_code == 404
