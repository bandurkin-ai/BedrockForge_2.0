"""The exchange of variogram models: JSON and the parameters of GSLIB (ТЗ 8.6.4; ADR 0022 §12; Э16, 16.7).
Т8.6-8: export and import keep every parameter bit for bit and the text byte for byte; a file written by hand
(comments, Fortran exponents, CRLF, a BOM, cp1251) is read; a broken file is refused as {code, message, hint}
with the line at fault.
"""

from __future__ import annotations

import json
import math
import struct
from typing import Any

import numpy as np
import pytest

from bf_core.geostat.variogram import exchange as ex
from bf_core.geostat.variogram.model import Model, Structure, parse_model
from bf_core.stats.weighted import StatsError

AWKWARD = [0.1 + 0.2, 1.0 / 3.0, 2.0**0.5, 1e-7, 123456.789, 5e-324, 1.7976931348623157e308, 0.30000000000000004,
           100.0, 2.5, 1e16, 6.02214076e23, 0.1, 1e-300, 4.9e-5, 299792.458]  # fmt: skip


def bits(x: float) -> bytes:
    return struct.pack("<d", x)


def same(a: Model, b: Model) -> bool:
    """Every parameter bit for bit."""
    if (bits(a.c0), a.common_axes, len(a.structures)) != (bits(b.c0), b.common_axes, len(b.structures)):
        return False
    return all(
        s.type == t.type and all(bits(x) == bits(y) for x, y in zip((s.c, *s.ranges, *s.angles),
                                                                       (t.c, *t.ranges, *t.angles), strict=True))
        for s, t in zip(a.structures, b.structures, strict=True)
    )  # fmt: skip


def random_model(rng: np.random.Generator) -> Model:
    def number() -> float:
        if rng.random() < 0.3:
            return float(rng.choice(AWKWARD))
        return float(rng.uniform(0.001, 1000.0) * 10.0 ** int(rng.integers(-3, 4)))

    common = bool(rng.random() < 0.5)
    first = (float(rng.uniform(0, 360)), float(rng.uniform(-90, 90)), float(rng.uniform(-180, 180)))
    structures = []
    for _ in range(int(rng.integers(0, 4))):
        ang = first if common else (float(rng.uniform(0, 360)), float(rng.uniform(-90, 90)),
                                    float(rng.uniform(-180, 180)))  # fmt: skip
        structures.append(Structure(str(rng.choice(["spherical", "exponential", "gaussian"])), number(),
                                    number(), number(), number(), *ang))  # fmt: skip
    if len(structures) < 2:
        common = True  # what a file can say: the axes are common when the angles are equal
    return Model(number() if rng.random() < 0.8 else 0.0, tuple(structures), common)


THREE = Model(
    0.05,
    (
        Structure("spherical", 0.25, 40.0, 25.0, 8.0, 30.0, -20.0, 40.0),
        Structure("exponential", 0.3, 90.0, 60.0, 15.0, 30.0, -20.0, 40.0),
        Structure("gaussian", 0.4, 220.0, 130.0, 30.5, 30.0, -20.0, 40.0),
    ),
)
THREE_GSLIB = (
    "3 0.05                                  -nst, nugget effect\n"
    "1 0.25 30.0 -20.0 40.0                  -it, cc, ang1, ang2, ang3\n"
    "40.0 25.0 8.0                           -a_hmax, a_hmin, a_vert\n"
    "2 0.3 30.0 -20.0 40.0                   -it, cc, ang1, ang2, ang3\n"
    "90.0 60.0 15.0                          -a_hmax, a_hmin, a_vert\n"
    "3 0.4 30.0 -20.0 40.0                   -it, cc, ang1, ang2, ang3\n"
    "220.0 130.0 30.5                        -a_hmax, a_hmin, a_vert\n"
)


# --- GSLIB --------------------------------------------------------------------------------------------------------


def test_gslib_text_of_a_model() -> None:
    """ТЗ 8.6.4: `nst c0`, `it cc ang1 ang2 ang3`, `a1 a2 a3` — the block of a GSLIB parameter file."""
    assert ex.to_gslib(THREE) == THREE_GSLIB
    assert ex.to_gslib(Model(0.3)) == "0 0.3                                   -nst, nugget effect\n"
    assert same(ex.from_gslib(THREE_GSLIB), THREE) and ex.from_gslib(THREE_GSLIB).common_axes is True
    long = Model(1.0 / 3.0, (Structure("spherical", 2.0**0.5, 1.0 / 7.0, 100.0 / 3.0, 1e-7, 0.5, -1.0 / 3.0),))
    text = ex.to_gslib(long)  # the numbers do not fit the column: the comment follows after two spaces
    assert text.splitlines()[1] == "1 1.4142135623730951 0.5 -0.3333333333333333 0.0  -it, cc, ang1, ang2, ang3"
    assert text.splitlines()[2] == "0.14285714285714285 33.333333333333336 1e-07  -a_hmax, a_hmin, a_vert"
    assert text.isascii() and text.endswith("\n") and "\r" not in text and same(ex.from_gslib(text), long)


def test_t8_6_8_gslib_round_trip_is_bit_for_bit() -> None:
    """Т8.6-8: export → import gives every parameter bit for bit, and the text again byte for byte."""
    rng = np.random.default_rng(868)
    for _ in range(500):
        m = random_model(rng)
        text = ex.to_gslib(m)
        back = ex.from_gslib(text)
        assert same(back, m), text
        assert ex.to_gslib(back) == text
        assert same(ex.from_gslib(text.encode("ascii")), m)  # the bytes of a file
        assert same(ex.from_gslib(text.replace("\n", "\r\n")), m)  # written on Windows
    for x in AWKWARD:  # every awkward number in every place
        m = Model(x, (Structure("gaussian", x, x, x, x, 30.0, -20.0, 40.0),))
        assert same(ex.from_gslib(ex.to_gslib(m)), m)


@pytest.mark.xfail(
    strict=True,
    reason="rotation._turn: ((a % 360) + 360) % 360 changes an angle already in range (0.1 → 0.09999999999999432);"
    " the fix — fmod, then + 360 only when negative — is for rotation.py, frames.json `rules` and frame.ts (16.6)",
)
def test_an_angle_in_its_range_is_kept_bit_for_bit() -> None:
    """Т8.6-8 for the angles a user types: an ang1 in [0; 360) and an ang3 in (−180; 180] are not changed."""
    s = Structure("spherical", 1.0, 10.0, 10.0, 10.0, 0.1 + 0.2, 0.0, -0.1)
    assert bits(s.ang1) == bits(0.1 + 0.2) and bits(s.ang3) == bits(-0.1)


def test_gslib_written_by_hand_is_read() -> None:
    text = (
        "﻿# модель по Au, домен 2\r\n"
        "\r\n"
        "   2    0.20d0          \\nst, nugget effect\r\n"
        "1\t0.5   -30.0   0.0   540      -it,cc,ang1,ang2,ang3\r\n"
        "         1.2E+02  60  2.D+01    -a_hmax, a_hmin, a_vert\r\n"
        "! вторая структура\r\n"
        "  3.0  .3  330  0  180   it cc ang1 ang2 ang3 (3 = gaussian)\r\n"
        "  400.  200.  5d1\r\n"
        "остаток файла параметров, который модели не касается\r\n"
        "5 5 5\r\n"
    )
    m = ex.from_gslib(text)
    assert m.c0 == 0.2 and [s.type for s in m.structures] == ["spherical", "gaussian"]
    assert m.structures[0].as_dict() == {"type": "spherical", "c": 0.5, "ang1": 330.0, "ang2": 0.0, "ang3": 180.0,
                                         "a1": 120.0, "a2": 60.0, "a3": 20.0}  # fmt: skip
    assert m.structures[1].ranges == (400.0, 200.0, 50.0) and m.structures[1].angles == (330.0, 0.0, 180.0)
    assert m.common_axes is True  # the angles are equal once normalised
    for raw in (text.encode("utf-8"), text.lstrip("﻿").encode("cp1251"), text.encode("utf-16")):
        assert same(ex.from_gslib(raw), m)
    own = ex.from_gslib("2 0\n1 0.5 0 0 0\n10 10 10\n2 0.5 45 0 0\n20 20 20\n")
    assert own.common_axes is False and own.structures[1].angles == (45.0, 0.0, 0.0) and own.c0 == 0.0
    assert same(ex.from_gslib("0 1.5"), Model(1.5)) and same(ex.from_gslib("0.0 1.5\n"), Model(1.5))  # nst as 0.0


@pytest.mark.parametrize(
    ("text", "line", "words"),
    [
        ("", None, ["пуст"]),
        ("  \n# только комментарий\n\n", None, ["пуст"]),
        ("-nst, nugget effect\n", 1, ["Строка 1", "nst c0"]),
        ("1\n1 1 0 0 0\n10 10 10\n", 1, ["Строка 1", "2 числа", "прочитано 1"]),
        ("# x\n\n1 0.1\n", 4, ["не хватает строк", "nst = 1", "3"]),
        ("2 0.1\n1 0.5 0 0 0\n10 10 10\n2 0.4 0 0 0\n", 5, ["не хватает строк", "5", "найдено 4"]),
        ("1 0.1\n1 0.9 0 0\n10 10 10\n", 2, ["Строка 2", "it cc ang1 ang2 ang3", "5 чисел", "прочитано 4"]),
        ("1 0.1\n1 0.9 0 0 0\n10 10\n", 3, ["Строка 3", "a1 a2 a3", "3 числа", "прочитано 2"]),
        ("1 0,1\n1 0,9 0 0 0\n10 10 10\n", 1, ["Строка 1", "прочитано 1"]),
        ("1 0.1\n1, 0.9, 0, 0, 0\n10 10 10\n", 2, ["Строка 2", "прочитано 0"]),
        ("1.5 0.1\n", 1, ["Строка 1", "nst", "целым"]),
        ("-1 0.1\n", 1, ["Строка 1", "nst"]),
        ("\n4 0.1\n" + "1 0.2 0 0 0\n10 10 10\n" * 4, 2, ["Строка 2", "4", "не больше 3"]),
        ("1 0.1\n4 0.9 0 0 0\n10 10 10\n", 2, ["Строка 2", "it = 4", "степенная"]),
        ("1 0.1\n5 0.9 0 0 0\n10 10 10\n", 2, ["Строка 2", "it = 5", "эффект дыры"]),
        ("1 0.1\n0 0.9 0 0 0\n10 10 10\n", 2, ["Строка 2", "it"]),
        ("1 0.1\n1.5 0.9 0 0 0\n10 10 10\n", 2, ["Строка 2", "it"]),
        ("1 -0.1\n1 0.9 0 0 0\n10 10 10\n", 1, ["Строка 1", "Самородок"]),
        ("1 0.1\n1 -0.9 0 0 0\n10 10 10\n", 2, ["Строка 2", "Вклад"]),
        ("1 0.1\n1 0.9 0 95 0\n10 10 10\n", 2, ["Строка 2", "ang2"]),
        ("1 0.1\n1 0.9 0 0 0\n10 0 10\n", 3, ["Строка 3", "a2", "больше нуля"]),
        ("1 0.1\n1 0.9 0 0 0\n10 nan 10\n", 3, ["Строка 3", "прочитано 1"]),
        ("1 inf\n", 1, ["Строка 1", "прочитано 1"]),
        ("1 1e999\n", 1, ["Строка 1", "прочитано 1"]),
        ("1 0.1\n1 0.9 0 0 0\n1_0 10 10\n", 3, ["Строка 3", "прочитано 0"]),
        ('{"c0": 0.1}', 1, ["Строка 1", "JSON"]),
    ],
)
def test_a_broken_gslib_file_is_refused_with_a_hint(text: str, line: int | None, words: list[str]) -> None:
    """422 `bad_file {line?}` (e16-api.md): the line at fault in the message and in `line`, a hint how to mend."""
    with pytest.raises(StatsError) as e:
        ex.from_gslib(text)
    err = e.value
    assert err.code == "bad_file" and all(w in err.message for w in words), err.message
    assert getattr(err, "line", "absent") == line and err.param is None
    assert err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    if "it =" not in err.message and "не больше" not in err.message:  # how the file is written
        assert "it cc ang1 ang2 ang3" in err.hint or "JSON" in err.hint, err.hint
    if "больше нуля" in err.message:  # the hint of the value first
        assert err.hint.startswith("Задайте положительное число.")


def test_gslib_refuses_what_is_not_text() -> None:
    for data in (b"\x00\x01\x02\x03PK\x03\x04", "1 0.1\x00", None, 12, ["1 0.1"]):
        with pytest.raises(StatsError) as e:
            ex.from_gslib(data)  # type: ignore[arg-type]
        assert e.value.code == "bad_file" and e.value.hint and "текст" in e.value.message


# --- JSON ---------------------------------------------------------------------------------------------------------

META: dict[str, Any] = {
    "v": 1,
    "source": {"set_id": 12, "table": "COMP_2M", "field": "AU", "domain": {"field": "DOMAIN", "values": ["Руда 1"]},
               "weights": "DECL_W"},
    "transform": "nscore",
    "type": "semivariogram",
    "sigma2": 1.0 / 3.0,
    **THREE.as_dict(),
    "calc": {"lag": 12.5, "nlag": 15, "lag_tol": 6.25, "seed": 12345, "sampled": None},
    "fit": {"weights": "n_h", "residual": 0.1 + 0.2, "n": 45, "fixed": ["c0"]},
    "back": None,
    "inputs": {"tables": [{"name_key": "comp_2m", "rev": 7}], "set_rev": 3},
    "sha256": "ab" * 32,
    "algo": "vario-1",
    "color": "#1F4E79",
}  # fmt: skip


def test_json_text_of_a_model() -> None:
    """ADR 0022 §12: the fields of `meta` without `inputs`, the numbers in their shortest exact form."""
    text = ex.to_json(META)
    doc = json.loads(text)
    assert list(doc)[0] == "kind" and doc["kind"] == "variogram_model" and "inputs" not in doc
    assert list(doc)[1:] == [k for k in META if k != "inputs"]  # the order of `meta` is kept
    assert doc["source"]["domain"]["values"] == ["Руда 1"] and "Руда 1" in text  # not \\u-escaped
    assert '"sigma2": 0.3333333333333333' in text and '"residual": 0.30000000000000004' in text
    assert text.endswith("}\n") and "\r" not in text and "NaN" not in text
    assert "inputs" in META  # the argument is not changed


def test_t8_6_8_json_round_trip_is_bit_for_bit() -> None:
    """Т8.6-8: export → import gives the parameters bit for bit, and the text again byte for byte."""
    text = ex.to_json(META)
    meta = ex.from_json(text)
    assert meta == {k: v for k, v in META.items() if k != "inputs"} and "kind" not in meta
    assert bits(meta["sigma2"]) == bits(1.0 / 3.0) and bits(meta["fit"]["residual"]) == bits(0.1 + 0.2)
    assert same(parse_model(meta), THREE) and ex.to_json(meta) == text
    assert ex.from_json(text.encode("utf-8")) == meta and ex.from_json(("﻿" + text).encode("utf-8")) == meta
    assert ex.from_json(text.replace("\n", "\r\n")) == meta
    rng = np.random.default_rng(869)
    for _ in range(300):
        m = random_model(rng)
        doc = ex.to_json({"v": 1, **m.as_dict(), "sigma2": float(rng.choice(AWKWARD))})
        back = ex.from_json(doc)
        assert same(parse_model(back), m) and ex.to_json(back) == doc
    # Only the model: what another program may write. The model is checked and made canonical.
    bare = ex.from_json('{"c0": 0.1, "structures": [{"type": 2, "c": "0.9", "a1": 50, "ang1": -30}]}')
    assert bare == {"c0": 0.1, "common_axes": True, "structures": [
        {"type": "exponential", "c": 0.9, "ang1": 330.0, "ang2": 0.0, "ang3": 0.0, "a1": 50.0, "a2": 50.0, "a3": 50.0}
    ]}  # fmt: skip


def test_json_has_no_gaps_written_as_numbers() -> None:
    """A missing number is null in the file, never NaN and never a zero; numpy values are written as numbers."""
    meta = {**THREE.as_dict(), "fit": {"residual": math.nan, "n": np.int64(45), "h": np.array([1.5, np.inf])},
            "sigma2": np.float64(0.25), "flags": (True, None)}  # fmt: skip
    doc = json.loads(ex.to_json(meta))
    assert doc["fit"] == {"residual": None, "n": 45, "h": [1.5, None]} and doc["sigma2"] == 0.25
    assert doc["flags"] == [True, None]


@pytest.mark.parametrize(
    ("text", "line", "words"),
    [
        ("", None, ["пуст"]),
        ("   \n", None, ["пуст"]),
        ('{"c0": 0.1, "structures": [', 1, ["JSON", "строка 1"]),
        ('{\n  "c0": 0.1,\n  "structures": [}\n', 3, ["JSON", "строка 3"]),
        ("3 0.05\n1 0.25 30 -20 40\n40 25 8\n", 1, ["JSON"]),
        ("[1, 2, 3]", 1, ["не является моделью вариограммы"]),
        ('{"kind": "trend", "ang1": 30}', None, ["не является моделью вариограммы", "trend"]),
        ('{"kind": "variogram_model"}', None, ["structures"]),
        ('{"name": "x"}', None, ["structures"]),
        ('{"c0": -1, "structures": []}', None, ["Самородок"]),
        ('{"c0": 0, "structures": [{"type": "power", "c": 1, "a1": 5}]}', None, ["Структура 1", "power"]),
        ('{"c0": 0, "structures": [{"type": 1, "c": 1, "a1": 5}, {"type": 1, "c": 1, "a1": 0}]}', None,
         ["Структура 2", "больше нуля"]),
        ('{"c0": 0, "structures": [{"type": 1, "c": 1, "a1": 5}] , "common_axes": 1}', None, ["общие оси"]),
        ('{"c0": 0, "structures": ' + json.dumps([{"type": 1, "c": 1, "a1": 5}] * 4) + "}", None, ["не больше 3"]),
        ('{"c0": NaN, "structures": []}', None, ["Самородок"]),
        ('{"type": "kriging", "c0": 0, "structures": []}', None, ["величина модели", "kriging"]),
    ],
)  # fmt: skip
def test_a_broken_json_file_is_refused_with_a_hint(text: str, line: int | None, words: list[str]) -> None:
    with pytest.raises(StatsError) as e:
        ex.from_json(text)
    err = e.value
    assert err.code == "bad_file" and all(w in err.message for w in words), err.message
    assert getattr(err, "line", "absent") == line and err.param is None
    assert err.hint and set(err.as_dict()) == {"code", "message", "hint"}


def test_to_json_refuses_a_meta_without_a_valid_model() -> None:
    for meta in ({"v": 1}, {"c0": 0.1, "structures": [{"type": "spherical", "c": 1, "a1": -1}]}, None, [1]):
        with pytest.raises(StatsError) as e:
            ex.to_json(meta)  # type: ignore[arg-type]
        assert e.value.code == "bad_param"


# --- the format of a file -------------------------------------------------------------------------------------------


def test_format_of_a_file_by_its_name_and_content() -> None:
    assert ex.FORMATS == ("json", "gslib")
    assert ex.detect_format(ex.to_json(META)) == "json" and ex.detect_format(THREE_GSLIB) == "gslib"
    assert ex.detect_format(b"\xef\xbb\xbf  \n{}") == "json" and ex.detect_format("# x\n1 0.1") == "gslib"
    assert same(parse_model(ex.read_model(ex.to_json(META))), THREE)
    assert same(parse_model(ex.read_model(THREE_GSLIB.encode())), THREE)
    assert ex.read_model(THREE_GSLIB, "gslib") == THREE.as_dict()  # of GSLIB — only the model
    assert ex.read_model(ex.to_json(META), "json")["source"]["table"] == "COMP_2M"
    assert ex.write_model(META, "json") == ex.to_json(META) and ex.write_model(META, "gslib") == THREE_GSLIB
    for call in (lambda: ex.read_model(THREE_GSLIB, "xml"), lambda: ex.write_model(META, "par")):
        with pytest.raises(StatsError) as e:
            call()
        assert e.value.code == "bad_param" and e.value.param == "format" and "JSON" in e.value.hint
    with pytest.raises(StatsError) as e:
        ex.read_model(THREE_GSLIB, "json")  # the wrong format is said plainly
    assert e.value.code == "bad_file" and "JSON" in e.value.message and "GSLIB" in e.value.hint


def test_file_name_of_an_export() -> None:
    """`GET /models/{id}/export` → `{format, filename, text}`: a name Windows accepts, Cyrillic kept."""
    assert ex.file_name("AU · Композиты", "json") == "AU · Композиты.json"
    assert ex.file_name("AU · Композиты", "gslib") == "AU · Композиты.par"
    assert ex.file_name('a/b\\c:d*e?f"g<h>i|j\tk', "json") == "a_b_c_d_e_f_g_h_i_j_k.json"
    assert ex.file_name("модель. . ", "json") == "модель.json"
    assert ex.file_name("  ", "json") == "Модель вариограммы.json"
    assert ex.file_name(None, "gslib") == "Модель вариограммы.par"
    assert ex.file_name("CON", "json") == "_CON.json" and ex.file_name("lpt1.txt", "json") == "_lpt1.txt.json"
    assert len(ex.file_name("ж" * 500, "json")) == 125
    with pytest.raises(StatsError) as e:
        ex.file_name("x", "xlsx")
    assert e.value.param == "format"


# --- the form of the API ------------------------------------------------------------------------------------------


def test_model_of_the_api() -> None:
    """e16-api.md «Модель»: `{type, c0, common_axes, structures}` for fit, back, curve, models and import."""
    kind, m = ex.api_model({"c0": "0.05", "structures": THREE.as_dict()["structures"]})
    assert kind == "semivariogram" and same(m, THREE)
    form = ex.model_form(kind, m)
    assert list(form) == ["type", "c0", "common_axes", "structures"] and form["structures"][2]["a3"] == 30.5
    assert ex.api_model({**form, "type": " NScore "})[0] == "nscore"
    assert [ex.value_type(v) for v in (None, "", "correlogram", "PAIRWISE")] == [
        "semivariogram", "semivariogram", "correlogram", "pairwise"]  # fmt: skip
    # The window imports a file: the fields → the model of the window.
    imported = ex.model_form(*ex.api_model(ex.read_model(THREE_GSLIB)))
    assert imported == {**form, "type": "semivariogram"}
    from_json = ex.model_form(*ex.api_model(ex.read_model(ex.to_json({**META, "type": "correlogram"}))))
    assert from_json["type"] == "correlogram" and from_json["structures"] == form["structures"]


@pytest.mark.parametrize(
    ("obj", "param", "words"),
    [
        (None, "model", ["Модель вариограммы"]),
        ({"type": "madogram", "structures": []}, "model.type", ["madogram"]),
        ({"c0": -1, "structures": []}, "model.c0", ["Самородок"]),
        ({"structures": [{"type": 1, "c": 1, "a1": 5}, {"type": 1, "c": 1, "a1": 5, "a2": -2}]},
         "model.structures.1.a2", ["Структура 2", "a2"]),
        ({"structures": [{"type": 1, "c": 1}]}, "model.structures.0.a1", ["Структура 1", "a1"]),
        ({"structures": "1 0.1"}, "model.structures", ["Структуры"]),
        ({"structures": [], "common_axes": "да"}, "model.common_axes", ["общие оси"]),
    ],
)  # fmt: skip
def test_a_bad_model_of_a_request_names_its_path(obj: Any, param: str, words: list[str]) -> None:
    with pytest.raises(StatsError) as e:
        ex.api_model(obj)
    assert e.value.code == "bad_param" and e.value.param == param, e.value.param
    assert all(w in e.value.message for w in words) and e.value.hint
