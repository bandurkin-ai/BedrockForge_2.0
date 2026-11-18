"""Packs of small line layers (lines/pack.py, lines/store.py::stage_layers; ADR 0011 «Замеры», debt of 7.30).

A file of many small layers used to cost five blobs a layer. Small layers of one portion now share one blob
(`pack:<n>`); a big layer keeps its own blobs (`xyz`, `offsets`, …). What is checked: the container itself, the
geometry and attributes bit for bit whichever way a layer is stored, the same 3D parts, attribute pages and
exported files, deleting with the collector, cancelling, the archive, and `import.objects` of a file of many
layers."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, blobs, objects, valid_blob_role
from bf_core.formats.lineset import LineSet, from_lines, from_points
from bf_core.lines import pack
from bf_core.lines import store as ls_store
from bf_core.storage.blobs import BlobStore
from bf_core.storage.gc import STRAY_GRACE_S, collect
from bf_core.viz import binary
from tests.dbutil import make_engine
from tests.test_import_job import API

pytestmark = pytest.mark.windows

UTM = np.array([512_345.678_901_234, 6_234_567.123_456_789, 412.345_678_9])


def _square(z: float = 100.0, n: float = 10.0) -> np.ndarray:
    return UTM + np.array([[0, 0, z], [n, 0, z], [n, n, z], [0, n, z]], dtype=np.float64)


def hard_layers() -> list[LineSet]:
    """Small layers of every kind the storage knows, and one too big for a pack."""
    rng = np.random.default_rng(11)
    lines = [UTM + rng.normal(0, 500, (k, 3)) for k in (2, 17, 1, 301)]
    lines[1][3, 2] = math.nan  # a vertex without a height: never 0
    lines[3][0] = [-0.0, 7_000_000.000_000_001, -0.0]
    n = sum(len(x) for x in lines) + 4
    mixed = from_lines(
        "Горизонт +400",
        [*lines, _square()],
        closed=[False, False, False, False, True],
        attrs={"STRING": ["1", "1", "2", "2", "3"], "AU": [1.0, None, 2.5, math.nan, 3.0]},
        vattrs={"M": rng.normal(size=n)},
        seq=np.arange(n)[::-1],
        color="#FF8800",
        units={"AU": "г/т"},
    )
    bare = from_lines("без атрибутов", [_square(5.0), _square(7.0)[:3]])  # 7 vertices: odd sizes, padding
    points = from_points("Пикеты", UTM + rng.uniform(0, 300, (333, 3)), attrs={"text": [f"ПК{i}" for i in range(333)]})
    lone = from_points("Репер", UTM + [[0.0, 0.0, math.nan]])
    big_xyz = UTM + rng.normal(0, 2000, (60_000, 3))  # 1.44 MB of float64: above the limit of a pack
    big = from_lines("Большой", [big_xyz[:40_000], big_xyz[40_000:]], attrs={"STRING": ["a", "b"]})
    return [mixed, bare, points, lone, big]


def same_layer(a: LineSet, b: LineSet) -> None:
    assert a.name == b.name and a.points_only == b.points_only
    assert a.xyz.dtype == np.float64 and a.xyz.tobytes() == b.xyz.tobytes()  # NaN and -0.0 bit for bit
    for one, two, dtype in ((a.offsets, b.offsets, "<i8"), (a.closed, b.closed, "u1"), (a.seq, b.seq, "<u4")):
        assert (one is None) == (two is None)
        if one is not None and two is not None:
            assert one.dtype == np.dtype(dtype) and one.tobytes() == np.asarray(two, dtype=dtype).tobytes()
    for one, two in ((a.attrs, b.attrs), (a.vattrs, b.vattrs)):
        assert (one is None) == (two is None)
        if one is not None:
            assert one.schema == two.schema and one.num_rows == two.num_rows
            for name in one.column_names:
                x, y = one.column(name).to_pylist(), two.column(name).to_pylist()
                assert len(x) == len(y)
                for p, q in zip(x, y, strict=True):
                    assert (p == q) or (
                        isinstance(p, float) and isinstance(q, float) and math.isnan(p) == math.isnan(q)
                    )
    assert a.color == b.color and a.units == b.units


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    yield engine, BlobStore(tmp_path)
    engine.dispose()


def _put_packed(
    engine: sa.Engine, store: BlobStore, layers: list[LineSet], name: str = "слои.str"
) -> tuple[int, list[int]]:
    staged = ls_store.stage_layers(store, layers)
    try:
        with dialect.write_tx(engine) as conn:
            file_id = ls_store.create_file(conn, store, name=name, meta={"format": "micromine_str"})
            return file_id, ls_store.commit_layers(conn, store, staged, file_id=file_id)
    finally:
        for part in staged:
            part.discard()


def _blob_files(store: BlobStore) -> list[Path]:
    return sorted(p for p in store.root.glob("??/??/*") if p.is_file())


# --- the container ---------------------------------------------------------------------------------------


def test_pack_container_is_aligned_and_refuses_damage(tmp_path: Path) -> None:
    entries = [
        {"xyz": np.arange(9, dtype="<f8").tobytes(), "closed": b"\x01\x00\x01", "attrs": b"PAR1-not-really"},
        {"xyz": np.arange(3, dtype="<f8").tobytes(), "seq": np.arange(1, dtype="<u4").tobytes()},
        {"xyz": b""},  # an entry may hold an empty array
    ]
    data = pack.encode(entries)
    path = tmp_path / "pack.bin"
    path.write_bytes(data)
    found = pack.directory(path)
    assert len(found) == 3 and [sorted(e) for e in found] == [["attrs", "closed", "xyz"], ["seq", "xyz"], ["xyz"]]
    for entry, slices in zip(entries, found, strict=True):
        for role, (offset, size) in slices.items():
            assert offset % 8 == 0 and data[offset : offset + size] == entry[role]
    assert np.fromfile(path, dtype="<f8", count=9, offset=found[0]["xyz"][0]).tolist() == list(range(9))
    assert pack.entry_of("pack:17") == 17 and pack.role(17) == "pack:17" and valid_blob_role(pack.role(1999))
    assert pack.entry_of("xyz") is None and pack.entry_of("pack:x") is None and pack.entry_of("pack:-1") is None

    for bad in (b"", data[:6], b"XXXX" + data[4:], data[:40], data[: len(data) - 1]):
        broken = tmp_path / f"bad-{len(bad)}-{bad[:4].hex()}.bin"
        broken.write_bytes(bad)
        with pytest.raises(pack.PackError):
            pack.directory(broken)


# --- storage ---------------------------------------------------------------------------------------------


def test_small_layers_share_one_blob_and_read_back_bit_exact(project) -> None:  # type: ignore[no-untyped-def]
    engine, store = project
    layers = hard_layers()
    file_id, ids = _put_packed(engine, store, layers)
    assert not list(store.tmp_dir.iterdir())
    with dialect.read_tx(engine) as conn:
        assert ls_store.layer_ids(conn, file_id) == ids  # the order of the file
        roles = [ls_store.layer_blobs(conn, i) for i in ids]
        back = [ls_store.read_layer(conn, store, i) for i in ids]
        infos = [ls_store.layer_info(conn, i) for i in ids]
        extent = conn.execute(sa.select(objects.c.extent).where(objects.c.id == file_id)).scalar_one()
        sizes = dict(conn.execute(sa.select(blobs.c.sha256, blobs.c.size)).all())
    for layer, got in zip(layers, back, strict=True):
        same_layer(got, layer)
    # four small layers: one blob, the role says which entry of it a layer is
    assert [list(r) for r in roles[:4]] == [["pack:0"], ["pack:1"], ["pack:2"], ["pack:3"]]
    assert len({next(iter(r.values())) for r in roles[:4]}) == 1 and all(valid_blob_role(k) for r in roles for k in r)
    # the big one is stored as before: a reader of the old layout needs nothing new
    assert set(roles[4]) == {"xyz", "offsets", "closed", "seq", "attrs"}
    assert len(_blob_files(store)) == 1 + 5 and len(sizes) == 6
    for path in _blob_files(store):
        assert sizes[path.name] == path.stat().st_size
    for layer, info in zip(layers, infos, strict=True):
        st = layer.stats()
        assert (info["line_count"], info["point_count"], info["length"]) == (
            st["line_count"],
            st["point_count"],
            st["length"],
        )
        assert info["origin"] == layer.origin() and info["points_only"] == layer.points_only
    lo = np.nanmin(np.vstack([x.xyz for x in layers]), axis=0)
    hi = np.nanmax(np.vstack([x.xyz for x in layers]), axis=0)
    assert extent == [*lo.tolist(), *hi.tolist()]


def test_limit_decides_what_is_packed(project) -> None:  # type: ignore[no-untyped-def]
    engine, store = project
    layers = [from_lines(f"L{i}", [_square(float(i))]) for i in range(3)]
    size = 4 * 24 + 2 * 8 + 1 + 4 * 4  # xyz, offsets, closed, seq of a square
    for limit, packed in ((size, True), (size - 1, False)):
        staged = ls_store.stage_layers(store, layers, pack_limit=limit)
        assert [s.packed for s in staged] == [packed] * 3
        for s in staged:
            s.discard()
        assert not list(store.tmp_dir.iterdir())
    # the limit 0 packs nothing, the single-layer call of the old interface neither
    one = ls_store.stage_layers(store, [from_points("p", UTM)], pack_limit=0)
    assert not one[0].packed and set(one[0].files) == {"xyz", "seq"}
    one[0].discard()
    alone = ls_store.stage_layer(store, layers[0])
    assert not alone.packed and set(alone.files) == {"xyz", "offsets", "closed", "seq"}
    alone.discard()
    assert not list(store.tmp_dir.iterdir())


def test_cancel_while_staging_leaves_no_temp_files(project) -> None:  # type: ignore[no-untyped-def]
    _engine, store = project
    layers = hard_layers()
    ticks = 0

    class Stop(Exception):
        pass

    def tick(_layer: int) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == stop_at:
            raise Stop

    total = None
    for stop_at in (10**9, 1, 2, 3, 5):
        ticks = 0
        if total is None:
            for s in ls_store.stage_layers(store, layers, tick):
                s.discard()
            total = ticks
            assert total >= len(layers)  # at least once a layer: a job can always be stopped
        else:
            with pytest.raises(Stop):
                ls_store.stage_layers(store, layers, tick)
        assert not list(store.tmp_dir.iterdir()), stop_at


def test_failed_commit_and_delete_leave_nothing_after_collect(project) -> None:  # type: ignore[no-untyped-def]
    engine, store = project
    layers = hard_layers()[:4]
    staged = ls_store.stage_layers(store, layers)
    with pytest.raises(RuntimeError), dialect.write_tx(engine) as conn:
        file_id = ls_store.create_file(conn, store, name="a.str", meta={})
        ls_store.commit_layers(conn, store, staged, file_id=file_id)
        raise RuntimeError("the transaction fails after the pack is in place")
    for s in staged:
        s.discard()
    assert not list(store.tmp_dir.iterdir())
    with dialect.read_tx(engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(objects)).scalar_one() == 0
        assert conn.execute(sa.select(sa.func.count()).select_from(blob_owners)).scalar_one() == 0
    import time

    assert collect(engine, store, now=time.time() + STRAY_GRACE_S + 1).stray_files == 1  # the pack without a row
    assert _blob_files(store) == []

    file_id, ids = _put_packed(engine, store, layers)
    other, kept = _put_packed(engine, store, [from_lines("остаётся", [_square(1.0)])], "b.str")
    with dialect.write_tx(engine) as conn:
        assert sorted(ls_store.delete_file(conn, file_id)) == sorted([file_id, *ids])
    report = collect(engine, store)
    assert report.blobs == 1 and len(_blob_files(store)) == 1
    with dialect.read_tx(engine) as conn:
        same_layer(ls_store.read_layer(conn, store, kept[0]), from_lines("остаётся", [_square(1.0)]))
        with pytest.raises(Exception) as err:
            ls_store.read_layer(conn, store, ids[0])
        assert getattr(err.value, "status", None) == 404


def test_broken_pack_is_reported_not_guessed(project) -> None:  # type: ignore[no-untyped-def]
    engine, store = project
    _file, ids = _put_packed(engine, store, hard_layers()[:2])
    (path,) = _blob_files(store)
    path.write_bytes(path.read_bytes()[:64])
    pack.forget()
    with dialect.read_tx(engine) as conn, pytest.raises(Exception) as err:
        ls_store.read_layer(conn, store, ids[1])
    assert getattr(err.value, "code", None) == "line_layer_broken" and "заново" in err.value.hint  # type: ignore[attr-defined]


# --- the same answers whichever way a layer is stored ----------------------------------------------------


def _view(c: Any, layer_id: int) -> tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, bytes]]]]:
    r = c.get(f"{API}/viz/lines/{layer_id}")
    assert r.status_code == 200, r.text
    head, _ = binary.decode(r.content)
    parts = []
    for part in head["parts"]:
        for lod in range(len(head["lods"])):
            pr = c.get(f"{API}/viz/lines/{layer_id}/parts/{part['k']}", params={"lod": lod})
            assert pr.status_code == 200, pr.text
            h, arrays = binary.decode(pr.content)
            parts.append((h, {k: v.tobytes() for k, v in arrays.items()}))
    return head, parts


def _strip(head: dict[str, Any]) -> dict[str, Any]:
    """A header without what names the layer object itself (two copies of a layer differ only there)."""
    out = {k: v for k, v in head.items() if k not in ("layer", "arrays")}
    if isinstance(head.get("layer"), dict):
        out["layer"] = {k: v for k, v in head["layer"].items() if k not in ("id", "file_id")}
    if "parts" in out:
        out["parts"] = [{k: v for k, v in p.items() if k != "etags"} for p in out["parts"]]
    return out


def test_packed_and_plain_layers_answer_alike(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """3D parts at every level, attribute pages and exported files do not depend on how a layer is stored."""
    from bf_core.projects.service import projects_of
    from tests.test_lines_export import Project, read_objects

    proj = Project(pclient, "Пакеты")
    project = projects_of(pclient.core).require_current()
    store = BlobStore(project.folder)
    layers = hard_layers()[:4]
    with dialect.write_tx(project.engine) as conn:
        plain_file = ls_store.create_file(conn, store, name="plain.str", meta={"format": "micromine_str"})
        plain = [
            ls_store.put_layer(conn, store, x, file_id=plain_file, sort_key=float(k)) for k, x in enumerate(layers)
        ]
    packed_file, packed = _put_packed(project.engine, store, layers, "packed.str")
    with dialect.read_tx(project.engine) as conn:
        assert all(set(ls_store.layer_blobs(conn, i)) <= set(ls_store.ARRAYS) | set(ls_store.TABLES) for i in plain)
        assert all(len(ls_store.layer_blobs(conn, i)) == 1 for i in packed)

    for a, b in zip(plain, packed, strict=True):
        head_a, parts_a = _view(pclient, a)
        head_b, parts_b = _view(pclient, b)
        assert _strip(head_a) == _strip(head_b) and len(parts_a) == len(parts_b) > 0
        for (ha, aa), (hb, ab) in zip(parts_a, parts_b, strict=True):
            assert _strip(ha) == _strip(hb) and aa == ab
        for scope in ("feature", "vertex"):
            pa_, pb_ = (
                pclient.get(f"{API}/lines/layers/{i}/attrs", params={"scope": scope, "offset": 1, "limit": 50})
                for i in (a, b)
            )
            assert pa_.status_code == pb_.status_code == 200
            ja, jb = pa_.json(), pb_.json()
            assert {k: v for k, v in ja.items() if k != "layer_id"} == {k: v for k, v in jb.items() if k != "layer_id"}
            # the page of a packed layer is cached by its ETag as any other
            again = pclient.get(
                f"{API}/lines/layers/{b}/attrs",
                params={"scope": scope, "offset": 1, "limit": 50},
                headers={"If-None-Match": pb_.headers["etag"]},
            )
            assert again.status_code == 304
    assert ja["rows"] == [] and ja["total"] == 1  # the lone point has no vertex attributes: an empty page

    out = tmp_path / "выгрузка"
    out.mkdir()
    for fmt, suffix in (("str", ".str"), ("dxf", ".dxf"), ("csv", ".csv")):
        files = []
        for name, file_id in (("plain", plain_file), ("packed", packed_file)):
            target = out / f"{name}{suffix}"
            result, _sent = proj.run(path=str(target), format=fmt, file_id=file_id)
            files.append(Path(result["path"]))
        if fmt == "dxf":  # the writer stamps a DXF with new GUIDs and the time: compare what a reader gets
            one, two = (read_objects(f).layers for f in files)
            assert len(one) == len(two) == 4
            for x, y in zip(one, two, strict=True):
                same_layer(x, y)
        else:
            assert files[0].read_bytes() == files[1].read_bytes() and files[0].stat().st_size > 1000, fmt


def test_archive_keeps_packed_layers(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.projects.service import projects_of
    from tests.test_import_job import wait_job

    r = pclient.post(f"{API}/projects", json={"name": "Архив пакетов"})
    assert r.status_code == 200, r.text
    p = projects_of(pclient.core).require_current()
    layers = hard_layers()
    file_id, ids = _put_packed(p.engine, BlobStore(p.folder), layers)
    r = pclient.post(f"{API}/projects/export", json={"path": str(tmp_path / "packs.bfggis"), "password": None})
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    r = pclient.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    new = {int(k): v for k, v in job["result"]["id_maps"]["objects"].items()}
    pclient.post(f"{API}/projects/{job['result']['uid']}/open")
    q = projects_of(pclient.core).require_current()
    assert q.uid != p.uid
    with dialect.read_tx(q.engine) as conn:
        assert ls_store.layer_ids(conn, new[file_id]) == [new[i] for i in ids]
        for layer, layer_id in zip(layers, ids, strict=True):
            same_layer(ls_store.read_layer(conn, BlobStore(q.folder), new[layer_id]), layer)


# --- import.objects --------------------------------------------------------------------------------------


def _many_layers(path: Path, layers: int, lines: int = 2, vertices: int = 5) -> list[LineSet]:
    from bf_core.formats.str_micromine import write_str

    rng = np.random.default_rng(layers)
    sets = []
    for i in range(layers):
        xyz = np.round(UTM + rng.uniform(0, 900, (lines * vertices, 3)), 3)
        if i % 7 == 3:
            xyz[1, 2] = math.nan
        offsets = np.arange(0, lines * vertices + 1, vertices, dtype=np.int64)
        seq = np.arange(lines * vertices, dtype=np.uint32)
        sets.append(LineSet(f"S{i:04d}", xyz, offsets, np.zeros(lines, dtype=np.uint8), seq))
    write_str(path, sets)
    return sets


def test_import_of_many_layers_packs_them(tmp_path: Path) -> None:
    """A STR of 130 layers: 6 packs instead of 650 blobs, every layer as the reader gave it; a cancel between
    the portions leaves nothing."""
    from bf_core.formats.str_micromine import read_str
    from bf_core.importing.objects import PORTION
    from bf_core.jobs.context import JobCancelled
    from bf_core.projects.project import create_folder
    from tests.test_import_objects import run_job

    src = tmp_path / "many.str"
    _many_layers(src, 130)
    expected = [layer for layer in read_str(src).layers if layer.point_count]
    assert len(expected) == 130

    folder = create_folder(tmp_path / "projects", "Много слоёв", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    store = BlobStore(folder)
    try:
        ctx = run_job(folder, {"files": [str(src)]})
        result = ctx.result  # type: ignore[attr-defined]
        assert result["layers"] == 130 and result["vertices"] == 1300 and not result["failed"]
        (file_id,) = result["objects"]
        with dialect.read_tx(engine) as conn:
            ids = ls_store.layer_ids(conn, file_id)
            assert len(ids) == 130
            for layer, layer_id in zip(expected, ids, strict=True):
                got = ls_store.read_layer(conn, store, layer_id)
                assert got.color is not None  # the palette of the import
                layer.color = got.color
                same_layer(got, layer)
            roles = [ls_store.layer_blobs(conn, i) for i in ids]
            packs = {sha for r in roles for sha in r.values()}
            assert all(len(r) == 1 and next(iter(r)).startswith("pack:") for r in roles)
            assert len(packs) == math.ceil(130 / PORTION)
            assert conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar_one() == len(packs) + 1
            states = set(conn.execute(sa.select(objects.c.state)).scalars())
            assert states == {"ready"}
        assert len(_blob_files(store)) == len(packs) + 1 and not list(store.tmp_dir.iterdir())  # + the source

        before = len(_blob_files(store))
        with pytest.raises(JobCancelled):
            run_job(folder, {"files": [str(src)]}, cancel_at=70)  # in the third portion of the layers
        with dialect.read_tx(engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(objects)).scalar_one() == 131
            owners = set(conn.execute(sa.select(blob_owners.c.owner_id)).scalars())
            assert owners <= {file_id, *ids}
        assert not list(store.tmp_dir.iterdir())
        collect(engine, store)
        assert len(_blob_files(store)) == before  # identical packs of the stopped attempt are the same blobs
    finally:
        engine.dispose()
