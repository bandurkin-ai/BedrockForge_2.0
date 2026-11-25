"""A project of the estimation for the scenarios that run the job (`estimate_job`, `estimate_compile`; ADR 0024).

    build(work, params)         in `prepare`: the project folder with the set of the deposit of `estimate_common`
                                (collars, «Пробы» with AU, the holes vertical), the variogram model (the job copies it
                                into the row), the regular model of `Deposit.rows` blocks with the category DOM (руда —
                                90 % of the columns of X, порода — the rest), and two rows of the plan: OK of a block
                                4×4×2 by the domain «руда» and IK of two thresholds, the search `manual` of the deposit
    ctx, meta = open_world(work)  in `run`: the project and the ids (`meta`: set_id, table, model_id, vz, estimation)
    spec, s, rk = look(ctx, meta)  the row as stored, the samples of it (`samples.load`) and the row compiled

The data are what `estimate_common.Deposit` makes: the same holes, intervals of unequal length, 3 % unassayed, an
outlier."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import estimate_common as ec

DOM_LABELS = ["руда", "порода"]
ORE_SHARE = 0.9  # of the columns of X are ore
PREFIX = "AU_OK_"  # the fields the job writes: AU_OK_STATUS, AU_OK_EST, …
DISC = [4, 4, 2]
IK_QUANTILES = (0.3, 0.7)  # the thresholds of the second row (IK) are these quantiles of the samples


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def ore_columns(dep: ec.Deposit) -> int:
    return int(round(ORE_SHARE * dep.counts[0]))


def write_set(engine: Any, dep: ec.Deposit) -> tuple[int, int, dict[str, Any]]:
    """The set: collars and «Пробы» (AU, г/т) → (set id, table id, facts of the holes)."""
    from bf_core.db import dialect
    from bf_core.drillholes import store as dh

    h = dep.holes()
    names = np.array([f"СКВ-{i + 1:05d}" for i in range(h["hx"].size)], dtype=object)
    ends = np.cumsum(h["counts"]) - 1
    collars = [dh.CollarRow(str(n), x=float(x), y=float(y), z=float(z), td=float(td), azimuth=0.0, dip=-90.0)
               for n, x, y, z, td in zip(names, h["hx"], h["hy"], h["top"], h["to"][ends], strict=True)]  # fmt: skip
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, collars)
    data = dh.TableData(name="Пробы", kind="assay", holes=names[h["hole"]].tolist(), from_m=h["from"], to_m=h["to"],
                        columns=[dh.ColumnSpec("AU", "number", "г/т", "component")],
                        values=[h["z"].tolist()])  # fmt: skip
    table = int(dh.write_table(engine, set_id, data, job_id=None, tick=_noop)["table_id"])
    facts = {"holes": int(names.size), "rows": int(h["z"].size), "assayed": int((~np.isnan(h["z"])).sum())}
    return set_id, table, facts


def write_model(ctx: bc.Ctx, dep: ec.Deposit) -> int:
    """The regular model of the deposit: the category DOM (the first `ore_columns` columns of X are «руда»)."""
    nx, ny, _ = dep.counts
    ore = ore_columns(dep)
    at = {"lo": 0}

    def make(_rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
        p = np.arange(at["lo"], at["lo"] + n, dtype=np.int64)
        at["lo"] += n
        return {"DOM": np.where(p % nx < ore, 0, 1).astype(np.int32)}

    fields = [{"name": "DOM", "dtype": "cat", "labels": DOM_LABELS, "role": "code"}]
    got = bc.regular_model(ctx, dep.rows, fields, make, name="Модель оценки", nz=dep.counts[2])
    assert got["counts"] == list(dep.counts), (got["counts"], dep.counts)
    return int(got["model_id"])


def body(dep: ec.Deposit, meta: dict[str, Any]) -> dict[str, Any]:
    """The row of the plan: OK of a block, the domain «руда» of DOM, the samples AU of the table."""
    return {"model_id": meta["model_id"], "target": {"domain_field": "DOM", "label": DOM_LABELS[0]},
            "samples": {"set_id": meta["set_id"], "table": str(meta["table"]), "field": "AU"},
            "variogram": {"model_id": meta["vz"]}, "disc": DISC, "method": {"kind": "ok"},
            "search": ec.search(dep)}  # fmt: skip


def build(work: Path, params: dict[str, Any]) -> dict[str, Any]:
    from bf_core.db import dialect
    from bf_core.geostat.estimate import spec as es
    from bf_core.geostat.estimate import store as st
    from tests.estutil import vmodel

    dep = ec.Deposit.of(params)
    ctx = bc.Ctx.create(work)
    try:
        set_id, table, holes = write_set(ctx.engine, dep)
        model_id = write_model(ctx, dep)
        w = SimpleNamespace(project=ctx.project, store=ctx.store, set_id=set_id, a=table)
        vz = vmodel(w, "Золото Z", model={"type": "semivariogram", **ec.TWO})
        meta: dict[str, Any] = {"set_id": set_id, "table": table, "model_id": model_id, "vz": vz, "holes": holes,
                                "rows": dep.rows}  # fmt: skip
        rows: dict[str, dict[str, Any]] = {"estimation": {"name": "Оценка OK"},
                "estimation_ik": {"name": "Индикаторы", "method": {"kind": "ik", "ik": {
                    "thresholds": [float(x) for x in np.quantile(dep.arrays()["z"], IK_QUANTILES)],
                    "models": "median"}}}}  # fmt: skip
        for key, over in rows.items():
            spec = es.parse_estimation({**body(dep, meta), **over})
            with dialect.write_tx(ctx.engine) as conn:
                meta[key] = int(st.create(conn, ctx.store, ctx.project, spec)["id"])
        ctx.keep(**meta)
        return meta
    finally:
        ctx.close()


def open_world(work: Path) -> tuple[bc.Ctx, dict[str, Any]]:
    ctx = bc.Ctx.open(work)
    return ctx, ctx.meta()


def look(ctx: bc.Ctx, meta: dict[str, Any]) -> tuple[Any, Any, Any]:
    """The row as stored → (spec, samples as the job loads them, the row compiled with the grid of the model)."""
    from bf_core.db import dialect
    from bf_core.geostat.estimate import kernel as kn
    from bf_core.geostat.estimate import samples as sm
    from bf_core.geostat.estimate import store_base as sb

    with dialect.read_tx(ctx.engine) as conn:
        spec = sb.load_spec(sb.stored(conn, sb.KIND, meta["estimation"]).meta or {})
    model = ctx.model(meta["model_id"])
    s = sm.load(ctx.project, spec)
    rk = kn.compile_row(spec, s, block_size=model.grid.regular(), rotation_deg=model.grid.rotation)
    return spec, s, rk
