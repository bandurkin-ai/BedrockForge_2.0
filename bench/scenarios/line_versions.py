"""Versions of a line layer in the editor (ТЗ 7.22; ADR 0015 §1, «Замеры»: 100 thousand / 1 million vertices —
ориентир ≤ 0,5 s, ≤ 1 s, ≤ 3 s, ≤ 0,2 s on full; bit mismatches 0).

A layer of a project database (SQLite, blobs) with `vertices` vertices in lines of 50–150 (a third closed, the
closing vertex repeated as stored), UTM coordinates with digits a float32 cannot hold, some heights empty (NaN)
and some −0.0. Version 2 — the lines saved into a new layer (prepared). Then, as the endpoints of the editor do
it (`lines/versions.py`, `lines/geometry_io.py`):
  read_s      the exact geometry: the current version from the blobs, the answer BFA1 `linegeom`;
  save_one_s  a save with one line changed (all the others untouched: their bits from the base version);
  rewrite_s   a save with every line changed (moved by 0,5 m);
  restore_s   the return to version 2 (version 5: the blobs of version 2, no file written).
What is checked, bit for bit (NaN and −0.0 too):
  bit_mismatches  vertices that differ from what they must be: after the one-line save — the untouched lines
                  from version 2 and the changed line as sent; after the rewrite — as sent; after the return —
                  version 2; plus the lines whose closedness changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BASE = (512_345.678_901_234, 6_789_012.345_678_901, 250.123_456_789)


def _lines(vertices: int) -> tuple[Any, Any, Any]:
    """(xyz in the storage form, offsets, closed)."""
    import numpy as np

    rng = np.random.default_rng(31)
    counts, total = [], 0
    while total < vertices:
        k = int(min(rng.integers(50, 151), vertices - total))
        counts.append(max(k, 2))
        total += counts[-1]
    closed = (rng.random(len(counts)) < 1 / 3) & (np.array(counts) >= 4)
    parts = []
    for number, (k, c) in enumerate(zip(counts, closed, strict=True)):
        p = np.asarray(BASE) + rng.normal(0.0, 1.0, (k, 3)).cumsum(axis=0) * [3.0, 3.0, 0.5] + rng.normal(0, 3e3, 3)
        empty = rng.random(k) < 0.02  # an empty height is not a zero
        empty[0] = False  # the closing vertex repeats the first one, compared as numbers
        p[empty, 2] = np.nan
        if number % 100 == 0:
            p[k // 2, 2] = -0.0
        if c:
            p[-1] = p[0]
        parts.append(p)
    xyz = np.vstack(parts)
    offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    return xyz, offsets, closed.astype(np.uint8)


def _edit(src: Any, touched: Any, closed: Any, offsets: Any, xyz: Any) -> Any:
    import numpy as np

    from bf_core.lines.assemble import Edit

    counts = np.diff(offsets)
    sent = np.repeat(touched.astype(bool) | (src < 0), counts)
    return Edit(src, touched, closed, counts, xyz[sent])


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.lines import versions
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore

    xyz, offsets, closed = _lines(int(params["vertices"]))
    np.savez(work / "lines.npz", xyz=xyz, offsets=offsets, closed=closed)
    folder = create_folder(work, "Версии слоя", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        bs = BlobStore(folder)
        with dialect.write_tx(engine) as conn:
            layer_id = versions.create_layer(conn, bs, name="РУДА", color="#ff8800")
        n = len(closed)
        body = _edit(np.full(n, -1), np.ones(n, dtype=np.uint8), closed, offsets, xyz)
        assert versions.save(engine, bs, layer_id, body, base_version=1, note="линии") == 2
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(f"{folder}\n{layer_id}", encoding="utf-8")


def load() -> None:
    import bf_core.lines.geometry_io  # noqa: F401
    import bf_core.lines.versions  # noqa: F401


def _mismatches(ls: Any, xyz: Any, offsets: Any, closed: Any) -> int:
    import numpy as np

    from bf_core.lines.assemble import as_lines

    got_offsets, got_closed = as_lines(ls)
    if len(ls.xyz) != len(xyz) or not np.array_equal(got_offsets, offsets):
        return max(len(ls.xyz), len(xyz))
    bad = (ls.xyz.view(np.uint64) != np.ascontiguousarray(xyz).view(np.uint64)).any(axis=1)
    return int(bad.sum()) + int(np.count_nonzero(np.asarray(got_closed, dtype=np.uint8) != closed))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np

    from bf_core.db import dialect
    from bf_core.lines import geometry_io, versions
    from bf_core.storage.blobs import BlobStore

    folder_text, layer_text = (work / "folder.txt").read_text(encoding="utf-8").split("\n")
    folder, layer_id = Path(folder_text), int(layer_text)
    data = np.load(work / "lines.npz")
    xyz, offsets, closed = data["xyz"], data["offsets"], data["closed"]
    n = len(closed)
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    bs = BlobStore(folder)
    out: dict[str, Any] = {"vertices": int(len(xyz)), "lines": n}
    try:
        t0 = time.perf_counter()
        with dialect.read_tx(engine) as conn:
            head, ls, lid = versions.read_current(conn, bs, layer_id)
        answer = geometry_io.encode_geometry(layer_id, int(head["rev"]), ls, lid, None)
        out["read_s"] = round(time.perf_counter() - t0, 4)
        out["answer_mb"] = round(len(answer) / 2**20, 2)
        bad = _mismatches(ls, xyz, offsets, closed)

        k = n // 2  # one line changed: its vertices lifted by 1 m
        one = xyz.copy()
        one[offsets[k] : offsets[k + 1], 2] += 1.0
        touched = np.zeros(n, dtype=np.uint8)
        touched[k] = 1
        t0 = time.perf_counter()
        v3 = versions.save(engine, bs, layer_id, _edit(lid, touched, closed, offsets, one), base_version=2)
        out["save_one_s"] = round(time.perf_counter() - t0, 4)
        with dialect.read_tx(engine) as conn:
            bad += _mismatches(versions.read_current(conn, bs, layer_id)[1], one, offsets, closed)

        moved = xyz + [0.5, 0.5, 0.0]
        every = _edit(lid, np.ones(n, np.uint8), closed, offsets, moved)
        t0 = time.perf_counter()
        v4 = versions.save(engine, bs, layer_id, every, base_version=v3)
        out["rewrite_s"] = round(time.perf_counter() - t0, 4)
        with dialect.read_tx(engine) as conn:
            bad += _mismatches(versions.read_current(conn, bs, layer_id)[1], moved, offsets, closed)

        t0 = time.perf_counter()
        v5 = versions.restore(engine, bs, layer_id, 2, expected_version=v4)
        out["restore_s"] = round(time.perf_counter() - t0, 4)
        with dialect.read_tx(engine) as conn:
            bad += _mismatches(versions.read_current(conn, bs, layer_id)[1], xyz, offsets, closed)
        out["versions"] = [v3, v4, v5]
        out["bit_mismatches"] = int(bad)
    finally:
        engine.dispose()
    return out
