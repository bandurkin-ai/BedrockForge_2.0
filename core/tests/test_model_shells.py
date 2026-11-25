"""Shells of grades (подшаг 19.14; ТЗ 8.9.2 «оболочки содержаний», 8.9.3; Т8.9-6; ADR 0026 решения 6, 16): the core of a
sphere of raised grade (a Gaussian) through the whole pipeline — numeric (linear, log, nscore) and indicator shells; the
bodies are nested (Т8.9-6: the shell 1,0 minus the shell 0,5 is empty to 1e-9 of the volume of the shell 0,5, the
volumes fall, also on noisy data whose RBF are not nested); the radii of the shells against the analytic ones; closed,
oriented, χ = 2; an empty threshold is a line of the journal; the same bytes with 1, 2, 4, 8 threads and any order of
the data; the cancel leaves nothing; the refusals; the laziness of the modules."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geo import checks, meshes, solids
from bf_core.model import data as md
from bf_core.model.bodies import BodiesResult
from bf_core.model.bodies_parts import box_solid
from bf_core.model.shells import ShellsResult, build_shells, label_of
from bf_core.model.spec import ModelSpec, parse_model
from bf_core.model.spec_parts import POINT_TYPES

CORE = Path(__file__).resolve().parents[1]
KIND = {name: i for i, name in enumerate(POINT_TYPES)}

C = np.array([512_345.0, 6_234_567.0, 412.0])
HALF = 80.0
BOX = [*(C - HALF), *(C + HALF)]
SIGMA2 = 400.0  # g(r) = 0.05 + 3·exp(−r²/(2·20²))
THRESHOLDS = [0.5, 1.0, 2.0]


class Cancelled(Exception):
    pass


# --- synthetic grades ---


def core_grade(r: np.ndarray) -> np.ndarray:
    return 0.05 + 3.0 * np.exp(-r * r / (2.0 * SIGMA2))


def core_radius(t: float) -> float:
    """The radius where the grade of the core is t."""
    return math.sqrt(2.0 * SIGMA2 * math.log(3.0 / (t - 0.05)))


def lattice(spacing: float = 8.0, jitter: float = 1.5, seed: int = 3) -> np.ndarray:
    """A jittered lattice of the whole box (a regular scatter of composites, no two points closer than the jitter)."""
    ax = np.arange(-HALF, HALF + 1e-9, spacing)
    grid = np.stack(np.meshgrid(ax, ax, ax, indexing="ij"), axis=-1).reshape(-1, 3)
    rng = np.random.default_rng(seed)
    return C + grid + rng.uniform(-jitter, jitter, size=grid.shape)


def shell_points(
    gid: int, xyz: np.ndarray, g: np.ndarray, thresholds: list[float], *, mode: str = "numeric",
    transform: str = "linear",
) -> md.Points:  # fmt: skip
    """The points of a shell as `data_holes.shell_points` makes them: f = T(g) (numeric) or g (indicator)."""
    graded = md.grade_points(xyz, g, transform="linear" if mode == "indicator" else transform, thresholds=thresholds)
    values = np.asarray(graded.values)
    keep = np.flatnonzero(~np.isnan(values))
    n = len(keep)
    f = (g if mode == "indicator" else values)[keep]
    levels = [float(x) for x in graded.levels]
    p = md.Points(np.ascontiguousarray(xyz[keep]), np.asarray(f, dtype=np.float64), np.full(n, KIND["value"], np.int8),
                  np.full(n, gid, np.int32), np.full(n, -1, np.int8), np.zeros(n, np.int32), np.full((n, 2), np.nan),
                  np.full(n, -1, np.int64), ["композиты"], {"points": n}, list(graded.notes),
                  {gid: levels})  # fmt: skip
    return md.assemble([p])


def shell_spec(
    thresholds: list[float] | None = None, *, mode: str = "numeric", transform: str = "linear", p: float | None = None,
    h: float = 2.0, name: str = "Au", **more: Any,
) -> ModelSpec:  # fmt: skip
    params: dict[str, Any] = {"result_id": 1, "field": "AU", "thresholds": thresholds or THRESHOLDS, "mode": mode,
                              "transform": transform}  # fmt: skip
    if p is not None:
        params["p"] = p
    group = {"id": 1, "name": name, "type": "shell", "params": params}
    return parse_model({"boundary": {"box": BOX}, "h": h, "groups": [group], **more})


def core_points(mode: str = "numeric", transform: str = "linear", spacing: float = 8.0) -> md.Points:
    xyz = lattice(spacing)
    return shell_points(1, xyz, core_grade(np.linalg.norm(xyz - C, axis=1)), THRESHOLDS, mode=mode, transform=transform)


def run(spec: ModelSpec, pts: md.Points, *, threads: int = 4, **kw: Any) -> ShellsResult:
    return build_shells(spec, spec.groups[0], pts, box_solid(BOX), threads=threads, **kw)


# --- checks of the results ---


def assert_solid(v: np.ndarray, t: np.ndarray) -> float:
    """Closed, manifold, consistently oriented, outward (volume > 0), no self-intersections: the volume."""
    assert len(t) > 0 and v.dtype == np.float64 and t.dtype == np.int32
    assert meshes.edge_summary(t, len(v)) == (0, 0)
    assert checks.orientation_conflicts(t, len(v)) == 0
    volume = solids.signed_volume(v - solids.local_origin(v), t)
    assert volume > 0.0
    assert checks.self_intersections(v, t, limit=1, samples=1).pairs == 0
    return float(volume)


def bodies_of(res: BodiesResult) -> list[Any]:
    return [o for o in res.outputs if o.kind == "body"]


def common(a: Any, b: Any, op: str) -> float:
    """The volume of the intersection or of the difference (a minus b) of two bodies (0 when it is empty or flat)."""
    _v, _t, rep = solids.boolean_many([(a.vertices, a.triangles), (b.vertices, b.triangles)], op)
    return 0.0 if rep["empty"] or rep["flat"] else float(rep["volume"])


def same(a: BodiesResult, b: BodiesResult) -> None:
    assert [(o.kind, o.group, o.threshold, o.name) for o in a.outputs] == [
        (o.kind, o.group, o.threshold, o.name) for o in b.outputs
    ]
    for x, y in zip(a.outputs, b.outputs, strict=True):
        assert x.vertices.tobytes() == y.vertices.tobytes()
        assert x.triangles.tobytes() == y.triangles.tobytes()
        assert x.volume == y.volume


def assert_nested(bodies: list[Any]) -> None:
    """Т8.9-6: the shell k is inside the shell k − 1: the difference is empty to 1e-9 of the volume of the lower one."""
    for lower, upper in zip(bodies, bodies[1:], strict=False):
        assert upper.volume < lower.volume
        assert common(upper, lower, "difference") <= 1e-9 * lower.volume
        assert common(upper, lower, "intersection") == pytest.approx(upper.volume, rel=1e-9)


# --- numeric shells: the core of a sphere ---


@pytest.fixture(scope="module")
def numeric() -> tuple[ShellsResult, md.Points]:
    pts = core_points()
    return run(shell_spec(), pts), pts


def test_numeric_core_shells_t896(numeric: tuple[ShellsResult, md.Points]) -> None:
    res, _ = numeric
    bodies = bodies_of(res)
    assert [o.name for o in bodies] == ["Au ≥ 0.5", "Au ≥ 1", "Au ≥ 2"]
    assert [o.name for o in bodies] == [label_of(res.fits[1].group, t) for t in THRESHOLDS]
    assert [o.threshold for o in bodies] == THRESHOLDS and {o.group for o in bodies} == {1}
    assert all(o.kind == "body" and o.wing is None for o in res.outputs) and len(res.outputs) == 3
    volumes = [assert_solid(o.vertices, o.triangles) for o in bodies]
    assert volumes == sorted(volumes, reverse=True) and volumes[0] > volumes[1] > volumes[2]  # Σ the volumes fall
    for o, v in zip(bodies, volumes, strict=True):
        assert o.volume == pytest.approx(v, rel=1e-12)
        assert len(o.vertices) - 3 * len(o.triangles) // 2 + len(o.triangles) == 2  # χ = 2
    assert_nested(bodies)
    for o, t in zip(bodies, THRESHOLDS, strict=True):  # the radii of the shells against g(r) = t
        r = np.linalg.norm(o.vertices - C, axis=1)
        assert abs(r.mean() - core_radius(t)) <= 0.25 and r.max() - r.min() <= 0.5  # measured: 0.06 m, 0.09 m
        assert o.volume == pytest.approx(4.0 / 3.0 * math.pi * core_radius(t) ** 3, rel=0.02)
    assert res.stats["nested"] is True and res.stats["mode"] == "numeric"
    assert [f["threshold"] for f in res.stats["thresholds"]] == THRESHOLDS
    assert len(res.fits_by_threshold) == 3 and len({id(f) for f in res.fits_by_threshold.values()}) == 1  # one RBF
    assert res.fits[1] is res.fits_by_threshold[(1, 0)] and res.fits[1].group.type == "shell"
    assert any("Au ≥ 0.5" in line for line in res.journal) and res.stats["time"]["total"] > 0.0


def test_numeric_field_is_the_same_bytes_with_threads(numeric: tuple[ShellsResult, md.Points]) -> None:
    base, pts = numeric
    for threads in (1, 2, 8):
        same(base, run(shell_spec(), pts, threads=threads))


def test_numeric_is_the_same_for_any_order_of_the_data(numeric: tuple[ShellsResult, md.Points]) -> None:
    base, _ = numeric
    xyz = lattice()
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    order = np.random.default_rng(11).permutation(len(g))
    shuffled = shell_points(1, xyz[order], g[order], THRESHOLDS)  # `assemble` puts the canonical order back
    assert shuffled.to_parquet() == core_points().to_parquet()
    same(base, run(shell_spec(), shuffled, threads=2))


def test_the_unit_of_the_grade_changes_nothing() -> None:
    """The grade in g/t or in ppm (·1000), the thresholds too: the same shells (the field is made of order 1)."""
    xyz = lattice()
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    a = run(shell_spec(), shell_points(1, xyz, g, THRESHOLDS))
    t_big = [1000.0 * t for t in THRESHOLDS]
    b = run(shell_spec(t_big), shell_points(1, xyz, 1000.0 * g, t_big))
    for x, y in zip(bodies_of(a), bodies_of(b), strict=True):
        assert x.volume == pytest.approx(y.volume, rel=1e-4)


def test_log_shells_are_the_isolines_of_the_logarithm() -> None:
    xyz = lattice()
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    pts = shell_points(1, xyz, g, THRESHOLDS, transform="log")
    assert pts.levels[1] == pytest.approx(np.log(THRESHOLDS))  # the thresholds go through the same transform
    res = run(shell_spec(transform="log"), pts)
    bodies = bodies_of(res)
    assert [o.name for o in bodies] == ["Au ≥ 0.5", "Au ≥ 1", "Au ≥ 2"]  # the names are the thresholds, not their logs
    for o, t in zip(bodies, THRESHOLDS, strict=True):
        assert_solid(o.vertices, o.triangles)
        assert abs(np.linalg.norm(o.vertices - C, axis=1).mean() - core_radius(t)) <= 2.0
    assert_nested(bodies)


def test_nscore_shells() -> None:
    xyz = lattice()
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    pts = shell_points(1, xyz, g, THRESHOLDS, transform="nscore")
    assert np.all(np.diff(pts.levels[1]) > 0) and pts.f.min() < 0.0 < pts.f.max()  # normal scores: both signs
    res = run(shell_spec(transform="nscore"), pts)
    bodies = bodies_of(res)
    assert len(bodies) == 3
    for o in bodies:
        assert_solid(o.vertices, o.triangles)
    assert_nested(bodies)
    # the transform is monotone: the shell of a threshold holds the data above it (the cut of g, not of T(g))
    inside = np.linalg.norm(bodies[1].vertices - C, axis=1).mean()
    assert abs(inside - core_radius(1.0)) <= 3.0


def test_numeric_shells_on_noisy_data_are_nested() -> None:
    """With noise the surfaces of two thresholds of one RBF cross each other in places; the cut makes them nested."""
    xyz = lattice(spacing=7.0, jitter=1.5, seed=5)
    rng = np.random.default_rng(9)
    g = core_grade(np.linalg.norm(xyz - C, axis=1)) * np.exp(0.5 * rng.standard_normal(len(xyz)))
    res = run(shell_spec([0.4, 0.5, 0.6]), shell_points(1, xyz, g, [0.4, 0.5, 0.6]))
    bodies = bodies_of(res)
    assert len(bodies) == 3
    for o in bodies:
        assert_solid(o.vertices, o.triangles)
    assert_nested(bodies)


# --- indicator shells ---


@pytest.fixture(scope="module")
def indicator() -> ShellsResult:
    return run(shell_spec(mode="indicator"), core_points("indicator"))


def test_indicator_shells_t896(indicator: ShellsResult) -> None:
    bodies = bodies_of(indicator)
    assert [o.name for o in bodies] == ["Au ≥ 0.5", "Au ≥ 1", "Au ≥ 2"] and [o.threshold for o in bodies] == THRESHOLDS
    volumes = [assert_solid(o.vertices, o.triangles) for o in bodies]
    assert volumes == sorted(volumes, reverse=True)
    assert_nested(bodies)
    for o, t in zip(bodies, THRESHOLDS, strict=True):  # the indicator surface is half-way between the data
        r = np.linalg.norm(o.vertices - C, axis=1)
        assert abs(r.mean() - core_radius(t)) <= 3.0
        assert o.volume == pytest.approx(4.0 / 3.0 * math.pi * core_radius(t) ** 3, rel=0.25)
    assert indicator.stats["mode"] == "indicator"
    assert len(indicator.fits_by_threshold) == 3 and len({id(f) for f in indicator.fits_by_threshold.values()}) == 3
    assert indicator.fits[1] is indicator.fits_by_threshold[(1, 0)]  # an RBF of the indicator of its own


def test_indicator_p_moves_the_surface() -> None:
    """p < 0,5 takes the surface out (f = (p − 0,5) − RBF is negative for a lower RBF): the body grows."""
    pts = core_points("indicator")
    volume = {p: bodies_of(run(shell_spec([1.0], mode="indicator", p=p), pts))[0].volume for p in (0.3, 0.5, 0.7)}
    assert volume[0.3] > volume[0.5] > volume[0.7]


def test_indicator_is_the_same_bytes_with_threads_and_order(indicator: ShellsResult) -> None:
    xyz = lattice()
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    order = np.random.default_rng(5).permutation(len(g))
    shuffled = shell_points(1, xyz[order], g[order], THRESHOLDS, mode="indicator")
    for threads, pts in ((1, core_points("indicator")), (2, shuffled), (8, shuffled)):
        same(indicator, run(shell_spec(mode="indicator"), pts, threads=threads))


def test_indicator_shells_on_noisy_data_are_nested() -> None:
    xyz = lattice(spacing=7.0, jitter=1.5, seed=6)
    rng = np.random.default_rng(10)
    g = core_grade(np.linalg.norm(xyz - C, axis=1)) * np.exp(0.6 * rng.standard_normal(len(xyz)))
    ths = [0.4, 0.8, 1.2]
    bodies = bodies_of(run(shell_spec(ths, mode="indicator"), shell_points(1, xyz, g, ths, mode="indicator")))
    assert len(bodies) >= 2
    for o in bodies:
        assert_solid(o.vertices, o.triangles)
    assert_nested(bodies)


# --- an empty threshold, the surfaces, the degenerate cases ---


def test_a_threshold_with_no_data_above_it_is_a_line_of_the_journal() -> None:
    xyz = lattice()
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    for mode in ("numeric", "indicator"):
        pts = shell_points(1, xyz, g, [0.5, 1.0, 50.0, 60.0], mode=mode)
        res = run(shell_spec([0.5, 1.0, 50.0, 60.0], mode=mode), pts)
        assert [o.name for o in bodies_of(res)] == ["Au ≥ 0.5", "Au ≥ 1"]
        assert sum("точек выше порога нет" in line for line in res.journal) == 1  # the first empty one
        assert any("Au ≥ 60" in line and "ниже пуста" in line for line in res.journal)  # the next ones are not run
        assert [f["volume"] > 0.0 for f in res.stats["thresholds"]] == [True, True, False, False]
        assert len(res.fits_by_threshold) == 2  # no RBF for the empty ones


def test_all_thresholds_empty_is_no_error() -> None:
    xyz = lattice()
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    res = run(shell_spec([10.0, 20.0]), shell_points(1, xyz, g, [10.0, 20.0]))
    assert res.outputs == [] and res.fits == {} and res.stats["nested"] is True
    assert sum("Au ≥" in line for line in res.journal) == 2


def test_the_isosurfaces_on_request() -> None:
    pts = core_points()
    res = run(shell_spec([0.5, 1.0]), pts, surfaces=True)
    names = [(o.kind, o.name) for o in res.outputs]
    assert names == [("surface", "Au ≥ 0.5: изоповерхность"), ("body", "Au ≥ 0.5"),
                     ("surface", "Au ≥ 1: изоповерхность"), ("body", "Au ≥ 1")]  # fmt: skip
    for o in res.outputs:
        if o.kind == "surface":
            assert o.volume is None and o.area > 0.0 and o.threshold in (0.5, 1.0)
    surface, body = res.outputs[0], res.outputs[1]
    assert surface.area == pytest.approx(body.area, rel=0.02)  # the shell is inside the box: all of it is the surface


def test_shells_are_cut_by_the_boundary_and_a_shell_that_fills_it_is_the_boundary() -> None:
    """A threshold under every grade: the shell is the whole boundary (the +H beyond the box closes it there)."""
    xyz = lattice()
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    res = run(shell_spec([0.01, 1.0]), shell_points(1, xyz, g, [0.01, 1.0]))
    low, high = bodies_of(res)
    assert low.volume == pytest.approx((2.0 * HALF) ** 3, rel=1e-6) and high.volume < low.volume
    assert_nested([low, high])
    small = [*(C - 30.0), *(C + 30.0)]  # a boundary smaller than the shell 0,5 (r = 39): the shell is cut by it
    cut = build_shells(shell_spec(), shell_spec().groups[0], core_points(), box_solid(small), threads=2)
    first = bodies_of(cut)[0]
    assert first.volume < 60.0**3 and np.abs(first.vertices - C).max() <= 30.0 + 1e-6
    assert_nested(bodies_of(cut))


def test_cancel_leaves_nothing() -> None:
    pts = core_points(spacing=12.0)
    spec = shell_spec([0.5, 1.0], h=4.0)
    seen: list[tuple[float, str]] = []
    res = run(spec, pts, tick=lambda f, m: seen.append((f, m)))
    assert len(res.outputs) == 2 and len(seen) > 10
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[0] >= 0.0 and fractions[-1] == 1.0
    assert any(m.startswith("Участки") for _, m in seen) and any(m.startswith("Изоповерхность") for _, m in seen)
    for stop in (1, len(seen) // 3, len(seen) // 2, 2 * len(seen) // 3, len(seen) - 2):
        count = [0]

        def tick(_f: float, _m: str, stop: int = stop, count: list[int] = count) -> None:
            count[0] += 1
            if count[0] == stop:
                raise Cancelled

        with pytest.raises(Cancelled):
            run(spec, pts, tick=tick)


def test_refusals_are_russian_with_a_hint() -> None:
    spec, pts = shell_spec(), core_points()
    with pytest.raises(ApiError) as err:
        run(spec, md.Points.empty())
    assert err.value.code == "no_contacts" and err.value.status == 422 and err.value.extra["group"] == 1
    assert "Au" in err.value.message and err.value.hint
    elsewhere = md.assemble([shell_points(2, lattice(), core_grade(np.linalg.norm(lattice() - C, axis=1)), THRESHOLDS)])
    with pytest.raises(ApiError) as none:
        run(spec, elsewhere)  # points of another group only
    assert none.value.code == "no_contacts" and none.value.extra["group"] == 1
    with pytest.raises(ApiError) as flat:
        build_shells(spec, spec.groups[0], pts, box_solid([*C, C[0] + 10.0, C[1] + 10.0, C[2]]))
    assert flat.value.code == "boundary_empty" and flat.value.status == 422
    v, t = box_solid(BOX)
    with pytest.raises(ApiError) as op:
        build_shells(spec, spec.groups[0], pts, (v, t[:-1]))
    assert op.value.code == "wireframe_open" and op.value.extra["param"] == "boundary"
    manual = [{"layer_id": 1, "role": "contact", "group": 2}]
    domain = parse_model({"boundary": {"box": BOX}, "h": 4.0, "manual": manual,
                          "groups": [{"id": 2, "name": "Д", "type": "domain", "codes": []}]})  # fmt: skip
    with pytest.raises(ApiError) as wrong:
        build_shells(domain, domain.groups[0], pts, box_solid(BOX))
    assert wrong.value.code == "bad_param" and wrong.value.status == 422


def test_coincident_grades_with_different_values_are_rbf_singular_with_sources() -> None:
    """With s = 0 two samples in one place with two grades make the system singular (the smoothing of Д-25 mends it)."""
    xyz = lattice(spacing=16.0)
    g = core_grade(np.linalg.norm(xyz - C, axis=1))
    xyz = np.vstack([xyz, xyz[:1]])
    g = np.concatenate([g, g[:1] * 1.5])
    pts = shell_points(1, xyz, g, THRESHOLDS)
    assert pts.n == len(g)  # two values in one place: both stay (решение 5)
    with pytest.raises(ApiError) as err:
        run(shell_spec(smoothing={"mode": "manual", "s": 0}), pts)
    e = err.value
    assert e.code == "rbf_singular" and e.status == 422 and e.extra["group"] == 1 and e.hint
    assert e.extra["sources"] and all("композиты" in s for s in e.extra["sources"])
    run(shell_spec(), pts)  # the default smoothing of grades (s = 0,05) takes both


def test_failed_checks_are_iso_check_with_the_group(monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.geo import iso_post

    monkeypatch.setattr(iso_post, "mesh_failure", lambda *a, **k: "поверхность пересекает сама себя: пар — 3")
    with pytest.raises(ApiError) as err:
        run(shell_spec([1.0], h=4.0), core_points(spacing=12.0))
    e = err.value
    assert e.code == "iso_check" and e.status == 422 and e.extra["group"] == 1 and e.hint
    assert e.message.startswith("Поверхность оболочки «Au ≥ 1» не прошла проверки: поверхность пересекает сама себя")


def test_brick_cache_is_asked_per_threshold() -> None:
    asked: list[tuple[str, int]] = []

    def cache(group: Any, k: int) -> None:
        asked.append((group.name, k))

    run(shell_spec([0.5, 1.0, 2.0], h=4.0), core_points(spacing=12.0), brick_cache=cache)
    assert asked == [("Au", 0), ("Au", 1), ("Au", 2)]


# --- the modules are light ---


def test_modules_do_not_load_numba_or_manifold() -> None:
    code = ("import bf_core.model.shells, bf_core.model.shells_parts, sys; "
            "bad = [m for m in ('numba', 'manifold3d', 'pyfqmr') if m in sys.modules]; "
            "raise SystemExit(3 if bad else 0)")  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120,
                         env={"PYTHONPATH": str(CORE), "BF_NO_WARMUP": "1", "PATH": "/usr/bin:/bin"})  # fmt: skip
    assert out.returncode == 0, out.stderr[-400:]
