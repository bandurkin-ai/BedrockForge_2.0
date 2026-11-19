"""A project for the tests of the operations (`bf_core/ops/`): two crossing cubes, a cube without a face, a
relief, a layer of contours (two squares 10 × 10 ten metres apart and a tie line) and a layer of polygons —
all at a UTM position."""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.db import dialect
from bf_core.formats.lineset import from_lines
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as ws
from tests.solidutil import OFFSET, box, grid

API = "/api/v1"


def square(z: float, side: float = 10.0, at: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    x, y = at
    return np.array([[x, y, z], [x + side, y, z], [x + side, y + side, z], [x, y + side, z]]) + OFFSET


def make_project(c: Any, name: str = "Операции") -> tuple[Any, dict[str, int]]:
    r = c.post(f"{API}/projects", json={"name": name})
    assert r.status_code == 200, r.text
    project = projects_of(c.core).require_current()
    a, b = box((0, 0, 0), (10, 10, 10)), box((5, 5, 5), (15, 15, 15))
    hole = (a[0], a[1][:-2])  # a cube without its face x = 0: a square hole, 4 open edges
    relief = grid(10, 10, (-5, 25), (-5, 25), lambda x, y: 20.0 + 0.1 * x + 0.05 * y)
    tie = np.array([square(0.0)[0], square(10.0)[0]])
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        ids = {
            "a": ws.put_wireframe(conn, store, *a, name="Куб A"),
            "b": ws.put_wireframe(conn, store, *b, name="Куб B"),
            "open": ws.put_wireframe(conn, store, *hole, name="Тело 2"),
            "relief": ws.put_wireframe(conn, store, *relief, name="Рельеф"),
        }
        file_id = ls_store.create_file(conn, store, name="контуры.str", meta={"format": "micromine_str"})
        ids["file"] = file_id
        ids["contours"] = ls_store.put_layer(
            conn, store,
            from_lines("Контуры", [square(0.0), square(10.0), tie], closed=[True, True, False],
                       attrs={"ROLE": [None, None, "TIE"]}),
            file_id=file_id,
        )  # fmt: skip
        ids["polygons"] = ls_store.put_layer(
            conn, store, from_lines("Полигоны", [square(0.0, 4.0, (3.0, 3.0))[:, :]], closed=[True]), file_id=file_id
        )
    return project, ids


def ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def refused(r: Any, status: int, code: str) -> dict[str, Any]:
    body: dict[str, Any] = r.json()
    assert r.status_code == status and body["code"] == code, r.text
    assert body["message"] and body["hint"]
    return body
