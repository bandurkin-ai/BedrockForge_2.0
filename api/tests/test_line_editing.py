"""The string editor's server side: new layers, exact geometry, saving with versions."""

import json

import numpy as np
import pytest

from app.lines.measure import layer_measures, line_length, plan_area, polygon_area
from app.storage import blobs, gc

# UTM-size coordinates with digits a float32 cannot hold.
BASE = np.array([512345.678901234, 6789012.345678901, 250.123456789])


def _project(client) -> int:
    return client.post("/api/projects", json={"name": "Правка"}).json()["id"]


def _new_layer(client, pid: int, name: str = "РУДА") -> dict:
    created = client.post(f"/api/projects/{pid}/line-layers", json={"name": name, "color": "#ff8800"})
    assert created.status_code == 201, created.text
    (layer,) = created.json()["lines"]
    return layer


def _blob(client, url: str, dtype: str) -> np.ndarray:
    return np.frombuffer(client.get(url).content, dtype=dtype)


def _save(client, layer_id: int, base: int, lines: list[tuple[np.ndarray, bool]], attributes=None, **form):
    """Sends lines the way the editor does: closed ones repeat their first vertex."""
    parts = [np.vstack([p, p[:1]]) if closed else p for p, closed in lines]
    vertices = np.vstack(parts) if parts else np.zeros((0, 3))
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in parts])]).astype("<u4")
    closed = np.array([c for _, c in lines], dtype="u1")
    return client.put(
        f"/api/lines/{layer_id}/geometry",
        data={"base_version": str(base), "attributes": json.dumps(attributes or [{} for _ in lines]), **form},
        files={"vertices": ("v", vertices.astype("<f8").tobytes()), "offsets": ("o", offsets.tobytes()),
               "closed": ("c", closed.tobytes())},
    )


def _exact(client, layer_id: int):
    info = client.get(f"/api/lines/{layer_id}/exact").json()
    return info, _blob(client, info["vertices"], "<f8").reshape(-1, 3), _blob(client, info["offsets"], "<i8"), _blob(client, info["closed"], "u1")


# ---- measures (analytic)


def test_polygon_area_tilted_and_length():
    # A 30 x 20 rectangle turned about two axes: the area in its own plane stays 600, the plan area shrinks.
    a, b = np.radians(35.0), np.radians(62.0)
    rot_z = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    rot_x = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
    flat = np.array([[0, 0, 0], [30, 0, 0], [30, 20, 0], [0, 20, 0]], dtype=float)
    tilted = flat @ (rot_z @ rot_x).T + BASE

    assert polygon_area(tilted) == pytest.approx(600.0, rel=1e-9)
    assert plan_area(tilted) == pytest.approx(600.0 * np.cos(b), rel=1e-9)
    assert line_length(tilted, closed=True) == pytest.approx(100.0, rel=1e-9)  # coordinates of 6.8e6 m carry 1e-9 m each
    assert line_length(tilted, closed=False) == pytest.approx(80.0, rel=1e-9)  # coordinates of 6.8e6 m carry 1e-9 m each
    # The stored form of a closed line (first vertex repeated) measures the same.
    stored = np.vstack([tilted, tilted[:1]])
    m = layer_measures(stored, np.array([0, 5]), np.array([1]))
    assert m["area"] == pytest.approx(600.0, rel=1e-9) and m["length"] == pytest.approx(100.0, rel=1e-9)  # coordinates of 6.8e6 m carry 1e-9 m each
    assert m["closed"] == 1 and not m["flat"]


# ---- API


def test_new_layer_save_read_bitwise_and_versions(db_client):
    pid = _project(db_client)
    layer = _new_layer(db_client, pid)
    assert (layer["line_count"], layer["point_count"], layer["version"]) == (0, 0, 1)

    rng = np.random.default_rng(7)
    square = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], dtype=float) + BASE
    trace = rng.normal(size=(50, 3)) * 100 + BASE
    point = BASE[None, :] + 0.1
    saved = _save(db_client, layer["id"], 1, [(square, True), (trace, False), (point, False)],
                  [{"NAME": "контур", "GRADE": 1.5}, {}, {"NAME": "репер"}], note="первые линии")

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["version"], body["line_count"], body["point_count"]) == (2, 3, 56)
    assert body["stats"]["area"] == pytest.approx(100.0, rel=1e-9) and body["stats"]["closed"] == 1 and body["stats"]["points"] == 1
    assert body["extent"]["min"] == np.vstack([square, trace, point]).min(axis=0).tolist()

    info, v, o, c = _exact(db_client, layer["id"])
    # Bit for bit: what was sent is what is read.
    assert np.array_equal(v, np.vstack([square, square[:1], trace, point]))
    assert o.tolist() == [0, 5, 55, 56] and c.tolist() == [1, 0, 0]
    assert info["attributes"][0] == {"NAME": "контур", "GRADE": 1.5} and info["version"] == 2

    # The display copy follows: float32 relative to the origin, within half a millimetre.
    g = db_client.get(f"/api/lines/{layer['id']}/geometry").json()
    shown = _blob(db_client, g["vertices"], "<f4").reshape(-1, 3).astype(float) + g["origin"]
    assert np.abs(shown - v).max() < 5e-4 and g["version"] == 2

    journal = db_client.get(f"/api/lines/{layer['id']}/versions").json()
    assert [(r["version"], r["action"], r["line_count"], r["prev_line_count"], r["current"]) for r in journal] == [
        (2, "save", 3, 0, True), (1, "create", 0, 0, False)]
    assert journal[0]["note"] == "первые линии" and journal[0]["author"] is None


def test_restore_version_brings_arrays_back_and_keeps_history(db_client, db_engine):
    pid = _project(db_client)
    layer = _new_layer(db_client, pid)
    first = np.array([[0, 0, 0], [5, 0, 0], [5, 5, 1]], dtype=float) + BASE
    second = first + [100.0, 0.0, 0.0]
    assert _save(db_client, layer["id"], 1, [(first, False)]).status_code == 200
    assert _save(db_client, layer["id"], 2, [(second, False), (first[:2], False)]).status_code == 200
    _, v_before, _, _ = _exact(db_client, layer["id"])
    assert np.array_equal(v_before[:3], second)

    restored = db_client.post(f"/api/lines/{layer['id']}/versions/2/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 4 and restored.json()["line_count"] == 1
    _, v, o, c = _exact(db_client, layer["id"])
    assert np.array_equal(v, first) and o.tolist() == [0, 3] and c.tolist() == [0]
    journal = db_client.get(f"/api/lines/{layer['id']}/versions").json()
    assert [r["version"] for r in journal] == [4, 3, 2, 1] and journal[0]["action"] == "restore"
    assert "версии 2" in journal[0]["note"]
    assert db_client.post(f"/api/lines/{layer['id']}/versions/4/restore").status_code == 422
    assert db_client.post(f"/api/lines/{layer['id']}/versions/99/restore").status_code == 404

    # The arrays of old versions survive garbage collection: the journal refers to them.
    from datetime import timedelta
    from sqlalchemy.orm import Session
    with Session(db_engine) as session:
        gc.collect(session, grace=timedelta(0))
    again = db_client.post(f"/api/lines/{layer['id']}/versions/3/restore")
    assert again.status_code == 200 and again.json()["line_count"] == 2
    _, v, _, _ = _exact(db_client, layer["id"])
    assert np.array_equal(v[:3], second)


def test_second_tab_does_not_overwrite(db_client):
    pid = _project(db_client)
    layer = _new_layer(db_client, pid)
    line = np.array([[0, 0, 0], [1, 1, 1]], dtype=float) + BASE
    assert _save(db_client, layer["id"], 1, [(line, False)]).status_code == 200

    stale = _save(db_client, layer["id"], 1, [(line + 5, False)])

    assert stale.status_code == 409
    assert "другой вкладке" in stale.json()["detail"]
    _, v, _, _ = _exact(db_client, layer["id"])
    assert np.array_equal(v, line)


def test_bad_geometry_is_refused(db_client):
    pid = _project(db_client)
    layer = _new_layer(db_client, pid)
    two = np.array([[0, 0, 0], [1, 1, 1]], dtype=float)
    files = {"vertices": ("v", two.astype("<f8").tobytes()), "closed": ("c", np.array([0], dtype="u1").tobytes())}

    def put(offsets, closed=b"\x00", vertices=None):
        return db_client.put(f"/api/lines/{layer['id']}/geometry", data={"base_version": "1"},
                             files={**files, "offsets": ("o", np.array(offsets, dtype="<u4").tobytes()), "closed": ("c", closed),
                                    **({"vertices": ("v", vertices)} if vertices is not None else {})})

    assert put([0, 3]).status_code == 422  # more vertices than there are
    assert put([0, 0, 2], b"\x00\x00").status_code == 422  # an empty line
    assert put([0, 2], b"\x01").status_code == 422  # closed with two vertices
    assert put([0, 2], vertices=np.array([[0, 0, 0], [np.nan, 1, 1]]).astype("<f8").tobytes()).status_code == 422
    assert put([0, 2], vertices=b"\x00" * 20).status_code == 422
    assert db_client.get(f"/api/lines/{layer['id']}/exact").json()["version"] == 1


def test_move_lines_to_another_layer(db_client):
    pid = _project(db_client)
    a = _new_layer(db_client, pid, "A")
    file_id = db_client.get(f"/api/projects/{pid}/datasets").json()[0]["id"]
    added = db_client.post(f"/api/projects/{pid}/line-layers", json={"name": "B", "dataset_id": file_id})
    assert added.status_code == 201 and len(added.json()["lines"]) == 2
    b = added.json()["lines"][1]
    l0 = np.array([[0, 0, 0], [1, 0, 0]], dtype=float) + BASE
    l1 = np.array([[0, 5, 0], [1, 5, 0], [1, 6, 0]], dtype=float) + BASE
    l2 = np.array([[0, 9, 0], [1, 9, 0]], dtype=float) + BASE
    assert _save(db_client, a["id"], 1, [(l0, False), (l1, True), (l2, False)], [{"N": 0}, {"N": 1}, {"N": 2}]).status_code == 200

    moved = db_client.post(f"/api/lines/{a['id']}/move-lines", json={"lines": [1], "target_id": b["id"], "base_version": 2})

    assert moved.status_code == 200, moved.text
    src, dst = moved.json()
    assert (src["line_count"], src["version"], dst["line_count"], dst["version"]) == (2, 3, 1, 2)
    info_a, va, oa, _ = _exact(db_client, a["id"])
    info_b, vb, ob, cb = _exact(db_client, b["id"])
    assert np.array_equal(va, np.vstack([l0, l2])) and [x["N"] for x in info_a["attributes"]] == [0, 2]
    assert np.array_equal(vb, np.vstack([l1, l1[:1]])) and cb.tolist() == [1] and info_b["attributes"] == [{"N": 1}]
    assert db_client.post(f"/api/lines/{a['id']}/move-lines", json={"lines": [0], "target_id": b["id"], "base_version": 2}).status_code == 409


def test_imported_layer_has_version_one_and_can_be_edited(db_client):
    from tests.test_lines_rasters import SQUARE, micromine_str

    pid = _project(db_client)
    data = micromine_str([(x + 500000, y + 4600000, 1000.0, 1, "КОНТУР", "Восточный") for x, y in SQUARE])
    (layer,) = db_client.post(f"/api/projects/{pid}/lines", files={"file": ("Контуры.STR", data)}).json()["lines"]
    journal = db_client.get(f"/api/lines/{layer['id']}/versions").json()
    assert [(r["version"], r["action"]) for r in journal] == [(1, "import")]

    info, v, o, c = _exact(db_client, layer["id"])
    assert c.tolist() == [1] and np.array_equal(v[0], v[-1])  # the reader's form of a closed line
    moved = v[:-1].copy()
    moved[2] += [0.0, 0.0, 12.5]
    assert _save(db_client, layer["id"], 1, [(moved, True)], info["attributes"]).status_code == 200
    assert db_client.post(f"/api/lines/{layer['id']}/versions/1/restore").status_code == 200
    _, back, _, _ = _exact(db_client, layer["id"])
    assert np.array_equal(back, v)
