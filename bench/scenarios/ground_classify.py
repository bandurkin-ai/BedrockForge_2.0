"""«Фильтр земли» on the scene «карьер» (ADR 0014 «Замеры»; ТЗ 7.35 Д-22: 10 млн точек ≤ 90 с и ≤ 4 ГБ на
4 ядрах, 50 млн — по частям ≤ 6 мин).

The scene (`tools/gen/cloud.py::quarry`, true labels): sites of 700 × 700 m, each with a conical pit 100 m deep
(wall 50°, vegetation on 15 % of it), a pit of four benches with vertical walls 15 m high, a dump 25 m high,
a rolling plain with trees, bushes and buildings, single outliers. The points come in the order of a stored
cloud (cells of 1 m along a Morton curve, ADR 0014 §1) and are read the way the job reads the blob `xyz`:
a memory map, not a copy. One million points — 2 points/m², ten million — 20, fifty million — 2 × 2 sites.

  classify_s   `geo/ground.py::classify_ground`, profile «Карьер», 4 threads (the kernels are warm);
  single_s     the same on 1 thread — and `mismatch`: the bytes of `class` and `h` that differ from 4 threads
               (must be 0: Т-З4); `params.single = false` skips it (50 million points);
  recall       ground points (walls aside) taken as ground; `recall_miss` = 1 − recall;
  false_ground points that are not ground among the class 2;
  walls        points of the vertical walls taken as ground; `walls_miss` = 1 − walls;
  the seconds of every stage, the sweeps of the cloth, the blocks, the counts by class.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SEED = 17
THREADS = 4


def prepare(params: dict[str, Any], work: Path) -> None:
    import sys

    import numpy as np

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.gen import cloud

    scene = cloud.quarry(int(params["points"]), SEED, order="storage")
    np.save(work / "xyz.npy", scene.xyz)
    np.save(work / "truth.npy", scene.truth)
    np.save(work / "tag.npy", scene.tag)
    load()  # compile into the cache outside the timing


def load() -> None:
    from bf_core.geo import ground

    ground.warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import sys
    import time

    import numpy as np

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.gen import cloud

    from bf_core.geo import ground

    xyz = np.load(work / "xyz.npy", mmap_mode="r")
    profile = ground.PROFILES["quarry"]
    t0 = time.perf_counter()
    res = ground.classify_ground(xyz, None, profile, threads=THREADS)
    t1 = time.perf_counter()
    info: dict[str, Any] = {"points": len(xyz), "classify_s": round(t1 - t0, 3), "threads": THREADS}
    if params.get("single", True):
        one = ground.classify_ground(xyz, None, profile, threads=1)
        info["single_s"] = round(time.perf_counter() - t1, 3)
        info["mismatch"] = int(np.count_nonzero(one.cls != res.cls)) + int(
            np.count_nonzero(one.h.view(np.uint32) != res.h.view(np.uint32))
        )
        del one
    truth = np.load(work / "truth.npy")
    tag = np.load(work / "tag.npy")
    got = res.cls == 2
    walls = tag == cloud.T_WALL
    ground_truth = (truth == cloud.GROUND) & ~walls
    recall = float(np.count_nonzero(got & ground_truth) / np.count_nonzero(ground_truth))
    false = float(np.count_nonzero(got & (truth != cloud.GROUND)) / max(np.count_nonzero(got), 1))
    wall = float(np.count_nonzero(got & walls) / max(np.count_nonzero(walls), 1))
    outliers = tag == cloud.T_OUTLIER
    report = res.report
    info.update(
        recall=round(recall, 4),
        recall_miss=round(1.0 - recall, 4),
        false_ground=round(false, 4),
        walls=round(wall, 4),
        walls_miss=round(1.0 - wall, 4),
        outliers_as_noise=round(float(np.count_nonzero(res.cls[outliers] == 7) / max(int(outliers.sum()), 1)), 4),
        blocks=report["blocks"],
        sweeps=report["sweeps"],
        edge_areas=report["edge_areas"],
        face_points=report["face_points"],
        classes={str(k): v for k, v in report["classes"].items()},
        stages_s=report["seconds"],
    )
    return info
