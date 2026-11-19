"""Storing a point cloud and the first data of its frame (ADR 0014 «Замеры»: «Импорт LAS и первые данные кадра —
1 млн / 10 млн: ≤ 6 с, 600 МБ / ≤ 30 с, 1,5 ГБ; манифест ≤ 0,2 с; все части на грубом уровне ≤ 1 с; часть LOD 0
≤ 0,1 с; прирост памяти API ≤ 150 МБ; расхождений цикла 0»).

From arrays (подшаг 7.1.3): the source is a file of float64 triples with classes and returns, read by portions
of a million points in the order of flight strips — what a LAS reader gives; the LAS file itself joins with
подшаг 7.1.10. The scene is the one of Т-З1 in large: a plane at 8 points a square metre with noise, «buildings»
and a pit, UTM coordinates.

  import_s        the two passes over the source, the parts and the levels, the four columns with their
                  SHA-256 in the blob store, the layer in the project database (`stage_cloud`, `commit_cloud`);
  import_grew_mb  how much the memory of the process grew while it did (does not depend on the cloud);
  manifest_s      the state of the layer and the manifest, as `GET /viz/lines/{id}` gives it;
  coarse_s        every part at the coarsest level (32 m) — with the manifest, what the first frame needs;
  first_s         import_s + manifest_s + coarse_s;
  part0_s         the slowest part at full detail (`part0_mean_s` — the mean; `parts0_s` — all of them);
  api_grew_mb     how much the memory grew while the manifest and all the parts were given;
  repeat_s        a repeated request of everything: 304 by the ETags, nothing is read;
  max_error_m     the largest distance between a stored point (float64) and the point of the full level as the
                  window gets it (float32 offsets from the origin of its part), over 16 parts all over the cloud;
  cycle_mismatch  0 when the stored cloud is the source: the same number of points and the same sum of the
                  hashes of (X, Y, Z, record number, class, return) of every point, whatever their order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PORTION = 1_000_000
DENSITY = 8.0  # points a square metre
ORIGIN = (512_345.678, 6_234_567.123, 412.3)


def _digest(xyz: Any, seq: Any, cls: Any, ret: Any) -> int:
    """The sum of the hashes of points (wrapping uint64): the same for the same points in any order."""
    import numpy as np

    bits = np.ascontiguousarray(xyz, dtype="<f8").view(np.uint64).reshape(-1, 3)
    tail = (np.asarray(cls).astype(np.uint64) << np.uint64(8)) | np.asarray(ret).astype(np.uint64)
    h = bits[:, 0] * np.uint64(0x9E3779B97F4A7C15) ^ bits[:, 1] * np.uint64(0xC2B2AE3D27D4EB4F)
    h ^= bits[:, 2] * np.uint64(0x165667B19E3779F9) ^ np.asarray(seq).astype(np.uint64) * np.uint64(0xD6E8FEB86659FD93)
    h ^= tail * np.uint64(0xFF51AFD7ED558CCD)
    h ^= h >> np.uint64(29)
    h *= np.uint64(0xBF58476D1CE4E5B9)
    h ^= h >> np.uint64(32)
    return int(h.sum(dtype=np.uint64))


def _source(work: Path, n: int) -> Any:
    """The portions of the source files, from the start at every call."""
    import numpy as np

    from bf_core.lines.cloud import CloudChunk

    def portions() -> Any:
        for a in range(0, n, PORTION):
            m = min(PORTION, n - a)
            xyz = np.fromfile(work / "xyz.f8", dtype="<f8", count=m * 3, offset=a * 24).reshape(-1, 3)
            cls = np.fromfile(work / "class.u1", dtype=np.uint8, count=m, offset=a)
            ret = np.fromfile(work / "ret.u1", dtype=np.uint8, count=m, offset=a)
            yield CloudChunk(xyz, cls, ret)

    return portions


def prepare(params: dict[str, Any], work: Path) -> None:
    import json

    import numpy as np

    from bf_core.projects.project import create_folder

    n = int(params["points"])
    side = (n / DENSITY) ** 0.5
    rng = np.random.default_rng(20261119)
    digest = 0
    with open(work / "xyz.f8", "wb") as fx, open(work / "class.u1", "wb") as fc, open(work / "ret.u1", "wb") as fr:
        strips = max(1, round(side / 150.0))  # flight strips 150 m wide, points along a strip as they were shot
        done = 0
        for s in range(strips):
            m = n // strips + (1 if s < n % strips else 0)
            x = np.sort(rng.uniform(0.0, side, m))
            y = (s + rng.uniform(0.0, 1.0, m)) * (side / strips)
            z = rng.normal(0.0, 0.02, m)
            r = np.hypot(x - 0.5 * side, y - 0.5 * side)
            z -= np.clip(0.3 * side - r, 0.0, None) * 1.19  # a pit: walls of 50°
            roof = (np.floor(x / 97.0) % 3 == 1) & (np.floor(y / 61.0) % 4 == 2) & (r > 0.32 * side)
            z[roof] += 5.0
            xyz = np.column_stack([x, y, z]) + ORIGIN
            cls = np.where(roof, 6, 2).astype(np.uint8)
            ret = np.full(m, 0x11, dtype=np.uint8)
            digest = (digest + _digest(xyz, np.arange(done, done + m), cls, ret)) % (1 << 64)
            fx.write(xyz.astype("<f8").tobytes())
            fc.write(cls.tobytes())
            fr.write(ret.tobytes())
            done += m
    folder = create_folder(work, "Облако точек", {})
    (work / "prepared.json").write_text(json.dumps({"folder": str(folder), "digest": digest}), encoding="utf-8")
    load()  # compile the kernels into the cache outside the timing


def load() -> None:
    import tempfile

    import numpy as np

    from bf_core.lines import cloud_build as cb
    from bf_core.lines.cloud import CloudChunk
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz import cloud, lines  # noqa: F401 — numba and the viz modules load outside the timing

    points = np.random.default_rng(1).uniform(0.0, 50.0, (5000, 3))
    with tempfile.TemporaryDirectory(prefix="bf-cloud-") as tmp:
        cb.stage_cloud(BlobStore(Path(tmp)), lambda: [CloudChunk(points)]).discard()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import json
    import threading
    import time
    from types import SimpleNamespace

    import numpy as np
    import psutil

    from bf_core.db import dialect
    from bf_core.lines import cloud as lc
    from bf_core.lines import cloud_build as cb
    from bf_core.lines import store as ls_store
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz import binary, lines
    from bf_core.viz import cloud as vc

    n = int(params["points"])
    prepared = json.loads((work / "prepared.json").read_text(encoding="utf-8"))
    folder = Path(prepared["folder"])
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    project = SimpleNamespace(engine=engine, uid="bench", folder=folder)
    store = BlobStore(folder)
    process = psutil.Process()
    peak = [0]
    watching = threading.Event()

    def watch() -> None:
        while not watching.wait(0.002):
            peak[0] = max(peak[0], process.memory_info().rss)

    def grew(before: int) -> float:
        return round(max(0, max(peak[0], process.memory_info().rss) - before) / 2**20, 1)

    threading.Thread(target=watch, daemon=True).start()
    out: dict[str, Any] = {"points": n}
    try:
        before = peak[0] = process.memory_info().rss
        t0 = time.perf_counter()
        staged = cb.stage_cloud(store, _source(work, n), expected=n)
        out["stage_s"] = round(time.perf_counter() - t0, 3)
        with dialect.write_tx(engine) as conn:
            file_id = ls_store.create_file(conn, store, name="съёмка.las", meta={"format": "las"})
            layer_id = lc.commit_cloud(
                conn, store, staged, file_id=file_id, name="Съёмка", source={"kind": "las", "every": 1}
            )
        out["import_s"] = round(time.perf_counter() - t0, 3)
        out["import_grew_mb"] = grew(before)

        before = peak[0] = process.memory_info().rss
        t1 = time.perf_counter()
        with dialect.read_tx(engine) as conn:
            state = lines.layer_state(conn, layer_id)
            view = vc.view(conn, state)
        assert view is not None
        manifest = vc.manifest(project, state, view)
        parts = binary.decode(manifest)[0]["parts"]
        out["manifest_s"] = round(time.perf_counter() - t1, 4)
        t2 = time.perf_counter()
        coarse = [vc.part(project, state, view, p["k"], 4, "class") for p in parts]
        out["coarse_s"] = round(time.perf_counter() - t2, 4)
        out["first_s"] = round(out["import_s"] + out["manifest_s"] + out["coarse_s"], 3)

        slowest = total = 0.0
        shown = 0
        for p in parts:
            t = time.perf_counter()
            body = vc.part(project, state, view, p["k"], 0, "class")
            took = time.perf_counter() - t
            slowest, total = max(slowest, took), total + took
            shown += int(binary.decode(body)[0]["n"])  # the header only: the arrays are views of the answer
            del body
        assert shown == n
        out.update(part0_s=round(slowest, 4), part0_mean_s=round(total / len(parts), 4), parts0_s=round(total, 3))
        out["api_grew_mb"] = grew(before)  # the manifest and every part at both ends of the levels, one by one

        error = 0.0
        for p in parts[:: max(1, len(parts) // 16)]:  # outside the memory above: the check holds a part thrice
            head, arrays = binary.decode(vc.part(project, state, view, p["k"], 0, "class"))
            stored = np.fromfile(
                store.path_of(str(state.sha("xyz"))), dtype="<f8", count=head["n"] * 3, offset=head["start"] * 24
            ).reshape(-1, 3)
            back = arrays["xyz"].reshape(-1, 3).astype(np.float64) + np.asarray(head["origin"])
            error = max(error, float(np.abs(back - stored).max()))
            assert np.array_equal(arrays["line"], np.arange(head["start"], head["start"] + head["n"]))
        out["max_error_m"] = error

        t3 = time.perf_counter()
        with dialect.read_tx(engine) as conn:
            again = lines.layer_state(conn, layer_id)
            assert vc.view(conn, again) is not None
        tags = [vc.layer_etag(project.uid, again)]
        tags += [vc.part_etag(project.uid, again, p["k"], lod, "class") for p in parts for lod in range(5)]
        out["repeat_s"] = round(time.perf_counter() - t3, 4)
        assert tags[0] == vc.layer_etag(project.uid, state)
        assert tags[1:] == [tag for p in parts for tag in p["etags"]]  # nothing changed: 304 for every request

        with dialect.read_tx(engine) as conn:
            cloud = lc.open_cloud(conn, store, layer_id)
        digest = 0
        for a in range(0, cloud.count, PORTION):
            m = min(PORTION, cloud.count - a)
            cols = [cloud.read(role, a, m) for role in ("xyz", "seq", "class", "ret")]
            digest = (digest + _digest(*cols)) % (1 << 64)
        out["cycle_mismatch"] = 0 if (cloud.count, digest) == (n, prepared["digest"]) else max(1, abs(n - cloud.count))
        levels = np.array([p["levels"] for p in parts]).sum(axis=0)
        out.update(
            parts=len(parts),
            manifest_kb=round(len(manifest) / 1024, 1),
            coarse_mb=round(sum(len(b) for b in coarse) / 2**20, 2),
            largest_part=int(max(p["n"] for p in parts)),
            blobs_mb=round(sum(cloud.paths[r].stat().st_size for r in cloud.paths) / 2**20, 1),
            **{f"points_lod{j}": int(v) for j, v in enumerate(levels)},
        )
    finally:
        watching.set()
        engine.dispose()
    return out
