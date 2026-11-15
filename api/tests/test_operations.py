"""Operations on lines and wireframes through the API: a job makes a new object, sources stay,
the result keeps what it was made from; preconditions are refused with a message."""

import json

import numpy as np
import pytest

BASE = np.array([512345.678, 6789012.345, 250.0])


def _project(client) -> int:
    return client.post("/api/projects", json={"name": "Операции"}).json()["id"]


def _box(lo, hi) -> tuple[np.ndarray, np.ndarray]:
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=float)
    t = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return v + BASE, t


def _obj(v: np.ndarray, t: np.ndarray, name: str = "mesh") -> bytes:
    return (f"o {name}\n" + "".join(f"v {x!r} {y!r} {z!r}\n" for x, y, z in v.tolist()) + "".join(f"f {a + 1} {b + 1} {c + 1}\n" for a, b, c in t.tolist())).encode()


def _wireframe(client, pid: int, name: str, v, t) -> dict:
    r = client.post(f"/api/projects/{pid}/wireframes", files={"file": (f"{name}.obj", _obj(v, t, name))})
    assert r.status_code == 201, r.text
    return r.json()["wireframes"][0]


def _layer(client, pid: int, name: str, lines: list[tuple[np.ndarray, bool]], attributes=None) -> dict:
    layer = client.post(f"/api/projects/{pid}/line-layers", json={"name": name}).json()["lines"][0]
    parts = [np.vstack([p, p[:1]]) if closed else p for p, closed in lines]
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in parts])]).astype("<u4")
    r = client.put(f"/api/lines/{layer['id']}/geometry", data={"base_version": "1", "attributes": json.dumps(attributes or [{} for _ in lines])},
                   files={"vertices": ("v", np.vstack(parts).astype("<f8").tobytes()), "offsets": ("o", offsets.tobytes()),
                          "closed": ("c", np.array([c for _, c in lines], dtype="u1").tobytes())})
    assert r.status_code == 200, r.text
    return r.json()


def _run(client, run_jobs, pid: int, op: str, params: dict, name: str | None = None) -> dict:
    started = client.post(f"/api/projects/{pid}/operations", json={"op": op, "params": params, "name": name})
    assert started.status_code == 202, started.text
    run_jobs()
    job = client.get(f"/api/jobs/{started.json()['id']}").json()
    assert job["status"] == "done", job["error"]
    return job["result"]


def _wireframes(client, pid: int) -> dict[int, dict]:
    return {w["id"]: w for d in client.get(f"/api/projects/{pid}/datasets").json() for w in d.get("wireframes") or []}


def _contour(x: float, pts) -> np.ndarray:
    """A closed contour on the vertical section x = const."""
    return np.array([[x, y, z] for y, z in pts], dtype=float) + BASE


SQUARE = [(0, 0), (20, 0), (20, 10), (0, 10)]


def test_stitch_contours_into_a_solid_with_ties_and_rebuild(db_client, run_jobs):
    pid = _project(db_client)
    tie = np.array([[0, 0, 0], [30, 0, 0]], dtype=float) + BASE
    layer = _layer(db_client, pid, "РУДА", [(_contour(0, SQUARE), True), (_contour(30, SQUARE), True), (_contour(60, SQUARE), True), (tie, False)])

    result = _run(db_client, run_jobs, pid, "stitch", {"contours": [{"layer_id": layer["id"], "lines": None}]})

    (wid,) = result["wireframe_ids"]
    w = _wireframes(db_client, pid)[wid]
    # A prism: area of the contour × distance between the end sections.
    assert w["stats"]["closed"] and w["stats"]["orientation"] == "outward"
    assert w["stats"]["volume"] == pytest.approx(200.0 * 60.0, rel=1e-9)
    assert w["name"] == "Тело: РУДА" and result["report"]["ties_used"] == 1
    made = w["stats"]["operation"]
    assert made["op"] == "stitch" and made["sources"][0]["id"] == layer["id"] and made["sources"][0]["version"] == 2
    assert w["stats"]["self_intersections"]["pairs"] == 0 and w["stats"]["orientation_conflicts"] == 0  # the checks job ran too

    # The contours are edited, the body is rebuilt with the same parameters.
    info = db_client.get(f"/api/lines/{layer['id']}/exact").json()
    v = np.frombuffer(db_client.get(info["vertices"]).content, dtype="<f8").reshape(-1, 3).copy()
    v[10:15, 0] += 30.0  # the last section moves from 60 to 90
    o = np.frombuffer(db_client.get(info["offsets"]).content, dtype="<i8").astype("<u4")
    c = db_client.get(info["closed"]).content
    saved = db_client.put(f"/api/lines/{layer['id']}/geometry", data={"base_version": "2"},
                          files={"vertices": ("v", v.tobytes()), "offsets": ("o", o.tobytes()), "closed": ("c", c)})
    assert saved.status_code == 200, saved.text
    again = db_client.post(f"/api/wireframes/{wid}/rebuild")
    assert again.status_code == 202, again.text
    run_jobs()
    rebuilt = [x for i, x in _wireframes(db_client, pid).items() if i != wid]
    assert len(rebuilt) == 1 and rebuilt[0]["stats"]["volume"] == pytest.approx(200.0 * 90.0, rel=1e-9)
    assert rebuilt[0]["stats"]["operation"]["sources"][0]["version"] == 3


def test_stitch_needs_two_closed_contours(db_client, run_jobs):
    pid = _project(db_client)
    layer = _layer(db_client, pid, "L", [(_contour(0, SQUARE), True), (_contour(30, SQUARE)[:3], False)])
    started = db_client.post(f"/api/projects/{pid}/operations", json={"op": "stitch", "params": {"contours": [{"layer_id": layer["id"]}]}})
    assert started.status_code == 202
    run_jobs()
    job = db_client.get(f"/api/jobs/{started.json()['id']}").json()
    # The job says what is wrong and what to do; nothing is added to the tree.
    assert job["status"] == "failed" and "двух замкнутых контуров" in job["error"] and "Замкните" in job["error"]
    assert _wireframes(db_client, pid) == {}


def test_boolean_volumes_and_preconditions(db_client, run_jobs):
    pid = _project(db_client)
    a = _wireframe(db_client, pid, "A", *_box((0, 0, 0), (10, 10, 10)))
    b = _wireframe(db_client, pid, "B", *_box((4, 3, 2), (14, 13, 12)))
    volumes = {}
    for op, sign in (("union", "∪"), ("intersection", "∩"), ("difference", "−")):
        result = _run(db_client, run_jobs, pid, "boolean", {"op": op, "a": a["id"], "b": b["id"]})
        w = _wireframes(db_client, pid)[result["wireframe_ids"][0]]
        assert w["name"] == f"A {sign} B" and w["stats"]["closed"] and w["stats"]["operation"]["params"]["op"] == op
        volumes[op] = w["stats"]["volume"]
    assert volumes["intersection"] == pytest.approx(6 * 7 * 8, abs=1e-6)
    assert volumes["difference"] == pytest.approx(1000 - 336, abs=1e-6)
    # V(A ∪ B) = V(A) + V(B) − V(A ∩ B)
    assert volumes["union"] == pytest.approx(2000 - volumes["intersection"], abs=1e-6)
    # Sources are untouched.
    assert _wireframes(db_client, pid)[a["id"]]["stats"]["volume"] == pytest.approx(1000.0)

    # An open surface is refused before any job starts, with the tool that fixes it.
    v, t = _box((0, 0, 0), (5, 5, 5))
    opened = _wireframe(db_client, pid, "Открытый", v, t[:-2])
    refused = db_client.post(f"/api/projects/{pid}/operations", json={"op": "boolean", "params": {"op": "union", "a": a["id"], "b": opened["id"]}})
    assert refused.status_code == 422
    assert "не замкнут" in refused.json()["detail"] and "Исправить каркас" in refused.json()["detail"]
    assert db_client.post(f"/api/projects/{pid}/operations", json={"op": "нет", "params": {}}).status_code == 422


def test_repair_closes_a_hole_and_reports_before_after(db_client, run_jobs):
    pid = _project(db_client)
    v, t = _box((0, 0, 0), (10, 10, 10))
    broken = _wireframe(db_client, pid, "Тело", v, np.vstack([t[:-2], t[:1][:, ::-1]]))  # a hole and a flipped duplicate

    result = _run(db_client, run_jobs, pid, "repair", {"wireframe_id": broken["id"]})

    w = _wireframes(db_client, pid)[result["wireframe_ids"][0]]
    assert w["name"] == "Тело (исправлен)" and w["stats"]["closed"] and w["stats"]["volume"] == pytest.approx(1000.0, rel=1e-9)
    report = result["report"]
    assert report["before"]["open_edges"] > 0 and report["after"]["open_edges"] == 0 and report["after"]["closed"]
    assert {s["step"] for s in report["steps"]} >= {"weld", "holes", "orient"}


def test_clip_merge_split_and_intersection_lines(db_client, run_jobs):
    pid = _project(db_client)
    a = _wireframe(db_client, pid, "A", *_box((0, 0, 0), (10, 10, 10)))
    b = _wireframe(db_client, pid, "B", *_box((20, 0, 0), (30, 10, 10)))

    cut = _run(db_client, run_jobs, pid, "clip_plane", {"wireframe_id": a["id"], "point": (BASE + [0, 0, 4]).tolist(), "normal": [0, 0, 1]})
    parts = [_wireframes(db_client, pid)[i] for i in cut["wireframe_ids"]]
    assert sorted(p["name"] for p in parts) == ["A: выше уровня", "A: ниже уровня"]
    # The parts add up to the solid.
    assert sum(p["stats"]["volume"] for p in parts) == pytest.approx(1000.0, rel=1e-9)

    polygon = _layer(db_client, pid, "Лицензия", [(np.array([[2, 2, 0], [8, 2, 0], [8, 8, 0], [2, 8, 0]], dtype=float) + BASE, True)])
    inside = _run(db_client, run_jobs, pid, "clip_polygon", {"wireframe_id": a["id"], "polygon": {"layer_id": polygon["id"]}, "keep": "inside"})
    assert _wireframes(db_client, pid)[inside["wireframe_ids"][0]]["stats"]["volume"] == pytest.approx(360.0, rel=1e-9)

    merged = _run(db_client, run_jobs, pid, "merge", {"wireframe_ids": [a["id"], b["id"]]})
    one = _wireframes(db_client, pid)[merged["wireframe_ids"][0]]
    assert one["triangle_count"] == 24 and one["stats"]["volume"] == pytest.approx(2000.0, rel=1e-9)
    split = _run(db_client, run_jobs, pid, "split", {"wireframe_id": one["id"]})
    assert len(split["wireframe_ids"]) == 2

    c = _wireframe(db_client, pid, "C", *_box((5, 5, 5), (15, 15, 15)))
    lines = _run(db_client, run_jobs, pid, "intersection_lines", {"a": a["id"], "b": c["id"]})
    assert lines["kind"] == "lines" and lines["report"]["lines"] >= 1
    layer = next(x for d in db_client.get(f"/api/projects/{pid}/datasets").json() for x in d.get("lines") or [] if x["id"] == lines["layer_ids"][0])
    assert layer["stats"]["length"] == pytest.approx(30.0, rel=1e-9)  # three 5 x 5 corners of the common box: 6 edges of 5 m


def test_dtm_contours_and_volume_between(db_client, run_jobs):
    pid = _project(db_client)
    xs, ys = np.meshgrid(np.arange(0, 101, 10.0), np.arange(0, 51, 10.0))
    ground = np.column_stack([xs.ravel(), ys.ravel(), 100 + 0.1 * xs.ravel()]) + BASE
    pit = np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, 95.0)]) + BASE
    points = lambda p: [(row[None, :], False) for row in p]  # noqa: E731 — every point is a line of one vertex
    g = _layer(db_client, pid, "Рельеф", points(ground))
    p = _layer(db_client, pid, "Дно", points(pit))

    top = _run(db_client, run_jobs, pid, "dtm", {"layers": [g["id"]]})
    bottom = _run(db_client, run_jobs, pid, "dtm", {"layers": [p["id"]]})
    surface = _wireframes(db_client, pid)[top["wireframe_ids"][0]]
    assert surface["name"] == "ЦМР: Рельеф" and not surface["stats"]["closed"] and surface["triangle_count"] == 100
    assert surface["stats"]["area"] == pytest.approx(5000 * np.hypot(1, 0.1), rel=1e-6)

    iso = _run(db_client, run_jobs, pid, "contours", {"wireframe_id": surface["id"], "interval": 1.0, "base": BASE[2] + 0.5, "major_every": 5})
    assert iso["report"]["levels"] == 10  # 350.5 … 359.5: the levels inside 350 … 360
    names = [x["name"] for d in db_client.get(f"/api/projects/{pid}/datasets").json() for x in d.get("lines") or [] if x["id"] in iso["layer_ids"]]
    assert names == ["Основные 5 м", "Дополнительные 1 м"]

    volume = _run(db_client, run_jobs, pid, "volume_between", {"top": surface["id"], "bottom": bottom["wireframe_ids"][0]})
    # Between z = 100 + 0.1 x and z = 95 over 100 x 50: mean thickness 10 m.
    assert volume["report"]["cut"] == pytest.approx(50000.0, rel=1e-9) and volume["report"]["fill"] == pytest.approx(0.0, abs=1e-6)
    assert volume["dataset_ids"] == []
    level = _run(db_client, run_jobs, pid, "volume_between", {"top": surface["id"], "level": float(BASE[2] + 105)})
    assert level["report"]["cut"] == pytest.approx(level["report"]["fill"], rel=1e-9) and level["report"]["cut"] == pytest.approx(6250.0, rel=1e-9)


def test_pick_triangle_and_export_round_trip(db_client, tmp_path):
    pid = _project(db_client)
    v, t = _box((0, 0, 0), (10, 10, 10))
    a = _wireframe(db_client, pid, "A", v, t)

    hit = db_client.post("/api/pick", json={"wireframe_ids": [a["id"]], "origin": (BASE + [3, 4, 50]).tolist(), "direction": [0, 0, -1]}).json()
    assert hit["id"] == a["id"] and hit["hit"]["point"] == pytest.approx((BASE + [3, 4, 10]).tolist(), abs=1e-9)
    assert hit["hit"]["dip"] == pytest.approx(0.0, abs=1e-9) and hit["hit"]["area"] == pytest.approx(50.0)
    side = db_client.post(f"/api/wireframes/{a['id']}/pick", json={"origin": (BASE + [-20, 5, 5]).tolist(), "direction": [1, 0, 0]}).json()["hit"]
    assert side["dip"] == pytest.approx(90.0) and side["dip_direction"] == pytest.approx(270.0)  # the face looks west
    assert db_client.post("/api/pick", json={"wireframe_ids": [a["id"]], "origin": (BASE + [50, 50, 50]).tolist(), "direction": [0, 0, 1]}).json()["hit"] is None

    from app.formats.wireframes import read_wireframes
    for fmt in ("obj", "dxf"):
        r = db_client.get(f"/api/export/wireframes?ids={a['id']}&format={fmt}")
        assert r.status_code == 200, r.text
        path = tmp_path / f"a.{fmt}"
        path.write_bytes(r.content)
        _, (mesh,) = read_wireframes(path, f".{fmt}")
        # Coordinates come back bit for bit.
        assert {tuple(x) for x in mesh.vertices.tolist()} == {tuple(x) for x in v.tolist()}

    square = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 1.5], [0, 10, 0]], dtype=float) + BASE + 0.123456789012
    layer = _layer(db_client, pid, "КОНТУР", [(square, True), (square[:2] + 50, False), (square[:1] + 70, False)], [{"NAME": "руда"}, {}, {}])
    from app.formats.lines import read_lines
    for fmt in ("str", "dxf"):
        r = db_client.get(f"/api/export/lines?ids={layer['id']}&format={fmt}")
        assert r.status_code == 200, r.text
        path = tmp_path / f"l.{fmt}"
        path.write_bytes(r.content)
        _, (read,) = read_lines(path, f".{fmt}")
        assert read.name == "КОНТУР" and [len(x.vertices) for x in read.lines] == [5, 2, 1] and read.lines[0].closed
        assert np.array_equal(read.lines[0].vertices, np.vstack([square, square[:1]]))  # float64, bit for bit
    assert db_client.get("/api/export/lines?ids=999999&format=str").status_code == 404


def test_hole_snap_targets_are_exact(db_client):
    from tests.helpers import new_set
    from app.models import Assay, Collar
    from sqlalchemy.orm import Session
    from app.db import get_session
    from app.main import app

    set_id = new_set(db_client, "Привязка")
    session: Session = next(app.dependency_overrides[get_session]())
    x, y, z = 512345.678901234, 6789012.345678901, 301.23456789
    session.add(Collar(set_id=set_id, hole_id="DH-1", x=x, y=y, z=z, td=100.0, azimuth=0.0, dip=-90.0, attributes={}))
    session.add_all([Assay(set_id=set_id, hole_id="DH-1", from_m=10.0, to_m=12.5, analytes={}), Assay(set_id=set_id, hole_id="DH-1", from_m=12.5, to_m=20.0, analytes={})])
    session.commit()

    body = db_client.get(f"/api/sets/{set_id}/snap").content

    n = int.from_bytes(body[:4], "little")
    header = json.loads(body[4:4 + n])
    at = 4 + n + (-(4 + n) % 8)
    arrays = {}
    for name, dtype, count in header["arrays"]:
        arrays[name] = np.frombuffer(body, dtype=dtype, count=count, offset=at)
        at += count * np.dtype(dtype).itemsize
        at += -at % 8
    assert header["holes"] == ["DH-1"]
    assert arrays["collars"].tolist() == [x, y, z]  # not rounded to a centimetre
    bounds = arrays["bounds"].reshape(-1, 3)
    assert arrays["bound_depth"].tolist() == [10.0, 12.5, 20.0]
    assert np.allclose(bounds[:, 2], [z - 10, z - 12.5, z - 20], atol=1e-9) and np.all(bounds[:, 0] == x)
