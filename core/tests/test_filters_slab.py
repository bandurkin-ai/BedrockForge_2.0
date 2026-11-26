"""The leaf `slab` of the rule tree (подшаг 20.8; ТЗ 8.10 Т8.10-4, 7.35 Д-36; ADR 0027 decision 9): the contract of
`filters/schema.py` (canonical form, 422 with the place, no text, the title) and the share of `restrict.py`
(«по центру», «по доле» along z, the axes x, y, u, v, AND / OR / NOT with the other leaves, the block of the volume
and the tonnage of Т8.10-4)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import restrict
from bf_core.blocks import store as bm
from bf_core.blocks.filters import routes
from bf_core.blocks.filters import schema as fs
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_model import make_sub
from tests.test_filters_store import cond, group

API = "/api/v1"
FL = API + "/filters"
GRID = {"corner": [0.0, 0.0, 0.0], "counts": [4, 3, 2], "size": [5.0, 5.0, 2.5]}  # 24 blocks, 2 layers of 2.5 m
FIELDS = [{"name": "AU", "kind": "number", "unit": "g/t"}, {"name": "RHO", "kind": "number", "unit": None}]
AU = np.array([0.5, 1.0, 1.5, 3.0, np.nan, 2.0, 0.2, 5.0, 1.1, 1.0001, np.nan, 7.0,
               2.5, 0.9, 1.2, 4.0, 0.0, 9.0, 1.5, np.nan, 3.3, 0.1, 8.0, 2.2])  # fmt: skip


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    """A core with an empty project and the router of the filters (until `api/modules.py` names it)."""
    if not any(str(getattr(r, "path", "")).startswith(FL) for r in pclient.app.routes):
        pclient.app.include_router(routes.router, prefix=API)
    assert pclient.post(f"{API}/projects", json={"name": "Слои"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def put(c: Any, grid: Any, au: np.ndarray, rho: np.ndarray, name: str = "Модель") -> int:
    idx = np.arange(len(au), dtype=np.uint64)
    with dialect.write_tx(c.project.engine) as conn:
        return bm.put_block_model(conn, c.store, grid, FIELDS, idx, {"AU": au, "RHO": rho}, name=name)


def plain(c: Any) -> int:
    return put(c, GRID, AU, np.full(24, 2.5))


def open_model(c: Any, mid: int) -> Model:
    with dialect.read_tx(c.project.engine) as conn:
        return Model.open(conn, c.store, mid)


def shares(c: Any, mid: int, rules: Any, chunk: int = 1 << 20) -> np.ndarray:
    m = open_model(c, mid)
    with dialect.read_tx(c.project.engine) as conn:
        r = restrict.prepare(m, rules, conn=conn, store=c.store)
    out = np.empty(m.rows)
    for ch, f in r.iter(m, chunk=chunk):
        out[ch.start : ch.start + ch.n] = f
    assert r.warnings == []
    return out


def slab(axis: str, lo: Any, hi: Any, by: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"axis": axis, "from": lo, "to": hi, **extra}
    if by is not None:
        body["by"] = by
    return {"slab": body}


def refused(rules: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        fs.normalize_rules(rules)
    return e.value


# --- the contract: schema ---------------------------------------------------------------------------------------------


def test_canonical_form_fills_the_defaults_and_reads_numbers() -> None:
    assert fs.normalize_rules(slab("z", 250, "260,5")) == slab("z", 250.0, 260.5, "centre")
    assert fs.normalize_rules(slab("z", 0, 10, "share")) == slab("z", 0.0, 10.0, "share")
    assert fs.normalize_rules(slab("x", -5.5, 5)) == slab("x", -5.5, 5.0, "centre")
    # u and v carry the angles of the swaths, the missing ones are 0
    assert fs.normalize_rules(slab("u", 0, 1)) == slab("u", 0.0, 1.0, "centre",
                                                       frame={"azimuth": 0.0, "dip": 0.0, "roll": 0.0})  # fmt: skip
    got = fs.normalize_rules(slab("v", 0, 1, frame={"azimuth": "30,5", "dip": -10}))
    assert got["slab"]["frame"] == {"azimuth": 30.5, "dip": -10.0, "roll": 0.0}
    again = fs.normalize_rules(got)
    assert again == got  # canonical form is a fixed point
    tree = group("and", cond("AU", ">", 1), group("not", slab("z", 0, 5)))
    inner = fs.normalize_rules(tree)["items"][1]["items"][0]
    assert inner == {"slab": {"axis": "z", "from": 0.0, "to": 5.0, "by": "centre"}}


@pytest.mark.parametrize(
    ("rules", "param"),
    [
        ({"slab": "z"}, "rules.slab"),
        ({"slab": {"axis": "z", "from": 0, "to": 5, "id": 1}}, "rules.slab"),
        (slab("w", 0, 5), "rules.slab.axis"),
        ({"slab": {"from": 0, "to": 5}}, "rules.slab.axis"),
        (slab("z", None, 5), "rules.slab.from"),
        (slab("z", "abc", 5), "rules.slab.from"),
        (slab("z", True, 5), "rules.slab.from"),
        (slab("z", float("nan"), 5), "rules.slab.from"),
        (slab("z", 0, float("inf")), "rules.slab.to"),
        ({"slab": {"axis": "z", "from": 0}}, "rules.slab.to"),
        (slab("z", 5, 5), "rules.slab.to"),
        (slab("z", 6, 5), "rules.slab.to"),
        (slab("z", 0, 5, "frac"), "rules.slab.by"),
        (slab("x", 0, 5, "share"), "rules.slab.by"),
        (slab("v", 0, 5, "share"), "rules.slab.by"),
        (slab("x", 0, 5, frame={"azimuth": 10}), "rules.slab.frame"),
        (slab("u", 0, 5, frame="90"), "rules.slab.frame"),
        (slab("u", 0, 5, frame={"turn": 1}), "rules.slab.frame"),
        (slab("u", 0, 5, frame={"azimuth": "x"}), "rules.slab.frame.azimuth"),
        (slab("u", 0, 5, frame={"dip": 91}), "rules.slab.frame.dip"),
        (group("and", cond("A", "=", 1), group("or", slab("z", 3, 1))), "rules.items.1.items.0.slab.to"),
        ({"slab": {"axis": "z", "from": 0, "to": 5}, "expr": "AU > 1"}, "rules"),
    ],
)
def test_wrong_slab_is_422_with_the_place(rules: Any, param: str) -> None:
    e = refused(rules)
    assert (e.status, e.code, e.extra["param"]) == (422, "filter_rules", param)
    assert e.message and e.hint  # in Russian, with a hint
    assert any("а" <= ch <= "я" or "А" <= ch <= "Я" for ch in e.message + e.hint)


def test_a_slab_has_no_text_no_object_and_no_field() -> None:
    one = fs.normalize_rules(slab("z", 0, 5))
    tree = fs.normalize_rules(group("and", cond("AU", ">", 1), slab("z", 0, 5)))
    assert fs.to_text(one) is None and fs.to_text(tree) is None  # as for a wireframe
    assert fs.to_text(fs.normalize_rules(group("and", cond("AU", ">", 1), cond("AU", "<", 9)))) is not None
    assert fs.leaves(tree) == [] and fs.object_ids(tree) == [] and fs.fields_of(tree) == ["AU"]
    assert fs.remap_ids(tree, lambda i: i + 1) == tree
    meta = fs.meta_of(tree, None)
    assert meta["text"] is None and meta["fields"] == ["AU"] and meta["rules"] == tree


def test_title_in_russian() -> None:
    assert fs.title_of(fs.normalize_rules(slab("z", 250, 260))) == "Z от 250 до 260"
    assert fs.title_of(fs.normalize_rules(slab("z", 250, 260.5, "share"))) == "Z от 250 до 260.5 (по доле)"
    assert fs.title_of(fs.normalize_rules(slab("u", -3, 2.5))) == "U от -3 до 2.5"
    assert fs.title_of(fs.normalize_rules(group("and", cond("AU", ">", 1), slab("x", 0, 5)))) == "AU > 1 И X от 0 до 5"
    assert fs.title_of(fs.normalize_rules(group("not", slab("y", 0, 5)))) == "НЕ Y от 0 до 5"


# --- the share --------------------------------------------------------------------------------------------------------


def test_by_the_centre_includes_from_excludes_to(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain(rc)  # the centres along z: 1.25 (rows 0…11) and 3.75 (rows 12…23)
    lo, up = lambda f: f[:12], lambda f: f[12:]  # noqa: E731
    f = shares(rc, mid, slab("z", 1.25, 3.0))  # from == the centre: in
    assert (lo(f) == 1).all() and (up(f) == 0).all()
    f = shares(rc, mid, slab("z", 0.0, 1.25))  # to == the centre: out
    assert not f.any()
    f = shares(rc, mid, slab("z", 1.25, 3.75))
    assert (lo(f) == 1).all() and (up(f) == 0).all()
    f = shares(rc, mid, slab("z", 1.25, 3.75 + 1e-9))
    assert (f == 1).all()
    assert not shares(rc, mid, slab("z", 100, 200)).any() and not shares(rc, mid, slab("z", -9, -1)).any()
    # a sliver between two centres touches none by the centre
    assert not shares(rc, mid, slab("z", 2.0, 3.0)).any()


def test_by_the_share_of_the_height_along_z(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain(rc)  # layers [0; 2.5] and [2.5; 5]
    f = shares(rc, mid, slab("z", 1.0, 4.0, "share"))
    assert np.allclose(f, 0.6, atol=1e-12)  # 1.5 m of 2.5 m in each
    f = shares(rc, mid, slab("z", 1.0, 2.5, "share"))
    assert np.allclose(f[:12], 0.6, atol=1e-12) and (f[12:] == 0).all()
    f = shares(rc, mid, slab("z", 0.5, 1.0, "share"))  # inside one block
    assert np.allclose(f[:12], 0.2, atol=1e-12) and (f[12:] == 0).all()
    f = shares(rc, mid, slab("z", -10.0, 10.0, "share"))  # covers the model
    assert (f == 1).all()
    assert not shares(rc, mid, slab("z", 5.0, 9.0, "share")).any()  # touches the top only: no share, not NaN
    assert not shares(rc, mid, slab("z", -9.0, 0.0, "share")).any()
    # by the centre the same slab takes a block whole or not at all
    f = shares(rc, mid, slab("z", 1.0, 4.0))
    assert (f == 1).all()
    # a block with 0 < f < 1 passes (№ 65): the weight is the sum of the shares
    got = restrict.test_rules(rc.project, mid, slab("z", 1.0, 2.5, "share"))
    assert got["passed"] == 12 and got["weight"] == pytest.approx(12 * 0.6, abs=1e-12)


def test_x_and_y_are_the_axes_of_the_project(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain(rc)
    c = open_model(rc, mid).centres()
    assert sorted(set(c[:, 0])) == [2.5, 7.5, 12.5, 17.5] and sorted(set(c[:, 1])) == [2.5, 7.5, 12.5]
    f = shares(rc, mid, slab("x", 5.0, 15.0))
    assert np.array_equal(f, ((c[:, 0] >= 5) & (c[:, 0] < 15)).astype(float)) and f.sum() == 12
    f = shares(rc, mid, slab("y", 7.5, 12.5))  # from == a centre: in, to == a centre: out
    assert np.array_equal(f, (c[:, 1] == 7.5).astype(float)) and f.sum() == 8


def test_u_and_v_are_the_axes_of_the_swath_frame(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain(rc)
    c = open_model(rc, mid).centres()
    # azimuth 90°: u is X bit for bit, v = (0, −1, 0)
    f = shares(rc, mid, slab("u", 5.0, 15.0, frame={"azimuth": 90}))
    assert np.array_equal(f, shares(rc, mid, slab("x", 5.0, 15.0)))
    f = shares(rc, mid, slab("v", -8.0, -3.0, frame={"azimuth": 90}))
    assert np.array_equal(f, ((-c[:, 1] >= -8) & (-c[:, 1] < -3)).astype(float)) and f.sum() == 8
    # the default frame (azimuth 0): u is north = Y
    assert np.array_equal(shares(rc, mid, slab("u", 5.0, 15.0)), shares(rc, mid, slab("y", 5.0, 15.0)))
    # a skew frame: the projection of the centres on (sin A, cos A, 0)
    a = np.radians(30.0)
    t = c[:, 0] * np.sin(a) + c[:, 1] * np.cos(a)
    f = shares(rc, mid, slab("u", 6.0, 12.0, frame={"azimuth": 30}))
    assert np.array_equal(f, ((t >= 6.0) & (t < 12.0)).astype(float))
    assert 0 < f.sum() < 24
    # a dipping axis: u = (cos D·sin A, cos D·cos A, sin D); D = 90° — Z
    f = shares(rc, mid, slab("u", 0.0, 2.5, frame={"azimuth": 0, "dip": 90}))
    assert np.array_equal(f, shares(rc, mid, slab("z", 0.0, 2.5)))


def test_a_turned_model_with_sub_blocks_keeps_the_share_of_the_height(rc) -> None:  # type: ignore[no-untyped-def]
    mid = make_sub(rc.project, rc.store)[0]  # turned by 30°, sub-blocks, z from −300
    m = open_model(rc, mid)
    c, box = m.centres(), m.boxes()
    height = box[:, 5] - box[:, 2]
    lo, hi = -298.0, -291.3
    want = np.clip((np.minimum(c[:, 2] + height / 2, hi) - np.maximum(c[:, 2] - height / 2, lo)) / height, 0.0, 1.0)
    got = shares(rc, mid, slab("z", lo, hi, "share"))
    assert np.allclose(got, want, atol=1e-12) and ((got > 0) & (got < 1)).any() and (got == 1).any()
    # the layers of a partition sum to 1 for every block, however the model is turned
    edges = [-1000.0, -297.7, -295.1, -290.0, 0.0]
    for by in ("share", "centre"):
        total = sum(shares(rc, mid, slab("z", a, b, by)) for a, b in zip(edges[:-1], edges[1:], strict=True))
        assert np.allclose(total, 1.0, atol=1e-12), by
    # u along the turned axis of the model (azimuth 90° − 30°): the projection of the centres
    a = np.radians(60.0)
    t = c[:, 0] * np.sin(a) + c[:, 1] * np.cos(a)
    mid_t = float(np.median(t))
    f = shares(rc, mid, slab("u", mid_t, mid_t + 1e6, frame={"azimuth": 60}))
    assert np.allclose(f, (t >= mid_t).astype(float)) and 0 < f.sum() < len(f)


def test_with_the_other_leaves_and_or_not(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain(rc)
    part = slab("z", 1.0, 2.5, "share")  # 0.6 in the lower layer
    centre = slab("x", 5.0, 15.0)
    hot = AU > 1  # NaN compares false
    z = np.where(np.arange(24) < 12, 0.6, 0.0)
    cx = open_model(rc, mid).centres()[:, 0]
    x = ((cx >= 5) & (cx < 15)).astype(float)
    assert np.allclose(shares(rc, mid, group("and", cond("AU", ">", 1), part)), z * hot, atol=1e-12)
    assert np.allclose(shares(rc, mid, group("and", part, centre)), z * x, atol=1e-12)
    assert np.allclose(shares(rc, mid, group("or", part, centre)), np.maximum(z, x), atol=1e-12)
    assert np.allclose(shares(rc, mid, group("not", part)), 1.0 - z, atol=1e-12)
    # a gap in AU is no zero: the calculator is two-valued, NOT (AU > 1) takes the block with a gap
    both = group("and", group("not", cond("AU", ">", 1)), part)
    got = shares(rc, mid, both)
    assert np.allclose(got, z * (~hot), atol=1e-12) and (got[np.isnan(AU)][:2] == 0.6).all()  # the gaps are taken
    # nested: (slab OR slab) AND cond, in one tree with a surface-free expression
    deep = group("and", group("or", slab("z", 0.0, 2.5, "share"), slab("z", 2.5, 5.0, "share")), cond("AU", ">", 1))
    assert np.allclose(shares(rc, mid, deep), hot.astype(float), atol=1e-12)
    # the result does not depend on the size of the chunk
    for chunk in (1, 5, 24, 1 << 20):
        both = group("and", part, centre)
        assert np.array_equal(shares(rc, mid, both, chunk=chunk), shares(rc, mid, both))


def test_a_field_the_model_lacks_is_still_422_and_a_slab_needs_no_field(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain(rc)
    m = open_model(rc, mid)
    with dialect.read_tx(rc.project.engine) as conn:
        r = restrict.prepare(m, slab("z", 0, 5), conn=conn, store=rc.store)
        assert r.fields == []  # a slab reads no column
        with pytest.raises(ApiError) as e:
            restrict.prepare(m, group("and", slab("z", 0, 5), cond("NOPE", ">", 1)), conn=conn, store=rc.store)
    assert e.value.code == "filter_field_missing"


# --- Т8.10-4 ----------------------------------------------------------------------------------------------------------


def test_t8_10_4_a_block_of_10_m_is_split_in_half_by_the_boundary_of_the_benches(rc) -> None:  # type: ignore[no-untyped-def]
    mid = put(rc, {"corner": [0.0, 0.0, 0.0], "counts": [1, 1, 1], "size": [10.0, 10.0, 10.0]},
              np.array([1.0]), np.array([3.0]))  # fmt: skip
    m = open_model(rc, mid)
    v = float(m.volume()[0])
    assert v == 1000.0
    lower, upper = (shares(rc, mid, slab("z", a, b, "share"))[0] for a, b in ((0.0, 5.0), (5.0, 10.0)))
    assert (lower, upper) == (0.5, 0.5)
    assert abs(v * lower * 3.0 - 1500.0) <= 1e-12 * 1500.0  # half of the tonnage: 1000 m³ · 3 t/m³ / 2


def test_t8_10_4_a_cube_of_100_m_on_the_grid_the_benches_sum_to_the_whole_volume_and_10e6_rho(rc) -> None:  # type: ignore[no-untyped-def]
    rho = 2.7
    n = 10 * 10 * 10
    mid = put(rc, {"corner": [0.0, 0.0, 0.0], "counts": [10, 10, 10], "size": [10.0, 10.0, 10.0]},
              np.linspace(0.1, 3.0, n), np.full(n, rho))  # fmt: skip
    m = open_model(rc, mid)
    vol = m.volume()
    assert vol.sum() == 1e6
    density = m.columns(["RHO"])["RHO"]
    # benches of 7 m from −3 m: the boundaries cut the blocks, the first and the last bench are partly empty
    edges = np.arange(-3.0, 111.0, 7.0)
    got_v = got_t = 0.0
    cut = 0
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        f = shares(rc, mid, slab("z", float(a), float(b), "share"))
        cut += int(((f > 0) & (f < 1)).any())
        got_v += float((vol * f).sum())
        got_t += float((vol * f * density).sum())
    assert cut >= 10  # the benches are not aligned to the grid: the blocks are cut
    assert abs(got_v - 1e6) <= 1e-12 * 1e6
    assert abs(got_t - 1e6 * rho) <= 1e-12 * 1e6 * rho
    # benches on the grid: every block in one bench, by the centre and by the share
    for by in ("centre", "share"):
        per = [float((vol * shares(rc, mid, slab("z", float(a), float(a + 10), by))).sum()) for a in range(0, 100, 10)]
        assert per == [1e5] * 10, by
    # the whole is not changed by the bench around it: AND with a slab that covers the cube
    assert np.array_equal(shares(rc, mid, group("and", slab("z", -1e9, 1e9, "share"), slab("x", 0, 100))), np.ones(n))


# --- API --------------------------------------------------------------------------------------------------------------


def test_a_filter_of_a_slab_is_stored_without_text_and_tested_on_the_model(rc) -> None:  # type: ignore[no-untyped-def]
    mid = plain(rc)
    rules = group("and", cond("AU", ">", 1), slab("z", 1.0, 2.5, "share"))
    r = rc.post(FL, json={"name": "Слой", "rules": rules})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["text"] is None and got["fields"] == ["AU"]
    assert got["rules"]["items"][1] == {"slab": {"axis": "z", "from": 1.0, "to": 2.5, "by": "share"}}
    t = rc.post(f"{FL}/{got['id']}/test", json={"model_id": mid})
    assert t.status_code == 200, t.text
    hot = (AU > 1) & (np.arange(24) < 12)
    assert (t.json()["passed"], t.json()["total"]) == (int(hot.sum()), 24)
    assert t.json()["weight"] == pytest.approx(0.6 * hot.sum(), abs=1e-12)
    t = rc.post(f"{FL}/test", json={"model_id": mid, "rules": slab("z", 0.0, 5.0)})
    assert (t.json()["passed"], t.json()["weight"]) == (24, 24.0)


def test_a_wrong_slab_over_the_api_is_422_in_russian(rc) -> None:  # type: ignore[no-untyped-def]
    r = rc.post(FL, json={"name": "Плохой", "rules": slab("z", 5, 1)})
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "filter_rules", "rules.slab.to")
    assert r.json()["message"] and r.json()["hint"]
    assert rc.get(FL).json()["filters"] == []  # nothing was written
