"""Names of the sections of a series (ТЗ 8.1.2, Т8.1-1; ADR 0016 §2; подшаг 10.2): a safe subset of the Python
format, the defaults of 8.1.2, a repeated name — 422 «добавьте {k}», the table of a series.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from bf_core.sections.geometry import SeriesError, SeriesGeometry, series_geometry
from bf_core.sections.names import check_template, default_template, section_names, series_table
from tests import section_cases as cases


def meta(**params: Any) -> dict[str, Any]:
    return {"type": "vertical", "p1": [1000.0, 5000.0, 0.0], "az": 90.0, "s0": 0.0, "s1": 100.0, "step": 25.0,
            **params}  # fmt: skip


def test_t8_1_1_names() -> None:
    assert section_names(meta()) == ["5000N", "5025N", "5050N", "5075N", "5100N"]
    data = {c["id"]: c for c in cases.load()["series"]}
    assert [s["name"] for s in data["t8_1_1"]["sections"]] == ["5000N", "5025N", "5050N", "5075N", "5100N"]
    assert [s["name"] for s in data["abut_hard"]["sections"]][:3] == ["-1234.5E", "-1234.2E", "-1233.9E"]
    assert [s["name"] for s in data["plan"]["sections"]][:3] == ["400", "390", "380"]
    assert data["t8_1_5"]["sections"][1]["name"] == "+20"
    assert [c["name"] for c in cases.load()["polylines"]] == ["+0", "+0", "+0"]


def test_defaults() -> None:
    assert section_names(meta(az=270.0)) == ["5000N", "4975N", "4950N", "4925N", "4900N"]
    assert section_names(meta(az=0.0)) == ["1000E", "975E", "950E", "925E", "900E"]
    assert section_names(meta(az=180.0)) == ["1000E", "1025E", "1050E", "1075E", "1100E"]
    assert section_names(meta(az=30.0)) == ["+0", "+25", "+50", "+75", "+100"]
    assert section_names(meta(p2=[1000.0, 6000.0], az=None)) == ["1000E", "975E", "950E", "925E", "900E"]
    plan = {"type": "plan", "p1": [0.0, 0.0, 400.0], "s0": 0.0, "s1": 30.0, "step": 10.0}
    assert section_names(plan) == ["400", "390", "380", "370"]
    assert section_names(meta(s0=-0.0, s1=0.0, az=45.0)) == ["+0"]  # not "-0"
    polyline = {"type": "polyline", "polyline": [[0.0, 0.0], [5.0, 5.0]]}
    assert section_names(polyline) == ["+0"]
    g = series_geometry(meta())
    assert isinstance(g, SeriesGeometry) and default_template(g) == "{Y:.0f}N"


def test_default_precision_grows() -> None:
    """Δ under 1 m: the default gives the same names at .0f — its precision grows, then it goes by s."""
    g = series_geometry(meta(step=0.5, towards=0.25, away=0.25))
    assert isinstance(g, SeriesGeometry) and default_template(g) == "{Y:.1f}N"
    assert section_names(g)[:3] == ["5000.0N", "5000.5N", "5001.0N"]
    tiny = series_geometry(meta(step=0.01, s1=1.0, towards=0.01, away=0.01))
    assert isinstance(tiny, SeriesGeometry) and default_template(tiny) == "{Y:.2f}N"
    assert len(set(section_names(tiny))) == tiny.count == 101
    flat = meta(type="inclined", phi=0.0, p1=[0.0, 0.0, 300.0], step=10.0)  # A = 90°, but Y does not change
    assert section_names(flat) == ["+0", "+10", "+20", "+30", "+40", "+50", "+60", "+70", "+80", "+90", "+100"]
    assert series_table(flat)["count"] == 11


def test_user_templates() -> None:
    assert section_names(meta(template="{k}")) == ["1", "2", "3", "4", "5"]
    assert section_names(meta(), "Р-{k:02d}") == ["Р-01", "Р-02", "Р-03", "Р-04", "Р-05"]
    assert section_names(meta(template="{s:+.1f} м"))[:2] == ["+0.0 м", "+25.0 м"]
    assert section_names(meta(template="{{{k}}} {X:.0f}/{Y:,.0f}"))[0] == "{1} 1000/5,000"
    assert section_names(meta(template="{Z:>6.2f}|{k:<3}|"))[0] == "  0.00|1  |"
    assert section_names(meta(template="{Y:.3e}"))[1] == "5.025e+03"
    assert section_names(meta(template=""))[0] == "5000N"  # empty — the default


@pytest.mark.parametrize(
    "template",
    [
        "{X.__class__}",
        "{X.__class__.__mro__}",
        "{0}",
        "{}",
        "{k!r}",
        "{k!s}",
        "{k!a}",
        "{X[0]}",
        "{name}",
        "{s:{k}}",
        "{k:999}",
        "{s:.25f}",
        "{s:d}",
        "{k:c}",
        "{s:s}",
        "{s:q}",
        "{",
        "}",
        "{k",
        "   ",
        "a\n{k}",
        "x" * 201,
        123,
        None,
    ],
)
def test_bad_templates(template: Any) -> None:
    with pytest.raises(SeriesError) as e:
        check_template(template)
    err = e.value
    assert err.code == "bad_series" and err.field == "template" and err.hint
    assert any("а" <= ch <= "я" for ch in err.message.lower()), err.message


def test_bad_template_of_a_series() -> None:
    with pytest.raises(SeriesError) as e:
        section_names(meta(template="{k!r}"))
    assert e.value.field == "template"


def test_repeated_name() -> None:
    with pytest.raises(SeriesError) as e:
        section_names(meta(template="Разрез"))
    assert "1 и 2" in e.value.message and "{k}" in e.value.hint
    with pytest.raises(SeriesError) as e:
        section_names(meta(step=0.3, towards=0.15, away=0.15, s1=3.0, template="{Y:.0f}N"))
    assert "«5000N»" in e.value.message and "{k}" in e.value.hint and e.value.code == "bad_series"
    assert section_names(meta(template="Разрез", s1=0.0)) == ["Разрез"]  # one section


def test_table() -> None:
    t = series_table(meta(towards=10.0, away=15.0, overrides={"2": {"away": 3.0}}))
    assert t["count"] == 5 and "segments" not in t
    assert t["sections"][2] == {"k": 2, "name": "5050N", "s": 50.0, "origin": [1000.0, 5050.0, 0.0],
                                "towards": 10.0, "away": 3.0}  # fmt: skip
    pl = series_table({"type": "polyline", "polyline": [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0]], "corridor": 4.0})
    assert pl == {"count": 1, "segments": 2,
                  "sections": [{"k": 0, "name": "+0", "s": 0.0, "origin": [0.0, 0.0, 0.0], "towards": 4.0,
                                "away": 4.0}]}  # fmt: skip


def test_table_of_5000_sections_is_fast() -> None:
    big = meta(p1=[512345.678, 7012345.123, 0.0], s1=2.0 * 4999, step=2.0)
    series_table(big)
    best = 1e9
    for _ in range(3):
        started = time.perf_counter()
        t = series_table(big)
        best = min(best, time.perf_counter() - started)
    print(f"\ntable of 5 000 sections: {best * 1e3:.1f} ms")
    assert t["count"] == 5000 and len({s["name"] for s in t["sections"]}) == 5000
    assert best <= 0.05


def test_no_numba_before_bf_ready() -> None:
    """The API builds the table of a series without numba (ADR 0002 «Быстрый старт»)."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from bf_core.sections.names import series_table\n"
        "series_table({'type': 'polyline', 'polyline': [[0, 0], [1, 1]]})\n"
        "series_table({'type': 'inclined', 'p1': [0, 0, 0], 'az': 3, 'phi': 40, 's1': 100, 'step': 5})\n"
        "assert 'numba' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=str(cases.DATA.parents[3]))
