"""The fixes of the review of the core of Э15 (подшаг 15.20): a table id that is not an ASCII number is a name (404,
not 500), «в том числе» c2 checked as the rule checks it, the limit of `/orientation/plane`, the units in the hash of
«Рудные пересечения», the names of `spec` re-exported from `spec_sources`."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns
from bf_core.intercepts import INCLUDE_REL, rule, spec_sources
from bf_core.intercepts import router as ir
from bf_core.intercepts import spec as sp
from tests.test_intercepts_mesh_job import mj  # noqa: F401 — fixture
from tests.test_intercepts_results import GLOBAL, IC, cc, fill  # noqa: F401 — fixture

# --- 1. a table id «²», «١», 10**30 ------------------------------------------------------------------------


@pytest.mark.parametrize("given", ["²", "١٢", "9" * 30, "³³"])
def test_core_table_not_an_ascii_id_is_404(cc, given: str) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    f = fill(cc)
    body = {"set_id": f["set_id"], "table": str(f["src"]),
            "orientation": [{"source": "core", "table": given, "alpha": "AU", "beta": "AU"}]}  # fmt: skip
    r = cc.post(f"{IC}/tt", json=body)
    assert r.status_code == 404, r.text
    out = r.json()
    assert out["code"] == "table_not_found" and out["param"] == "orientation.0.table" and out["hint"]


# --- 2. c2 > c as `rule.RuleParams.checked` -----------------------------------------------------------------


def test_include_cutoff_as_the_rule(cc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    f = fill(cc)
    base = {"set_id": f["set_id"], "table": str(f["src"]), "orientation": GLOBAL, "field": "AU", "cutoff": 1.0}
    with dialect.read_tx(f["project"].engine) as conn:
        for c2 in (1.0, 1.0 + INCLUDE_REL / 2):  # equal within the tolerance: the rule would refuse it
            with pytest.raises(ApiError) as e:
                sp.parse_run(conn, {**base, "include_cutoff": c2})
            assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "bad_param", "include_cutoff")
        s = sp.parse_run(conn, {**base, "include_cutoff": 1.0 + 1e-6})
    p = rule.RuleParams(cutoff=s.cutoff, include_cutoff=s.include_cutoff).checked()
    assert p.include_cutoff == 1.0 + 1e-6


# --- 4. the limit of `/orientation/plane`; the geometry leaves the cache -------------------------------------


def test_orientation_plane_limit_and_cache(mj, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.wireframes import exact

    c = mj.c
    exact.forget()
    r = c.post(f"{IC}/orientation/plane", json={"wireframe_ids": [mj.wf]})
    assert r.status_code == 200, r.text
    assert exact.cache_info()["loads"] == 1  # two passes over the triangles, one read
    assert not exact._cache, "the geometry read by «Взять с каркаса» stays out of the cache"
    monkeypatch.setattr(ir, "MESH_TRIANGLES", r.json()["triangles"] - 1)
    r = c.post(f"{IC}/orientation/plane", json={"wireframe_ids": [mj.wf]})
    assert r.status_code == 422, r.text
    out = r.json()
    assert out["code"] == "bad_param" and out["param"] == "wireframe_ids" and "треугольников" in out["message"]
    assert out["hint"]


# --- 5. the units in the hash -------------------------------------------------------------------------------


def test_run_hash_follows_the_units(cc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    f = fill(cc)
    body: dict[str, Any] = {"set_id": f["set_id"], "table": str(f["src"]), "orientation": GLOBAL, "field": "AU",
                            "cutoff": 1.0}  # fmt: skip

    def hashed() -> dict[str, Any]:
        with dialect.read_tx(f["project"].engine) as conn:
            return sp.parse_run(conn, body).hash_params()  # type: ignore[no-any-return]

    before = hashed()
    assert before["unit"] is None and before["units"]["AU"] is None
    with dialect.write_tx(f["project"].engine) as conn:
        conn.execute(sa.update(dh_columns).where(dh_columns.c.table_id == f["src"], dh_columns.c.name == "AU")
                     .values(unit="г/т"))  # fmt: skip
    after = hashed()
    assert after != before and after["unit"] == "г/т" and after["units"]["AU"] == "г/т"


# --- 6. the split of `spec` ---------------------------------------------------------------------------------


def test_spec_reexports_the_sources() -> None:
    for name in ("RANGES", "SOURCES", "MESH_MAX", "Source", "Attitude", "bad", "number", "ranged", "angle_pair",
                 "choice", "parse_orientation", "simple_orientation", "_located", "_any_table"):  # fmt: skip
        assert getattr(sp, name) is getattr(spec_sources, name), name
