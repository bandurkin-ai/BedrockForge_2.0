"""The sections of the wireframes on a section of a series in one answer (ADR 0016 §3, §6; ТЗ 8.1.5):
`GET /section-series/{id}/sections/{k}/wireframes?ids=&seg=` — BFA1 `series_section`.

* bit for bit the lines of `GET /viz/wireframes/{id}/section?point=O_k&normal=v̂` of every wireframe, on a
  vertical series whose planes pass through the vertices and the faces of a cube exactly (UTM coordinates), on
  one at 63°, on an inclined one and on a plan; the point is O_k of the table of the series;
* a segment of a polyline — the lines of the plane of the segment cut by the half-spaces of its ends;
* the ETag: by the planes, k and the geometry — a new name of a wireframe or of the series keeps it, a new step
  or a deleted wireframe does not; If-None-Match → 304;
* a wireframe that is gone (and an id that never was one) is left out and listed in `missing`;
* wrong requests — 422 `bad_wireframes` / `bad_section`, 404 `series_not_found`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.geo import section as gs
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.wireframes import exact
from bf_core.wireframes import store as ws
from tests.meshutil import UTM, cube, grid, icosphere
from tests.test_import_job import API

pytestmark = pytest.mark.windows
URL = f"{API}/section-series"
LO = UTM.copy()
SIDE = 100.0


@pytest.fixture()
def wb(pclient):  # type: ignore[no-untyped-def]
    """A closed cube 100 m (40 cells a face) at UTM, an inclined open surface through it, a sphere beside it."""
    assert pclient.post(f"{API}/projects", json={"name": "Сечения серии"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    cv, ct = cube(40, SIDE, offset=LO)
    fv, ft = grid(24, 24, lambda x, y: 0.4 * x - 0.15 * y, step=7.0, offset=LO + [-30.0, -30.0, 10.0])
    sv, st = icosphere(4, 40.0, centre=LO + [170.0, 50.0, 50.0])
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        ids = {
            "cube": ws.put_wireframe(conn, store, cv, ct, name="Куб"),
            "fault": ws.put_wireframe(conn, store, fv, ft, name="Разлом"),
            "sphere": ws.put_wireframe(conn, store, sv, st, name="Шар"),
        }
    exact.forget()
    return pclient, project, ids


def create(c: Any, params: dict[str, Any]) -> dict[str, Any]:
    r = c.post(URL, json={"params": params})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def batch(c: Any, sid: int, k: int, ids: Any, **params: Any) -> tuple[Any, dict[str, Any], dict[str, np.ndarray]]:
    q = {"ids": ",".join(str(i) for i in ids), **params}
    r = c.get(f"{URL}/{sid}/sections/{k}/wireframes", params=q)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["cache-control"] == "no-cache"
    return r, *binary.decode(r.content)


def triple(v: Any) -> str:
    return ",".join(repr(float(x)) for x in v)


def single(c: Any, wf: int, point: Any, normal: Any) -> gs.Section:
    r = c.get(f"{API}/viz/wireframes/{wf}/section", params={"point": triple(point), "normal": triple(normal)})
    assert r.status_code == 200, r.text
    _h, a = binary.decode(r.content)
    return gs.Section(a["xyz"].reshape(-1, 3), a["offsets"].astype(np.int64), a["closed"].astype(bool))


def per_wireframe(head: dict[str, Any], a: dict[str, np.ndarray]) -> dict[int, gs.Section]:
    """The lines of the answer by wireframe, offsets from the first point of each."""
    xyz, offs, closed, wf = a["xyz"].reshape(-1, 3), a["offsets"].astype(np.int64), a["closed"], a["wf"]
    assert len(offs) == head["lines"] + 1 == len(wf) + 1 and len(xyz) == head["points"] == offs[-1]
    assert list(wf) == sorted(wf) and set(wf.tolist()) <= set(head["wireframes"])
    out = {}
    for i in head["wireframes"]:
        on = np.flatnonzero(wf == i)
        a0, b0 = (int(on[0]), int(on[-1]) + 1) if len(on) else (0, 0)
        p0 = int(offs[a0]) if len(on) else 0
        out[i] = gs.Section(xyz[p0 : int(offs[b0])] if len(on) else xyz[:0], offs[a0 : b0 + 1] - p0 if len(on)
                            else np.zeros(1, dtype=np.int64), closed[a0:b0].astype(bool))  # fmt: skip
    return out


def same(a: gs.Section, b: gs.Section) -> None:
    assert a.xyz.shape == b.xyz.shape and a.xyz.tobytes() == b.xyz.tobytes()
    assert a.offsets.tolist() == b.offsets.tolist() and a.closed.tolist() == b.closed.tolist()


SERIES = {
    # planes y = LO.y + 10 k: through rows of vertices of the cube, its two faces at k = 0 and k = 10
    "vertical": {"type": "vertical", "p1": [LO[0] - 20.0, LO[1], 0.0], "az": 90, "s0": 0, "s1": 100, "step": 10},
    "oblique": {"type": "vertical", "p1": [LO[0] - 40.0, LO[1] + 10.0, 0.0], "az": 63, "s0": -20, "s1": 120,
                "step": 17.5},  # fmt: skip
    "inclined": {"type": "inclined", "p1": [*(LO + [50.0, 50.0, 60.0])], "az": 117, "phi": 50, "s0": -60,
                 "s1": 60, "step": 15},  # fmt: skip
    "plan": {"type": "plan", "p1": [*(LO + [0.0, 0.0, SIDE])], "s0": 0, "s1": 100, "step": 12.5},
}


@pytest.mark.parametrize("name", list(SERIES))
def test_lines_are_those_of_the_section_of_one_wireframe_bit_for_bit(wb, name: str) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = wb
    s = create(c, SERIES[name])
    table = c.get(f"{URL}/{s['id']}/table").json()
    every = sorted(ids.values())
    lines = closed = 0
    for row in table["sections"]:
        _r, head, a = batch(c, s["id"], row["k"], every)
        assert head["kind"] == "series_section" and head["k"] == row["k"] and head["seg"] is None
        assert head["point"] == row["origin"] and head["clip"] is None  # O_k of the table, exactly
        assert head["wireframes"] == every and head["missing"] == []
        got = per_wireframe(head, a)
        for wf in every:
            same(got[wf], single(c, wf, head["point"], head["normal"]))
        lines += head["lines"]
        closed += int(a["closed"].sum())
    assert lines and closed  # the cube and the sphere are cut along closed lines somewhere


def test_a_plane_through_vertices_and_on_a_face_of_the_cube(wb) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = wb
    s = create(c, SERIES["vertical"])
    _r, head, a = batch(c, s["id"], 5, [ids["cube"]])  # y = LO.y + 50: through a row of vertices
    xyz = a["xyz"].reshape(-1, 3)
    assert head["lines"] == 1 and bool(a["closed"][0]) and (xyz[:, 1] == LO[1] + 50.0).all()
    assert len(xyz) == 160  # the vertices of the row themselves, each once
    _r, head, a = batch(c, s["id"], 10, [ids["cube"]])  # the face y = LO.y + 100, the solid behind the plane
    xyz = a["xyz"].reshape(-1, 3)
    assert head["lines"] == 1 and bool(a["closed"][0]) and len(xyz) == 160 and (xyz[:, 1] == LO[1] + SIDE).all()
    _r, head, a = batch(c, s["id"], 0, [ids["cube"]])  # the face y = LO.y, the solid in front of it: nothing
    assert head["lines"] == 0 and len(a["xyz"]) == 0


def test_a_segment_of_a_polyline_is_cut_at_its_ends(wb) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = wb
    bend = [[LO[0] - 60.0, LO[1] + 20.0], [LO[0] + 45.0, LO[1] + 60.0], [LO[0] + 160.0, LO[1] + 35.0],
            [LO[0] + 260.0, LO[1] + 90.0]]  # fmt: skip
    s = create(c, {"type": "polyline", "polyline": bend, "corridor": 30})
    every = sorted(ids.values())
    total = 0
    for seg in range(3):
        _r, head, a = batch(c, s["id"], 0, every, seg=seg)
        assert head["seg"] == seg and head["k"] == 0 and len(head["clip"]) == 2
        assert head["point"] == [*bend[seg], 0.0]
        clip = np.array(head["clip"])
        got = per_wireframe(head, a)
        for wf in every:
            same(got[wf], single(c, wf, head["point"], head["normal"]).clip(clip))
        xyz = a["xyz"].reshape(-1, 3)
        if len(xyz):
            assert (xyz @ clip[:, :3].T + clip[:, 3] >= -1e-9).all()
        total += head["lines"]
    assert total
    r = c.get(f"{URL}/{s['id']}/sections/0/wireframes", params={"ids": "1", "seg": 3})
    assert r.status_code == 422 and r.json()["code"] == "bad_section" and r.json()["hint"]
    r = c.get(f"{URL}/{s['id']}/sections/1/wireframes", params={"ids": "1"})
    assert r.status_code == 422 and r.json()["code"] == "bad_section"


def test_etag_by_the_planes_and_the_geometry(wb) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = wb
    s = create(c, SERIES["oblique"])
    every = sorted(ids.values())
    r, _head, _a = batch(c, s["id"], 3, every)
    tag = r.headers["etag"]
    url, q = f"{URL}/{s['id']}/sections/3/wireframes", {"ids": ",".join(map(str, every))}
    r304 = c.get(url, params=q, headers={"If-None-Match": tag})
    assert r304.status_code == 304 and r304.content == b"" and r304.headers["etag"] == tag
    # names do not matter: neither of a wireframe nor of the series
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == ids["cube"]).values(name="Куб 2", version=7))
    assert c.patch(f"{URL}/{s['id']}", json={"expected_version": 1, "name": "Профили"}).status_code == 200
    assert c.get(url, params=q, headers={"If-None-Match": tag}).status_code == 304
    # another order of the ids is the same request
    back = {"ids": ",".join(map(str, every[::-1]))}
    assert c.get(url, params=back, headers={"If-None-Match": tag}).status_code == 304
    # another section, another step, fewer wireframes: other answers
    assert batch(c, s["id"], 4, every)[0].headers["etag"] != tag
    assert batch(c, s["id"], 3, every[:2])[0].headers["etag"] != tag
    assert c.patch(f"{URL}/{s['id']}", json={"expected_version": 2, "params": {"step": 17.0}}).status_code == 200
    r = c.get(url, params=q, headers={"If-None-Match": tag})
    assert r.status_code == 200 and r.headers["etag"] != tag


def test_a_deleted_wireframe_is_left_out_and_listed(wb) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = wb
    s = create(c, SERIES["vertical"])
    every = sorted(ids.values())
    r, head, a = batch(c, s["id"], 4, every)
    before = per_wireframe(head, a)
    tag = r.headers["etag"]
    assert c.delete(f"{API}/wireframes/{ids['fault']}").status_code == 200
    asked = [*every, 987654]
    r, head, a = batch(c, s["id"], 4, asked)
    assert r.headers["etag"] != tag
    assert head["missing"] == [ids["fault"], 987654] and head["wireframes"] == [ids["cube"], ids["sphere"]]
    got = per_wireframe(head, a)
    for wf in (ids["cube"], ids["sphere"]):
        same(got[wf], before[wf])
    assert ids["fault"] not in a["wf"].tolist()
    _r, head, a = batch(c, s["id"], 4, [])
    assert head["wireframes"] == [] and head["lines"] == 0 and len(a["xyz"]) == 0


def test_wrong_requests(wb) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = wb
    s = create(c, SERIES["vertical"])
    url = f"{URL}/{s['id']}/sections/2/wireframes"

    def refused(params: dict[str, Any], status: int, code: str, path: str = url) -> None:
        r = c.get(path, params=params)
        body = r.json()
        assert r.status_code == status and body["code"] == code, r.text
        assert body["message"] and body["hint"]

    refused({}, 422, "bad_wireframes")
    refused({"ids": "1,x"}, 422, "bad_wireframes")
    refused({"ids": "-1"}, 422, "bad_wireframes")
    refused({"ids": ",".join(str(i) for i in range(1, 202))}, 422, "bad_wireframes")
    refused({"ids": "1", "seg": 1}, 422, "bad_section")
    refused({"ids": "1"}, 422, "bad_section", f"{URL}/{s['id']}/sections/11/wireframes")
    refused({"ids": "1"}, 422, "bad_section", f"{URL}/{s['id']}/sections/-1/wireframes")
    refused({"ids": "1"}, 404, "series_not_found", f"{URL}/99999/sections/0/wireframes")
    assert c.get(url, params={"ids": ",".join(str(i) for i in range(1, 201))}).status_code == 200  # 200 is fine
