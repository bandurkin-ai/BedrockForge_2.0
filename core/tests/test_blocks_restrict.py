"""The restrictor of a block model (подшаг 17.8; ТЗ 7.31, 7.35 Д-02, 8.7.5; ADR 0023 §8, decisions 11–12):
«AU > 1 И DOMAIN = 2» and its inverse, a filter on two models = the direct expression, 422 `filter_field_missing`, a
surface «по центру» and «по доле», the shares of И / ИЛИ / НЕ, the extension point of the leaf «каркас» (17.20), the
chunks, `/filters/test` → {passed, total, weight}."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import restrict
from bf_core.blocks import store as bm
from bf_core.blocks.filters import routes
from bf_core.blocks.model import Model
from bf_core.calc import compile as calc_compile
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as wf
from tests.test_blocks_fields_store import make_model
from tests.test_filters_store import cond, group

API = "/api/v1"
FL = API + "/filters"
GRID = {"corner": [0.0, 0.0, 0.0], "counts": [4, 3, 2], "size": [5.0, 5.0, 2.5]}  # 24 blocks, 2 layers of 2.5 m
FIELDS = [{"name": "AU", "kind": "number", "unit": "g/t"}, {"name": "DOMAIN", "kind": "number", "unit": None}]
AU = np.array([0.5, 1.0, 1.5, 3.0, np.nan, 2.0, 0.2, 5.0, 1.1, 1.0001, np.nan, 7.0,
               2.5, 0.9, 1.2, 4.0, 0.0, 9.0, 1.5, np.nan, 3.3, 0.1, 8.0, 2.2])  # fmt: skip
DOMAIN = np.array([2, 2, 1, 2, 2, 0, 2, 2, 2, 1, 2, 2, 2, 2, np.nan, 2, 2, 0, 2, 2, 1, 2, 2, 2], dtype=np.float64)


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    """A core with an empty project and the router of the filters (until `api/modules.py` names it)."""
    if not any(str(getattr(r, "path", "")).startswith(FL) for r in pclient.app.routes):
        pclient.app.include_router(routes.router, prefix=API)
    assert pclient.post(f"{API}/projects", json={"name": "Ограничитель"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def model(c: Any, name: str = "Модель", au: Any = AU, dom: Any = DOMAIN, fields: Any = FIELDS) -> int:
    idx = np.arange(len(au), dtype=np.uint64)
    names = [f["name"] for f in fields]
    with dialect.write_tx(c.project.engine) as conn:
        return bm.put_block_model(conn, c.store, GRID, fields, idx, dict(zip(names, (au, dom), strict=True)), name=name)


def plane(
    c: Any, z: float, name: str = "Поверхность", slope: float = 0.0, span: tuple[float, float] = (-50.0, 100.0)
) -> int:
    """A surface under the whole plan of the model: z = `z` + `slope`·x."""
    lo, hi = span
    x = np.array([lo, hi, hi, lo])
    xyz = np.column_stack([x, [lo, lo, hi, hi], z + slope * x])
    with dialect.write_tx(c.project.engine) as conn:
        return wf.put_wireframe(conn, c.store, xyz, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32), name=name)


def open_model(c: Any, mid: int) -> Model:
    with dialect.read_tx(c.project.engine) as conn:
        return Model.open(conn, c.store, mid)


def shares(c: Any, mid: int, rules: Any, chunk: int = 1 << 20) -> tuple[np.ndarray, list[str]]:
    m = open_model(c, mid)
    with dialect.read_tx(c.project.engine) as conn:
        r = restrict.prepare(m, rules, conn=conn, store=c.store)
    out = np.empty(m.rows)
    for ch, f in r.iter(m, chunk=chunk):
        out[ch.start : ch.start + ch.n] = f
    return out, r.warnings


def surface(oid: int | None, side: str = "above", by: str = "centre") -> dict[str, Any]:
    return {"surface": {"id": oid, "side": side, "by": by}}


def wire(oid: int | None, side: str = "inside", by: str = "frac") -> dict[str, Any]:
    return {"wireframe": {"id": oid, "side": side, "by": by, "m": 4}}


WANT = (AU > 1) & (DOMAIN == 2)  # NaN compares false


# --- «AU > 1 И DOMAIN = 2» and the inverse (7.31) -------------------------------------------------------------------


def test_au_and_domain_and_the_inverse(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc)
    rules = group("and", cond("AU", ">", 1), cond("DOMAIN", "=", 2))
    f, warnings = shares(rc, mid, rules)
    assert warnings == []
    assert f.dtype == np.float64 and not np.isnan(f).any()  # a gap is not a pass and not NaN
    assert np.array_equal(f, WANT.astype(np.float64))
    assert int(WANT.sum()) == 9 and not f[4] and not f[10]  # AU empty — does not pass «AU > 1»
    inverse, _ = shares(rc, mid, {"op": "not", "items": [rules]})
    assert np.array_equal(inverse, 1.0 - f)  # «обратный» = 1 − f; the empty ones pass it (two-valued logic)
    assert inverse[4] == 1.0 and inverse[14] == 1.0  # DOMAIN empty: «DOMAIN = 2» false
    assert shares(rc, mid, {"expr": "NOT (AU > 1 AND DOMAIN = 2)"})[0].tolist() == inverse.tolist()
    assert shares(rc, mid, {"expr": "AU > 1 AND DOMAIN = 2"})[0].tolist() == f.tolist()


def test_test_rules_counts_passed_total_and_weight(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc)
    rules = group("and", cond("AU", ">", 1), cond("DOMAIN", "=", 2))
    got = restrict.test_rules(rc.project, mid, rules)
    assert got == {"passed": 9, "total": 24, "weight": 9.0, "warnings": []}
    inv = restrict.test_rules(rc.project, mid, {"op": "not", "items": [rules]})
    assert (inv["passed"], inv["total"], inv["weight"]) == (15, 24, 15.0)
    # the same numbers over the route (the unsaved filter and the saved one)
    r = rc.post(f"{FL}/test", json={"model_id": mid, "rules": rules})
    assert r.status_code == 200, r.text
    assert (r.json()["passed"], r.json()["total"], r.json()["weight"]) == (9, 24, 9.0)
    made = rc.post(FL, json={"name": "Руда", "rules": rules})
    assert made.status_code == 200, made.text
    saved = rc.post(f"{FL}/{made.json()['id']}/test", json={"model_id": mid})
    assert saved.status_code == 200 and (saved.json()["passed"], saved.json()["weight"]) == (9, 9.0)
    assert set(saved.json()) == {"passed", "total", "weight", "warnings"}


def test_conditions_of_every_kind_over_a_category_and_gaps(rc) -> None:  # type: ignore[no-untyped-def]
    p = rc.project
    mid = make_model(p, rc.store)  # v2, turned by 30°: AU f8, CU f4, N i2, DOM category
    m = open_model(rc, mid)
    cols = m.columns(["AU", "CU", "DOM"])
    dom = cols["DOM"]
    labels = m.labels("DOM")
    cases = {
        "DOM = 'руда'": dom == labels.index("руда"),
        "DOM IN ('руда', 'дайка')": np.isin(dom, [labels.index("руда"), labels.index("дайка")]),
        "AU > 1 OR CU > 0.5": (cols["AU"] > 1) | (cols["CU"] > 0.5),
    }
    for text, want in cases.items():
        assert np.array_equal(shares(rc, mid, {"expr": text})[0], want.astype(np.float64)), text
    c = {"cond": {"field": "dom", "op": "=", "value": "руда"}}  # a name without case; a text of a category
    assert np.array_equal(shares(rc, mid, c)[0], (dom == labels.index("руда")).astype(np.float64))
    empty = shares(rc, mid, {"cond": {"field": "AU", "op": "empty"}})[0]
    assert np.array_equal(empty, np.isnan(cols["AU"]).astype(np.float64)) and empty.sum() > 0
    notempty = shares(rc, mid, {"cond": {"field": "AU", "op": "notempty"}})[0]
    assert np.array_equal(notempty, 1.0 - empty)
    got = shares(rc, mid, {"cond": {"field": "N", "op": "in", "values": [1, 2, 3]}})[0]
    assert np.array_equal(got, np.isin(m.columns(["N"])["N"], [1, 2, 3]).astype(np.float64))


# --- a filter on two models = the direct expression (Д-02), 422 without a field -------------------------------------


def test_one_filter_on_two_models_equals_the_direct_expression(rc) -> None:  # type: ignore[no-untyped-def]
    rules = group("and", cond("AU", ">", 1), group("or", cond("DOMAIN", "=", 2), cond("AU", ">=", 8)))
    text = "AU > 1 AND (DOMAIN = 2 OR AU >= 8)"
    rng = np.random.default_rng(5)
    au2 = rng.lognormal(0.0, 1.0, 24)
    au2[[3, 9]] = np.nan
    dom2 = rng.integers(0, 4, 24).astype(np.float64)
    swapped = [FIELDS[1], FIELDS[0]]  # another order of the fields and another case of the names
    first = model(rc, "Первая")
    second = model(rc, "Вторая", au2, dom2, [{"name": "domain", "kind": "number"}, {"name": "au", "kind": "number"}])
    third = model(rc, "Третья", dom2, au2, swapped)
    for mid in (first, second, third):
        m = open_model(rc, mid)
        names = {f["name"].casefold(): f["name"] for f in m.fields}
        cols = m.columns([names["au"], names["domain"]])
        prog = calc_compile(text, {n: "num" for n in cols})
        direct = prog.eval({n: cols[n] for n in prog.fields_used}).astype(np.float64)
        f, _ = shares(rc, mid, rules)
        assert np.array_equal(f, direct), m.name
        got = restrict.test_rules(rc.project, mid, rules)
        assert got["passed"] == int(direct.sum()) and got["total"] == 24 and got["weight"] == float(direct.sum())
    assert not np.array_equal(shares(rc, first, rules)[0], shares(rc, second, rules)[0])  # the models differ


def test_a_missing_field_is_422_with_the_field_and_the_model(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc, "Без CU")
    lacking = (cond("CU", ">", 1), {"expr": "AU > 1 AND cu < 3"}, group("or", cond("AU", ">", 1), cond("cu", "empty")))
    for rules in lacking:
        with pytest.raises(ApiError) as e:
            restrict.test_rules(rc.project, mid, rules)
        assert (e.value.status, e.value.code) == (422, "filter_field_missing")
        assert e.value.extra["model"] == "Без CU" and e.value.extra["field"].casefold() == "cu"
        assert "Без CU" in e.value.message and e.value.hint
    r = rc.post(f"{FL}/test", json={"model_id": mid, "expr": "CU > 1"})
    assert (r.status_code, r.json()["code"], r.json()["field"], r.json()["model"]) == (
        422, "filter_field_missing", "CU", "Без CU")  # fmt: skip
    made = rc.post(FL, json={"name": "С медью", "rules": cond("CU", ">", 1)})
    r = rc.post(f"{FL}/{made.json()['id']}/test", json={"model_id": mid})
    assert (r.status_code, r.json()["code"]) == (422, "filter_field_missing")


def test_an_expression_that_is_not_a_condition_is_refused(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc)
    with pytest.raises(ApiError) as e:
        shares(rc, mid, {"expr": "AU * 2"})
    assert (e.value.code, e.value.extra["param"]) == ("filter_rules", "rules.expr")
    with pytest.raises(ApiError) as e:  # a text in a number field: the calculator's own refusal with the place
        shares(rc, mid, cond("AU", "=", "руда"))
    assert e.value.code == "expr_type"


# --- a surface: «по центру» and «по доле» ---------------------------------------------------------------------------


def layers(f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The shares of the lower layer (k = 0: rows 0…11) and the upper (rows 12…23)."""
    return f[:12], f[12:]


def test_surface_by_the_centre_and_by_the_share(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc)
    wid = plane(rc, 1.0)  # inside the lower layer [0; 2.5]: its centre 1.25 is above
    above, warnings = shares(rc, mid, surface(wid, "above", "centre"))
    below, _ = shares(rc, mid, surface(wid, "below", "centre"))
    assert warnings == []
    assert np.array_equal(above, np.ones(24)) and np.array_equal(below, np.zeros(24))
    wid = plane(rc, 1.5, "Выше")  # the centre of the lower layer (1.25) is under it
    lo, up = layers(shares(rc, mid, surface(wid, "above", "centre"))[0])
    assert (lo == 0).all() and (up == 1).all()
    lo, up = layers(shares(rc, mid, surface(wid, "below", "centre"))[0])
    assert (lo == 1).all() and (up == 0).all()
    # by the share: 1.5 m of 2.5 m of the lower layer are below the surface
    lo, up = layers(shares(rc, mid, surface(wid, "below", "frac"))[0])
    assert np.allclose(lo, 0.6, atol=1e-12) and (up == 0).all()
    lo, up = layers(shares(rc, mid, surface(wid, "above", "frac"))[0])
    assert np.allclose(lo, 0.4, atol=1e-12) and (up == 1).all()
    # «по доле»: a block with 0 < f < 1 passes, «по центру» of the same block does not (№ 65)
    got = restrict.test_rules(rc.project, mid, surface(wid, "above", "frac"))
    assert (got["passed"], got["weight"]) == (24, pytest.approx(12 * 0.4 + 12))
    got = restrict.test_rules(rc.project, mid, surface(wid, "above", "centre"))
    assert (got["passed"], got["weight"]) == (12, 12.0)
    # a surface over the whole model: nothing «above» by the centre and by the share, all «below»
    high = plane(rc, 10.0, "Высоко")
    assert not shares(rc, mid, surface(high, "above", "frac"))[0].any()
    assert (shares(rc, mid, surface(high, "below", "frac"))[0] == 1).all()
    # a centre exactly on the surface is above (and not below: no block is lost between the two sides)
    on = plane(rc, 1.25, "На центре")
    assert (layers(shares(rc, mid, surface(on, "above", "centre"))[0])[0] == 1).all()
    assert (layers(shares(rc, mid, surface(on, "below", "centre"))[0])[0] == 0).all()
    assert np.allclose(layers(shares(rc, mid, surface(on, "below", "frac"))[0])[0], 0.5)


def test_surface_that_slopes_over_a_turned_model(rc) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(rc.project, rc.store)  # turned by 30°, blocks 5 × 5 × 2.5, corner z −300
    m = open_model(rc, mid)
    c = m.centres()
    box = m.boxes()
    height = box[:, 5] - box[:, 2]
    wid = plane(
        rc, -295.0, "Наклон", slope=0.01, span=(900.0, 2300.0)
    )  # z = −295 + 0.01·x, the centres are near x ≈ 1000
    zs = -295.0 + 0.01 * c[:, 0]
    low = c[:, 2] - height / 2
    want = np.clip((zs - low) / height, 0.0, 1.0)
    got = shares(rc, mid, surface(wid, "below", "frac"))[0]
    assert np.allclose(got, want, atol=1e-9) and ((got > 0) & (got < 1)).any() and (got == 1).any()
    assert np.allclose(shares(rc, mid, surface(wid, "above", "frac"))[0], 1.0 - want, atol=1e-9)
    assert np.array_equal(shares(rc, mid, surface(wid, "above", "centre"))[0], (c[:, 2] >= zs).astype(np.float64))


def test_surface_without_a_surface_under_the_block_has_share_0_and_a_warning(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc)
    xyz = np.array([[-5.0, -5.0, 1.0], [7.0, -5.0, 1.0], [7.0, 40.0, 1.0]])  # covers only a part of the plan
    with dialect.write_tx(rc.project.engine) as conn:
        part = wf.put_wireframe(conn, rc.store, xyz, np.array([[0, 1, 2]], dtype=np.int32), name="Часть")
    for side in ("above", "below"):
        for by in ("centre", "frac"):
            f, warnings = shares(rc, mid, surface(part, side, by))
            assert len(warnings) == 1 and "«Часть»" in warnings[0]
            assert (f[[0, 1, 4]] > 0).any() or side == "below"  # the covered columns answer, the others are 0
            assert not f[[3, 7]].any()  # x ≈ 17.5 is off the plan of the triangle
    nan_free = restrict.test_rules(rc.project, mid, surface(part, "above", "centre"))
    assert nan_free["total"] == 24 and nan_free["warnings"]


def test_a_leaf_without_an_object_is_unavailable_with_a_warning(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc)
    for leaf in (surface(None), surface(777_777), wire(None), wire(777_777)):
        f, warnings = shares(rc, mid, leaf)
        assert not f.any() and len(warnings) == 1, leaf
    # «НЕ» of an unavailable leaf passes everything: the leaf itself is 0
    f, _ = shares(rc, mid, {"op": "not", "items": [surface(None)]})
    assert (f == 1).all()


# --- the shares of И / ИЛИ / НЕ -------------------------------------------------------------------------------------


def test_and_is_the_product_or_is_the_maximum_not_is_one_minus(rc) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc)
    wid = plane(rc, 1.5)  # lower layer: below 0.6, above 0.4; upper layer: below 0, above 1
    s_below = surface(wid, "below", "frac")
    s_above = surface(wid, "above", "frac")
    c = cond("AU", ">", 1)
    ones = (AU > 1).astype(np.float64)
    below = np.where(np.arange(24) < 12, 0.6, 0.0)
    above = 1.0 - below
    cases = {
        "and": (group("and", s_below, c), below * ones),
        "and2": (group("and", s_below, s_above), below * above),
        "or": (group("or", s_below, c), np.maximum(below, ones)),
        "or2": (group("or", s_below, s_above), np.maximum(below, above)),
        "not": ({"op": "not", "items": [s_below]}, 1.0 - below),
        "not and": ({"op": "not", "items": [group("and", s_below, c)]}, 1.0 - below * ones),
        "deep": (
            group("or", group("and", s_above, c), {"op": "not", "items": [group("or", s_below, c)]}),
            np.maximum(above * ones, 1.0 - np.maximum(below, ones)),
        ),  # fmt: skip
    }
    for name, (rules, want) in cases.items():
        f, _ = shares(rc, mid, rules)
        assert np.allclose(f, want, atol=1e-12), name
        assert ((f >= 0) & (f <= 1)).all()
    # a count and a weight over the shares: passed — f > 0, weight — Σ f
    got = restrict.test_rules(rc.project, mid, group("and", s_below, c))
    want = below * ones
    assert got["passed"] == int((want > 0).sum()) and got["weight"] == pytest.approx(float(want.sum()))
    # the quick restrictor with a filter: the product (the contract of the display)
    assert restrict.combine(None, None) is None
    both = restrict.combine(c, s_below)
    assert np.allclose(shares(rc, mid, both)[0], below * ones)


def test_the_leaf_of_a_wireframe_is_a_point_of_extension(rc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    mid = model(rc)
    wid = plane(rc, 1.5, "Тело")
    f, warnings = shares(rc, mid, wire(wid))
    assert not f.any() and len(warnings) == 1 and "«Тело»" in warnings[0]  # until 17.20
    seen: list[tuple[Any, str]] = []

    def builder(b: restrict.Build, body: Any, path: str) -> Any:
        seen.append((body, path))
        return lambda chunk: np.full(chunk.n, 0.25)

    monkeypatch.setitem(restrict.LEAVES, "wireframe", builder)
    rules = group("or", group("and", wire(wid, "outside", "centre"), cond("AU", ">", 1)), cond("DOMAIN", "=", 0))
    f, warnings = shares(rc, mid, rules)
    assert warnings == [] and seen and seen[0][1] == "rules.items.0.items.0.wireframe"
    assert seen[0][0]["side"] == "outside" and seen[0][0]["m"] == 4
    want = np.maximum(0.25 * (AU > 1), (DOMAIN == 0).astype(np.float64))
    assert np.allclose(f, want)


# --- chunks, cancel, memory -----------------------------------------------------------------------------------------


def test_the_result_does_not_depend_on_the_size_of_the_chunk_and_cancel_stops(rc) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(rc.project, rc.store, n=350)  # row groups of 100
    wid = plane(rc, -295.0, "Наклон", slope=0.01, span=(900.0, 2300.0))
    rules = group("or", group("and", cond("AU", ">", 1), surface(wid, "below", "frac")), cond("DOM", "=", "дайка"))
    whole, _ = shares(rc, mid, rules)
    assert whole.shape == (350,) and 0 < np.count_nonzero(whole) < 350
    for chunk in (1, 7, 64, 100, 349, 350, 1 << 20):
        assert np.array_equal(shares(rc, mid, rules, chunk=chunk)[0], whole), chunk
    m = open_model(rc, mid)
    with dialect.read_tx(rc.project.engine) as conn:
        assert np.array_equal(restrict.evaluate(m, rules, conn=conn, store=rc.store), whole)
        assert (restrict.evaluate(m, None, conn=conn, store=rc.store) == 1).all()
        r = restrict.prepare(m, rules, conn=conn, store=rc.store)
    sizes = [ch.n for ch, _ in r.iter(m, chunk=64)]
    assert max(sizes) <= 64 and sum(sizes) == 350
    calls = []

    class Stop(Exception):
        pass

    def cancel() -> None:
        calls.append(1)
        if len(calls) == 3:
            raise Stop

    got = []
    with pytest.raises(Stop):
        for ch, _ in r.iter(m, chunk=64, cancel=cancel):
            got.append(ch.n)
    assert got == [64, 64]
    assert restrict.prepare(m, None, conn=None, store=rc.store).mask is not None  # type: ignore[arg-type]
    only = restrict.prepare(m, cond("AU", ">", 1), conn=None, store=rc.store)  # type: ignore[arg-type]
    assert only.fields == ["AU"]
    for ch, f in only.iter(m, extra=["CU"], chunk=100):
        assert set(ch.cols) == {"AU", "CU"} and np.array_equal(only.mask(ch), f > 0)
        break
