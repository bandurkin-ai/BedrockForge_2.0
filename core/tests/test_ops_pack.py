"""The wireframes of a result of an operation in packs (10.10; ADR 0015 §8, `ops/job.py::_write`): «На части» of one
wireframe of 1 000 small boxes and a big relief gives 1 001 wireframes in a few blobs — the small ones entries of
one pack `pack:<n>`, the big one with blobs of its own — and everything that reads a wireframe sees the geometry of
its part bit for bit: the parts of the 3D view and their levels, the click, the section, the export, the delete
with the collection of the storage, a `.bfggis` cycle. A cancel while the files are written and a failure inside
the write transaction leave neither wireframes nor temporary files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.formats.registry import read_objects
from bf_core.jobs.context import JobCancelled
from bf_core.ops import job as ops_job
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.storage.gc import collect
from bf_core.viz import binary
from bf_core.wireframes import files as wf_files
from bf_core.wireframes import store as ws
from tests.meshutil import grid
from tests.test_archive import export
from tests.test_import_job import API, wait_job
from tests.test_ops_job import Ctx, checked, nothing_left, run_here, wireframe_names
from tests.test_wireframe_pack import boxes, corners, roles_of

COUNT = 1000
F32 = 2.5e-4


def blob_files(project: Any) -> list[Path]:
    root = BlobStore(project.folder).root
    return [p for p in root.rglob("*") if p.is_file() and ".tmp" not in p.relative_to(root).parts]


@pytest.fixture()
def split(pclient):  # type: ignore[no-untyped-def]
    """A project with one wireframe: 1 000 closed boxes (12–108 triangles, UTM, awkward doubles) and a relief of
    120 000 triangles (4.3 MB of arrays: over the limit of a pack). → (client, project, source id, given parts)."""
    r = pclient.post(f"{API}/projects", json={"name": "На части"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    relief = grid(300, 200, lambda x, y: np.sin(x / 9.0) * y / 50.0, offset=(503_000.25, 6_998_000.5, -900.0))
    given = [(m.xyz, m.tri) for m in boxes(COUNT, seed=29)] + [relief]
    shift = np.cumsum([0] + [len(v) for v, _t in given[:-1]])
    v = np.concatenate([g[0] for g in given])
    t = np.concatenate([g[1] + s for g, s in zip(given, shift, strict=True)]).astype(np.int32)
    with dialect.write_tx(project.engine) as conn:
        src = ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Выработки")
    return pclient, project, src, given


def run_split(project: Any, src: int) -> tuple[list[int], Ctx]:
    out, ctx = run_here(project, "split", {"wireframe_id": src, "max_parts": COUNT + 1})
    ids = [int(i) for i in out["wireframe_ids"]]
    assert len(ids) == COUNT + 1 and out["report"]["parts"] == COUNT + 1
    return ids, ctx


def by_corners(given: list[tuple[np.ndarray, np.ndarray]]) -> dict[frozenset[bytes], int]:
    return {frozenset(corners(v, t)): k for k, (v, t) in enumerate(given)}


def test_1001_parts_are_a_pack_and_a_big_one_and_read_back_bit_for_bit(split) -> None:  # type: ignore[no-untyped-def]
    c, project, src, given = split
    before = len(blob_files(project))
    ids, ctx = run_split(project, src)
    files = blob_files(project)
    assert before == 2 and len(files) <= 10, len(files)  # was 2 a part: ~2 000 blobs
    roles = roles_of(project.engine, ids)
    big, small = ids[0], ids[1:]  # the part with most triangles first
    assert set(roles[big]) == {"xyz", "tri"}
    assert all(len(r) == 1 and wf_files.is_pack(next(iter(r))) for i, r in roles.items() if i != big)
    entries = {next(iter(roles[i].items())) for i in small}
    assert len(entries) == COUNT and len({sha for _role, sha in entries}) == 1  # 2.6 MB of boxes: one pack
    assert len(files) == before + 2 + 1
    index = by_corners(given)
    store = BlobStore(project.folder)
    seen = set()
    with dialect.read_tx(project.engine) as conn:
        for wf_id in ids:
            v, t = ws.read_wireframe(conn, store, wf_id)
            k = index.get(frozenset(corners(v, t)))
            assert k is not None, wf_id  # the triangles of one given part, bit for bit
            seen.add(k)
            info = ws.wireframe_info(conn, wf_id)
            assert info["built"]["op"] == "split" and info["built"]["sources"][0]["id"] == src
            if k < COUNT:  # a box: closed, its volume
                gv, gt = given[k]
                assert info["stats"]["closed"] and info["stats"]["volume"] == pytest.approx(
                    np.prod(gv.max(axis=0) - gv.min(axis=0)), rel=1e-9
                )
    assert seen == set(range(COUNT + 1))
    names = wireframe_names(c)
    assert len(set(names)) == len(names) and "Выработки — часть 1001" in names
    made = [text for _level, text in ctx.logs if text.startswith("Создан")]
    assert len(made) == ops_job.LOGGED + 1 and made[-1].startswith("Созданы ещё каркасы: 981")


def test_parts_levels_pick_and_section_of_the_packed_parts(split) -> None:  # type: ignore[no-untyped-def]
    c, project, src, given = split
    ids, _ctx = run_split(project, src)
    index = by_corners(given)
    store = BlobStore(project.folder)
    for wf_id in [ids[1], ids[2], ids[500], ids[-1]]:
        with dialect.read_tx(project.engine) as conn:
            m_v, m_t = given[index[frozenset(corners(*ws.read_wireframe(conn, store, wf_id)))]]
        head = binary.decode(c.get(f"{API}/viz/wireframes/{wf_id}").content)[0]
        (p,) = head["parts"]
        r = c.get(f"{API}/viz/wireframes/{wf_id}/parts/0", params={"lod": 0})
        ph, pa = binary.decode(r.content)
        assert ph["triangles"] == p["n"] == len(m_t) and r.headers["etag"] == p["etags"][0]
        world = pa["xyz"].reshape(-1, 3).astype(np.float64) + np.array(ph["origin"])
        assert np.abs(np.sort(world, axis=0) - np.sort(m_v, axis=0)).max() <= F32  # its vertices, in float32
        lo, hi = m_v.min(axis=0), m_v.max(axis=0)
        centre = (lo + hi) / 2.0
        body = {"origin": [float(centre[0]), float(centre[1]), float(hi[2]) + 100.0], "direction": [0.0, 0.0, -1.0]}
        hit = c.post(f"{API}/wireframes/pick", json={**body, "wireframe_ids": [wf_id]}).json()["hit"]
        assert hit["wireframe_id"] == wf_id and abs(hit["point"][2] - hi[2]) <= 1e-9
        plane = {"point": ",".join(repr(float(x)) for x in centre), "normal": "1,0,0"}
        r = c.get(f"{API}/viz/wireframes/{wf_id}/section", params=plane)
        assert r.status_code == 200, r.text
        cut = binary.decode(r.content)[1]
        assert len(cut["closed"]) == 1 and bool(cut["closed"].all())  # one closed loop around the box
    r = c.get(f"{API}/viz/wireframes/{ids[0]}/parts/0", params={"lod": 0})  # the big one: blobs of its own
    assert r.status_code == 200 and binary.decode(r.content)[0]["triangles"] > 0


def test_export_delete_collect_and_bfggis_of_the_packed_parts(split, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, project, src, given = split
    ids, _ctx = run_split(project, src)
    index = by_corners(given)
    store = BlobStore(project.folder)
    out = tmp_path / "части.obj"
    started = c.post(f"{API}/wireframes/export", json={"path": str(out), "format": "obj", "wireframe_ids": ids[:600]})
    assert started.status_code == 200, started.text
    job = wait_job(c, started.json()["source"], started.json()["id"], timeout=120)
    assert job["status"] == "done", job
    with dialect.read_tx(project.engine) as conn:
        want = [index[frozenset(corners(*ws.read_wireframe(conn, store, i)))] for i in ids[:600]]
    written = list(read_objects(out).meshes)
    assert [index.get(frozenset(corners(m.xyz, m.tri))) for m in written] == want  # in the order asked

    roles = roles_of(project.engine, ids)
    pack_sha = next(iter(roles[ids[1]].values()))
    with dialect.write_tx(project.engine) as conn:  # all of the pack but one: it stays for that one
        assert not any(ws.delete_wireframe(conn, wf_id)["shared"] for wf_id in ids[1:-1])
    collect(project.engine, store, now=1e12)
    assert store.exists(pack_sha)
    with dialect.read_tx(project.engine) as conn:
        assert frozenset(corners(*ws.read_wireframe(conn, store, ids[-1]))) in index
    left = [ids[0], ids[-1]]

    job = export(c, tmp_path / "части.bfggis")
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"], timeout=120)
    assert job["status"] == "done", job
    new = {int(k): v for k, v in job["result"]["id_maps"]["objects"].items()}
    assert c.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    copy = projects_of(c.core).require_current()
    assert roles_of(copy.engine, [new[i] for i in left]) == {new[i]: roles[i] for i in left}  # same blobs, entry
    with dialect.read_tx(copy.engine) as conn:
        for wf_id in left:
            v, t = ws.read_wireframe(conn, BlobStore(copy.folder), new[wf_id])
            with dialect.read_tx(project.engine) as old:
                assert corners(v, t) == corners(*ws.read_wireframe(old, store, wf_id))
            assert ws.wireframe_info(conn, new[wf_id])["built"]["op"] == "split"

    assert c.post(f"{API}/projects/{project.uid}/open").status_code == 200
    with dialect.write_tx(project.engine) as conn:
        assert not ws.delete_wireframe(conn, ids[-1])["shared"]
    collect(project.engine, store, now=1e12)
    assert not store.exists(pack_sha)  # the last of its wireframes is gone: the pack goes with the collection


class Late(Ctx):
    """Cancelled at the tick of `stop_at` from `after` of the run on: «Запись каркаса» of `stage_files` (0.9–0.92),
    not that of the big part, which writes its own blobs first."""

    after = 0.0

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        self.seen.append(str(message))
        if message == self.stop_at and (progress or 0.0) >= self.after:
            raise JobCancelled()


@pytest.mark.parametrize(
    ("stage", "after"), [("Запись каркаса", 0.0), ("Запись каркаса", 0.91), ("Сохранение результата в проект", 0.0)]
)
def test_a_cancel_while_the_files_are_written_leaves_nothing(split, stage: str, after: float) -> None:  # type: ignore[no-untyped-def]
    c, project, src, _given = split
    before = wireframe_names(c)
    files = len(blob_files(project))
    ctx = Late(project, ops_job.job_params(checked(project, "split", {"wireframe_id": src, "max_parts": COUNT + 1})),
               stage)  # fmt: skip
    ctx.after = after
    try:
        with pytest.raises(JobCancelled):
            ops_job.run(ctx)
    finally:
        ctx.close()
    assert stage in ctx.seen and "Готово" not in ctx.seen
    nothing_left(c, project, before)
    assert len(blob_files(project)) == files


def test_a_failure_inside_the_write_transaction_leaves_nothing(split, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, project, src, _given = split
    before = wireframe_names(c)
    calls: list[int] = []
    real = ws.commit_wireframe

    def fails(*a: Any, **kw: Any) -> int:
        calls.append(1)
        if len(calls) == 700:  # the big part and the pack are in place by now
            raise RuntimeError("диск переполнен")
        return real(*a, **kw)

    monkeypatch.setattr(ws, "commit_wireframe", fails)
    with pytest.raises(RuntimeError):
        run_here(project, "split", {"wireframe_id": src, "max_parts": COUNT + 1})
    nothing_left(c, project, before)  # the files moved into place have no rows: the collection removes them
    store = BlobStore(project.folder)
    collect(project.engine, store, now=1e12)
    assert len(blob_files(project)) == 2
