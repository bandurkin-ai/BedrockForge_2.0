"""3D hole manifest (ADR 0007 §1): the BFA1 container, parts in Morton order with integer origins, the
manifest of the demo set, the audit colour per hole («нет аудита», «устарел»), ETag and 304."""

from __future__ import annotations

import json
import math
import struct

import numpy as np
import pytest

from bf_core.viz import binary
from bf_core.viz.parts import MAX_HOLES, make_parts, morton, order
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows


# --- BFA1 -------------------------------------------------------------------------------------------


def test_bfa1_round_trip_alignment_and_little_endian() -> None:
    xyz = np.arange(9, dtype=np.float64).reshape(3, 3) + 0.25
    data = binary.encode(
        {"kind": "test", "names": ["DH-1", "Скв. 2"], "x": None},
        [
            ("u1", np.array([1, 2, 3], dtype=np.uint8), "<u1"),
            ("xyz", xyz, "<f8"),
            ("i4", [-1, 0, 7], "<i4"),
            ("empty", np.zeros(0), "<f4"),
            ("v:Au г/т", [np.nan, 1.5], "<f8"),
            ("u2", [65535], "<u2"),
        ],
    )
    assert data[:4] == b"BFA1"
    (length,) = struct.unpack("<I", data[4:8])
    start = (8 + length + 7) & ~7
    assert all(b == 0 for b in data[8 + length : start])  # zeros up to 8
    header, arrays = binary.decode(data)
    assert header["kind"] == "test" and header["names"] == ["DH-1", "Скв. 2"] and header["x"] is None
    table = json.loads(data[8 : 8 + length])["arrays"]
    assert [t[0] for t in table] == ["u1", "xyz", "i4", "empty", "v:Au г/т", "u2"]
    assert all(t[3] % 8 == 0 and t[3] >= start for t in table)
    assert table[1][2] == 9  # count — elements, not points
    np.testing.assert_array_equal(arrays["xyz"].reshape(3, 3), xyz)
    assert arrays["xyz"].dtype == np.dtype("<f8") and arrays["i4"].tolist() == [-1, 0, 7]
    assert math.isnan(arrays["v:Au г/т"][0]) and arrays["v:Au г/т"][1] == 1.5  # NaN stays NaN
    assert arrays["empty"].size == 0 and arrays["u2"].tolist() == [65535]
    off = table[1][3]
    assert struct.unpack("<d", data[off : off + 8])[0] == 0.25  # little-endian on any host


def test_bfa1_refuses_bad_input() -> None:
    with pytest.raises(ValueError):
        binary.encode({}, [("a", [1], "<i8")])
    with pytest.raises(ValueError):
        binary.encode({"arrays": 1}, [])
    with pytest.raises(ValueError):
        binary.encode({"x": float("nan")}, [])  # the header is strict JSON
    with pytest.raises(binary.FormatError):
        binary.decode(b"BFA2\0\0\0\0")
    good = binary.encode({}, [("a", [1.0, 2.0], "<f8")])
    with pytest.raises(binary.FormatError):
        binary.decode(good[:-4])  # an array beyond the end


def test_bfa1_header_length_changes_offsets() -> None:
    """Absolute offsets feed back into the header length: the fixed point is found for any size."""
    for n in range(0, 300, 7):
        header, arrays = binary.decode(binary.encode({"pad": "я" * n}, [("a", np.arange(n), "<u4")]))
        assert header["pad"] == "я" * n and arrays["a"].tolist() == list(range(n))


# --- parts ------------------------------------------------------------------------------------------


def test_morton_order_keeps_neighbours_together() -> None:
    x, y = np.meshgrid(np.arange(8) * 50.0 + 10, np.arange(8) * 50.0 + 10)
    x, y = x.ravel(), y.ravel()
    ids = np.arange(64)[::-1]
    o = order(x, y, ids)
    # the first four are the 2×2 block at the origin, in Z order
    assert [(x[i], y[i]) for i in o[:4]] == [(10.0, 10.0), (60.0, 10.0), (10.0, 60.0), (60.0, 60.0)]
    assert morton(np.array([0.0, 49.9, 50.0]), np.array([0.0, 0.0, 0.0])).tolist() == [0, 0, 1]
    same = order(np.zeros(3), np.zeros(3), np.array([7, 3, 5]))  # one cell: by id
    assert same.tolist() == [1, 2, 0]


def _bounds(x: np.ndarray, y: np.ndarray, z: np.ndarray, depth: float) -> np.ndarray:
    return np.column_stack([x, y, z - depth, x + 10, y + 10, z])


def test_parts_at_most_2000_holes_integer_origin() -> None:
    rng = np.random.default_rng(3)
    n = 4500
    x = 500_000 + rng.uniform(0, 5000, n)
    y = 7_100_000 + rng.uniform(0, 5000, n)
    z = rng.uniform(300, 400, n)
    b = _bounds(x, y, z, 250.0)
    parts = make_parts(b)
    assert [p.n for p in parts] == [MAX_HOLES, MAX_HOLES, 500]
    assert parts[0].start == 0 and parts[-1].stop == n
    for p in parts:
        box = np.array(p.bbox)
        sub = b[p.start : p.stop]
        np.testing.assert_array_equal(box[:3], sub[:, :3].min(axis=0))
        np.testing.assert_array_equal(box[3:], sub[:, 3:].max(axis=0))
        assert all(float(v).is_integer() for v in p.origin)
        assert np.all(np.abs(np.array(p.origin) - (box[:3] + box[3:]) / 2) <= 0.5)


def test_part_wider_than_16_km_is_split() -> None:
    """Two clusters 30 km apart with fewer than 2000 holes: halves until every part is ≤ 16 km; every point
    of a part is within 8000.5 m of its origin (float32 step ≤ 0.49 mm)."""
    x = np.concatenate([np.linspace(0, 900, 300), np.linspace(30_000, 30_900, 300)]) + 400_000
    y = np.full(600, 6_000_000.0)
    b = _bounds(x, y, np.full(600, 500.0), 300.0)
    parts = make_parts(b)
    assert len(parts) >= 2 and sum(p.n for p in parts) == 600
    for p in parts:
        box = np.array(p.bbox)
        assert np.all(box[3:] - box[:3] <= 16_000)
        assert np.all(np.maximum(np.abs(box[:3] - p.origin), np.abs(box[3:] - p.origin)) <= 8000.5)
    one = make_parts(np.array([[0, 0, 0, 20_000, 1, 1]], dtype=float))  # one hole is never split
    assert len(one) == 1 and one[0].n == 1


# --- the manifest of the demo set -------------------------------------------------------------------


def open_demo(c):  # type: ignore[no-untyped-def]
    """The demo project with its set «Скважины 2025» (48 holes): (client, set id)."""
    r = c.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    sets = c.get(f"{API}/drillholes/sets").json()["sets"]
    assert [s["name"] for s in sets] == ["Скважины 2025"] and sets[0]["holes"] == 49  # 48 holes and the trench К-01
    return c, sets[0]["id"]


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    return open_demo(pclient)


def manifest(c, set_id: int, **params) -> tuple[dict, dict]:  # type: ignore[no-untyped-def, type-arg]
    r = c.get(f"{API}/viz/sets/{set_id}/holes", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["cache-control"] == "no-cache"
    return binary.decode(r.content)


def test_demo_manifest(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    head, a = manifest(c, set_id)
    # 48 holes and the trench «К-01» (demo/geo.py): a hole of the set whose stations carry coordinates
    assert head["kind"] == "holes" and head["method"] == "minimum_curvature" and head["n"] == 49
    assert head["skipped"] == [] and sorted(head["names"]) == [*(f"DH-{i:03d}" for i in range(1, 49)), "К-01"]
    (part,) = head["parts"]
    assert part["k"] == 0 and part["n"] == 49 and all(float(v).is_integer() for v in part["origin"])
    assert a["part"].tolist() == [0] * 49 and a["slot"].tolist() == list(range(49))
    collars = {
        r["hole"]: r
        for r in c.get(f"{API}/drillholes/sets/{set_id}/tables/collar", params={"limit": 100}).json()["rows"]
    }
    xyz = a["collar"].reshape(-1, 3)
    for i, name in enumerate(head["names"]):
        row = collars[name]
        assert a["id"][i] == row["id"]
        assert xyz[i].tolist() == [row["x"], row["y"], row["z"]]  # float64, not rounded
        assert a["td"][i] == row["td"]
        if name != "DH-006":
            assert abs(a["length"][i] - row["td"]) < 1e-9
        assert 80 < a["plan_az"][i] < 100  # the demo holes go east, and so does the trench across the ore zone
    assert a["length"][head["names"].index("DH-006")] > a["td"][head["names"].index("DH-006")]  # survey below TD
    box = np.array(part["bbox"])
    assert np.all(box[:3] <= xyz.min(axis=0)) and np.all(box[3:] >= xyz.max(axis=0))
    kinds = dict(zip(head["names"], a["kind"].tolist(), strict=True))
    assert kinds.pop("К-01") == 1 and set(kinds.values()) == {0}  # a trench by its type, the rest are holes
    # neighbours on the ground are neighbours in the order: the first 4 holes are within 2 cells
    first = xyz[:4, :2]
    assert np.ptp(first[:, 0]) < 120 and np.ptp(first[:, 1]) < 120


def _set_attr(c, set_id: int, name: str, key: str, value: str) -> None:  # type: ignore[no-untyped-def]
    rows = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar", params={"limit": 100}).json()["rows"]
    row = next(r for r in rows if r["hole"] == name)
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/collar/rows/{row['id']}", json={"column": key, "value": value})
    assert r.status_code == 200, r.text


def test_kind_vertical_and_skipped(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    _set_attr(c, set_id, "DH-002", "a:status", "Канава")
    _set_attr(c, set_id, "DH-003", "a:status", "Проектная")
    s = c.post(f"{API}/drillholes/sets", json={"name": "Вертикальные"}).json()["id"]
    for row in (
        {"hole": "V-1", "x": 1000, "y": 2000, "z": 300, "td": 100, "azimuth": 0, "dip": -90},
        {"hole": "V-2", "x": 1000.05, "y": 2100, "z": 300, "td": 100, "azimuth": 30, "dip": -89.99},
        {"hole": "V-3", "x": 1100, "y": 2100, "z": 300, "td": 100, "azimuth": 30, "dip": -60},
    ):
        r = c.post(f"{API}/drillholes/sets/{s}/tables/collar/rows", json={"values": row})
        assert r.status_code == 200, r.text
    survey = {"hole": "NC-1", "depth": 0, "azimuth": 0, "dip": -90}  # a hole without a collar
    assert c.post(f"{API}/drillholes/sets/{s}/tables/survey/rows", json={"values": survey}).status_code == 200
    head, a = manifest(c, set_id)
    kinds = dict(zip(head["names"], a["kind"].tolist(), strict=True))
    assert kinds["DH-002"] == 1 and kinds["DH-003"] == 2 and kinds["DH-001"] == 0
    head, a = manifest(c, s)
    assert head["names"] == ["V-1", "V-2", "V-3"] or sorted(head["names"]) == ["V-1", "V-2", "V-3"]
    assert head["skipped"] == ["NC-1"] and head["audit"] is None and a["audit"].tolist() == [0, 0, 0]  # no audit
    az = dict(zip(head["names"], a["plan_az"].tolist(), strict=True))
    assert math.isnan(az["V-1"]) and math.isnan(az["V-2"])  # 100 m at 0.01° off vertical: 1.7 cm in plan
    assert abs(az["V-3"] - 30) < 1e-4


def test_audit_colours_stale_etag_and_304(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    url = f"{API}/viz/sets/{set_id}/holes"
    first = c.get(url)
    tag = first.headers["etag"]
    assert tag.startswith('"') and len(tag) == 66
    again = c.get(url, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == tag
    assert c.get(url, headers={"If-None-Match": f'"x", W/{tag}'}).status_code == 304

    r = c.post(f"{API}/audit/sets/{set_id}/run")
    assert wait_job(c, r.json()["source"], r.json()["id"], timeout=120)["status"] == "done"
    after = c.get(url, headers={"If-None-Match": tag})
    assert after.status_code == 200 and after.headers["etag"] != tag  # a new run is a new key
    head, a = binary.decode(after.content)
    assert head["audit"]["stale"] is False and head["audit"]["run_id"] > 0 and head["audit"]["at"]
    findings = c.get(f"{API}/audit/sets/{set_id}/findings", params={"limit": 1000}).json()["findings"]
    rank = {"error": 4, "warning": 3, "info": 2}
    worst: dict[str, int] = {}
    for f in findings:
        if f["hole"]:
            worst[f["hole"]] = max(worst.get(f["hole"], 1), rank[f["severity"]])
    got = dict(zip(head["names"], a["audit"].tolist(), strict=True))
    assert got == {n: worst.get(n, 1) for n in head["names"]}
    assert set(got.values()) >= {1, 4}  # clean holes and holes with errors (DH-012)

    _set_attr(c, set_id, "DH-001", "z", "500")  # an edit: «устарел», the colours stay
    head2, a2 = manifest(c, set_id)
    assert head2["audit"]["stale"] is True and a2["audit"].tolist() == a["audit"].tolist()
    assert a2["collar"].reshape(-1, 3)[head2["names"].index("DH-001")][2] == 500.0


def test_errors(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    r = c.get(f"{API}/viz/sets/999999/holes")
    assert r.status_code == 404 and r.json()["code"] == "set_not_found" and r.json()["hint"]
    r = c.get(f"{API}/viz/sets/{set_id}/holes", params={"method": "spline"})
    assert r.status_code == 422 and r.json()["code"] == "bad_method" and "Минимальная кривизна" in r.json()["hint"]
    head, _a = manifest(c, set_id, method="tangent")
    assert head["method"] == "tangent"
