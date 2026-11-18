"""The benchmarks of wireframes (ADR 0013 «Замеры», подшаг 8.19) on small volumes: the generator of their data
against analytic references, and every scenario from `prepare` to the numbers it reports.

References and tolerances:
  * the surface of the generator — `Surface.height` is the height of its triangles: exact at the vertices, and
    at any point the height of the plane of the triangle over it within 1e-8 m (a northing of 6 million is
    known to 1e-9 m, and the faces of a pit are steeper than 45°);
  * an ore body — a closed solid with its normals outward, its volume that of the ellipsoid within 2e-3
    (the chords of a UV sphere of 20 000 triangles; the tolerance of `test_gen_and_volume`);
  * the scenarios — the errors they report are within the limits of `bench/budgets.json`: a file written and
    read back — 0 (a binary STL — half the step of float32); a section and a pick — 1e-8 m; a crossing of a
    hole — 1e-7 m from the plane of its triangle and the 1 mm of 7.15 from the surface; a flat place at a simplified level — 0.25 mm (float32 from the
    origin of its part).
"""

from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.gen import wireframes as gen  # noqa: E402

from bf_core.geo.meshes import mesh_stats  # noqa: E402

BUDGETS = json.loads((ROOT / "bench" / "budgets.json").read_text(encoding="utf-8"))


def run(name: str, params: dict[str, Any], work: Path) -> dict[str, Any]:
    """A scenario as `bench.run` runs it, in this process."""
    mod = importlib.import_module(f"bench.scenarios.{name}")
    mod.prepare(params, work)
    mod.load()
    info: dict[str, Any] = mod.run(params, work)
    json.dumps(info)  # what `bench.run` prints
    return info


def within_limits(name: str, info: dict[str, Any], keys: tuple[str, ...]) -> None:
    """The accuracy limits of both profiles hold on the small volume too (the times are not looked at)."""
    for profile in ("ci", "full"):
        spec = BUDGETS[profile][name]
        for key in keys:
            assert info[key] <= spec["limits"][key], (profile, key, info[key])


# --- the generator -----------------------------------------------------------------------------------------


def test_surface_height_is_the_height_of_its_triangles() -> None:
    s = gen.pit_surface(20_000)
    v, t = s.mesh()
    assert len(t) == s.triangles == 2 * s.nx * s.ny and abs(len(t) - 20_000) <= 0.02 * 20_000
    assert v.dtype == np.float64 and t.dtype == np.int32 and v[:, 0].min() > 5e5 and v[:, 1].min() > 6e6
    assert np.array_equal(s.height(v[:, 0], v[:, 1]), v[:, 2])  # at the vertices — bit for bit
    # anywhere — on the plane of the triangle over the point
    rng = np.random.default_rng(3)
    k = rng.integers(0, len(t), 5000)
    w = rng.dirichlet([1.0, 1.0, 1.0], len(k))
    p = np.einsum("ij,ijk->ik", w, v[t[k]])
    assert np.abs(s.height(p[:, 0], p[:, 1]) - p[:, 2]).max() <= 1e-8
    assert np.isnan(s.height(v[:, 0].min() - 1.0, v[:, 1].min())).all()  # outside: no surface
    # a pit with level berms and plane faces: a good share of the triangles lie in the plane of a neighbour
    stats = mesh_stats(v, t)
    assert not stats["closed"] and stats["degenerate"] == 0 and stats["nonmanifold_edges"] == 0
    a, b, c = (v[t[:, i]] for i in range(3))
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1)[:, None]
    assert (n[:, 2] > 0).all()  # normals up
    pair = np.abs(np.einsum("ij,ij->i", n[0::2], n[1::2]))  # the two triangles of a cell
    assert 0.15 < float((pair > 1.0 - 1e-12).mean()) < 0.75  # flat cells and rough relief, both in numbers


def test_ore_body_is_a_closed_solid_with_the_volume_of_its_ellipsoid() -> None:
    radii = (120.0, 60.0, 35.0)
    v, t = gen.ore_body(20_000, centre=(513_000.0, 6_235_000.0, 100.0), radii=radii, azimuth=40.0, plunge=25.0)
    stats = mesh_stats(v, t)
    exact = 4.0 / 3.0 * math.pi * radii[0] * radii[1] * radii[2]
    assert stats["closed"] and stats["orientation"] == "outward" and stats["degenerate"] == 0
    assert abs(stats["volume"] - exact) / exact <= 2e-3
    assert t.dtype == np.int32 and abs(len(t) - 20_000) <= 0.02 * 20_000


def test_fault_crosses_the_surface_and_the_scene_is_many_wireframes() -> None:
    s = gen.pit_surface(8_000)
    v, t = gen.fault(s, 800)
    above = v[:, 2] - s.height(v[:, 0], v[:, 1])
    assert not np.isnan(above).any() and above.min() < -10.0 and above.max() > 10.0  # through the surface
    assert mesh_stats(v, t)["degenerate"] == 0 and abs(len(t) - 800) <= 0.2 * 800
    meshes = gen.scene(30_000)
    names = [m["name"] for m in meshes]
    total = sum(len(m["tri"]) for m in meshes)
    assert len(names) == len(set(names)) >= 40 and abs(total - 30_000) <= 0.1 * 30_000
    assert max(len(m["tri"]) for m in meshes) > 0.5 * total  # one large wireframe and many small ones
    assert sum(1 for m in meshes if mesh_stats(m["xyz"], m["tri"])["closed"]) >= 3
    assert all(m["xyz"].dtype == np.float64 and m["tri"].dtype == np.int32 for m in meshes)
    assert any(m["attrs"] for m in meshes) and all(m["color"] for m in meshes)


# --- the scenarios -----------------------------------------------------------------------------------------


@pytest.mark.windows
def test_wireframe_import_reads_stores_and_writes_a_tridb_back_bit_for_bit(tmp_path: Path) -> None:
    info = run("wireframe_import", {"triangles": 30_000}, tmp_path)
    assert info["wireframes"] == 45 and abs(info["triangles"] - 30_000) <= 0.1 * 30_000
    assert info["blob_mismatches"] == 0 and info["row_mismatches"] == 0  # the file comes back as it was
    assert info["measure_mismatches"] == 0  # the volume and the area of the file against the geometry, 1e-9
    assert all(info[k] >= 0.0 for k in ("read_s", "store_s", "import_s", "export_s"))
    within_limits("wireframe_import", info, ("blob_mismatches", "row_mismatches", "measure_mismatches"))


@pytest.mark.windows
def test_wireframe_check_builds_levels_finds_crossings_and_serves_the_first_frame(tmp_path: Path) -> None:
    info = run("wireframe_check", {"triangles": 60_000}, tmp_path)
    assert info["pairs"] > 20 and info["normal_conflicts"] == 0
    assert info["crossing"] >= info["straddling"] > 20  # every triangle of the fault through the surface is found
    lod = info["lod_triangles"]
    assert lod[0] == info["triangles"] and lod[1] <= lod[0] / 6 and lod[2] <= lod[0] / 40  # the shares of 8.12
    assert info["flat_vertices"] > 0 and info["flat_error_m"] <= 2.5e-4  # a level berm stays level: float32
    within_limits("wireframe_check", info, ("flat_error_m", "normal_conflicts"))


@pytest.mark.windows
def test_wireframe_exact_sections_and_picks_against_the_surface(tmp_path: Path) -> None:
    info = run("wireframe_exact", {"triangles": 30_000}, tmp_path)
    assert info["section_points"] > 100 and info["section_lines"] >= 21 and info["closed_lines"] >= 1
    assert info["picks"] == 250 and info["hits"] >= 200
    assert info["section_error_m"] <= 1e-8 and info["pick_error_m"] <= 1e-8
    within_limits("wireframe_exact", info, ("section_error_m", "pick_error_m"))


@pytest.mark.windows
def test_hole_intersections_against_the_surface_and_the_solid(tmp_path: Path) -> None:
    info = run("hole_intersections", {"triangles": 30_000, "holes": 400}, tmp_path)
    assert info["holes"] == 400 and info["crossings"] >= info["holes_through_surface"] > 300
    # every fifth collar is a vertex of the surface: a crossing at depth 0 unless the hole starts into the air
    assert 60 <= info["at_collar"] <= 80 and info["collar_errors"] == 0
    assert info["parity_errors"] == 0  # a hole from above to below the surface crosses it an odd number of times
    assert info["enters"] > 0 and info["enters"] - info["leaves"] == info["ends_inside"]
    assert info["plane_error_m"] <= 1e-7 and info["max_error_m"] <= 1e-3
    within_limits("hole_intersections", info, ("plane_error_m", "max_error_m", "parity_errors", "collar_errors"))


@pytest.mark.windows
def test_mesh_formats_write_and_read_every_format(tmp_path: Path) -> None:
    info = run("mesh_formats", {"triangles": 20_000}, tmp_path)
    for fmt in ("dxf", "obj", "stl", "stl_text", "dtm", "datamine"):
        assert info[f"{fmt}_read_s"] >= 0.0 and info[f"{fmt}_mb"] > 0.0, fmt
        assert info[f"{fmt}_triangles"] == info["triangles"], fmt
        assert info[f"{fmt}_wireframes"] == (1 if fmt == "stl" else 2), fmt  # a binary STL is one solid
    assert info["max_error_m"] == 0.0  # float64 text and Datamine tables: bit for bit
    assert 0.0 < info["stl_error_m"] <= 0.25  # float32 on a northing of 6 million: half its step of 0.5 m
    within_limits("mesh_formats", info, ("max_error_m", "stl_error_m"))
