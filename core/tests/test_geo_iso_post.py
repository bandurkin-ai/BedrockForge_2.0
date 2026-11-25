"""The surface after the isosurface (подшаг 19.6; ТЗ 8.9.3 п. 7–8; ADR 0026 решение 13, вопросы № 100, № 102): V_min
takes the islands and the cavities, pyfqmr stays within 0.1·h of the surface as it was (else a gentler repeat, at most
three, then none and a line of the journal), the cut by the extent / a wireframe / a DTM, the checks of п. 7 as the
error `iso_check`, an empty surface as a result, the same bytes whatever the threads."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.geo import deviation, iso_post, solids
from bf_core.geo.iso import isosurface
from bf_core.geo.iso_post import IsoError, PostResult, mesh_failure, postprocess
from bf_core.model.spec_parts import parse_post
from tests.meshutil import icosphere
from tests.solidutil import box, grid

H = 2.0
BOX = ((-70.0, -70.0, -70.0), (70.0, 70.0, 70.0))
NONE = parse_post({})


def post(**kw):  # type: ignore[no-untyped-def]
    return parse_post({"clip": "none", **kw})


def sphere_field(r: float, c=(0.0, 0.0, 0.0)):  # type: ignore[no-untyped-def]
    centre = np.asarray(c, dtype=np.float64)
    return lambda p: np.linalg.norm(p - centre, axis=1) - r


def surface(field, h=H):  # type: ignore[no-untyped-def]
    m = isosurface(field, *BOX, h, levels=2)
    return m.vertices, m.triangles


def vol(v: np.ndarray, t: np.ndarray) -> float:
    return solids.signed_volume(v - v.mean(axis=0), t)


def ball(r: float) -> float:
    return 4.0 / 3.0 * math.pi * r**3


@pytest.fixture(scope="module")
def sphere50() -> tuple[np.ndarray, np.ndarray]:
    return surface(sphere_field(50.0))


# --- the checks of п. 7 ------------------------------------------------------------------------------------------


def test_good_surface_passes_and_is_returned_as_it_is(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    assert mesh_failure(v, t, h=H) is None
    r = postprocess(v, t, h=H, post=NONE)
    assert isinstance(r, PostResult) and not r.empty
    assert r.vertices.tobytes() == v.tobytes() and r.triangles.tobytes() == t.tobytes()
    assert r.removed_parts == 0 and not r.simplified and r.journal == []
    assert r.vertices.dtype == np.float64 and r.triangles.dtype == np.int32


def test_failures_are_named_in_russian(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    assert "не замкнута" in (mesh_failure(v, t[1:], h=H) or "")
    assert "направлены внутрь" in (mesh_failure(v, t[:, ::-1], h=H) or "")
    flipped = t.copy()
    flipped[0] = flipped[0][::-1]
    assert "не согласованы" in (mesh_failure(v, flipped, h=H) or "")
    # two spheres in one mesh that cross each other: the self-intersections
    a, b = icosphere(3, 20.0, (0.0, 0.0, 0.0)), icosphere(3, 20.0, (15.0, 0.0, 0.0))
    both = (np.concatenate([a[0], b[0]]), np.concatenate([a[1], b[1] + len(a[0])]))
    assert "пересекает сама себя" in (mesh_failure(*both, h=H) or "")
    assert "нет треугольников" in (mesh_failure(v, t[:0], h=H) or "")


@pytest.mark.parametrize("break_it", ["open", "inside_out", "crossing"])
def test_failed_check_is_iso_check_and_nothing_is_returned(sphere50, break_it: str) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    if break_it == "open":
        t = t[2:]
    elif break_it == "inside_out":
        t = t[:, ::-1]
    else:
        b = icosphere(3, 30.0, (25.0, 0.0, 0.0))
        v, t = np.concatenate([v, b[0]]), np.concatenate([t, b[1] + len(v)])
    with pytest.raises(IsoError) as e:
        postprocess(v, t, h=H, post=NONE)
    assert e.value.code == "iso_check"
    assert "не прошла проверку" in e.value.message and e.value.hint
    assert set(e.value.as_dict()) == {"code", "message", "hint"}


# --- V_min ---------------------------------------------------------------------------------------------------


def test_v_min_removes_islands_and_cavities() -> None:
    big = sphere_field(40.0)
    island = sphere_field(6.0, (55.0, 0.0, 0.0))  # 905 m³
    hole = sphere_field(5.0, (10.0, 0.0, 0.0))  # a cavity of 524 m³

    def field(p: np.ndarray) -> np.ndarray:
        return np.maximum(np.minimum(big(p), island(p)), -hole(p))

    v, t = surface(field)
    assert iso_post.postprocess(v, t, h=H, post=post(v_min=0.0)).removed_parts == 0
    total = vol(v, t)
    assert total == pytest.approx(ball(40.0) + ball(6.0) - ball(5.0), rel=0.02)
    cavity = postprocess(v, t, h=H, post=post(v_min=700.0))  # only the cavity is under 700
    assert cavity.removed_parts == 1 and "полостей — 1" in cavity.journal[0] and "островов" not in cavity.journal[0]
    assert vol(cavity.vertices, cavity.triangles) == pytest.approx(ball(40.0) + ball(6.0), rel=0.02)
    both = postprocess(v, t, h=H, post=post(v_min=1000.0))
    assert both.removed_parts == 2 and "островов — 1" in both.journal[0] and "полостей — 1" in both.journal[0]
    assert vol(both.vertices, both.triangles) == pytest.approx(ball(40.0), rel=0.01)
    assert np.linalg.norm(both.vertices, axis=1).max() < 41.0  # the island at x = 55 is gone
    assert mesh_failure(both.vertices, both.triangles, h=H) is None
    assert len(both.vertices) == len(np.unique(both.triangles))  # no vertex of a removed part is left
    nothing = postprocess(v, t, h=H, post=post(v_min=1e9))  # every part under V_min: an empty result, no error
    assert nothing.empty and nothing.removed_parts == 3 and len(nothing.vertices) == 0


# --- pyfqmr --------------------------------------------------------------------------------------------------


def test_simplify_stays_within_a_tenth_of_h(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    r = postprocess(v, t, h=H, post=post(simplify=True), threads=2)
    assert r.simplified and len(r.triangles) < 0.6 * len(t)
    assert mesh_failure(r.vertices, r.triangles, h=H) is None
    assert deviation.max_deviation(v, r.vertices, r.triangles) <= 0.1 * H  # the vertices as they were, from the new
    assert vol(r.vertices, r.triangles) == pytest.approx(vol(v, t), rel=0.01)
    assert r.journal[0].startswith("Упрощение: треугольников") and "допуск 0,1·h" in r.journal[0]
    again = postprocess(v, t, h=H, post=post(simplify=True), threads=1)
    assert again.vertices.tobytes() == r.vertices.tobytes() and again.triangles.tobytes() == r.triangles.tobytes()


def test_simplify_repeats_gentler_three_times_then_gives_up(monkeypatch, sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    calls: list[int] = []
    shares: list[int] = []
    real = iso_post._decimate

    def spy(local, tri, target, aggressiveness):  # type: ignore[no-untyped-def]
        shares.append(target)
        return real(local, tri, target, aggressiveness)

    monkeypatch.setattr(iso_post, "_decimate", spy)
    monkeypatch.setattr(deviation, "max_deviation", lambda *a, **k: calls.append(1) or 1.0)  # always too far
    r = postprocess(v, t, h=H, post=post(simplify=True))
    assert not r.simplified and len(calls) == 4  # the first attempt and three repeats
    assert shares == sorted(shares) and len(set(shares)) == 4  # every repeat keeps more
    assert r.vertices.tobytes() == v.tobytes() and r.triangles.tobytes() == t.tobytes()
    assert r.journal[-1].startswith("Упрощение отменено") and "без упрощения" in r.journal[-1]


def test_a_gentler_repeat_may_pass(monkeypatch, sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    answers = iter([0.5, 0.0])
    monkeypatch.setattr(deviation, "max_deviation", lambda *a, **k: next(answers))
    r = postprocess(v, t, h=H, post=post(simplify=True))
    assert r.simplified and 0.3 * len(t) < len(r.triangles) < 0.6 * len(t)


def test_tiny_surface_is_not_simplified() -> None:
    v, t = icosphere(1, 10.0, (0.0, 0.0, 0.0))
    r = postprocess(v, t, h=H, post=post(simplify=True))
    assert not r.simplified and r.journal == ["Упрощение пропущено: в поверхности слишком мало треугольников."]


# --- the cut -------------------------------------------------------------------------------------------------


def test_cut_by_the_extent(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    half = box((-60.0, -60.0, -60.0), (0.0, 60.0, 60.0), np.zeros(3))
    r = postprocess(v, t, h=H, post=post(clip="boundary"), clip=half)
    assert vol(r.vertices, r.triangles) == pytest.approx(vol(v, t) / 2.0, rel=0.005)
    assert r.vertices[:, 0].max() <= 1e-9 and "Обрезка охватом модели" in r.journal[0]
    again = solids.canonical(r.vertices, r.triangles)  # the result of a boolean is canonical
    assert again[0].tobytes() == r.vertices.tobytes() and again[1].tobytes() == r.triangles.tobytes()
    # the extent that does not cut anything is the same body
    whole = box((-60.0, -60.0, -60.0), (60.0, 60.0, 60.0), np.zeros(3))
    kept = postprocess(v, t, h=H, post=post(clip="boundary"), clip=whole)
    assert vol(kept.vertices, kept.triangles) == pytest.approx(vol(v, t), rel=1e-9)
    # without a solid of the extent the surface is as the field closed it
    assert postprocess(v, t, h=H, post=post(clip="boundary")).vertices.tobytes() == v.tobytes()


def test_cut_by_a_wireframe(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    inner = icosphere(3, 30.0, (0.0, 0.0, 0.0))
    r = postprocess(v, t, h=H, post=parse_post({"clip": "wireframe", "clip_id": 7}), clip=inner)
    assert vol(r.vertices, r.triangles) == pytest.approx(
        vol(inner[0], inner[1]), rel=1e-9
    )  # the whole of the inner sphere
    assert "Обрезка каркасом" in r.journal[0]
    with pytest.raises(IsoError) as e:
        postprocess(v, t, h=H, post=parse_post({"clip": "wireframe", "clip_id": 7}))
    assert e.value.code == "iso_clip_missing"
    with pytest.raises(solids.SolidError) as e2:  # an open wireframe of the cut: named
        postprocess(v, t, h=H, post=parse_post({"clip": "wireframe", "clip_id": 7}), clip=(inner[0], inner[1][1:]))
    assert e2.value.code == "wireframe_open" and "Каркас обрезки" in e2.value.message


def test_cut_by_a_dtm_and_the_extent(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    dtm = grid(20, 20, (-100.0, 100.0), (-100.0, 100.0), lambda x, y: 0.1 * x, offset=np.zeros(3))  # through the centre
    p = parse_post({"clip": "dtm", "clip_id": 3})
    wedge = math.acos(0.1 / math.hypot(1.0, 0.1)) / (2.0 * math.pi)  # below z = 0.1·x and x ≤ 0: 84.3° of the turn
    below = postprocess(v, t, h=H, post=p, clip=dtm)
    assert vol(below.vertices, below.triangles) == pytest.approx(vol(v, t) / 2.0, rel=0.01)
    assert "Обрезка ЦТМ" in below.journal[0]
    quarter = postprocess(v, t, h=H, post=p, clip=dtm, boundary=box((-60, -60, -60), (0, 60, 60), np.zeros(3)))
    assert vol(quarter.vertices, quarter.triangles) == pytest.approx(vol(v, t) * wedge, rel=0.01)
    # the DTM covers a part of the body only: what lies beyond its edge has no ground and goes
    part = grid(10, 20, (-100.0, 0.0), (-100.0, 100.0), lambda x, y: 0.1 * x, offset=np.zeros(3))
    cut = postprocess(v, t, h=H, post=p, clip=part)
    assert vol(cut.vertices, cut.triangles) == pytest.approx(vol(v, t) * wedge, rel=0.01)
    with pytest.raises(IsoError) as e:
        postprocess(v, t, h=H, post=p)
    assert e.value.code == "iso_clip_missing" and "ЦТМ" in e.value.message


def test_empty_results_are_results(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    far = box((100.0, 100.0, 100.0), (120.0, 120.0, 120.0), np.zeros(3))
    r = postprocess(v, t, h=H, post=post(clip="boundary"), clip=far)
    assert r.empty and r.vertices.shape == (0, 3) and r.triangles.shape == (0, 3) and r.triangles.dtype == np.int32
    assert "ничего не осталось" in r.journal[-1]
    r = postprocess(v[:0], t[:0], h=H, post=post(v_min=5.0, simplify=True, clip="boundary"), clip=far)
    assert r.empty and r.journal == ["Поверхность пуста: тело не построено."]
    dtm = grid(4, 4, (-100.0, 100.0), (-100.0, 100.0), lambda x, y: 0.0 * x - 80.0, offset=np.zeros(3))
    under = postprocess(v, t, h=H, post=parse_post({"clip": "dtm", "clip_id": 1}), clip=dtm)  # ground below the body
    assert under.empty


def test_same_bytes_under_any_threads(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    p = parse_post({"v_min": 10.0, "simplify": True, "clip": "wireframe", "clip_id": 1})
    clip = icosphere(3, 45.0, (0.0, 0.0, 0.0))
    a = postprocess(v, t, h=H, post=p, clip=clip, threads=1)
    b = postprocess(v, t, h=H, post=p, clip=clip, threads=4)
    assert a.vertices.tobytes() == b.vertices.tobytes() and a.triangles.tobytes() == b.triangles.tobytes()
    assert a.journal == b.journal and a.simplified and not a.empty


def test_tick_reports_and_cancels(sphere50) -> None:  # type: ignore[no-untyped-def]
    v, t = sphere50
    seen: list[float] = []
    postprocess(v, t, h=H, post=post(v_min=1.0, simplify=True), tick=lambda f, m: seen.append(f))
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)

    class Cancelled(Exception):
        pass

    def stop(fraction: float, message: str) -> None:
        if fraction >= 0.15:
            raise Cancelled

    with pytest.raises(Cancelled):
        postprocess(v, t, h=H, post=post(simplify=True), tick=stop)
