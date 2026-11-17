"""Cases from the Э4 review: edits of a table loaded from Parquet, an id given again after a deletion, a new
import into an existing set, deleted columns kept by row edits, undo of a changed value."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_import_job import API, _check, wait_job
from tests.test_tables import _key, _page, dset  # noqa: F401 - the fixture

pytestmark = pytest.mark.windows


def test_edit_after_reopen_from_parquet(dset, monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.drillholes import snapshot

    c, set_id, tabs = dset
    monkeypatch.setattr(snapshot, "PARQUET_FROM", 1)
    assay = tabs["assay"]["table"]
    p = _page(c, set_id, assay, sort="from")  # built from the database, Parquet written
    snapshot.snapshots.forget()
    p = _page(c, set_id, assay, sort="from")  # read back from Parquet
    au = _key(p, "Au_ppm")
    r = c.patch(
        f"{API}/drillholes/sets/{set_id}/tables/{assay}/rows/{p['rows'][0]['id']}", json={"column": au, "value": "7"}
    )
    assert r.status_code == 200, r.text
    assert _page(c, set_id, assay, sort="from")["rows"][0][au] == 7.0


def test_a_new_table_with_a_freed_id_never_shows_old_rows(dset, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.drillholes import snapshot

    c, set_id, tabs = dset
    monkeypatch.setattr(snapshot, "PARQUET_FROM", 1)
    assay = tabs["assay"]["table"]
    _page(c, set_id, assay)
    assert c.delete(f"{API}/drillholes/sets/{set_id}").status_code == 200
    f = tmp_path / "again.csv"
    f.write_text("Hole_ID,From,To,Au_ppm,Lith,SampleID\nZ9,0,1,42,кварц,S9\n", encoding="utf-8")
    chk = _check(c, f)
    spec = {
        "path": str(f),
        "kind": "assay",
        "mapping": chk["mapping"],
        "components": [{"column": "Au_ppm", "unit": "ppm"}],
        "attributes": ["Lith"],
    }
    r = c.post(f"{API}/drillholes/import", json={"set_name": "Снова", "files": [spec]}).json()
    assert wait_job(c, r["source"], r["id"])["status"] == "done"
    new_set = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    tab = next(t for t in c.get(f"{API}/drillholes/sets/{new_set}/tables").json()["tables"] if t["kind"] == "assay")
    rows = _page(c, new_set, tab["table"])["rows"]
    assert [row["hole"] for row in rows] == ["Z9"]


def test_import_into_an_existing_set_refreshes_collars_and_trajectories(dset, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, _tabs = dset
    assert next(r for r in _page(c, set_id, "collar")["rows"] if r["hole"] == "D1")["z"] == 300.0
    c.get(f"{API}/drillholes/sets/{set_id}/trajectories")  # cached
    f = tmp_path / "collar2.csv"
    f.write_text("HoleID,X,Y,Z,Depth\nD1,10,20,999,50\n", encoding="utf-8")
    chk = _check(c, f)
    r = c.post(
        f"{API}/drillholes/import",
        json={"set_id": set_id, "files": [{"path": str(f), "kind": "collar", "mapping": chk["mapping"]}]},
    ).json()
    assert wait_job(c, r["source"], r["id"])["status"] == "done"
    assert next(r for r in _page(c, set_id, "collar")["rows"] if r["hole"] == "D1")["z"] == 999.0
    d1 = next(h for h in c.get(f"{API}/drillholes/sets/{set_id}/trajectories").json()["holes"] if h["name"] == "D1")
    assert d1["stations"][0][3] == 999.0


def test_row_edits_keep_deleted_columns_and_undo_checks_later_edits(dset) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, tabs = dset
    assay = tabs["assay"]["table"]
    base = f"{API}/drillholes/sets/{set_id}/tables/{assay}"
    p = _page(c, set_id, assay, sort="from")
    au = _key(p, "Au_ppm")
    row = p["rows"][0]
    assert c.delete(f"{base}/columns/{au}").status_code == 200
    assert c.patch(f"{base}/rows/{row['id']}", json={"column": "to", "value": "1,5"}).status_code == 200
    edits = c.get(f"{API}/drillholes/sets/{set_id}/edits").json()["edits"]
    column_edit = next(e for e in edits if e["action"] == "column")
    assert c.post(f"{API}/drillholes/edits/{column_edit['id']}/undo").status_code == 200
    assert next(r for r in _page(c, set_id, assay)["rows"] if r["id"] == row["id"])[au] == 1.5  # value kept

    assert c.patch(f"{base}/rows/{row['id']}", json={"column": au, "value": "2"}).status_code == 200
    first = c.get(f"{API}/drillholes/sets/{set_id}/edits").json()["edits"][0]
    assert c.patch(f"{base}/rows/{row['id']}", json={"column": au, "value": "3"}).status_code == 200
    r = c.post(f"{API}/drillholes/edits/{first['id']}/undo")  # 2 → 1.5 would overwrite the later 3
    assert r.status_code == 409 and r.json()["code"] == "edit_conflict"
