"""The streamed build of a point cloud (ADR 0014 §1 «Сборка при импорте»; `lines/cloud_build.stage_cloud`): the
same cloud, byte for byte, as the build in memory — whatever the portions of the source, the size of the tiles
and the path a crowded tile takes; 5 million points within a gigabyte; a cancel, a source that changed and a
source without points leave no file; the cloud survives the archive `.bfggis`."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners
from bf_core.formats.errors import FormatError
from bf_core.lines import cloud as lc
from bf_core.lines import cloud_build as cb
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.test_archive import export, wait_job
from tests.test_cloud_store import UTM, scene
from tests.test_import_job import API

pytestmark = pytest.mark.windows


def portions(chunk: lc.CloudChunk, size: int) -> lc.ChunkSource:
    """A source that gives the points of a chunk by `size` at a time, from the start at every call."""

    def source() -> Iterator[lc.CloudChunk]:
        for a in range(0, len(chunk.xyz), size):
            yield lc.CloudChunk(
                chunk.xyz[a : a + size],
                None if chunk.cls is None else chunk.cls[a : a + size],
                None if chunk.ret is None else chunk.ret[a : a + size],
                None if chunk.seq is None else chunk.seq[a : a + size],
            )

    return source


def hard(seed: int = 12) -> lc.CloudChunk:
    """A scan of one spot, one point many times, strays thousands of kilometres away, points across the axes
    of the grid, junk — in the order of nothing."""
    rng = np.random.default_rng(seed)
    spot = np.repeat(UTM[None, :], 9000, axis=0) + rng.uniform(0, 3.0, (9000, 3))
    same = np.repeat(UTM[None, :] + [5.0, 5.0, 0.0], 1500, axis=0)
    far = UTM + rng.uniform(-1, 1, (60, 3)) * [3e6, 3e6, 5e4]
    zero = rng.uniform(-200, 200, (4000, 3))
    junk = np.array([[math.nan, 0, 0], [0, math.inf, 0], [1e12, 1, 1], [1, 1, -1e300]])
    xyz = np.vstack([spot, junk[:2], same, far, zero, junk[2:]])
    xyz = xyz[rng.permutation(len(xyz))]
    cls = rng.integers(0, 256, len(xyz)).astype(np.uint8)
    ret = rng.integers(0, 256, len(xyz)).astype(np.uint8)
    seq = (np.arange(len(xyz), dtype=np.uint32) * 3 + 7)[::-1].copy()  # record numbers of a thinned file, falling
    return lc.CloudChunk(xyz, cls, ret, seq)


def same_cloud(staged: lc.StagedCloud, data: cb.CloudData) -> None:
    assert (staged.count, staged.skipped, staged.bbox, staged.classes) == (
        data.count,
        data.skipped,
        data.bbox,
        data.classes,
    )
    assert staged.parts == data.parts
    for role, want in zip(lc.BASE_ROLES, (data.xyz, data.seq, data.cls, data.ret), strict=True):
        path, sha, size = staged.files[role]
        raw = path.read_bytes()
        assert raw == want.astype(lc.ROLES[role]).tobytes() and size == len(raw), role
        import hashlib

        assert sha == hashlib.sha256(raw).hexdigest()


@pytest.fixture()
def store(tmp_path: Path) -> BlobStore:
    return BlobStore(tmp_path)


@pytest.mark.parametrize(
    ("what", "size", "limit", "kw"),
    [
        ("scene", 7919, lc.PART_POINTS, {}),
        ("scene", 100_000, 4096, {}),  # one portion; a 64 m tile holds more than a part: sorted in memory
        ("scene", 1000, 4096, {"chunk": 1500}),  # … through the second temporary file
        ("scene", 33_333, 4096, {"max_tiles": 3}),  # tiles of 128 m and wider
        ("hard", 977, 512, {"chunk": 700}),
        ("hard", 5000, 512, {"max_tiles": 8}),
        ("hard", 100_000, lc.PART_POINTS, {}),
    ],
)
def test_streamed_build_gives_the_cloud_of_the_build_in_memory(
    store: BlobStore, what: str, size: int, limit: int, kw: dict[str, int]
) -> None:
    chunk = scene(11, 100_000) if what == "scene" else hard()
    data = cb.build_cloud([chunk], part_points=limit)
    staged = cb.stage_cloud(store, portions(chunk, size), part_points=limit, **kw)
    try:
        same_cloud(staged, data)
        assert sorted(p.name for p in store.tmp_dir.iterdir()) == sorted(f[0].name for f in staged.files.values())
    finally:
        staged.discard()
    assert not list(store.tmp_dir.iterdir())


def test_tiles_grow_when_the_cloud_is_spread_thin(store: BlobStore) -> None:
    """A point every 90 m (a regional DEM given as points): every point in a 64 m tile of its own. The tiles of
    the sort become wider, the cloud is the same."""
    rng = np.random.default_rng(3)
    gx, gy = np.meshgrid(np.arange(120) * 90.0, np.arange(100) * 90.0)
    xyz = UTM + np.column_stack([gx.ravel(), gy.ravel(), rng.uniform(0, 300, gx.size)])
    chunk = lc.CloudChunk(xyz[rng.permutation(len(xyz))])
    data = cb.build_cloud([chunk], part_points=2000)
    seen: list[int] = []
    real = cb._survey

    def spy(*args, **kw):  # type: ignore[no-untyped-def]
        out = real(*args, **kw)
        seen.append(out.shift)
        assert len(out.tiles) <= 64 and int(out.counts.sum()) == len(xyz)
        return out

    cb._survey = spy  # type: ignore[assignment]
    try:
        staged = cb.stage_cloud(store, portions(chunk, 2500), part_points=2000, max_tiles=64)
    finally:
        cb._survey = real  # type: ignore[assignment]
    assert seen == [11]  # 2048 m: 6 × 5 tiles
    same_cloud(staged, data)
    staged.discard()
    assert max(max(p.bbox[3] - p.bbox[0], p.bbox[4] - p.bbox[1]) for p in data.parts) <= cb.MAX_SPAN_M


def test_progress_grows_to_one_and_a_cancel_leaves_no_files(store: BlobStore) -> None:
    chunk = hard()
    source = portions(chunk, 1000)
    shares: list[float] = []
    staged = cb.stage_cloud(store, source, shares.append, expected=len(chunk.xyz), part_points=512, chunk=700)
    staged.discard()
    k = math.ceil(len(chunk.xyz) / 1000)  # a call a portion in either pass, then a call a part and a read
    assert shares[0] == 0.0 and shares[-1] == 1.0 and shares == sorted(shares) and len(shares) > 60
    assert all(s < 0.25 for s in shares[:k]) and all(0.25 <= s < 0.6 for s in shares[k : 2 * k])
    assert all(s >= 0.6 for s in shares[2 * k :])
    blind: list[float] = []
    cb.stage_cloud(store, source, blind.append, part_points=512, chunk=700).discard()
    assert len(blind) == len(shares) and set(blind[:k]) == {0.0} and blind[k:] == shares[k:]  # no `expected`

    class Cancel(Exception):
        pass

    for stop in (0, 7, k, k + 9, 2 * k, 2 * k + 3, 55, 90, len(shares) - 2, len(shares) - 1):  # in every stage
        calls = 0

        def tick(_share: float, stop: int = stop) -> None:
            nonlocal calls
            calls += 1
            if calls > stop:
                raise Cancel

        with pytest.raises(Cancel):
            cb.stage_cloud(store, source, tick, part_points=512, chunk=700)
        assert not list(store.tmp_dir.iterdir()), stop


def test_a_source_that_changed_or_has_no_points_leaves_no_files(store: BlobStore) -> None:
    chunk = scene(5, 20_000)
    assert chunk.cls is not None and chunk.ret is not None
    moved = lc.CloudChunk(chunk.xyz + [500.0, 0.0, 0.0], chunk.cls, chunk.ret)
    fewer = lc.CloudChunk(chunk.xyz[:-1], chunk.cls[:-1], chunk.ret[:-1])
    more = lc.CloudChunk(np.vstack([chunk.xyz, chunk.xyz[:1]]), np.append(chunk.cls, 0), np.append(chunk.ret, 0))
    junk = lc.CloudChunk(np.vstack([chunk.xyz[:-1], [[math.nan, 0, 0]]]), chunk.cls, chunk.ret)
    for second in (moved, fewer, more, junk):
        passes = iter([chunk, second])
        with pytest.raises(FormatError) as err:
            cb.stage_cloud(store, lambda passes=passes: iter([next(passes)]))  # type: ignore[misc]
        assert err.value.code == "source_changed" and err.value.hint
        assert not list(store.tmp_dir.iterdir())
    for nothing in ([], [lc.CloudChunk(np.empty((0, 3)))], [lc.CloudChunk(np.full((4, 3), math.inf))]):
        with pytest.raises(FormatError) as err:
            cb.stage_cloud(store, lambda nothing=nothing: iter(nothing))  # type: ignore[misc]
        assert err.value.code == "no_points"
        assert not store.tmp_dir.exists() or not list(store.tmp_dir.iterdir())
    with pytest.raises(ValueError, match="triples"):
        cb.stage_cloud(store, lambda: iter([lc.CloudChunk(np.zeros((3, 4)))]))
    assert not store.tmp_dir.exists() or not list(store.tmp_dir.iterdir())
    # 7.1.18: record 2³² of a source that does not number its records has no number — told to the user, in
    # Russian with a hint (it was a ValueError: «внутренняя ошибка» of the import)
    last = cb.columns(lc.CloudChunk(chunk.xyz[:2]), (1 << 32) - 2)[1]
    assert last.dtype == np.dtype("<u4") and last.tolist() == [(1 << 32) - 2, (1 << 32) - 1]
    with pytest.raises(FormatError) as err:
        cb.columns(lc.CloudChunk(chunk.xyz[:2]), (1 << 32) - 1)
    assert err.value.code == "too_many_points" and "4 294 967 296" in err.value.message
    assert "каждую k-ю запись" in err.value.hint and err.value.as_dict()["hint"]
    numbered = lc.CloudChunk(chunk.xyz[:2], seq=np.array([5, 9], dtype=np.uint32))  # its own numbers: any base
    assert cb.columns(numbered, 1 << 40)[1].tolist() == [5, 9]


def test_points_scattered_over_a_continent_are_refused(store: BlobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Junk coordinates: every point in a 16 km square of its own would make a part a point."""
    rng = np.random.default_rng(4)
    wide = lc.CloudChunk(rng.uniform(-4e8, 4e8, (3000, 3)))
    spot = lc.CloudChunk(np.repeat(UTM[None, :], 3000, axis=0))  # one cell: cut every 100 points
    for limit, chunk, parts in ((100, wide, 3000), (2, wide, 3000), (25, spot, 30)):
        monkeypatch.setattr(cb, "MAX_PARTS", limit)
        for build in (
            lambda chunk=chunk: cb.build_cloud([chunk], part_points=100),
            lambda chunk=chunk: cb.stage_cloud(store, portions(chunk, 700), part_points=100, chunk=500),
        ):
            with pytest.raises(FormatError) as err:
                build()
            assert err.value.code == "cloud_too_wide" and "метрах" in err.value.hint
            assert not store.tmp_dir.exists() or not list(store.tmp_dir.iterdir())
        monkeypatch.setattr(cb, "MAX_PARTS", parts)  # as many parts as there are: built
        data = cb.build_cloud([chunk], part_points=100)
        staged = cb.stage_cloud(store, portions(chunk, 700), part_points=100, chunk=500)
        assert len(data.parts) == parts
        same_cloud(staged, data)
        staged.discard()


def test_staged_cloud_is_committed_and_read_back(tmp_path: Path) -> None:
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    store = BlobStore(tmp_path)
    chunk = scene(8, 30_000)
    data = cb.build_cloud([chunk])
    try:
        staged = cb.stage_cloud(store, portions(chunk, 4096))
        with dialect.write_tx(engine) as conn:
            file_id = ls_store.create_file(conn, store, name="борт.xyz", meta={"format": "xyz"})
            layer_id = lc.commit_cloud(conn, store, staged, file_id=file_id, name="Борт", source={"kind": "xyz"})
        assert not list(store.tmp_dir.iterdir())
        with dialect.read_tx(engine) as conn:
            cloud = lc.open_cloud(conn, store, layer_id)
        assert cloud.count == 30_000 and list(cloud.parts) == data.parts and cloud.classes == data.classes
        assert cloud.read("xyz").tobytes() == data.xyz.tobytes() and np.array_equal(cloud.read("seq"), data.seq)
    finally:
        engine.dispose()


def test_a_source_may_be_any_object_with_the_four_arrays(store: BlobStore) -> None:
    """`formats.las.LasChunk` is not a `CloudChunk`: the builder takes a portion by its fields."""

    class Chunk:
        def __init__(self, a: int, b: int, src: lc.CloudChunk) -> None:
            assert src.cls is not None and src.ret is not None
            self.start = a
            self.xyz, self.cls, self.ret = src.xyz[a:b], src.cls[a:b], src.ret[a:b]
            self.seq = np.arange(a, min(b, len(src.xyz)), dtype="<u4") * 4

    src = scene(3, 9000)
    staged = cb.stage_cloud(store, lambda: (Chunk(a, a + 2500, src) for a in range(0, 9000, 2500)))
    seq = np.fromfile(staged.files["seq"][0], dtype="<u4")
    staged.discard()
    assert staged.count == 9000 and np.array_equal(np.sort(seq), np.arange(9000) * 4)


# --- 5 million points --------------------------------------------------------------------------------------

BIG = r"""
import json, sys, threading, time
from pathlib import Path

import numpy as np
import psutil

from bf_core.paths import configure_numba_cache

configure_numba_cache()
from bf_core.lines import cloud as lc
from bf_core.lines import cloud_build as cb
from bf_core.storage.blobs import BlobStore

n, folder = int(sys.argv[1]), Path(sys.argv[2])
SIDE = (n / 8.0) ** 0.5  # the scene of Т-З1: 8 points a square metre


def source():
    for k in range(0, n, 1_000_000):
        rng = np.random.default_rng([77, k])
        m = min(1_000_000, n - k)
        xyz = np.column_stack([rng.uniform(0, SIDE, m), rng.uniform(0, SIDE, m), rng.normal(0, 0.02, m)])
        roof = (np.floor(xyz[:, 0] / 97.0) % 3 == 1) & (np.floor(xyz[:, 1] / 61.0) % 4 == 2)
        xyz[roof, 2] += 5.0
        xyz += [512_345.678, 6_234_567.123, 412.3]
        yield lc.CloudChunk(xyz, rng.integers(0, 8, m).astype(np.uint8), np.full(m, 0x11, dtype=np.uint8))


def rss():
    return psutil.Process().memory_info().rss


store = BlobStore(folder)
cb.stage_cloud(store, lambda: [lc.CloudChunk(np.random.default_rng(1).uniform(0, 50, (5000, 3)))]).discard()  # warm
before = rss()
peak = [before]
stop = threading.Event()


def watch():
    while not stop.wait(0.005):
        peak[0] = max(peak[0], rss())


threading.Thread(target=watch, daemon=True).start()
t0 = time.perf_counter()
staged = cb.stage_cloud(store, source, expected=n)
seconds = time.perf_counter() - t0
stop.set()
seq = np.fromfile(staged.files["seq"][0], dtype="<u4")
levels = np.array([p.n for p in staged.parts]).sum(axis=0).tolist()
print(json.dumps({
    "seconds": seconds, "grew_mb": (peak[0] - before) / 2**20, "count": staged.count, "parts": len(staged.parts),
    "levels": levels, "seq_ok": bool(np.array_equal(np.sort(seq), np.arange(n, dtype=np.uint32))),
    "size": sum(f[2] for f in staged.files.values()), "tmp": len(list(store.tmp_dir.iterdir())),
    "largest": max(p.n[0] for p in staged.parts), "skipped": staged.skipped,
}))
staged.discard()
"""


@pytest.mark.slow
def test_five_million_points_are_built_within_a_gigabyte(tmp_path: Path) -> None:
    n = 5_000_000
    script = tmp_path / "big.py"
    script.write_text(BIG, encoding="utf-8")
    core = Path(__file__).resolve().parents[1]
    run = subprocess.run(
        [sys.executable, str(script), str(n), str(tmp_path / "project")],
        capture_output=True,
        text=True,
        cwd=core,
        env={**os.environ, "PYTHONPATH": str(core)},
        timeout=600,
    )
    assert run.returncode == 0, run.stderr[-4000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    print(out)
    assert out["count"] == n and out["skipped"] == 0 and out["seq_ok"] and out["size"] == n * 30
    assert out["tmp"] == 4  # the four columns: the temporary files of the sort are gone
    assert out["grew_mb"] <= 1024, out  # ADR 0014: the memory of the job does not grow with the cloud
    assert out["largest"] <= lc.PART_POINTS and 10 <= out["parts"] <= 40
    assert out["levels"][0] == n and out["levels"] == sorted(out["levels"], reverse=True)


# --- the archive -------------------------------------------------------------------------------------------


def test_cloud_survives_the_archive(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """`.bfggis` (ADR 0014 «Хранение и показ»): the layer with `meta.cloud`, its columns under the roles of a
    cloud and the new classes come back in a copy of the project, bit for bit, under new ids."""
    pclient.post(API + "/projects", json={"name": "Облако"})
    p = projects_of(pclient.core).require_current()
    store = BlobStore(p.folder)
    chunk = scene(9, 40_000)
    staged = cb.stage_cloud(store, portions(chunk, 9000), part_points=8192)
    with dialect.write_tx(p.engine) as conn:
        file_id = ls_store.create_file(conn, store, name="съёмка.las", meta={"format": "las"})
        layer_id = lc.commit_cloud(conn, store, staged, file_id=file_id, name="Съёмка", source={"kind": "las"})
    with dialect.read_tx(p.engine) as conn:
        first = lc.open_cloud(conn, store, layer_id)
    h = np.linspace(-1, 30, first.count).astype(np.float32)
    h[::7] = np.nan
    values = lc.stage_values(store, first, {"class": np.full(first.count, 2, dtype=np.uint8), "h": h})
    with dialect.write_tx(p.engine) as conn:
        lc.replace_values(conn, store, first, values, ground={"params": {"cr": 1.0}})
    with dialect.read_tx(p.engine) as conn:
        cloud = lc.open_cloud(conn, store, layer_id)
    columns = {role: cloud.read(role).tobytes() for role in ("xyz", "seq", "class", "ret", "h")}

    job = export(pclient, tmp_path / "облако.bfggis")
    r = pclient.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    ids = {int(k): v for k, v in job["result"]["id_maps"]["objects"].items()}
    assert set(ids) == {file_id, layer_id}
    pclient.post(f"{API}/projects/{job['result']['uid']}/open")
    q = projects_of(pclient.core).require_current()
    assert q.uid != p.uid
    qstore = BlobStore(q.folder)
    with dialect.read_tx(q.engine) as conn:
        back = lc.open_cloud(conn, qstore, ids[layer_id])
        roles = sorted(
            conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == ids[layer_id])).scalars()
        )
    assert roles == ["class", "h", "ret", "seq", "xyz"]
    assert (back.file_id, back.name, back.version, back.count) == (ids[file_id], "Съёмка", cloud.version, 40_000)
    assert back.parts == cloud.parts and back.bbox == cloud.bbox and back.classes == {2: 40_000}
    assert back.meta == cloud.meta and back.meta["ground"] == {"params": {"cr": 1.0}}
    assert {role: back.read(role).tobytes() for role in columns} == columns
    assert len(back.parts) > 4 and back.shas == cloud.shas  # the same files under the same SHA-256
