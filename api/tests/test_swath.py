"""Swath plot: the slices, weighted means, samples inside blocks, the API and saved plots."""

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.models import Assay, Collar, Component
from app.stats import swath as sw
from tests.test_blockmodels import ORIGIN, PARENT, _import, make_blocks


def test_frames_are_orthonormal_and_azimuth_matches_the_axes():
    for direction, az in (("x", 0), ("y", 0), ("z", 0), ("azimuth", 37.5)):
        f = sw.frame(direction, az)
        assert np.allclose(f.axes @ f.axes.T, np.eye(3), atol=1e-12)
    assert np.allclose(sw.frame("azimuth", 90).u, sw.frame("x").u, atol=1e-12)
    assert np.allclose(sw.frame("azimuth", 0).u, sw.frame("y").u, atol=1e-12)
    assert np.allclose(sw.frame("azimuth", 45).u, [2 ** -0.5, 2 ** -0.5, 0])
    with pytest.raises(ValueError):
        sw.frame("down")


def test_layout_and_default_width():
    assert sw.layout(100.0, 200.0, 10.0) == (100.0, 10)
    assert sw.layout(100.0, 201.0, 10.0) == (100.0, 11)
    # A boundary passes through the anchor: the first swath starts at or below the data.
    assert sw.layout(103.0, 200.0, 10.0, anchor=0.0) == (100.0, 10)
    assert sw.layout(103.0, 200.0, 10.0, anchor=1005.0) == (95.0, 11)
    with pytest.raises(ValueError, match="Слишком много"):
        sw.layout(0.0, 1e6, 1.0)
    assert sw.default_width(100.0, 5.0) == 5.0          # 20 swaths of one block
    assert sw.default_width(1000.0, 5.0) == 20.0        # 4 blocks: 50 swaths
    assert sw.default_width(1000.0, None) == 50.0       # no model: a round number, about 1/25 of the extent
    assert sw.nice(37.0) == 50.0 and sw.nice(0.21) == 0.25


def test_means_match_a_direct_count():
    rng = np.random.default_rng(1)
    t = rng.uniform(-5, 105, 2000)
    v = rng.lognormal(0, 1, 2000)
    w = rng.uniform(0.5, 2, 2000)
    v[::50] = np.nan
    r = sw.swath_means(t, v, w, 0.0, 10.0, 10)
    for k in range(10):
        m = (t >= 10 * k) & (t < 10 * (k + 1)) & np.isfinite(v)
        assert r["mean"][k] == pytest.approx((v[m] * w[m]).sum() / w[m].sum())
        assert r["count"][k] == m.sum() and r["weight"][k] == pytest.approx(w[m].sum())
    assert r["outside"] == int(((t < 0) | (t > 100)).sum())
    inside = (t >= 0) & (t <= 100) & np.isfinite(v)
    assert r["total"]["mean"] == pytest.approx((v[inside] * w[inside]).sum() / w[inside].sum())
    # The far boundary belongs to the last swath; an empty swath has no mean.
    edge = sw.swath_means(np.array([100.0, 5.0]), np.array([3.0, 1.0]), np.ones(2), 0.0, 10.0, 10)
    assert edge["mean"][9] == 3.0 and np.isnan(edge["mean"][4]) and edge["count"].sum() == 2


def test_points_in_blocks_matches_a_direct_test():
    m = make_blocks()
    centres = np.column_stack([m["X"], m["Y"], m["Z"]])
    sizes = np.column_stack([m["_X"], m["_Y"], m["_Z"]])
    rng = np.random.default_rng(4)
    points = ORIGIN + rng.uniform(-3, [23, 18, 13], (4000, 3))
    direct = (np.abs(points[:, None, :] - centres[None]) <= sizes[None] / 2).all(axis=2).any(axis=1)
    got = sw.points_in_blocks(points, centres, sizes, ORIGIN, [PARENT] * 3)
    assert (got == direct).all() and 0 < got.sum() < len(points)  # some parents are air
    assert not sw.points_in_blocks(points, centres[:0], sizes[:0], ORIGIN, [PARENT] * 3).any()


@pytest.fixture
def model(db_client, run_jobs, tmp_path):
    m = make_blocks()
    pid, d = _import(db_client, run_jobs, tmp_path, m)
    keys = {a["name"]: a["key"] for a in d["meta"]["attributes"]}
    return db_client, pid, d, m, keys


def _expected(m, field, weight, mask=None):
    """Means by parent column along X, counted block by block."""
    vol = m["_X"] * m["_Y"] * m["_Z"]
    sg = m["SG"].copy()
    sg[1] = np.nan  # the file has no density for this block: it has no tonnage
    w = {"volume": vol, "tonnage": vol * sg, "none": np.ones(len(vol))}[weight]
    mask = (np.ones(len(vol), bool) if mask is None else mask) & np.isfinite(w)
    out = []
    for i in range(4):
        sel = mask & (m["X"] >= ORIGIN[0] + PARENT * i) & (m["X"] < ORIGIN[0] + PARENT * (i + 1))
        out.append((m[field][sel] * w[sel]).sum() / w[sel].sum() if sel.any() else None)
    return out


def test_block_models_by_volume_tonnage_and_count(model):
    client, _, d, m, keys = model
    r = client.post("/api/stats/swath", json={"direction": "x", "series": [
        {"kind": "blocks", "model_id": d["id"], "field": keys["AU_CUT"]},
        {"kind": "blocks", "model_id": d["id"], "field": "AU", "weight": "tonnage", "density": "SG"},
        {"kind": "blocks", "model_id": d["id"], "field": keys["AU_CUT"], "weight": "none"},
    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    # The default width is one parent block; the cuboid is the model's extent.
    assert body["width"] == PARENT and body["count"] == 4 and body["start"] == ORIGIN[0]
    assert body["box"]["u"] == [ORIGIN[0], ORIGIN[0] + 20] and body["box"]["w"] == [ORIGIN[2], ORIGIN[2] + 10]
    assert body["centres"] == [ORIGIN[0] + 2.5 + 5 * i for i in range(4)]
    a, b, c = body["series"]
    assert [s["index"] for s in body["series"]] == [0, 1, 2] and a["name"].endswith("AU_CUT") and a["unit"] == "g/t"
    assert a["mean"] == pytest.approx(_expected(m, "AU_CUT", "volume"))
    assert b["mean"] == pytest.approx(_expected(m, "AU", "tonnage")) and b["weight_unit"] == "т"
    assert b["counts"]["no_weight"] == 1 and b["counts"]["used"] == len(m["X"]) - 1
    assert c["mean"] == pytest.approx(_expected(m, "AU_CUT", "none"))
    vol = m["_X"] * m["_Y"] * m["_Z"]
    assert sum(a["weights"]) == pytest.approx(vol.sum()) and sum(a["count"]) == len(vol) == sum(c["weights"])
    assert a["total"]["mean"] == pytest.approx((m["AU_CUT"] * vol).sum() / vol.sum())

    # A category filter: only the blocks with ALT = G; a wider swath: two parents each.
    g = m["ALT"] == "G"
    f = client.post("/api/stats/swath", json={"direction": "x", "width": 10, "series": [
        {"kind": "blocks", "model_id": d["id"], "field": "AU_CUT", "filters": [{"key": "ALT", "labels": ["G"]}]},
    ]}).json()
    vg = vol[g]
    first = g & (m["X"] < ORIGIN[0] + 10)
    assert f["count"] == 2 and f["series"][0]["counts"]["selected"] == int(g.sum())
    assert f["series"][0]["mean"][0] == pytest.approx((m["AU_CUT"][first] * vol[first]).sum() / vol[first].sum())
    assert sum(f["series"][0]["weights"]) == pytest.approx(vg.sum())

    # Y, Z and an azimuth: an azimuth of 90° is the X direction.
    for direction, n in (("y", 3), ("z", 2)):
        o = client.post("/api/stats/swath", json={"direction": direction, "series": [{"kind": "blocks", "model_id": d["id"], "field": "AU"}]}).json()
        assert o["count"] == n and sum(o["series"][0]["count"]) == len(vol)
    az = client.post("/api/stats/swath", json={"direction": "azimuth", "azimuth": 90, "width": 5, "series": [
        {"kind": "blocks", "model_id": d["id"], "field": "AU_CUT"}]}).json()
    assert az["series"][0]["mean"] == pytest.approx(a["mean"]) and az["frame"]["u"] == pytest.approx([1, 0, 0], abs=1e-12)
    narrow = client.post("/api/stats/swath", json={"direction": "x", "width": 0.5, "series": [{"kind": "blocks", "model_id": d["id"], "field": "AU"}]}).json()
    assert narrow["count"] == 40 and "уже блока" in narrow["notes"][0] and None in narrow["series"][0]["mean"]


def _add_holes(db_engine, sid):
    """Vertical holes 5 m apart along X through the model and beyond it, two 5 m samples each."""
    rng = np.random.default_rng(9)
    rows = []
    with Session(db_engine) as s:
        for k in range(-1, 6):
            x, y = ORIGIN[0] + 2.5 + 5 * k, ORIGIN[1] + 7.5
            hole = f"S{k + 1}"
            s.add(Collar(set_id=sid, hole_id=hole, x=float(x), y=float(y), z=float(ORIGIN[2] + 10), td=10.0, azimuth=0.0, dip=-90.0))
            for i in range(2):
                au = float(rng.lognormal(0, 0.5))
                sg = float(rng.uniform(2.5, 3.0))
                s.add(Assay(set_id=sid, hole_id=hole, from_m=5.0 * i, to_m=5.0 * i + (5.0 if i else 3.0), analytes={"AU": au, "SG": sg}))
                rows.append((x, y, ORIGIN[2] + 10 - (1.5 if i == 0 else 7.5), au, 5.0 if i else 3.0, sg))
        s.add_all([Component(set_id=sid, name="AU", unit="g/t"), Component(set_id=sid, name="SG", unit="")])
        s.commit()
    return np.array(rows)


def test_samples_against_a_model_in_the_box_and_in_blocks(model, db_engine):
    client, pid, d, m, _ = model
    sid = client.post(f"/api/projects/{pid}/sets", json={"name": "Скважины"}).json()["id"]
    rows = _add_holes(db_engine, sid)
    x, z, au, length, sg = rows[:, 0], rows[:, 2], rows[:, 3], rows[:, 4], rows[:, 5]
    body = {"direction": "x", "series": [
        {"kind": "samples", "set_id": sid, "field": "comp:AU", "weight": "length"},
        {"kind": "blocks", "model_id": d["id"], "field": "AU_CUT"},
        {"kind": "samples", "set_id": sid, "field": "comp:AU", "weight": "length_field", "weight_field": "comp:SG", "within": "blocks"},
        {"kind": "samples", "set_id": sid, "field": "comp:AU", "within": "blocks", "within_series": 1},
    ]}
    r = client.post("/api/stats/swath", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    box, _, weighted, plain = out["series"]
    # In the cuboid: the 4 holes over the model (the two outside it along X are left out).
    in_box = (x >= ORIGIN[0]) & (x <= ORIGIN[0] + 20)
    assert box["counts"]["rows"] == 14 and box["counts"]["outside_box"] == int((~in_box).sum()) == 6
    for k in range(4):
        sel = in_box & (np.floor((x - ORIGIN[0]) / 5) == k)
        assert box["mean"][k] == pytest.approx((au[sel] * length[sel]).sum() / length[sel].sum())
        assert box["count"][k] == 2 and box["weights"][k] == pytest.approx(8.0)
    assert box["within"] == "box" and box["weight_name"] == "длина" and box["unit"] == "g/t"

    # In blocks: the same samples without those in the air parents of the model.
    centres = np.column_stack([m["X"], m["Y"], m["Z"]])
    sizes = np.column_stack([m["_X"], m["_Y"], m["_Z"]])
    pts = rows[:, :3]
    inside = (np.abs(pts[:, None, :] - centres[None]) <= sizes[None] / 2).all(axis=2).any(axis=1)
    assert 0 < inside.sum() < in_box.sum()
    assert plain["counts"]["used"] == int(inside.sum()) and plain["counts"]["outside_blocks"] == int((in_box & ~inside).sum())
    assert weighted["within"].endswith("AU_CUT")
    for k in range(4):
        sel = inside & (np.floor((x - ORIGIN[0]) / 5) == k)
        if not sel.any():
            assert plain["mean"][k] is None and weighted["mean"][k] is None
            continue
        assert plain["mean"][k] == pytest.approx(au[sel].mean())
        assert weighted["mean"][k] == pytest.approx((au[sel] * length[sel] * sg[sel]).sum() / (length[sel] * sg[sel]).sum())

    # Along Z the samples of every hole fall into two benches.
    zr = client.post("/api/stats/swath", json={"direction": "z", "series": body["series"][:2]}).json()
    low = in_box & (z < ORIGIN[2] + 5)
    assert zr["series"][0]["mean"][0] == pytest.approx(au[low].mean()) and zr["series"][0]["count"] == [4, 4]

    # Samples alone: the cuboid is around them, the width a round number.
    alone = client.post("/api/stats/swath", json={"direction": "x", "series": [body["series"][0]]}).json()
    assert alone["box"]["u"][0] == x.min() and alone["series"][0]["counts"]["outside_box"] == 0 and alone["block"] is None
    assert sum(alone["series"][0]["count"]) == 14

    # Errors: «in blocks» without a model, or pointing at a series that is not a model.
    no_model = client.post("/api/stats/swath", json={"series": [{**body["series"][0], "within": "blocks"}]})
    assert no_model.status_code == 422 and "блочной модели" in no_model.json()["detail"]
    wrong = client.post("/api/stats/swath", json={"series": [body["series"][1], {**body["series"][0], "within": "blocks", "within_series": 1}]})
    assert wrong.status_code == 422
    assert client.post("/api/stats/swath", json={"series": [{"kind": "blocks", "model_id": sid, "field": "AU"}]}).status_code == 404
    assert client.post("/api/stats/swath", json={"series": [{"kind": "blocks", "model_id": d["id"], "field": "ALT"}]}).status_code == 422
    assert client.post("/api/stats/swath", json={"series": []}).status_code == 422
    assert client.post("/api/stats/swath", json={"series": [{"kind": "blocks", "model_id": d["id"], "field": "AU", "weight": "tonnage"}]}).status_code == 422


def test_saved_swath_plots_live_in_the_project_tree(model):
    client, pid, d, _, _ = model
    settings = {"direction": "y", "series": [{"kind": "blocks", "modelId": d["id"], "field": "AU"}]}
    made = client.post(f"/api/projects/{pid}/swaths", json={"name": "Swath plot 1", "settings": settings})
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    assert made.json()["kind"] == "swath" and made.json()["meta"]["settings"] == settings
    kinds = sorted(x["kind"] for x in client.get(f"/api/projects/{pid}/datasets").json())
    assert kinds == ["blocks", "swath"]
    patched = client.patch(f"/api/swaths/{sid}", json={"settings": {**settings, "width": 10}})
    assert patched.json()["meta"]["settings"]["width"] == 10
    assert client.patch(f"/api/datasets/{sid}", json={"name": "По Y"}).json()["name"] == "По Y"
    assert client.patch(f"/api/swaths/{d['id']}", json={"settings": {}}).status_code == 404  # a block model is not a plot
    assert client.get(f"/api/projects/{pid}/stats").status_code == 200
    assert client.delete(f"/api/datasets/{sid}").status_code == 204
    assert [x["kind"] for x in client.get(f"/api/projects/{pid}/datasets").json()] == ["blocks"]
