"""`.bfggis` export → import (ТЗ 7.4): same composition, renumbered ids, password, versions, failures."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.archive.container import ArchiveWriter
from bf_core.archive.spec import SKIPPED, TABLES
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, edit_log, object_refs, objects, project_md, settings
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_section_series_api import series_router  # noqa: F401 — a fixture

pytestmark = pytest.mark.windows
API = "/api/v1"


def test_every_project_table_is_archived_or_skipped() -> None:
    listed = {t.name for t in TABLES} | set(SKIPPED)
    assert set(project_md.tables) - {"alembic_version"} <= listed, set(project_md.tables) - listed


def wait_job(c, key: str, job_id: int, timeout: float = 30) -> dict:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"{API}/jobs/{key}/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(job)


def fill(c) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    p = projects_of(c.core).require_current()
    now = dialect.utcnow()
    base = dict(sort_key=0.0, state="ready", version=1, created_at=now, updated_at=now)
    store = BlobStore(p.folder)
    with dialect.write_tx(p.engine) as conn:
        # gaps in ids: renumbering must not rely on them being 1..n
        conn.execute(sa.insert(objects).values(id=10, kind="folder", name="Папка", name_key="папка", meta={}, **base))
        conn.execute(
            sa.insert(objects).values(
                id=20, parent_id=10, kind="wireframe", name="Тело", name_key="тело", meta={"triangles": 12}, **base
            )
        )
        conn.execute(sa.insert(objects).values(id=30, kind="chart", name="Гр", name_key="гр", meta={}, **base))
        conn.execute(sa.insert(object_refs).values(src_id=30, dst_id=20, role="source"))
        sha = store.put_bytes(
            conn, b"\x00\x01" * 5000, media_type="application/x-npy", owner_kind="object", owner_id=20
        )
        conn.execute(
            sa.insert(edit_log).values(
                scope_kind="object",
                scope_id=20,
                group_id=1,
                action="meta",
                changes={"name": ["Старое", "Тело"]},
                undone=False,
                created_at=now,
            )
        )
        conn.execute(sa.insert(settings).values(scope="view", key="hidden", value=["wf:20"], version=1))
    return {"sha": sha, "uid": p.uid}


def export(c, path: Path, password: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    r = c.post(f"{API}/projects/export", json={"path": str(path), "password": password})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    return job


@pytest.fixture()
def ac(pclient):  # type: ignore[no-untyped-def]
    pclient.post(API + "/projects", json={"name": "Исходный", "description": {"deposit": "Северное"}})
    return pclient


def test_roundtrip_same_composition_and_new_ids(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    src = fill(ac)
    out = tmp_path / "Проект_2026-11-16"
    job = export(ac, out)
    path = Path(job["result"]["path"])
    assert path.suffix == ".bfggis" and not path.with_name(path.name + ".part").exists()
    info = ac.post(f"{API}/projects/import/inspect", json={"path": str(path)}).json()
    assert info["name"] == "Исходный" and info["name_taken"] and info["copy_name"] == "Исходный (2)"
    assert info["summary"]["wireframes"] == 1 and info["encrypted"] is False and info["from_bf"] is False
    r = ac.post(f"{API}/projects/import", json={"path": str(path), "mode": "copy"}).json()
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["name"] == "Исходный (2)" and res["uid"] != src["uid"]
    new_ids = res["id_maps"]["objects"]
    assert set(new_ids) == {"10", "20", "30"}
    opened = ac.post(f"{API}/projects/{res['uid']}/open").json()
    assert opened["summary"]["wireframes"] == 1 and opened["summary"]["charts"] == 1
    assert opened["description"]["deposit"] == "Северное"
    p = projects_of(ac.core).require_current()
    with dialect.read_tx(p.engine) as conn:
        wf = conn.execute(sa.select(objects).where(objects.c.kind == "wireframe")).mappings().one()
        assert wf["id"] == new_ids["20"] and wf["parent_id"] == new_ids["10"] and wf["meta"] == {"triangles": 12}
        ref = conn.execute(sa.select(object_refs)).mappings().one()
        assert (ref["src_id"], ref["dst_id"]) == (new_ids["30"], new_ids["20"])
        own = conn.execute(sa.select(blob_owners)).mappings().one()
        assert own["owner_id"] == new_ids["20"] and own["sha256"] == src["sha"]
        log = conn.execute(sa.select(edit_log)).mappings().one()
        assert log["scope_id"] == new_ids["20"] and log["changes"] == {"name": ["Старое", "Тело"]}
        assert conn.execute(sa.select(settings.c.value)).scalar_one() == [f"wf:{new_ids['20']}"]  # renumbered
    assert BlobStore(p.folder).read_bytes(src["sha"]) == b"\x00\x01" * 5000
    r = ac.get(f"{API}/blobs/{src['sha']}", headers={"Range": "bytes=0-1"})
    assert r.status_code == 206 and r.content == b"\x00\x01"


def test_password_wrong_missing_and_never_stored(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    fill(ac)
    path = Path(export(ac, tmp_path / "secret.bfggis", password="Пароль-42")["result"]["path"])
    info = ac.post(f"{API}/projects/import/inspect", json={"path": str(path)}).json()
    assert info["encrypted"] and info["needs_password"] and "name" not in info
    r = ac.post(f"{API}/projects/import/inspect", json={"path": str(path), "password": "нет"})
    assert r.status_code == 422 and r.json()["code"] == "bad_password" and r.json()["message"] == "Неверный пароль"
    r = ac.post(f"{API}/projects/import", json={"path": str(path), "password": "Пароль-42", "mode": "copy"}).json()
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "done"
    everything = ac.get(f"{API}/jobs").text + json.dumps(
        [ac.get(f"{API}/jobs/{j['source']}/{j['id']}/log").json() for j in ac.get(f"{API}/jobs").json()["jobs"]]
    )
    assert "Пароль-42" not in everything


def test_newer_file_rejected(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "new.bfggis"
    manifest = {"format": "bfggis", "app": "bf2", "schema_revision": "9999", "project": {"name": "Будущее"}}
    with ArchiveWriter(path, {"app": "bf2"}) as w:
        w.add_bytes("manifest.json", json.dumps(manifest).encode())
    r = ac.post(f"{API}/projects/import/inspect", json={"path": str(path)})
    assert r.status_code == 422 and "обновите программу" in r.json()["message"]


def test_broken_file_leaves_nothing_and_replace_keeps_old(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    fill(ac)
    p = projects_of(ac.core).require_current()
    with dialect.write_tx(p.engine) as conn:  # several zstd blocks: a cut end leaves the beginning readable
        BlobStore(p.folder).put_bytes(conn, os.urandom(3 << 20), media_type="x", owner_kind="object", owner_id=20)
    good = Path(export(ac, tmp_path / "good.bfggis")["result"]["path"])
    data = good.read_bytes()
    broken = tmp_path / "broken.bfggis"
    broken.write_bytes(data[: len(data) - 1000])  # cut: the end of the zstd stream is gone
    root = projects_of(ac.core).root
    before = sorted(p.name for p in root.iterdir())
    r = ac.post(f"{API}/projects/import", json={"path": str(broken), "mode": "replace"}).json()
    assert "source" in r, r
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "failed" and job["attempts"] <= 2
    assert sorted(p.name for p in root.iterdir()) == before  # no partial project, the replaced one is intact
    r = ac.post(f"{API}/projects/import", json={"path": str(good), "mode": "replace"}).json()
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "done", job
    names = [e["name"] for e in ac.get(f"{API}/projects").json()["projects"]]
    assert names == ["Исходный"]


def test_file_of_bf1_is_recognised(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "bf1.bfggis"
    manifest = {
        "format": "bfggis",
        "app_version": "0.9.0",
        "schema_revision": "0016",
        "project": {"name": "Демо: участок Северный", "description": ""},
        "tables": {"collar": 48},
    }
    with ArchiveWriter(path, {"app_version": "0.9.0", "schema_revision": "0016"}) as w:
        w.add_bytes("manifest.json", json.dumps(manifest, ensure_ascii=False).encode())
    info = ac.post(f"{API}/projects/import/inspect", json={"path": str(path)}).json()
    assert info["from_bf"] is True and info["name"] == "Демо: участок Северный" and info["tables"]["collar"] == 48


def test_lost_password_never_writes_a_plain_file(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.jobs.service import jobs_of

    sched = jobs_of(ac.core)
    out = tmp_path / "x.bfggis"
    # what a retry after a worker crash sees: the job says «encrypted», the secret was taken by the first attempt
    key, jid = sched.submit("export.project", {"path": str(out), "encrypted": True}, secret=None)
    job = wait_job(ac, key, jid)
    assert job["status"] == "failed" and job["error"]["code"] == "password_lost" and not out.exists()
    r = ac.post(f"{API}/jobs/{key}/{jid}/retry")
    assert r.status_code == 409 and r.json()["code"] == "password_not_kept"


def test_internal_kinds_are_not_started_by_the_generic_endpoint(ac) -> None:  # type: ignore[no-untyped-def]
    r = ac.post(f"{API}/jobs", json={"kind": "import.project", "params": {"replace_folder": "/"}})
    assert r.status_code == 422 and r.json()["code"] == "unknown_job"


def test_import_retry_finds_the_project_of_the_first_attempt(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.archive.importer import find_imported, import_project

    fill(ac)
    path = Path(export(ac, tmp_path / "t.bfggis")["result"]["path"])
    root = projects_of(ac.core).root
    first = import_project(path, root, name="Повтор", token="tok1")
    again = find_imported(root, "tok1")
    assert again is not None and again["uid"] == first["uid"] and again["id_maps"] == first["id_maps"]
    assert find_imported(root, "other") is None


def test_roundtrip_lines_rasters_dtm_renumbered(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Э7 (ADR 0011 §1): line layers with their blobs, a raster with sidecars, a DTM with its drape reference and
    survey stations with coordinates come back with new ids, references rewritten, float64 bit for bit."""
    import numpy as np

    from bf_core.db.schema import dh_holes, dh_surveys, line_layers, rasters, wireframes
    from bf_core.formats.lineset import from_lines, from_points
    from bf_core.lines import store as ls_store

    p = projects_of(ac.core).require_current()
    now = dialect.utcnow()
    base = dict(sort_key=0.0, state="ready", version=1, created_at=now, updated_at=now)
    store = BlobStore(p.folder)
    xyz = np.array([[512_345.678_901_234, 6_234_567.123_456_789, float("nan")], [512_400.5, 6_234_600.25, 401.0]])
    lines = from_lines("Борт", [xyz, xyz[::-1] + 1.0], attrs={"STRING": ["7", None]})
    pts = from_points("Устья", xyz, attrs={"NAME": ["C-1", "C-2"]})
    with dialect.write_tx(p.engine) as conn:
        conn.execute(sa.insert(objects).values(id=40, kind="folder", name="Э7", name_key="э7", meta={}, **base))
        file_id = ls_store.create_file(conn, store, name="pit.str", meta={"format": "micromine_str"}, parent_id=40)
        conn.execute(sa.update(objects).where(objects.c.id == file_id).values(id=77))  # a gap before the layers
        file_id = 77
        layer_a = ls_store.put_layer(conn, store, lines, file_id=file_id)
        layer_b = ls_store.put_layer(conn, store, pts, file_id=file_id)
        conn.execute(sa.insert(objects).values(id=90, kind="raster", name="План", name_key="план", meta={}, **base))
        georef = {"affine": [0.5, 0.0, 512_000.25, 0.0, -0.5, 6_235_000.75], "source": "world", "crs": None}
        conn.execute(
            sa.insert(rasters).values(object_id=90, width=40, height=30, georef=georef, vertical=False, opacity=0.5)
        )
        for role, data in (("source", b"jpeg"), ("sidecar:jgw", b"0.5\n0\n0\n-0.5\n1\n2\n"), ("display0", b"d0")):
            store.put_bytes(conn, data, media_type="image/jpeg", owner_kind="object", owner_id=90, role=role)
        conn.execute(sa.insert(objects).values(id=95, kind="wireframe", name="ЦТМ", name_key="цтм", meta={}, **base))
        dtm = {"clip": False, "tint": "#ffffff", "opacity": 1.0, "shade": True}
        conn.execute(
            sa.insert(wireframes).values(
                object_id=95,
                point_count=3,
                triangle_count=1,
                origin=[512_000.0, 6_235_000.0, 400.0],
                stats={},
                attrs={"Участок": "Север"},
                dtm=dtm,
            )
        )
        conn.execute(sa.insert(object_refs).values(src_id=95, dst_id=90, role="drape"))
        conn.execute(
            sa.insert(objects).values(id=60, kind="drillhole_set", name="Канавы", name_key="канавы", meta={}, **base)
        )
        hole = conn.execute(sa.insert(dh_holes).values(set_id=60, name="К-1", has_collar=True)).inserted_primary_key[0]
        conn.execute(
            sa.insert(dh_surveys).values(
                set_id=60, hole_id=hole, depth=0.0, x=xyz[0, 0], y=xyz[0, 1], z=401.123_456_789_012_3
            )
        )
    job = export(ac, tmp_path / "e7.bfggis")
    r = ac.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "done", job
    ids = {int(k): v for k, v in job["result"]["id_maps"]["objects"].items()}
    assert set(ids) == {40, 77, layer_a, layer_b, 90, 95, 60}
    res = job["result"]
    ac.post(f"{API}/projects/{res['uid']}/open")
    q = projects_of(ac.core).require_current()
    qstore = BlobStore(q.folder)
    with dialect.read_tx(q.engine) as conn:
        assert conn.execute(sa.select(objects.c.parent_id).where(objects.c.id == ids[77])).scalar_one() == ids[40]
        assert ls_store.layer_ids(conn, ids[77]) == [ids[layer_a], ids[layer_b]]
        back = ls_store.read_layer(conn, qstore, ids[layer_a])
        back_pts = ls_store.read_layer(conn, qstore, ids[layer_b])
        assert set(conn.execute(sa.select(line_layers.c.object_id)).scalars()) == {ids[layer_a], ids[layer_b]}
        ras = conn.execute(sa.select(rasters)).mappings().one()
        roles = {r.role for r in conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == ids[90]))}
        wf = conn.execute(sa.select(wireframes)).mappings().one()
        ref = conn.execute(sa.select(object_refs).where(object_refs.c.role == "drape")).mappings().one()
        st = conn.execute(sa.select(dh_surveys)).mappings().one()
    assert back.xyz.tobytes() == lines.xyz.tobytes() and back.attrs is not None
    assert back.attrs.column("STRING").to_pylist() == ["7", None]
    assert back_pts.points_only and back_pts.xyz.tobytes() == pts.xyz.tobytes()
    assert ras["object_id"] == ids[90] and ras["georef"] == georef and ras["opacity"] == 0.5
    assert roles == {"source", "sidecar:jgw", "display0"}
    assert wf["object_id"] == ids[95] and wf["dtm"] == dtm and wf["attrs"] == {"Участок": "Север"}
    assert (ref["src_id"], ref["dst_id"]) == (ids[95], ids[90])
    assert (st["x"], st["y"], st["z"]) == (xyz[0, 0], xyz[0, 1], 401.123_456_789_012_3) and st["set_id"] == ids[60]


def test_line_versions_are_archived_with_their_files() -> None:
    """Э9 (ADR 0015 §1): the journal is renumbered and the owner `line_version` follows its rows."""
    from bf_core.archive.spec import ID_COLUMNS, KIND_COLUMNS

    names = [t.name for t in TABLES]
    spec = TABLES[names.index("line_layer_versions")]
    assert spec.renumber and names.index("objects") < names.index("line_layer_versions")
    assert ID_COLUMNS[("line_layer_versions", "layer_id")] == "objects"
    assert KIND_COLUMNS[("blob_owners", "owner_kind", "owner_id")]["line_version"] == "line_layer_versions"


@pytest.mark.usefixtures("series_router")
def test_roundtrip_series_of_sections(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Э10 (ADR 0016 §1, §4): a series keeps its number and parameters, its contour layer and the view key
    `section.series` follow the new ids; a series without a contour layer comes back as it was."""
    c = pclient
    c.post(API + "/projects", json={"name": "Серии"})
    layer = c.post(f"{API}/lines/layers", json={"name": "Контуры", "color": "#ff8800"}).json()
    params = {"type": "vertical", "p1": [1000, 5000, 0], "az": 90, "s1": 100, "step": 25}
    first = c.post(f"{API}/section-series", json={"params": params}).json()
    series = c.post(
        f"{API}/section-series",
        json={"params": {**params, "contour_layer": layer["id"], "template": "Р-{k}"}, "name": "Профили"},
    ).json()
    assert c.delete(f"{API}/section-series/{first['id']}").status_code == 200
    plain = c.post(f"{API}/section-series", json={"params": {**params, "type": "plan", "p1": [0, 0, 400]}}).json()
    assert (series["number"], plain["number"]) == (2, 3)
    key = {"line": None, "clip": True, "series": {"id": series["id"], "k": 3, "seg": None}}
    assert c.put(f"{API}/view/section", json={"value": key, "expected_version": 0}).status_code == 200
    job = export(c, tmp_path / "Серии")
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    res = wait_job(c, r["source"], r["id"])["result"]
    new_ids = {int(k): v for k, v in res["id_maps"]["objects"].items()}
    c.post(f"{API}/projects/{res['uid']}/open")
    got = {s["name"]: s for s in c.get(f"{API}/section-series").json()["series"]}
    assert set(got) == {"Профили", "Серия 3"}
    s = got["Профили"]
    assert s["id"] == new_ids[series["id"]] and s["number"] == 2 and s["count"] == 5
    assert s["params"] == {**series["params"], "contour_layer": new_ids[layer["id"]]}
    assert got["Серия 3"]["params"] == plain["params"] and got["Серия 3"]["number"] == 3
    names = [x["name"] for x in c.get(f"{API}/section-series/{s['id']}/table").json()["sections"]]
    assert names == ["Р-1", "Р-2", "Р-3", "Р-4", "Р-5"]
    view = c.get(f"{API}/view").json()["values"]["section"]["value"]
    assert view == {**key, "series": {**key["series"], "id": s["id"]}}
    # a new series of the imported project continues the numbers
    assert c.post(f"{API}/section-series", json={"params": params}).json()["number"] == 4
