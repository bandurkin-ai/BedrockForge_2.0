"""SIS and co-SGS through the job `simulate.run` and `simulate.post` (подшаг 18.32б; ТЗ 8.8.1, Д-23; ADR 0024 §9;
docs/progress/e18-api.md §9): thresholds with the median and the per-threshold models, categories (the codes of a
numeric field aligned to the samples by `row_id`, the blob in u1), the post-processing of the categories (`PROB_<code>`,
`MODE`), the co-SGS (ρ = 0 is the SGS bit for bit, ρ computed, a gap of the secondary, the refusals of the field)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.geostat.estimate import samples as sm
from bf_core.geostat.simulation import secondary
from bf_core.geostat.simulation import store as st
from bf_core.geostat.simulation import store_check as sc
from bf_core.storage.blobs import BlobStore
from tests.estutil import vmodel
from tests.simutil import SW, get, make, model_of, put, reals, refused, run, run_post, sha_of, table, world
from tests.simutil_cat import CODES, LITH, SG, add_lith, add_secondary, lith_code

THRESHOLDS = [0.8, 1.1, 1.5]
LABELS = ["Окисленные", "Переходные", "Первичные"]


def ind_model(a: float) -> dict[str, Any]:
    """A model of an indicator: the range differs from threshold to threshold (so the per-threshold run differs)."""
    return {"type": "semivariogram", "c0": 0.05, "common_axes": True,
            "structures": [{"type": "spherical", "c": 0.2, "ang1": 0, "ang2": 0, "ang3": 0, "a1": a, "a2": a,
                            "a3": a}]}  # fmt: skip


@dataclass
class CW(SW):
    lith: int = 0  # the table of the categories
    ind: list[int] = field(default_factory=list)  # the indicator models of THRESHOLDS


@pytest.fixture()
def cw(tmp_path: Path):  # type: ignore[no-untyped-def]
    base = world(tmp_path)
    w = CW(**base.__dict__)
    w.lith = add_lith(w.project.engine, w.set_id, [(f"H{i}-{j}", 12.0 * i) for i in range(7) for j in range(7)])
    w.ind = [vmodel(w, f"Индикатор {t}", transform=f"indicator:{t}", model=ind_model(a))  # type: ignore[arg-type]
             for t, a in zip(THRESHOLDS, (15.0, 30.0, 45.0), strict=True)]  # fmt: skip
    add_secondary(w.project, w.store, w.model, SG)
    add_secondary(w.project, w.store, w.model, "SG_GAPS", gaps=True)
    yield w
    w.project.engine.dispose()


def th_body(cw: CW, models: str = "median", **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"method": {"ik": {"thresholds": THRESHOLDS, "models": models}}}
    if models == "median":
        out["variogram"] = {"model_id": cw.vz}
    else:
        out.update(variogram=None, variograms=[{"model_id": m} for m in cw.ind])
    return {**out, **over}


def cat_body(cw: CW, models: str = "median", **over: Any) -> dict[str, Any]:
    samples = {"set_id": cw.set_id, "table": str(cw.lith), "field": LITH,
               "domain": {"table": str(cw.b), "field": "ZONE", "values": ["окисл"]}}  # fmt: skip
    out = th_body(cw, models, samples=samples, method={"ik": {"categories": list(CODES), "labels": LABELS,
                                                                "models": models}})  # fmt: skip
    if models == "per":
        out["variograms"] = [{"model_id": cw.vz}] * 3
    return {**out, **over}


# --- SIS with thresholds ---------------------------------------------------------------------------------------------


def test_sis_thresholds_median_and_per_model(cw: CW) -> None:
    ident = make(cw, **th_body(cw))
    out = run(cw, simulation_id=ident, threads=2)
    assert (out["simulation_id"], out["realizations"]) == (ident, 4) and "gap" not in out["counts"]
    a = table(cw, ident)
    assert a.shape == (4, out["nodes"]) and bool(np.isfinite(a).all()) and 0.0 < float(a.min()) < float(a.max()) < 5.0
    got = get(cw, ident)
    assert got["meta"]["kind"] == "sis" and got["meta"]["last_run"]["params"] and got["stale"] is False
    assert got["meta"]["variogram"]["scale"] == "z" and got["meta"]["method"]["ik"]["thresholds"] == THRESHOLDS
    assert got["file"]["columns"] == ["p", "dom", "r001", "r002", "r003", "r004"]
    # the same bits with one thread and with another row of the same parameters (Т18б-С4)
    again = make(cw, **th_body(cw, name="Тот же"))
    run(cw, simulation_id=again, threads=1)
    assert np.array_equal(table(cw, again), a)
    # one model per threshold: the copies of the indicator models, «Где используется»
    per = make(cw, **th_body(cw, "per", name="По порогам"))
    assert run(cw, simulation_id=per, threads=2)["nodes"] == out["nodes"]
    meta = get(cw, per)["meta"]
    assert [v["scale"] for v in meta["variograms"]] == ["z"] * 3 and meta["variogram"] is None
    assert [v["model_id"] for v in meta["variograms"]] == cw.ind and not np.array_equal(table(cw, per), a)
    with dialect.read_tx(cw.project.engine) as conn:
        assert [r["id"] for r in st.usage_of_variogram(conn, cw.ind[1])] == [per]
        assert [r["id"] for r in st.usage_of_variogram(conn, cw.vz)] == [ident, again]


def test_sis_post_processing_is_that_of_a_continuous_variable(cw: CW) -> None:
    ident = make(cw, **th_body(cw))
    run(cw, simulation_id=ident)
    res = run_post(cw, ident, thresholds=[1.0])
    assert res["fields"] == ["AU_SIS_MEAN", "AU_SIS_STD", "AU_SIS_P10", "AU_SIS_P50", "AU_SIS_P90",
                             "AU_SIS_PROB_GT_1"]  # fmt: skip
    cols = model_of(cw).columns(["AU_SIS_MEAN", "AU_SIS_P10", "AU_SIS_P90"])
    ok = np.isfinite(cols["AU_SIS_MEAN"])
    assert ok.sum() > 0 and bool((cols["AU_SIS_P10"][ok] <= cols["AU_SIS_P90"][ok]).all())
    assert not bool(np.isfinite(cols["AU_SIS_MEAN"][~ok]).any())  # outside the target — empty, not 0


# --- SIS with categories ---------------------------------------------------------------------------------------------


def test_the_codes_of_the_samples_follow_them_by_row_id(cw: CW) -> None:
    ident = make(cw, **cat_body(cw))
    with dialect.read_tx(cw.project.engine) as conn:
        spec = sc.load_spec(sc.stored(conn, ident).meta or {})
    samples = sm.load(cw.project, spec)
    codes = secondary.sample_codes(cw.project, spec, samples)
    k = np.rint((99.0 - samples.xyz[:, 2]) / 2.0).astype(int)  # collars at 100 m, vertical holes, 2 m intervals
    want = [lith_code(x, int(kk)) for x, kk in zip(samples.xyz[:, 0], k, strict=True)]
    assert codes.tolist() == want and set(codes.tolist()) == set(CODES)  # the rows of the table are shuffled


def test_sis_categories_are_u1_codes_and_their_summary_is_shares(cw: CW) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    ident = make(cw, **cat_body(cw))
    out = run(cw, simulation_id=ident, threads=2)
    path = BlobStore(cw.project.folder).path_of(sha_of(cw, ident) or "")
    schema = pq.ParquetFile(path).schema_arrow
    assert schema.field("r001").type == pa.uint8() and schema.field("p").type == pa.uint64()
    a = reals(cw, ident).rows(0, out["nodes"])
    assert set(np.unique(a).tolist()) <= set(CODES) and len(np.unique(a)) >= 2
    last = get(cw, ident)["meta"]["last_run"]
    assert last["summary"]["all"]["codes"] == list(CODES) and abs(sum(last["summary"]["all"]["shares"]) - 1.0) < 1e-12
    assert [round(sum(p["shares"]), 12) for p in last["summary"]["per"]] == [1.0] * 4
    per = make(cw, **cat_body(cw, "per", name="По категориям"))
    run(cw, simulation_id=per, threads=1)
    b = reals(cw, per).rows(0, out["nodes"])
    assert set(np.unique(b).tolist()) <= set(CODES)
    again = make(cw, **cat_body(cw, name="Тот же"))
    run(cw, simulation_id=again, threads=1)
    assert np.array_equal(reals(cw, again).rows(0, out["nodes"]), a)  # the threads do not change the bits


def test_sis_categories_post_probabilities_and_the_mode(cw: CW) -> None:
    ident = make(cw, **cat_body(cw))
    run(cw, simulation_id=ident)
    refused(lambda: run_post(cw, ident, thresholds=[1.0]), 422, "bad_param", "thresholds")
    refused(lambda: run_post(cw, ident, stats=["mean"]), 422, "bad_param", "stats.0")
    res = run_post(cw, ident)
    names = [f"LITH_SIS_PROB_{c}" for c in CODES]
    assert res["fields"] == [*names, "LITH_SIS_MODE"]
    cols = model_of(cw).columns([*names, "LITH_SIS_MODE"])
    p = np.stack([cols[n] for n in names])
    ok = np.isfinite(p).all(axis=0)
    assert ok.sum() > 0 and np.allclose(p[:, ok].sum(axis=0), 1.0, atol=1e-6)  # Σ p_k = 1 at every node
    assert not bool(np.isfinite(p[:, ~ok]).any())
    mode = cols["LITH_SIS_MODE"]
    assert np.array_equal(mode[ok], np.argmax(p[:, ok].astype(np.float32), axis=0)) and bool((mode[~ok] == -1).all())
    f = model_of(cw).field("LITH_SIS_MODE")
    assert f["kind"] == "category" and f["labels"] == LABELS and f["source"]["op"] == "simulate"
    only = run_post(cw, ident, stats=["mode"], prefix="M_", replace=True)
    assert only["fields"] == ["M_MODE"]


# --- co-SGS ------------------------------------------------------------------------------------------------------


def test_cosgs_with_rho_zero_is_the_sgs_bit_for_bit(cw: CW) -> None:
    plain = make(cw)
    run(cw, simulation_id=plain, threads=2)
    co = make(cw, name="Ко-SGS", secondary={"field": "sg", "rho": 0})  # the name as the model has it: SG
    out = run(cw, simulation_id=co, threads=1)
    assert out["secondary"] == {"field": SG, "rho": 0.0, "rho_used": 0.0, "pairs": 0, "gaps": 0}
    assert np.array_equal(table(cw, co), table(cw, plain))
    meta = get(cw, co)["meta"]
    assert meta["kind"] == "cosgs" and meta["secondary"] == {"field": SG, "rho": 0.0, "rho_used": 0.0}
    assert (get(cw, co)["stale"], meta["last_run"]["secondary"]["gaps"]) == (False, 0)
    assert any(i["kind"] == "bm_field" and i["id"] == f"{cw.model}:{SG}" for i in meta["inputs"])  # the graph input
    hi = make(cw, name="Ко-SGS 0,9", secondary={"field": SG, "rho": 0.9})
    assert run(cw, simulation_id=hi, threads=2)["secondary"]["rho_used"] == 0.9
    assert not np.array_equal(table(cw, hi), table(cw, plain))  # the secondary enters


def test_cosgs_computes_rho_and_takes_gaps_as_sgs_nodes(cw: CW) -> None:
    ident = make(cw, name="ρ по пробам", secondary={"field": SG, "rho": None})
    out = run(cw, simulation_id=ident, threads=2)
    sec = out["secondary"]
    assert sec["rho"] is None and -1.0 <= sec["rho_used"] <= 1.0 and sec["pairs"] >= 10 and sec["gaps"] == 0
    assert get(cw, ident)["meta"]["secondary"]["rho_used"] == sec["rho_used"]
    gap = make(cw, name="С пропусками", secondary={"field": "SG_GAPS", "rho": 0.5})
    out = run(cw, simulation_id=gap, threads=2)
    assert out["secondary"]["gaps"] > 0 and out["counts"]["gap"] > 0 and any("без вторичной" in s for s in out["log"])
    a = table(cw, gap)
    assert bool(np.isfinite(a).all())  # a node without the secondary is modelled as SGS, never left empty or 0
    # the post-processing is that of the SGS
    assert run_post(cw, gap)["fields"][0] == "AU_COSGS_MEAN"


def test_cosgs_refuses_a_secondary_that_is_not_a_number_field(cw: CW) -> None:
    refused(lambda: make(cw, secondary={"field": "NOPE", "rho": 0.5}), 422, "field_not_found", "secondary.field")
    refused(lambda: make(cw, secondary={"field": "DOM", "rho": 0.5}), 422, "field_not_number", "secondary.field")
    refused(lambda: make(cw, secondary={"field": SG, "rho": 1.5}), 422, "bad_param", "secondary.rho")
    ident = make(cw, secondary={"field": SG, "rho": 0.5})
    version = get(cw, ident)["version"]
    assert put(cw, ident, version, secondary={"field": "sg_gaps", "rho": None})["version"] == version + 1
    assert get(cw, ident)["meta"]["secondary"] == {"field": "SG_GAPS", "rho": None, "rho_used": None}


def test_sis_refusals_of_the_models(cw: CW) -> None:
    refused(lambda: make(cw, **th_body(cw, variogram={"model_id": cw.vy})), 422, "variogram_scale",
            "variogram.model_id")  # fmt: skip
    refused(lambda: make(cw, **th_body(cw, "per", variograms=[{"model_id": cw.vy}] * 3)), 422, "variogram_scale",
            "variograms.0.model_id")  # fmt: skip
    ident = make(cw, **th_body(cw))
    version = get(cw, ident)["version"]
    new = put(cw, ident, version, **th_body(cw, "per"))
    assert new["version"] == version + 1
    meta = get(cw, ident)["meta"]
    assert meta["method"]["ik"]["models"] == "per" and len(meta["variograms"]) == 3 and meta["variogram"] is None
