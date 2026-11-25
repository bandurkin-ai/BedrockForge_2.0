"""Data points of an implicit model on arrays (подшаг 19.7; ТЗ 8.9.2; ADR 0026 решения 4–6): the rules of the
contacts and of s(t) — checked by hand on small holes and against an independent oracle on the synthetic scene
(`tests/fixtures/implicit`, `tools/gen/implicit.py`) — the gap is a break, a missing grade is no point (never 0),
the canonical order, the merge of coincident points with its journal, the same bytes with any order of holes, rows
and any number of threads, the Parquet blob, `too_many_points`, `no_contacts`, numba is not loaded by the modules."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.gen import implicit as gen  # noqa: E402

from bf_core.api.errors import ApiError  # noqa: E402
from bf_core.composites import intervals as iv  # noqa: E402
from bf_core.drillholes.desurvey import Collars, desurvey  # noqa: E402
from bf_core.model import data as md  # noqa: E402
from bf_core.model import data_holes as dh  # noqa: E402
from bf_core.model import data_manual as dm  # noqa: E402
from bf_core.model.spec_parts import POINT_TYPES  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "implicit"
TOP = 500.0


def vertical(td: float = 40.0) -> Any:
    """One vertical hole at (100, 200, 500): depth t is at z = 500 − t."""
    return desurvey(Collars(np.arange(1), [100.0], [200.0], [TOP], [td]), n_holes=1)


def one_hole(rows: list[tuple[float, float, int]], *, d_max: float, step: float, td: float = 40.0, **kw: Any) -> Any:
    """`domain_points` of one vertical hole with the rows (from, to, state); the points as {depth: (f, kind)}."""
    lines = iv.build_lines([0] * len(rows), [r[0] for r in rows], [r[1] for r in rows])
    state = np.array([r[2] for r in rows], dtype=np.int8)
    p = dh.domain_points(lines, state, vertical(td), np.array([0]), d_max=d_max, step=step, group=1, names=["H1"], **kw)
    return p, {
        round(TOP - float(z), 6): (float(f), POINT_TYPES[int(k)])
        for z, f, k in zip(p.xyz[:, 2], p.f, p.kind, strict=True)
    }


# --- the rules of 8.9.2 by hand ----------------------------------------------------------------------------------


def test_contact_and_s_of_t_on_both_sides() -> None:
    """D | not D: the contact (f = 0), −s inside D, +s outside every Δ_т while |s| ≤ D_max; a gap is a break."""
    p, got = one_hole([(0, 10, 1), (10, 20, 0), (20, 25, -1), (25, 40, 1)], d_max=6.0, step=2.0)
    assert got[10.0] == (0.0, "contact")
    assert {t: v for t, v in got.items() if t < 10.0} == {
        8.0: (-2.0, "inside"),
        6.0: (-4.0, "inside"),
        4.0: (-6.0, "inside"),
    }
    assert {t: v for t, v in got.items() if 10.0 < t < 20.0} == {
        12.0: (2.0, "outside"), 14.0: (4.0, "outside"), 16.0: (6.0, "outside")}  # fmt: skip
    # no point in the gap [20; 25] and no contact at 20 / 25: a missing code is not «outside D»
    assert not [t for t in got if 20.0 <= t <= 25.0]
    # the stretch below the gap has no contact at all: −D_max every 2·Δ_т (4 cells of 4 m... of 15 m → 4 cells)
    assert {t: v[0] for t, v in got.items() if t > 25.0} == {26.875: -6.0, 30.625: -6.0, 34.375: -6.0, 38.125: -6.0}
    assert p.n == 11 and p.group.tolist() == [1] * 11 and p.wing.tolist() == [-1] * 11 and p.sources == ["H1"]


def test_run_between_two_contacts_is_shared_at_the_middle() -> None:
    """Each point goes with the nearer contact: s = −min(distance to the two contacts)."""
    _p, got = one_hole([(0, 10, 0), (10, 20, 1), (20, 30, 0)], d_max=6.0, step=2.0)
    inside = {t: v for t, v in got.items() if 10.0 < t < 20.0}
    assert inside == {12.0: (-2.0, "inside"), 14.0: (-4.0, "inside"), 16.0: (-4.0, "inside"), 18.0: (-2.0, "inside")}
    assert got[10.0] == (0.0, "contact") and got[20.0] == (0.0, "contact")
    assert {t: v[0] for t, v in got.items() if t < 10.0} == {8.0: 2.0, 6.0: 4.0, 4.0: 6.0}
    assert {t: v[0] for t, v in got.items() if t > 20.0} == {22.0: 2.0, 24.0: 4.0, 26.0: 6.0}


def test_not_less_often_than_one_point_per_interval() -> None:
    """A thin interval inside the zone of a contact gets the middle of its part when no Δ_т point fell in it."""
    _p, got = one_hole([(0, 10, 0), (10, 10.4, 1), (10.4, 10.6, 1), (10.6, 30, 1)], d_max=8.0, step=5.0)
    assert got[10.0] == (0.0, "contact")
    assert got[15.0] == (-5.0, "inside")
    assert abs(got[10.2][0] + 0.2) < 1e-12 and got[10.2][1] == "inside"
    assert abs(got[10.5][0] + 0.5) < 1e-12 and got[10.5][1] == "inside"
    assert not [t for t in got if 10.6 < t < 15.0 or 15.0 < t < 30.0]  # row 3 has its Δ_т point: no extra one
    assert {t: v[0] for t, v in got.items() if t < 10.0} == {5.0: 5.0}


def test_whole_hole_in_d_and_outside_d() -> None:
    """A stretch without a contact: −D_max (+D_max) in the middle of every cell of 2·Δ_т."""
    _p, inside = one_hole([(0, 40, 1)], d_max=8.0, step=5.0)
    assert inside == {5.0: (-8.0, "inside"), 15.0: (-8.0, "inside"), 25.0: (-8.0, "inside"), 35.0: (-8.0, "inside")}
    _p, outside = one_hole([(0, 12, 0)], d_max=8.0, step=5.0)
    assert outside == {3.0: (8.0, "outside"), 9.0: (8.0, "outside")}


def test_a_missing_code_is_a_break_not_outside() -> None:
    """The same hole with «not D» in the middle has a contact; with a missing code there is none (ТЗ 8.9.2)."""
    _p, with_code = one_hole([(0, 10, 1), (10, 20, 0), (20, 40, 1)], d_max=6.0, step=2.0)
    _p, with_gap = one_hole([(0, 10, 1), (10, 20, -1), (20, 40, 1)], d_max=6.0, step=2.0)
    assert sorted(k for k, v in with_code.items() if v[1] == "contact") == [10.0, 20.0]
    assert not [k for k, v in with_gap.items() if v[1] == "contact"]
    assert all(v[0] == -6.0 for v in with_gap.values())  # only the ±D_max points of the two free stretches
    _p, apart = one_hole([(0, 10, 1), (12, 20, 0)], d_max=6.0, step=2.0)  # a hole of the table with a gap of 2 m
    assert not [k for k, v in apart.items() if v[1] == "contact"]


def test_depths_beyond_the_path_and_holes_without_a_path() -> None:
    p, got = one_hole([(0, 30, 1), (30, 50, 0)], d_max=12.0, step=2.0, td=40.0)
    assert max(got) <= 40.0 + 1e-6 and p.counts["no_depth"] > 0
    assert any("глубже конца ствола" in t for t in p.notes)
    lines = iv.build_lines([0, 0], [0.0, 10.0], [10.0, 20.0])
    lost = dh.domain_points(
        lines, np.array([1, 0], dtype=np.int8), vertical(), np.array([-1]), d_max=6, step=2, group=1
    )
    assert lost.n == 0 and any("без траектории" in t for t in lost.notes) and lost.counts["no_trajectory"] == 1
    none = dh.domain_points(lines, None, vertical(), np.array([0]), d_max=6, step=2, group=1)
    assert none.n == 0 and none.notes


def test_empty_lines_and_boolean_states() -> None:
    empty = iv.build_lines([], [], [])
    p = dh.domain_points(empty, np.zeros(0, np.int8), vertical(), np.zeros(0, np.int64), d_max=6, step=2, group=1)
    assert p.n == 0 and p.xyz.shape == (0, 3) and p.to_parquet()
    lines = iv.build_lines([0, 0], [0.0, 10.0], [10.0, 20.0])
    as_bool = dh.domain_points(lines, np.array([True, False]), vertical(), np.array([0]), d_max=6, step=2, group=1)
    as_int = dh.domain_points(lines, np.array([1, 0], np.int8), vertical(), np.array([0]), d_max=6, step=2, group=1)
    assert as_bool.n > 0 and as_bool.to_parquet() == as_int.to_parquet()
    with pytest.raises(ValueError):
        dh.domain_points(lines, np.array([1, 0, 0], np.int8), vertical(), np.array([0]), d_max=6, step=2, group=1)


def test_domain_points_refuse_too_many_points() -> None:
    lines = iv.build_lines([0, 0], [0.0, 10.0], [10.0, 20.0])
    with pytest.raises(ApiError) as err:
        dh.domain_points(lines, np.array([1, 0], np.int8), vertical(), np.array([0]), d_max=6, step=2, group=1, limit=5)
    assert err.value.code == "too_many_points" and err.value.status == 422
    assert "Δ_т" in err.value.hint and err.value.extra["points"] > 2_000_000 - 5


# --- the scene: against an oracle ----------------------------------------------------------------------------------


def read_csv(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def scene_arrays() -> dict[str, Any]:
    collars, litho = read_csv("collars.csv"), read_csv("lithology.csv")
    names = [r["HOLE"] for r in collars]
    where = {n: i for i, n in enumerate(names)}
    num = lambda key: np.array([float(r[key]) for r in collars])  # noqa: E731
    traj = desurvey(Collars(np.arange(len(names)), num("X"), num("Y"), num("Z"), num("TD"), num("AZIMUTH"), num("DIP")),
                    n_holes=len(names))  # fmt: skip
    hole = np.array([where[r["HOLE"]] for r in litho])
    lines = iv.build_lines(hole, [float(r["FROM"]) for r in litho], [float(r["TO"]) for r in litho])
    code = np.array([r["CODE"] for r in litho])
    return {"names": names, "traj": traj, "lines": lines, "code": lines.take(code), "x": num("X"), "y": num("Y"),
            "z": num("Z"), "az": num("AZIMUTH"), "dip": num("DIP")}  # fmt: skip


def runs_of(rows: list[tuple[float, float, int]], eps: float = 1e-6) -> list[tuple[float, float, int, bool, bool]]:
    """The oracle: (from, to, state, contact at the top, contact at the bottom) of every run of touching rows."""
    out: list[tuple[float, float, int, bool, bool]] = []
    i = 0
    while i < len(rows):
        j = i + 1
        while j < len(rows) and rows[j][2] == rows[i][2] and rows[j][0] - rows[j - 1][1] <= eps:
            j += 1
        if rows[i][2] >= 0:
            top = i > 0 and rows[i - 1][2] >= 0 and rows[i - 1][1] >= rows[i][0] - eps
            bottom = j < len(rows) and rows[j][2] >= 0 and rows[j][0] - rows[j - 1][1] <= eps
            out.append((rows[i][0], rows[j - 1][1], rows[i][2], top, bottom))
        i = j
    return out


@pytest.mark.parametrize("codes", [("INT",), ("SS", "SH"), ("VN",), ("GR",)])
def test_the_points_of_the_scene_follow_the_rules(codes: tuple[str, ...]) -> None:
    sc = scene_arrays()
    lines, traj = sc["lines"], sc["traj"]
    state = np.where(np.isin(sc["code"], codes), 1, 0).astype(np.int8)
    d_max, step = 30.0, 5.0
    p = dh.domain_points(lines, state, traj, np.arange(lines.n_holes), d_max=d_max, step=step, group=3,
                         names=sc["names"])  # fmt: skip
    assert p.n and (np.abs(p.f) <= d_max + 1e-9).all()
    where = {n: i for i, n in enumerate(sc["names"])}
    direction = gen._direction(sc["az"], sc["dip"])
    depth = np.empty(p.n)
    hole = np.array([where[p.sources[s]] for s in p.src])
    for k in range(p.n):  # a hole is a straight line from the collar: the depth of a point is its projection
        h = hole[k]
        depth[k] = float((p.xyz[k] - np.array([sc["x"][h], sc["y"][h], sc["z"][h]])) @ direction[h])
    checked = covered = 0
    for h in range(lines.n_holes):
        a, b = lines.starts[h], lines.starts[h + 1]
        rows = [(float(lines.frm[i]), float(lines.to[i]), int(state[i])) for i in range(a, b)]
        mine = np.flatnonzero(hole == h)
        t, f, kind = depth[mine], p.f[mine], p.kind[mine]
        for ra, rb, st, ca, cb in runs_of(rows):
            sign = -1.0 if st == 1 else 1.0
            inside = (t >= ra - 1e-6) & (t <= rb + 1e-6)
            if not ca and not cb:
                assert (np.abs(f[inside]) == d_max).all() and (np.sign(f[inside]) == sign).all()
                assert inside.sum() >= 1 and inside.sum() >= int(np.ceil((rb - ra) / (2 * step) - 1e-9))
                continue
            for tt, ff, kk in zip(t[inside], f[inside], kind[inside], strict=True):
                d = min(tt - ra if ca else np.inf, rb - tt if cb else np.inf)
                if POINT_TYPES[kk] == "contact":
                    assert ff == 0.0 and (abs(tt - ra) < 1e-6 or abs(tt - rb) < 1e-6)
                else:
                    assert ff == pytest.approx(sign * d, abs=1e-6) and d <= d_max + 1e-6
                checked += 1
            # the points every Δ_т from each contact and one point per interval inside the zone
            for contact, at in ((ca, ra), (cb, rb)):
                if not contact:
                    continue
                direction_sign = 1.0 if at == ra else -1.0
                for k in range(1, int(d_max / step) + 1):
                    tk = at + direction_sign * k * step
                    if ra < tk < rb and min(tk - ra if ca else np.inf, rb - tk if cb else np.inf) == abs(tk - at):
                        assert (np.abs(t[inside] - tk) < 1e-5).any(), (h, ra, rb, tk)
                        covered += 1
            for fa, tb, _s in [r for r in rows if ra - 1e-6 <= r[0] and r[1] <= rb + 1e-6]:
                zone = [(max(fa, ra), min(tb, ra + d_max))] if ca else []
                zone += [(max(fa, rb - d_max), min(tb, rb))] if cb else []
                if any(hi - lo > 1e-6 for lo, hi in zone):
                    assert ((t >= fa - 1e-6) & (t <= tb + 1e-6)).any(), (h, fa, tb)
    assert checked > 20 and covered > 20
    assert (p.kind == POINT_TYPES.index("contact")).sum() >= 2


def test_the_fixture_is_what_the_generator_writes(tmp_path: Path) -> None:
    gen.generate(tmp_path)
    for name in ("collars.csv", "lithology.csv", "assays.csv", "strings.csv"):
        assert (tmp_path / name).read_bytes() == (FIXTURES / name).read_bytes(), name


# --- the same bytes ---------------------------------------------------------------------------------------------


def big_set(
    holes: int, perm: np.ndarray | None, rng: np.random.Generator | None = None
) -> tuple[Any, Any, Any, list[str]]:
    """The generated holes as lines + trajectories; with `perm` the hole ids are permuted and the rows shuffled."""
    a = gen.holes_arrays(holes, seed=7)
    ids = np.arange(holes) if perm is None else perm
    collars = Collars(ids, a["x"], a["y"], a["z"], a["td"], a["azimuth"], a["dip"])
    traj = desurvey(collars, n_holes=holes)
    hole, frm, to = ids[a["hole"]], a["from"], a["to"]
    state = np.where(a["code"] < 0, -1, np.isin(a["code"], (1, 4)).astype(np.int64)).astype(np.int8)
    if rng is not None:
        order = rng.permutation(hole.shape[0])
        hole, frm, to, state = hole[order], frm[order], to[order], state[order]
    lines = iv.build_lines(hole, frm, to, ids=np.arange(hole.shape[0]))
    st = lines.take(state)
    names = [f"H{int(np.flatnonzero(ids == h)[0]):05d}" for h in lines.holes]
    return lines, st, traj, names


def test_same_points_with_any_order_of_holes_and_rows_and_threads() -> None:
    holes = 9000  # three strips of 4096
    lines, st, traj, names = big_set(holes, None)
    dense = np.asarray(lines.holes)
    base = dh.domain_points(lines, st, traj, dense, d_max=30.0, step=10.0, group=2, names=names).to_parquet()
    assert base == dh.domain_points(lines, st, traj, dense, d_max=30.0, step=10.0, group=2, names=names).to_parquet()
    rng = np.random.default_rng(3)
    perm = rng.permutation(holes)
    for threads in (1, 2, 4, 8):
        l2, s2, t2, n2 = big_set(holes, perm, rng)
        got = dh.domain_points(l2, s2, t2, np.asarray(l2.holes), d_max=30.0, step=10.0, group=2, names=n2,
                               threads=threads).to_parquet()  # fmt: skip
        assert got == base, threads


# --- canonical order and merging -----------------------------------------------------------------------------------


def mk(xyz: Any, f: Any, *, group: int = 1, kind: int = 1, wing: int = -1, sources: tuple[str, ...] = ("a",),
       src: Any = None) -> md.Points:  # fmt: skip
    p = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    n = p.shape[0]
    return md.Points(p, np.asarray(f, dtype=np.float64), np.full(n, kind, np.int8), np.full(n, group, np.int32),
                     np.full(n, wing, np.int8), np.zeros(n, np.int32) if src is None else np.asarray(src, np.int32),
                     np.full((n, 2), np.nan), np.full(n, -1, np.int64), list(sources), {"points": n})  # fmt: skip


def test_canonical_order_is_lexicographic_and_stable_to_permutation() -> None:
    rng = np.random.default_rng(1)
    xyz = rng.integers(0, 3, (60, 3)).astype(float)
    f = rng.integers(0, 3, 60).astype(float)
    kind = rng.integers(0, 3, 60).astype(np.int8)
    o = md.canonical_order(xyz, f, kind)
    keys = [tuple(r) for r in np.column_stack([xyz, f, kind])[o].tolist()]
    assert keys == sorted(keys)
    q = rng.permutation(60)
    again = md.canonical_order(xyz[q], f[q], kind[q])
    assert np.array_equal(np.column_stack([xyz, f, kind])[o], np.column_stack([xyz[q], f[q], kind[q]])[again])
    a = md.canonical_order(np.array([[0.0, 0, 0], [-0.0, 0, 0]]), np.zeros(2), np.zeros(2, np.int8), np.array([2, 1]))
    assert a.tolist() == [1, 0]  # −0.0 equals +0.0, the tie key decides


def test_merge_equal_values_and_keep_different_ones_with_a_journal() -> None:
    p = mk([[0, 0, 0], [5e-10, 0, 0], [1, 1, 1], [1, 1, 1 + 4e-10], [2, 2, 2], [2, 2, 2 + 2e-9]], [1, 1, 3, 4, 5, 5])
    out = md.assemble([p], group_names={1: "Дайка"})
    assert out.n == 5 and out.counts["merged"] == 1 and out.counts["conflicts"] == 1
    assert any("Дайка" in t and "слито" in t for t in out.notes)  # equal values, 5e-10 m apart: one point
    assert any("с разным значением" in t and "1" in t for t in out.notes)  # 3 and 4 stay
    pair = out.f[np.isclose(out.xyz[:, 0], 1.0)]
    assert sorted(pair.tolist()) == [3.0, 4.0]
    far = out.f[np.isclose(out.xyz[:, 0], 2.0)]
    assert far.tolist() == [5.0, 5.0]  # 2e-9 m apart: not coincident
    assert out.sources == ["a"] and out.counts["points"] == 5


def test_points_of_different_groups_never_merge() -> None:
    out = md.assemble(
        [mk([[0, 0, 0]], [1.0], group=1), mk([[0, 0, 0]], [1.0], group=2), mk([[0, 0, 0]], [1.0], group=2)]
    )
    assert out.group.tolist() == [1, 2]  # the point of group 2 given twice is one, group 1 keeps its own
    assert out.n == 2


def test_assemble_orders_groups_then_canonical_and_sorts_the_sources() -> None:
    a = mk([[2, 0, 0], [1, 0, 0]], [0, 0], group=2, sources=("Б", "А"), src=[0, 1])
    b = mk([[0, 0, 0]], [0.0], group=1, sources=("В",))
    out = md.assemble([a, b])
    assert out.group.tolist() == [1, 2, 2] and out.xyz[:, 0].tolist() == [0.0, 1.0, 2.0]
    assert out.sources == ["А", "Б", "В"]
    assert [out.sources[s] for s in out.src] == ["В", "А", "Б"]
    only = out.of_group(2)
    assert only.n == 2 and out.of_group(2, wing=0).n == 0 and out.take([2]).xyz.tolist() == [[2.0, 0, 0]]


def test_parquet_round_trip_and_stable_bytes() -> None:
    p = md.assemble([mk([[1.5, 2.5, 3.5], [0, 0, 0]], [0.25, -1.0], sources=("H1",))])
    p = md.Points(p.xyz, p.f, p.kind, p.group, p.wing, p.src, np.array([[1.0, 2.0], [np.nan, np.nan]]), p.vertex,
                  p.sources, p.counts, ["Заметка"], {1: [0.5, 1.5]})  # fmt: skip
    blob = p.to_parquet()
    q = md.Points.from_parquet(blob)
    for name in ("xyz", "f", "kind", "group", "wing", "src", "span", "vertex"):
        assert getattr(q, name).tobytes() == getattr(p, name).tobytes(), name
    assert q.sources == p.sources and q.notes == ["Заметка"] and q.levels == {1: [0.5, 1.5]} and q.counts == p.counts
    assert q.to_parquet() == blob
    import pyarrow as pa
    import pyarrow.parquet as pq

    sink = pa.BufferOutputStream()
    pq.write_table(pa.table({"x": [1.0]}), sink)  # a Parquet file that is not a blob of points
    with pytest.raises(ApiError) as err:
        md.Points.from_parquet(bytes(sink.getvalue().to_pybytes()))
    assert err.value.status == 422 and err.value.hint


def test_checks_too_many_and_no_contacts() -> None:
    with pytest.raises(ApiError) as err:
        md.check_size(2_000_001)
    assert err.value.code == "too_many_points" and err.value.extra == {"points": 2_000_001} and "Δ_т" in err.value.hint
    md.check_size(2_000_000)
    out = md.assemble(
        [mk([[0, 0, 0], [1, 0, 0]], [-1.0, -2.0], group=4), mk([[0, 0, 0], [1, 0, 0]], [-1.0, 2.0], group=5)]
    )
    with pytest.raises(ApiError) as err:
        md.check_contacts(out, 4)
    assert err.value.code == "no_contacts" and err.value.status == 422 and err.value.extra == {"group": 4}
    assert err.value.message and err.value.hint
    md.check_contacts(out, 5)
    with pytest.raises(ApiError):
        md.check_contacts(out, 99)  # a group without a point has no contacts either


# --- hand data (strings) -------------------------------------------------------------------------------------------


def test_layer_points_roles_vertices_and_spacing() -> None:
    xyz = np.array([[0.0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0, 0, 0], [50, 50, 5]])
    off = np.array([0, 5, 6])
    got = {role: dm.layer_points(xyz, off, [True, False], role=role, d_max=30.0, step=4.0, group=7, layer=3)
           for role in ("contact", "inside", "outside")}  # fmt: skip
    assert got["contact"].f.tolist() == [0.0] * got["contact"].n
    assert set(got["inside"].f.tolist()) == {-15.0} and set(got["outside"].f.tolist()) == {15.0}
    p = got["contact"]
    assert set(p.kind.tolist()) == {POINT_TYPES.index("manual")} and set(p.group.tolist()) == {7}
    assert p.sources == ["слой 3"] and np.isnan(p.span).all()
    # the ring: 4 vertices (the repeated one is not a point) + 2 points between on each side of 10 m / 4 m → 3 pieces
    assert p.n == 4 + 4 * 2 + 1
    assert [tuple(r) for r in p.xyz[p.vertex == 5].tolist()] == [(50.0, 50.0, 5.0)]
    assert (p.vertex >= 0).all()
    ring = p.xyz[(p.xyz[:, 2] == 0.0)]
    assert len(np.unique(ring.round(9), axis=0)) == len(ring)  # nothing twice
    lengths = np.diff(np.array([r for r in ring if r[1] == 0.0 and r[0] >= 0]).round(6)[:, 0])
    assert (lengths <= 4.0 + 1e-9).all()


def test_a_vertex_without_elevation_is_no_point_never_zero() -> None:
    xyz = np.array([[0.0, 0, 0], [10, 0, np.nan], [20, 0, 0], [30, 0, 0]])
    p = dm.layer_points(xyz, np.array([0, 4]), None, role="contact", d_max=10.0, step=50.0, group=1, name="Линия")
    assert p.n == 3 and not np.isnan(p.xyz).any()
    assert p.xyz[:, 0].tolist() == [0.0, 20.0, 30.0] and any("без координат" in t for t in p.notes)
    assert p.sources == ["слой «Линия»"]
    with pytest.raises(ApiError):
        dm.layer_points(xyz, np.array([0, 4]), None, role="above", d_max=10.0, step=5.0, group=1)


# --- grades ------------------------------------------------------------------------------------------------------


XYZ = np.arange(18.0).reshape(6, 3)


def test_grades_linear_missing_is_not_zero_and_zero_stays() -> None:
    g = np.array([0.0, 1.5, np.nan, 3.0, np.inf, 2.0])
    values, notes = dm.grade_points(XYZ, g, transform="linear")
    assert values[0] == 0.0 and values[1] == 1.5 and values[3] == 3.0 and values[5] == 2.0
    assert np.isnan(values[[2, 4]]).all()  # missing: no point, never 0
    assert any("без содержания — 2" in t for t in notes)
    xyz = XYZ.copy()
    xyz[3, 2] = np.nan
    values, notes = dm.grade_points(xyz, g)
    assert np.isnan(values[3]) and any("без координат" in t for t in notes)


def test_grades_log_drops_non_positive_and_levels() -> None:
    g = np.array([0.0, -1.0, 1.0, np.e, 10.0, np.nan])
    graded = dm.grade_points(XYZ, g, transform="log", thresholds=[1.0, 10.0])
    assert np.isnan(graded.values[[0, 1, 5]]).all()
    assert graded.values[2:5].tolist() == pytest.approx([0.0, 1.0, np.log(10.0)])
    assert any("g ≤ 0 — 2" in t for t in graded.notes)
    assert graded.levels.tolist() == pytest.approx([0.0, np.log(10.0)])
    with pytest.raises(ApiError) as err:
        dm.grade_points(XYZ, g, transform="log", thresholds=[0.0, 1.0])
    assert err.value.extra["param"] == "thresholds.0"


def test_grades_nscore_ties_are_one_score_in_any_order_and_thresholds_use_the_table() -> None:
    rng = np.random.default_rng(5)
    g = rng.choice([0.1, 0.1, 0.2, 0.5, 1.0, 4.0], 40).astype(float)
    xyz = rng.normal(size=(40, 3))
    base = dm.grade_points(xyz, g, transform="nscore", thresholds=[0.3])
    q = rng.permutation(40)
    again = dm.grade_points(xyz[q], g[q], transform="nscore", thresholds=[0.3])
    assert again.values.tobytes() == base.values[q].tobytes() and again.levels.tobytes() == base.levels.tobytes()
    ties = base.values[g == 0.1]
    assert len(set(ties.tolist())) == 1
    order = np.argsort(g, kind="stable")
    assert (np.diff(base.values[order]) >= 0).all()
    assert abs(base.values.mean()) < 0.35
    # the level of t goes through the same table: between the scores of 0.2 and 0.5
    lo, hi = base.values[g == 0.2][0], base.values[g == 0.5][0]
    assert lo < base.levels[0] < hi


def test_indicator_values_and_levels_of_linear() -> None:
    g = np.array([0.0, 1.0, 2.0, np.nan, 3.0, 1.0])
    graded = dm.grade_points(XYZ, g, thresholds=[1.0, 2.5])
    assert graded.indicators[:, 0].tolist()[:3] == [-0.5, 0.5, 0.5] and np.isnan(graded.indicators[3]).all()
    assert graded.indicators[:, 1].tolist()[:3] == [-0.5, -0.5, -0.5] and graded.indicators[4, 1] == 0.5
    assert md.indicator(g, 1.0).tolist()[:3] == [-0.5, 0.5, 0.5] and np.isnan(md.indicator(g, 1.0)[3])
    assert graded.levels.tolist() == [1.0, 2.5]
    with pytest.raises(ApiError):
        dm.grade_points(XYZ, g, transform="power")


# --- numba ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["bf_core.model.data", "bf_core.model.data_holes", "bf_core.model.data_manual"])
def test_data_modules_do_not_load_numba(module: str) -> None:
    """The handlers of jobs import these modules: numba comes with the kernels inside the functions only
    (`test_job_handler_modules_do_not_load_numba`)."""
    code = f"import {module}, sys; raise SystemExit(3 if 'numba' in sys.modules else 0)"
    run = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "core", capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-300:] or f"numba is loaded at import of {module}"
