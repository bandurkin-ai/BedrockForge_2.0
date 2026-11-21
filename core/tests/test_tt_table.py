"""«Истинная мощность», the table (ТЗ 8.5.1–8.5.2; ADR 0021 §3–4, 8, 12): the chain of sources and TT_SRC, the
bits of TT_FLAG, domains (a field of the table, of another table, bodies), the core in the chain, a source that
is not there, missing values (empty, never 0), holes without a trajectory, Т8.5-3 on a table, the summary, one
hash with 1, 2, 4 and 8 threads.

Tolerances: TT on a straight hole — 1e−12·l against the expanded formula of ТЗ 8.5.1 (the kernel takes the
vector one); on an arc — 1e−4·l (Т8.5-10); angles — 1e−9° (given ones are written as given, bit for bit)."""

from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

import bf_core.intercepts as pkg
from bf_core.composites import coding, jobs_coding, source
from bf_core.composites import jobs as cj
from bf_core.composites.intervals import CompositeError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.drillholes.desurvey import Collars, Surveys, desurvey
from bf_core.intercepts import core_orient as co
from bf_core.intercepts import directions as dr
from bf_core.intercepts import formulas as fm
from bf_core.intercepts import orient, tt
from bf_core.intercepts import spec as sp
from bf_core.projects.service import projects_of
from tests.meshutil import UTM, cube

NAN = math.nan
TOL = 1e-12
IDS = {"A": 101, "B": 102, "C": 103, "D": 104, "E": 105, "Z": 999}  # dh_holes.id; «Z» — not in the trajectories
GLOBAL = sp.Source("global", alpha=30.0, dipdir=120.0)
NONE, GLOB, DOM, CROSS, NEAR, MEAS, NEXT = orient.SRC_LABELS


def scene() -> SimpleNamespace:
    """The trajectories of a set (as `drillholes.trajectory.SetTrajectories`), collars on UTM-sized coordinates:
    A — vertical; B — θ = 30°, Aс = 90°; C — an arc from vertical to 45° to the north over 100 m; D — no collar
    coordinates: no trajectory; E — θ = 60°, Aс = 120°."""
    x = np.array([0.0, 100.0, 200.0, NAN, 300.0]) + UTM[0]
    traj = desurvey(
        Collars(np.arange(5), x, np.full(5, UTM[1]), np.full(5, 300.0), np.full(5, 100.0),
                azimuth=[NAN, 90.0, NAN, 0.0, 120.0], dip=[NAN, -60.0, NAN, -90.0, -30.0]),
        Surveys([2, 2], [0.0, 100.0], [0.0, 0.0], [-90.0, -45.0]),
        n_holes=5,
    )  # fmt: skip
    ids = np.array([101, 102, 103, 104, 105], dtype=np.int64)
    return SimpleNamespace(hole_ids=ids, index={int(h): i for i, h in enumerate(ids)}, traj=traj)


def table(table_id: int, rows: list[tuple[Any, ...]], *, kind: str = "assay", name: str = "Пробы") -> source.Lines:
    """A snapshot of a table as `source.read` gives it: rows (hole, from, to, {field: value}) put into line
    order; a field with a text value is a text field."""
    rows = sorted(rows, key=lambda r: (r[0], r[1], r[2]))
    holes = sorted({r[0] for r in rows})
    starts = np.searchsorted([r[0] for r in rows], holes).tolist() + [len(rows)]
    names = list(dict.fromkeys(k for r in rows for k in (r[3] if len(r) > 3 else {})))
    values = {k: [r[3].get(k) if len(r) > 3 else None for r in rows] for k in names}
    out = source.Lines(
        set_id=1, table_id=table_id, name=name, name_key=name.casefold(), token="t", rev=1, kind=kind, fields={},
        row_ids=np.arange(1, len(rows) + 1, dtype=np.int64), holes=holes,
        hole_db_ids=np.array([IDS[h] for h in holes], dtype=np.int64), starts=np.array(starts, dtype=np.int64),
        from_m=np.array([r[1] for r in rows], dtype=float), to_m=np.array([r[2] for r in rows], dtype=float),
    )  # fmt: skip
    for k, col in values.items():
        if any(isinstance(v, str) for v in col):
            out.codes[k], out.labels[k] = source.text_codes(np.array(col, dtype=object))
        else:
            out.numbers[k] = np.array([NAN if v is None else v for v in col], dtype=float)
    return out


def spec(*chain: sp.Source, eps: float = 0.1, step: float = 1.0, angles: bool = True) -> sp.TtSpec:
    return sp.TtSpec(1, 10, "Пробы", list(chain), eps, step, angles, {})


def by_domain(field: str, kind: str, attitudes: list[tuple[str, float, float]], *, table_id: int = 10,
              wireframes: Any = None) -> sp.Source:  # fmt: skip
    d = cj.DomainSpec(table_id, "Таблица", field, kind, wireframes)
    return sp.Source("domain", domain=d, attitudes=tuple(sp.Attitude(*a) for a in attitudes))


def by_core(d_core: float = 10.0, line: str = "boh", table_id: int = 14) -> sp.Source:
    return sp.Source("core", table_id=table_id, table_name="Керн", table_kind="event", alpha_field="ALPHA_K",
                     beta_field="BETA_K", line=line, d_core=d_core)  # fmt: skip


def straight(length: float, alpha: float, dipdir: float, theta: float, azimuth: float) -> float:
    """The expanded formula of ТЗ 8.5.1 — the reference of a straight hole."""
    return float(fm.tt_expanded(length, alpha, dipdir, theta, azimuth))


def digest(t: tt.TtTable) -> str:
    h = hashlib.sha256()
    for a in (t.tt, t.flag, t.src, t.alpha, t.dipdir, t.theta, t.azimuth):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def labels(t: tt.TtTable) -> list[str]:
    return t.src_text().tolist()  # type: ignore[no-any-return]


# --- one source ----------------------------------------------------------------------------------------------


def test_global_orientation_fills_the_columns() -> None:
    """Т8.5-1 on a table (a vertical hole, α = 30°: m = 0,8660254·l) and a straight inclined hole; the columns
    are those of the contract; α and Aп are written as given."""
    lines = table(10, [("A", 0.0, 10.0), ("A", 10.0, 12.5), ("B", 5.0, 7.5), ("C", 0.0, 100.0)])
    t = tt.of_table(spec(GLOBAL), lines, scene())
    c30 = math.cos(math.radians(30.0))
    assert t.tt[:3] == pytest.approx([10.0 * c30, 2.5 * c30, straight(2.5, 30.0, 120.0, 30.0, 90.0)], abs=TOL * 10)
    assert t.tt[0] == pytest.approx(8.660254, abs=5e-7)
    assert t.flag.dtype == np.uint8 and t.flag.tolist() == [0, 0, 0, 0]
    assert labels(t) == [GLOB] * 4
    assert t.alpha.tolist() == [30.0] * 4 and t.dipdir.tolist() == [120.0] * 4  # as given, bit for bit
    assert t.theta == pytest.approx([0.0, 0.0, 30.0, 22.5], abs=1e-9)  # the middle of the arc of C
    assert t.azimuth == pytest.approx([0.0, 0.0, 90.0, 0.0], abs=1e-9)
    cols = t.columns(angles=True)
    assert [name for name, _kind, _v in cols] == spec(GLOBAL).columns() == [*sp.TT_COLUMNS, *sp.ANGLE_COLUMNS]
    assert [kind for _n, kind, _v in cols] == ["number", "number", "text", "number", "number", "number", "number"]
    assert all(len(v) == 4 for _n, _k, v in cols)
    assert [n for n, _k, _v in t.columns(angles=False)] == spec(GLOBAL, angles=False).columns() == list(sp.TT_COLUMNS)


def test_level_plane_on_an_arc_is_the_vertical_extent() -> None:
    """Т8.5-10 on a table: a level plane, a curved hole without rises — TT is the vertical extent (1e−4·l)."""
    lines = table(10, [("C", 0.0, 100.0), ("C", 20.0, 60.0)])
    t = tt.of_table(spec(sp.Source("global", alpha=0.0, dipdir=0.0)), lines, scene())
    radius = 100.0 / (math.pi / 4.0)  # the arc turns by 45° over 100 m
    drop = [radius * (math.sin(b / radius) - math.sin(a / radius)) for a, b in ((0.0, 100.0), (20.0, 60.0))]
    assert t.tt[0] == pytest.approx(drop[0], abs=1e-4 * 100.0) and t.tt[1] == pytest.approx(drop[1], abs=1e-4 * 40.0)
    assert t.flag.tolist() == [0, 0]


def test_t8_5_3_on_a_table() -> None:
    """Т8.5-3. α = 30°, θ = 60°, Aс = Aп: m = 0, TT_FLAG = 1 — TT is written as it came out, with the flag."""
    lines = table(10, [("E", 0.0, 4.0), ("E", 4.0, 5.0), ("E", 50.0, 62.5), ("A", 0.0, 4.0)])
    t = tt.of_table(spec(GLOBAL), lines, scene())
    e = np.array([h == "E" for h in ("A", "E", "E", "E")])
    assert np.abs(t.tt[e]).max() <= TOL * 12.5 and not np.isnan(t.tt).any()
    assert t.flag.tolist() == [0, 1, 1, 1] and labels(t) == [GLOB] * 4
    assert t.theta[e] == pytest.approx(60.0, abs=1e-9) and t.azimuth[e] == pytest.approx(120.0, abs=1e-9)
    assert t.summary["flags"] == {"1": 3, "2": 0, "4": 0, "8": 0} and t.summary["src"] == {GLOB: 4}
    # the threshold is the parameter ε_в: the vertical hole A with α = 60,1° has TT / l = 0,4985 < 0,5
    assert tt.of_table(spec(sp.Source("global", alpha=60.1, dipdir=0.0), eps=0.5), lines, scene()).flag[0] == 1


# --- domains -------------------------------------------------------------------------------------------------


def test_domain_by_a_text_field_of_the_table() -> None:
    """Код → α, Aп; a code with spaces around is the same code; no code, a code that is not in the table of
    domains — no orientation: TT is empty (not 0), bit 2, «нет»."""
    lines = table(10, [("A", 0.0, 2.0, {"DOM": "QZ"}), ("A", 2.0, 4.0, {"DOM": "SH "}), ("A", 4.0, 6.0, {"DOM": None}),
                       ("A", 6.0, 8.0, {"DOM": "XX"}), ("B", 0.0, 2.0, {"DOM": "QZ"})])  # fmt: skip
    dom = by_domain("DOM", "text", [("QZ", 30.0, 120.0), ("SH", 60.0, 300.0), ("YY", 10.0, 10.0)])
    t = tt.of_table(spec(dom), lines, scene())
    assert labels(t) == [DOM, DOM, NONE, NONE, DOM]
    assert t.flag.tolist() == [0, 0, 2, 2, 0]
    assert t.tt[[0, 1, 4]] == pytest.approx(
        [2.0 * math.cos(math.radians(30.0)), 1.0, straight(2.0, 30.0, 120.0, 30.0, 90.0)], abs=TOL * 10)  # fmt: skip
    assert np.isnan(t.tt[[2, 3]]).all()  # empty, never 0
    assert t.alpha[[0, 1, 4]].tolist() == [30.0, 60.0, 30.0] and t.dipdir[[0, 1, 4]].tolist() == [120.0, 300.0, 120.0]
    assert np.isnan(t.alpha[[2, 3]]).all() and np.isnan(t.dipdir[[2, 3]]).all()
    assert t.theta[:4] == pytest.approx(0.0, abs=1e-9)  # the hole is known where the plane is not
    rep = t.summary["sources"][0]
    assert (rep["source"], rep["rows"], rep["no_code"], rep["not_listed"]) == ("domain", 3, 1, 1)
    assert any("«XX»" in line for line in t.journal)


def test_domain_by_a_numeric_field() -> None:
    """Numeric codes are matched as the table of domains writes them: 1.0 — «1», 2.5 — «2.5», −0.0 — «0»."""
    lines = table(10, [("A", 0.0, 1.0, {"ZONE": 1.0}), ("A", 1.0, 2.0, {"ZONE": 2.5}), ("A", 2.0, 3.0, {"ZONE": NAN}),
                       ("A", 3.0, 4.0, {"ZONE": -0.0}), ("A", 4.0, 5.0, {"ZONE": 3.0})])  # fmt: skip
    dom = by_domain("ZONE", "number", [("1", 10.0, 0.0), ("2.5", 20.0, 90.0), ("0", 40.0, 180.0)])
    t = tt.of_table(spec(dom), lines, scene())
    assert labels(t) == [DOM, DOM, NONE, DOM, NONE]
    assert t.alpha[[0, 1, 3]].tolist() == [10.0, 20.0, 40.0]
    assert t.tt[[0, 1, 3]] == pytest.approx([math.cos(math.radians(a)) for a in (10.0, 20.0, 40.0)], abs=TOL)
    assert np.isnan(t.tt[[2, 4]]).all() and t.flag.tolist() == [0, 0, 2, 0, 2]


def test_domain_of_another_table_takes_the_longest_code() -> None:
    """The code of an interval from another interval table — the one with the greatest length inside it; equal
    lengths — the first down the hole; not covered, a hole the table does not have — none."""
    litho = table(12, [("A", 0.0, 4.0, {"ROCK": "QZ"}), ("A", 4.0, 10.0, {"ROCK": "SH"}),
                       ("B", 0.0, 50.0, {"ROCK": "QZ"})], kind="interval", name="Литология")  # fmt: skip
    lines = table(10, [("A", 0.0, 3.0), ("A", 3.0, 5.0), ("A", 3.0, 6.0), ("A", 10.0, 12.0), ("B", 1.0, 2.0),
                       ("C", 0.0, 1.0)])  # fmt: skip
    dom = by_domain("ROCK", "text", [("QZ", 30.0, 120.0), ("SH", 60.0, 300.0)], table_id=12)
    t = tt.of_table(spec(dom), lines, scene(), tables={12: litho})
    assert labels(t) == [DOM, DOM, DOM, NONE, DOM, NONE]
    assert t.alpha[[0, 1, 2, 4]].tolist() == [30.0, 30.0, 60.0, 30.0]  # 3–5: 1 m of each — the first; 3–6: SH
    assert t.flag.tolist() == [0, 0, 0, 2, 0, 2]
    with pytest.raises(ValueError, match="12"):
        tt.of_table(spec(dom), lines, scene())  # the table of the domain was not read


def test_domain_by_bodies() -> None:
    """Каркасы-домены: the code is the body with the greatest length inside the interval; the rest goes down
    the chain to the global orientation."""
    v, tri = cube(1, 1.0, offset=[0.0, 0.0, 0.0], seed=None)
    low = np.array([UTM[0] - 5.0, UTM[1] - 5.0, 260.0])  # the vertical hole A is inside from 20 to 40 m
    body = coding.Body.of("Тело 1", v * np.array([10.0, 10.0, 20.0]) + low, tri)
    wf = SimpleNamespace(targets=[("state", "stats")])
    asked: list[Any] = []
    lines = table(10, [("A", 0.0, 10.0), ("A", 15.0, 22.0), ("A", 25.0, 30.0), ("A", 38.0, 50.0), ("B", 0.0, 5.0),
                       ("D", 0.0, 5.0)])  # fmt: skip
    dom = by_domain("", "wireframes", [("Тело 1", 45.0, 90.0)], wireframes=wf)
    t = tt.of_table(spec(dom, sp.Source("global", alpha=10.0, dipdir=0.0)), lines, scene(),
                    bodies=lambda given: asked.append(given) or [body])  # fmt: skip
    assert asked == [wf]
    assert labels(t) == [GLOB, DOM, DOM, DOM, GLOB, GLOB]
    assert t.alpha.tolist() == [10.0, 45.0, 45.0, 45.0, 10.0, 10.0]
    assert t.tt[1:4] == pytest.approx(np.array([7.0, 5.0, 12.0]) * math.cos(math.radians(45.0)), abs=TOL * 12)
    assert np.isnan(t.tt[5]) and t.flag.tolist() == [0, 0, 0, 0, 0, 8]  # D has no trajectory


# --- the chain -----------------------------------------------------------------------------------------------


def fake_mesh(calls: list[np.ndarray]) -> orient.MeshSource:
    """A stand-in for `orient_mesh` (15.7): a plane for the intervals above 3 m — a crossing above 2 m."""

    def give(rows: orient.Intervals, traj: Any, s: Any, *, todo: np.ndarray, threads: int = 1,
             tick: Any = None) -> orient.Given:  # fmt: skip
        assert s.kind == "mesh" and traj is not None and todo.dtype == np.bool_ and todo.shape == (rows.n,)
        calls.append(todo.copy())
        normal = np.full((rows.n, 3), NAN)
        src = np.zeros(rows.n, dtype=np.uint8)
        pick = rows.frm < 3.0
        normal[pick] = fm.plane_normal(45.0, 270.0)
        src[pick] = np.where(rows.frm[pick] < 2.0, orient.SRC_MESH_CROSSING, orient.SRC_MESH_NEAREST)
        return orient.Given(normal, src, journal=["каркас: проверка"], report={"crossings": 1})

    return give


def chain_table() -> source.Lines:
    return table(10, [("A", 0.0, 2.0, {"DOM": "QZ"}), ("A", 2.0, 4.0, {"DOM": None}), ("A", 4.0, 6.0, {"DOM": None}),
                      ("A", 6.0, 8.0, {"DOM": "XX"}), ("A", 8.0, 8.0, {"DOM": "QZ"})])  # fmt: skip


def test_chain_takes_the_first_source_that_gave() -> None:
    """Домен → каркас → глобальная: every interval takes the first source that gave a plane; a later source
    is asked only about the intervals still without one, and never about a row out of the calculation."""
    mesh = sp.Source("mesh", meshes=[], d_max=50.0, r_n=0.0)
    dom = by_domain("DOM", "text", [("QZ", 30.0, 120.0)])
    calls: list[np.ndarray] = []
    t = tt.of_table(spec(dom, mesh, GLOBAL), chain_table(), scene(), mesh=fake_mesh(calls))
    assert labels(t) == [DOM, NEAR, GLOB, GLOB, NONE]
    assert [c.tolist() for c in calls] == [[False, True, True, True, False]]
    assert t.alpha == pytest.approx([30.0, 45.0, 30.0, 30.0, NAN], abs=1e-9, nan_ok=True)
    assert t.dipdir == pytest.approx([120.0, 270.0, 120.0, 120.0, NAN], abs=1e-9, nan_ok=True)
    assert t.tt[1] == pytest.approx(2.0 * math.cos(math.radians(45.0)), abs=TOL)
    assert t.flag.tolist() == [0, 0, 0, 0, 2] and np.isnan(t.tt[4])
    assert [(r["source"], r["rows"]) for r in t.summary["sources"]] == [("domain", 1), ("mesh", 1), ("global", 2)]
    assert t.summary["sources"][1]["crossings"] == 1 and "каркас: проверка" in t.journal
    # the order is the priority: the mesh first takes the two intervals above 3 m, a crossing and the nearest
    first = tt.of_table(spec(mesh, dom, GLOBAL), chain_table(), scene(), mesh=fake_mesh([]))
    assert labels(first) == [CROSS, NEAR, GLOB, GLOB, NONE]


def test_angles_of_a_plane_given_by_its_normal() -> None:
    """A source that gives normals only (a wireframe, the core): ALPHA and DIPDIR are those of
    `formulas.plane_angles` — the upper side, Aп ∈ [0°; 180°) of a vertical plane, Aп = 0 of a level one."""
    rng = np.random.Generator(np.random.PCG64(4))
    u = rng.normal(size=(2000, 3))
    u /= np.linalg.norm(u, axis=1, keepdims=True)  # both sides of the planes
    az = np.radians(np.concatenate([np.arange(15.0, 180.0, 15.0), np.arange(195.0, 360.0, 15.0)]))
    upright = np.column_stack([np.sin(az), np.cos(az), np.zeros(len(az))])
    tilt = math.radians(1e-7)
    flat = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [math.sin(tilt), 0.0, math.cos(tilt)],
                     [0.0, -math.sin(tilt), -math.cos(tilt)]])  # fmt: skip
    normals = np.concatenate([u, upright, flat])
    lines = table(10, [("A", float(k), float(k + 1)) for k in range(len(normals))])

    def give(rows: orient.Intervals, traj: Any, s: Any, *, todo: np.ndarray, threads: int = 1,
             tick: Any = None) -> orient.Given:  # fmt: skip
        return orient.Given(normals, orient.SRC_MESH_CROSSING)

    t = tt.of_table(spec(sp.Source("mesh", meshes=[], d_max=50.0, r_n=0.0)), lines, scene(), mesh=give, threads=2)
    alpha, dipdir = fm.plane_angles(normals)
    assert float(np.abs(t.alpha - alpha).max()) <= 1e-12 and float(np.abs(t.dipdir - dipdir).max()) <= 1e-12
    assert t.dipdir[2000 : 2000 + len(az)] == pytest.approx(np.degrees(az) % 180.0, abs=1e-12)
    assert t.alpha[2000 : 2000 + len(az)].tolist() == [90.0] * len(az)
    assert t.dipdir[-4:].tolist() == [0.0] * 4 and t.alpha[-4:] == pytest.approx([0.0, 0.0, 1e-7, 1e-7], abs=1e-12)
    assert float(np.abs(t.tt - np.abs(normals[:, 2])).max()) <= TOL  # the vertical hole A, 1 m each
    assert set(labels(t)) == {CROSS} and not np.signbit(t.dipdir).any()


@pytest.mark.parametrize("given", ["absent", "none"])
def test_a_source_that_is_not_there_gives_way_to_the_next(given: str) -> None:
    """No function of the mesh source (or it answers None: «не считается») — the next source of the chain is
    taken and the result says so; alone in the chain — no orientation at all, nothing fails."""
    mesh = sp.Source("mesh", meshes=[], d_max=50.0, r_n=0.0)
    fn = None if given == "absent" else (lambda rows, traj, s, *, todo, threads=1, tick=None: None)
    t = tt.of_table(spec(mesh, GLOBAL), chain_table(), scene(), mesh=fn)
    assert labels(t) == [GLOB, GLOB, GLOB, GLOB, NONE]
    assert t.summary["absent"] == ["mesh"] and any("каркас" in line for line in t.journal)
    assert t.summary["sources"][0] == {"source": "mesh", "rows": 0, "absent": True}
    alone = tt.of_table(spec(mesh), chain_table(), scene(), mesh=fn)
    assert labels(alone) == [NONE] * 5 and alone.flag.tolist() == [2] * 5 and np.isnan(alone.tt).all()
    assert tt.of_table(spec(), chain_table(), scene()).summary["src"] == {NONE: 5}  # no source at all (8.5.3)


def core_table() -> source.Lines:
    return table(14, [
        ("B", 6.0, 6.0, {"ALPHA_K": 40.0, "BETA_K": 10.0}), ("B", 8.0, 8.0, {"ALPHA_K": NAN, "BETA_K": 10.0}),
        ("B", 9.0, 9.0, {"ALPHA_K": 120.0, "BETA_K": 10.0}), ("B", 20.0, 20.0, {"ALPHA_K": 55.0, "BETA_K": 200.0}),
        ("A", 5.0, 5.0, {"ALPHA_K": 50.0, "BETA_K": 0.0}), ("A", 30.0, 30.0, {"ALPHA_K": 3.0, "BETA_K": 0.0}),
        ("D", 3.0, 3.0, {"ALPHA_K": 30.0, "BETA_K": 90.0}),
        ("E", 1.0, 1.0, {"ALPHA_K": 60.0, "BETA_K": 90.0}), ("E", 3.0, 3.0, {"ALPHA_K": 60.0, "BETA_K": 100.0}),
    ], kind="event", name="Керн")  # fmt: skip


def test_core_in_the_chain() -> None:
    """Керн → глобальная. A measurement inside: TT = l·sin α_к («керн: замер»); several — the principal vector;
    the nearest within d_к — «керн: ближайший», TT along the hole; a measurement of a vertical hole or of a hole
    without a trajectory is not turned: TT = l·sin α_к, bit 4, no ALPHA and DIPDIR (bit 8 — no trajectory)."""
    lines = table(10, [("B", 5.0, 7.5), ("B", 13.0, 15.0), ("B", 40.0, 42.0), ("A", 4.0, 6.0), ("A", 29.0, 31.0),
                       ("D", 2.0, 4.0), ("D", 10.0, 12.0), ("E", 0.0, 4.0)])  # fmt: skip
    t = tt.of_table(spec(by_core(), GLOBAL), lines, scene(), tables={14: core_table()})
    # line order: A 4–6, A 29–31, B 5–7.5, B 13–15 (5 m above the measurement at 20 m), B 40–42, D 2–4, D 10–12, E 0–4
    assert labels(t) == [MEAS, MEAS, MEAS, NEXT, GLOB, MEAS, GLOB, MEAS]
    assert t.flag.tolist() == [4, 4 | 1, 0, 0, 0, 4 | 8, 8, 0]
    sin = lambda a: math.sin(math.radians(a))  # noqa: E731
    d_b, d_e = fm.hole_direction(30.0, 90.0), fm.hole_direction(60.0, 120.0)
    n_e = co.core_normal(np.array([d_e, d_e]), [60.0, 60.0], [90.0, 100.0])
    n_e = fm.upper_normal(np.linalg.eigh(n_e.T @ n_e)[1][:, -1])  # the principal vector of Σ n·nᵀ
    assert t.tt[[0, 1, 2, 3, 5]] == pytest.approx(
        [2.0 * sin(50.0), 2.0 * sin(3.0), 2.5 * sin(40.0), 2.0 * sin(55.0), 2.0 * sin(30.0)], abs=TOL * 10)  # fmt: skip
    assert t.tt[4] == pytest.approx(straight(2.0, 30.0, 120.0, 30.0, 90.0), abs=TOL * 10)
    assert t.tt[7] == pytest.approx(4.0 * abs(float(n_e @ d_e)), abs=TOL * 10)
    assert np.isnan(t.tt[6])  # the global plane is known, the hole is not
    want = [fm.plane_angles(co.core_normal(d_b, 40.0, 10.0)), fm.plane_angles(co.core_normal(d_b, 55.0, 200.0)),
            fm.plane_angles(n_e)]  # fmt: skip
    assert t.alpha[[2, 3, 7]] == pytest.approx([float(a) for a, _ in want], abs=1e-9)
    assert t.dipdir[[2, 3, 7]] == pytest.approx([float(b) for _, b in want], abs=1e-9)
    assert np.isnan(t.alpha[[0, 1, 5]]).all() and t.alpha[[4, 6]].tolist() == [30.0, 30.0]
    rep = t.summary["sources"][0]
    assert (rep["source"], rep["rows"], rep["measurements"], rep["turned"], rep["local"], rep["unused"]) == (
        "core", 6, 9, 4, 3, 2)  # fmt: skip
    assert t.summary["flags"] == {"1": 1, "2": 0, "4": 3, "8": 2}
    # alone and with d_к = 4 m: the measurement 5 m away is too far; no plane — bit 2 (and 8 where no path)
    alone = tt.of_table(spec(by_core(d_core=4.0)), lines, scene(), tables={14: core_table()})
    assert labels(alone) == [MEAS, MEAS, MEAS, NONE, NONE, MEAS, NONE, MEAS]
    assert alone.flag.tolist() == [4, 5, 0, 2, 2, 12, 2 | 8, 0] and np.isnan(alone.tt[[3, 4, 6]]).all()


# --- missing values, holes without a trajectory ----------------------------------------------------------------


def test_missing_values_stay_empty() -> None:
    """Пропуски: TO ≤ FROM — the row is out of the calculation (counted, «нет», bit 2); no code — bit 2; no
    trajectory (a hole without a collar, a hole the set does not know) — bit 8. TT is empty, never 0, and every
    empty TT has a bit that says why."""
    lines = table(10, [("A", 0.0, 2.0, {"DOM": "QZ"}), ("A", 2.0, 2.0, {"DOM": "QZ"}), ("A", 5.0, 3.0, {"DOM": "QZ"}),
                       ("B", 0.0, 2.0, {"DOM": None}), ("D", 0.0, 2.0, {"DOM": "QZ"}), ("Z", 0.0, 2.0, {"DOM": "QZ"}),
                       ("Z", 2.0, 4.0, {"DOM": None})])  # fmt: skip
    lines.skipped = 3  # rows without FROM or TO never reach the kernel: the reading counts them
    t = tt.of_table(spec(by_domain("DOM", "text", [("QZ", 30.0, 120.0)])), lines, scene())
    assert labels(t) == [DOM, NONE, NONE, NONE, DOM, DOM, NONE]
    assert t.flag.tolist() == [0, 2, 2, 2, 8, 8, 2 | 8]
    assert t.tt[0] == pytest.approx(2.0 * math.cos(math.radians(30.0)), abs=TOL)
    assert np.isnan(t.tt[1:]).all() and not (t.tt == 0.0).any()
    assert bool(((t.flag & 10) != 0)[np.isnan(t.tt)].all())
    assert t.alpha == pytest.approx([30.0, NAN, NAN, NAN, 30.0, 30.0, NAN], nan_ok=True)
    assert t.theta == pytest.approx([0.0, 0.0, 0.0, 30.0, NAN, NAN, NAN], abs=1e-9, nan_ok=True)
    s = t.summary
    assert (s["rows"], s["excluded"], s["skipped"], s["with_tt"], s["capped"]) == (7, 2, 3, 1, 0)
    assert s["src"] == {DOM: 3, NONE: 4} and s["flags"] == {"1": 0, "2": 4, "4": 0, "8": 3}
    assert s["length"] == pytest.approx(10.0, abs=TOL) and s["tt"] == pytest.approx(float(t.tt[0]), abs=TOL)
    text = " ".join(t.journal)
    assert "«до» ≤ «от» — 2" in text and "без траектории — 3" in text and "без ориентировки — 2" in text
    assert "без «от» или «до» — 3" in text
    assert t.columns()[2][2].dtype == object and t.columns()[2][2].tolist() == labels(t)  # TT_SRC — a text


def test_intervals_that_are_not_rows_of_the_table() -> None:
    """Candidates of the rule 8.5.3 (15.8): any intervals grouped by hole; the domain of one is the code with
    the greatest length among the samples inside; a missing depth — empty, bit 2."""
    lines = table(10, [("A", 0.0, 2.0, {"DOM": "QZ"}), ("A", 2.0, 6.0, {"DOM": "SH"}), ("B", 0.0, 2.0, {"DOM": "QZ"})])
    s = scene()
    rows = orient.Intervals.grouped([102, 101], [1, 0], [0, 1, 4], [0.0, 0.0, 3.0, NAN], [2.0, 6.0, 4.0, 5.0])
    assert (rows.n, rows.table_id, rows.valid.tolist()) == (4, None, [True, True, True, False])
    dom = by_domain("DOM", "text", [("QZ", 30.0, 120.0), ("SH", 60.0, 300.0)])
    t = tt.compute(spec(dom), rows, orient.Inputs(s.traj, lines))
    assert labels(t) == [DOM, DOM, DOM, NONE] and t.alpha[:3].tolist() == [30.0, 60.0, 60.0]
    assert t.tt[:3] == pytest.approx([straight(2.0, 30.0, 120.0, 30.0, 90.0), 3.0, 0.5], abs=TOL * 10)
    assert np.isnan(t.tt[3]) and t.flag.tolist() == [0, 0, 0, 2]
    for bad in (([101, 101], [0, 0], [0, 1, 2], [0.0, 1.0], [1.0, 2.0]), ([101], [0], [0, 3], [0.0], [1.0])):
        with pytest.raises(ValueError):
            orient.Intervals.grouped(*bad)


def test_empty_table_and_a_very_long_interval() -> None:
    empty = tt.of_table(spec(GLOBAL), table(10, []), scene())
    assert empty.tt.shape == (0,) and empty.summary["rows"] == 0 and empty.summary["src"] == {}
    assert empty.summary["length"] == 0.0 and empty.journal == []
    # more than 100 000 parts: the step of that interval grows, the journal says so (ADR 0021 §12)
    long = tt.of_table(spec(GLOBAL, step=0.1), table(10, [("A", 0.0, 20_000.0)]), scene())
    assert long.tt[0] == pytest.approx(20_000.0 * math.cos(math.radians(30.0)), rel=1e-12)
    assert long.summary["capped"] == 1 and any(str(dr.MAX_PARTS)[:3] in line for line in long.journal)


# --- threads, progress, parameters ---------------------------------------------------------------------------


def big() -> tuple[sp.TtSpec, source.Lines, SimpleNamespace, dict[int, source.Lines]]:
    """3000 curved holes (every 50th without a collar), 201 000 intervals (four strips) with a numeric domain
    (gaps, a code that is not listed) and 20 000 core measurements (gaps, angles out of range)."""
    rng = np.random.Generator(np.random.PCG64(15))
    nh, per = 3000, 67
    x = rng.uniform(0.0, 5000.0, nh) + UTM[0]
    x[::50] = NAN
    st_hole = np.repeat(np.arange(nh), 4)
    st_depth = np.tile([0.0, 60.0, 120.0, 200.0], nh)
    traj = desurvey(Collars(np.arange(nh), x, rng.uniform(0.0, 5000.0, nh) + UTM[1], np.full(nh, 300.0),
                            np.full(nh, 210.0)),
                    Surveys(st_hole, st_depth, rng.uniform(0.0, 360.0, 4 * nh), rng.uniform(-90.0, -20.0, 4 * nh)),
                    n_holes=nh)  # fmt: skip
    ids = np.arange(nh, dtype=np.int64) + 1000
    st = SimpleNamespace(hole_ids=ids, index={}, traj=traj)
    names = [f"H{i:05d}" for i in range(nh)]
    step = rng.uniform(0.5, 5.5, (nh, per))
    to = np.cumsum(step, axis=1).ravel()
    frm = to - step.ravel()
    to[::1000] = frm[::1000]  # rows out of the calculation
    zone = rng.integers(0, 6, nh * per).astype(float)
    zone[zone == 5.0] = NAN
    starts = np.arange(nh + 1, dtype=np.int64) * per
    lines = source.Lines(set_id=1, table_id=10, name="Пробы", name_key="пробы", token="t", rev=1, kind="assay",
                         fields={}, row_ids=np.arange(nh * per, dtype=np.int64), holes=names, hole_db_ids=ids,
                         starts=starts, from_m=frm, to_m=to, numbers={"ZONE": zone})  # fmt: skip
    m = 20_000
    m_hole = np.sort(rng.integers(0, nh, m))
    m_depth = rng.uniform(0.0, 220.0, m)
    order = np.lexsort((m_depth, m_hole))
    m_hole, m_depth = m_hole[order], m_depth[order]
    alpha_c = rng.uniform(-5.0, 95.0, m)
    alpha_c[::17] = NAN
    present = np.unique(m_hole)
    core = source.Lines(set_id=1, table_id=14, name="Керн", name_key="керн", token="t", rev=1, kind="event",
                        fields={}, row_ids=np.arange(m, dtype=np.int64), holes=[names[i] for i in present],
                        hole_db_ids=ids[present], starts=np.searchsorted(m_hole, np.append(present, nh)),
                        from_m=m_depth, to_m=m_depth.copy(),
                        numbers={"ALPHA_K": alpha_c, "BETA_K": rng.uniform(0.0, 360.0, m)})  # fmt: skip
    dom = by_domain("ZONE", "number", [("0", 30.0, 120.0), ("1", 60.0, 300.0), ("2", 5.0, 10.0), ("3", 90.0, 45.0)])
    return spec(dom, by_core(d_core=3.0)), lines, st, {14: core}


def test_one_hash_with_any_number_of_threads() -> None:
    """ТЗ 8.0.2: the columns are the same bit for bit with 1, 2, 4 and 8 threads, and so is the summary."""
    sp_, lines, st, tables = big()
    runs = [tt.of_table(sp_, lines, st, tables=tables, threads=k) for k in (1, 2, 4, 8)]
    assert len({digest(r) for r in runs}) == 1
    assert all(r.summary == runs[0].summary and r.journal == runs[0].journal for r in runs)
    t = runs[0]
    n = lines.rows
    assert n == 201_000 and n > 3 * dr.STRIP_ROWS
    assert set(labels(t)) == {DOM, MEAS, NEXT, NONE}
    s = t.summary
    assert sum(s["src"].values()) == n and s["excluded"] == 201 and s["with_tt"] == int(np.isfinite(t.tt).sum())
    assert s["flags"] == {str(b): int(np.count_nonzero(t.flag & b)) for b in (1, 2, 4, 8)} and min(s["flags"].values())
    assert s["tt"] == pytest.approx(math.fsum(t.tt[np.isfinite(t.tt)].tolist()), rel=1e-13)
    valid = lines.to_m > lines.from_m
    assert s["length"] == pytest.approx(math.fsum((lines.to_m - lines.from_m)[valid].tolist()), rel=1e-13)
    # every empty TT is explained by a bit; a TT never exceeds the length; no orientation — «нет» and bit 2
    assert bool(((t.flag & 10) != 0)[np.isnan(t.tt)].all())
    has = np.isfinite(t.tt)
    assert bool((t.tt[has] <= (lines.to_m - lines.from_m)[has] * (1.0 + 1e-12)).all()) and not (has & ~valid).any()
    assert bool((((t.flag & 2) != 0) == (t.src == orient.SRC_NONE)).all())
    # the domain rows against the plain formula, one direction a part
    rows = np.flatnonzero((t.src == orient.SRC_DOMAIN) & np.isfinite(t.tt))[::97]
    att = {0.0: (30.0, 120.0), 1.0: (60.0, 300.0), 2.0: (5.0, 10.0), 3.0: (90.0, 45.0)}
    dense = np.repeat(np.arange(3000), 67)
    for r in rows.tolist():
        normal = fm.plane_normal(*att[float(lines.numbers["ZONE"][r])])
        k = max(1, math.ceil((lines.to_m[r] - lines.from_m[r]) / 1.0))
        part = (lines.to_m[r] - lines.from_m[r]) / k
        mids = lines.from_m[r] + (np.arange(k) + 0.5) * part
        d = dr.directions(st.traj, np.full(k, dense[r]), mids)
        assert t.tt[r] == pytest.approx(part * float(np.abs(d @ normal).sum()), abs=1e-12 * k * part)


def test_order_of_the_holes_does_not_matter() -> None:
    """The same holes named so that the table lists them the other way round: every interval gets the same bits
    (nothing is carried from row to row, holes are matched by their ids)."""
    sp_, lines, st, tables = big()
    nh, per = len(lines.holes), 67
    back = lambda a: a.reshape(nh, per)[::-1].ravel().copy()  # noqa: E731
    turned = source.Lines(set_id=1, table_id=10, name="Пробы", name_key="пробы", token="t", rev=1, kind="assay",
                          fields={}, row_ids=back(lines.row_ids), holes=lines.holes,
                          hole_db_ids=lines.hole_db_ids[::-1].copy(), starts=lines.starts, from_m=back(lines.from_m),
                          to_m=back(lines.to_m), numbers={"ZONE": back(lines.numbers["ZONE"])})  # fmt: skip
    a = tt.of_table(sp_, lines, st, tables=tables, threads=2)
    b = tt.of_table(sp_, turned, st, tables=tables, threads=4)
    for name in ("tt", "flag", "src", "alpha", "dipdir", "theta", "azimuth"):
        assert back(getattr(b, name)).tobytes() == getattr(a, name).tobytes(), name
    assert {**b.summary, "tt": 0.0, "length": 0.0} == {**a.summary, "tt": 0.0, "length": 0.0}
    assert b.summary["tt"] == pytest.approx(a.summary["tt"], rel=1e-14)


def test_core_fields_of_the_table_itself() -> None:
    """The measurements may be fields of the interval table itself: the depth of one is the middle of its row."""
    lines = table(10, [("B", 0.0, 2.0, {"ALPHA_K": 40.0, "BETA_K": 10.0}),
                       ("B", 2.0, 4.0, {"ALPHA_K": NAN, "BETA_K": NAN}),
                       ("B", 30.0, 32.0, {"ALPHA_K": NAN, "BETA_K": 5.0})])  # fmt: skip
    t = tt.of_table(spec(by_core(table_id=10)), lines, scene())
    assert labels(t) == [MEAS, NEXT, NONE] and t.flag.tolist() == [0, 0, 2]
    sin40 = math.sin(math.radians(40.0))
    assert t.tt[:2] == pytest.approx([2.0 * sin40, 2.0 * sin40], abs=TOL * 10) and np.isnan(t.tt[2])
    assert t.alpha[0] == t.alpha[1] and t.dipdir[0] == t.dipdir[1]


def test_on_a_project_through_the_real_reading(pclient) -> None:  # type: ignore[no-untyped-def]
    """`spec.parse_tt`, `source.read` and the trajectories of the demo project: domains by closed bodies are read
    through `jobs_coding.reading`; a body the table does not list and the holes outside take the global plane."""
    assert pclient.post("/api/v1/projects/demo").status_code == 200
    project = projects_of(pclient.core).require_current()
    with dialect.read_tx(project.engine) as conn:
        t = conn.execute(sa.select(dh_tables.c.id, dh_tables.c.set_id).where(dh_tables.c.name == "Пробы")).one()
        found = conn.execute(sa.select(objects.c.name, objects.c.id).where(objects.c.kind == "wireframe"))
        wf = {r.name: int(r.id) for r in found}
        dom = {"source": "domain", "wireframe_ids": [wf["Рудное тело 1"], wf["Рудное тело 2"]],
               "attitudes": [{"code": "Рудное тело 1", "alpha": 40, "dipdir": 100}]}  # fmt: skip
        chain = [dom, {"source": "global", "alpha": 30, "dipdir": 360}]
        parsed = sp.parse_tt(conn, {"set_id": int(t.set_id), "table": str(t.id), "orientation": chain})
    lines = source.read(project.engine, parsed.set_id, parsed.table_id, parsed.tables()[0][1])
    with jobs_coding.reading(project):
        res = tt.of_table(parsed, lines, source.trajectories(project, parsed.set_id), threads=2)
    s = res.summary
    assert s["rows"] == lines.rows == s["with_tt"] > 4000 and s["flags"]["2"] == s["flags"]["8"] == 0
    assert s["src"][DOM] > 0 and s["src"][GLOB] > 0 and s["sources"][0]["not_listed"] > 0
    by_dom = res.src == orient.SRC_DOMAIN
    assert set(res.alpha[by_dom].tolist()) == {40.0} and set(res.dipdir[~by_dom].tolist()) == {0.0}
    assert any("«Рудное тело 2»" in line for line in res.journal)
    assert bool((res.tt <= (lines.to_m - lines.from_m) * (1.0 + 1e-12)).all())


def test_progress_and_cancel() -> None:
    sp_, lines, st, tables = big()
    seen: list[float] = []
    tt.of_table(sp_, lines, st, tables=tables, threads=2, tick=seen.append)
    assert seen == sorted(seen) and 0.0 < seen[0] < 1.0 and seen[-1] == 1.0 and len(seen) > 8

    def cancel(done: float) -> None:
        if done > 0.5:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        tt.of_table(sp_, lines, st, tables=tables, threads=2, tick=cancel)


def test_parameters_and_wrong_answers_of_a_source() -> None:
    lines = chain_table()
    for bad in ({"eps": 0.6}, {"eps": 0.001}, {"step": 0.05}, {"step": 11.0}):
        with pytest.raises(CompositeError) as err:
            tt.of_table(spec(GLOBAL, **bad), lines, scene())
        assert err.value.code == "bad_param" and err.value.param == next(iter(bad))
    with pytest.raises(CompositeError) as err:
        tt.of_table(spec(by_core(d_core=1001.0)), lines, scene(), tables={14: core_table()})
    assert err.value.param == "d_core"
    mesh = sp.Source("mesh", meshes=[], d_max=50.0, r_n=0.0)
    wrong = lambda rows, traj, s, *, todo, threads=1, tick=None: orient.Given(np.zeros((2, 3)), 3)  # noqa: E731
    with pytest.raises(ValueError, match="mesh"):
        tt.of_table(spec(mesh), lines, scene(), mesh=wrong)
    long = lambda rows, traj, s, *, todo, threads=1, tick=None: orient.Given([0.0, 0.0, 2.0], 3)  # noqa: E731
    with pytest.raises(ValueError, match="unit"):
        tt.of_table(spec(mesh), lines, scene(), mesh=long)  # a wrong TT is never written silently
    code = lambda rows, traj, s, *, todo, threads=1, tick=None: orient.Given([0.0, 0.0, 1.0], 7)  # noqa: E731
    with pytest.raises(ValueError, match="mesh"):
        tt.of_table(spec(mesh), lines, scene(), mesh=code)  # not a code of TT_SRC
    with pytest.raises(ValueError, match="14"):
        tt.of_table(spec(by_core()), lines, scene())  # the table of the core was not read
    with pytest.raises(ValueError, match="ROCK"):
        tt.of_table(spec(by_domain("ROCK", "text", [("QZ", 30.0, 120.0)])), lines, scene())  # nor that field


def test_ranges_live_in_one_place() -> None:
    """The ranges of ТЗ 8.5.4 the kernels check are those of the parameters (`spec.RANGES`): one place — the
    package, without numpy (`spec` is loaded before BF-READY)."""
    assert fm.EPS_RANGE is pkg.EPS_RANGE and fm.STEP_RANGE is pkg.STEP_RANGE
    assert co.D_CORE_RANGE is pkg.D_CORE_RANGE and co.LINES is pkg.LINES is sp.LINES
    assert sp.RANGES["eps"][:3] == (*fm.EPS_RANGE, fm.EPS_DEFAULT)
    assert sp.RANGES["step"][:3] == (*fm.STEP_RANGE, fm.STEP_DEFAULT)
    assert sp.RANGES["d_core"][:3] == (*co.D_CORE_RANGE, co.D_CORE_DEFAULT)
    assert orient.SRC_LABELS == ("нет", "глобальная", "домен", "каркас: пересечение", "каркас: ближайшая",
                                 "керн: замер", "керн: ближайший")  # fmt: skip
    assert tt.ALGO == "tt-1"
    tt.warm()
