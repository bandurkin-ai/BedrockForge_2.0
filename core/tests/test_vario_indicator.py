"""The indicator transform of the variography (подшаг 18.10; ТЗ 8.8.1, ADR 0024 № 85): `transform: indicator:<t>`,
i = 1 where z ≤ t (the threshold itself — 1), else 0; σ² = p(1 − p); a gap is a gap, never a 0; a threshold that
divides nothing is `no_spread`; the canonical string, the answer of `defaults`, the cache key of a calculation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.variogram import jobs, points, spec
from tests.test_vario_points import USED_Z, data_of
from tests.test_vario_spec import make_project, parse


@pytest.fixture()
def proj(tmp_path: Path):  # type: ignore[no-untyped-def]
    project, set_id, _a, _b = make_project(tmp_path)
    yield project, set_id
    project.engine.dispose()


def of(project: Any, set_id: int, **extra: Any) -> spec.Data:
    return data_of(project, {"set_id": set_id, "table": "Пробы", "field": "AU", **extra})


def test_indicators_function_keeps_gaps() -> None:
    z = np.array([1.0, np.nan, 2.0, 3.0, -1.0])
    out = points.indicators(z, 2.0)
    assert out.dtype == np.float64
    assert out[[0, 2, 3, 4]].tolist() == [1.0, 1.0, 0.0, 1.0]  # z = t → 1
    assert np.isnan(out[1])  # a gap is a gap, not a 0


def test_sigma2_is_p_one_minus_p(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    z = np.asarray(USED_Z)
    for t in (0.5, 1.5, 2.0, 2.5, 4.0):
        p = points.load(project, of(project, set_id, transform=f"indicator:{t}"))
        share = float((z <= t).mean())
        assert p.values.tolist() == (z <= t).astype(float).tolist()
        assert p.sigma2 == pytest.approx(share * (1.0 - share), abs=1e-12)
        assert p.sigma2_z == pytest.approx(float(np.var(z)), rel=1e-15)  # the variance of the values is kept
        assert p.z.tolist() == USED_Z and p.transform is None


def test_gaps_are_counted_not_zeroed(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    p = points.load(project, of(project, set_id, transform="indicator:2"))
    assert p.counts == {"rows": 10, "missing": 1, "no_position": 1, "outside_domain": 0, "used": 8, "no_weight": 1}
    assert p.n == 8 and not np.isnan(p.values).any()  # the row without a value is not a point (not i = 0)
    q = points.load(project, of(project, set_id, transform="indicator:2", weights=False))
    assert q.values.tolist() == p.values.tolist()  # the weights are not in the indicator


def test_threshold_outside_the_values_is_no_spread(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    for t, same in ((0.1, 0), (5.0, 1), (100, 1)):
        with pytest.raises(ApiError) as e:
            points.load(project, of(project, set_id, transform=f"indicator:{t}"))
        assert (e.value.status, e.value.code) == (422, "no_spread") and f"везде {same}" in e.value.message
        assert e.value.hint and e.value.extra["counts"]["used"] == 8


def test_canonical_string_and_threshold(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    d = of(project, set_id, transform="indicator:1,50")  # a decimal comma
    assert d.transform == "indicator:1.5" and d.threshold == 1.5
    assert d.canonical()["transform"] == "indicator:1.5"
    assert of(project, set_id, transform="indicator", threshold=1.5).transform == "indicator:1.5"
    assert of(project, set_id, transform="indicator", threshold="1.5").transform == "indicator:1.5"
    assert of(project, set_id, transform="indicator:-0").transform == "indicator:0"
    assert of(project, set_id, transform="indicator:1e-7").threshold == 1e-7
    assert of(project, set_id).threshold is None
    assert of(project, set_id, transform="nscore").threshold is None


@pytest.mark.parametrize(
    "bad",
    [
        "indicator",
        "indicator:",
        "indicator:abc",
        "indicator:nan",
        "indicator:inf",
        "indicator:-inf",
        "indicatorx",
        "indicator:true",
        "log",
    ],
)
def test_bad_threshold(proj, bad: str) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    with pytest.raises(ApiError) as e:
        of(project, set_id, transform=bad)
    assert (e.value.status, e.value.code, e.value.extra.get("param")) == (422, "bad_param", "transform")
    assert e.value.hint


def test_bad_threshold_value_types(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    for t in (True, float("nan"), None, "", [1]):
        with pytest.raises(ApiError) as e:
            of(project, set_id, transform="indicator", threshold=t)
        assert e.value.code == "bad_param"


def test_defaults_answer_has_the_share(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    out = points.defaults(project, of(project, set_id, transform="indicator:2"))
    p = float((np.asarray(USED_Z) <= 2.0).mean())
    assert out["transform"] == "indicator:2" and out["indicator"] == {"threshold": 2.0, "p": p}
    assert out["sigma2"] == pytest.approx(p * (1 - p), abs=1e-12)
    assert "indicator" not in points.defaults(project, of(project, set_id))


def test_other_threshold_other_key(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    body = {"set_id": set_id, "table": "пробы", "field": "au", "transform": "indicator:2"}

    def key(b: dict[str, Any]) -> str:
        s = parse(project, spec.parse_experimental, b)
        return jobs.key_of(s.resolved(spec.defaults_for(2.0)), {})

    assert key(body) != key({**body, "transform": "indicator:3"}) != key({**body, "transform": "nscore"})
    assert key(body) == key({**body, "transform": "indicator", "threshold": 2})
