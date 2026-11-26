"""The rows, the curve and the totals of a resource report (подшаг 20.3; ТЗ 8.10; ADR 0027 decisions 4–6, 10–12).

Т8.10-1 through the whole chain (spec → core → rows → form of the template: 12 500 t, 2,5 g/t, 31,25 kg =
1,004711 thousand ounces), Т8.10-2 through the spec (the rows of BF `grade_tonnage`), **Т8.10-3** (the property on 50
random models with PCG64: the sum over the categories and the domains = the total, the tonnage does not grow with the
cut-off), the balance rows, the curve against a brute force and against the table, the bench labels «+250…+260», a
grade that is empty (not 0) at Σt = 0, the same bits with any portion, the truncated preview, 422 in Russian."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import restrict
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.resources import compute as cp
from bf_core.resources import forms, templates
from bf_core.resources import result as rr
from bf_core.resources.spec import parse_params
from bf_core.storage.blobs import BlobStore
from tests.ref import bf_report as bf
from tests.test_blocks_report import put
from tests.test_resources_kernels import bf_model2, bf_ref

OZ = bf.TROY_OUNCE_G


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Ресурсы"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


CLASS = {"name": "CLASS", "dtype": "cat", "labels": ["Измеренные", "Выявленные", "Предполагаемые"], "role": "class"}


def prepared(c: Any, mid: int, body: dict[str, Any]) -> tuple[Model, rr.Plan, restrict.Restrictor]:
    """The model, the plan of the body and the restrictor of its rules."""
    store = BlobStore(c.project.folder)
    with dialect.read_tx(c.project.engine) as conn:
        model = Model.open(conn, store, mid)
        spec = parse_params({"model_id": mid, **body}).resolved(model.fields, rr.z_range_of(model))
        return model, rr.core_from_spec(model, spec), restrict.prepare(model, spec.restrict, conn=conn, store=store)


def report(c: Any, mid: int, body: dict[str, Any], chunk: int = 1 << 20) -> tuple[rr.Plan, cp.Sums, rr.Result]:
    model, plan, r = prepared(c, mid, body)
    sums = cp.run(model, plan.core, r, chunk=chunk)
    return plan, sums, rr.build(sums, plan)


def pick(rows: list[dict[str, Any]], kind: str = "cutoff", cutoff: float | None = None, g: Any = "total") -> Any:
    """The row of a kind and a cut-off: `g` — a list of labels, «total» — the total row."""
    got = [
        r
        for r in rows
        if r["kind"] == kind and r["cutoff"] == cutoff and ((r["total"] and g == "total") or r["g"] == g)
    ]
    assert len(got) == 1, (kind, cutoff, g, len(got))
    return got[0]


def close(a: float, b: float, rel: float = 1e-12) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b))


GRID8 = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 2], "size": [10.0, 10.0, 10.0]}
AU8 = np.array([0.2, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0])


def model8(c: Any, grid: dict[str, Any] = GRID8, cls: np.ndarray | None = None) -> int:
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"}, CLASS]
    cls = np.zeros(8, dtype=np.int32) if cls is None else cls
    return put(c, grid, fields, np.arange(8, dtype=np.uint64), {"AU": AU8, "CLASS": cls})


# --- Т8.10-1: the whole chain -----------------------------------------------------------------------------------------


def test_t8_10_1_spec_rows_and_the_form_of_the_template(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model8(rc)
    body = {"elements": ["AU"], "cutoffs": [1.0], "density": 2.5, "groups": ["CLASS"], "curve": 0}
    plan, sums, res = report(rc, mid, body)
    total = pick(res.rows, cutoff=1.0)
    au = total["elements"]["AU"]
    assert (total["blocks"], total["volume"], total["tonnes"]) == (5, 5000.0, 12_500.0)
    assert au["grade"] == 2.5 and au["metal"] == 31_250.0 and au["missing_n"] == 0  # grams: 31,25 kg
    assert res.totals["tonnes"] == 12_500.0 and res.totals["elements"]["AU"]["metal"] == 31_250.0
    # the only group: «Измеренные» (the rows of the others are not there), the same numbers as the total
    group = pick(res.rows, cutoff=1.0, g=["Измеренные"])
    assert group["tonnes"] == 12_500.0 and group["elements"]["AU"]["metal"] == 31_250.0
    assert not [r for r in res.rows if r["g"] in (["Выявленные"], ["Предполагаемые"])]
    # → the form of the template JORC: thousand tonnes, g/t, thousand ounces
    spec = plan.spec
    flat = [rr.flat_row(r, plan.nkeys) for r in res.rows]
    tpl = {"id": "jorc", **templates.defaults()["jorc"]}
    form = forms.build(flat, tpl, rr.elements_of(spec), rr.roles_of(spec, plan_fields(rc, mid)), cutoff=1.0)
    cells = {r["label"]: r["cells"] for r in form["sections"][0]["rows"]}
    assert not form["warnings"], form["warnings"]
    for label in ("Измеренные", "Итого"):
        c = cells[label]
        assert close(c["tonnes"]["value"], 12.5) and c["tonnes"]["text"] == "13"  # kt, no decimals
        assert c["grade:AU"]["value"] == 2.5 and c["grade:AU"]["text"] == "2,50"
        assert abs(c["metal:AU"]["value"] - 1.004711) < 1e-6  # thousand troy ounces
    assert cells["Выявленные"]["tonnes"]["value"] == 0.0 and cells["Выявленные"]["grade:AU"]["value"] is None


def plan_fields(c: Any, mid: int) -> list[dict[str, Any]]:
    with dialect.read_tx(c.project.engine) as conn:
        return list(Model.open(conn, BlobStore(c.project.folder), mid).fields)


def test_rows_order_kinds_and_labels(rc) -> None:  # type: ignore[no-untyped-def]
    cls = np.array([0, 0, 1, 1, -1, 2, 2, 2], dtype=np.int32)  # the block 4 has no class: «(пусто)»
    mid = model8(rc, cls=cls)
    body = {"elements": ["AU"], "cutoffs": [1.0, None, 0.5], "density": 2.5, "groups": ["CLASS"], "curve": 0,
            "balance": {"cutoff": 1.0, "off_cutoff": 0.5}}  # fmt: skip
    plan, sums, res = report(rc, mid, body)
    kinds = [(r["kind"], r["cutoff"], r["cutoff_to"]) for r in res.rows if r["total"]]
    assert kinds == [("all", None, None), ("cutoff", 1.0, None), ("cutoff", 0.5, None), ("balance", 1.0, None),
                     ("offbalance", 0.5, 1.0)]  # fmt: skip
    first = [r["g"] for r in res.rows if r["kind"] == "all"]
    assert first == [[None], ["(пусто)"], ["Измеренные"], ["Выявленные"], ["Предполагаемые"]]  # the total, the codes
    assert res.rows_total == len(res.rows) == 5 * 5 and not res.truncated
    # balance = the cut-off row of c_б; the off-balance — what is between the cut-offs
    assert pick(res.rows, "balance", 1.0)["tonnes"] == pick(res.rows, cutoff=1.0)["tonnes"] == 12_500.0
    off = pick(res.rows, "offbalance", 0.5)
    assert off["tonnes"] == 5_000.0 and off["blocks"] == 2 and off["elements"]["AU"]["grade"] == (0.5 + 0.8) / 2
    for g in (["Измеренные"], ["Выявленные"], ["(пусто)"], ["Предполагаемые"]):  # a row for every cut-off
        assert len([r for r in res.rows if r["g"] == g]) == 5
    # «(пусто)» is a group with its own numbers: the block with AU 1.5
    assert pick(res.rows, cutoff=1.0, g=["(пусто)"])["tonnes"] == 2500.0
    assert pick(res.rows, "all", None, ["Выявленные"])["blocks"] == 2


def test_empty_grade_is_null_not_zero(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model8(rc)
    body = {"elements": ["AU"], "cutoffs": [9.0], "density": 2.5, "groups": ["CLASS"], "curve": 0}
    _, _, res = report(rc, mid, body)
    for r in res.rows:  # nothing passes the cut-off 9: T = 0, the grade and the metal are empty, never 0
        assert r["tonnes"] == 0.0 and r["blocks"] == 0
        assert r["elements"]["AU"]["grade"] is None and r["elements"]["AU"]["metal"] is None
    assert '"grade": null' in json.dumps(res.rows[0]) and '"grade": 0' not in json.dumps(res.rows)


def test_missing_grade_is_out_of_the_mean_and_the_metal(rc) -> None:  # type: ignore[no-untyped-def]
    au = AU8.copy()
    au[[6, 7]] = np.nan
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t"}, {"name": "CU", "dtype": "f8", "unit": "ppm"}]
    mid = put(rc, GRID8, fields, np.arange(8, dtype=np.uint64), {"AU": au, "CU": np.full(8, np.nan)})
    body = {"elements": ["AU", "CU"], "cutoffs": [None], "density": 2.5, "curve": 0}
    _, _, res = report(rc, mid, body)
    (row,) = res.rows
    a, cu = row["elements"]["AU"], row["elements"]["CU"]
    assert a["wt"] == 15_000.0 and a["missing_t"] == 5_000.0 and a["missing_n"] == 2
    assert close(a["grade"], float(np.sum(AU8[:6])) / 6) and close(a["metal"], a["sum"])
    assert cu["grade"] is None and cu["metal"] is None and cu["missing_n"] == 8 and cu["missing_t"] == 20_000.0
    assert row["tonnes"] == 20_000.0  # the blocks with no grade are in the tonnage


def test_unknown_unit_gives_no_metal_and_a_warning(rc) -> None:  # type: ignore[no-untyped-def]
    fields = [{"name": "AU", "dtype": "f8", "unit": "кг/т"}]
    mid = put(rc, GRID8, fields, np.arange(8, dtype=np.uint64), {"AU": AU8})
    plan, _, res = report(rc, mid, {"elements": ["AU"], "cutoffs": [None], "density": 2.5, "curve": 0})
    assert res.rows[0]["elements"]["AU"]["metal"] is None and res.rows[0]["elements"]["AU"]["grade"] is not None
    (w,) = [x for x in res.warnings if x["code"] == "unit_unknown"]
    assert w["param"] == "elements.0.unit" and "AU" in w["message"] and w["hint"]


def test_percent_ppb_and_g_per_t_metal(rc) -> None:  # type: ignore[no-untyped-def]
    fields = [{"name": "A", "dtype": "f8", "unit": "g/t"}, {"name": "B", "dtype": "f8", "unit": "%"},
              {"name": "C", "dtype": "f8", "unit": "ppb"}]  # fmt: skip
    cols = {"A": np.full(8, 2.0), "B": np.full(8, 3.0), "C": np.full(8, 4000.0)}
    mid = put(rc, GRID8, fields, np.arange(8, dtype=np.uint64), cols)
    _, _, res = report(rc, mid, {"elements": ["A", "B", "C"], "cutoffs": [None], "density": 2.5, "curve": 0})
    e = res.rows[0]["elements"]
    t = 20_000.0
    assert (
        close(e["A"]["metal"], t * 2.0) and close(e["B"]["metal"], t * 3.0 * 0.01) and close(e["C"]["metal"], t * 4.0)
    )


# --- Т8.10-2 through the spec: the rows of BF -------------------------------------------------------------------------


def test_t8_10_2_rows_of_bf_through_the_spec(rc) -> None:  # type: ignore[no-untyped-def]
    mid, m = bf_model2(rc)
    cuts: list[float | None] = [0.0, 0.5, 1.0, 2.5, None]
    body = {"elements": [{"field": "AU_CUT", "unit": "g/t"}, {"field": "CU_CUT", "unit": "%"},
                         {"field": "PB", "unit": "ppb"}],
            "cutoff_field": "AU_CUT", "cutoffs": cuts, "density": "SG", "groups": ["ALT"], "curve": 0}  # fmt: skip
    plan, sums, res = report(rc, mid, body, chunk=7)
    ref = bf_ref(m, np.ones(m["n"], dtype=bool), "AU_CUT", cuts, key="ALT")
    assert ref["excluded_no_density"] == res.excluded["no_density"] >= 2
    n_rows = 0
    for row in ref["rows"]:
        kind = "all" if row["cutoff"] is None else "cutoff"
        got = pick(res.rows, kind, row["cutoff"], "total" if row["group"] is None else [row["group"]])
        n_rows += 1
        assert got["blocks"] == row["blocks"] and close(got["volume"], row["volume"])
        assert close(got["tonnes"], row["tonnes"])
        for name, unit in (("AU_CUT", "g/t"), ("CU_CUT", "%"), ("PB", "ppb")):
            want, cell = row["grades"][name], got["elements"][name]
            if want["grade"] is None:
                assert cell["grade"] is None and cell["metal"] is None
                continue
            assert close(cell["grade"], want["grade"])
            mt = bf.metal(cell["sum"], unit)
            assert close(cell["metal"], mt["value"] * (1.0 if unit == "%" else OZ * 1000.0)), (name, row["cutoff"])
            assert abs(cell["missing_t"] - want["missing_tonnes"]) <= 1e-9 * max(1.0, got["tonnes"])
    assert n_rows == len(ref["rows"]) and n_rows <= len(res.rows)


# --- the curve --------------------------------------------------------------------------------------------------------


def test_curve_against_a_brute_force_and_the_table(rc) -> None:  # type: ignore[no-untyped-def]
    au = np.array([0.2, 0.5, np.nan, 1.0, 1.5, 2.0, 3.0, 5.0])
    cu = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, np.nan, 8.0])
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t"}, {"name": "CU", "dtype": "f8", "unit": "%"}]
    mid = put(rc, GRID8, fields, np.arange(8, dtype=np.uint64), {"AU": au, "CU": cu})
    body = {"elements": ["AU", "CU"], "cutoff_field": "AU", "cutoffs": [0.5, 1.0, 2.0], "density": 2.5, "curve": 11}
    plan, sums, res = report(rc, mid, body)
    cv = res.curve
    assert cv is not None and cv["field"] == "AU" and len(cv["cutoffs"]) == 11
    assert cv["cutoffs"][0] == 0.2 and cv["cutoffs"][-1] == 5.0
    t = 2500.0
    for i, c in enumerate(cv["cutoffs"]):
        sel = np.isfinite(au) & (au >= c)  # a block with no AU is in no cut-off
        assert cv["blocks"][i] == int(sel.sum()) and cv["tonnes"][i] == t * sel.sum()
        assert cv["volume"][i] == 1000.0 * sel.sum()
        for name, v, k in (("AU", au, 1.0), ("CU", cu, 0.01)):
            ok = sel & np.isfinite(v)
            wt = t * ok.sum()
            got = cv["elements"][name]
            if wt == 0:
                assert got["grade"][i] is None and got["metal"][i] is None
            else:
                assert close(got["grade"][i], math.fsum(v[ok]) / ok.sum())
                assert close(got["metal"][i], math.fsum(v[ok] * t) * k)
        assert cv["tonnes"] == sorted(cv["tonnes"], reverse=True)  # T does not grow with the cut-off
    # the same bits as the table at the cut-offs both have (the table: bins by `range_sums`, the curve: suffix sums)
    for c in (0.5, 1.0, 2.0):
        sums_plan = plan
        assert c in sums_plan.core.edges
        row = pick(res.rows, cutoff=c)
        i = int(np.searchsorted(cv["cutoffs"], c))
        if i < len(cv["cutoffs"]) and cv["cutoffs"][i] == c:
            assert cv["tonnes"][i] == row["tonnes"]
    # no curve: `curve` = 0
    _, _, res0 = report(rc, mid, {**body, "curve": 0})
    assert res0.curve is None


def test_curve_cutoffs_do_not_change_the_table(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model8(rc)
    body = {"elements": ["AU"], "cutoffs": [0.5, 1.0], "density": 2.5, "groups": ["CLASS"]}
    _, _, with_curve = report(rc, mid, {**body, "curve": 100})
    _, _, without = report(rc, mid, {**body, "curve": 0})
    assert json.dumps(with_curve.rows) == json.dumps(without.rows)  # the bins of the curve add no bit to the rows
    assert with_curve.curve is not None and len(with_curve.curve["cutoffs"]) == 100


# --- benches ----------------------------------------------------------------------------------------------------------


def test_bench_labels_and_shares(rc) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 250.0], "counts": [2, 2, 2], "size": [10.0, 10.0, 10.0]}
    mid = model8(rc, grid)
    body = {"elements": ["AU"], "cutoffs": [None], "density": 2.5, "groups": [{"kind": "bench"}],
            "bench": {"h": 10.0}, "curve": 0}  # fmt: skip
    plan, _, res = report(rc, mid, body)
    labels = [r["g"] for r in res.rows if not r["total"]]
    assert labels == [["+250…+260"], ["+260…+270"]]
    assert pick(res.rows, "all", None, ["+250…+260"])["tonnes"] == 4 * 2500.0
    # a bench of 15 m cuts the layers: «(пусто)» is the part above the benches
    body2 = {**body, "bench": {"h": 15.0, "z0": 250.0, "z_min": 250.0, "z_max": 265.0}}
    _, _, res2 = report(rc, mid, body2)
    by = {tuple(r["g"]): r["tonnes"] for r in res2.rows if not r["total"]}
    assert close(sum(by.values()), 20_000.0) and close(by[("+250…+265",)], 4 * 2500.0 + 4 * 2500.0 * 0.5)
    assert close(by[("(пусто)",)], 4 * 2500.0 * 0.5)


# --- Т8.10-3: the property --------------------------------------------------------------------------------------------


GROUPS = [["CLASS"], ["DOM", "CLASS"], ["DOM", {"kind": "bench"}], [{"kind": "bench"}, "CLASS", "DOM"]]


def random_case(c: Any, seed: int) -> tuple[int, dict[str, Any]]:
    """A model of a random size with gaps in the grades and in the density, two categories (the domain, the class —
    with a code of «no value»), a random set of cut-offs; the body of the report."""
    rng = np.random.Generator(np.random.PCG64(seed))
    counts = [int(x) for x in rng.integers(1, 6, 3)]
    n = counts[0] * counts[1] * counts[2]
    grid = {"corner": [float(rng.uniform(-50, 50)), float(rng.uniform(-50, 50)), float(rng.uniform(-30, 30))],
            "counts": counts, "size": [float(x) for x in rng.uniform(2.0, 12.0, 3)]}  # fmt: skip
    au = rng.lognormal(0.0, 1.0, n)
    au[rng.random(n) < 0.15] = np.nan
    cu = rng.random(n) * 3
    cu[rng.random(n) < 0.3] = np.nan
    sg = rng.uniform(1.8, 3.4, n)
    sg[rng.random(n) < 0.1] = np.nan
    dom = rng.integers(-1, 3, n).astype(np.int32)
    cls = rng.integers(-1, 3, n).astype(np.int32)
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t"}, {"name": "CU", "dtype": "f8", "unit": "%"},
              {"name": "SG", "dtype": "f8", "role": "density"},
              {"name": "DOM", "dtype": "cat", "labels": ["A", "B", "C"]}, CLASS]  # fmt: skip
    mid = put(c, grid, fields, np.arange(n, dtype=np.uint64), {"AU": au, "CU": cu, "SG": sg, "DOM": dom, "CLASS": cls},
              name=f"М{seed}")  # fmt: skip
    cuts = sorted(float(x) for x in rng.uniform(0.0, 3.0, int(rng.integers(1, 5))))
    zt = grid["corner"][2] + counts[2] * grid["size"][2]
    body: dict[str, Any] = {
        "elements": ["AU", "CU"], "cutoff_field": "AU", "cutoffs": [None, *cuts], "density": "SG",
        "groups": GROUPS[seed % 4],
        "bench": {"h": float(rng.uniform(3.0, 20.0)), "z_max": zt}, "curve": 30,
        "balance": {"cutoff": cuts[-1], "off_cutoff": cuts[0] if cuts[0] < cuts[-1] else None},
    }  # fmt: skip
    return mid, body


def test_t8_10_3_property_on_50_models(rc) -> None:  # type: ignore[no-untyped-def]
    checked_groups = 0
    for seed in range(50):
        mid, body = random_case(rc, seed)
        plan, sums, res = report(rc, mid, body, chunk=1 + seed % 17)
        has_bench = any(k.kind == "bench" for k in plan.spec.keys)
        tables = sorted({(r["kind"], r["cutoff"], r["cutoff_to"]) for r in res.rows}, key=repr)
        for kind, cutoff, cutoff_to in tables:
            rows = [r for r in res.rows if (r["kind"], r["cutoff"], r["cutoff_to"]) == (kind, cutoff, cutoff_to)]
            total = next(r for r in rows if r["total"])
            groups = [r for r in rows if not r["total"]]
            checked_groups += len(groups)
            # Σ over the groups (every combination of the categories, the domains and the benches) = the total
            assert close(math.fsum(r["tonnes"] for r in groups), total["tonnes"]), (seed, kind, cutoff)
            assert close(math.fsum(r["volume"] for r in groups), total["volume"]), (seed, kind, cutoff)
            if not has_bench:  # a block on two benches is counted in both groups, once in the total
                assert sum(r["blocks"] for r in groups) == total["blocks"], (seed, kind, cutoff)
            for name in plan.names:
                a = math.fsum(r["elements"][name]["sum"] for r in groups)
                assert close(a, total["elements"][name]["sum"]), (seed, kind, cutoff, name)
                assert close(math.fsum(r["elements"][name]["wt"] for r in groups), total["elements"][name]["wt"])
                assert close(math.fsum(r["elements"][name]["missing_t"] for r in groups),
                             total["elements"][name]["missing_t"])  # fmt: skip
        # the tonnage never grows with the cut-off: «all» ≥ the cut-offs from the lowest up; the curve too
        cut_rows = sorted((r for r in res.rows if r["total"] and r["kind"] == "cutoff"), key=lambda r: r["cutoff"])
        chain = [pick(res.rows, "all", None)["tonnes"], *(r["tonnes"] for r in cut_rows)]
        assert all(b <= a * (1 + 1e-12) for a, b in zip(chain, chain[1:], strict=False)), (seed, chain)
        if res.curve is not None:
            tn = res.curve["tonnes"]
            assert all(b <= a * (1 + 1e-12) for a, b in zip(tn, tn[1:], strict=False)), seed
            assert tn[0] <= chain[0] * (1 + 1e-12)
        # the balance: balance + off-balance = the cut-off of the lower bound, and each group too
        bal = [r for r in res.rows if r["total"] and r["kind"] == "balance"]
        off = [r for r in res.rows if r["total"] and r["kind"] == "offbalance"]
        if off:
            lo = pick(res.rows, "cutoff", off[0]["cutoff"])
            assert close(bal[0]["tonnes"] + off[0]["tonnes"], lo["tonnes"]), seed
    assert checked_groups > 500  # the property was really exercised


# --- the same bits, the preview, the errors ---------------------------------------------------------------------------


def test_same_rows_with_any_portion(rc) -> None:  # type: ignore[no-untyped-def]
    mid, body = random_case(rc, 3)
    runs = [report(rc, mid, body, chunk=c)[2] for c in (1, 7, 1 << 20)]
    ref = json.dumps([runs[0].rows, runs[0].curve, runs[0].totals])
    for other in runs[1:]:
        assert json.dumps([other.rows, other.curve, other.totals]) == ref


def test_preview_is_truncated_with_a_warning(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model8(rc, cls=np.array([0, 1, 2, 0, 1, 2, 0, 1], dtype=np.int32))
    body = {"elements": ["AU"], "cutoffs": [0.5, 1.0], "density": 2.5, "groups": ["CLASS"], "curve": 0}
    plan, sums, _ = report(rc, mid, body)
    res = rr.build(sums, plan, max_rows=5)
    assert len(res.rows) == 5 and res.rows_total == 2 * 4 and res.truncated
    (w,) = [x for x in res.warnings if x["code"] == "rows_truncated"]
    assert w["message"] and w["hint"]
    assert res.totals is not None and res.totals["cutoff"] == 0.5  # the totals of the first table, whatever the cut
    assert rr.row_count(sums, plan) == 8


def test_no_keys_means_only_total_rows(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model8(rc)
    _, _, res = report(rc, mid, {"elements": ["AU"], "cutoffs": [None, 1.0], "density": 2.5, "curve": 0})
    assert [r["g"] for r in res.rows] == [[], []] and all(r["total"] for r in res.rows)
    flat = rr.flat_row(res.rows[1], 0)
    assert flat["g1"] is None and flat["AU:grade"] == 2.5 and flat["AU:wt"] == 12_500.0 and flat["AU:missing_n"] == 0
    assert set(flat) >= {"g1", "g2", "g3", "kind", "total", "cutoff", "cutoff_to", "blocks", "volume", "tonnes",
                         "AU:wt", "AU:sum", "AU:metal", "AU:grade", "AU:missing_t", "AU:missing_n"}  # fmt: skip


def test_unresolved_spec_is_refused_in_russian(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model8(rc)
    store = BlobStore(rc.project.folder)
    with dialect.read_tx(rc.project.engine) as conn:
        model = Model.open(conn, store, mid)
    with pytest.raises(ApiError) as e:
        rr.core_from_spec(model, parse_params({"model_id": mid}))
    assert e.value.status == 422 and e.value.code == "bad_param" and e.value.hint
    assert any("а" <= ch.lower() <= "я" for ch in e.value.message)
