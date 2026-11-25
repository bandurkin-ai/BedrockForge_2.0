"""The prefix of the fields of an estimate in the classification (подшаг 18.18; ТЗ 8.7.5; question № 78): JORC on fields
`EST_PASS`, `EST_SLOPE`, … gives the same categories as JORC on the fields without a prefix; no prefix is the old
behaviour; only the fields of an estimate are renamed; the body is checked; `params` keeps the prefix."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import categories as cat
from bf_core.blocks import schemes as sc
from tests.test_blocks_density_categories import EDGES, JORC, Fake, jorc_model, labels_of, num

PREFIX = "EST_"


def prefixed_model(plain: Fake, prefix: str = PREFIX, keep: tuple[str, ...] = ()) -> Fake:
    cols = {(k if k in keep else prefix + k): v for k, v in plain.cols.items()}
    return Fake([num(k) for k in cols], cols)


def run(model: Fake, body: dict[str, Any]) -> cat.Result:
    spec = cat.parse(body, lambda sid: JORC)
    return cat.classify(model, cat.prepare(model, spec))


def test_jorc_with_a_prefix_gives_the_same_categories_as_without() -> None:
    for plain in (jorc_model(EDGES), jorc_model(n=3000)):
        want = run(plain, {"scheme": "jorc", "estimate": "EST"})
        got = run(prefixed_model(plain), {"scheme": "jorc", "estimate": PREFIX + "EST", "prefix": PREFIX})
        assert np.array_equal(got.codes, want.codes) and got.labels == want.labels
        assert (got.passed, got.no_estimate, got.unclassified, got.counts) == (
            want.passed, want.no_estimate, want.unclassified, want.counts)  # fmt: skip
        assert labels_of(got) == labels_of(want)


def test_no_prefix_is_the_old_behaviour() -> None:
    m = jorc_model(EDGES)
    base = run(m, {"scheme": "jorc"})
    for prefix in ("", None, "  "):
        body = {"scheme": "jorc", "prefix": prefix}
        assert np.array_equal(run(m, body).codes, base.codes)
    spec = cat.parse({"scheme": "jorc"}, lambda sid: JORC)
    assert spec.prefix == "" and spec.params()["prefix"] == ""


def test_a_field_that_is_not_of_an_estimate_stays_as_it_is() -> None:
    plain = jorc_model(EDGES)
    cols = {**plain.cols, "AU": np.arange(plain.rows, dtype=np.float64)}
    named = {(k if k == "AU" else PREFIX + k): v for k, v in cols.items()}
    m = Fake([num(k) for k in named], named)
    rules = [{"label": "A", "expr": "pass = 1 AND [AU] >= 0"}, {"label": "B", "expr": "nh >= 0 OR AU > 99"}]
    res = run(m, {"rules": rules, "prefix": PREFIX})
    ref = run(Fake([num(k) for k in cols], cols), {"rules": rules})
    assert np.array_equal(res.codes, ref.codes)


def test_a_model_without_the_prefixed_field_is_422_with_the_prefixed_name_and_the_place_of_the_text() -> None:
    m = prefixed_model(jorc_model(EDGES), keep=("SLOPE",))  # SLOPE has no prefix: EST_SLOPE is missing
    spec = cat.parse({"scheme": "jorc", "prefix": PREFIX}, lambda sid: JORC)
    with pytest.raises(ApiError) as ei:
        cat.prepare(m, spec)
    e = ei.value
    assert (e.status, e.code, e.extra["field"], e.extra["param"], e.extra["pos"]) == (
        422, "classify_field_missing", "EST_SLOPE", "rules.0.expr", 13)  # fmt: skip
    assert "EST_SLOPE" in e.message


def test_the_prefix_is_kept_for_the_rebuild_and_checked() -> None:
    spec = cat.parse({"scheme": "jorc", "prefix": " AU_OK_ "}, lambda sid: JORC)
    assert spec.prefix == "AU_OK_" and cat.parse({**spec.params(), "scheme": None}).prefix == "AU_OK_"
    for bad in ("AU OK", "АУ_", "x" * 41, "a-b", 5):
        with pytest.raises(ApiError) as ei:
            cat.parse({"scheme": "jorc", "prefix": bad}, lambda sid: JORC)
        assert (ei.value.status, ei.value.code, ei.value.extra["param"]) == (422, "bad_param", "prefix")
    assert cat.parse({"scheme": "jorc", "prefix": "x" * 40}, lambda sid: JORC).prefix == "x" * 40


def test_prefixed_renames_only_the_fields_of_an_estimate_in_every_node() -> None:
    tree = sc.prefixed("NOT (pass IN (1, 2)) AND ([KV] < 0.5 OR MAX(AU, p3) > 1) AND -ad < 5", "Z_")
    assert set(tree.fields()) == {"AU", "Z_AD", "Z_KV", "Z_P3", "Z_PASS"}
    assert sc.prefixed("PASS = 1", "").fields() == ("PASS",)
