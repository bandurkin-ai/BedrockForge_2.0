"""Evaluation of the field calculator (ТЗ 8.7.4, Т8.7-6, ADR 0023 §7): NaN rules, categories, types, errors,
numexpr = numpy bit for bit, chunk-size independence, a row-by-row reference interpreter on random expressions."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np
import pytest

from bf_core.calc import (
    ExprDataError,
    ExprError,
    ExprFieldError,
    ExprTypeError,
    FieldInfo,
    evaluate,
    parse,
    runtime,
)
from bf_core.calc import compile as calc_compile

_FIX = json.loads((Path(__file__).parent / "data" / "blocks" / "calc_eval_cases.json").read_text(encoding="utf-8"))
SCHEMA = _FIX["schema"]
DATA = {
    k: (np.array([np.nan if x is None else x for x in v], dtype=np.float64) if k in ("AU", "CU")
        else np.array(v, dtype=np.int32))
    for k, v in _FIX["data"].items()
}  # fmt: skip
nan = float("nan")


def nums(values: list) -> np.ndarray:
    return np.array([nan if v is None else v for v in values], dtype=np.float64)


def same_bits(a: np.ndarray, b: np.ndarray) -> bool:
    """Equal bit for bit; every NaN counts as the same value."""
    na, nb = np.isnan(a), np.isnan(b)
    return bool(np.array_equal(na, nb) and np.array_equal(a[~na].view(np.int64), b[~nb].view(np.int64)))


# ------------------------------------------------------------------------------ fixture: Т8.7-6, NaN, categories


@pytest.mark.parametrize("case", [pytest.param(c, id=c["text"]) for c in _FIX["cases"]])
@pytest.mark.parametrize("chunk", [1, 4, 1 << 20])
def test_fixture_cases(case: dict, chunk: int) -> None:
    prog = calc_compile(case["text"], SCHEMA)
    got = prog.eval(DATA, chunk=chunk)
    assert prog.out_kind == case["kind"]
    if case["kind"] == "num":
        assert got.dtype == np.float64
        np.testing.assert_allclose(got, nums(case["out"]), rtol=1e-12, atol=0, equal_nan=True)
    elif case["kind"] == "bool":
        assert got.dtype == np.bool_
        assert got.tolist() == case["out"]
    else:
        assert got.dtype == np.int32
        assert got.tolist() == case["out"]
        assert list(prog.labels) == case["labels"]


def test_t87_6_if_au() -> None:
    """Т8.7-6: `IF(AU > 1, AU*2, 0)` on the whole range of values."""
    au = np.array([-5.0, 0.0, 0.999999, 1.0, 1.000001, 2.5, 1e12, nan])
    got = calc_compile("IF(AU > 1, AU*2, 0)", {"AU": "num"}).eval({"AU": au})
    np.testing.assert_array_equal(got, [0, 0, 0, 0, 2.000002, 5.0, 2e12, nan])


def test_t87_6_import_is_a_syntax_error_and_nothing_runs() -> None:
    with pytest.raises(ExprError) as err:
        calc_compile("__import__('os')", {"AU": "num"})
    assert err.value.code == "expr_syntax"
    assert err.value.to_body()["pos"] == 0


@pytest.mark.parametrize("op", ["=", "<>", "<", "<=", ">", ">="])
def test_every_comparison_with_nan_is_false(op: str) -> None:
    x = np.array([nan, nan, 1.0])
    for text in (f"X {op} 1", f"1 {op} X", f"X {op} X"):
        assert not calc_compile(text, {"X": "num"}).eval({"X": x})[:2].any(), text


def test_arithmetic_with_nan_is_nan_and_functions_too() -> None:
    x = np.array([nan, 4.0])
    texts = ["X + 1", "X * 0", "-X", "ABS(X)", "SQRT(X)", "LN(X)", "EXP(X)", "ROUND(X, 1)", "FLOOR(X)", "X ^ 2", "2 ^ X"]  # noqa: E501
    for text in texts:
        got = calc_compile(text, {"X": "num"}).eval({"X": x})
        assert np.isnan(got[0]) and not np.isnan(got[1]), text
    assert np.isnan(calc_compile("MIN(X, 1)", {"X": "num"}).eval({"X": x})[0])
    assert np.isnan(calc_compile("CLIP(X, 0, 1)", {"X": "num"}).eval({"X": x})[0])


def test_missing_is_never_turned_into_zero() -> None:
    x = np.array([nan, 1.0])
    got = calc_compile("X * 1 + 0", {"X": "num"}).eval({"X": x})
    assert np.isnan(got[0])
    assert np.isnan(calc_compile("IF(X > 0, X, 0)", {"X": "num"}).eval({"X": x})[0])


def test_if_mark_travels_up_the_condition_but_not_into_the_branches() -> None:
    a, b = np.array([nan, 5.0, 5.0]), np.array([1.0, nan, 1.0])
    cols = {"A": a, "B": b}
    sch = {"A": "num", "B": "num"}
    # B is a gap only in the branch: IF picks the other branch, no NaN
    got = calc_compile("IF(A > 0, 1, B)", sch).eval(cols)
    np.testing.assert_array_equal(got, [nan, 1.0, 1.0])
    # a gap in the condition through arithmetic, comparison, AND, NOT, IN
    for text in ("IF(A + B > 1, 1, 2)", "IF(A > 0 AND B > 0, 1, 2)", "IF(NOT (A > 0), 1, 2)", "IF(A IN (5), 1, 2)"):
        got = calc_compile(text, sch).eval(cols)
        assert np.isnan(got[0]), text
    # ISNULL decides: a condition with no gap mark
    got = calc_compile("IF(ISNULL(A), 7, A)", sch).eval(cols)
    np.testing.assert_array_equal(got, [7.0, 5.0, 5.0])
    # the gap mark of one IF does not leak into the next one
    got = calc_compile("IF(IF(A > 0, 1, 0) > 0, 10, 20)", sch).eval(cols)
    assert np.isnan(got[0]) and got[1] == 10.0


def test_if_with_a_constant_condition() -> None:
    assert calc_compile("IF(1 > 0, 3, 4)", {}).eval({}, n=3).tolist() == [3.0, 3.0, 3.0]
    assert calc_compile("IF(0, 3, 4)", {}).eval({}, n=2).tolist() == [4.0, 4.0]


# ---------------------------------------------------------------------------------------- division, overflow, inf


def test_division_by_zero_overflow_and_domain_errors_give_nan_never_inf() -> None:
    x = np.array([0.0, 1.0, -1.0, 1e308])
    cols = {"X": x}
    sch = {"X": "num"}
    expected = {
        "1 / X": [nan, 1.0, -1.0, 1e-308],
        "X / 0": [nan, nan, nan, nan],
        "X * 10": [0.0, 10.0, -10.0, nan],
        "X * X * 10": [0.0, 10.0, 10.0, nan],
        "LN(X)": [nan, 0.0, nan, math.log(1e308)],
        "LOG10(X)": [nan, 0.0, nan, 308.0],
        "SQRT(X)": [0.0, 1.0, nan, math.sqrt(1e308)],
        "EXP(X * 1000)": [1.0, nan, 0.0, nan],
        "X ^ -1": [nan, 1.0, -1.0, 1e-308],
        "X ^ 0.5": [0.0, 1.0, nan, math.sqrt(1e308)],
        "ROUND(X, 2)": [0.0, 1.0, -1.0, 1e308],
        "1 / (X * 1e300 * 1e300)": [nan, nan, nan, nan],  # an overflowed divisor is a gap, not 1 / inf = 0
        "1 / (X / 0)": [nan, nan, nan, nan],
    }
    for text, want in expected.items():
        got = calc_compile(text, sch).eval(cols)
        assert not np.isinf(got).any(), text
        np.testing.assert_allclose(got, want, rtol=1e-12, equal_nan=True, err_msg=text)


def test_an_infinite_value_is_not_a_value_in_comparisons_and_input() -> None:
    x = np.array([0.0, 2.0, np.inf, -np.inf])
    cols = {"X": x}
    sch = {"X": "num"}
    assert calc_compile("1 / X > 0.1", sch).eval(cols).tolist() == [False, True, False, False]
    assert calc_compile("X > 1", sch).eval(cols).tolist() == [False, True, False, False]
    np.testing.assert_array_equal(calc_compile("X + 0", sch).eval(cols), [0.0, 2.0, nan, nan])
    got = calc_compile("IF(1 / X > 0.1, 1, 0)", sch).eval(cols)
    np.testing.assert_array_equal(got, [nan, 1.0, nan, nan])  # 1/0 is a gap in the condition


def test_round_half_away_from_zero_digits_and_constants() -> None:
    x = np.array([2.5, -2.5, 0.5, -0.4, 1.005, 1234.5678, 4.5e15, 1e300, nan, 0.49999999999999994])
    cols = {"X": x}
    sch = {"X": "num"}
    np.testing.assert_array_equal(
        calc_compile("ROUND(X, 0)", sch).eval(cols), [3, -3, 1, 0, 1, 1235, 4.5e15, 1e300, nan, 0]
    )
    got = calc_compile("ROUND(X, 2)", sch).eval(cols)
    assert got[5] == 1234.57 and got[1] == -2.5
    assert calc_compile("ROUND(X, -2)", sch).eval(cols)[5] == 1200.0
    assert calc_compile("ROUND(X, 3 - 1)", sch).eval(cols)[5] == 1234.57  # a constant expression is fine
    assert not np.signbit(calc_compile("ROUND(X, 0)", sch).eval(cols)[3])  # −0.4 → 0, not −0


@pytest.mark.parametrize("digits", ["X", "1.5", "16", "-16", "1 / 0", "'a'", "AU > 1"])
def test_round_digits_must_be_a_constant_integer(digits: str) -> None:
    with pytest.raises(ExprTypeError) as err:
        calc_compile(f"ROUND(X, {digits})", {"X": "num", "AU": "num"})
    body = err.value.to_body()
    assert body["code"] == "expr_type" and body["hint"] and body["message"]


# ---------------------------------------------------------------------------------------- categories


def test_category_empty_numeric_labels_and_cat_against_cat() -> None:
    sch = {"A": ["x", "y", "z"], "B": ["z", "x"], "N": ["10", "2,5", "abc"]}
    cols = {
        "A": np.array([0, 1, 2, -1, 0], dtype=np.int32),
        "B": np.array([1, 0, 0, 1, -1], dtype=np.int32),
        "N": np.array([0, 1, 2, -1, 0], dtype=np.int16),
    }
    assert calc_compile("A = B", sch).eval(cols).tolist() == [True, False, True, False, False]  # by label
    assert calc_compile("A <> B", sch).eval(cols).tolist() == [False, True, False, False, False]  # empty: never
    assert calc_compile("N > 3", sch).eval(cols).tolist() == [True, False, False, False, True]
    assert calc_compile("N = 2.5", sch).eval(cols).tolist() == [False, True, False, False, False]
    assert calc_compile("3 < N", sch).eval(cols).tolist() == [True, False, False, False, True]  # «abc» is a gap
    assert calc_compile("N IN (10, 2.5)", sch).eval(cols).tolist() == [True, True, False, False, True]
    assert calc_compile("A IN ('x', 'z')", sch).eval(cols).tolist() == [True, False, True, False, True]
    got = calc_compile("IF(N > 3, 1, 0)", sch).eval(cols)
    assert got[0] == 1.0 and got[1] == 0.0 and np.isnan(got[2]) and np.isnan(got[3])  # «abc» and empty are gaps


def test_category_results_merge_dictionaries() -> None:
    sch = {"A": ["x", "y"], "B": ["y", "z"]}
    cols = {"A": np.array([0, 1, -1], dtype=np.int32), "B": np.array([1, 0, 0], dtype=np.int32)}
    prog = calc_compile("IF(A = 'x', B, A)", sch)
    assert prog.out_kind == "cat" and prog.labels == ("y", "z", "x") or prog.labels == ("x", "y", "z")
    got = prog.eval(cols)
    labels = prog.labels
    # row 0: A = 'x' → B = 'z'; row 1: A = 'y' → A = 'y'; row 2: A empty → gap → empty
    assert [labels[c] if c >= 0 else None for c in got] == ["z", "y", None]
    prog = calc_compile("COALESCE(A, B)", sch)
    got = prog.eval(cols)
    assert [prog.labels[c] if c >= 0 else None for c in got] == ["x", "y", "y"]


def test_category_codes_outside_the_dictionary_are_an_error() -> None:
    prog = calc_compile("A = 'x'", {"A": ["x"]})
    for bad in (np.array([0, 1], dtype=np.int32), np.array([-2, 0], dtype=np.int32)):
        with pytest.raises(ExprDataError) as err:
            prog.eval({"A": bad})
        assert err.value.to_body()["code"] == "expr_data"
    with pytest.raises(ExprDataError):
        prog.eval({"A": np.array([0.0, 0.0])})  # not integer codes


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("AU", "num"), ("AU + 1", "num"), ("AU > 1", "bool"), ("NOT AU > 1", "bool"), ("ISNULL(AU)", "bool"),
        ("DOMAIN", "cat"), ("'A'", "cat"), ("IF(AU > 1, 'a', 'b')", "cat"), ("IF(AU > 1, AU > 2, ISNULL(AU))", "bool"),
        ("COALESCE(AU, 0)", "num"), ("ROUND(AU, 1)", "num"), ("DOMAIN IN ('OX')", "bool"), ("1 / 4", "num"),
    ],
)  # fmt: skip
def test_result_types(text: str, kind: str) -> None:
    prog = calc_compile(text, SCHEMA)
    assert prog.out_kind == kind
    got = prog.eval(DATA)
    assert got.shape == (6,)
    assert got.dtype == {"num": np.float64, "bool": np.bool_, "cat": np.int32}[kind]


@pytest.mark.parametrize(
    "text",
    [
        "(AU > 1) + 1", "-(AU > 1)", "DOMAIN + 1", "SQRT(DOMAIN)", "NOT DOMAIN",
        "DOMAIN < 'A'", "'A' = 1", "'A' + 1", "AU IN ('a')", "IF(AU > 1, 1, 'a')",
        "IF(AU > 1, AU > 1, 2)",
        "COALESCE(AU, 'a')", "ISNULL(AU > 1)", "AU AND DOMAIN", "MAX(AU, DOMAIN)", "IF('a', 1, 2)",
    ],
)  # fmt: skip
def test_type_errors_are_russian_with_a_hint_and_a_place(text: str) -> None:
    with pytest.raises(ExprTypeError) as err:
        calc_compile(text, SCHEMA)
    body = err.value.to_body()
    assert body["code"] == "expr_type" and body["reason"] in ("type", "round_digits")
    assert body["hint"] and any("а" <= c.lower() <= "я" for c in body["message"])
    assert body["line"] == 1 and body["column"] >= 1


# ---------------------------------------------------------------------------------------- names


def test_unknown_field_is_reported_at_its_place_with_a_hint() -> None:
    text = "AU + CUU * 2"
    with pytest.raises(ExprFieldError) as err:
        calc_compile(text, SCHEMA)
    body = err.value.to_body()
    assert body["code"] == "expr_field" and body["reason"] == "unknown_field"
    assert body["pos"] == text.index("CUU") and body["column"] == 6
    assert "«CUU»" in body["message"] and "«CU»" in body["hint"] and "AU" in body["hint"]


def test_unknown_field_leftmost_first_and_empty_schema() -> None:
    with pytest.raises(ExprFieldError) as err:
        calc_compile("B + A", {"X": "num"})
    assert err.value.pos == 0
    with pytest.raises(ExprFieldError) as err2:
        calc_compile("A", {})
    assert "нет полей" in err2.value.hint


def test_names_ignore_case_and_ambiguity_is_an_error() -> None:
    prog = calc_compile("au + [Cu Ppm]", {"AU": "num", "CU PPM": "num"})
    assert prog.fields_used == ("AU", "CU PPM")
    got = prog.eval({"AU": np.array([1.0]), "CU PPM": np.array([2.0])})
    assert got.tolist() == [3.0]
    with pytest.raises(ExprFieldError) as err:
        calc_compile("au", {"Au": "num", "AU": "num"})
    assert err.value.reason == "ambiguous_field"
    assert calc_compile("Au", {"Au": "num", "AU": "num"}).fields_used == ("Au",)  # an exact name wins


def test_compile_accepts_a_tree_and_the_source_text_for_places() -> None:
    text = "AU + ZZ"
    with pytest.raises(ExprFieldError) as err:
        calc_compile(parse(text), SCHEMA, text=text)
    assert err.value.pos == 5
    with pytest.raises(ExprFieldError) as err2:
        calc_compile(parse(text), SCHEMA)  # no text: the place is not known
    assert err2.value.pos == 0
    prog = calc_compile(parse("AU * 2"), {"AU": FieldInfo.num()})
    assert prog.eval({"AU": np.array([2.0])}).tolist() == [4.0]
    assert calc_compile("A", {"A": FieldInfo.cat(["q"])}).labels == ("q",)


def test_syntax_errors_come_through_compile() -> None:
    with pytest.raises(ExprError) as err:
        calc_compile("AU +", {"AU": "num"})
    assert err.value.code == "expr_syntax"


# ---------------------------------------------------------------------------------------- columns


def test_input_dtypes_masks_and_memmaps(tmp_path: Path) -> None:
    prog = calc_compile("A + B", {"A": "num", "B": "num"})
    a32 = np.array([1.5, 2.5], dtype=np.float32)
    bi = np.array([1, 2], dtype=np.int64)
    assert prog.eval({"A": a32, "B": bi}).tolist() == [2.5, 4.5]
    masked = np.ma.masked_array([1, 2, 3], mask=[False, True, False])
    got = prog.eval({"A": masked, "B": np.array([1.0, 1.0, 1.0])})
    assert got[0] == 2.0 and np.isnan(got[1]) and got[2] == 4.0
    path = tmp_path / "a.f8"
    np.arange(10, dtype=np.float64).tofile(path)
    mm = np.memmap(path, dtype=np.float64, mode="r")
    assert prog.eval({"A": mm, "B": mm}, chunk=3).tolist() == [2.0 * i for i in range(10)]
    codes = np.ma.masked_array([0, 1, 0], mask=[False, False, True], dtype=np.int16)
    assert calc_compile("C = 'x'", {"C": ["x", "y"]}).eval({"C": codes}).tolist() == [True, False, False]
    with pytest.raises(ExprDataError):
        prog.eval({"A": np.array(["a", "b"]), "B": bi})


def test_input_is_not_modified() -> None:
    x = np.array([1.0, np.inf, nan, 3.0])
    keep = x.copy()
    calc_compile("IF(X > 1, X * 2, X)", {"X": "num"}).eval({"X": x})
    assert same_bits(x, keep)


def test_missing_and_ragged_columns_and_constants() -> None:
    prog = calc_compile("A + B", {"A": "num", "B": "num"})
    with pytest.raises(ExprDataError) as err:
        prog.eval({"A": np.zeros(2)})
    assert "«B»" in err.value.message
    with pytest.raises(ExprDataError):
        prog.eval({"A": np.zeros(2), "B": np.zeros(3)})
    assert calc_compile("1 / 4", {}).eval({}, n=3).tolist() == [0.25] * 3
    assert calc_compile("AU", {"AU": "num"}).eval({"AU": np.zeros(0)}).shape == (0,)
    assert prog.eval({"A": np.zeros(2), "B": np.zeros(2), "X": np.zeros(7)}).shape == (2,)  # extra columns are fine
    with pytest.raises(ExprDataError):
        prog.eval({"A": np.zeros(2), "B": np.zeros(2)}, engine="gpu")


def test_iter_eval_and_eval_chunk() -> None:
    prog = calc_compile("AU * 2", {"AU": "num"})
    parts = list(prog.iter_eval({"AU": np.arange(k, k + 3, dtype=np.float64)} for k in (0, 3, 6)))
    assert np.concatenate(parts).tolist() == [2.0 * i for i in range(9)]
    assert prog.eval_chunk({"AU": np.array([1.0, 2.0])}).tolist() == [2.0, 4.0]


# ---------------------------------------------------------------------------------------- numexpr = numpy


def _random_columns(n: int, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    cols = {}
    for k in ("X", "Y", "Z"):
        v = rng.normal(size=n) * 10.0 ** rng.integers(-6, 7, size=n)
        v[rng.integers(0, n, n // 20)] = np.nan
        v[rng.integers(0, n, n // 20)] = 0.0
        v[rng.integers(0, n, n // 50)] = -0.0
        v[rng.integers(0, n, n // 50)] = 1e200
        cols[k] = v
    return cols


_BIT_EXPRS = [
    "X + Y * Z",
    "X / 3 + Y / 7 - Z / 0.1",
    "(X - Y) * (X + Y) / 7",
    "X / Y",
    "7 / X",
    "-X * -Y - -Z",
    "SQRT(ABS(X)) + FLOOR(Y) - CEIL(Z)",
    "X * 1e300 * 1e300",
    "X / (Y * Z * 1e300 * 1e300) + 1 / (X / 0)",
    "ABS(X / (Y + 1)) * 0.1 + 1 / 3",
    "2 / 3 + X / (1 / 3)",
    "IF(X > Y, X / 3, Z * 0.1)",
    "ROUND(X / 7, 3) + MAX(Y, Z) * LN(ABS(X) + 1)",
    "IF(X > 0 AND Y < 1, SQRT(X * Y), COALESCE(Z, X) / 3)",
    "EXP(X / 1e6) ^ 2 + CLIP(Y, -1, 1)",
]


@pytest.mark.skipif(runtime.numexpr_module() is None, reason="numexpr is not installed")
@pytest.mark.parametrize("text", _BIT_EXPRS)
def test_numexpr_equals_numpy_bit_for_bit(text: str) -> None:
    cols = _random_columns(300_017, 11)
    prog = calc_compile(text, {"X": "num", "Y": "num", "Z": "num"})
    a = prog.eval(cols, engine="numpy")
    b = prog.eval(cols, engine="numexpr")
    c = prog.eval(cols, chunk=4099)
    assert same_bits(a, b), text
    assert same_bits(a, c), text
    assert not np.isinf(a).any()


@pytest.mark.skipif(runtime.numexpr_module() is None, reason="numexpr is not installed")
def test_numexpr_is_really_used_for_arithmetic_and_only_there(monkeypatch: pytest.MonkeyPatch) -> None:
    ne = runtime.numexpr_module()
    calls: list[str] = []
    real = ne.evaluate

    def counting(code: str, *a: object, **kw: object) -> np.ndarray:
        calls.append(code)
        return real(code, *a, **kw)

    monkeypatch.setattr(ne, "evaluate", counting)
    sch = {"X": "num", "Y": "num"}
    cols = {"X": np.arange(5.0), "Y": np.ones(5)}
    arith = calc_compile("X * 2 + Y / 3", sch)
    assert arith.uses_numexpr
    arith.eval(cols)
    assert len(calls) == 1 and "v0" in calls[0] and "c0" in calls[0]
    calls.clear()
    arith.eval(cols, engine="numpy")
    assert calls == []
    other = calc_compile("LN(X + 1) ^ 2", sch)  # only X + 1 is arithmetic
    other.eval(cols)
    assert len(calls) == 1
    calls.clear()
    calc_compile("LN(X) ^ 2", sch).eval(cols)
    assert calls == []


def test_without_numexpr_the_results_are_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    cols = _random_columns(5_000, 3)
    sch = {"X": "num", "Y": "num", "Z": "num"}
    with_ne = {t: calc_compile(t, sch).eval(cols) for t in _BIT_EXPRS}
    monkeypatch.setattr(runtime, "numexpr_module", lambda: None)
    monkeypatch.setattr(evaluate, "numexpr_module", lambda: None)
    for t in _BIT_EXPRS:
        assert same_bits(calc_compile(t, sch).eval(cols), with_ne[t]), t
    with pytest.raises(ExprDataError):
        calc_compile("X + 1", sch).eval(cols, engine="numexpr")


@pytest.mark.parametrize("text", _BIT_EXPRS)
def test_chunks_of_one_and_n_give_the_same_result(text: str) -> None:
    cols = _random_columns(181, 5)
    prog = calc_compile(text, {"X": "num", "Y": "num", "Z": "num"})
    whole = prog.eval(cols)
    for chunk in (1, 7, 64, 181, 1000):
        assert same_bits(prog.eval(cols, chunk=chunk), whole), (text, chunk)


# ---------------------------------------------------------------------------------------- reference interpreter


class _Ref:
    """Row-by-row reference of the NaN rules: a value is a float, or (truth, mark) for a condition."""

    def __init__(self, row: dict[str, float]) -> None:
        self.row = row

    def num(self, t: tuple) -> float:
        v = self.ev(t)
        assert isinstance(v, float)
        return v

    @staticmethod
    def fin(x: float) -> float:
        return x if math.isfinite(x) or math.isnan(x) else nan

    def ev(self, t: tuple) -> object:  # noqa: C901
        k = t[0]
        if k == "num":
            return float(t[1])
        if k == "f":
            return self.row[t[1]]
        if k == "neg":
            return -self.num(t[1])
        if k == "bin":
            a, b = self.num(t[2]), self.num(t[3])
            if math.isnan(a) or math.isnan(b):
                return nan
            if t[1] == "/":
                return nan if b == 0 else self.fin(a / b)
            return self.fin({"+": a + b, "-": a - b, "*": a * b}[t[1]])
        if k == "cmp":
            a, b = self.num(t[2]), self.num(t[3])
            mark = math.isnan(a) or math.isnan(b)
            ops = {"=": a == b, "<>": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}
            return (False if mark else ops[t[1]], mark)
        if k == "not":
            v, m = self.ev(t[1])  # type: ignore[misc]
            return (not v, m)
        if k in ("and", "or"):
            (va, ma), (vb, mb) = self.ev(t[1]), self.ev(t[2])  # type: ignore[misc]
            return ((va and vb) if k == "and" else (va or vb), ma or mb)
        if k == "isnull":
            return (math.isnan(self.num(t[1])), False)
        if k == "if":
            (v, m), a, b = self.ev(t[1]), self.num(t[2]), self.num(t[3])  # type: ignore[misc]
            return nan if m else (a if v else b)
        if k == "coalesce":
            a, b = self.num(t[1]), self.num(t[2])
            return b if math.isnan(a) else a
        if k in ("min", "max"):
            a, b = self.num(t[1]), self.num(t[2])
            if math.isnan(a) or math.isnan(b):
                return nan
            return min(a, b) if k == "min" else max(a, b)
        if k == "abs":
            return abs(self.num(t[1]))
        raise AssertionError(k)


def _gen(rng: random.Random, depth: int, kind: str) -> tuple[tuple, str]:
    """A random tree and its text; `kind` is `n` (number) or `c` (condition)."""
    if kind == "n":
        if depth == 0 or rng.random() < 0.25:
            if rng.random() < 0.7:
                name = rng.choice("ABC")
                return ("f", name), name
            v = rng.choice([0, 1, 2, 0.5, 3])
            return ("num", v), str(v)
        pick = rng.choice(["+", "-", "*", "/", "neg", "if", "coalesce", "min", "max", "abs"])
        if pick in ("+", "-", "*", "/"):
            (a, sa), (b, sb) = _gen(rng, depth - 1, "n"), _gen(rng, depth - 1, "n")
            return ("bin", pick, a, b), f"({sa} {pick} {sb})"
        if pick == "neg":
            a, sa = _gen(rng, depth - 1, "n")
            return ("neg", a), f"(-{sa})"
        if pick == "abs":
            a, sa = _gen(rng, depth - 1, "n")
            return ("abs", a), f"ABS({sa})"
        if pick == "if":
            (c, sc), (a, sa), (b, sb) = _gen(rng, depth - 1, "c"), _gen(rng, depth - 1, "n"), _gen(rng, depth - 1, "n")
            return ("if", c, a, b), f"IF({sc}, {sa}, {sb})"
        (a, sa), (b, sb) = _gen(rng, depth - 1, "n"), _gen(rng, depth - 1, "n")
        return (pick, a, b), f"{pick.upper()}({sa}, {sb})"
    pick = rng.choice(["cmp", "cmp", "not", "and", "or", "isnull"]) if depth > 0 else "cmp"
    if pick == "cmp":
        (a, sa), (b, sb) = _gen(rng, max(depth - 1, 0), "n"), _gen(rng, max(depth - 1, 0), "n")
        op = rng.choice(["=", "<>", "<", "<=", ">", ">="])
        return ("cmp", op, a, b), f"{sa} {op} {sb}"
    if pick == "not":
        a, sa = _gen(rng, depth - 1, "c")
        return ("not", a), f"NOT ({sa})"
    if pick == "isnull":
        a, sa = _gen(rng, depth - 1, "n")
        return ("isnull", a), f"ISNULL({sa})"
    (a, sa), (b, sb) = _gen(rng, depth - 1, "c"), _gen(rng, depth - 1, "c")
    return (pick, a, b), f"(({sa}) {pick.upper()} ({sb}))"


def test_random_expressions_agree_with_the_row_by_row_reference() -> None:
    rng = random.Random(8704)
    n = 23
    data_rng = np.random.default_rng(8704)
    cols = {k: np.round(data_rng.normal(size=n) * 3, 1) for k in "ABC"}
    for v in cols.values():
        v[data_rng.integers(0, n, 5)] = np.nan
        v[data_rng.integers(0, n, 3)] = 0.0
    sch = {k: "num" for k in "ABC"}
    checked = 0
    for case in range(300):
        kind = "n" if case % 3 else "c"
        tree, text = _gen(rng, 4, kind)
        prog = calc_compile(text, sch)
        got = prog.eval(cols, chunk=5)
        for i in range(n):
            ref = _Ref({k: float(v[i]) for k, v in cols.items()}).ev(tree)
            if kind == "c":
                assert bool(got[i]) == ref[0], (text, i)  # type: ignore[index]
            else:
                want = ref
                assert (math.isnan(got[i]) and math.isnan(want)) or got[i] == want, (text, i, got[i], want)  # type: ignore[arg-type]
            checked += 1
    assert checked == 300 * n
