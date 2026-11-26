"""Helpers of the tests of the stored pit scenarios (подшаг 21.10): a project with a block model (`world`), the tables
of a run made by hand (shells, benches, z-grids, template, control), the resolved parameters, a scenario with and
without a result, the counters of the rows."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core import lineage as lg
from bf_core.api.errors import ApiError
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, lineage, objects
from bf_core.mine import spec
from bf_core.mine import store as st
from bf_core.mine import store_data as sd
from bf_core.mine import store_graph as sg
from tests.estutil import W
from tests.test_blocks_fields_store import add_fields
from tests.test_lineage_resources import est_field

ELEMENTS = ["AU"]
GRID = {"corner": [1000.5, 2000.25, -300.0], "counts": [10, 8, 6], "size": [5.0, 5.0, 2.5], "rotation": 30}
BASE = {"elements": [{"field": "AU", "unit": "g/t", "price": 2000.0, "sales": 5.0, "royalty": 0.03}],
        "density": "2,7", "rocks": {"types": [{"name": "Все породы", "c_p": 20.0, "rec": {"AU": 0.9}}]},
        "mining": {"c_m0": 2.5}, "rf": [0.8, 1.0, 1.2]}  # fmt: skip


# --- the tables of a run (made by hand) -------------------------------------------------------------------------------


def _strip(ore: float, waste: float) -> float | None:
    return None if ore == 0 else waste / ore


def shells_of(n: int = 3, scale: float = 1.0) -> list[dict[str, Any]]:
    """`n` shells of the contract (§7); the first has no ore (strip and grade are null: a gap, never 0)."""
    out: list[dict[str, Any]] = []
    cum = dict.fromkeys(("blocks", "ore_t", "waste_t", "revenue", "cost_mine", "cost_process", "cost_sales", "royalty",
                         "cf"), 0.0)  # fmt: skip
    metal = rec = 0.0
    for k in range(1, n + 1):
        ore = 0.0 if k == 1 else 1.0e6 * k * scale
        waste, blocks = 2.0e6 * k * scale, 1000 * k
        grade = None if ore == 0 else 0.9 + 0.1 * k
        m = 0.0 if ore == 0 else ore * grade * 0.032150746  # type: ignore[operator]
        r = m * 0.9
        rev, cm, cp, cs, roy = r * 2000.0, (ore + waste) * 2.5, ore * 20.0, m * 5.0, 0.03 * (r * 2000.0 - m * 5.0)
        cf = rev - cs - roy - cm - cp
        row = dict(zip(sd.SHELL_PARTS, (blocks, ore, waste, rev, cm, cp, cs, roy, cf), strict=True))
        for key, v in row.items():
            cum[key] += v
        metal, rec = metal + m, rec + r
        cum_ore = cum["ore_t"]
        out.append({
            "k": k, "rf": [0.8, 1.0, 1.2, 1.4, 1.6][k - 1], **row, "strip": _strip(ore, waste),
            "elements": {"AU": {"grade": grade, "metal": m, "rec_metal": r}},
            "cum": {**cum, "blocks": int(cum["blocks"]), "strip": _strip(cum_ore, cum["waste_t"]),
                    "elements": {"AU": {"grade": metal / cum_ore / 0.032150746 if cum_ore else None, "metal": metal,
                                        "rec_metal": rec}}},
        })  # fmt: skip
    return out


def meta_shells(shells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"k": s["k"], "rf": s["rf"], "blocks": s["blocks"], "ore_t": s["cum"]["ore_t"],
             "waste_t": s["cum"]["waste_t"], "value": 1.0e7 * s["k"]} for s in shells]  # fmt: skip


BENCHES = {"n": [1, 1, 2, 2, 2], "level": [1, 2, 1, 2, 3], "ore_t": [0.0, 0.0, 5.0e5, 4.0e5, 1.0e5],
           "waste_t": [1.0e6, 1.0e6, 9.0e5, 8.0e5, 7.0e5], "cf": [-5.0e6, -4.0e6, 1.0e6, 2.0e6, -1.0e5]}
ZI, ZJ = [0, 1, 0, 1], [0, 0, 1, 1]
ZGRID = [[10.0, 12.0, math.nan, 9.5], [8.0, 10.0, math.nan, 7.5], [6.0, 8.0, 5.0, 5.5]]  # NaN: outside of the terrain
TEMPLATE = {"k": 8, "k_auto": True, "tolerance": 2.5, "levels": 8,
            "domains": [{"label": None, "table": [{"az": 0.0, "angle": 45.0}], "offsets": [[1, 0, 1], [0, 1, 1]],
                         "n_offsets": 2, "error": {"max": 1.44, "mean": 0.58}, "warning": False,
                         "phi_eff": {"az": [0, 1], "angle": [44.6, 44.7]}}], "warnings": []}  # fmt: skip
CONTROL = {"air": {"blocks": 20, "volume": 2.5e4}, "empty_cells": {"blocks": 0, "volume": 0.0},
           "no_grade": {"AU": {"blocks": 2, "tonnes": 100.0}}, "no_density": {"blocks": 0, "volume": 0.0},
           "default_density": None, "sub_blocks": 0, "closed": True, "nested": True}  # fmt: skip


def params_of(w: W, **over: Any) -> dict[str, Any]:
    """The resolved canonical form of the parameters on the model of the world."""
    body = {"model_id": w.model, **BASE, **over}
    with dialect.read_tx(w.project.engine) as conn:
        fields = Model.open(conn, w.store, w.model).fields
    return spec.parse_params(body).resolved(fields, (-300.0, -285.0)).canonical()


def built(w: W, shells: list[dict[str, Any]] | None = None, params: dict[str, Any] | None = None, on_write: Any = None,
          prints: Any = None, job_id: int | None = 3, wireframes: tuple[int, ...] = (),
          **over: Any) -> st.Built:  # fmt: skip
    rows = shells or shells_of()
    staged = st.stage(w.store, sd.shells_table(rows, ELEMENTS), sd.benches_table(BENCHES),
                      sd.zgrid_table(ZI, ZJ, ZGRID), TEMPLATE, CONTROL)  # fmt: skip
    p = params or params_of(w, **over)
    return st.Built(p, {"id": w.model, "name": "Модель", "grid": GRID}, sg.read_fields(p), staged, meta_shells(rows),
                    {"ore_t": 6.0e6, "waste_t": 1.2e7, "strip": 2.0, "cf": 1.0e8}, q=0.01, objective_error=12.5,
                    outputs={"fields": ["PIT", "ORE"], "wireframes": list(wireframes)}, solver="hpf",
                    seconds={"total": 1.5}, versions={"numpy": "2"}, job_id=job_id, on_write=on_write,
                    prints=prints)  # fmt: skip


def rd(w: W, fn: Any, *args: Any, **kw: Any) -> Any:
    with dialect.read_tx(w.project.engine) as conn:
        return fn(conn, *args, **kw)


def new(w: W, name: str | None = None, **params: Any) -> int:
    """A new scenario (no result) on the model of the world."""
    with dialect.write_tx(w.project.engine) as conn:
        return int(st.create(conn, name, {"model_id": w.model, **BASE, **params})["id"])


def make(w: W, name: str = "Сценарий", wireframes: tuple[int, ...] = (), **over: Any) -> int:
    """A scenario with a result (the run is not done here: the tables are the ones above)."""
    ident = new(w, name, **over)
    b = built(w, wireframes=wireframes, **over)
    with dialect.write_tx(w.project.engine) as conn:
        st.write_result(conn, w.store, ident, b)
    return ident


def refused(fn: Any, status: int, code: str) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn()
    assert (e.value.status, e.value.code) == (status, code), e.value
    assert e.value.message and e.value.hint  # Russian text with a hint
    return e.value


def roles(w: W, ident: int) -> set[str]:
    with dialect.read_tx(w.project.engine) as conn:
        return set(conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == ident,
                                                                    blob_owners.c.owner_kind == "object")).scalars())


def sha_of(w: W, ident: int) -> str | None:
    return rd(w, st.data_sha, ident)


def counts(w: W) -> tuple[int, int, int]:
    with dialect.read_tx(w.project.engine) as conn:
        return tuple(  # type: ignore[return-value]
            int(conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one())
            for t in (objects, blob_owners, lineage)
        )


def owned_sha(conn: sa.Connection, ident: int, role: str) -> str:
    q = sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_kind == "object", blob_owners.c.owner_id == ident,
                                              blob_owners.c.role == role)  # fmt: skip
    return str(conn.execute(q).scalar_one())


def hash_of(params: dict[str, Any]) -> str:
    return spec.parse_params(params).params_hash()


def shas(w: W, ident: int) -> dict[str, str]:
    """The SHA-256 of every file of a scenario by role."""
    return {r: rd(w, owned_sha, ident, r) for r in roles(w, ident)}


AU_EST = [{"field": "AU_EST", "unit": "g/t", "price": 2000.0, "sales": 5.0}]
ROCKS_EST = {"types": [{"name": "Все породы", "c_p": 20.0, "rec": {"AU_EST": 0.9}}]}


def of_estimate(w: W, name: str = "Сценарий ЮЗ", **over: Any) -> int:
    """A scenario that reads the field of an estimation (and has a result)."""
    est_field(w)
    return make(w, name, elements=AU_EST, rocks=ROCKS_EST, **over)


def pit_field(w: W, ident: int, name: str = "PIT") -> None:
    """The field PIT as the run writes it (`source {op: "pit", scenario_id}`), the scenario its input in the graph."""
    src = {"op": "pit", "params": {}, "inputs": [], "algo": "pit-1", "scenario_id": ident}
    add_fields(w.project, w.store, w.model, [{"name": name, "dtype": "i2"}], {name: np.ones(350, dtype=np.int16)},
               source=src)  # fmt: skip
    with dialect.write_tx(w.project.engine) as conn:
        lg.record(conn, lg.Node("bm_field", w.model, name.casefold()), [lg.Node(st.NODE, ident)])
