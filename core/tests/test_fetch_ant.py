"""tools/ci/fetch_ant.py (Э23 23.19, ADR 0029 decision 37): pinned SHA-256, skip without pins, no network."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "ci" / "fetch_ant.py"
CONFIG = SCRIPT.with_name("ant.json")


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_ant_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fa() -> ModuleType:
    return load_script()


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def targz_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def setup(
    fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes, **cfg: object
) -> tuple[Path, Path, list[str]]:
    base: dict[str, object] = {
        "version": "2.9.0",
        "url": "https://example.invalid/{version}/{archive_name}",
        "verified": False,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "license": "MIT",
        "archive_name": "ant_test.zip",
    }
    base.update(cfg)
    config = tmp_path / "ant.json"
    config.write_text(json.dumps(base), encoding="utf-8")
    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> str:
        calls.append(url)
        dest.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(fa, "download", fake_download)
    return config, tmp_path / "out" / "ant", calls


def run(fa: ModuleType, config: Path, out: Path, *extra: str) -> int:
    return int(fa.main(["--config", str(config), "--out", str(out), *extra]))


def test_pinned_hash_extracts_binary(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    payload = zip_bytes({"ant.exe": b"MZ-fake", "LICENSE": b"MIT text"})
    config, out, calls = setup(fa, tmp_path, monkeypatch, payload)
    assert run(fa, config, out) == 0
    assert calls == ["https://example.invalid/2.9.0/ant_test.zip"]
    assert (out / "ant.exe").read_bytes() == b"MZ-fake"
    assert (out / ".empty").exists()
    text = capsys.readouterr().out
    assert hashlib.sha256(payload).hexdigest() in text


def test_tar_gz_archive_and_nested_binary(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = targz_bytes({"ant_2.9.0/ant.exe": b"MZ-nested"})
    config, out, _ = setup(fa, tmp_path, monkeypatch, payload, archive_name="ant_test.tar.gz")
    assert run(fa, config, out) == 0
    assert (out / "ant.exe").read_bytes() == b"MZ-nested"


def test_hash_mismatch_fails_and_leaves_no_binary(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    payload = zip_bytes({"ant.exe": b"MZ-fake"})
    config, out, _ = setup(fa, tmp_path, monkeypatch, payload, sha256="0" * 64)
    assert run(fa, config, out) == 1
    assert "не совпал" in capsys.readouterr().err
    assert sorted(p.name for p in out.rglob("*")) == [".empty"]


def test_archive_without_binary_fails(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, out, _ = setup(fa, tmp_path, monkeypatch, zip_bytes({"README": b"x"}))
    assert run(fa, config, out) == 1
    assert not (out / "ant.exe").exists()


def test_unsafe_archive_path_fails(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, out, _ = setup(fa, tmp_path, monkeypatch, zip_bytes({"../evil.exe": b"x", "ant.exe": b"y"}))
    assert run(fa, config, out) == 1
    assert not (tmp_path / "out" / "evil.exe").exists()


@pytest.mark.parametrize("missing", ["url", "sha256"])
def test_missing_pin_skips_with_warning(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str], missing: str) -> None:
    config, out, calls = setup(fa, tmp_path, monkeypatch, zip_bytes({"ant.exe": b"x"}), **{missing: None})
    out.mkdir(parents=True)
    (out / "ant.exe").write_bytes(b"stale")
    assert run(fa, config, out) == 0
    assert calls == []
    assert "ant не включён: нет закреплённых адреса/хеша (вопрос № 125)" in capsys.readouterr().out
    assert sorted(p.name for p in out.rglob("*")) == [".empty"]  # stale binary is removed, marker keeps the folder


@pytest.mark.parametrize("lic", [None, "GPL-3.0-only", "Proprietary", "MIT AND GPL-3.0-only"])
def test_license_outside_allow_list_skips(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str], lic: str | None) -> None:
    config, out, calls = setup(fa, tmp_path, monkeypatch, zip_bytes({"ant.exe": b"x"}), license=lic)
    assert run(fa, config, out) == 0
    assert calls == []
    assert "не из белого списка" in capsys.readouterr().out
    assert not (out / "ant.exe").exists()


def test_license_expressions(fa: ModuleType) -> None:
    ok = ["MIT", "Apache-2.0", "BSD-3-Clause", "MPL-2.0", "ISC", "PSF-2.0", "MIT OR GPL-3.0-only", "(MIT AND ISC)"]
    bad = [None, "", "GPL-3.0-only", "AGPL-3.0", "MIT AND GPL-3.0-only", "LicenseRef-proprietary"]
    assert all(fa.license_allowed(x) for x in ok)
    assert not any(fa.license_allowed(x) for x in bad)


def test_dry_run_touches_nothing(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, out, calls = setup(fa, tmp_path, monkeypatch, zip_bytes({"ant.exe": b"x"}))
    assert run(fa, config, out, "--dry-run") == 0
    assert calls == []
    assert not out.exists()


def test_print_hash_reports_hash_and_license(fa: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    payload = zip_bytes({"ant.exe": b"x", "LICENSE": b"Permission is hereby granted"})
    config, out, calls = setup(fa, tmp_path, monkeypatch, payload, url=None, sha256=None)
    assert run(fa, config, out, "--print-hash", "--url", "https://example.invalid/a.zip") == 0
    text = capsys.readouterr().out
    assert hashlib.sha256(payload).hexdigest() in text
    assert "Permission is hereby granted" in text
    assert calls == ["https://example.invalid/a.zip"]
    assert not out.exists()


def test_repo_config_is_unpinned_until_verified() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert cfg["version"] == "2.9.0"
    assert cfg["verified"] is False or cfg["url"] is not None
    if cfg["sha256"] is not None:
        assert len(cfg["sha256"]) == 64 and cfg["url"] and cfg["license"]  # a pin is complete or absent
