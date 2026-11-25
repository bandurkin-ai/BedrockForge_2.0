"""IK through the estimation kernel (ТЗ 8.8.1; Э18, 18.7 + 18.9): `kernel.estimate` of a row with `method.kind = ik` is
`indicator.ik_passes` per block size, with the status of Д-27, the mask of the domain, the exclusion of the
cross-validation, threads and portions."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.geostat.estimate import indicator as ind
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate.spec import parse_estimation
from tests.test_estimate_kernel import BODY_PASSES, holes_set, same

VG = {"c0": 0.05, "structures": [{"type": "spherical", "c": 0.2, "a1": 120, "a2": 70, "a3": 25}]}


def ik_spec(per: bool, disc=(1, 1, 1)):
    body = {"model_id": 1, "samples": {"set_id": 1, "table": "t", "field": "z"}, "disc": list(disc),
            "method": {"kind": "ik", "ik": {"thresholds": [0.8, 1.5, 3.0], "models": "per" if per else "median"}},
            "search": {"from": "manual", "r1": 140.0, "r2": 80.0, "r3": 40.0, "passes": BODY_PASSES}}  # fmt: skip
    if per:
        body["variograms"] = [{"model_id": i + 1, "snapshot": VG} for i in range(3)]
    else:
        body["variogram"] = {"model_id": 1, "snapshot": VG}
    return parse_estimation(body)


@pytest.mark.parametrize("per", [True, False])
def test_ik_through_the_kernel_equals_ik_passes_and_is_a_distribution(per):
    s, rng = holes_set(21, n_holes=60, per=10)
    rk = kn.compile_row(ik_spec(per, (2, 2, 2)), s, block_size=(20.0, 20.0, 10.0))
    centres = np.column_stack([rng.uniform(0.0, 1000.0, 400), rng.uniform(0.0, 1000.0, 400),
                               rng.uniform(0.0, 200.0, 400)])  # fmt: skip
    res = kn.estimate(rk, centres, active=np.arange(400) != 7)
    assert {"P1", "P2", "P3", "ETYPE", "NS", "PASS"} <= set(res.values)
    assert res.status[7] == kn.ST_OUT and np.isnan(res.values["P1"][7])
    ok = res.status == kn.ST_OK
    assert ok.sum() > 50
    p = np.column_stack([res.values[f"P{i}"] for i in (1, 2, 3)])[ok]
    assert (p >= 0.0).all() and (p <= 1.0).all() and (np.diff(p, axis=1) >= 0.0).all()  # Т8.8-13: a distribution
    run_ = ind.ik_passes(rk.searcher, rk.local, centres - s.origin, rk.ik_setup(rk.size))
    want = np.where(np.arange(400) == 7, np.nan, run_.etype)
    assert same(res.values["ETYPE"], want)
    assert same(res.values["PASS"][ok], run_.pass_[ok].astype(float))
    parts = [centres[a : a + 100] for a in range(0, 400, 100)]  # threads and portions: the same bits
    got = list(kn.run(rk, parts, threads=4))
    keep = np.arange(400) != 7  # the block 7 was masked out above
    assert same(np.concatenate([g.values["P2"] for g in got])[keep], res.values["P2"][keep])
    i = 30  # the exclusion of a sample is the row without it
    ex = kn.estimate(rk, s.xyz[i : i + 1], exclude=("sample", np.array([i])))
    rest_row = kn.compile_row(ik_spec(per, (2, 2, 2)), s.take(np.delete(np.arange(s.n), i)),
                              block_size=(20.0, 20.0, 10.0))  # fmt: skip
    rest = kn.estimate(rest_row, s.xyz[i : i + 1])
    assert ex.status[0] == rest.status[0]  # P_k are the same bits; E-type uses the means of the whole row
    assert all(same(ex.values[f"P{q}"], rest.values[f"P{q}"]) for q in (1, 2, 3))
    d = kn.estimate_block(rk, centres[0])
    assert d.status == res.status[0] and same(np.array([d.values["ETYPE"]]), res.values["ETYPE"][:1])


def test_ik_sizes_of_own_and_the_missing_size():
    from bf_core.api.errors import ApiError

    s, rng = holes_set(22, n_holes=40, per=10)
    rk = kn.compile_row(ik_spec(False, (2, 2, 2)), s)
    centres = rng.uniform(100.0, 900.0, (30, 3)) * [1, 1, 0.2]
    with pytest.raises(ApiError) as e:
        kn.estimate(rk, centres)
    assert e.value.code == "no_block_size"
    sizes = np.where((np.arange(30) % 2 == 0)[:, None], [20.0, 20.0, 10.0], [10.0, 10.0, 5.0])
    got = kn.estimate(rk, centres, sizes=sizes)
    one = kn.compile_row(ik_spec(False, (2, 2, 2)), s, block_size=(10.0, 10.0, 5.0))
    want = kn.estimate(one, centres[1::2])
    assert same(got.values["ETYPE"][1::2], want.values["ETYPE"])
