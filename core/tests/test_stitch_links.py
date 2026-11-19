"""Stitching with given sections and links (ТЗ 8.1.4, Т8.1-4; ADR 0016 §8; 10.7): `normal`, `groups`, `links`,
`spacing` of `stitch_contours`, caps at the ends of every body, bodies in the report.

Reference values: Т8.1-4 — squares 20 × 20 m on 5 sections Δ = 25 m apart: 50 000 m³ with the default caps
(continued by Δ/2 and closed flat), 40 000 m³ with flat caps on the end sections; other prisms and slabs —
area × length, all 1e-9 relative (exact up to float64 rounding at project and UTM coordinates). Links equal
to the default ones give the same solid bit for bit. Branching volumes — the bounds of `test_stitch.py`.
"""

import numpy as np
import pytest

from bf_core.geo.stitch import StitchError, stitch_contours
from bf_core.geo.stitch_links import bodies
from tests.test_stitch import (
    CHILD_1,
    CHILD_2,
    PARENT,
    SQUARE,
    area,
    assert_solid,
    ellipse,
    half_ring,
    level,
    rotate,
    section,
    volume,
)

EXACT = 1e-9


def square(x0, z0, side, y, origin):
    """A square on the vertical section y = const (along x, up z)."""
    xz = np.array([[x0, z0], [x0 + side, z0], [x0 + side, z0 + side], [x0, z0 + side]], dtype=np.float64)
    return np.asarray(origin, dtype=np.float64) + np.column_stack([xz[:, 0], np.full(4, float(y)), xz[:, 1]])


def table(report):
    """Links and sections equal to the ones the default stitching chose."""
    links = [(a, b) for p in report["pairs"] for a in p["a"] for b in p["b"]]
    groups = {c: k for k, s in enumerate(report["sections"]) for c in s["contours"]}
    return links, [groups[c] for c in range(len(groups))]


def same_bits(a, b):
    return a.vertices.tobytes() == b.vertices.tobytes() and a.triangles.tobytes() == b.triangles.tobytes()


# ---- Т8.1-4


@pytest.mark.parametrize("origin", [(0.0, 0.0, 0.0), (500_000.0, 7_000_000.0, 250.0)], ids=["series", "utm"])
@pytest.mark.parametrize("given", ["all", "groups"])
def test_t8_1_4_squares_on_five_sections(origin, given):
    """Т8.1-1 series (A = 90°, P1 = (1000; 5000; 0), Δ = 25): O_k.y = 5000 + 25k, a square 20 × 20 on each."""
    contours = [square(990.0, 0.0, 20.0, 5000.0 + 25.0 * k, origin) for k in range(5)]
    extra = {"normal": (0.0, 1.0, 0.0), "spacing": 25.0} if given == "all" else {}
    links = [(k, k + 1) for k in range(4)]
    out = {}
    for caps, expected in (("extend", 50_000.0), ("flat", 40_000.0)):
        result = stitch_contours(contours, start_cap=caps, end_cap=caps, cap_distance=0.5, groups=range(5),
                                 links=links, **extra)  # fmt: skip
        assert_solid(result.vertices, result.triangles)
        assert result.report["volume"] == pytest.approx(expected, rel=EXACT)
        assert volume(result.vertices, result.triangles) == pytest.approx(expected, rel=EXACT)
        assert result.vertices[:20].tobytes() == np.vstack(contours).tobytes()  # given vertices bit for bit
        [body] = result.report["bodies"]
        assert body["contours"] == [0, 1, 2, 3, 4] and body["sections"] == [0, 4]
        assert body["volume"] == pytest.approx(expected, rel=EXACT) and body["triangles"] == len(result.triangles)
        out[caps] = result
    # A tie line on the corners of two linked squares is honoured the same way.
    tied = stitch_contours(contours, [np.array([contours[1][2], contours[2][2]])], start_cap="extend",
                           end_cap="extend", groups=range(5), links=links, **extra)  # fmt: skip
    assert tied.report["ties_used"] == 1 and tied.report["volume"] == pytest.approx(50_000.0, rel=EXACT)
    # The same series given in another order and with the normal the other way round.
    order = [3, 0, 4, 1, 2]
    shuffled = stitch_contours([contours[i] for i in order], start_cap="extend", end_cap="extend",
                               groups=order, links=[(order.index(a), order.index(b)) for a, b in links],
                               normal=(0.0, -1.0, 0.0))  # fmt: skip
    assert shuffled.report["volume"] == pytest.approx(50_000.0, rel=EXACT)
    assert shuffled.report["normal"] == pytest.approx([0.0, 1.0, 0.0], abs=1e-15)


# ---- links equal to the default ones


def series_cases():
    chain = [section(ellipse(40 + 4 * k, 30.0 - 3 * k, 12.0 + k, cx=2.0 * k, phase=0.3 * k), 20.0 * k)
             for k in range(4)]  # fmt: skip
    one_two = [section(PARENT, 0.0), section(CHILD_1, 20.0), section(CHILD_2, 20.0)]
    two_one_two = [
        section(CHILD_1, 0.0),
        section(CHILD_2, 0.01),
        section(PARENT, 20.0),
        section(ellipse(20, 12.0, 5.0, cy=8.0), 40.0),
        section(ellipse(22, 12.0, 5.0, cy=-8.0), 40.0),
    ]
    return {"chain": chain, "one_two": one_two, "two_one_two": two_one_two}


@pytest.mark.parametrize("name", ["chain", "one_two", "two_one_two"])
def test_links_equal_to_the_default_give_the_same_solid(name):
    contours = series_cases()[name]
    auto = stitch_contours(contours, start_cap="extend", end_cap="point")
    links, groups = table(auto.report)
    for extra in ({"links": links}, {"groups": groups}, {"groups": groups, "links": links},
                  {"groups": groups, "links": links[::-1]}):  # fmt: skip
        given = stitch_contours(contours, start_cap="extend", end_cap="point", **extra)
        assert same_bits(auto, given), extra
        assert given.report["volume"] == auto.report["volume"]
        assert [p["a"] for p in given.report["pairs"]] == [p["a"] for p in auto.report["pairs"]]
        [body] = given.report["bodies"]
        assert body["volume"] == pytest.approx(auto.report["volume"], rel=EXACT)
    assert "bodies" not in auto.report  # the Э9 report is unchanged


# ---- 1 → 2 by the table, N : M


A2 = ellipse(16, 5.0, 5.0, cx=90.0)


def test_one_to_two_by_the_table():
    """Centres pair the far small contour with a child; the table makes the parent branch and leaves it alone."""
    contours = [section(PARENT, 0.0), section(A2, 0.0), section(CHILD_1, 20.0), section(CHILD_2, 20.0)]
    auto = stitch_contours(contours, check=False)
    assert [(p["a"], p["b"]) for p in auto.report["pairs"]] == [([0], [2]), ([1], [3])]
    result = stitch_contours(contours, start_cap="extend", end_cap="extend", groups=[0, 0, 1, 1],
                             links=[(0, 2), (3, 0)])  # fmt: skip
    assert_solid(result.vertices, result.triangles)
    report = result.report
    assert [(p["a"], p["b"], p["branch"]) for p in report["pairs"]] == [([0], [2, 3], True)]
    one, lone = report["bodies"]
    assert one["contours"] == [0, 2, 3] and one["sections"] == [0, 1]
    assert lone == {"contours": [1], "sections": [0, 0], "triangles": lone["triangles"],
                    "volume": pytest.approx(area(A2) * 20.0, rel=EXACT)}  # fmt: skip
    ends = 10.0 * (area(PARENT) + area(CHILD_1) + area(CHILD_2))
    assert ends + (area(CHILD_1) + area(CHILD_2)) * 20.0 < one["volume"] < ends + area(PARENT) * 20.0
    assert report["volume"] == pytest.approx(one["volume"] + lone["volume"], rel=EXACT)
    # Threads do not change a bit.
    again = [stitch_contours(contours, start_cap="extend", end_cap="extend", groups=[0, 0, 1, 1],
                             links=[(0, 2), (0, 3)], threads=n) for n in (1, 4)]  # fmt: skip
    assert same_bits(again[0], again[1]) and same_bits(again[0], result)


def test_n_to_m_is_refused_with_the_sections():
    contours = [square(0.0, 0.0, 10.0, 25.0, (0, 0, 0)), square(30.0, 0.0, 10.0, 25.0, (0, 0, 0)),
                square(0.0, 0.0, 10.0, 50.0, (0, 0, 0)), square(30.0, 0.0, 10.0, 50.0, (0, 0, 0))]  # fmt: skip
    with pytest.raises(StitchError) as err:
        stitch_contours(contours, groups=[1, 1, 2, 2], links=[(0, 2), (0, 3), (1, 3)])
    assert err.value.code == "stitch_links" and err.value.between == (1, 2)
    assert err.value.contours == (0, 1, 2, 3)
    assert "Между разрезами 2 и 3" in err.value.message and "2 : 2" in err.value.message
    assert "разделите связи" in err.value.message and err.value.hint
    # Split into 1 : 1 and 1 : 1 the same contours are two prisms (sections 1 and 2 are neighbours).
    result = stitch_contours(contours, groups=[1, 1, 2, 2], links=[(0, 2), (1, 3)])
    assert result.report["volume"] == pytest.approx(2 * 100.0 * 25.0, rel=EXACT)
    assert result.report["sections"][0] == {"position": None, "contours": []}


# ---- bodies and their ends


def test_every_body_has_caps_at_its_ends():
    o = (500_000.0, 7_000_000.0, 250.0)
    big = [square(0.0, 0.0, 20.0, 25.0 * k, o) for k in range(5)]
    small = [square(100.0, 0.0, 10.0, 25.0 * k, o) for k in (1, 2)]
    contours = big + small
    links = [(k, k + 1) for k in range(4)] + [(5, 6)]
    for caps, v_big, v_small in (("extend", 50_000.0, 5_000.0), ("flat", 40_000.0, 2_500.0)):
        result = stitch_contours(contours, start_cap=caps, end_cap=caps, groups=[0, 1, 2, 3, 4, 1, 2], links=links)
        assert_solid(result.vertices, result.triangles)
        first, second = result.report["bodies"]
        assert first["contours"] == [0, 1, 2, 3, 4] and second["contours"] == [5, 6]
        assert second["sections"] == [1, 2]
        assert first["volume"] == pytest.approx(v_big, rel=EXACT)
        assert second["volume"] == pytest.approx(v_small, rel=EXACT)
        assert result.report["volume"] == pytest.approx(v_big + v_small, rel=EXACT)
        assert first["triangles"] + second["triangles"] == len(result.triangles)


def test_a_branch_that_ends_between_the_end_sections_gets_a_cap():
    contours = [section(PARENT, 0.0), section(CHILD_1, 20.0), section(CHILD_2, 20.0), section(CHILD_1, 40.0)]
    result = stitch_contours(contours, groups=[0, 1, 1, 2], links=[(0, 1), (0, 2), (1, 3)])
    assert_solid(result.vertices, result.triangles)
    [body] = result.report["bodies"]
    assert body["sections"] == [0, 2]
    low = (area(CHILD_1) + area(CHILD_2)) * 20.0 + area(CHILD_1) * 20.0
    assert low < result.report["volume"] < area(PARENT) * 20.0 + area(CHILD_1) * 20.0


def test_nothing_is_stitched_across_an_empty_section():
    contours = [square(0.0, 0.0, 20.0, 25.0 * k, (0, 0, 0)) for k in (0, 1, 3, 4)]
    result = stitch_contours(contours, start_cap="extend", end_cap="extend", groups=[0, 1, 3, 4])
    assert_solid(result.vertices, result.triangles)
    assert [b["contours"] for b in result.report["bodies"]] == [[0, 1], [2, 3]]
    assert result.report["volume"] == pytest.approx(2 * 400.0 * 50.0, rel=EXACT)
    assert result.report["sections"][2] == {"position": None, "contours": []}
    with pytest.raises(StitchError) as err:
        stitch_contours(contours, groups=[0, 1, 3, 4], links=[(0, 1), (1, 2), (2, 3)])
    assert err.value.code == "stitch_links" and err.value.between == (1, 3) and err.value.contours == (1, 2)
    assert "соседних разрезов" in err.value.message


def test_a_contour_linked_to_nothing():
    o = (500_000.0, 7_000_000.0, 250.0)
    contours = [square(0.0, 0.0, 20.0, 0.0, o), square(0.0, 0.0, 20.0, 25.0, o), square(60.0, 0.0, 20.0, 50.0, o)]
    # Continued by half the spacing on each side: a slab Δ thick (the step of the series: 25).
    result = stitch_contours(contours, start_cap="extend", end_cap="extend", groups=[0, 1, 2], links=[(0, 1)])
    assert_solid(result.vertices, result.triangles)
    assert result.report["bodies"][1]["volume"] == pytest.approx(400.0 * 25.0, rel=EXACT)
    with pytest.raises(StitchError) as err:
        stitch_contours(contours, groups=[0, 1, 2], links=[(0, 1)])  # flat caps: no volume
    assert err.value.code == "stitch_links" and err.value.between == (2, 2) and err.value.contours == (2,)
    assert "Контур 3 на разрезе 3" in err.value.message and err.value.hint
    # One contour alone: a slab, if the spacing is known.
    lone = stitch_contours([contours[2]], start_cap="extend", end_cap="extend", groups=[7], spacing=30.0)
    assert lone.report["volume"] == pytest.approx(400.0 * 30.0, rel=EXACT)
    with pytest.raises(StitchError) as err:
        stitch_contours([contours[2]], start_cap="extend", end_cap="extend", groups=[7])
    assert err.value.code == "stitch_sections" and err.value.between == (7, 7)


def test_bodies_that_touch_are_separate_solids():
    """Unlinked overlapping contours of neighbouring sections: their continued ends meet half way — a warning."""
    o = (500_000.0, 7_000_000.0, 250.0)
    contours = [square(0.0, 0.0, 20.0, 0.0, o), square(0.0, 0.0, 20.0, 25.0, o), square(5.0, 0.0, 10.0, 50.0, o),
                square(5.0, 0.0, 10.0, 75.0, o)]  # fmt: skip
    result = stitch_contours(contours, start_cap="extend", end_cap="extend", groups=range(4), links=[(0, 1), (2, 3)])
    assert [b["volume"] for b in result.report["bodies"]] == pytest.approx([400.0 * 50.0, 100.0 * 50.0], rel=EXACT)
    assert any("Тела 1 и 2 касаются или пересекаются" in w for w in result.report["warnings"])


def test_a_body_that_crosses_itself_is_refused_with_the_numbers_of_the_series():
    ring = half_ring(40, 22.0, 30.0, 90.0, 270.0)
    contours = [
        level(ring, 0.0),
        level(ring, 15.0),
        level(ring, 30.0),
        level(rotate(ring, 100.0), 45.0),
        level(rotate(ring, 100.0), 60.0),
        level(SQUARE + [200.0, 0.0], 15.0),
        level(SQUARE + [200.0, 0.0], 30.0),
    ]
    with pytest.raises(StitchError, match="Между контурами разрезов 5 и 6 получаются самопересечения") as error:
        stitch_contours(contours, groups=[2, 3, 4, 5, 6, 3, 4], links=[(0, 1), (1, 2), (2, 3), (3, 4), (5, 6)])
    assert error.value.between == (4, 5) and error.value.code == "stitch_self_intersection"


# ---- the normal, the order of the sections, bad parameters


def test_the_normal_is_given_and_turned_along_the_sections():
    contours = [section(ellipse(30, 20.0, 10.0), 20.0 * k) for k in range(3)]
    a = np.radians(30.0)
    across = np.array([-np.sin(a), np.cos(a), 0.0])
    for sign in (1.0, -1.0):
        result = stitch_contours(contours, normal=sign * across * 3.0, groups=[0, 1, 2])
        assert np.array(result.report["normal"]) == pytest.approx(across, abs=1e-15)
        assert_solid(result.vertices, result.triangles)
    flipped = stitch_contours(contours, normal=across, groups=[2, 1, 0])
    assert np.array(flipped.report["normal"]) == pytest.approx(-across, abs=1e-15)
    assert flipped.report["volume"] == pytest.approx(stitch_contours(contours).report["volume"], rel=EXACT)
    with pytest.raises(StitchError) as err:
        stitch_contours(contours, normal=(0.0, 0.0, 1.0))
    assert err.value.code == "stitch_sections" and "60°" in err.value.message
    with pytest.raises(StitchError) as err:
        stitch_contours(contours, groups=[0, 2, 1])
    assert err.value.code == "stitch_sections" and err.value.between == (1, 2)
    assert "Разрезы 2 и 3 идут не по порядку" in err.value.message


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"groups": [0, 1]}, "bad_parameter"),
        ({"groups": [0, -1, 2]}, "bad_parameter"),
        ({"groups": [0, 1.5, 2]}, "bad_parameter"),
        ({"groups": [0, 1, 2], "links": [(0, 1, 2)]}, "bad_parameter"),
        ({"groups": [0, 1, 2], "links": [(0, 3)]}, "bad_parameter"),
        ({"groups": [0, 1, 2], "links": [(1, 1)]}, "bad_parameter"),
        ({"groups": [0, 1, 1], "links": [(1, 2)]}, "stitch_links"),
        ({"normal": (0.0, 0.0, 0.0)}, "bad_parameter"),
        ({"normal": (1.0, 0.0)}, "bad_parameter"),
        ({"spacing": 0.0}, "bad_parameter"),
        ({"spacing": float("nan")}, "bad_parameter"),
    ],
)
def test_bad_parameters_are_refused_in_russian(extra, code):
    contours = [square(0.0, 0.0, 10.0, 25.0 * k, (0, 0, 0)) for k in range(3)]
    with pytest.raises(StitchError) as err:
        stitch_contours(contours, **extra)
    assert err.value.code == code and err.value.hint
    assert any("а" <= ch <= "я" for ch in err.value.message.lower())


def test_cancellation_with_given_links():
    contours = series_cases()["chain"]

    class Stop(Exception):
        pass

    def progress(fraction, message):
        if message.startswith("Сшивка"):
            raise Stop

    with pytest.raises(Stop):
        stitch_contours(contours, groups=range(4), links=[(0, 1), (1, 2), (2, 3)], progress=progress)


def test_bodies_of_links():
    assert bodies(6, [(0, 2), (2, 3), (5, 1)]) == [[0, 2, 3], [1, 5], [4]]
    assert bodies(2, []) == [[0], [1]]
