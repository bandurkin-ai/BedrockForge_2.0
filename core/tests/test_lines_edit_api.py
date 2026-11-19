"""/api/v1/lines — the string editor (ТЗ 7.22; ADR 0015 §2): new layer, exact geometry BFA1, save on a base version,
journal, return, move, cloud → 422, read-only → 423, `objects.changed`, the journal through a `.bfggis`."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.formats.lineset import from_lines
from bf_core.lines import geometry_io
from bf_core.lines import store as ls_store
from bf_core.lines.assemble import Edit
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

BASE = np.array([512345.678901234, 6789012.345678901, 250.123456789])
SQUARE = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], dtype=float) + BASE
OCTET = {"content-type": "application/octet-stream"}


def body(lines: list[tuple[int, bool, Any]], new_attrs: dict[int, dict[str, Any]] | None = None) -> Edit:
    src, touched, closed, count, parts = [], [], [], [], []
    for s, c, v in lines:
        src.append(s)
        closed.append(int(c))
        if isinstance(v, int):
            touched.append(0)
            count.append(v)
            continue
        p = np.vstack([v, v[:1]]) if c else np.asarray(v)
        touched.append(1)
        count.append(len(p))
        parts.append(p)
    xyz = np.vstack(parts) if parts else np.zeros((0, 3))
    return Edit(np.array(src), np.array(touched), np.array(closed), np.array(count), xyz, new_attrs or {})


def error_of(r: Any, status: int, code: str) -> dict[str, Any]:
    out = r.json()
    assert r.status_code == status and out["code"] == code, r.text
    assert out["message"] and out["hint"]  # Russian text with a hint, every time
    return out


def events(c: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(name, json.loads(data)) for _id, name, data in c.core.events._recent]


@pytest.fixture()
def ed(pclient):  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Правка"})
    return pclient


def new_layer(c: Any, name: str = "РУДА", **kw: Any) -> dict[str, Any]:
    r = c.post(f"{API}/lines/layers", json={"name": name, "color": "#ff8800", **kw})
    assert r.status_code == 201, r.text
    return dict(r.json())


def geometry(c: Any, layer_id: int, lines: str | None = None) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    r = c.get(f"{API}/lines/layers/{layer_id}/geometry", params={"lines": lines} if lines else None)
    assert r.status_code == 200, r.text
    return binary.decode(r.content)


def save(c: Any, layer_id: int, base: int, edit: Edit, note: str = "") -> Any:
    data = geometry_io.encode_save(base, edit, note)
    return c.put(f"{API}/lines/layers/{layer_id}/geometry", content=data, headers=OCTET)


def test_new_layer_save_read_bitwise_etag_and_events(ed) -> None:  # type: ignore[no-untyped-def]
    layer = new_layer(ed)
    assert (layer["line_count"], layer["point_count"], layer["rev"], layer["color"]) == (0, 0, 1, "#ff8800")
    assert ("objects.changed", {"kind": "line_file", "id": layer["file_id"]}) in events(ed)
    head, arrays = geometry(ed, layer["id"])
    assert head["kind"] == "linegeom" and (head["lines"], head["points"], head["version"]) == (0, 0, 1)

    trace = np.array([[0, 0, math.nan], [5, 5, -0.0], [9, 1, 3.25]]) + [BASE[0], BASE[1], 0.0]
    tie = np.array([[0, 0, 0], [0, 0, 10]], dtype=float) + BASE
    r = save(ed, layer["id"], 1, body([(-1, True, SQUARE), (-1, False, trace), (-1, False, tie)], {2: {"ROLE": "TIE"}}))
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["version"] == 2 and saved["layer"]["rev"] == 2 and saved["layer"]["line_count"] == 3
    assert ("objects.changed", {"kind": "line_layer", "id": layer["id"]}) in events(ed)

    first = ed.get(f"{API}/lines/layers/{layer['id']}/geometry")
    head, arrays = binary.decode(first.content)
    want = np.vstack([SQUARE, SQUARE[:1], trace, tie])
    assert arrays["xyz"].tobytes() == want.reshape(-1).tobytes()  # float64 bit for bit: NaN, −0.0, UTM
    assert arrays["offsets"].tolist() == [0, 5, 8, 10] and arrays["closed"].tolist() == [1, 0, 0]
    assert arrays["lid"].tolist() == [0, 1, 2] and head["ties"] == [2] and head["version"] == 2
    again = ed.get(f"{API}/lines/layers/{layer['id']}/geometry", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304
    head, arrays = geometry(ed, layer["id"], "2,0")  # the lines asked for, in that order
    assert arrays["lid"].tolist() == [2, 0] and arrays["offsets"].tolist() == [0, 2, 7] and head["ties"] == [0]
    error_of(ed.get(f"{API}/lines/layers/{layer['id']}/geometry", params={"lines": "7"}), 404, "line_not_found")


def test_stale_base_restore_and_journal(ed) -> None:  # type: ignore[no-untyped-def]
    """Приёмка 7.22 по ядру: устаревшая база отклоняется; возврат к 1 — версия N+1 с геометрией версии 1."""
    layer = new_layer(ed)
    lid = layer["id"]
    assert save(ed, lid, 1, body([(-1, True, SQUARE)]), "квадрат").status_code == 200
    assert save(ed, lid, 2, body([(0, True, SQUARE + [0, 0, 5]), (-1, False, SQUARE[:2])])).status_code == 200
    stale = error_of(save(ed, lid, 2, body([(0, True, 5)])), 409, "layer_changed")
    assert stale["version"] == 3 and "Ничего не записано" in stale["message"]
    journal = ed.get(f"{API}/lines/layers/{lid}/versions").json()
    rows = [(v["version"], v["action"], v["line_count"], v["prev_line_count"]) for v in journal["versions"]]
    assert journal["version"] == 3 and rows == [(3, "save", 2, 1), (2, "save", 1, 0), (1, "create", 0, 0)]
    assert (
        journal["versions"][1]["stats"]["plan_area"] == pytest.approx(100.0)
        and journal["versions"][1]["note"] == "квадрат"
    )

    r = ed.post(f"{API}/lines/layers/{lid}/versions/2/restore", json={"expected_version": 3})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 4 and r.json()["layer"]["line_count"] == 1
    _h, arrays = geometry(ed, lid)
    assert arrays["xyz"].tobytes() == np.vstack([SQUARE, SQUARE[:1]]).reshape(-1).tobytes()
    error_of(
        ed.post(f"{API}/lines/layers/{lid}/versions/4/restore", json={"expected_version": 4}), 422, "version_current"
    )
    error_of(
        ed.post(f"{API}/lines/layers/{lid}/versions/9/restore", json={"expected_version": 4}), 404, "version_not_found"
    )
    error_of(
        ed.post(f"{API}/lines/layers/{lid}/versions/1/restore", json={"expected_version": 3}), 409, "layer_changed"
    )


def test_bad_bodies_are_refused(ed) -> None:  # type: ignore[no-untyped-def]
    layer = new_layer(ed)
    url = f"{API}/lines/layers/{layer['id']}/geometry"
    error_of(ed.put(url, content=b"not a bfa", headers=OCTET), 422, "bad_body")
    no_src = binary.encode({"base_version": 1}, [("xyz", np.zeros(3), "<f8")])
    error_of(ed.put(url, content=no_src, headers=OCTET), 422, "bad_body")
    wrong = Edit(np.array([-1]), np.array([1]), np.array([1]), np.array([2]), np.zeros((2, 3)))
    error_of(save(ed, layer["id"], 1, wrong), 422, "bad_geometry")  # closed with two vertices
    assert ed.get(f"{API}/lines/layers/{layer['id']}").json()["rev"] == 1


def test_move_lines_bfa_and_json(ed) -> None:  # type: ignore[no-untyped-def]
    a = new_layer(ed, "A")
    b = new_layer(ed, "B", file_id=a["file_id"])
    l0, l1, l2 = (SQUARE[:2] + [0, k, 0] for k in (0, 20, 40))
    attrs = {0: {"N": 0}, 1: {"N": 1}, 2: {"N": 2}}
    assert save(ed, a["id"], 1, body([(-1, False, l0), (-1, False, l1), (-1, False, l2)], attrs)).status_code == 200
    moved_xyz = l1 + [0, 0, 7]  # moved with an unsaved change
    data = geometry_io.encode_move(2, b["id"], 1, body([(0, False, 2), (2, False, 2)]), body([(1, False, moved_xyz)]))
    r = ed.post(f"{API}/lines/layers/{a['id']}/move-lines", content=data, headers=OCTET)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["source"]["version"], out["target"]["version"]) == (3, 2)
    _h, arrays = geometry(ed, b["id"])
    assert arrays["xyz"].tobytes() == moved_xyz.reshape(-1).tobytes()
    page = ed.get(f"{API}/lines/layers/{b['id']}/attrs").json()
    assert page["rows"] == [[1.0]]
    r = ed.post(
        f"{API}/lines/layers/{b['id']}/move-lines",
        json={"lines": [0], "target_id": a["id"], "base_version": 2, "target_base": 3},
    )
    assert r.status_code == 200, r.text
    _h, arrays = geometry(ed, a["id"])
    assert arrays["lid"].tolist() == [0, 2, 3] and arrays["xyz"][-6:].tobytes() == moved_xyz.reshape(-1).tobytes()
    assert ed.get(f"{API}/lines/layers/{b['id']}").json()["line_count"] == 0
    stale = {"lines": [0], "target_id": b["id"], "base_version": 3, "target_base": 3}
    error_of(ed.post(f"{API}/lines/layers/{a['id']}/move-lines", json=stale), 409, "layer_changed")


def test_cloud_is_422_and_read_only_is_423(ed, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    layer = new_layer(ed)
    project = projects_of(ed.core).require_current()
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == layer["id"]).values(meta={"cloud": {"parts": []}}))
    error_of(ed.get(f"{API}/lines/layers/{layer['id']}/geometry"), 422, "not_editable")
    error_of(save(ed, layer["id"], 1, body([(-1, False, SQUARE)])), 422, "not_editable")
    monkeypatch.setattr(project, "read_only", True)
    error_of(save(ed, layer["id"], 1, body([(-1, False, SQUARE)])), 423, "project_read_only")
    error_of(ed.post(f"{API}/lines/layers", json={"name": "X"}), 423, "project_read_only")
    error_of(
        ed.post(f"{API}/lines/layers/{layer['id']}/versions/1/restore", json={"expected_version": 1}),
        423,
        "project_read_only",
    )


def test_journal_survives_bfggis_and_restore_works(ed, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """10.5 п. 5: after a `.bfggis` the journal of 3 versions is whole and a return works."""
    project = projects_of(ed.core).require_current()
    store = BlobStore(project.folder)
    ring = np.vstack([SQUARE, SQUARE[:1] + [4e-7, 0, 0]])  # the closing vertex «as in the file»
    with dialect.write_tx(project.engine) as conn:
        file_id = ls_store.create_file(conn, store, name="pit.str", meta={"format": "micromine_str"})
        layer_id = ls_store.put_layer(
            conn, store, from_lines("Борт", [ring, SQUARE[:2]], attrs={"S": ["1", "2"]}), file_id=file_id
        )
    assert save(ed, layer_id, 1, body([(1, False, SQUARE[:2] + 1), (0, True, 5)])).status_code == 200
    assert save(ed, layer_id, 2, body([(0, True, 5), (-1, False, SQUARE[:3])])).status_code == 200
    r = ed.post(f"{API}/projects/export", json={"path": str(tmp_path / "e9.bfggis")})
    job = wait_job(ed, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    r = ed.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(ed, r["source"], r["id"])
    assert job["status"] == "done", job
    new_id = int(job["result"]["id_maps"]["objects"][str(layer_id)])
    ed.post(f"{API}/projects/{job['result']['uid']}/open")
    journal = ed.get(f"{API}/lines/layers/{new_id}/versions").json()["versions"]
    assert [(v["version"], v["action"]) for v in journal] == [(3, "save"), (2, "save"), (1, "import")]
    r = ed.post(f"{API}/lines/layers/{new_id}/versions/1/restore", json={"expected_version": 3})
    assert r.status_code == 200, r.text
    _h, arrays = geometry(ed, new_id)
    assert arrays["xyz"].tobytes() == np.vstack([ring, SQUARE[:2]]).reshape(-1).tobytes() and arrays[
        "lid"
    ].tolist() == [0, 1]
    assert ed.get(f"{API}/lines/layers/{new_id}/attrs").json()["rows"] == [["1"], ["2"]]
