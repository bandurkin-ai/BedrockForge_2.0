"""The contact analysis (ТЗ 8.3; ADR 0019 §4): Т8.3-2 (A is 1.0 everywhere, B is 3.0 — the means of the steps
−D … 0 are 1 and of 0 … D are 3 exactly), the order B → A is the mirror, a sample does not go beyond the
neighbouring contact of its run, a gap in sampling is not a contact, shuffling the holes changes nothing; the
steps against a plain Python count on «hard» tables."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from bf_core.stats import contact
from bf_core.stats.contact import MAX_STEPS, contact_chart, steps_of
from bf_core.stats.select import SampleFilter
from bf_core.stats.weighted import StatsError

NAN = math.nan
REL = 1e-12
LABELS = ["GRANITE", "ORE", "SCHIST"]
G, A, B = 0, 1, 2  # the codes of LABELS: the contact is ORE | SCHIST


def refused(code: str, param: str | None, call) -> StatsError:  # type: ignore[no-untyped-def]
    with pytest.raises(StatsError) as e:
        call()
    assert e.value.code == code and e.value.param == param and e.value.message and e.value.hint
    return e.value


def chart(hole, frm, to, codes, values, flt=None, **kw):  # type: ignore[no-untyped-def]
    kw.setdefault("a", "ORE")
    kw.setdefault("b", "SCHIST")
    return contact_chart(hole, frm, to, np.asarray(codes, dtype=np.int32), LABELS, values=values, flt=flt, **kw)


def hole_of(codes, lengths, start=0.0, hole=0):  # type: ignore[no-untyped-def]
    """Rows of one hole: consecutive samples of the lengths given from the depth `start`."""
    to = start + np.cumsum(np.asarray(lengths, dtype=float))
    frm = np.concatenate(([start], to[:-1]))
    return np.full(len(to), hole, dtype=np.int64), frm, to, np.asarray(codes, dtype=np.int32)


def join(*parts):  # type: ignore[no-untyped-def]
    return tuple(np.concatenate(col) for col in zip(*parts, strict=True))


def table(n_holes: int = 60, seed: int = 7):  # type: ignore[no-untyped-def]
    """A «hard» table: runs of three domains and of no domain, gaps in sampling (and gaps of float noise), depths
    with float noise, rows without a value, without a depth, with TO ≤ FROM, repeated rows; rows shuffled."""
    rng = np.random.default_rng(seed)
    hole, frm, to, codes = [], [], [], []
    for h in range(n_holes):
        depth = float(rng.choice([0.0, 0.5, 1.2]))
        code = int(rng.integers(-1, 3))
        for _ in range(int(rng.integers(1, 60))):
            if rng.random() < 0.08:
                depth += float(rng.choice([0.3, 2.0, 5e-7]))  # 5e−7 m is float noise, not a gap
            if rng.random() < 0.25:
                code = int(rng.integers(-1, 3))
            length = float(rng.choice([0.1, 0.5, 1.0, 1.0, 1.5, 2.0]))
            hole.append(h)
            frm.append(depth)
            to.append(depth + length)
            codes.append(code)
            depth += length
    n = len(hole)
    hole, frm, to, codes = np.array(hole), np.array(frm), np.array(to), np.array(codes, dtype=np.int32)
    values = rng.lognormal(0.4 * codes, 0.7, n)
    values[rng.random(n) < 0.05] = NAN
    lengths = to - frm
    # rows that take no part: no depth, TO ≤ FROM, a repeated row (it overlaps the one kept)
    bad = rng.choice(n, 12, replace=False)
    hole = np.concatenate([hole, hole[bad]])
    frm = np.concatenate([frm, [NAN] * 4, frm[bad[4:8]], frm[bad[8:]]])
    to = np.concatenate([to, to[bad[:4]], frm[bad[4:8]], to[bad[8:]]])
    codes = np.concatenate([codes, codes[bad]])
    values = np.concatenate([values, np.full(12, 77.0)])
    lengths = np.concatenate([lengths, np.ones(12)])
    order = rng.permutation(len(hole))
    return hole[order], frm[order], to[order], codes[order], values[order], lengths[order]


def reference(hole, frm, to, codes, values, weights, ca, cb, distance=20.0, step=1.0, eps=1e-6):  # type: ignore[no-untyped-def]
    """The steps counted in plain Python: the rows of a hole by depth, the runs of one code without a gap, the
    contacts A | B between adjacent runs, every sample — to the nearest contact at the ends of its own run."""
    n_side = max(1, math.ceil(distance / step - 1e-9))
    rows: list[list[tuple[float, float]]] = [[] for _ in range(2 * n_side)]
    contacts, with_contacts = 0, set()
    by_hole: dict[int, list[int]] = {}
    for i in range(len(hole)):
        if math.isfinite(frm[i]) and math.isfinite(to[i]) and to[i] > frm[i]:
            by_hole.setdefault(int(hole[i]), []).append(i)
    for h, idx in by_hole.items():
        runs: list[dict] = []
        prev = None
        for i in sorted(idx, key=lambda i: (frm[i], to[i], i)):
            f, t = float(frm[i]), float(to[i])
            if prev is not None and (f < prev - eps or t <= prev):
                continue  # overlaps the row kept before it
            f = f if prev is None else max(f, prev)
            prev = t
            if runs and runs[-1]["code"] == codes[i] and f - runs[-1]["to"] <= eps:
                runs[-1]["to"] = t
                runs[-1]["rows"].append((f, t, i))
            else:
                runs.append({"code": int(codes[i]), "frm": f, "to": t, "rows": [(f, t, i)]})
        for k, r in enumerate(runs):
            if r["code"] not in (ca, cb):
                continue
            other = cb if r["code"] == ca else ca
            top = k > 0 and runs[k - 1]["code"] == other and r["frm"] - runs[k - 1]["to"] <= eps
            bottom = k + 1 < len(runs) and runs[k + 1]["code"] == other and runs[k + 1]["frm"] - r["to"] <= eps
            if top:
                contacts += 1
                with_contacts.add(h)
            for f, t, i in r["rows"]:
                mid = 0.5 * (f + t)
                near = ([mid - r["frm"]] if top else []) + ([r["to"] - mid] if bottom else [])
                if not near or min(near) > distance + eps:
                    continue
                if not math.isfinite(values[i]) or not weights[i] > 0:
                    continue
                k_step = min(int(math.floor((min(near) + eps) / step)), n_side - 1)
                rows[n_side - 1 - k_step if r["code"] == ca else n_side + k_step].append(
                    (float(weights[i]), float(values[i]))
                )
    means = [math.fsum(w * v for w, v in r) / math.fsum(w for w, _ in r) if r else None for r in rows]
    return {"n": [len(r) for r in rows], "mean": means, "contacts": contacts, "holes": len(with_contacts)}


def same_steps(r: dict, ref: dict) -> None:
    assert r["n"] == ref["n"] and r["contacts"] == ref["contacts"] and r["holes"] == ref["holes"]
    assert [m is None for m in r["mean"]] == [m is None for m in ref["mean"]]
    for got, want in zip(r["mean"], ref["mean"], strict=True):
        assert got is None or got == pytest.approx(want, rel=REL)


# --- acceptance of ТЗ 8.3 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("weight", ["length", "none", "field"])
@pytest.mark.parametrize("grades", [(1.0, 3.0), (0.1, 0.3)])
def test_t83_2_means_of_the_steps_are_exact(weight: str, grades: tuple[float, float]) -> None:
    """Т8.3-2: A is 1.0 everywhere and B is 3.0 — the means of the steps −D … 0 are 1 and of 0 … D are 3
    exactly, whatever the lengths of the samples (0.1 and 0.3 too: no rounding of the sums shows)."""
    rng = np.random.default_rng(83)
    parts = []
    for h in range(7):
        na, nb = int(rng.integers(70, 120)), int(rng.integers(70, 120))
        codes = [A] * na + [B] * nb if h % 2 == 0 else [B] * nb + [A] * na
        parts.append(hole_of(codes, rng.choice([0.1, 0.3, 0.7], na + nb), start=float(rng.choice([0.0, 0.3])), hole=h))
    hole, frm, to, codes = join(*parts)
    values = np.where(codes == A, grades[0], grades[1])
    flt = SampleFilter(weight=weight, weight_field="DENSITY" if weight == "field" else None)
    r = chart(hole, frm, to, codes, values, flt, weight_values=rng.uniform(2.4, 3.1, len(hole)))
    assert r["edges"] == [float(k) for k in range(-20, 21)] and len(r["mean"]) == len(r["n"]) == 40
    assert (r["distance"], r["step"], r["contacts"], r["holes"]) == (20.0, 1.0, 7, 7)
    assert min(r["n"]) > 0  # every step has samples
    assert r["mean"][:20] == [grades[0]] * 20 and r["mean"][20:] == [grades[1]] * 20  # exactly, not approx
    assert r["a"] == {"label": "ORE", "n": sum(r["n"][:20]), "mean": grades[0]}
    assert r["b"] == {"label": "SCHIST", "n": sum(r["n"][20:]), "mean": grades[1]}
    assert r["weighted"] is (weight != "none") and r["counts"]["in_steps"] == sum(r["n"])
    json.dumps(r, allow_nan=False)


@pytest.mark.parametrize(("distance", "step"), [(20.0, 1.0), (7.0, 0.5), (10.0, 3.0), (2.0, 5.0)])
@pytest.mark.parametrize("weight", ["length", "none"])
def test_steps_against_a_plain_count_on_a_hard_table(distance: float, step: float, weight: str) -> None:
    hole, frm, to, codes, values, lengths = table()
    r = chart(hole, frm, to, codes, values, SampleFilter(weight=weight), length=lengths, distance=distance, step=step)
    w = lengths if weight == "length" else np.ones(len(hole))
    ref = reference(hole, frm, to, codes, values, w, A, B, distance, step)
    same_steps(r, ref)
    assert r["contacts"] > 20 and sum(r["n"]) > 100
    c = r["counts"]
    assert c["rows"] == len(hole) and c["no_depth"] == 8 and c["overlap"] == 4
    assert c["rows"] - c["no_depth"] - c["overlap"] == c["used"] + c["missing"]
    assert c["no_domain"] > 0 and c["in_steps"] == sum(r["n"]) == r["a"]["n"] + r["b"]["n"]
    # the sides: the mean of all their samples
    for side, steps in ((r["a"], slice(0, len(r["n"]) // 2)), (r["b"], slice(len(r["n"]) // 2, None))):
        n, mean = r["n"][steps], r["mean"][steps]
        assert side["n"] == sum(n)
        if weight == "none":
            total = sum(k * m for k, m in zip(n, mean, strict=True) if k)
            assert side["mean"] == pytest.approx(total / sum(n), rel=REL)
    json.dumps(r, allow_nan=False)


def test_the_order_b_to_a_is_the_mirror() -> None:
    """Domains asked as B, A give the steps of A, B in the reverse order, bit for bit; a contact B over A down
    the hole puts its samples on the same sides as A over B does."""
    hole, frm, to, codes, values, lengths = table(seed=11)
    r = chart(hole, frm, to, codes, values, length=lengths, distance=6.0, step=0.5)
    m = chart(hole, frm, to, codes, values, length=lengths, distance=6.0, step=0.5, a="SCHIST", b="ORE")
    assert m["mean"] == r["mean"][::-1] and m["n"] == r["n"][::-1] and m["edges"] == r["edges"]
    assert (m["a"], m["b"], m["contacts"], m["holes"]) == (r["b"], r["a"], r["contacts"], r["holes"])
    assert m["counts"] == r["counts"]
    # A over B and B over A: the side is that of the domain, the distance — from the contact
    lengths = [1.0] * 6
    down = hole_of([A, A, A, B, B, B], lengths, hole=0)
    up = hole_of([B, B, B, A, A, A], lengths, hole=1)
    values = np.array([1.0, 2.0, 4.0, 10.0, 20.0, 40.0])
    one = chart(*down, values, distance=3.0)
    two = chart(*up, values[[5, 4, 3, 2, 1, 0]], distance=3.0)
    assert one["mean"] == two["mean"] == [1.0, 2.0, 4.0, 10.0, 20.0, 40.0] and one["n"] == two["n"] == [1] * 6


def test_a_sample_does_not_go_beyond_the_neighbouring_contact() -> None:
    """A A A | B | A A A | B B, 1 m samples, D = 20 m: every sample is taken once — at the nearest contact of
    its own run; the contacts beyond the run do not see it."""
    hole, frm, to, codes = hole_of([A, A, A, B, A, A, A, B, B], [1.0] * 9)
    values = np.arange(1.0, 10.0)
    r = chart(hole, frm, to, codes, values, SampleFilter(weight="none"))
    assert (r["contacts"], r["holes"], sum(r["n"])) == (3, 1, 9)
    assert r["n"][17:23] == [1, 2, 3, 2, 1, 0] and sum(r["n"][:17]) == sum(r["n"][23:]) == 0
    # A: 0.5 m — samples 3, 5, 7; 1.5 m — 2, 6; 2.5 m — 1.  B: 0.5 m — 4 (between two contacts: once), 8; 1.5 m — 9
    assert r["mean"][17:23] == [1.0, 4.0, 5.0, 6.0, 9.0, None]
    assert r["a"] == {"label": "ORE", "n": 6, "mean": 4.0} and r["b"] == {"label": "SCHIST", "n": 3, "mean": 7.0}
    # a run of another domain between A and B: neither a contact nor a way to one
    hole, frm, to, codes = hole_of([A, A, G, B, B, A], [1.0] * 6)
    r = chart(hole, frm, to, codes, np.arange(1.0, 7.0), distance=20.0)
    assert (r["contacts"], sum(r["n"])) == (1, 3)  # B B | A only: the first two A are behind GRANITE
    assert r["n"][19:22] == [1, 1, 1] and r["mean"][19:22] == [6.0, 5.0, 4.0]


def test_a_gap_in_sampling_is_not_a_contact() -> None:
    values = np.array([1.0, 1.0, 3.0, 3.0])
    a = hole_of([A, A], [1.0, 1.0])
    for gap, contacts in ((0.5, 0), (2e-6, 0), (5e-7, 1), (0.0, 1)):
        hole, frm, to, codes = join(a, hole_of([B, B], [1.0, 1.0], start=2.0 + gap))
        r = chart(hole, frm, to, codes, values)
        assert (r["contacts"], r["holes"], sum(r["n"])) == (contacts, contacts, 4 * contacts), gap
        if not contacts:  # found nothing: an answer without steps, not a refusal; an empty step is not 0
            assert set(r["mean"]) == {None} and r["a"]["mean"] is None and r["b"] == {"label": "SCHIST", "n": 0,
                                                                                       "mean": None}  # fmt: skip
            assert r["counts"]["used"] == 4 and r["counts"]["in_steps"] == 0
    # a sample without a domain between A and B is not a contact either
    hole, frm, to, codes = hole_of([A, A, -1, B, B], [1.0] * 5)
    r = chart(hole, frm, to, codes, np.ones(5))
    assert r["contacts"] == 0 and r["counts"]["no_domain"] == 1
    # … but a sample without a value keeps its place in the run: the contact is there, the sample is not counted
    hole, frm, to, codes = hole_of([A, A, B, B], [1.0] * 4)
    r = chart(hole, frm, to, codes, np.array([1.0, NAN, NAN, 3.0]), SampleFilter(weight="none"))
    assert (r["contacts"], r["n"][18:22], r["mean"][18:22]) == (1, [1, 0, 0, 1], [1.0, None, None, 3.0])
    assert r["counts"]["missing"] == 2 and r["a"]["mean"] == 1.0 and r["b"]["mean"] == 3.0


def test_shuffling_the_holes_does_not_change_the_result() -> None:
    hole, frm, to, codes, values, lengths = table(seed=3)
    r = chart(hole, frm, to, codes, values, length=lengths)
    rng = np.random.default_rng(5)
    for _ in range(3):  # the rows in any order: bit for bit
        p = rng.permutation(len(hole))
        assert chart(hole[p], frm[p], to[p], codes[p], values[p], length=lengths[p]) == r
    # the holes under other numbers: the same samples in the steps, the sums in another order
    other = rng.permutation(int(hole.max()) + 1)[hole]
    s = chart(other, frm, to, codes, values, length=lengths)
    assert (s["n"], s["contacts"], s["holes"], s["counts"]) == (r["n"], r["contacts"], r["holes"], r["counts"])
    assert [m is None for m in s["mean"]] == [m is None for m in r["mean"]]
    assert [m for m in s["mean"] if m is not None] == pytest.approx([m for m in r["mean"] if m is not None], rel=REL)
    assert (s["a"]["mean"], s["b"]["mean"]) == pytest.approx((r["a"]["mean"], r["b"]["mean"]), rel=REL)


def test_threads_give_the_same_bits() -> None:
    rng = np.random.default_rng(1)
    n_holes, per = 9000, 8  # three strips of 4096 holes
    hole = np.repeat(np.arange(n_holes), per)
    frm = np.tile(np.arange(per) * 1.5, n_holes)
    codes = rng.integers(0, 3, n_holes * per).astype(np.int32)
    values = rng.lognormal(0.0, 1.0, n_holes * per)
    one = chart(hole, frm, frm + 1.5, codes, values, distance=5.0, step=0.75)
    for threads in (2, 4, 8):
        assert chart(hole, frm, frm + 1.5, codes, values, distance=5.0, step=0.75, threads=threads) == one
    assert one["contacts"] > 5000 and one["holes"] > 4096


# --- the steps ---------------------------------------------------------------------------------------------


def test_steps_and_edges() -> None:
    assert steps_of(20.0, 1.0).edges == [float(k) for k in range(-20, 21)]
    assert steps_of(2, 0.5).edges == [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0]
    s = steps_of(0.3, 0.1)  # 0.3 / 0.1 is 2.9999999999999996: three steps, not four
    assert s.n_side == 3 and s.edges == pytest.approx([-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3], abs=1e-15)
    assert s.edges[0] == -0.3 and s.edges[-1] == 0.3 and s.edges[3] == 0.0
    # D is not a whole number of steps: the outermost step is shorter and ends at D
    assert steps_of(2.5, 1.0).edges == [-2.5, -2.0, -1.0, 0.0, 1.0, 2.0, 2.5]
    assert steps_of(2.0, 5.0).edges == [-2.0, 0.0, 2.0]
    assert steps_of(MAX_STEPS * 0.1, 0.1).n_side == MAX_STEPS


def test_a_middle_on_the_edge_of_a_step_goes_to_the_farther_step_on_both_sides() -> None:
    """1 m samples and steps of 0.5 m: every middle is on an edge of a step, a hair above or below it as the
    float noise of the depths has it — all of them belong to the step that starts there, on both sides."""
    parts = []
    for h, start in enumerate([0.1 + 0.2, 0.7, 1.1 * 3, 12.3, 1e3 + 0.1]):
        frm = start + np.arange(12) * 1.0
        frm = frm + np.where(np.arange(12) % 2 == 0, 0.0, 1e-13)  # noise of both signs between the rows
        to = np.concatenate((frm[1:], [frm[-1] + 1.0]))
        parts.append((np.full(12, h), frm, to, np.array([A] * 6 + [B] * 6, dtype=np.int32)))
    hole, frm, to, codes = join(*parts)
    r = chart(hole, frm, to, codes, np.ones(len(hole)), distance=6.0, step=0.5)
    # the middles are 0.5, 1.5, … 5.5 m from the contact: steps 1, 3, … 11 of 12 on each side
    b_side = r["n"][12:]
    assert b_side == [0, 5] * 6 and r["n"][:12] == b_side[::-1]
    # a middle at D exactly is inside, in the outermost step; the next sample is outside
    r = chart(hole, frm, to, codes, np.ones(len(hole)), distance=2.5, step=1.0)
    assert r["edges"] == [-2.5, -2.0, -1.0, 0.0, 1.0, 2.0, 2.5] and r["n"] == [5] * 6
    r = chart(hole, frm, to, codes, np.ones(len(hole)), distance=2.0, step=1.0)
    assert r["n"] == [5] * 4


def test_distances_of_the_samples() -> None:
    """`domain_contacts`: the distance of the middle of every sample and its side."""
    from bf_core.composites import intervals as iv

    hole, frm, to, codes = hole_of([G, A, A, B, B, B, A], [2.0, 1.0, 3.0, 1.0, 1.0, 2.0, 4.0], start=10.0)
    lines = iv.build_lines(hole, frm, to)
    c = contact.domain_contacts(lines, codes, A, B, reach=2.5)
    assert (c.contacts, c.holes) == (2, 1)
    # the contacts are at 16 and 20: A — 12.5 (3.5 m: beyond the reach), 14.5; B — 16.5, 17.5, 19; A — 22
    assert c.side.tolist() == [-1, -1, 0, 1, 1, 1, 0]
    assert c.signed()[2:].tolist() == [-1.5, 0.5, 1.5, 1.0, -2.0] and np.isnan(c.signed()[:2]).all()


# --- the selection, refusals, progress ---------------------------------------------------------------------


def test_the_selection_of_values_applies() -> None:
    hole, frm, to, codes = hole_of([A, A, A, B, B, B], [1.0, 1.0, 2.0, 2.0, 1.0, 1.0])
    values = np.array([-1.0, 0.0, 2.0, 8.0, 50.0, 0.5])
    flt = SampleFilter(weight="length", negatives="half_abs", exclude_zero=True, top_cut=10.0, bottom_cut=0.6)
    r = chart(hole, frm, to, codes, values, flt, distance=4.0)
    # A: −1 → 0.5 is below the bottom cut, 0 is a zero, 2 stays; B: 8, 50 → 10 (capped), 0.5 is below the cut
    # the middles of 2 and of 8 are 1 m from the contact — on the edge of a step: the step 1 … 2 m
    assert r["n"] == [0, 0, 1, 0, 0, 1, 1, 0] and r["mean"] == [None, None, 2.0, None, None, 8.0, 10.0, None]
    c = r["counts"]
    assert (c["rows"], c["negative"], c["zero_excluded"], c["bottom_excluded"], c["capped"], c["used"]) == (
        6, 1, 1, 2, 1, 3)  # fmt: skip
    assert r["b"]["mean"] == pytest.approx((8.0 * 2 + 10.0 * 1) / 3, rel=REL) and r["weighted"] is True
    # the weight is the length by default (ТЗ 8.3); a weight field; a sample without a weight is left out
    values = np.array([1.0, 2.0, 4.0, 3.0, 5.0, 9.0])
    r = chart(hole, frm, to, codes, values, distance=4.0, step=4.0)
    assert r["mean"] == pytest.approx([(1 + 2 + 4 * 2) / 4, (3 * 2 + 5 + 9) / 4], rel=REL) and r["n"] == [3, 3]
    r = chart(hole, frm, to, codes, values, SampleFilter(weight="length_field", weight_field="SG"), distance=4.0,
              step=4.0, weight_values=np.array([2.0, NAN, 3.0, 1.0, 0.0, 2.0]))  # fmt: skip
    assert r["mean"] == pytest.approx([(1 * 2 + 4 * 6) / 8, (3 * 2 + 9 * 2) / 4], rel=REL) and r["n"] == [2, 2]
    assert r["counts"]["bad_weight"] == 2


def test_refusals() -> None:
    hole, frm, to, codes = hole_of([A, A, B, B], [1.0] * 4)
    v = np.ones(4)
    for key, value in (("a", None), ("a", ""), ("b", None), ("a", 5), ("b", "QUARTZ"), ("a", "ore")):
        refused("bad_param", key, lambda k=key, x=value: chart(hole, frm, to, codes, v, **{k: x}))
    e = refused("bad_param", "b", lambda: chart(hole, frm, to, codes, v, a="ORE", b="ORE"))
    assert "различаться" in e.message
    for key in ("distance", "step"):
        for value in (0.0, -1.0, NAN, math.inf, "5", True):
            refused("bad_param", key, lambda k=key, x=value: chart(hole, frm, to, codes, v, **{k: x}))
    e = refused("bad_param", "step", lambda: chart(hole, frm, to, codes, v, distance=50.2, step=0.1))
    assert str(MAX_STEPS) in e.message and "шаг" in e.hint
    assert len(chart(hole, frm, to, codes, v, distance=50.0, step=0.1)["n"]) == 2 * MAX_STEPS
    r = chart(hole, frm, to, codes, v, distance=None, step=None)  # the keys of the settings left empty: 20 m, 1 m
    assert (r["distance"], r["step"], len(r["n"])) == (20.0, 1.0, 40)
    # nothing to count: the hint says what the rows were left out for
    e = refused("no_values", None, lambda: chart(hole, frm, to, codes, np.full(4, NAN)))
    assert "без значения — 4" in e.hint
    refused("no_values", None, lambda: chart(*(np.zeros(0) for _ in range(4)), np.zeros(0)))
    by_field = SampleFilter(weight="field", weight_field="SG")
    refused("bad_param", "weight_field", lambda: chart(hole, frm, to, codes, v, by_field))  # no column of weights
    with pytest.raises(ValueError, match="codes"):
        chart(hole, frm, to, codes[:3], v)
    with pytest.raises(ValueError, match="values"):
        chart(hole, frm, to, codes, v[:3])


def test_progress_and_cancellation() -> None:
    hole, frm, to, codes, values, lengths = table(seed=2)
    seen: list[float] = []
    r = chart(hole, frm, to, codes, values, length=lengths, tick=seen.append)
    assert seen == sorted(seen) and seen[0] >= 0.0 and seen[-1] == 1.0 and len(seen) >= 5
    assert r == chart(hole, frm, to, codes, values, length=lengths)

    class Cancelled(Exception):
        pass

    def stop(f: float) -> None:
        if f > 0.5:
            raise Cancelled

    with pytest.raises(Cancelled):
        chart(hole, frm, to, codes, values, length=lengths, tick=stop)


def test_inputs_are_not_changed_and_any_integer_holes_do() -> None:
    hole, frm, to, codes, values, lengths = table(seed=4)
    kept = [a.copy() for a in (hole, frm, to, codes, values, lengths)]
    for a in (hole, frm, to, codes, values, lengths):
        a.setflags(write=False)  # the cached columns of `stats.source` are read-only
    r = chart(hole, frm, to, codes, values, length=lengths)
    for a, b in zip((hole, frm, to, codes, values, lengths), kept, strict=True):
        assert np.array_equal(a, b, equal_nan=True)
    assert chart(hole.astype(np.int32), frm, to, codes, values, length=lengths) == r
    assert chart(hole * 1_000_003 + 17, frm, to, codes, values, length=lengths) == r  # ids of a database
