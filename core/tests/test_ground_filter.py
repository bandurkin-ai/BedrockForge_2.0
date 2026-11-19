"""Э7.1, 7.1.7–7.1.9: the ground filter on synthetic clouds with true labels (ADR 0014 «Тесты и приёмка»,
ТЗ 7.35 Д-22): Т-З1 the plain, Т-З2 the funnel, Т-З3 the benches, Т-З4 threads and repeats, «Блоки»."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen import cloud  # noqa: E402

from bf_core.geo import ground  # noqa: E402
from bf_core.geo.dtm import build_dtm  # noqa: E402
from bf_core.jit import njit  # noqa: E402

PLAIN = ground.PROFILES["plain"]
QUARRY = ground.PROFILES["quarry"]


@njit(cache=True)
def _raster(vx, vy, vz, tri, x0, y0, step, nx, ny):  # type: ignore[no-untyped-def]
    """Heights of a TIN at the nodes (x0 + i·step, y0 + j·step); NaN outside it."""
    out = np.full((ny, nx), np.nan)
    for t in range(len(tri)):
        a, b, c = tri[t, 0], tri[t, 1], tri[t, 2]
        ax, ay, bx, by, cx, cy = vx[a], vy[a], vx[b], vy[b], vx[c], vy[c]
        det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if det == 0.0:
            continue
        i0 = max(int(np.ceil((min(ax, bx, cx) - x0) / step)), 0)
        i1 = min(int(np.floor((max(ax, bx, cx) - x0) / step)), nx - 1)
        j0 = max(int(np.ceil((min(ay, by, cy) - y0) / step)), 0)
        j1 = min(int(np.floor((max(ay, by, cy) - y0) / step)), ny - 1)
        for j in range(j0, j1 + 1):
            y = y0 + j * step
            for i in range(i0, i1 + 1):
                x = x0 + i * step
                wb = ((x - ax) * (cy - ay) - (cx - ax) * (y - ay)) / det
                wc = ((bx - ax) * (y - ay) - (x - ax) * (by - ay)) / det
                if wb >= -1e-12 and wc >= -1e-12 and wb + wc <= 1.0 + 1e-12:
                    out[j, i] = vz[a] + wb * (vz[b] - vz[a]) + wc * (vz[c] - vz[a])
    return out


def dtm_deviation(scene: cloud.Scene, cls: np.ndarray, inside=None) -> np.ndarray:  # type: ignore[no-untyped-def]
    """«Построить ЦТМ» from the points of class 2 → its deviation from the true surface along the normal at
    the nodes of a 1 m grid over the site (those `inside(x, y)` picks)."""
    v, t, _report = build_dtm(scene.xyz[cls == 2])
    x0, y0, x1, y1 = scene.extent
    nx, ny = int(x1 - x0), int(y1 - y0)
    z = _raster(v[:, 0], v[:, 1], v[:, 2], t.astype(np.int64), x0 + 0.5, y0 + 0.5, 1.0, nx, ny)
    gy, gx = np.mgrid[0:ny, 0:nx]
    gx, gy = x0 + 0.5 + gx, y0 + 0.5 + gy
    keep = np.isfinite(z)
    if inside is not None:
        keep &= inside(gx, gy)
    return ((z - scene.ground_z(gx, gy)) * scene.ground_cos(gx, gy))[keep]


def scores(scene: cloud.Scene, cls: np.ndarray) -> dict[str, float]:
    truth = scene.truth == cloud.GROUND
    got = cls == 2
    out = {"recall": float((got & truth).sum() / truth.sum()), "false": float((got & ~truth).sum() / max(got.sum(), 1))}
    for name, tags in (
        ("wall", (cloud.T_WALL,)),
        ("slope", (cloud.T_SLOPE, cloud.T_UNDER)),
        ("flat", (cloud.T_BERM, cloud.T_TOP, cloud.T_FLOOR, cloud.T_FLAT, cloud.T_RIM)),
    ):
        m = np.isin(scene.tag, tags)
        if m.any():
            out[name] = float(got[m].mean())
    return out


# --- Т-З1 ------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plain():  # type: ignore[no-untyped-def]
    scene = cloud.plain(101)
    return scene, ground.classify_ground(scene.xyz, None, PLAIN)


def test_tz1_plain_with_buildings_trees_and_outliers(plain) -> None:  # type: ignore[no-untyped-def]
    scene, res = plain
    s = scores(scene, res.cls)
    assert s["recall"] >= 0.99, s  # ТЗ: верно земля ≥ 99 %
    assert s["false"] <= 0.01, s  # ложная земля ≤ 1 %
    assert (res.cls[scene.tag == cloud.T_OUTLIER] == 7).all()  # все выбросы — класс 7
    assert not (res.cls[scene.truth == cloud.GROUND] == 7).any()
    dev = dtm_deviation(scene, res.cls)
    assert len(dev) > 39_000 and float(np.sqrt((dev**2).mean())) <= 0.1  # СКО высоты ЦТМ ≤ 0,1 м
    # roofs and crowns are high vegetation by their height (class 6 is not set), never ground
    assert (res.cls[scene.tag == cloud.T_ROOF] == 5).all()
    assert (res.cls[scene.tag == cloud.T_CROWN] >= 4).all()
    roof_h = res.h[scene.tag == cloud.T_ROOF]
    assert abs(float(np.median(roof_h)) - 5.0) < 0.2  # the height above the ground under a roof: by the cloth
    ground_h = res.h[(scene.truth == cloud.GROUND) & (res.cls == 2)]
    assert res.h.dtype == np.float32 and res.cls.dtype == np.uint8
    assert float(np.abs(ground_h).max()) <= 0.5 and abs(float(ground_h.mean())) < 0.01
    r = res.report
    assert r["classes"][7] == 50 == r["noise"] and r["blocks"] == 1 and r["face_points"] == 0
    assert sum(r["classes"].values()) == len(scene) and r["skipped"] == 0


def test_returns_keep_the_first_return_of_a_crown_out_of_the_minima(plain) -> None:  # type: ignore[no-untyped-def]
    scene, res = plain
    with_ret = ground.classify_ground(scene.xyz, scene.ret, PLAIN)
    s = scores(scene, with_ret.cls)
    assert s["recall"] >= 0.99 and s["false"] <= 0.01
    assert float((with_ret.cls != res.cls).mean()) < 0.002


# --- Т-З2 ------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def funnel():  # type: ignore[no-untyped-def]
    scene = cloud.funnel(202)
    return scene, ground.classify_ground(scene.xyz, None, QUARRY)


def test_tz2_funnel_300_m_deep_with_vegetation_on_the_wall(funnel) -> None:  # type: ignore[no-untyped-def]
    scene, res = funnel
    s = scores(scene, res.cls)
    assert s["slope"] >= 0.98, s  # ТЗ: полнота земли на борту ≥ 98 %
    bare = res.cls[scene.tag == cloud.T_SLOPE] == 2
    under = res.cls[scene.tag == cloud.T_UNDER] == 2
    assert bare.mean() >= 0.995 and under.mean() >= 0.98  # and under the vegetation as well
    assert (res.cls[np.isin(scene.tag, (cloud.T_RIM, cloud.T_FLOOR))] == 2).mean() >= 0.99  # the rim: by the growth
    x0, y0, x1, y1 = scene.extent
    centre, radius = ((x0 + x1) / 2, (y0 + y1) / 2), (x1 - x0) / 2
    dev = dtm_deviation(scene, res.cls, lambda x, y: np.hypot(x - centre[0], y - centre[1]) < radius - 2.0)
    rms = float(np.sqrt((dev**2).mean()))
    assert len(dev) > 250_000 and rms <= 0.15, rms  # СКО отклонения ЦТМ по нормали ≤ 0,15 м
    # vegetation: what is left as ground is what stands within CT of the wall along the normal, and a little more
    veg = scene.tag == cloud.T_BUSH
    over = (scene.xyz[veg, 2] - scene.ground_z(scene.xyz[veg, 0], scene.xyz[veg, 1])) * np.cos(np.radians(50.0))
    taken = res.cls[veg] == 2
    assert taken[over <= 0.45].mean() > 0.9 and taken[over > 0.8].mean() < 0.05
    assert s["false"] <= 0.03, s


def test_tz2_depth_does_not_add_cloth_sweeps(funnel) -> None:  # type: ignore[no-untyped-def]
    _scene, deep = funnel
    shallow_scene = cloud.funnel(203, depth=30.0)
    shallow = ground.classify_ground(shallow_scene.xyz, None, QUARRY)
    s = scores(shallow_scene, shallow.cls)
    assert s["slope"] >= 0.98 and s["recall"] >= 0.99, s
    fine_deep, fine_shallow = deep.report["sweeps"][0][-1], shallow.report["sweeps"][0][-1]
    assert fine_deep <= 640 and fine_shallow <= fine_deep  # ten times the depth — not ten times the sweeps
    assert sum(deep.report["sweeps"][0]) <= 5 * sum(shallow.report["sweeps"][0])


# --- Т-З3 ------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def benches():  # type: ignore[no-untyped-def]
    scene = cloud.benches(303)
    return scene, ground.classify_ground(scene.xyz, None, QUARRY)


def test_tz3_benches_with_vertical_walls(benches) -> None:  # type: ignore[no-untyped-def]
    scene, res = benches
    got = res.cls == 2
    assert got[scene.tag == cloud.T_WALL].mean() >= 0.95  # ТЗ: точки стенки ≥ 95 % «земля»
    for tag in (cloud.T_BERM, cloud.T_TOP, cloud.T_FLOOR, cloud.T_UNDER):
        assert got[scene.tag == tag].mean() >= 0.98, tag  # бермы ≥ 98 %
    # the control against «everything is ground»: a facade is not a wall of a bench — it has no ground above
    facade = scene.tag == cloud.T_FACADE
    height = scene.xyz[:, 2] - scene.origin[2]
    assert got[facade & (height > QUARRY.ct)].mean() <= 0.05
    assert got[facade].mean() <= 0.05 + QUARRY.ct / 6.0  # with the foot of the facade, which is within CT
    assert not got[scene.tag == cloud.T_ROOF].any()
    bush = scene.tag == cloud.T_BUSH
    over = scene.xyz[:, 2] - scene.ground_z(scene.xyz[:, 0], scene.xyz[:, 1])
    assert got[bush & (over > QUARRY.ct + 0.1)].mean() < 0.02
    assert res.report["edge_areas"] == 1  # the top platform: the cloth passes 20 m under it
    assert res.report["face_points"] > 0.8 * (scene.tag == cloud.T_WALL).sum()
    assert float(np.nanmax(res.h[(scene.tag == cloud.T_WALL) & got])) <= QUARRY.ct


def test_tz3_without_faces_the_walls_are_not_ground(benches) -> None:  # type: ignore[no-untyped-def]
    scene, _res = benches
    res = ground.classify_ground(scene.xyz, None, ground.make_params("quarry", r_b=False))
    got = res.cls == 2
    assert got[scene.tag == cloud.T_WALL].mean() < 0.2  # only the points at the crest and at the foot
    assert got[scene.tag == cloud.T_BERM].mean() >= 0.98
    assert res.report["face_points"] == 0


# --- parameters and hard input ---------------------------------------------------------------------------


def test_profiles_and_ranges() -> None:
    assert ground.PROFILES["quarry"] == ground.GroundParams() == ground.make_params()
    p = ground.make_params("plain")
    assert (p.cr, p.ri, p.ct, p.r_b, p.noise_r, p.noise_k) == (1.0, 3, 0.5, None, 2.0, 3)
    q = ground.make_params("quarry", cr=2, ri=1, ct=0.3, r_b=8, noise={"r": 3, "k_min": 5})
    assert (q.cr, q.ri, q.ct, q.r_b, q.noise_r, q.noise_k) == (2, 1, 0.3, 8, 3.0, 5)
    assert ground.make_params("quarry", noise=False).noise_r is None
    assert ground.make_params("quarry", cr=None, noise=None) == ground.PROFILES["quarry"]
    for bad in (
        {"cr": 0.05},
        {"cr": 51},
        {"ri": 4},
        {"ct": 0.01},
        {"ct": 6},
        {"r_b": 0.5},
        {"r_b": 21},
        {"noise": {"r": 0.1}},
        {"noise": {"k_min": 0}},
        {"cr": float("nan")},
        {"speed": 1},
    ):
        with pytest.raises(ground.GroundError) as err:
            ground.make_params("quarry", **bad)
        assert err.value.code == "bad_param" and err.value.message and err.value.hint
    with pytest.raises(ground.GroundError):
        ground.make_params("горы")


def test_empty_tiny_and_broken_clouds() -> None:
    res = ground.classify_ground(np.zeros((0, 3)))
    assert res.cls.shape == (0,) and res.h.shape == (0,) and res.report["blocks"] == 0
    one = ground.classify_ground(np.array([[432_000.0, 6_180_000.0, 250.0]]))
    assert one.cls.tolist() == [7]  # alone: no neighbour within 2 m
    alone = ground.classify_ground(np.array([[432_000.0, 6_180_000.0, 250.0]]), None, ground.make_params(noise=False))
    assert alone.cls.tolist() == [2] and alone.h.tolist() == [0.0]
    same = ground.classify_ground(np.tile([[432_000.0, 6_180_000.0, 250.0]], (50, 1)))
    assert (same.cls == 2).all() and (same.h == 0.0).all()
    rng = np.random.default_rng(7)
    xyz = np.column_stack([rng.random((5000, 2)) * 30.0, rng.normal(0, 0.02, 5000)]) + cloud.ORIGIN
    xyz[[3, 77, 4000]] = [[np.nan, 6_180_001.0, 250.0], [432_001.0, np.inf, 250.0], [432_001.0, 6_180_001.0, np.nan]]
    res = ground.classify_ground(xyz, None, PLAIN)
    assert res.report["skipped"] == 3 and res.cls[[3, 77, 4000]].tolist() == [1, 1, 1]
    assert np.isnan(res.h[[3, 77, 4000]]).all()  # a gap is not a zero
    ok = np.ones(5000, bool)
    ok[[3, 77, 4000]] = False
    assert (res.cls[ok] == 2).mean() > 0.999
    with pytest.raises(ground.GroundError) as err:
        ground.classify_ground(np.zeros((10, 2)))
    assert err.value.code == "bad_input"
    with pytest.raises(ground.GroundError):
        ground.classify_ground(xyz, np.zeros(10, np.uint8))


def test_views_memory_maps_and_other_types_are_taken_as_they_are(tmp_path) -> None:  # type: ignore[no-untyped-def]
    scene = cloud.plain(5, side=60.0, buildings=1, trees=10, outliers=5)
    want = ground.classify_ground(scene.xyz, None, PLAIN)
    path = tmp_path / "xyz.bin"
    scene.xyz.tofile(path)
    mapped = np.memmap(path, dtype="<f8", mode="r", shape=scene.xyz.shape)
    got = ground.classify_ground(mapped, None, PLAIN)
    assert got.cls.tobytes() == want.cls.tobytes() and got.h.tobytes() == want.h.tobytes()
    del mapped
    wide = np.zeros((len(scene), 5))
    wide[:, 1:4] = scene.xyz  # a view that is not contiguous
    got = ground.classify_ground(wide[:, 1:4], None, PLAIN)
    assert got.cls.tobytes() == want.cls.tobytes() and got.h.tobytes() == want.h.tobytes()


def test_tick_reports_the_stages_and_cancels() -> None:
    scene = cloud.plain(6, side=60.0, buildings=1, trees=10, outliers=5)
    seen: list[tuple[float, str]] = []
    ground.classify_ground(scene.xyz, None, QUARRY, tick=lambda f, m: seen.append((f, m)))
    fractions = [f for f, _ in seen]
    assert fractions[0] == 0.0 and fractions[-1] == 1.0 and fractions == sorted(fractions)
    assert {m for _, m in seen} == set(ground.STAGES)

    class Stop(Exception):
        pass

    def stop(fraction: float, stage: str) -> None:
        if stage == "Ткань":
            raise Stop

    with pytest.raises(Stop):
        ground.classify_ground(scene.xyz, None, QUARRY, threads=4, tick=stop)


# --- Т-З4, «Блоки» ---------------------------------------------------------------------------------------


def _two_pits(seed: int) -> cloud.Scene:
    """The funnel of Т-З2 (60 m deep) and the benches of Т-З3 side by side: 290 × 190 m, six blocks of 128 m."""
    a, b = cloud.funnel(seed, depth=60.0), cloud.benches(seed + 1)
    shift = np.array([a.extent[2] - b.extent[0] + 20.0, 0.0, 0.0])
    return cloud.Scene(
        xyz=np.ascontiguousarray(np.vstack([a.xyz, b.xyz + shift])),
        truth=np.concatenate([a.truth, b.truth]),
        tag=np.concatenate([a.tag, b.tag]),
        ret=np.concatenate([a.ret, b.ret]),
        origin=a.origin,
        ground_z=a.ground_z,
        ground_cos=a.ground_cos,
        extent=(a.extent[0], a.extent[1], b.extent[2] + shift[0], max(a.extent[3], b.extent[3])),
    )


def test_tz4_threads_and_repeats_give_the_same_bytes() -> None:
    scene = _two_pits(404)
    first = ground.classify_ground(scene.xyz, scene.ret, QUARRY, threads=1, block=128)
    assert first.report["blocks"] >= 4
    got = first.cls == 2
    assert got[scene.tag == cloud.T_WALL].mean() >= 0.95 and got[scene.tag == cloud.T_SLOPE].mean() >= 0.98
    for threads in (2, 4, 8, 1):
        again = ground.classify_ground(scene.xyz, scene.ret, QUARRY, threads=threads, block=128)
        assert again.cls.tobytes() == first.cls.tobytes(), threads
        assert again.h.tobytes() == first.h.tobytes(), threads  # NaN included: the bytes, not the values
        assert again.report["sweeps"] == first.report["sweeps"]
    # and so are the vertices of the DTM built from the ground
    v1, t1, _ = build_dtm(scene.xyz[first.cls == 2])
    v2, t2, _ = build_dtm(scene.xyz[again.cls == 2])
    assert v1.tobytes() == v2.tobytes() and t1.tobytes() == t2.tobytes()
    # one block and threads: the same again
    whole = ground.classify_ground(scene.xyz, scene.ret, QUARRY, threads=1)
    for threads in (2, 8):
        again = ground.classify_ground(scene.xyz, scene.ret, QUARRY, threads=threads)
        assert again.cls.tobytes() == whole.cls.tobytes() and again.h.tobytes() == whole.h.tobytes()


def test_blocks_keep_the_thresholds_of_tz1(plain) -> None:  # type: ignore[no-untyped-def]
    scene, whole = plain
    res = ground.classify_ground(scene.xyz, None, PLAIN, threads=2, block=128)
    assert res.report["blocks"] == 4
    s = scores(scene, res.cls)
    assert s["recall"] >= 0.99 and s["false"] <= 0.01, s
    assert (res.cls[scene.tag == cloud.T_OUTLIER] == 7).all()
    dev = dtm_deviation(scene, res.cls)
    assert float(np.sqrt((dev**2).mean())) <= 0.1
    assert float((res.cls != whole.cls).mean()) < 0.001  # the seams of the blocks do not show


def test_blocks_keep_the_thresholds_of_tz2(funnel) -> None:  # type: ignore[no-untyped-def]
    scene, whole = funnel
    res = ground.classify_ground(scene.xyz, None, QUARRY, threads=2, block=128)
    assert res.report["blocks"] >= 16
    s = scores(scene, res.cls)
    assert s["slope"] >= 0.98, s
    x0, y0, x1, y1 = scene.extent
    centre, radius = ((x0 + x1) / 2, (y0 + y1) / 2), (x1 - x0) / 2
    dev = dtm_deviation(scene, res.cls, lambda x, y: np.hypot(x - centre[0], y - centre[1]) < radius - 2.0)
    assert float(np.sqrt((dev**2).mean())) <= 0.15
    assert float((res.cls != whole.cls).mean()) < 0.01


# --- other steps of the cloth ----------------------------------------------------------------------------


@pytest.mark.parametrize("changes", [{"cr": 2.0}, {"cr": 0.5}, {"ct": 0.2}, {"ri": 1}, {"noise": False}])
def test_benches_hold_with_other_parameters(benches, changes) -> None:  # type: ignore[no-untyped-def]
    scene, _res = benches
    res = ground.classify_ground(scene.xyz, None, ground.make_params("quarry", **changes), threads=2)
    got = res.cls == 2
    assert got[scene.tag == cloud.T_WALL].mean() >= 0.95, changes
    assert got[np.isin(scene.tag, (cloud.T_BERM, cloud.T_TOP, cloud.T_FLOOR))].mean() >= 0.98, changes
    assert not got[scene.tag == cloud.T_ROOF].any()
    if changes.get("ct") == 0.2:
        assert res.report["classes"][3] > 0  # low vegetation: between CT and 0.5 m


def test_a_step_finer_than_the_cloud_is_refused_with_the_step_to_take() -> None:
    scene = cloud.funnel(505, depth=30.0)  # 2 points/m²: a point every 0.7 m
    with pytest.raises(ground.GroundError) as err:
        ground.classify_ground(scene.xyz, None, ground.make_params("quarry", cr=0.5))
    assert err.value.code == "cloud_too_sparse"
    assert "CR = 0,5 м" in err.value.message and err.value.hint == "Задайте шаг ткани CR не меньше 1 м."
    res = ground.classify_ground(scene.xyz, None, ground.make_params("quarry", cr=0.8))
    assert [w["code"] for w in res.report["warnings"]] == ["cloud_sparse"] and 0.25 <= res.report["fill"] < 0.35
    assert scores(scene, res.cls)["recall"] >= 0.98
    res = ground.classify_ground(scene.xyz, None, QUARRY)
    assert res.report["warnings"] == [] and res.report["fill"] > 0.35


def test_returns_of_a_facade_one_above_another_do_not_pull_the_cloth_down() -> None:
    """A plane through the lowest returns of a facade stands almost upright; continued to a particle beside it
    it gave an obstacle tens of metres under the ground, and the stiff cloth of «Равнина» followed it."""
    scene = cloud.plain(606, side=120.0, buildings=2, trees=30, outliers=10)
    res = ground.classify_ground(scene.xyz, None, ground.make_params("plain", cr=0.4))
    s = scores(scene, res.cls)
    assert s["recall"] >= 0.99 and s["false"] <= 0.01, s
