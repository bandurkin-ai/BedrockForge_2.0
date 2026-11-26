"""The mode «explicit arcs» (подшаг 21.13; ТЗ 8.11, Т8.11-4; ADR 0028 decisions 14, 32; question № 114): the reader of
MineLib UPIT sets (`.upit` + `.prec`) on files of our own made in the test, the solvers HPF and OR-Tools against the
enumeration of all subsets and scipy, the round trip of a grid graph through the files, and Т8.11-4 — the published
optima of the MineLib sets, which run when the files are in `tests/data/ext/minelib/` (they are not: the test skips)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.mine import explicit as X
from bf_core.mine import flow_ref as F
from bf_core.mine import graph as G
from bf_core.mine import hpf as H
from tests.ref import pit_brute as B
from tests.test_pit_hpf import case_3d

MINELIB = Path(__file__).resolve().parents[2] / "tests" / "data" / "ext" / "minelib"
SETS = ["newman1", "zuck_small", "kd", "marvin", "mclaughlin_limit"]  # ТЗ 8.11.6, Т8.11-4
SCIPY_MAX_NODES = 400_000  # the scipy reference is for the small sets (it is a reference, not a product)


# --- the files of the format -----------------------------------------------------------------------------------------


def write_upit(path: Path, values, *, name="demo", ids=None, header=True, eof=True, extra="") -> None:
    ids = list(range(len(values))) if ids is None else ids
    lines = [f"NAME: {name}", "TYPE: UPIT", f"NBLOCKS: {len(values)}", extra, "OBJECTIVE_FUNCTION:"] if header else []
    lines += [f"{i} {v}" for i, v in zip(ids, values, strict=True)]
    if eof:
        lines.append("EOF")
    path.write_text("\n".join(x for x in lines if x != "") + "\n", encoding="ascii")


def write_prec(path: Path, preds, *, order=None, base=0, eof=True, skip_empty=False) -> None:
    order = range(len(preds)) if order is None else order
    lines = []
    for u in order:
        if skip_empty and not preds[u]:
            continue
        lines.append(" ".join(str(x) for x in [u + base, len(preds[u]), *[q + base for q in preds[u]]]))
    if eof:
        lines.append("EOF")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def csr_lists(indptr, indices):
    return [indices[indptr[u] : indptr[u + 1]].tolist() for u in range(len(indptr) - 1)]


def brute(value, preds):
    """(the optimum, the smallest optimal closure) by the enumeration of all subsets of n ≤ 14 blocks."""
    n = len(value)
    sub = np.arange(2**n, dtype=np.int64)
    bits = [(sub >> u) & 1 for u in range(n)]
    ok = np.ones(2**n, dtype=bool)
    for u, qs in enumerate(preds):
        for q in qs:
            ok &= (bits[u] == 0) | (bits[q] == 1)
    total = sum(bits[u] * int(value[u]) for u in range(n))
    best = int(total[ok].max())
    opt = sub[ok & (total == best)]
    both = int(np.bitwise_and.reduce(opt))  # the optimal closures are closed under ∩: the smallest one
    return best, np.array([(both >> u) & 1 for u in range(n)], dtype=bool)


# --- the reader ------------------------------------------------------------------------------------------------------


def test_upit_and_prec_become_the_csr_of_the_predecessors(tmp_path):
    write_upit(tmp_path / "a.upit", [6, -2, -3, 7], extra="COMMENT: ignored")
    write_prec(tmp_path / "a.prec", [[1, 2], [], [3], []])
    p = X.read_set(tmp_path / "a.upit")
    assert p.name == "demo" and p.n == 4 and p.arcs == 3 and p.scale == 1
    assert p.value.tolist() == [6, -2, -3, 7] and p.value.dtype == np.int64
    assert p.indptr.tolist() == [0, 2, 2, 3, 3] and p.indptr.dtype == np.int64
    assert p.indices.tolist() == [1, 2, 3] and p.indices.dtype == np.int32
    s = X.solve(p)
    assert s.mined.tolist() == [True, True, True, True] and s.objective_int == 8 and s.objective == 8.0
    assert X.violations(p, s.mined) == 0 and X.violations(p, np.array([True, False, False, False])) == 2


def test_the_header_is_optional_and_the_values_may_be_decimals(tmp_path):
    write_upit(tmp_path / "b.upit", ["12.5", "-3.25", "0", "1e1"], header=False, eof=False)
    write_prec(tmp_path / "b.prec", [[1], [], [], []], eof=False)
    p = X.read_set(tmp_path / "b.upit")
    assert p.name == "b" and p.scale == 100 and p.value.tolist() == [1250, -325, 0, 1000]
    s = X.solve(p)  # 12.5 − 3.25 = 9.25 and the block of 10 on its own
    assert s.objective_int == 1250 - 325 + 1000 and s.objective == 19.25


def test_ids_in_any_order_and_one_based(tmp_path):
    values, preds = [4, -1, -2, 6, -3], [[1, 2], [], [4], [2], []]
    order = [3, 0, 4, 2, 1]
    write_upit(tmp_path / "c.upit", [values[u] for u in order], ids=order)
    write_prec(tmp_path / "c.prec", preds, order=order)
    p = X.read_set(tmp_path / "c.upit")
    assert p.value.tolist() == values and csr_lists(p.indptr, p.indices) == preds
    write_upit(tmp_path / "d.upit", values, ids=[u + 1 for u in range(5)])
    write_prec(tmp_path / "d.prec", preds, base=1, skip_empty=True)  # the blocks with no predecessors have no line
    q = X.read_set(tmp_path / "d.upit")
    assert q.value.tolist() == values and csr_lists(q.indptr, q.indices) == preds


def test_prec_by_portions_and_a_block_is_not_its_own_predecessor(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "LINES", 3)
    rng = np.random.default_rng(3)
    n = 40
    preds = [sorted(rng.choice(n, size=int(rng.integers(0, 5)), replace=False).tolist()) for _ in range(n)]
    write_upit(tmp_path / "e.upit", list(range(n)))
    order = rng.permutation(n).tolist()
    write_prec(tmp_path / "e.prec", preds, order=order)
    p = X.read_set(tmp_path / "e.upit")
    assert csr_lists(p.indptr, p.indices) == [[q for q in qs if q != u] for u, qs in enumerate(preds)]


@pytest.mark.parametrize(
    ("kind", "text", "code"),
    [
        ("upit", "NBLOCKS: 3\n0 1\n1 2\nEOF\n", "minelib_format"),  # NBLOCKS ≠ the lines
        ("upit", "0 1 9\n1 2\n", "minelib_format"),  # three tokens
        ("upit", "0 1\n1 abc\n", "minelib_format"),
        ("upit", "0 1\n0 2\n", "minelib_format"),  # an id twice
        ("upit", "0 1\n5 2\n", "minelib_format"),  # an id out of 0…n−1
        ("upit", "NBLOCKS: x\n0 1\n", "minelib_format"),
        ("upit", "NAME: empty\nEOF\n", "minelib_format"),
        ("prec", "0 1 1\n1 1\n", "minelib_format"),  # 1 predecessor declared, none given
        ("prec", "0 1 9\n1 0\n", "minelib_format"),  # a predecessor out of the set
        ("prec", "0 0\n0 0\n", "minelib_format"),  # a block twice
        ("prec", "0 x 1\n", "minelib_format"),
        ("prec", "7 0\n", "minelib_format"),  # a block out of the set
        ("prec", "0\n", "minelib_format"),
    ],
)
def test_wrong_files_are_a_422_in_russian_with_a_hint(tmp_path, kind, text, code):
    write_upit(tmp_path / "w.upit", [1, 2])
    write_prec(tmp_path / "w.prec", [[], []])
    (tmp_path / f"w.{kind}").write_text(text, encoding="ascii")
    with pytest.raises(ApiError) as e:
        X.read_set(tmp_path / "w.upit")
    assert e.value.status == 422 and e.value.code == code
    assert "MineLib" in e.value.message and e.value.hint and any("а" <= c <= "я" for c in e.value.message)


def test_a_missing_file_is_a_404(tmp_path):
    write_upit(tmp_path / "m.upit", [1])
    with pytest.raises(ApiError) as e:
        X.read_set(tmp_path / "m.upit")
    assert e.value.status == 404 and e.value.code == "minelib_no_file" and "m.prec" in e.value.message


def test_values_that_are_not_whole_at_nine_digits_are_refused(tmp_path):
    write_upit(tmp_path / "f.upit", ["0.1234567891", "1"])
    write_prec(tmp_path / "f.prec", [[], []])
    with pytest.raises(ApiError, match="целым"):
        X.read_set(tmp_path / "f.upit")


# --- the solvers on the sets ------------------------------------------------------------------------------------------


def random_set(rng, n):
    value = rng.integers(-9, 10, n)
    value[rng.random(n) < 0.15] = 0  # ties
    preds = [sorted(rng.choice(n, size=int(rng.integers(0, 4)), replace=False).tolist()) for _ in range(n)]
    return value, preds


def test_hpf_ortools_scipy_and_the_enumeration_agree_on_random_sets(tmp_path):
    rng = np.random.default_rng(114)
    nonempty = 0
    for _ in range(80):
        n = int(rng.integers(3, 14))
        value, preds = random_set(rng, n)  # cycles are allowed: the closure problem does not mind
        write_upit(tmp_path / "r.upit", value.tolist())
        write_prec(tmp_path / "r.prec", preds)
        p = X.read_set(tmp_path / "r.upit")
        best, smallest = brute(value, preds)
        h, o = X.solve(p, "hpf"), X.solve(p, "ortools")
        sp_val, sp_mined = B.scipy_closure(p.value, p.indptr, p.indices)
        assert h.objective_int == o.objective_int == sp_val == best
        assert np.array_equal(h.mined, smallest) and np.array_equal(o.mined, smallest)
        assert np.array_equal(sp_mined, smallest)
        assert X.violations(p, h.mined) == 0
        nonempty += int(best > 0)
    assert nonempty > 40  # the sets are not trivial


def test_a_grid_graph_through_the_files_is_the_same_pit(tmp_path):
    for seed, dims in [(1, (6, 5, 4)), (2, (9, 7, 5)), (3, (14, 12, 6))]:
        g = case_3d(seed, dims, kind=seed % 3)
        indptr, indices = G.predecessors(g)
        write_upit(tmp_path / "g.upit", g.value.tolist())
        write_prec(tmp_path / "g.prec", csr_lists(indptr, indices))
        p = X.read_set(tmp_path / "g.upit")
        assert np.array_equal(p.indptr, indptr) and np.array_equal(p.indices, indices)
        grid = H.solve(g)
        assert np.array_equal(X.solve(p, "hpf").mined, grid) and np.array_equal(X.solve(p, "ortools").mined, grid)
        assert np.array_equal(grid, F.solve(g))


def test_portions_do_not_change_the_answer_and_a_tick_can_cancel(tmp_path):
    g = case_3d(5, (12, 10, 6))
    indptr, indices = G.predecessors(g)
    write_upit(tmp_path / "t.upit", g.value.tolist())
    write_prec(tmp_path / "t.prec", csr_lists(indptr, indices))
    p = X.read_set(tmp_path / "t.upit")
    whole = X.solve(p).mined
    ticks: list[float] = []
    assert np.array_equal(X.solve(p, portion=7, tick=ticks.append).mined, whole)
    assert len(ticks) > 2 and all(0.0 <= f <= 1.0 for f in ticks)

    class Cancelled(Exception):
        pass

    def stop(_f: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        X.solve(p, portion=7, tick=stop)


def test_no_positive_block_means_an_empty_pit(tmp_path):
    write_upit(tmp_path / "z.upit", [-1, 0, -5])
    write_prec(tmp_path / "z.prec", [[1], [], [0]])
    p = X.read_set(tmp_path / "z.upit")
    for solver in ("hpf", "ortools"):
        s = X.solve(p, solver)
        assert not s.mined.any() and s.objective == 0.0


# --- Т8.11-4: the published optima of MineLib ------------------------------------------------------------------------


def check_set(upit: Path, prec: Path, optimum: float, *, scipy_max: int = SCIPY_MAX_NODES) -> dict:
    """HPF = OR-Tools = scipy (the values below 2³¹, small sets), the set is closed, the value is the published
    optimum to 1e−9 (relative). Returns what it measured."""
    p = X.read_set(upit, prec)
    h = X.solve(p, "hpf")
    o = X.solve(p, "ortools")
    assert np.array_equal(h.mined, o.mined) and h.objective_int == o.objective_int, "HPF and OR-Tools differ"
    assert X.violations(p, h.mined) == 0
    if p.n <= scipy_max and int(p.value[p.value > 0].sum()) + 1 < 2**31:
        val, mined = B.scipy_closure(p.value, p.indptr, p.indices)
        assert val == h.objective_int and np.array_equal(mined, h.mined), "scipy differs"
    assert abs(h.objective - optimum) <= 1e-9 * max(1.0, abs(optimum)), (h.objective, optimum)
    return {"blocks": p.n, "arcs": p.arcs, "objective": h.objective}


def manifest() -> dict:
    f = MINELIB / "manifest.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


@pytest.mark.parametrize("name", SETS)
def test_t8_11_4_minelib_optimum(name):
    upit, prec = X.set_paths(MINELIB, name)
    if not (upit.is_file() and prec.is_file()):
        pytest.skip("файл MineLib не предоставлен (вопрос № 114)")
    entry = manifest().get(name)
    if not entry or "optimum" not in entry:
        pytest.skip(f"в tests/data/ext/minelib/manifest.json нет оптимума набора «{name}» (вопрос № 114)")
    got = check_set(upit, prec, float(entry["optimum"]))
    if "blocks" in entry:
        assert got["blocks"] == int(entry["blocks"])


def test_the_check_of_t8_11_4_runs_on_a_set_of_our_own_in_the_format(tmp_path):
    rng = np.random.default_rng(4)
    value, preds = random_set(rng, 12)
    write_upit(tmp_path / "own.upit", value.tolist(), name="own")
    write_prec(tmp_path / "own.prec", preds)
    best, _ = brute(value, preds)
    got = check_set(tmp_path / "own.upit", tmp_path / "own.prec", float(best))
    assert got["blocks"] == 12 and got["objective"] == best
    with pytest.raises(AssertionError):  # a wrong «published optimum» is caught
        check_set(tmp_path / "own.upit", tmp_path / "own.prec", float(best) + 1.0)
