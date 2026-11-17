"""Wizard «Импорт скважин» end to end: inspect → check → job (ТЗ 7.7)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, dh_holes, import_log
from bf_core.projects.service import projects_of

pytestmark = pytest.mark.windows
API = "/api/v1"


def wait_job(c, key: str, job_id: int, timeout: float = 60) -> dict:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"{API}/jobs/{key}/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(job)


@pytest.fixture()
def files(tmp_path: Path) -> dict[str, Path]:
    out = {}
    out["collar"] = tmp_path / "collar.csv"
    out["collar"].write_bytes(
        "Скважина;X, м;Y, м;Z, м;Глубина, м\nC1;100;200;300;50\nC2;110;210;310;40\n".encode("cp1251")
    )
    out["survey"] = tmp_path / "survey.txt"
    out["survey"].write_text(
        "HOLEID\tDEPTH\tAZIMUTH\tDIP\nC1\t0\t90\t-60\nC1\t25\t92\t-61\nC2\t0\t0\t-90\n", encoding="utf-8"
    )
    out["assay"] = tmp_path / "assay.csv"
    out["assay"].write_text(
        "Hole_ID,From_m,To_m,Au_ppm,SampleID\nC1,0,1,<0.01,S1\nC1,1,2,1.5,S2\nC2,0,2,abc,S3\n", encoding="utf-8"
    )
    return out


def _check(c, path: Path, **extra):  # type: ignore[no-untyped-def]
    r = c.post(f"{API}/drillholes/import/check", json={"path": str(path), **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_wizard_end_to_end(pclient, files) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Импорт"})
    info = pclient.post(f"{API}/drillholes/import/inspect", json={"path": str(files["collar"])}).json()
    assert info["format"] == "csv" and "1251" in info["info"]["encoding"] and info["info"]["delimiter"] == ";"
    specs = []
    for kind in ("collar", "survey", "assay"):
        chk = _check(pclient, files[kind])
        assert chk["kind"] == kind, chk
        spec = {"path": str(files[kind]), "kind": kind, "mapping": chk["mapping"], "components": chk["components"]}
        if kind == "assay":
            assert chk["mapping"]["sample_id"] == "SampleID"
            assert chk["can_force"] and any(i["code"] == "non_numeric" for i in chk["issues"])
            spec["components"] = [{"column": "Au_ppm", "unit": "ppm"}]
            chk = _check(
                pclient, files[kind], kind=kind, mapping=chk["mapping"], components=spec["components"], force=True
            )
            assert chk["preview"][0]["Au_ppm"] == 0.005 and chk["counts"]["below_limit"] == 1
            assert chk["preview"][2]["Au_ppm (исходное)"] == "abc"
            spec["force"] = True
        specs.append(spec)
    r = pclient.post(f"{API}/drillholes/import", json={"set_name": "Набор 1", "files": specs}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    cur = pclient.get(f"{API}/projects/current").json()["project"]
    assert cur["summary"]["sets"] == 1 and cur["summary"]["holes"] == 2
    assert cur["summary"]["surveys"] == 3 and cur["summary"]["assays"] == 3
    p = projects_of(pclient.core).require_current()
    with dialect.read_tx(p.engine) as conn:
        hist = conn.execute(sa.select(import_log)).mappings().all()
        assert sorted(h["target"] for h in hist) == ["assay", "collar", "survey"] and all(h["sha256"] for h in hist)
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(blob_owners).where(blob_owners.c.owner_kind == "import")
            ).scalar_one()
            == 3
        )
    sets = pclient.get(f"{API}/drillholes/sets").json()["sets"]
    assert sets == [
        {
            "id": sets[0]["id"],
            "name": "Набор 1",
            "crs": None,
            "holes": 2,
            "tables": [{"name": "Пробы (Assay)", "kind": "assay", "rows": 3}],
        }
    ]

    # «Заменить» collars of set 1 does not touch set 2 with the same hole names
    r = pclient.post(f"{API}/drillholes/import", json={"set_name": "Набор 2", "files": specs[:1]}).json()
    assert wait_job(pclient, r["source"], r["id"])["status"] == "done"
    files["collar"].write_bytes("Скважина;X, м;Y, м;Z, м;Глубина, м\nC1;999;999;999;50\n".encode("cp1251"))
    chk = _check(pclient, files["collar"])
    r = pclient.post(
        f"{API}/drillholes/import",
        json={
            "set_id": sets[0]["id"],
            "files": [{"path": str(files["collar"]), "kind": "collar", "mapping": chk["mapping"]}],
        },
    ).json()
    assert wait_job(pclient, r["source"], r["id"])["status"] == "done"
    with dialect.read_tx(p.engine) as conn:
        xs = {(h.set_id, h.name): h.x for h in conn.execute(sa.select(dh_holes))}
    assert xs[(sets[0]["id"], "C1")] == 999 and xs[(sets[0]["id"], "C2")] is None
    other = [k for k in xs if k[0] != sets[0]["id"]]
    assert {xs[k] for k in other} == {100.0, 110.0}


def test_failed_check_in_the_job_leaves_no_set(pclient, files) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Сбой"})
    chk = _check(pclient, files["assay"])
    spec = {
        "path": str(files["assay"]),
        "kind": "assay",
        "mapping": chk["mapping"],
        "components": [{"column": "Au_ppm", "unit": "ppm"}],
    }
    r = pclient.post(f"{API}/drillholes/import", json={"set_name": "Пустой", "files": [spec]}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "failed" and "нечисловых" in job["error"]["message"]
    assert pclient.get(f"{API}/drillholes/sets").json()["sets"] == []
    assert pclient.get(f"{API}/projects/current").json()["project"]["summary"]["holes"] == 0


def test_xls_is_read_and_unknown_file_is_refused(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Форматы"})
    xls = Path(__file__).parent / "formats" / "data" / "book.xls"
    r = pclient.post(f"{API}/drillholes/import/inspect", json={"path": str(xls)})
    assert r.status_code == 200 and r.json()["format"] == "xls"
    bad = tmp_path / "x.bin"
    bad.write_bytes(b"\x00\x01\x02" * 100)
    r = pclient.post(f"{API}/drillholes/import/inspect", json={"path": str(bad)})
    assert r.status_code == 422 and r.json()["hint"]
