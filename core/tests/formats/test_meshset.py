"""The model of wireframe readers and the exact welding of coincident vertices (ADR 0013 §2–3).

Reference of the welding: numpy `unique` over the bytes of the coordinates after −0.0 → 0.0 (an independent
path: a sort, not a hash table); the vertex kept is the first one in the order given, bit for bit. Tolerance:
none — a vertex one unit of the last place away is another vertex. Hard data: UTM coordinates, ±0.0, denormals,
neighbours one ulp apart, every corner the same vertex, portions of 1 and 7 corners, a table that grows from two
slots, hash collisions of a lattice.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from bf_core.formats import meshset as ms
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import ExactWelder, MeshEntry, MeshSet, clean_attrs, drop_unused, weld_exact
from tests.test_viz_mesh import UTM, cube, grid, hills

pytestmark = pytest.mark.windows


def reference_weld(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(the first vertex of every group in the order given, the group of every vertex) by a sort of the bytes."""
    key = np.ascontiguousarray(xyz + 0.0).view(np.dtype((np.void, 24))).ravel()  # −0.0 + 0.0 = +0.0
    _uniq, first, inverse = np.unique(key, return_index=True, return_inverse=True)
    order = np.argsort(first, kind="stable")  # groups in the order of their first vertex
    rank = np.empty(len(order), dtype=np.int64)
    rank[order] = np.arange(len(order))
    return first[order], rank[inverse.ravel()]


def soup(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Every triangle with corners of its own, as 3DFACE and STL give them."""
    return np.ascontiguousarray(v[t.ravel()]), np.arange(3 * len(t), dtype=np.int32).reshape(-1, 3)


def error(fn: Any, *args: Any, **kw: Any) -> FormatError:
    with pytest.raises(FormatError) as info:
        fn(*args, **kw)
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    assert any("а" <= ch <= "я" for ch in err.message.lower())  # Russian text for the user
    return err


# --- exact welding ---------------------------------------------------------------------------------------


def hard_vertices(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.uniform(-500.0, 500.0, (4000, 3)) + UTM
    near = np.nextafter(base[:300], np.inf)  # one unit of the last place away: other vertices
    tiny = np.array([[5e-324, 0.0, -0.0], [0.0, -0.0, 5e-324], [-0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [-5e-324, 0.0, 0.0]])
    big = np.array([[1.7e308, -1.7e308, 2.0**-1022], [2.0**53, 2.0**53 + 2.0, -(2.0**53)]])
    pool = np.concatenate([base, near, tiny, big])
    return np.ascontiguousarray(pool[rng.integers(0, len(pool), 60_000)])  # every vertex about 14 times


def test_weld_is_the_reference_bit_for_bit() -> None:
    xyz = hard_vertices()
    tri = np.arange(len(xyz), dtype=np.int32).reshape(-1, 3)
    first, group = reference_weld(xyz)
    v, t, merged = weld_exact(xyz, tri)
    assert v.dtype == np.float64 and t.dtype == np.int32 and v.flags.c_contiguous
    assert merged == len(xyz) - len(first) > 50_000
    assert v.tobytes() == xyz[first].tobytes()  # the first vertex of a group as given: −0.0 stays −0.0
    assert np.array_equal(t, group[tri])
    assert np.array_equal(v[t], xyz[tri])  # the same triangles (−0.0 == 0.0)
    assert np.signbit(v).any() and len(np.unique(v + 0.0, axis=0)) == len(v)


def test_one_ulp_apart_and_signed_zero() -> None:
    a = np.array([512_345.678_901, 6_234_567.123_456, 412.345_678])
    xyz = np.array([a, np.nextafter(a, np.inf), a, [0.0, -0.0, 0.0], [-0.0, 0.0, -0.0], np.nextafter(a, -np.inf)])
    v, t, merged = weld_exact(xyz, np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32))
    assert merged == 2 and t.tolist() == [[0, 1, 0], [2, 2, 3]]
    assert v.tobytes() == xyz[[0, 1, 3, 5]].tobytes()


def test_nothing_to_merge_gives_the_same_arrays() -> None:
    v, t = cube(6, 10.0)
    out_v, out_t, merged = weld_exact(v, t)
    assert merged == 0 and out_v is v and out_t is t


@pytest.mark.parametrize("portion", [1, 7, 4096, 1 << 20])
def test_portions_give_what_one_go_gives(portion: int) -> None:
    xyz = hard_vertices(seed=11)[:9000]
    first, group = reference_weld(xyz)
    welder = ExactWelder(capacity=2)  # the table and the vertex store grow many times
    index = np.concatenate([welder.add(xyz[a : a + portion]) for a in range(0, len(xyz), portion)])
    assert index.dtype == np.int32 and welder.count == len(first)
    assert np.array_equal(index, group)
    assert welder.vertices().tobytes() == xyz[first].tobytes()
    assert welder.add(np.zeros((0, 3))).shape == (0,) and welder.count == len(first)


def test_lattice_and_a_single_vertex() -> None:
    """A regular lattice (the bits of its coordinates differ in a few places only: the hash must still spread
    them) and a soup where every corner is one vertex."""
    g = np.arange(64, dtype=np.float64)
    lattice = np.stack(np.meshgrid(g, g, g, indexing="ij"), axis=-1).reshape(-1, 3) * 0.25 + UTM
    xyz = np.concatenate([lattice, lattice[::-1], lattice])
    first, group = reference_weld(xyz)
    start = time.perf_counter()
    v, t, merged = weld_exact(xyz, np.arange(len(xyz), dtype=np.int32).reshape(-1, 3))
    assert time.perf_counter() - start < 20.0  # a degenerate hash would probe for minutes (compilation included)
    assert merged == 2 * len(lattice) and v.tobytes() == lattice.tobytes() and np.array_equal(t, group.reshape(-1, 3))
    same = np.tile(UTM, (30_000, 1))
    v, t, merged = weld_exact(same, np.arange(30_000, dtype=np.int32).reshape(-1, 3))
    assert len(v) == 1 and merged == 29_999 and not t.any()


def test_soup_of_a_closed_cube_is_the_cube_again() -> None:
    from bf_core.geo.meshes import mesh_stats

    v, t = cube(20, 10.0)
    sv, st = soup(v, t)
    assert mesh_stats(sv, st)["open_edges"] == 3 * len(t)  # a soup has no shared edges at all
    wv, wt, merged = weld_exact(sv, st)
    stats = mesh_stats(wv, wt)
    assert merged == 3 * len(t) - len(v) and len(wv) == len(v)
    assert stats["closed"] and stats["volume"] == pytest.approx(1000.0, rel=1e-12)
    assert np.array_equal(wv[wt], v[t])  # the triangles in their order, corner for corner
    # welding again changes nothing: the cycle import → export → import stays bit for bit
    again_v, again_t, merged = weld_exact(wv, wt)
    assert merged == 0 and again_v is wv and again_t is wt


def test_weld_reports_progress_and_can_be_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "WELD_SLICE", 5000)
    xyz, tri = soup(*grid(60, 50, hills))
    seen: list[float] = []
    weld_exact(xyz, tri, lambda f, _m=None: seen.append(f))
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) >= 3

    def cancel(fraction: float, _message: str | None = None) -> None:
        if fraction > 0.4:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        weld_exact(xyz, tri, cancel)


def test_drop_unused_keeps_the_order() -> None:
    v, t = cube(3, 10.0)
    extra = np.concatenate([[[1.0, 2.0, 3.0]], v[:5], [[4.0, 5.0, 6.0]], v[5:], [[7.0, 8.0, 9.0]]])
    shifted = np.where(t >= 5, t + 2, t + 1).astype(np.int32)
    out_v, out_t, dropped = drop_unused(extra, shifted)
    assert dropped == 3 and out_v.tobytes() == v.tobytes() and np.array_equal(out_t, t) and out_t.dtype == np.int32
    same_v, same_t, dropped = drop_unused(v, t)
    assert dropped == 0 and same_v is v and same_t is t


# --- the model -------------------------------------------------------------------------------------------


def test_meshset_keeps_the_geometry_and_cleans_the_rest() -> None:
    v, t = cube(2, 10.0)
    mesh = MeshSet(
        "  Рудное   тело\t1 ",
        v,
        t.astype(np.int64),
        color="#8C9A6B",
        attrs=[
            {"name": " Участок ", "value": " Север "},
            {"name": "Год", "value": 2026},
            {"name": "Пусто", "value": ""},
        ],
        source={"format": "obj", "name": "  Рудное   тело\t1 "},
    )
    assert mesh.name == "Рудное тело 1" and mesh.color == "#8c9a6b"
    assert mesh.xyz.dtype == np.float64 and mesh.tri.dtype == np.int32 and mesh.tri.flags.c_contiguous
    assert mesh.xyz.tobytes() == v.tobytes() and np.array_equal(mesh.tri, t)
    assert mesh.attrs == [
        {"name": "Участок", "value": "Север"},
        {"name": "Год", "value": 2026},
        {"name": "Пусто", "value": None},  # an empty text is an empty value, never ""
    ]
    assert mesh.source["name"] == "  Рудное   тело\t1 "  # the name of the file stays as the file has it
    assert (mesh.point_count, mesh.triangle_count) == (len(v), len(t))
    lo, hi = v.min(axis=0), v.max(axis=0)
    assert mesh.extent() == [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]]
    assert MeshSet("", v, t).name == "Каркас" and MeshSet("x", v, t, color="red").color is None
    assert MeshSet("Тело\x00 1\x7f\r\nюг", v, t).name == "Тело 1 юг" and len(MeshSet("я" * 300, v, t).name) == 200
    assert MeshSet("x", v, t).stats is None and MeshSet("x", v, t).warnings == []


def test_meshset_refuses_what_cannot_be_a_wireframe() -> None:
    v, t = cube(2, 10.0)
    assert error(MeshSet, "Тело", v[:, :2], t).code == "bad_mesh"
    assert error(MeshSet, "Тело", v, t[:, :2]).code == "bad_mesh"
    assert error(MeshSet, "Тело", v, t.astype(np.float64)).code == "bad_mesh"
    assert error(MeshSet, "Тело", v, np.zeros((0, 3), dtype=np.int32)).code == "empty_mesh"
    out = t.copy()
    out[5, 1] = len(v)
    assert error(MeshSet, "Тело", v, out).code == "bad_mesh"
    out[5, 1] = -1
    assert error(MeshSet, "Тело", v, out).code == "bad_mesh"
    for bad in (np.nan, np.inf, -np.inf):  # a vertex without a coordinate is not replaced by 0
        w = v.copy()
        w[3, 2] = bad
        err = error(MeshSet, "Тело", w, t)
        assert err.code == "bad_coordinates" and "«Тело»" in err.message
    assert (
        error(MeshSet, "Тело", v, t, attrs=[{"name": "A", "value": 1}, {"name": "a", "value": 2}]).code == "bad_attrs"
    )
    assert error(MeshSet, "Тело", v, t, attrs=[{"name": " ", "value": 1}]).code == "bad_attrs"
    assert error(MeshSet, "Тело", v, t, attrs=[{"name": "A", "value": [1]}]).code == "bad_attrs"
    assert error(MeshSet, "Тело", v, t, attrs=[{"name": "A", "value": "я" * 2001}]).code == "bad_attrs"
    assert error(MeshSet, "Тело", v, t, attrs=[{"name": f"A{k}", "value": k} for k in range(201)]).code == "bad_attrs"


def test_clean_attrs_loses_nothing_silently() -> None:
    pairs: list[tuple[Any, Any]] = [
        ("UNIT", ""),
        ("unit", "x" * 2500),
        (None, 3.5),
        ("N" * 150, float("nan")),
        ("Флаг", True),
        ("Целое", np.int64(7)),
        ("Дробь", np.float64(0.1)),
    ]
    attrs, warnings = clean_attrs(pairs)
    assert [a["name"] for a in attrs] == ["UNIT", "unit (2)", "Атрибут 3", "N" * 100, "Флаг", "Целое", "Дробь"]
    assert attrs[0]["value"] is None and attrs[1]["value"] == "x" * 2000 and attrs[2]["value"] == 3.5
    assert attrs[3]["value"] is None and attrs[4]["value"] is True  # NaN is empty; yes/no stays yes/no
    assert attrs[5]["value"] == 7 and type(attrs[5]["value"]) is int and type(attrs[6]["value"]) is float
    assert len(warnings) == 4 and all(any("а" <= ch <= "я" for ch in w.lower()) for w in warnings)
    many, warnings = clean_attrs((f"A{i}", i) for i in range(250))
    assert len(many) == 200 and len(warnings) == 1 and "50" in warnings[0]
    v, t = cube(2, 10.0)
    assert MeshSet("x", v, t, attrs=attrs).attrs == attrs  # what `clean_attrs` gives is what the model takes


def test_limits_are_those_of_the_store() -> None:
    from bf_core.wireframes import store

    assert (ms.MAX_ATTRS, ms.MAX_ATTR_NAME, ms.MAX_ATTR_TEXT, ms.MAX_NAME) == (
        store.MAX_ATTRS, store.MAX_ATTR_NAME, store.MAX_ATTR_TEXT, store.MAX_NAME,
    )  # fmt: skip


def test_entry_of_a_composition() -> None:
    entry = MeshEntry(7, "Тело", 8, 12, color="#ff0000", attrs=[{"name": "A", "value": None}], warnings=["w"])
    assert entry.as_dict() == {
        "key": 7, "name": "Тело", "points": 8, "triangles": 12, "color": "#ff0000",
        "attrs": [{"name": "A", "value": None}], "extent": None, "warnings": ["w"], "error": None,
    }  # fmt: skip
    broken = MeshEntry(8, "Пусто", None, None, error={"code": "broken", "message": "м", "hint": "п"})
    assert broken.as_dict()["error"]["code"] == "broken" and broken.as_dict()["points"] is None
