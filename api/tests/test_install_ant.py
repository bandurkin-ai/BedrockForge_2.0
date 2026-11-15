"""The installer of `ant` in the image: only an archive with the pinned checksum is installed."""

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "docker" / "install_ant.py"


def load():
    spec = importlib.util.spec_from_file_location("install_ant", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"#!/bin/sh\necho ant\n"
        info = tarfile.TarInfo("ant")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def installer(monkeypatch, tmp_path):
    m = load()
    data = archive()

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    urls = []
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda req, timeout: urls.append(req.full_url) or Response(data))
    monkeypatch.setattr(m.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(m, "DEST", str(tmp_path / "ant"))
    return m, data, urls, tmp_path / "ant"


def test_installs_only_a_matching_archive(installer, monkeypatch):
    m, data, urls, dest = installer
    monkeypatch.setitem(m.SHA256, "amd64", hashlib.sha256(data).hexdigest())

    assert m.main() == 0
    assert dest.read_bytes().startswith(b"#!/bin/sh") and dest.stat().st_mode & 0o111
    assert urls == [f"https://github.com/anthropics/anthropic-cli/releases/download/v{m.ANT_VERSION}/ant_{m.ANT_VERSION}_linux_amd64.tar.gz"]


def test_a_wrong_checksum_stops_the_build(installer):
    m, _, _, dest = installer  # the pinned checksum is the real release's, not this test archive's

    assert m.main() == 1
    assert not dest.exists()


def test_pinned_checksums_look_like_sha256():
    m = load()
    assert set(m.SHA256) == {"amd64", "arm64"}
    assert all(len(v) == 64 and int(v, 16) >= 0 for v in m.SHA256.values())
