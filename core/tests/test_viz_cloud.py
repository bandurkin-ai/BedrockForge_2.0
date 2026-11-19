"""A point cloud in the 3D window (ADR 0014 §2; `viz/cloud.py`, the routes of `viz/router.py`): the manifest and
the parts at the addresses of a line layer; a level is a prefix of the full part; float32 offsets within
0.25 mm; `value` by the colour modes (class codes; `h` and `dz` in steps of 0.02 m, 255 — none); ETags that
follow the data and not the name of the layer; 304 before anything is read; an ordinary layer of points is
served as before."""

from __future__ import annotations

import math

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.formats.lineset import from_points
from bf_core.lines import cloud as lc
from bf_core.lines import cloud_build as cb
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.viz import cloud as vc
from tests.test_cloud_build import portions
from tests.test_cloud_store import F32, UTM, scene
from tests.test_import_job import API

pytestmark = pytest.mark.windows

N = 60_000
LIMIT = 8192


@pytest.fixture()
def shown(pclient):  # type: ignore[no-untyped-def]
    """A project with a cloud in parts of 8192 points and a plain layer of points:
    (client, project, the cloud in memory, cloud id, points id)."""
    r = pclient.post(f"{API}/projects", json={"name": "Облако"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    store = BlobStore(project.folder)
    chunk = scene(41, N)
    data = cb.build_cloud([chunk], part_points=LIMIT)
    staged = cb.stage_cloud(store, portions(chunk, 7000), part_points=LIMIT)
    with dialect.write_tx(project.engine) as conn:
        file_id = ls_store.create_file(conn, store, name="съёмка.las", meta={"format": "las"})
        cloud_id = lc.commit_cloud(
            conn,
            store,
            staged,
            file_id=file_id,
            name="Съёмка",
            color="#3366cc",
            source={"kind": "las", "every": 2, "las": {"wkt": "x" * 2000}},
        )
        points_id = ls_store.put_layer(
            conn,
            store,
            from_points("Пикеты", UTM + np.random.default_rng(2).uniform(0, 400, (3000, 3))),
            file_id=file_id,
        )
    return pclient, project, data, cloud_id, points_id


def _get(c, url: str, **params):  # type: ignore[no-untyped-def]
    r = c.get(f"{API}/viz/lines/{url}", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["cache-control"] == "no-cache"
    return r, *binary.decode(r.content)


def _classify(project, cloud_id: int, **values: np.ndarray) -> lc.Cloud:  # type: ignore[no-untyped-def]
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        cloud = lc.open_cloud(conn, store, cloud_id)
    staged = lc.stage_values(store, cloud, values)
    extra = {"ground": {"params": {"cr": 1.0}}} if "h" in values else {}  # not given — kept
    with dialect.write_tx(project.engine) as conn:
        lc.replace_values(conn, store, cloud, staged, **extra)
    with dialect.read_tx(project.engine) as conn:
        return lc.open_cloud(conn, store, cloud_id)


def test_quantize_steps_of_two_centimetres_and_255_for_none() -> None:
    v = np.array([-30.0, -2.5, -2.49, -0.011, -0.009, 0.0, 0.009, 0.011, 0.02, 2.48, 2.5, 30.0, math.nan, math.inf])
    assert vc.quantize(v).tolist() == [0, 0, 0, 124, 125, 125, 125, 126, 126, 249, 250, 250, 255, 250]
    assert vc.quantize(v.astype(np.float32)).dtype == np.uint8
    back = vc.quantize(np.arange(-2.5, 2.5, 0.004)) * vc.VALUE_STEP + vc.VALUE_MIN
    assert np.abs(back - np.arange(-2.5, 2.5, 0.004)).max() <= vc.VALUE_STEP / 2 + 1e-12


def test_manifest_and_parts_of_a_cloud(shown) -> None:  # type: ignore[no-untyped-def]
    c, _project, data, cloud_id, _points = shown
    r, head, arrays = _get(c, str(cloud_id))
    assert arrays == {} and head["kind"] == "line_layer" and head["layer"]["name"] == "Съёмка"
    assert head["points_only"] and (head["lines"], head["points"], head["pieces"]) == (0, N, N)
    assert head["color"] == "#3366cc" and head["labels"] is False and head["max_vertices"] == lc.PART_POINTS
    assert head["lods"] == [{"lod": j, "tol": tol} for j, tol in enumerate((0.0, 0.5, 2.0, 8.0, 32.0))]
    assert head["bbox"] == list(data.bbox) and len(head["parts"]) == len(data.parts) > 4
    cloud = head["cloud"]
    assert cloud["classes"] == {str(k): v for k, v in sorted(data.classes.items())}
    assert [m["mode"] for m in cloud["modes"]] == ["class"] and len(cloud["modes"][0]["key"]) == 16
    assert cloud["value"] == {"step": 0.02, "min": -2.5, "max": 2.5, "empty": 255}
    assert cloud["source"] == {"kind": "las", "every": 2}  # not the metadata of the file
    assert cloud["ground"] is None and cloud["dz"] is None
    seen = np.zeros(N, dtype=np.int64)
    for k, (entry, part) in enumerate(zip(head["parts"], data.parts, strict=True)):
        assert (entry["k"], entry["n"], entry["points"], entry["levels"]) == (k, part.n[0], part.n[0], list(part.n))
        assert entry["bbox"] == list(part.bbox) and entry["origin"] == list(part.origin)
        assert len(set(entry["etags"])) == 5
        full: dict[str, bytes] = {}
        for lod in (0, 4, 2, 1, 3):
            rp, h, a = _get(c, f"{cloud_id}/parts/{k}", lod=lod)
            assert rp.headers["etag"] == entry["etags"][lod]
            n = part.n[lod]
            assert (h["kind"], h["k"], h["lod"], h["tol"]) == ("lines", k, lod, lc.LEVELS[lod])
            assert h["points_only"] and h["cloud"] and h["mode"] == "class" and h["labels"] is None
            assert (h["n"], h["points"], h["source_points"], h["start"]) == (n, n, part.n[0], part.start)
            assert h["origin"] == entry["origin"] and h["bbox"] == entry["bbox"] and h["simplified"] is (n < part.n[0])
            assert {name: v.dtype.str for name, v in a.items()} == {"xyz": "<f4", "line": "<u4", "value": "|u1"}
            rows = slice(part.start, part.start + n)
            assert np.array_equal(a["line"], np.arange(part.start, part.start + n))  # the row of the attributes
            assert np.array_equal(a["value"], data.cls[rows])
            back = a["xyz"].reshape(-1, 3).astype(np.float64) + np.array(part.origin)
            assert np.abs(back - data.xyz[rows]).max() <= F32
            if lod == 0:
                full = {name: v.tobytes() for name, v in a.items()}
                seen[a["line"]] += 1
            else:  # a level is the head of the full part, byte for byte
                assert all(full[name].startswith(v.tobytes()) for name, v in a.items())
    assert seen.tolist() == [1] * N  # the parts at level 0 are the cloud


def test_colour_modes_give_the_value_of_a_point(shown) -> None:  # type: ignore[no-untyped-def]
    c, project, data, cloud_id, _points = shown
    for mode, word in (("h", "Фильтр земли"), ("dz", "ЦТМ")):
        r = c.get(f"{API}/viz/lines/{cloud_id}/parts/0", params={"color": mode})
        assert r.status_code == 409 and r.json()["code"] == "color_mode_unavailable" and word in r.json()["hint"]
    rng = np.random.default_rng(7)
    cls = rng.choice(np.array([1, 2, 3, 4, 5, 7], dtype=np.uint8), N)
    h = rng.normal(0.0, 2.0, N).astype(np.float32)
    h[::11] = np.nan
    h[5], h[6], h[7] = -40.0, 40.0, 0.0
    cloud = _classify(project, cloud_id, **{"class": cls, "h": h})
    dz = rng.normal(0.0, 0.1, N).astype(np.float32)
    dz[::5] = np.nan
    cloud = _classify(project, cloud_id, dz=dz)
    _r, head, _a = _get(c, str(cloud_id))
    assert [m["mode"] for m in head["cloud"]["modes"]] == ["class", "h", "dz"]
    assert [m["key"] for m in head["cloud"]["modes"]] == [cloud.shas[m][:16] for m in ("class", "h", "dz")]
    assert head["cloud"]["classes"] == {
        str(k): int(v) for k, v in zip(*np.unique(cls, return_counts=True), strict=True)
    }
    assert head["cloud"]["ground"] == {"params": {"cr": 1.0}} and head["version"] == 3
    part = data.parts[2]
    for lod in (0, 3):
        rows = slice(part.start, part.start + part.n[lod])
        got = {mode: _get(c, f"{cloud_id}/parts/2", lod=lod, color=mode) for mode in ("class", "h", "dz")}
        assert np.array_equal(got["class"][2]["value"], cls[rows])
        assert np.array_equal(_get(c, f"{cloud_id}/parts/2", lod=lod)[2]["value"], cls[rows])  # the default
        for mode, values in (("h", h), ("dz", dz)):
            _r, hh, a = got[mode]
            want = values[rows].astype(np.float64)
            code = a["value"].astype(np.float64)
            assert hh["mode"] == mode and np.array_equal(a["value"] == 255, np.isnan(want))
            inside = ~np.isnan(want) & (np.abs(want) < 2.5)
            assert np.abs(code[inside] * 0.02 - 2.5 - want[inside]).max() <= 0.01 + 1e-9
            assert np.all(code[want >= 2.5] == 250) and np.all(code[want <= -2.5] == 0)
            assert np.array_equal(a["xyz"], got["class"][2]["xyz"]) and np.array_equal(
                a["line"], got["class"][2]["line"]
            )
    first = _get(c, f"{cloud_id}/parts/0", color="h")[2]
    at = {int(row): i for i, row in enumerate(first["line"].tolist())}
    assert [int(first["value"][at[row]]) for row in (5, 6, 7) if row in at] == [
        code for row, code in ((5, 0), (6, 250), (7, 125)) if row in at
    ]


def test_etags_follow_the_data_of_a_part(shown, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, project, _data, cloud_id, points_id = shown
    r, head, _a = _get(c, str(cloud_id))
    tag, tags = r.headers["etag"], head["parts"][1]["etags"]
    assert len({tag, *tags, *head["parts"][0]["etags"]}) == 11

    def boom(*_a: object, **_k: object) -> bytes:
        raise AssertionError("a 304 must not read anything")

    with monkeypatch.context() as m:
        m.setattr(vc, "manifest", boom)
        m.setattr(vc, "part", boom)
        m.setattr(vc, "_slice", boom)
        assert c.get(f"{API}/viz/lines/{cloud_id}", headers={"If-None-Match": tag}).status_code == 304
        for lod in range(5):
            r304 = c.get(
                f"{API}/viz/lines/{cloud_id}/parts/1", params={"lod": lod}, headers={"If-None-Match": tags[lod]}
            )
            assert r304.status_code == 304 and r304.headers["etag"] == tags[lod] and not r304.content
    wrong = c.get(f"{API}/viz/lines/{cloud_id}/parts/1", params={"lod": 1}, headers={"If-None-Match": tags[2]})
    assert wrong.status_code == 200 and wrong.headers["etag"] == tags[1]

    # a new name: a new manifest, the same points — the window keeps the parts it has
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == cloud_id).values(name="Борт", version=2))
    r2, head2, _a = _get(c, str(cloud_id))
    assert r2.headers["etag"] != tag and head2["version"] == 2 and head2["layer"]["name"] == "Борт"
    assert head2["parts"][1]["etags"] == tags
    assert c.get(f"{API}/viz/lines/{cloud_id}/parts/1", headers={"If-None-Match": tags[0]}).status_code == 304

    # new classes: the parts of the mode «class» are new, the heights are a mode of their own
    _classify(project, cloud_id, **{"class": np.full(N, 2, dtype=np.uint8), "h": np.zeros(N, dtype=np.float32)})
    _r, head3, _a = _get(c, str(cloud_id))
    assert not set(head3["parts"][1]["etags"]) & set(tags)
    r_h = c.get(f"{API}/viz/lines/{cloud_id}/parts/1", params={"color": "h"})
    r_c = c.get(f"{API}/viz/lines/{cloud_id}/parts/1", params={"color": "class"})
    assert r_c.headers["etag"] == head3["parts"][1]["etags"][0] != r_h.headers["etag"]
    # a difference to a DTM appears: neither the classes nor the heights are loaded again
    _classify(project, cloud_id, dz=np.zeros(N, dtype=np.float32))
    for mode, before in (("h", r_h), ("class", r_c)):
        again = c.get(
            f"{API}/viz/lines/{cloud_id}/parts/1",
            params={"color": mode},
            headers={"If-None-Match": before.headers["etag"]},
        )
        assert again.status_code == 304
    assert c.get(f"{API}/viz/lines/{cloud_id}/parts/1", params={"color": "dz"}).status_code == 200
    # the tags of a cloud are not the tags of a layer of points
    other = _get(c, str(points_id))[1]["parts"][0]["etags"]
    assert len(other) == 3 and not set(other) & set(head3["parts"][0]["etags"])


def test_requests_out_of_the_cloud_are_refused_and_plain_points_are_served_as_before(shown) -> None:  # type: ignore[no-untyped-def]
    c, _project, data, cloud_id, points_id = shown
    for params, status, code in (
        ({"lod": 5}, 422, "bad_lod"),
        ({"lod": -1}, 422, "bad_lod"),
        ({"color": "rgb"}, 422, "bad_color_mode"),
        ({"color": "x" * 17}, 422, None),
    ):
        r = c.get(f"{API}/viz/lines/{cloud_id}/parts/0", params=params)
        assert r.status_code == status and (code is None or r.json()["code"] == code) and r.json()["hint"]
    for k in (-1, len(data.parts)):
        r = c.get(f"{API}/viz/lines/{cloud_id}/parts/{k}")
        assert r.status_code == 404 and r.json()["code"] == "part_not_found"
    assert c.get(f"{API}/viz/lines/{cloud_id + 1000}").status_code == 404
    # a layer of points that is not a cloud: three levels, no `value`, `color` means nothing to it
    _r, head, _a = _get(c, str(points_id))
    assert "cloud" not in head and len(head["lods"]) == 3 and head["points"] == 3000
    _r, h, a = _get(c, f"{points_id}/parts/0", lod=2, color="h")
    assert set(a) == {"xyz", "line"} and "cloud" not in h and h["simplified"]
    r = c.get(f"{API}/viz/lines/{points_id}/parts/0", params={"lod": 3})
    assert r.status_code == 422 and r.json()["code"] == "bad_lod"


def test_a_damaged_cloud_is_an_error_with_a_hint_not_a_wrong_picture(shown) -> None:  # type: ignore[no-untyped-def]
    c, project, _data, cloud_id, _points = shown
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        cloud = lc.open_cloud(conn, store, cloud_id)
    path = cloud.paths["xyz"]
    whole = path.read_bytes()
    path.write_bytes(whole[: len(whole) // 2])
    try:
        last = len(cloud.parts) - 1
        r = c.get(f"{API}/viz/lines/{cloud_id}/parts/{last}")
        assert r.status_code == 500 and r.json()["code"] == "cloud_broken" and r.json()["hint"]
    finally:
        path.write_bytes(whole)
    with dialect.write_tx(project.engine) as conn:
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == cloud_id)).scalar_one()
        broken = {**meta, "cloud": {**meta["cloud"], "parts": meta["cloud"]["parts"][:-1]}}
        conn.execute(sa.update(objects).where(objects.c.id == cloud_id).values(meta=broken))
    r = c.get(f"{API}/viz/lines/{cloud_id}")
    assert r.status_code == 500 and r.json()["code"] == "cloud_broken"
