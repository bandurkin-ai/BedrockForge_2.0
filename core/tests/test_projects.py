"""Projects (ТЗ 7.3, ADR 0002 §5): create, rename, describe, search, delete, lock, backups, migration."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from bf_core.projects.lock import ProjectLock
from bf_core.projects.project import open_folder
from bf_core.projects.service import projects_of

pytestmark = pytest.mark.windows  # file locks, rename of open folders
API = "/api/v1/projects"


def _create(c, name: str, **desc: str) -> dict:  # type: ignore[no-untyped-def]
    r = c.post(API, json={"name": name, "description": desc or None})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_opens_and_lists(pclient) -> None:  # type: ignore[no-untyped-def]
    p = _create(pclient, "Участок Северный", deposit="Северное")
    assert p["name"] == "Участок Северный" and p["read_only"] is False
    assert p["summary"]["holes"] == 0 and set(p["summary"]) >= {"sets", "wireframes", "rasters", "chats"}
    folder = Path(p["path"])
    assert folder.name == "Участок Северный.bfproj"
    for sub in ("project.bfproj", "project.sqlite", "blobs", "cache", "backups", "logs", "project.lock"):
        assert (folder / sub).exists(), sub
    assert pclient.get(API + "/current").json()["project"]["uid"] == p["uid"]
    listed = pclient.get(API).json()["projects"]
    assert [e["name"] for e in listed] == ["Участок Северный"] and listed[0]["open"] is True


def test_duplicate_and_rename_into_taken_name_rejected(pclient) -> None:  # type: ignore[no-untyped-def]
    a = _create(pclient, "Альфа")
    _create(pclient, "Бета")
    r = pclient.post(API, json={"name": "  альфа "})
    assert r.status_code == 409 and r.json()["code"] == "name_taken"
    pclient.post(f"{API}/{a['uid']}/open")
    r = pclient.patch(f"{API}/{a['uid']}", json={"name": "БЕТА"})
    assert r.status_code == 409 and "уже есть" in r.json()["message"]
    assert sorted(e["name"] for e in pclient.get(API).json()["projects"]) == ["Альфа", "Бета"]
    r = pclient.patch(f"{API}/{a['uid']}", json={"name": "Гамма"})
    assert r.status_code == 200 and r.json()["name"] == "Гамма"


def test_description_persists_and_search_finds_word(pclient, tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = _create(pclient, "Проект 1")
    _create(pclient, "Проект 2")
    pclient.post(f"{API}/{p['uid']}/open")
    r = pclient.patch(f"{API}/{p['uid']}", json={"description": {"deposit": "Кедровое", "stage": "разведка"}})
    assert r.json()["description"]["deposit"] == "Кедровое" and r.json()["version"] == 2
    stale = pclient.patch(f"{API}/{p['uid']}", json={"description": {"stage": "x"}, "expected_version": 1})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    found = pclient.get(API, params={"q": "КЕДРОВ"}).json()["projects"]
    assert [e["name"] for e in found] == ["Проект 1"]
    # «после перезапуска»: a new service reads the folder
    pclient.post(API + "/close")
    found = pclient.get(API, params={"q": "разведка"}).json()["projects"]
    assert found[0]["description"]["stage"] == "разведка"


def test_delete_removes_folder_and_list_entry(pclient) -> None:  # type: ignore[no-untyped-def]
    p = _create(pclient, "Удаляемый")
    folder = Path(p["path"])
    r = pclient.delete(f"{API}/{p['uid']}")
    assert r.status_code == 200
    assert not folder.exists() and pclient.get(API).json()["projects"] == []
    assert pclient.get(API + "/current").json()["project"] is None
    assert not [x for x in folder.parent.iterdir() if x.name.startswith(".del-")]


def test_second_instance_opens_read_only_and_refuses_changes(pclient, tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = _create(pclient, "Общий")
    folder = Path(p["path"])
    pclient.post(API + "/close")
    holder = ProjectLock(folder / "project.lock")
    code = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from bf_core.projects.lock import ProjectLock
        lock = ProjectLock(Path({str(folder / "project.lock")!r}))
        assert lock.acquire()
        print("locked", flush=True)
        sys.stdin.read()
        """
    )
    other = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONPATH": "."},
    )
    try:
        assert other.stdout is not None and other.stdout.readline().strip() == "locked"
        assert not holder.acquire()
        r = pclient.post(f"{API}/{p['uid']}/open")
        assert r.status_code == 200 and r.json()["read_only"] is True
        # on Windows the venv python.exe is a launcher: the locking process is its child, not other.pid
        owner = r.json()["lock_owner"]
        assert isinstance(owner.get("pid"), int) and owner["pid"] != os.getpid() and owner.get("since")
        r = pclient.patch(f"{API}/{p['uid']}", json={"name": "Другое"})
        assert r.status_code == 423 and r.json()["code"] == "project_read_only"
        assert pclient.delete(f"{API}/{p['uid']}").status_code == 423
    finally:
        other.communicate("")
    pclient.post(API + "/close")
    r = pclient.post(f"{API}/{p['uid']}/open")
    assert r.json()["read_only"] is False


def test_backups_daily_and_keep_five(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path, "Копии", {})
    project = open_folder(folder)
    try:
        first = list((folder / "backups").glob("project-*.sqlite"))
        assert len(first) == 1  # the first open makes the daily copy
        assert project.backup_if_due() is None
        for i in range(7):
            b = project.backup()
            os.utime(b, (i, i))
            b.rename(b.with_name(f"project-2026010{i}-000000.sqlite"))
            project.backup()
        assert len(list((folder / "backups").glob("project-*.sqlite"))) == 5
    finally:
        project.close()


def test_damaged_database_is_reported(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.api.errors import ApiError
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path, "Битый", {})
    db = folder / "project.sqlite"
    data = bytearray(db.read_bytes())
    data[4096:] = b"\x0d\xff" * ((len(data) - 4096) // 2)  # every page after the header page is garbage
    db.write_bytes(bytes(data))
    with pytest.raises(ApiError) as err:
        open_folder(folder)
    assert err.value.code == "project_damaged"
    assert ProjectLock(folder / "project.lock").acquire()  # the failed open released the lock


def test_network_and_long_paths_rejected(pclient) -> None:  # type: ignore[no-untyped-def]
    from bf_core.api.errors import ApiError
    from bf_core.projects.project import check_location

    with pytest.raises(ApiError) as err:
        check_location(Path("\\\\server\\share\\x.bfproj"))
    assert err.value.code == "network_path"
    with pytest.raises(ApiError) as err:
        check_location(Path("/" + "п" * 200))
    assert err.value.code == "path_too_long"


def test_folder_refused_by_os_is_reported(pclient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Windows «Контролируемый доступ к папкам» refuses mkdir in Documents: a clear error, not «internal»."""
    real = Path.mkdir

    def refused(self: Path, *a, **kw) -> None:  # type: ignore[no-untyped-def]
        if "Проекты" in str(self):
            raise FileNotFoundError(2, "Не удается найти указанный файл", str(self))
        real(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", refused)
    r = pclient.post(API, json={"name": "Новый"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "folder_denied"
    assert "Папка данных проектов" in body["hint"]


def test_demo_created_once_then_opened(pclient) -> None:  # type: ignore[no-untyped-def]
    first = pclient.post(API + "/demo").json()
    assert first["created"] is True and first["name"] == "Демо: участок Северный"
    assert first["description"]["stage"] == "Разведка"
    pclient.post(API + "/close")
    second = pclient.post(API + "/demo").json()
    assert second["created"] is False and second["uid"] == first["uid"]
    assert [e["name"] for e in pclient.get(API).json()["projects"]] == ["Демо: участок Северный"]


def test_sweep_removes_abandoned_service_folders_only(pclient, tmp_path) -> None:  # type: ignore[no-untyped-def]
    svc = projects_of(pclient.core)
    svc.root.mkdir(parents=True, exist_ok=True)
    old = svc.root / ".imp-dead.bfproj"
    busy = svc.root / ".imp-busy.bfproj"
    fresh = svc.root / ".del-fresh.bfproj"
    for d in (old, busy, fresh):
        d.mkdir()
        (d / "project.lock").write_bytes(b" ")
    lock = ProjectLock(busy / "project.lock")
    assert lock.acquire()
    past = time.time() - 3600
    for d in (old, busy):
        os.utime(d, (past, past))
    try:
        assert svc.sweep() == 1
    finally:
        lock.release()
    assert not old.exists() and busy.exists() and fresh.exists()


def test_busy_project_is_not_deleted_and_stays_open(pclient) -> None:  # type: ignore[no-untyped-def]
    p = _create(pclient, "Занятый")
    svc = projects_of(pclient.core)
    svc.is_busy = lambda uid: uid == p["uid"]
    r = pclient.delete(f"{API}/{p['uid']}")
    assert r.status_code == 409 and r.json()["code"] == "project_busy"
    assert pclient.get(API + "/current").json()["project"]["uid"] == p["uid"]


def test_damaged_project_does_not_close_the_open_one(pclient) -> None:  # type: ignore[no-untyped-def]
    a = _create(pclient, "Целый")
    b = _create(pclient, "Сломанный")
    pclient.post(f"{API}/{a['uid']}/open")
    db = Path(b["path"]) / "project.sqlite"
    data = bytearray(db.read_bytes())
    data[4096:] = b"\x0d\xff" * ((len(data) - 4096) // 2)
    db.write_bytes(bytes(data))
    r = pclient.post(f"{API}/{b['uid']}/open")
    assert r.status_code == 409 and r.json()["code"] == "project_damaged"
    assert pclient.get(API + "/current").json()["project"]["uid"] == a["uid"]
