"""The points of a variography calculation (подшаг 16.12; ADR 0022 §2, §4): the middles on the hole paths less the
centre of the extent, a missing value / position / domain counted and never a 0, the domain of the same and of
another table (text and numeric), the weights DECL_W by default, the normal scores with the weights, σ², the
refusals `no_values`, `too_few_values`, `no_spread`, the sampling with a seed, the answer of `defaults`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.geostat.variogram import points, spec
from tests.test_vario_spec import SAMPLES, make_project

USED_Z = [1.0, 2.0, 4.0, 3.0, 5.0, 0.5, 2.5, 1.5]  # line order: A-1 (no 4–6: missing), B-2, C-3; D-9 — no collar


@pytest.fixture()
def proj(tmp_path: Path):  # type: ignore[no-untyped-def]
    project, set_id, a, b = make_project(tmp_path)
    yield project, set_id, a, b
    project.engine.dispose()


def data_of(project: Any, body: dict[str, Any]) -> spec.Data:
    with dialect.read_tx(project.engine) as conn:
        return spec.parse_data(conn, body)


def load(project: Any, set_id: int, **extra: Any) -> points.Points:
    return points.load(project, data_of(project, {"set_id": set_id, "table": "Пробы", "field": "AU", **extra}))


def test_positions_counts_and_variance(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, a, _ = proj
    stages: list[str | None] = []
    p = points.load(project, data_of(project, {"set_id": set_id, "table": "Пробы", "field": "AU"}),
                    tick=lambda _f, m: stages.append(m))  # fmt: skip
    assert stages == [points.STAGE_READ, points.STAGE_TRAJ]
    assert p.counts == {"rows": 10, "missing": 1, "no_position": 1, "outside_domain": 0, "used": 8, "no_weight": 1}
    assert p.z.tolist() == USED_Z and p.values is p.z and not np.isnan(p.z).any()  # the gap is left out, not 0
    assert p.xyz.dtype == np.float64 and p.origin.tolist() == [50.0, 50.0, 96.0]  # the centre of the extent
    world = p.xyz + p.origin
    assert world[:3].tolist() == [[0.0, 0.0, 99.0], [0.0, 0.0, 97.0], [0.0, 0.0, 93.0]]  # vertical, collar z 100
    assert world[-1].tolist() == [0.0, 100.0, 97.0]
    assert p.extent() == {"min": [0.0, 0.0, 93.0], "max": [100.0, 100.0, 99.0]}
    assert p.hole.tolist() == [0, 0, 0, 1, 1, 1, 2, 2] and p.holes[:3] == ["A-1", "B-2", "C-3"]
    assert p.depth.tolist() == [1.0, 3.0, 7.0, 1.0, 3.0, 5.0, 1.0, 3.0] and p.length.tolist() == [2.0] * 8
    assert p.median_length == 2.0 and len(set(p.row_ids.tolist())) == 8
    assert p.sigma2 == p.sigma2_z == pytest.approx(float(np.var(USED_Z)), rel=1e-15)  # divisor n (BF `v.var()`)
    assert p.weights is not None and np.isnan(p.weights[2]) and p.weights[3] == 0.5  # DECL_W by default
    assert p.inputs["tables"] == [{"id": a, "name_key": "пробы", "token": p.inputs["tables"][0]["token"],
                                   "rev": p.inputs["tables"][0]["rev"]}]  # fmt: skip
    assert p.inputs["trajectories"] and all(isinstance(k, str) for k in p.inputs["trajectories"])


def test_domain_of_another_table_text_and_number(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, a, b = proj
    p = load(project, set_id, domain={"table": "Литология", "field": "ZONE", "values": ["окисл"]})
    assert p.z.tolist() == [1.0, 2.0, 3.0, 5.0, 0.5]  # A-1 0–4, B-2 0–6; C-3 2–4: its middle 3 is outside
    assert p.counts["outside_domain"] == 3 and p.counts["used"] == 5
    assert [t["id"] for t in p.inputs["tables"]] == [a, b]
    p = load(project, set_id, domain={"table": "Литология", "field": "CODE", "values": ["2,5"]})
    assert p.z.tolist() == [4.0, 2.5]  # A-1 6–8, C-3 0–2
    p = load(project, set_id, domain={"field": "ROCK", "values": ["гранит"]}, weights=False)
    assert p.z.tolist() == [1.0, 2.0, 3.0, 0.5, 1.5] and p.weights is None and p.counts["no_weight"] == 0
    with pytest.raises(ApiError) as e:
        load(project, set_id, domain={"table": "Литология", "field": "ZONE", "values": ["нет такого"]})
    assert (e.value.code, e.value.extra["counts"]["outside_domain"]) == ("no_values", 8)


def test_normal_scores_with_weights(proj) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats.transforms import nscore

    project, set_id, _, _ = proj
    p = load(project, set_id, transform="nscore")
    assert p.sigma2 == 1.0 and p.sigma2_z == pytest.approx(float(np.var(USED_Z)))
    assert p.transform is not None and np.isfinite(p.values).all()
    order = np.argsort(p.z)
    assert (np.diff(p.values[order]) > 0).all()  # the scores rise with the values
    good = np.isfinite(p.weights)  # type: ignore[arg-type]
    ref, _ = nscore(p.z[good], p.weights[good])  # type: ignore[index]
    assert p.values[good].tolist() == ref.tolist()  # the table of the points with a weight
    assert p.values[2] == p.transform.forward(np.array([4.0]))[0]  # no weight: read from the table
    q = load(project, set_id, transform="nscore", weights=False)
    assert q.values.tolist() == nscore(np.asarray(USED_Z))[0].tolist()


def test_refusals(tmp_path: Path) -> None:
    for sub in ("one", "flat", "gaps"):
        (tmp_path / sub).mkdir()
    one = [r for r in SAMPLES if r[0] == "B-2"][:1] + [("D-9", 0.0, 2.0, 7.0, "гранит", 1.0)]
    project, set_id, _, _ = make_project(tmp_path / "one", one)
    with pytest.raises(ApiError) as e:
        load(project, set_id)
    assert (e.value.status, e.value.code) == (422, "too_few_values") and e.value.message and e.value.hint
    project.engine.dispose()
    flat = [(h, f, t, 2.0, r, w) for h, f, t, _z, r, w in SAMPLES]
    project, set_id, _, _ = make_project(tmp_path / "flat", flat)
    with pytest.raises(ApiError) as e:
        load(project, set_id)
    assert e.value.code == "no_spread"
    project.engine.dispose()
    gaps = [(h, f, t, None, r, w) for h, f, t, _z, r, w in SAMPLES]
    project, set_id, _, _ = make_project(tmp_path / "gaps", gaps)
    with pytest.raises(ApiError) as e:
        load(project, set_id)
    assert (e.value.code, e.value.extra["counts"]["missing"]) == ("no_values", 10)
    project.engine.dispose()


def test_sampling_is_reproducible(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    p = load(project, set_id)
    q, sampled = points.sample(p, 5, 7)
    assert sampled == {"n": 5, "of": 8, "seed": 7} and q.n == 5
    rows = [USED_Z.index(v) for v in q.z.tolist()]
    assert rows == sorted(rows)  # the order of the points kept
    r, _ = points.sample(p, 5, 7)
    assert r.row_ids.tolist() == q.row_ids.tolist() and r.xyz.tolist() == q.xyz.tolist()
    assert q.sigma2 == p.sigma2 and q.counts == p.counts  # those of all the points
    assert points.sample(p, 8)[1] is None and points.sample(p, 100)[0] is p
    assert points.sample_size(1000, 8e9) == 500 and points.sample_size(1000, 2e9) == 1000
    assert points.sample_size(10, 1e30) == 2


def test_defaults_answer(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, a, _ = proj
    d = points.defaults(project, data_of(project, {"set_id": set_id, "table": "Пробы", "field": "AU"}))
    assert d["table"] == str(a) and d["field"] == "AU" and d["unit"] == "г/т" and d["weights"] == "DECL_W"
    assert d["has_decl_w"] is True and d["median_length"] == 2.0 and d["counts"]["used"] == 8
    assert d["defaults"]["lag"] == 10.0 and d["defaults"]["dh_lag"] == 2.0 and d["defaults"]["band_v"] == 20.0
    assert d["ranges"]["nlag"] == {"lo": 1, "hi": 100, "lo_open": False, "integer": True, "inf": False, "unit": ""}
    d = points.defaults(project, data_of(project, {"set_id": set_id, "table": "Пробы", "field": "AU",
                                                   "weights": False}))  # fmt: skip
    assert d["weights"] is None and d["has_decl_w"] is True
