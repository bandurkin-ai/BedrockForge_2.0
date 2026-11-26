"""Bodies of the domains and their chronology (подшаг 19.13; ТЗ 8.9.2 «хронология», 8.9.3; Т8.9-1, Т8.9-5; ADR 0026
решения 12–15): the sphere of Т8.9-1 through the whole pipeline (the contact on the sphere to 0.25·h, the volume ±1 %,
closed, χ = 2); the chronology on two overlapping spheres (the younger body whole, the older one minus it, «Прочее»,
the volumes add up to the boundary to 1e-9, the bodies do not overlap); the layers of the fixture scene with the
intrusion (Т8.9-5: five domains from real holes); the same bytes with 1, 2, 4, 8 threads and any order of the holes;
the cancel leaves nothing; `no_contacts`, `rbf_singular` with sources, `iso_check`, `boundary_empty`; the laziness."""

from __future__ import annotations

import math
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
from bf_core.geo import checks, meshes, solids  # noqa: E402
from bf_core.model import data as md  # noqa: E402
from bf_core.model import data_holes as dh  # noqa: E402
from bf_core.model.bodies import BodiesResult, build_bodies  # noqa: E402
from bf_core.model.bodies_parts import box_solid  # noqa: E402
from bf_core.model.spec import ModelSpec, parse_model  # noqa: E402
from bf_core.model.spec_parts import POINT_TYPES  # noqa: E402

CORE = Path(__file__).resolve().parents[1]
KIND = {name: i for i, name in enumerate(POINT_TYPES)}


class Cancelled(Exception):
    pass


# --- synthetic points ---


def fibonacci(n: int, radius: float, centre: Any) -> np.ndarray:
    """n points of the golden spiral on a sphere (Т8.9-1)."""
    i = np.arange(n) + 0.5
    z = 1.0 - 2.0 * i / n
    phi = np.pi * (1.0 + 5.0**0.5) * i
    r = np.sqrt(1.0 - z * z)
    return np.asarray(centre) + radius * np.column_stack([r * np.cos(phi), r * np.sin(phi), z])


def sphere_points(
    gid: int, centre: Any, radius: float, *, contacts: int, shells: int, name: str = "сфера"
) -> md.Points:
    """Contacts f = 0 on the sphere; points at r = R/2 and 3R/2 with the values −R/2 and +R/2 (the signed distance)."""
    c = np.asarray(centre, dtype=np.float64)
    inside = fibonacci(shells, 0.5 * radius, c)
    outside = fibonacci(shells, 1.5 * radius, c)
    xyz = np.concatenate([fibonacci(contacts, radius, c), inside, outside])
    f = np.concatenate([np.zeros(contacts), np.full(shells, -0.5 * radius), np.full(shells, 0.5 * radius)])
    kind = np.concatenate([np.full(contacts, KIND["contact"]), np.full(shells, KIND["inside"]),
                           np.full(shells, KIND["outside"])]).astype(np.int8)  # fmt: skip
    n = len(f)
    p = md.Points(xyz, f, kind, np.full(n, gid, np.int32), np.full(n, -1, np.int8), np.zeros(n, np.int32),
                  np.full((n, 2), np.nan), np.full(n, -1, np.int64), [name], {"points": n}, [])  # fmt: skip
    return p


def concat(parts: list[md.Points]) -> md.Points:
    return md.assemble(parts)


def model(groups: list[dict[str, Any]], box: list[float], *, h: float, **more: Any) -> ModelSpec:
    manual = [{"layer_id": 1 + i, "role": "contact", "group": g["id"]} for i, g in enumerate(groups)
              if not g.get("codes")]  # fmt: skip
    body = {"boundary": {"box": box}, "h": h, "groups": groups, "manual": manual, **more}
    return parse_model(body)


def domain(gid: int, name: str, *codes: str) -> dict[str, Any]:
    return {"id": gid, "name": name, "type": "domain", "codes": list(codes)}


# --- the scene of the holes: five domains of the fixture ---

LAYERS = (("INT", 1), ("ALV", 2), ("SS", 3), ("SH", 4), ("GR", 5))  # the youngest first: the intrusion, then down
SCENE_BOX = [0.0, 0.0, 120.0, 420.0, 420.0, 330.0]


def scene_points(
    holes: int = 100, order: np.ndarray | None = None, d_max: float = 30.0, step: float = 5.0
) -> md.Points:
    """The points of the five domains of `tools.gen.implicit.scene` through `data_holes.domain_points`; `order`
    reorders the holes (the result must not depend on it). The vein is a gap in the code, not a domain."""
    sc = gen.scene(holes)
    n = holes
    perm = np.arange(n) if order is None else np.asarray(order)
    names = [sc["names"][k] for k in perm]
    coll = {k: np.asarray(sc[k])[perm] for k in ("x", "y", "z", "td", "azimuth", "dip")}
    traj = desurvey(Collars(np.arange(n), coll["x"], coll["y"], coll["z"], coll["td"], coll["azimuth"], coll["dip"]),
                    n_holes=n)  # fmt: skip
    litho = sc["litho"]
    where = np.empty(n, dtype=np.int64)
    where[perm] = np.arange(n)  # new number of an old hole
    hole = where[litho["hole"]]
    rows = np.lexsort((litho["from"], hole))
    lines = iv.build_lines(hole[rows], litho["from"][rows], litho["to"][rows])
    code = litho["code"][rows]
    parts = []
    for name, gid in LAYERS:
        c = gen.CODES.index(name)
        state = np.where(code == c, 1, np.where(code == gen.CODES.index("VN"), -1, 0)).astype(np.int8)
        parts.append(dh.domain_points(lines, state, traj, np.arange(n), d_max=d_max, step=step, group=gid, names=names))
    return concat(parts)


def scene_spec(**more: Any) -> ModelSpec:
    groups = [domain(gid, name, name) for name, gid in LAYERS]
    data = {"set_id": 1, "table": "1", "field": "CODE", "d_max": 30, "step": 5}
    return parse_model({"boundary": {"box": SCENE_BOX}, "h": 4.0, "groups": groups, "data": data, **more})


# --- checks of the results ---


def assert_solid(v: np.ndarray, t: np.ndarray) -> float:
    """Closed, manifold, consistently oriented, outward (volume > 0), no self-intersections: the volume."""
    assert len(t) > 0
    assert v.dtype == np.float64 and t.dtype == np.int32
    assert meshes.edge_summary(t, len(v)) == (0, 0)
    assert checks.orientation_conflicts(t, len(v)) == 0
    origin = solids.local_origin(v)
    volume = solids.signed_volume(v - origin, t)
    assert volume > 0.0
    assert checks.self_intersections(v, t, limit=1, samples=1).pairs == 0
    return float(volume)


def bodies_of(res: BodiesResult) -> list[Any]:
    return [o for o in res.outputs if o.kind == "body"]


def overlap(a: Any, b: Any) -> float:
    """The volume of the common part of two bodies (0 when they only touch)."""
    _v, _t, rep = solids.boolean_many([(a.vertices, a.triangles), (b.vertices, b.triangles)], "intersection")
    return 0.0 if rep["empty"] or rep["flat"] else float(rep["volume"])


def same(a: BodiesResult, b: BodiesResult) -> None:
    assert [(o.kind, o.group, o.name) for o in a.outputs] == [(o.kind, o.group, o.name) for o in b.outputs]
    for x, y in zip(a.outputs, b.outputs, strict=True):
        assert x.vertices.tobytes() == y.vertices.tobytes()
        assert x.triangles.tobytes() == y.triangles.tobytes()
        assert x.volume == y.volume


# --- Т8.9-1: the sphere through the pipeline ---


R = 50.0
C = np.array([512_345.0, 6_234_567.0, 412.0])
H = 2.0


@pytest.fixture(scope="module")
def sphere() -> tuple[BodiesResult, np.ndarray]:
    pts = concat([sphere_points(1, C, R, contacts=2000, shells=500)])
    box = [*(C - 80.0), *(C + 80.0)]
    spec = model([domain(1, "Шар")], box, h=H)
    return build_bodies(spec, pts, box_solid(box), threads=4), np.array(box)


def test_sphere_t891(sphere: tuple[BodiesResult, np.ndarray]) -> None:
    res, box = sphere
    assert [(o.kind, o.name) for o in res.outputs] == [("surface", "Шар: контакт"), ("body", "Шар"), ("body", "Прочее")]
    contact, body, rest = res.outputs
    r = np.linalg.norm(contact.vertices - C, axis=1)
    assert np.abs(r - R).max() <= 0.25 * H  # Т8.9-1: every vertex within 0.25·h of the sphere
    volume = assert_solid(body.vertices, body.triangles)
    assert volume == pytest.approx(4.0 / 3.0 * math.pi * R**3, rel=0.01)
    assert body.volume == pytest.approx(volume, rel=1e-12)
    v, t = body.vertices, body.triangles
    edges = 3 * len(t) // 2
    assert len(v) - edges + len(t) == 2  # χ = 2
    assert contact.volume is None and contact.area == pytest.approx(4.0 * math.pi * R**2, rel=0.01)
    assert rest.group is None and rest.volume == pytest.approx(160.0**3 - body.volume, rel=1e-9)
    assert res.stats["balance"] <= 1e-9
    fit = res.fits[1]
    assert fit.stats["points"] == 3000 and fit.stats["max_residual"] < 1e-6 * R  # η = 0: the data are reproduced


def test_sphere_is_the_same_bytes_with_threads(sphere: tuple[BodiesResult, np.ndarray]) -> None:
    base, box = sphere
    pts = concat([sphere_points(1, C, R, contacts=2000, shells=500)])
    spec = model([domain(1, "Шар")], [*box], h=H)
    for threads in (1, 2, 8):
        same(base, build_bodies(spec, pts, box_solid(box), threads=threads))


# --- the chronology: two overlapping spheres ---


def lens(radius: float, d: float) -> float:
    """The volume of the common part of two spheres of one radius at the distance d."""
    return math.pi * (4.0 * radius + d) * (2.0 * radius - d) ** 2 / 12.0


def test_chronology_of_two_spheres() -> None:
    """A (the younger, first) is whole; B loses what A takes; the rest is the boundary minus both."""
    r, d = 40.0, 50.0
    ca, cb = C, C + np.array([d, 0.0, 0.0])
    pts = concat([sphere_points(1, ca, r, contacts=1200, shells=300, name="A"),
                  sphere_points(2, cb, r, contacts=1200, shells=300, name="B")])  # fmt: skip
    box = [*(C - np.array([70.0, 70.0, 70.0])), *(C + np.array([120.0, 70.0, 70.0]))]
    spec = model([domain(1, "A"), domain(2, "B")], box, h=2.0)
    res = build_bodies(spec, pts, box_solid(box), threads=2)
    a, b, rest = bodies_of(res)
    sphere_v = 4.0 / 3.0 * math.pi * r**3
    assert a.volume == pytest.approx(sphere_v, rel=0.01)
    assert b.volume == pytest.approx(sphere_v - lens(r, d), rel=0.02)  # B without the lens that A holds
    assert a.volume + b.volume == pytest.approx(2.0 * sphere_v - lens(r, d), rel=0.01)  # the union
    total = 190.0 * 140.0 * 140.0
    assert a.volume + b.volume + rest.volume == pytest.approx(total, rel=1e-9)  # Т8.9-5: the boundary is tiled
    assert res.stats["balance"] <= 1e-9
    for x, y in ((a, b), (a, rest), (b, rest)):
        assert overlap(x, y) <= 1e-9 * total  # the bodies do not overlap
    for o in (a, b, rest):
        assert_solid(o.vertices, o.triangles)
    contact_b = next(o for o in res.outputs if o.kind == "surface" and o.group == 2)
    inside_a = np.linalg.norm(contact_b.vertices - ca, axis=1) < r - 0.5 * 2.0
    assert not inside_a.any()  # the contact of B hidden by A is not in the sheet


def test_older_group_first_in_the_list_is_the_younger() -> None:
    """The order of `groups` is the age: swapping them swaps who keeps the lens."""
    r, d = 40.0, 50.0
    cb = C + np.array([d, 0.0, 0.0])
    pts = concat(
        [sphere_points(1, C, r, contacts=1200, shells=300), sphere_points(2, cb, r, contacts=1200, shells=300)]
    )
    box = [*(C - 70.0), *(C + np.array([120.0, 70.0, 70.0]))]
    first = build_bodies(model([domain(1, "A"), domain(2, "B")], box, h=2.0), pts, box_solid(box), threads=2)
    swapped = build_bodies(model([domain(2, "B"), domain(1, "A")], box, h=2.0), pts, box_solid(box), threads=2)
    va = {o.name: o.volume for o in bodies_of(first)}
    vs = {o.name: o.volume for o in bodies_of(swapped)}
    assert va["A"] > va["B"] and vs["B"] > vs["A"]
    assert va["A"] == pytest.approx(vs["B"], rel=1e-6) or abs(va["A"] - vs["B"]) < 0.02 * va["A"]


# --- Т8.9-5: the layers of the fixture scene ---


@pytest.fixture(scope="module")
def layers() -> tuple[BodiesResult, md.Points]:
    pts = scene_points()
    return build_bodies(scene_spec(), pts, box_solid(SCENE_BOX), threads=4), pts


def test_scene_bodies_tile_the_boundary_t895(layers: tuple[BodiesResult, md.Points]) -> None:
    res, _ = layers
    bodies = bodies_of(res)
    names = [o.name for o in bodies]
    assert names[:5] == [n for n, _ in LAYERS] or set(names[:5]) <= {n for n, _ in LAYERS}
    for o in bodies:
        assert_solid(o.vertices, o.triangles)
    boundary = 420.0 * 420.0 * 210.0
    assert sum(o.volume for o in bodies) == pytest.approx(boundary, rel=1e-9)  # Σ the volumes = the boundary
    assert res.stats["balance"] <= 1e-9
    for i, x in enumerate(bodies):
        for y in bodies[i + 1 :]:
            assert overlap(x, y) <= 1e-9 * boundary
    intrusion = next(o for o in bodies if o.name == "INT")
    sphere_v = 4.0 / 3.0 * math.pi * gen.SPHERE_RADIUS**3
    assert 0.4 * sphere_v < intrusion.volume < 1.3 * sphere_v  # nine holes meet a sphere of 55 m: a rough body
    assert np.abs(intrusion.vertices.mean(axis=0) - gen.SPHERE_CENTRE).max() < 20.0
    assert {o.group for o in res.outputs} >= {1, 2, 3, 4, 5, None}
    assert all(o.volume is None for o in res.outputs if o.kind == "surface")
    assert any("Группа «INT»" in line for line in res.journal)
    assert res.stats["time"]["total"] > 0.0 and set(res.fits) == {1, 2, 3, 4, 5}


def test_scene_is_the_same_bytes_for_threads_and_order_of_holes(layers: tuple[BodiesResult, md.Points]) -> None:
    base, pts = layers
    rng = np.random.default_rng(7)
    shuffled = scene_points(order=rng.permutation(100))
    assert shuffled.to_parquet() == pts.to_parquet()  # the points: the same bytes whatever the order of the holes
    for threads, data in ((1, pts), (2, pts), (8, shuffled)):
        same(base, build_bodies(scene_spec(), data, box_solid(SCENE_BOX), threads=threads))


# --- the cancel ---


def test_cancel_leaves_nothing() -> None:
    pts = concat([sphere_points(1, C, R, contacts=600, shells=200)])
    box = [*(C - 80.0), *(C + 80.0)]
    spec = model([domain(1, "Шар")], box, h=4.0)
    seen: list[tuple[float, str]] = []
    res = build_bodies(spec, pts, box_solid(box), tick=lambda f, m: seen.append((f, m)))
    assert len(res.outputs) == 3 and len(seen) > 10
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and 0.0 <= fractions[0] and fractions[-1] == 1.0
    assert any(m.startswith("Участки") for _, m in seen) and any(m.startswith("Изоповерхность") for _, m in seen)
    for stop in (1, len(seen) // 3, len(seen) // 2, 2 * len(seen) // 3, len(seen) - 2):
        count = [0]

        def tick(_f: float, _m: str, stop: int = stop, count: list[int] = count) -> None:
            count[0] += 1
            if count[0] == stop:
                raise Cancelled

        with pytest.raises(Cancelled):
            build_bodies(spec, pts, box_solid(box), tick=tick)  # nothing returned, nothing written anywhere


# --- the degenerate cases ---


def test_no_contacts_names_the_group() -> None:
    p = sphere_points(1, C, R, contacts=0, shells=300)
    only_inside = p.take(np.flatnonzero(p.f < 0.0))
    box = [*(C - 80.0), *(C + 80.0)]
    with pytest.raises(ApiError) as err:
        build_bodies(model([domain(1, "Шар")], box, h=4.0), concat([only_inside]), box_solid(box))
    assert err.value.code == "no_contacts" and err.value.status == 422 and err.value.extra["group"] == 1
    assert "Шар" in err.value.message and err.value.hint
    with pytest.raises(ApiError) as none:
        build_bodies(model([domain(1, "Шар")], box, h=4.0), md.Points.empty(), box_solid(box))
    assert none.value.code == "no_contacts"


def test_coincident_points_with_different_values_are_rbf_singular_with_sources() -> None:
    p = sphere_points(1, C, R, contacts=300, shells=60, name="скв. A-1")
    k = int(np.flatnonzero(p.kind == KIND["contact"])[0])
    twin = p.take([k])
    twin = md.Points(twin.xyz, np.array([3.0]), np.array([KIND["inside"]], np.int8), twin.group, twin.wing, twin.src,
                     np.array([[12.0, 14.0]]), twin.vertex, ["скв. A-1"], {"points": 1}, [])  # fmt: skip
    box = [*(C - 80.0), *(C + 80.0)]
    with pytest.raises(ApiError) as err:
        build_bodies(model([domain(1, "Шар")], box, h=4.0), concat([p, twin]), box_solid(box))
    e = err.value
    assert e.code == "rbf_singular" and e.status == 422 and e.extra["group"] == 1
    assert e.extra["sources"] and all("скв. A-1" in s for s in e.extra["sources"]) and len(e.extra["sources"]) <= 20
    assert "Шар" in e.message and e.hint


def test_boundary_empty_and_open() -> None:
    p = concat([sphere_points(1, C, R, contacts=300, shells=60)])
    spec = model([domain(1, "Шар")], [*(C - 80.0), *(C + 80.0)], h=4.0)
    flat = box_solid([*C, C[0] + 10.0, C[1] + 10.0, C[2]])
    with pytest.raises(ApiError) as err:
        build_bodies(spec, p, flat)
    assert err.value.code == "boundary_empty" and err.value.status == 422
    with pytest.raises(ApiError) as none:
        build_bodies(spec, p, (np.zeros((0, 3)), np.zeros((0, 3), np.int32)))
    assert none.value.code == "boundary_empty"
    v, t = box_solid([*(C - 80.0), *(C + 80.0)])
    with pytest.raises(ApiError) as op:
        build_bodies(spec, p, (v, t[:-1]))
    assert op.value.code == "wireframe_open" and op.value.extra["param"] == "boundary"


def test_failed_checks_are_iso_check_with_the_group(monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.geo import iso_post

    p = concat([sphere_points(1, C, R, contacts=300, shells=60)])
    box = [*(C - 80.0), *(C + 80.0)]
    monkeypatch.setattr(
        iso_post, "mesh_failure", lambda *a, **k: "поверхность пересекает сама себя: пар треугольников — 3"
    )
    with pytest.raises(ApiError) as err:
        build_bodies(model([domain(1, "Шар")], box, h=4.0), p, box_solid(box))
    e = err.value
    assert e.code == "iso_check" and e.extra["group"] == 1 and e.status == 422
    assert e.message.startswith("Поверхность группы «Шар» не прошла проверки: поверхность пересекает сама себя")
    assert e.hint


def test_a_model_without_domains_has_no_bodies() -> None:
    box = [*(C - 80.0), *(C + 80.0)]
    spec = parse_model({"boundary": {"box": box}, "h": 4.0, "groups": []})
    res = build_bodies(spec, md.Points.empty(), box_solid(box))
    assert res.outputs == [] and res.fits == {}


def test_a_domain_outside_the_boundary_has_no_body_and_the_rest_is_the_boundary() -> None:
    far = C + np.array([600.0, 0.0, 0.0])
    p = concat([sphere_points(1, far, 40.0, contacts=300, shells=60)])
    box = [*(C - 80.0), *(C + 80.0)]
    res = build_bodies(model([domain(1, "Шар")], box, h=4.0), p, box_solid(box))
    names = [o.name for o in res.outputs]
    assert names == ["Прочее"] and res.outputs[0].volume == pytest.approx(160.0**3, rel=1e-9)
    assert any("тела нет" in line or "тело пусто" in line for line in res.journal)


# --- the modules are light ---


def test_module_does_not_load_numba_or_manifold() -> None:
    code = ("import bf_core.model.bodies, bf_core.model.bodies_parts, sys; "
            "bad = [m for m in ('numba', 'manifold3d', 'pyfqmr') if m in sys.modules]; "
            "raise SystemExit(3 if bad else 0)")  # fmt: skip
    run = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120,
                         env={"PYTHONPATH": str(CORE), "BF_NO_WARMUP": "1", "PATH": "/usr/bin:/bin"})  # fmt: skip
    assert run.returncode == 0, run.stderr[-400:]
