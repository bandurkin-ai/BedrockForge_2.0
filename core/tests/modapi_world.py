"""The synthetic project of the tests of the routes of the implicit models (подшаг 19.16): the fixture scene of
`tools/gen/implicit.py` (100 holes through folded layers, the sphere INT, the vein VN; `tests/fixtures/implicit`) in the
project the core has open — a set, the tables of codes and of assays, the layers of strings — and the fixtures
`im` (a core with the world, the routers of 19.16 in the app until the line of the orchestrator) and `world`.
Deterministic: the same numbers on every run (the file `core/tests/data/implicit/contract_answers.json`)."""

from __future__ import annotations

from typing import Any

import pytest

from bf_core.db import dialect
from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API
from tests.test_model_data import read_csv
from tests.test_model_data_load import World, noop

IMP = f"{API}/implicit"
BOX = [0.0, 0.0, 120.0, 420.0, 420.0, 330.0]
LAYERS = {"INT": 1, "ALV": 2, "SS": 3, "SH": 4, "GR": 5}


class ProjectWorld(World):
    """`World` on the open project of the API (its engine, its folder) instead of an engine of its own."""

    def __init__(self, project: Any) -> None:  # noqa: PLR0913 - the same steps as `World.__init__`
        self.engine = project.engine
        self.project = project
        self.store = BlobStore(project.folder)
        with dialect.write_tx(self.engine) as conn:
            self.set_id = dh.create_set(conn, "Скважины")
            dh.replace_collars(conn, self.set_id, [
                dh.CollarRow(r["HOLE"], float(r["X"]), float(r["Y"]), float(r["Z"]), float(r["TD"]),
                             float(r["AZIMUTH"]), float(r["DIP"])) for r in read_csv("collars.csv")])  # fmt: skip
        self.litho = self._table("Литология", "interval", read_csv("lithology.csv"), "CODE", "text")
        self.assays = self._table("Пробы", "assay", read_csv("assays.csv"), "AU", "number")
        self.layers = self._strings()


def domain(name: str, *, gid: int | None = None, codes: tuple[str, ...] | None = None) -> dict[str, Any]:
    gid = LAYERS[name] if gid is None else gid
    return {"id": gid, "name": name, "type": "domain", "codes": list(codes or (name,))}


def body(w: ProjectWorld, *names: str, **over: Any) -> dict[str, Any]:
    """The body of a model of the scene: the domains `names` (the youngest first), a coarse step for the speed."""
    out: dict[str, Any] = {
        "name": "Литология", "boundary": {"box": BOX}, "h": 8.0,
        "data": {"set_id": w.set_id, "table": str(w.litho), "field": "CODE", "d_max": 30, "step": 5},
        "groups": [domain(n) for n in (names or ("INT", "SS"))]}  # fmt: skip
    out.update(over)
    return out


@pytest.fixture()
def reg():  # type: ignore[no-untyped-def]
    """The router of 19.16 in the app while the line of the orchestrator (`api/modules.py`) is not there (before the app
    is made): the same router the line would add."""
    import os
    import tempfile
    from pathlib import Path

    import bf_core.api.app as app

    saved = app.ROUTER_MODULES
    if "model" not in saved:
        app.ROUTER_MODULES = (*saved, "model")
    old = os.environ.get("NUMBA_CACHE_DIR")  # the worker of a job compiles the kernels once for the whole run
    os.environ["NUMBA_CACHE_DIR"] = str(Path(tempfile.gettempdir()) / "bf-test-numba-implicit")
    yield
    if old is None:
        os.environ.pop("NUMBA_CACHE_DIR", None)
    else:
        os.environ["NUMBA_CACHE_DIR"] = old
    app.ROUTER_MODULES = saved


@pytest.fixture()
def im(reg, pclient):  # noqa: ARG001  # type: ignore[no-untyped-def]
    """A core with an open project and the world; `.w` — the world, `.project`, `.events` — what was published."""
    assert pclient.post(f"{API}/projects", json={"name": "Неявные модели"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.w = ProjectWorld(pclient.project)
    pclient.events = []
    publish = pclient.core.events.publish

    def spy(name: str, data: Any = None, *a: Any, **kw: Any) -> Any:
        pclient.events.append((name, data))
        return publish(name, data, *a, **kw)

    pclient.core.events.publish = spy
    return pclient


__all__ = ["BOX", "IMP", "LAYERS", "ProjectWorld", "body", "domain", "im", "noop", "reg"]
