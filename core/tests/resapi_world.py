"""The fixture and the helpers of the tests of the API of the resource reports (подшаг 20.7): a core with an open
project, the model of Т8.10-1 (8 blocks 10×10×10 m, ρ = 2,5, Au 0,2…5,0 g/t, the class of each block) and a model with
many groups, the calls of the routes with the job followed to its end, the events that were published."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.estapi_util import finish, ok, read_only, refused  # noqa: F401 - re-exported for the tests
from tests.test_blocks_report import put
from tests.test_import_job import API
from tests.test_resources_result import CLASS, GRID8, model8

RS = f"{API}/resources"
# the blocks 3…7 have Au ≥ 1: 12 500 t, 2,5 g/t, 31,25 kg (Т8.10-1); the classes — 1 block, 2 blocks, 2 blocks of them
CLS = np.array([0, 0, 1, 0, 1, 1, 2, 2], dtype=np.int32)
BODY = {"elements": ["AU"], "cutoffs": [1.0], "density": 2.5, "groups": ["CLASS"], "curve": 0}


@pytest.fixture()
def ra(pclient):  # type: ignore[no-untyped-def]
    """A core with an open project; `.project`, `.store`, `.events` — what was published."""
    assert pclient.post(f"{API}/projects", json={"name": "Ресурсы"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    pclient.events = []
    publish = pclient.core.events.publish

    def spy(name: str, data: Any = None, *a: Any, **kw: Any) -> Any:
        pclient.events.append((name, data))
        return publish(name, data, *a, **kw)

    pclient.core.events.publish = spy
    return pclient


def model(c: Any) -> int:
    """The model of Т8.10-1 with the classes `CLS`."""
    return model8(c, GRID8, CLS)


def many_groups(c: Any, n: int = 300) -> int:
    """`n` blocks in a row, each in a domain of its own: with 100 cut-offs the report has 100 × (n + 1) rows."""
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [n, 1, 1], "size": [10.0, 10.0, 10.0]}
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"},
              {"name": "DOM", "dtype": "cat", "labels": [f"Д{i:03d}" for i in range(n)]}]  # fmt: skip
    au = np.linspace(0.1, 5.0, n)
    return put(c, grid, fields, np.arange(n, dtype=np.uint64), {"AU": au, "DOM": np.arange(n, dtype=np.int32)},
               name="Много групп")  # fmt: skip


def post(c: Any, params: dict[str, Any], save: bool = False, name: str | None = None) -> Any:
    body: dict[str, Any] = {"params": params, "save": save}
    if name is not None:
        body["name"] = name
    return c.post(f"{RS}/reports", json=body)


def run(c: Any, params: dict[str, Any], save: bool = False, name: str | None = None) -> dict[str, Any]:
    """`POST /resources/reports`, the job followed to its end; the result of the job."""
    r = post(c, params, save, name)
    assert r.status_code == 200, r.text
    job = finish(c, r.json(), timeout=120)
    assert job["status"] == "done", job
    return job["result"]  # type: ignore[no-any-return]


def get(c: Any, report_id: int, **query: Any) -> dict[str, Any]:
    r = c.get(f"{RS}/reports/{report_id}", params=query)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def row(res: dict[str, Any], kind: str = "cutoff", cutoff: float | None = 1.0, g: Any = "total") -> dict[str, Any]:
    got = [r for r in res["rows"] if r["kind"] == kind and r["cutoff"] == cutoff
           and ((g == "total" and r["total"]) or r["g"] == g)]  # fmt: skip
    assert len(got) == 1, (kind, cutoff, g, len(got))
    return got[0]  # type: ignore[no-any-return]


__all__ = ["BODY", "CLASS", "CLS", "RS", "finish", "get", "many_groups", "model", "ok", "post", "ra", "read_only",
           "refused", "row", "run"]  # fmt: skip
