"""A world for the tests of the simulations (подшаг 18.32): the set of 49 holes, the plain model of 14 × 14 × 10
blocks and the one with sub-blocks and a turn of 20° of `estjob_util`, plus a variogram model in normal scores (Д-38).
The body of a simulation is small (4 realizations, 8 nodes) so that a run takes a second."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.geostat.simulation import job, post, real
from bf_core.geostat.simulation import store as st
from bf_core.geostat.simulation.spec import parse_simulation
from bf_core.storage.blobs import BlobStore
from tests import estjob_util as u
from tests.estjob_util import JW, MODEL
from tests.estutil import vmodel

__all__ = ["SW", "body", "make", "world"]


@dataclass
class SW(JW):
    vy: int = 0  # the variogram model in normal scores


def world(tmp_path: Path, url: str = "sqlite") -> SW:
    w = u.world(tmp_path, url)
    sw = SW(**w.__dict__)
    sw.vy = vmodel(sw, "AU · Y", transform="nscore", model=MODEL)  # type: ignore[arg-type]  # attributes only
    return sw


def body(sw: SW, *, model: int | None = None, label: str = "руда", zone: str = "окисл", **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model_id": sw.model if model is None else model,
        "target": {"domain_field": "DOM", "label": label},
        "samples": {"set_id": sw.set_id, "table": str(sw.a), "field": "AU",
                    "domain": {"table": str(sw.b), "field": "ZONE", "values": [zone]}},
        "variogram": {"model_id": sw.vy}, "n_real": 4, "nodes": 8, "seed": 777}  # fmt: skip
    out.update(over)
    return out


def make(sw: SW, **over: Any) -> int:
    spec = parse_simulation(body(sw, **over))
    with dialect.write_tx(sw.project.engine) as conn:
        return int(st.create(conn, sw.project, spec)["id"])


def get(sw: SW, ident: int) -> dict[str, Any]:
    with dialect.read_tx(sw.project.engine) as conn:
        return st.get(conn, ident)


def put(sw: SW, ident: int, version: int, **raw: Any) -> dict[str, Any]:
    with dialect.write_tx(sw.project.engine) as conn:
        return st.update(conn, sw.project, ident, raw, version)


def run(sw: SW, threads: int = 2, **params: Any) -> dict[str, Any]:
    return job.run_simulate(sw.project.engine, sw.project.folder, params, threads=threads)


def run_post(sw: SW, ident: int, model: int | None = None, **over: Any) -> dict[str, Any]:
    params = {"simulation_id": ident, **over}
    if "expected_version" not in over:
        params["expected_version"] = u.version_of(sw, model)
    return post.run_post(sw.project.engine, sw.project.folder, params)


def sha_of(sw: SW, ident: int) -> str | None:
    with dialect.read_tx(sw.project.engine) as conn:
        return st.real_sha(conn, ident)


def reals(sw: SW, ident: int) -> real.Reals:
    sha = sha_of(sw, ident)
    assert sha is not None
    return real.Reals(BlobStore(sw.project.folder), sha)


def table(sw: SW, ident: int) -> np.ndarray:
    """f4 [n_real, n] as the file has them."""
    r = reals(sw, ident)
    return r.rows(0, r.n).astype(np.float32)


def model_of(sw: SW, model: int | None = None) -> Model:
    return u.open_model(sw, model)


def refused(fn: Any, status: int, code: str, param: str | None = None) -> ApiError:
    import pytest

    with pytest.raises(ApiError) as e:
        fn()
    err = e.value
    assert (err.status, err.code) == (status, code), (err.code, err.message)
    assert err.message and err.hint and any("а" <= ch.lower() <= "я" for ch in err.message)
    if param is not None:
        assert err.extra.get("param") == param
    return err


def left_over(sw: SW) -> list[str]:
    tmp = BlobStore(sw.project.folder).tmp_dir
    return sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else []
