"""The parser of the field calculator (ТЗ 8.7.4, Т8.7-6, ADR 0023 §7): texts, error places, limits, round trip."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from bf_core.calc import (
    FUNCTIONS,
    MAX_DEPTH,
    MAX_LENGTH,
    BinOp,
    BoolOp,
    Call,
    Compare,
    Expr,
    ExprError,
    ExprLimitError,
    Field,
    In,
    Neg,
    Not,
    Num,
    Str,
    parse,
)

_FIX = json.loads((Path(__file__).parent / "data" / "blocks" / "expr_texts.json").read_text(encoding="utf-8"))
_VALID = [pytest.param(c, id=c["text"][:40]) for c in _FIX["valid"]]
_INVALID = [pytest.param(c, id=c["text"][:40]) for c in _FIX["invalid"]]


@pytest.mark.parametrize("case", _VALID)
def test_valid_texts(case: dict) -> None:
    expr = parse(case["text"])
    assert expr.to_text() == case["canonical"]
    assert list(expr.fields()) == case["fields"]
    assert list(expr.functions()) == case["functions"]
    assert expr.depth() <= MAX_DEPTH


@pytest.mark.parametrize("case", _VALID)
def test_round_trip_is_lossless(case: dict) -> None:
    """text → tree → text → tree → text: equal trees (positions aside) and a stable text."""
    first = parse(case["text"])
    text1 = first.to_text()
    second = parse(text1)
    assert second == first
    assert second.to_text() == text1


@pytest.mark.parametrize("case", _INVALID)
def test_invalid_texts_have_a_place(case: dict) -> None:
    with pytest.raises(ExprError) as info:
        parse(case["text"])
    err = info.value
    assert (err.pos, err.line, err.column, err.char, err.reason) == (
        case["pos"], case["line"], case["column"], case["char"], case["reason"]
    )
    body = err.to_body()
    assert body["code"] == "expr_syntax"
    assert body["pos"] == case["pos"]
    assert str(body["message"]).endswith(").") and f"строка {case['line']}, столбец {case['column']}" in body["message"]
    assert body["hint"], "every error carries a hint"
    assert any("а" <= c.lower() <= "я" for c in str(body["message"])), "Russian message"


def test_t87_6_import_is_a_syntax_error_without_execution() -> None:
    """Т8.7-6: `__import__('os')` is a syntax error; nothing is run, nothing is imported."""
    import sys

    sys.modules.pop("os.path.never", None)
    with pytest.raises(ExprError) as info:
        parse("__import__('os')")
    assert info.value.reason == "unknown_function" and info.value.pos == 0
    assert "Допустимые функции" in info.value.hint
    for text in ("eval('1')", "exec('1')", "open('f')", "getattr(a, 'b')", "().__class__", "a.b", "lambda: 1",
                 "__import__ ('os')", "os.system('x')", "a if b else c", "x := 1"):
        with pytest.raises(ExprError):
            parse(text)


def test_bracket_text_is_only_a_field_name() -> None:
    """Whatever stands in [ ] is a name to look up in the model (17.6 refuses an unknown one), never code."""
    assert parse("[__import__('os')]") == Field("__import__('os')")
    assert parse("[x for x in y]").fields() == ("x for x in y",)


def test_t87_6_if_example_tree() -> None:
    expr = parse("IF(AU > 1, AU*2, 0)")
    assert expr == Call(
        "IF", (Compare(">", Field("AU"), Num(1.0)), BinOp("*", Field("AU"), Num(2.0)), Num(0.0))
    )
    assert [n.kind for n in expr.walk()] == ["call", "compare", "field", "num", "binop", "field", "num", "num"]
    assert expr.fields() == ("AU",) and expr.functions() == ("IF",)


def test_precedence_and_associativity() -> None:
    assert parse("1 + 2 * 3") == BinOp("+", Num(1), BinOp("*", Num(2), Num(3)))
    assert parse("1 - 2 - 3") == BinOp("-", BinOp("-", Num(1), Num(2)), Num(3))
    assert parse("2 ^ 3 ^ 2") == BinOp("^", Num(2), BinOp("^", Num(3), Num(2)))
    assert parse("-2 ^ 2") == Neg(BinOp("^", Num(2), Num(2)))
    assert parse("2 ^ -1") == BinOp("^", Num(2), Neg(Num(1)))
    assert parse("-a * b") == BinOp("*", Neg(Field("a")), Field("b"))
    assert parse("a OR b AND c") == BoolOp("OR", (Field("a"), BoolOp("AND", (Field("b"), Field("c")))))
    assert parse("NOT a > 1 AND b") == BoolOp("AND", (Not(Compare(">", Field("a"), Num(1))), Field("b")))
    assert parse("a + 1 > b * 2") == Compare(">", BinOp("+", Field("a"), Num(1)), BinOp("*", Field("b"), Num(2)))
    assert parse("a AND (b AND c)") == parse("a AND b AND c") == BoolOp("AND", (Field("a"), Field("b"), Field("c")))


def test_words_fields_strings() -> None:
    assert parse("x in ('A', 'it''s', 2, -3)") == In(Field("x"), ("A", "it's", 2.0, -3.0))
    assert parse("[Au ppm]") == Field("Au ppm")
    assert parse("Содержание_Au1") == Field("Содержание_Au1")
    assert parse("'руда'") == Str("руда")
    assert parse("a−b") == BinOp("-", Field("a"), Field("b"))  # U+2212, the minus of the ТЗ
    assert parse("[and] + [IF]").fields() == ("and", "IF")
    assert parse("ABS(x)").to_text() == parse("abs(X)").to_text().replace("X", "x")
    with pytest.raises(ExprError):  # a non-ASCII `ı` is not the word IN
        parse("a ın ('x')")
    assert set(FUNCTIONS) == {
        "ABS", "SQRT", "LN", "LOG10", "EXP", "MIN", "MAX", "ROUND", "FLOOR", "CEIL", "CLIP", "IF", "ISNULL", "COALESCE"
    }


def test_fields_are_distinct_ignoring_case_in_order_of_use() -> None:
    expr = parse("b + A + a + [B] + c")
    assert expr.fields() == ("b", "A", "c")


def test_positions_of_nodes() -> None:
    expr = parse("  AU + ABS(-b)")
    assert isinstance(expr, BinOp)
    assert expr.pos == 2 and expr.left.pos == 2 and expr.right.pos == 7
    assert isinstance(expr.right, Call) and expr.right.args[0].pos == 11


def test_error_message_names_the_place_in_russian() -> None:
    with pytest.raises(ExprError) as info:
        parse("AU +\n  * 2")
    err = info.value
    assert (err.line, err.column, err.char) == (2, 3, "*")
    assert "строка 2, столбец 3, символ «*»" in err.message
    with pytest.raises(ExprError) as info:
        parse("AU +")
    assert "конец выражения" in info.value.message and info.value.char == ""
    with pytest.raises(ExprError) as info:
        parse("a\t$")
    assert "символ «$»" in info.value.message and info.value.column == 3


# ------------------------------------------------------------------------------------------------ limits


def test_length_limit_4000() -> None:
    assert MAX_LENGTH == 4000
    assert parse("x" + " " * 3999) == Field("x")  # exactly 4000
    long_string = "'" + "a" * 3998 + "'"
    assert len(long_string) == 4000 and isinstance(parse(long_string), Str)
    with pytest.raises(ExprLimitError) as info:
        parse("x" + " " * 4000)  # 4001
    err = info.value
    assert err.pos == 4000 and err.reason == "too_long" and err.code == "expr_syntax"
    assert "4000" in err.message and err.hint
    with pytest.raises(ExprLimitError):
        parse("1" + "+1" * 2500)  # 5001: the length is checked before anything else


def test_depth_limit_64_parentheses_and_calls() -> None:
    assert MAX_DEPTH == 64
    assert parse("(" * 64 + "x" + ")" * 64) == Field("x")
    with pytest.raises(ExprLimitError) as info:
        parse("(" * 65 + "x" + ")" * 65)
    assert info.value.reason == "too_deep" and info.value.pos == 64

    deep = parse("ABS(" * 64 + "x" + ")" * 64)
    assert deep.depth() == 64
    assert deep.to_text() == "ABS(" * 64 + "x" + ")" * 64
    with pytest.raises(ExprLimitError) as info:
        parse("ABS(" * 65 + "x" + ")" * 65)
    assert info.value.reason == "too_deep" and info.value.pos == 4 * 64 + 3  # the 65th «(»


def test_depth_limit_64_unary_power_and_chains() -> None:
    assert parse("-" * 64 + "x").depth() == 64
    with pytest.raises(ExprLimitError):
        parse("-" * 65 + "x")
    with pytest.raises(ExprLimitError):
        parse("NOT " * 65 + "x")
    assert parse("NOT " * 64 + "x").depth() == 64
    assert parse("x" + " ^ x" * 64).depth() == 64
    with pytest.raises(ExprLimitError):
        parse("x" + " ^ x" * 65)
    assert parse("x" + " + x" * 64).depth() == 64  # a chain is as high as it is long
    with pytest.raises(ExprLimitError) as info:
        parse("x" + " + x" * 65)
    assert info.value.reason == "too_deep"
    assert parse(" AND ".join(["a > 1"] * 300)).depth() == 2  # AND / OR are n-ary: long conditions are fine


def test_hostile_depth_never_overflows_the_stack() -> None:
    for text in ("(" * 3999, "-" * 3999, "NOT " * 990, "x" + "^-" * 1990 + "x", "f(" * 1990, "ABS(" * 990):
        assert len(text) <= MAX_LENGTH
        with pytest.raises(ExprError):
            parse(text)


# ------------------------------------------------------------------------------------------ round trip


def _random_tree(rng: random.Random, depth: int) -> Expr:
    if depth == 0 or rng.random() < 0.2:
        return rng.choice([
            Num(float(rng.choice([0, 1, 2, 3.5, 0.1, 1e-7, 123456.789, 1e22]))),
            Str(rng.choice(["a", "руда", "it's", "A B"])),
            Field(rng.choice(["AU", "au", "Cu", "Au ppm", "IF", "и", "x_1"])),
        ])
    sub = lambda: _random_tree(rng, depth - 1)  # noqa: E731
    kind = rng.choice(["neg", "not", "bin", "cmp", "and", "or", "in", "call"])
    if kind == "neg":
        return Neg(sub())
    if kind == "not":
        return Not(sub())
    if kind == "bin":
        return BinOp(rng.choice(["+", "-", "*", "/", "^"]), sub(), sub())
    if kind == "cmp":
        return Compare(rng.choice(["=", "<>", "<", "<=", ">", ">="]), sub(), sub())
    if kind in ("and", "or"):
        return BoolOp.of(kind.upper(), [sub() for _ in range(rng.randint(2, 4))])
    if kind == "in":
        return In(sub(), tuple(rng.choice(["A", "b'c", 1.5, -2.0, 7.0]) for _ in range(rng.randint(1, 3))))
    name = rng.choice(sorted(FUNCTIONS))
    low, high = FUNCTIONS[name]
    count = rng.randint(low, high if high is not None else low + 2)
    return Call(name, tuple(sub() for _ in range(count)))


def test_round_trip_random_trees() -> None:
    """Any normal tree → text → the same tree → the same text (precedence-aware parentheses)."""
    rng = random.Random(8704)
    for _ in range(400):
        tree = _random_tree(rng, rng.randint(1, 5))
        text = tree.to_text()
        again = parse(text)
        assert again == tree, text
        assert again.to_text() == text


def test_parenthesised_text_matches_precedence() -> None:
    for text, canonical in (
        ("a - (b + c)", "a - (b + c)"),
        ("(a * b) + c", "a * b + c"),
        ("(a + b) ^ 2", "(a + b) ^ 2"),
        ("-(a ^ 2)", "-a ^ 2"),
        ("(-a) ^ 2", "(-a) ^ 2"),
        ("NOT (a = 1)", "NOT a = 1"),
        ("(NOT a) = 1", "(NOT a) = 1"),
        ("(a OR b) AND (c OR d)", "(a OR b) AND (c OR d)"),
        ("(a AND b) OR c", "a AND b OR c"),
    ):
        assert parse(text).to_text() == canonical
        assert parse(canonical) == parse(text)


def test_nodes_are_immutable_and_hashable() -> None:
    expr = parse("AU + 1")
    with pytest.raises(AttributeError):
        expr.op = "-"  # type: ignore[attr-defined]
    assert hash(expr) == hash(parse("AU   +   1.0"))
    assert parse("1.0") == Num(1.0, pos=99)  # the position is not part of equality


def test_parse_rejects_non_strings() -> None:
    with pytest.raises(TypeError):
        parse(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse(b"1")  # type: ignore[arg-type]
