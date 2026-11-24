"""Density and categories of a block model (подшаг 17.26; ТЗ 8.7.5; ADR 0023 §9, decision 13, № 68): the kernels of the
density (a constant, a field, a table «domain → density», a formula; a gap is never 0; DENS_SRC), the categories by a
scheme (JORC on a synthetic model to the last digit of a threshold, the template ГКЗ without thresholds is refused, a
field the model lacks is 422, «без оценки — без категории», the majority filter 3×3×3), the reference of the schemes
(`GET/PUT /blocks/schemes`, versions, 409) and the portions of 1, 77 and 2²⁰ rows giving the same bits."""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import APIRouter

from bf_core.api.errors import ApiError
from bf_core.blocks import categories as cat
from bf_core.blocks import density as dens
from bf_core.blocks import restrict, routes_schemes
from bf_core.blocks import schemes as sc
from bf_core.blocks.model import Chunk, Model
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_fields_store import make_model
from tests.test_filters_store import cond

API = "/api/v1"
SCHEMES = API + "/blocks/schemes"
DOM_LABELS = ["руда", "порода", "дайка"]


# --- a model in memory ------------------------------------------------------------------------------------------------


class Fake:
    """What the kernels ask of `Model`: `fields`, `name`, `rows`, `iter`, `p`, `grid.counts`, `sub`."""

    def __init__(self, fields: list[dict[str, Any]], cols: dict[str, np.ndarray], p: np.ndarray | None = None,
                 counts: tuple[int, int, int] = (1000, 1000, 1000)) -> None:  # fmt: skip
        self.fields, self.cols, self.name, self.sub = fields, cols, "Модель", False
        self.rows = len(next(iter(cols.values())))
        self._p = np.arange(self.rows, dtype=np.uint64) if p is None else p
        self.grid = SimpleNamespace(counts=counts)

    def iter(self, names: list[str] = (), chunk: int = 1 << 20) -> Any:  # type: ignore[assignment]
        for a in range(0, self.rows, chunk):
            b = min(a + chunk, self.rows)
            yield Chunk(a, self._p[a:b], None, None, {k: self.cols[k][a:b] for k in names})

    def p(self) -> np.ndarray:
        return self._p


def num(name: str, unit: str | None = None) -> dict[str, Any]:
    return {"name": name, "kind": "number", "dtype": "f8", "unit": unit}


def catf(name: str, labels: list[str]) -> dict[str, Any]:
    return {"name": name, "kind": "category", "dtype": "cat", "unit": None, "labels": labels}


class Inside:
    """A restrictor of tests: the blocks of `mask` pass."""

    def __init__(self, mask: np.ndarray, fields: list[str] = ()) -> None:  # type: ignore[assignment]
        self.mask, self.fields = mask, list(fields)

    def weights(self, chunk: Chunk) -> np.ndarray:
        return self.mask[chunk.start : chunk.start + chunk.n].astype(np.float64)


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b, equal_nan=True))


def run_density(model: Any, body: dict[str, Any], restrictor: Any = None, chunk: int = 1 << 20):  # type: ignore[no-untyped-def]
    plan = dens.prepare(model, dens.parse(body))
    parts = list(dens.iter_density(model, plan, restrictor, chunk=chunk))
    d = np.concatenate([x.density for x in parts]) if parts else np.empty(0)
    s = np.concatenate([x.source for x in parts]) if parts else np.empty(0)
    return plan, d, s


def refused(call: Any, *args: Any, **kw: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        call(*args, **kw)
    assert e.value.message and e.value.hint  # in Russian, with a hint
    return e.value


def domains(n: int = 12) -> Fake:
    code = np.array([0, 1, 2, -1, 0, 1, 2, -1, 0, 0, 1, 2][:n], dtype=np.int32)
    au = np.array([3.0, np.nan, 2.5, 2.7, 0.0, -1.0, 4.0, 2.0, np.inf, 2.9, 3.1, 1.0])[:n]
    return Fake([catf("DOM", DOM_LABELS), num("SG", "г/см³"), num("N")],
                {"DOM": code, "SG": au, "N": np.array([1, 2, 2, np.nan, 1, 3, 2, 1, 1, 2, 3, 2.0])[:n]})  # fmt: skip


# --- density: the constant, the table, the field, the formula ---------------------------------------------------------


def test_the_constant_density_is_the_same_everywhere_and_its_own() -> None:
    plan, d, s = run_density(domains(), {"method": "constant", "value": "2,6"})
    assert (d == 2.6).all() and (s == 0.0).all() and plan.unit == "т/м³"
    assert (plan.tally.rows, plan.tally.passed, plan.tally.defaulted, plan.tally.empty) == (12, 12, 0, 0)
    assert plan.spec.params()["value"] == 2.6 and plan.spec.out == "DENS" and plan.spec.src_field == "DENS_SRC"


def test_the_table_by_a_category_domain_defaults_what_it_does_not_know() -> None:
    body = {
        "method": "table",
        "default": 2.5,
        "table": {
            "field": "DOM",
            "values": [
                {"value": "руда", "density": 3.1},
                {"value": "ДАЙКА", "density": "2,9"},
                {"value": "нет такой", "density": 1},
            ],
        },
    }
    plan, d, s = run_density(domains(), body)
    code = domains().cols["DOM"]
    # руда 3.1, дайка 2.9 (case aside); порода is not in the table and the empty domain has none: the default, source 1
    assert d.tolist() == [3.1, 2.5, 2.9, 2.5, 3.1, 2.5, 2.9, 2.5, 3.1, 3.1, 2.5, 2.9]
    assert s.tolist() == [0.0 if c in (0, 2) else 1.0 for c in code]
    assert (plan.tally.defaulted, plan.tally.empty) == (5, 0)
    assert any("нет такой" in w for w in plan.warnings)  # a domain of the table that the model never has
    body["table"]["default"] = 2.0  # the default of the table is closer than the general one
    assert set(run_density(domains(), body)[1][[1, 3]]) == {2.0}


def test_no_default_leaves_the_gap_a_gap_never_zero() -> None:
    body = {"method": "table", "table": {"field": "DOM", "values": [{"value": "руда", "density": 3.1}]}}
    plan, d, s = run_density(domains(), body)
    code = domains().cols["DOM"]
    assert np.isnan(d[code != 0]).all() and (d[code == 0] == 3.1).all() and not (d == 0).any()
    assert (s[code != 0] == 1.0).all() and (plan.tally.empty, plan.tally.defaulted) == (8, 0)
    assert any("по умолчанию" in w for w in plan.warnings)


def test_the_table_by_a_number_domain_matches_exactly() -> None:
    body = {
        "method": "table",
        "default": 2.4,
        "table": {
            "field": "N",
            "values": [{"value": 1, "density": 2.1}, {"value": "2", "density": 2.2}, {"value": 3.0, "density": 2.3}],
        },
    }
    _, d, s = run_density(domains(), body)
    n = domains().cols["N"]
    assert d[n == 1].tolist() == [2.1] * 4 and d[n == 2].tolist() == [2.2] * 5 and d[n == 3].tolist() == [2.3] * 2
    assert d[np.isnan(n)].tolist() == [2.4] and s[np.isnan(n)].tolist() == [1.0]
    text = refused(
        dens.prepare,
        domains(),
        dens.parse({**body, "table": {"field": "N", "values": [{"value": "руда", "density": 2}]}}),
    )
    assert (text.code, text.extra["param"]) == ("bad_param", "table.values.0.value")


def test_a_field_as_the_density_copies_values_and_defaults_the_gaps() -> None:
    m = domains(8)
    inside = np.ones(8, dtype=bool)
    inside[[4, 5]] = False  # SG is 0.0 and −1.0 there: not a density
    body = {"method": "field", "field": "sg", "default": 2.65}
    plan, d, s = run_density(m, body, Inside(inside))
    assert plan.unit == "г/см³"  # the label of the unit of the field, the values are not converted
    assert d[2] == 2.5 and d[3] == 2.7 and d[1] == 2.65 and s[1] == 1.0 and s[2] == 0.0  # NaN → the default
    assert np.isnan(d[[4, 5]]).all() and np.isnan(s[[4, 5]]).all() and d[0] == 3.0  # outside: empty, not 0
    bad = refused(run_density, m, body)  # the same blocks inside the restrictor
    assert bad.code == "density_invalid" and bad.extra["count"] == 2 and bad.extra["row"] == 4  # 0.0 and −1.0
    inf = refused(run_density, domains(12), {"method": "field", "field": "SG"}, Inside(np.arange(12) >= 8))
    assert (inf.code, inf.extra["count"], inf.extra["row"]) == ("density_invalid", 1, 8)  # +inf is not a density


def test_a_formula_gives_the_density_and_nan_takes_the_default() -> None:
    body = {"method": "formula", "expr": "IF(DOM = 'руда', 3.1, 2.7) * N / N", "default": 2.0}
    _, d, s = run_density(domains(), body)
    code, n = domains().cols["DOM"], domains().cols["N"]
    assert np.allclose(d[(code == 0)], 3.1) and np.allclose(d[(code == 1)], 2.7)
    gap = np.isnan(n) | (code < 0)  # an empty domain in the condition is a gap of the result (17.6), as an empty N
    assert (s[gap] == 1.0).all() and (d[gap] == 2.0).all() and (s[~gap] == 0.0).all() and gap.sum() == 2
    e = refused(dens.prepare, domains(), dens.parse({"method": "formula", "expr": "DOM"}))
    assert e.code == "expr_type" and e.extra["param"] == "expr"
    e = refused(dens.prepare, domains(), dens.parse({"method": "formula", "expr": "1 + AUX"}))
    assert (e.code, e.extra["pos"], e.extra["field"]) == ("expr_field", 4, "AUX")
    e = refused(dens.prepare, domains(), dens.parse({"method": "formula", "expr": "N >"}))
    assert e.code == "expr_syntax"
    assert refused(dens.prepare, domains(), dens.parse({"method": "field", "field": "DOM"})).extra["param"] == "field"
    assert (
        refused(dens.prepare, domains(), dens.parse({"method": "field", "field": "NO"})).code == "density_field_missing"
    )


def test_the_body_of_the_density_is_checked() -> None:
    def table(*rows: dict[str, Any]) -> dict[str, Any]:
        return {"method": "table", "table": {"field": "DOM", "values": list(rows)}}

    const = {"method": "constant", "value": 2.7}
    for body, param in [
        ({"method": "magic"}, "method"),
        ({"method": "constant"}, "value"),
        ({"method": "constant", "value": 0}, "value"),
        ({"method": "constant", "value": "abc"}, "value"),
        ({**const, "default": -1}, "default"),
        ({**const, "out": "A", "src_field": "a"}, "src_field"),
        ({**const, "out": "idx"}, "out"),
        ({"method": "field"}, "field"),
        ({"method": "formula", "expr": " "}, "expr"),
        ({"method": "table"}, "table"),
        (table(), "table.values"),
        (table({"value": "a", "density": 0}), "table.values.0.density"),
        (table({"value": "a", "density": 1}, {"value": "A", "density": 2}), "table.values.1.value"),
    ]:
        e = refused(dens.parse, body)
        assert (e.status, e.code, e.extra["param"]) == (422, "bad_param", param), body


# --- categories: JORC to the last digit -------------------------------------------------------------------------------

JORC = sc.defaults()["jorc"]
ME, IND, INF = "Измеренные", "Выявленные", "Предполагаемые"
# (PASS, SLOPE, NH, AD, estimate, the label expected)
EDGES = [
    (1, 0.8, 3, 25, 1.0, ME),
    (1, 0.8, 3, 25.000000001, 1.0, IND),
    (1, 0.7999999999999999, 3, 25, 1.0, IND),
    (1, 0.8, 2, 25, 1.0, IND),
    (1, 0.8, 3, 24.9, 1.0, ME),
    (2, 0.8, 3, 25, 1.0, IND),
    (2, 0.5, 2, 99, 1.0, IND),
    (2, 0.4999999999999999, 2, 0, 1.0, INF),
    (2, 0.5, 1, 0, 1.0, INF),
    (3, 1.0, 9, 1, 1.0, INF),
    (np.nan, 1.0, 9, 1, 1.0, INF),  # an estimate, no PASS: the third rule (a gap is not 1)
    (1, 0.9, 5, 5, np.nan, None),  # no estimate: no category, whatever the rules say
    (1, 0.9, 5, 5, 0.0, ME),  # an estimate of 0 is an estimate
]


def jorc_model(rows: list[tuple[Any, ...]] | None = None, n: int = 0, seed: int = 3) -> Fake:
    if rows is None:
        rng = np.random.default_rng(seed)
        rows = [(rng.choice([1, 2, 3, np.nan]), rng.choice([0.4, 0.5, 0.79, 0.8, 0.95]), rng.integers(0, 6),
                 rng.choice([10.0, 25.0, 26.0]), rng.choice([1.0, np.nan]), None) for _ in range(n)]  # fmt: skip
    cols = {
        k: np.array([r[i] for r in rows], dtype=np.float64) for i, k in enumerate(["PASS", "SLOPE", "NH", "AD", "EST"])
    }
    return Fake([num(k) for k in cols], cols)


def python_jorc(p: float, s: float, nh: float, ad: float, est: float) -> str | None:
    """The rules of 8.7.5 written by hand: a gap makes a comparison false."""
    if est != est:
        return None
    if p == 1 and s >= 0.8 and nh >= 3 and ad <= 25:
        return ME
    if p <= 2 and s >= 0.5 and nh >= 2:
        return IND
    return INF


def classify(model: Any, body: dict[str, Any], restrictor: Any = None, chunk: int = 1 << 20) -> cat.Result:
    spec = cat.parse(body, lambda sid: JORC if sid == "jorc" else sc.defaults()[sid])
    return cat.classify(model, cat.prepare(model, spec), restrictor, chunk=chunk)


def labels_of(res: cat.Result) -> list[str | None]:
    return [None if c < 0 else res.labels[c] for c in res.codes.tolist()]


def test_jorc_on_the_edges_of_every_threshold() -> None:
    m = jorc_model(EDGES)
    res = classify(m, {"scheme": "jorc", "estimate": "EST"})
    assert labels_of(res) == [r[5] for r in EDGES]
    assert res.labels == [ME, IND, INF]
    want = [r[5] for r in EDGES]
    assert res.counts == [want.count(ME), want.count(IND), want.count(INF)]
    assert (res.passed, res.no_estimate, res.unclassified) == (13, 1, 0)
    assert res.codes.dtype == np.int16 and next(res.iter_codes()).dtype == np.int32


def test_jorc_on_random_blocks_equals_the_rules_written_by_hand() -> None:
    m = jorc_model(n=4000)
    res = classify(m, {"scheme": "jorc", "estimate": "EST"})
    c = m.cols
    want = [python_jorc(*(float(c[k][i]) for k in ["PASS", "SLOPE", "NH", "AD", "EST"])) for i in range(m.rows)]
    assert labels_of(res) == want and {None, ME, IND, INF} <= set(want)


def test_the_restrictor_and_no_estimate_leave_a_block_without_a_category() -> None:
    m = jorc_model(EDGES)
    inside = np.ones(len(EDGES), dtype=bool)
    inside[[0, 3]] = False
    res = classify(m, {"scheme": "jorc", "estimate": "EST"}, Inside(inside))
    want = [None if not inside[i] else r[5] for i, r in enumerate(EDGES)]
    assert labels_of(res) == want and (res.passed, res.no_estimate) == (11, 1)
    plain = classify(m, {"scheme": "jorc"})  # no estimate field named: every block has one
    assert labels_of(plain)[11] == ME and plain.no_estimate == 0
    own = classify(m, {"rules": [{"label": "A", "expr": "PASS = 1"}], "estimate": "EST"})
    assert own.unclassified == 13 - 1 - own.counts[0] and own.unclassified > 0  # an estimate, no rule fulfilled


def test_a_number_is_a_condition_and_the_first_rule_wins() -> None:
    m = jorc_model(EDGES)
    res = classify(m, {"rules": [{"label": "один", "expr": "PASS"}, {"label": "два", "expr": "NH >= 0"}]})
    assert labels_of(res)[:3] == ["один"] * 3 and labels_of(res)[10] == "два"  # PASS NaN is false, not an error


def test_the_template_gkz_has_no_thresholds_and_is_refused() -> None:
    gkz = sc.defaults()["gkz"]
    assert [r["label"] for r in gkz["rules"]] == ["A", "B", "C1", "C2"] and all(r["expr"] is None for r in gkz["rules"])
    e = refused(cat.parse, {"scheme": "gkz"}, lambda sid: gkz)
    assert (e.status, e.code, e.extra["param"], e.extra["label"]) == (422, "scheme_incomplete", "rules.0.expr", "A")
    e = refused(cat.parse, {"rules": [{"label": "A", "expr": "PASS = 1"}, {"label": "B"}]})
    assert (e.code, e.extra["param"]) == ("scheme_incomplete", "rules.1.expr")


def test_a_field_the_scheme_needs_but_the_model_lacks_is_422_not_a_quiet_skip() -> None:
    m = Fake([num("PASS"), num("AU")], {"PASS": np.ones(3), "AU": np.ones(3)})
    spec = cat.parse({"scheme": "jorc"}, lambda sid: JORC)
    e = refused(cat.prepare, m, spec)
    assert (e.status, e.code, e.extra["field"], e.extra["label"], e.extra["param"]) == (
        422, "classify_field_missing", "SLOPE", ME, "rules.0.expr")  # fmt: skip
    assert e.extra["pos"] == 13 and "SLOPE" in e.message and "Измеренные" in e.message
    spec = cat.parse({"rules": [{"label": "A", "expr": "PASS = 1"}], "estimate": "AU_OK"})
    assert refused(cat.prepare, m, spec).extra["param"] == "estimate"
    e = refused(cat.prepare, m, cat.parse({"rules": [{"label": "A", "expr": "IF(PASS = 1, 'a', 'b')"}]}))
    assert e.code == "expr_type" and e.extra["param"] == "rules.0.expr"
    e = refused(cat.parse, {"rules": [{"label": "A", "expr": "PASS >"}]})  # the syntax is checked at the body
    assert e.code == "expr_syntax" and e.extra["param"] == "rules.0.expr"


def test_the_body_of_the_classification_is_checked() -> None:
    rules = [{"label": "A", "expr": "PASS = 1"}, {"label": "B", "expr": "PASS = 2"}]
    for body, param in [
        ({}, "rules"), ({"rules": rules, "scheme": "jorc"}, "rules"), ({"rules": rules, "majority": 11}, "majority"),
        ({"rules": rules, "majority": -1}, "majority"), ({"rules": rules, "majority": True}, "majority"),
        ({"rules": rules, "estimate": " "}, "estimate"), ({"rules": rules, "out": "idx"}, "out"),
        ({"rules": rules + [{"label": "a", "expr": "1"}]}, "rules.2.label"),
        ({"rules": [{"label": " ", "expr": "1"}]}, "rules.0.label"),
        ({"rules": rules, "overrides": [{"wireframe_id": 3, "label": "Z"}]}, "overrides.0.label"),
        ({"rules": rules, "overrides": [{"wireframe_id": 0, "label": "A"}]}, "overrides.0.wireframe_id"),
        ({"rules": [{"label": "A", "expr": "PASS = ="}]}, "rules.0.expr"),
    ]:  # fmt: skip
        e = refused(cat.parse, body)
        assert e.status == 422 and e.extra["param"] == param, (body, e.code)
    spec = cat.parse({"rules": rules, "overrides": [{"wireframe_id": 7, "label": "b"}], "majority": 2})
    assert (
        spec.overrides == ((7, "B"),)
        and spec.params()["majority"] == 2
        and spec.params()["rules"][0]["expr"] == "PASS = 1"
    )


def test_a_wireframe_of_a_category_overrides_the_rule_only_for_a_block_with_an_estimate() -> None:
    m = jorc_model(EDGES)
    n = len(EDGES)
    in_a = np.zeros(n, dtype=bool)
    in_a[[1, 7, 11]] = True  # 11 has no estimate
    in_b = np.zeros(n, dtype=bool)
    in_b[[7, 8]] = True
    body = {
        "scheme": "jorc",
        "estimate": "EST",
        "overrides": [{"wireframe_id": 1, "label": INF}, {"wireframe_id": 2, "label": ME}],
    }
    spec = cat.parse(body, lambda sid: JORC)
    plan = cat.prepare(
        m, spec, [lambda ch: in_a[ch.start : ch.start + ch.n] * 1.0, lambda ch: in_b[ch.start : ch.start + ch.n] * 1.0]
    )
    got = labels_of(cat.classify(m, plan))
    want = [r[5] for r in EDGES]
    want[1], want[7], want[8] = INF, INF, ME  # the first of the list holds block 7; 11 stays without a category
    assert got == want
    with pytest.raises(ValueError):
        cat.prepare(m, spec, [])


# --- the majority filter ----------------------------------------------------------------------------------------------


def brute(codes: np.ndarray, ids: np.ndarray, counts: tuple[int, int, int], k: int, times: int) -> np.ndarray:
    nx, ny, nz = counts
    at = {int(p): int(c) for p, c in zip(ids, codes, strict=True)}
    for _ in range(times):
        new = {}
        for pid, own in at.items():
            i, j, kk = pid % nx, (pid // nx) % ny, pid // (nx * ny)
            votes = [0] * k
            for di, dj, dk in itertools.product((-1, 0, 1), repeat=3):
                a, b, c = i + di, j + dj, kk + dk
                if 0 <= a < nx and 0 <= b < ny and 0 <= c < nz and at.get(a + nx * (b + ny * c), -1) >= 0:
                    votes[at[a + nx * (b + ny * c)]] += 1
            best = max(votes)
            new[pid] = -1 if own < 0 else own if votes[own] == best else votes.index(best)
        at = new
    return np.array([at[int(p)] for p in ids], dtype=np.int16)


@pytest.mark.parametrize("dense", [True, False])
def test_the_majority_filter_equals_the_brute_force(monkeypatch: pytest.MonkeyPatch, dense: bool) -> None:
    if not dense:
        monkeypatch.setattr(cat, "DENSE_MAX", 0)  # the search in the sorted parents instead of the table
    counts = (7, 6, 5)
    rng = np.random.default_rng(11)
    ids = np.sort(rng.choice(7 * 6 * 5, size=150, replace=False)).astype(np.uint64)
    codes = rng.integers(-1, 3, size=150).astype(np.int16)
    for times in (1, 2, 3):
        got = cat.majority(codes, ids, counts, 3, times)
        assert same(got, brute(codes, ids, counts, 3, times)), times
        assert ((got < 0) == (codes < 0)).all()  # a block without a category never gets one
    assert cat.majority(codes, ids, counts, 3, 0) is codes


def test_the_filter_smooths_a_lone_block_and_a_tie_keeps_the_own_category() -> None:
    counts = (3, 3, 3)
    ids = np.arange(27, dtype=np.uint64)
    codes = np.zeros(27, dtype=np.int16)
    codes[13] = 1  # the centre, alone among 26
    assert cat.majority(codes, ids, counts, 2, 1).tolist() == [0] * 27
    pair = cat.majority(np.array([0, 1], dtype=np.int16), np.array([0, 1], dtype=np.uint64), (2, 1, 1), 2, 3)
    assert pair.tolist() == [0, 1]  # one against one: nothing changes


def test_the_filter_on_sub_blocks_takes_the_mode_by_volume() -> None:
    # parent 0: rows of class 1 (volume 3) and 0 (volume 1) → 1; parent 1: rows of 0 only; parent 2: no category
    p = np.array([0, 0, 0, 1, 2], dtype=np.uint64)
    codes = np.array([1, 1, 0, 0, -1], dtype=np.int16)
    volume = np.array([1.0, 2.0, 1.0, 4.0, 1.0])
    got = cat.majority(codes, p, (3, 1, 1), 2, 1, volume)
    # parent 0 (class 1) and parent 1 (class 0) are a tie of one against one in both: they keep their own
    assert got.tolist() == [1, 1, 1, 0, -1]
    with pytest.raises(ValueError):
        cat.majority(codes, p, (3, 1, 1), 2, 1)


def test_the_filter_is_a_step_of_the_classification() -> None:
    counts = (6, 5, 4)
    rng = np.random.default_rng(5)
    n = 6 * 5 * 4
    x = rng.random(n)
    m = Fake([num("X")], {"X": x}, counts=counts)
    body = {"rules": [{"label": "низ", "expr": "X < 0.35"}, {"label": "верх", "expr": "X >= 0.35"}]}
    raw = classify(m, body)
    smooth = classify(m, {**body, "majority": 2})
    assert same(smooth.codes, brute(raw.codes, m.p(), counts, 2, 2)) and not same(smooth.codes, raw.codes)
    assert sum(smooth.counts) == n and smooth.counts != raw.counts


# --- the portions of any size give the same bits ----------------------------------------------------------------------


def synthetic(n: int, seed: int = 21) -> Fake:
    rng = np.random.default_rng(seed)
    code = rng.integers(-1, 3, n).astype(np.int32)
    sg = np.where(rng.random(n) < 0.2, np.nan, 2.0 + rng.random(n))
    est = np.where(rng.random(n) < 0.1, np.nan, rng.random(n))
    pas = rng.integers(1, 4, n).astype(np.float64)
    slope = np.round(rng.random(n), 2)
    nh, ad = rng.integers(0, 6, n).astype(np.float64), rng.integers(10, 30, n).astype(np.float64)
    return Fake([catf("DOM", DOM_LABELS), num("SG"), num("EST"), num("PASS"), num("SLOPE"), num("NH"), num("AD")],
                {"DOM": code, "SG": sg, "EST": est, "PASS": pas, "SLOPE": slope, "NH": nh, "AD": ad})  # fmt: skip


DENS_BODIES = [
    {"method": "field", "field": "SG", "default": 2.6},
    {"method": "table", "default": 2.5, "table": {"field": "DOM", "values": [{"value": "руда", "density": 3.1}]}},
    {"method": "formula", "expr": "SG * 1.001 + EST", "default": 2.0},
]


@pytest.mark.parametrize(("n", "chunks"), [(3000, (1, 77, 1 << 20)), (1 << 20 | 12345, (77777, 1 << 20, 1 << 21))])
def test_portions_of_any_size_give_the_same_bits(n: int, chunks: tuple[int, ...]) -> None:
    m = synthetic(n)
    inside = np.random.default_rng(2).random(n) < 0.8
    for body in DENS_BODIES:
        runs = [run_density(m, body, Inside(inside), chunk=c) for c in chunks]
        for plan, d, s in runs[1:]:
            assert same(d, runs[0][1]) and same(s, runs[0][2]) and vars(plan.tally) == vars(runs[0][0].tally)
        assert np.isnan(runs[0][1][~inside]).all() and not (runs[0][1][inside & ~np.isnan(runs[0][1])] == 0).any()
    jorc = {"scheme": "jorc", "estimate": "EST", "majority": 0}
    first = None
    for c in chunks:
        res = classify(m, jorc, Inside(inside), chunk=c)
        first = first or res
        assert same(res.codes, first.codes) and res.counts == first.counts
        assert (res.passed, res.no_estimate, res.unclassified) == (first.passed, first.no_estimate, first.unclassified)
    assert (first.codes >= 0).sum() > 0 and (first.codes[~inside] < 0).all()
    assert sum(len(x) for x in first.iter_codes(1000)) == n


# --- on a real model of the project -----------------------------------------------------------------------------------


@pytest.fixture()
def pc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Плотность"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    pclient.model_id = make_model(pclient.project, pclient.store)
    return pclient


def test_a_model_of_the_project_with_a_restrictor_of_the_library(pc) -> None:  # type: ignore[no-untyped-def]
    with dialect.read_tx(pc.project.engine) as conn:
        model = Model.open(conn, pc.store, pc.model_id)
        r = restrict.prepare(model, cond("AU", ">", 1), conn=conn, store=pc.store)
    au = model.columns(["AU"])["AU"]
    passes = au > 1
    spec = dens.parse({"method": "table", "default": 2.5, "table": {"field": "DOM", "values": [
        {"value": "руда", "density": 3.1}, {"value": "порода", "density": 2.7}]}})  # fmt: skip
    plan = dens.prepare(model, spec)
    parts = list(dens.iter_density(model, plan, r, chunk=77))
    d = np.concatenate([x.density for x in parts])
    dom = model.columns(["DOM"])["DOM"]
    want = np.where(dom == 0, 3.1, np.where(dom == 1, 2.7, 2.5))
    assert same(d, np.where(passes, want, np.nan)) and plan.tally.passed == int(passes.sum())
    rules = [{"label": "богатые", "expr": "AU > 3"}, {"label": "рядовые", "expr": "AU > 1"}]
    plan2 = cat.prepare(model, cat.parse({"rules": rules, "estimate": "AU"}))
    res = cat.classify(model, plan2, r, chunk=100)
    assert res.counts == [int((au > 3).sum()), int(((au > 1) & ~(au > 3)).sum())] and res.no_estimate == 0
    assert (res.codes[~passes] < 0).all() and res.unclassified == 0


# --- the reference of the schemes -------------------------------------------------------------------------------------


@pytest.fixture()
def sch(pclient):  # type: ignore[no-untyped-def]
    """A core with an open project and the router of the schemes in front of `/blocks/{bm_id}` (until
    `blocks/router.py` includes it, in that order)."""
    routes = pclient.app.router.routes
    before = list(routes)
    mount = APIRouter(prefix="/blocks")
    mount.include_router(routes_schemes.router)
    pclient.app.include_router(mount, prefix=API)
    routes[:] = [*(r for r in routes if r not in before), *before]
    assert pclient.post(f"{API}/projects", json={"name": "Схемы"}).status_code == 200
    return pclient


def test_a_project_without_schemes_has_jorc_and_the_template_gkz(sch) -> None:  # type: ignore[no-untyped-def]
    r = sch.get(SCHEMES)
    assert r.status_code == 200
    got = r.json()["schemes"]
    assert [s["id"] for s in got] == ["jorc", "gkz"] and [s["version"] for s in got] == [1, 1]
    assert [x["label"] for x in got[0]["rules"]] == [ME, IND, INF]
    assert got[0]["rules"][0]["expr"] == "PASS = 1 AND SLOPE >= 0.8 AND NH >= 3 AND AD <= 25"
    assert got[0]["rules"][1]["expr"] == "PASS <= 2 AND SLOPE >= 0.5 AND NH >= 2"
    assert [x["label"] for x in got[1]["rules"]] == ["A", "B", "C1", "C2"] and all(
        x["expr"] is None for x in got[1]["rules"]
    )
    assert got[0]["name"] == "JORC" and got[1]["name"] == "ГКЗ"


def test_put_makes_versions_and_a_stale_version_is_409(sch) -> None:  # type: ignore[no-untyped-def]
    rules = [{"label": "Руда", "expr": "AU > 1"}, {"label": "Бедная", "expr": "AU > 0.3"}]
    made = sch.put(f"{SCHEMES}/my-scheme", json={"name": "Моя схема", "rules": rules})
    assert made.status_code == 200 and made.json() == {
        "id": "my-scheme",
        "name": "Моя схема",
        "version": 1,
        "rules": rules,
    }
    again = sch.put(f"{SCHEMES}/my-scheme", json={"name": "Моя схема", "rules": rules})  # exists: a version is needed
    assert (again.status_code, again.json()["code"], again.json()["version"]) == (409, "version_conflict", 1)
    rules2 = [*rules, {"label": "Прочая", "expr": None}]
    ok = sch.put(f"{SCHEMES}/my-scheme", json={"expected_version": 1, "name": "Моя схема 2", "rules": rules2})
    assert ok.status_code == 200 and ok.json()["version"] == 2 and ok.json()["rules"][2]["expr"] is None
    stale = sch.put(f"{SCHEMES}/my-scheme", json={"expected_version": 1, "name": "x", "rules": rules})
    assert (stale.status_code, stale.json()["version"]) == (409, 2) and stale.json()["message"] and stale.json()["hint"]
    new_with_version = sch.put(f"{SCHEMES}/other", json={"expected_version": 4, "name": "x", "rules": rules})
    assert (new_with_version.status_code, new_with_version.json()["version"]) == (409, 0)  # nothing to be at version 4
    got = sch.get(SCHEMES).json()["schemes"]
    assert (
        [s["id"] for s in got] == ["jorc", "gkz", "my-scheme"]
        and got[2]["version"] == 2
        and got[2]["name"] == "Моя схема 2"
    )


def test_a_default_scheme_can_be_rewritten_and_the_other_stays(sch) -> None:  # type: ignore[no-untyped-def]
    thresholds = [
        {"label": "A", "expr": "PASS = 1"},
        {"label": "B", "expr": "PASS = 2"},
        {"label": "C1", "expr": "PASS = 3"},
    ]
    r = sch.put(f"{SCHEMES}/gkz", json={"expected_version": 1, "name": "ГКЗ РФ", "rules": thresholds})
    assert r.status_code == 200 and r.json()["version"] == 2
    got = {s["id"]: s for s in sch.get(SCHEMES).json()["schemes"]}
    assert got["gkz"]["rules"] == thresholds and got["jorc"]["version"] == 1 and got["jorc"]["rules"][0]["expr"]
    assert (
        sch.put(f"{SCHEMES}/jorc", json={"name": "JORC", "rules": thresholds}).json()["version"] == 1
    )  # needs the version
    with dialect.read_tx(projects_of(sch.core).require_current().engine) as conn:
        assert sc.get(conn, "gkz")["name"] == "ГКЗ РФ"
        assert refused(sc.get, conn, "nope").status == 404
    spec = cat.parse({"scheme": "gkz"}, lambda sid: got[sid])  # a filled template classifies
    assert spec.labels == ["A", "B", "C1"] and spec.scheme == "gkz"


@pytest.mark.parametrize(
    ("body", "param", "code"),
    [
        ({"rules": [{"label": "A", "expr": "1"}]}, "name", "bad_param"),
        ({"name": "x", "rules": []}, "rules", "bad_param"),
        (
            {"name": "x", "rules": [{"label": "A", "expr": "1"}, {"label": " a ", "expr": None}]},
            "rules.1.label",
            "bad_param",
        ),
        ({"name": "x", "rules": [{"label": "", "expr": "1"}]}, "rules.0.label", "bad_param"),
        ({"name": "x", "rules": [{"label": "A", "expr": "PASS = 1 AND ("}]}, "rules.0.expr", "expr_syntax"),
        ({"name": "x", "rules": [{"label": "A", "expr": 5}]}, "rules.0.expr", "bad_param"),
        (
            {"name": "x", "rules": [{"label": "A", "expr": "1"}], "expected_version": "1"},
            "expected_version",
            "bad_param",
        ),
    ],
)
def test_a_wrong_scheme_is_422_with_the_place(sch, body: dict[str, Any], param: str, code: str) -> None:  # type: ignore[no-untyped-def]
    r = sch.put(f"{SCHEMES}/s1", json=body)
    assert r.status_code == 422 and r.json()["code"] == code and r.json()["param"] == param
    assert r.json()["message"] and r.json()["hint"]
    assert (
        sch.put(f"{SCHEMES}/bad id!", json={"name": "x", "rules": [{"label": "A", "expr": None}]}).json()["param"]
        == "id"
    )
    assert [s["id"] for s in sch.get(SCHEMES).json()["schemes"]] == ["jorc", "gkz"]  # nothing was written


def test_a_read_only_project_refuses_to_write_schemes(sch) -> None:  # type: ignore[no-untyped-def]
    svc = projects_of(sch.core)
    project = svc.require_current()
    project.read_only = True
    try:
        r = sch.put(f"{SCHEMES}/s2", json={"name": "x", "rules": [{"label": "A", "expr": None}]})
        assert (r.status_code, r.json()["code"]) == (423, "project_read_only")
        assert sch.get(SCHEMES).status_code == 200  # reading works
    finally:
        project.read_only = False
