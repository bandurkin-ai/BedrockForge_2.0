"""The world of the tests of the pit jobs and routes (подшаг 21.11): a core with an open project, a synthetic model — a
rock mass of cubes with a lognormal ore body of a few high-grade blocks (the pit is a real inverted cone) — the
parameters of a scenario that is ready to run, the routes and the jobs followed to their end, the rows of the graph."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.estapi_util import finish, ok, read_only, refused  # noqa: F401 - re-exported for the tests
from tests.test_blocks_report import put
from tests.test_import_job import API

PIT = f"{API}/pit"
COUNTS = (14, 12, 9)
SIZE = 10.0
CORNER = [500.0, 700.0, 0.0]
FIELDS = [
    {"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"},
    {"name": "SG", "dtype": "f8", "role": "density"},
    {"name": "ROCK", "dtype": "cat", "labels": ["Руда", "Порода"]},
]
ROCKS = {
    "by": {"kind": "field", "field": "ROCK"},
    "types": [{"name": "Руда", "c_p": 20.0, "rec": {"AU": 0.9}}, {"name": "Порода", "waste_only": True}],
}


def grid_of(counts: tuple[int, int, int] = COUNTS, rotation: float = 0.0) -> dict[str, Any]:
    out: dict[str, Any] = {"corner": list(CORNER), "counts": list(counts), "size": [SIZE, SIZE, SIZE]}
    if rotation:
        out["rotation"] = rotation
    return out


def blocks(counts: tuple[int, int, int] = COUNTS, seed: int = 11, gaps: float = 0.0) -> dict[str, np.ndarray]:
    """The rows of the model in the order of P (i fastest): an ellipsoidal body of high grades in the lower middle,
    low grades round it; `ROCK` is the ore inside the body and the rock outside."""
    nx, ny, nz = counts
    rng = np.random.default_rng(seed)
    k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    r = ((i - nx * 0.5) / (nx * 0.28)) ** 2 + ((j - ny * 0.5) / (ny * 0.28)) ** 2 + ((k - nz * 0.3) / (nz * 0.3)) ** 2
    au = (0.05 + 4.5 * np.exp(-1.6 * r) * np.exp(rng.normal(0.0, 0.35, k.shape))).ravel()
    if gaps:
        au[rng.random(au.shape) < gaps] = np.nan
    rock = (r > 1.2).astype(np.int32).ravel()  # 0 — «Руда», 1 — «Порода»
    sg = rng.uniform(2.5, 2.9, au.shape)
    p = np.arange(nx * ny * nz, dtype=np.uint64)
    return {"p": p, "AU": au, "SG": sg, "ROCK": rock}


def make_model(
    c: Any, counts: tuple[int, int, int] = COUNTS, seed: int = 11, gaps: float = 0.0, rotation: float = 0.0,
    name: str = "Рудное тело",
) -> int:  # fmt: skip
    b = blocks(counts, seed, gaps)
    return put(c, grid_of(counts, rotation), FIELDS, b.pop("p"), b, name=name)


def params(mid: int | None, **over: Any) -> dict[str, Any]:
    """The parameters of a scenario that is ready to run on the model `mid` (four shells, one slope table)."""
    body: dict[str, Any] = {
        "model_id": mid, "elements": [{"field": "AU", "unit": "g/t", "price": 2000.0, "sales": 5.0, "royalty": 0.02}],
        "density": "SG", "rocks": ROCKS, "mining": {"c_m0": 2.5, "c_gl": 0.05},
        "rf": {"from": 0.4, "to": 1.3, "step": 0.3}, "slopes": {"table": [{"az": 0.0, "angle": 45.0}]},
        "calendar": {"q_ore": 500000.0, "q_mass": None, "rate": 0.08},
    }  # fmt: skip
    body.update(over)
    return body


@pytest.fixture()
def pw(pclient):  # type: ignore[no-untyped-def]
    """A core with an open project; `.project`, `.store`, `.events` — what was published."""
    assert pclient.post(f"{API}/projects", json={"name": "Карьер"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    pclient.events = []
    publish = pclient.core.events.publish

    def spy(name: str, data: Any = None, *a: Any, **kw: Any) -> Any:
        pclient.events.append((name, data))
        return publish(name, data, *a, **kw)

    pclient.core.events.publish = spy
    return pclient


def new(c: Any, mid: int | None = None, name: str | None = None, **over: Any) -> dict[str, Any]:
    """`POST /pit/scenarios` with the parameters of `params`."""
    body: dict[str, Any] = {"params": params(mid, **over)}
    if name is not None:
        body["name"] = name
    return ok(c.post(f"{PIT}/scenarios", json=body))  # type: ignore[no-any-return]


def run(c: Any, scenario_id: int, **body: Any) -> dict[str, Any]:
    """`POST …/run`, the job followed to its end; the result of the job."""
    r = c.post(f"{PIT}/scenarios/{scenario_id}/run", json=body)
    assert r.status_code == 200, r.text
    job = finish(c, r.json(), timeout=300)
    assert job["status"] == "done", job
    return job["result"]  # type: ignore[no-any-return]


def get(c: Any, scenario_id: int) -> dict[str, Any]:
    return ok(c.get(f"{PIT}/scenarios/{scenario_id}"))  # type: ignore[no-any-return]


__all__ = ["COUNTS", "FIELDS", "PIT", "ROCKS", "blocks", "finish", "get", "grid_of", "make_model", "new", "ok",
           "params", "pw", "read_only", "refused", "run"]  # fmt: skip
