"""Fetches the `ant` CLI (account login of the assistant, ADR 0029 decision 37) into app/build/ant/.

Values live in tools/ci/ant.json (version, url template, verified, sha256, license, archive_name).
Nothing is downloaded while url or sha256 is null or the licence is not on the allow list: the script
warns and exits 0, the build then goes without `ant` (API key only, question 125). With `--strict` (release.yml)
a pinned sha256 whose url or licence is missing or not allowed is an error (exit 1): half-made pins never pass.
The URL template may use {version} and {archive_name}. Stdlib only; proxy and CA come from the
environment (HTTPS_PROXY, SSL_CERT_FILE).

  python tools/ci/fetch_ant.py --platform windows        # build step
  python tools/ci/fetch_ant.py --platform windows --strict   # release step
  python tools/ci/fetch_ant.py --dry-run                 # show the plan, touch nothing
  python tools/ci/fetch_ant.py --print-hash --url <url>  # first build: print SHA-256 and the licence text to pin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import ssl
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(__file__).with_name("ant.json")
DEFAULT_OUT = ROOT / "app" / "build" / "ant"
MARKER = ".empty"  # keeps app/build/ant/ present, so electron-builder extraResources never meets a missing dir
ALLOWED_LICENSES = {"MIT", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "BSD", "APACHE-2.0", "MPL-2.0", "ISC", "PSF-2.0", "PSF"}
MAX_DOWNLOAD = 300 * 1024 * 1024
SKIP_NOTE = "ant не включён: нет закреплённых адреса/хеша (вопрос № 125)"


class FetchError(Exception):
    """A failure that must fail the build (exit code 1)."""


def load_config(path: Path) -> dict[str, object]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or not cfg.get("version"):
        raise FetchError(f"{path}: нужен объект с полем version")
    return cfg


def license_allowed(expr: object) -> bool:
    """SPDX-like expression: `A OR B` needs one allowed id, anything else needs all of them."""
    if not isinstance(expr, str) or not expr.strip():
        return False
    text = expr.replace("(", " ").replace(")", " ")
    ors = [p for p in re.split(r"\s+OR\s+", text, flags=re.I) if p.strip()]

    def ids(part: str) -> list[str]:
        return [t.strip().upper() for t in re.split(r"\s+AND\s+", part.strip(), flags=re.I) if t.strip()]

    return any(all(i in ALLOWED_LICENSES for i in ids(p)) for p in ors)


def resolve_url(cfg: dict[str, object], override: str | None = None) -> str | None:
    template = override or cfg.get("url")
    if not isinstance(template, str) or not template:
        return None
    return template.format(version=cfg["version"], archive_name=cfg.get("archive_name") or "")


def download(url: str, dest: Path) -> str:
    """Streams url into dest; returns the SHA-256 hex digest. Replaced by tests."""
    if not url.lower().startswith("https://"):
        raise FetchError(f"Адрес загрузки должен быть https: {url}")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "bf-build-fetch-ant"})
    h = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp, dest.open("wb") as out:
        while chunk := resp.read(1 << 20):
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise FetchError(f"Архив больше {MAX_DOWNLOAD >> 20} МБ — загрузка прервана")
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


def _check_inside(root: Path, name: str) -> None:
    target = (root / name).resolve()
    base = root.resolve()
    if target != base and base not in target.parents:
        raise FetchError(f"Небезопасный путь в архиве: {name}")


def extract(archive: Path, name: str, dest: Path) -> None:
    """Unpacks a zip or tar.gz archive (by the name suffix, else by content) into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    lower = name.lower()
    if lower.endswith(".zip") or (not lower.endswith((".tar.gz", ".tgz")) and zipfile.is_zipfile(archive)):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                _check_inside(dest, info.filename)
            z.extractall(dest)
    else:
        with tarfile.open(archive, "r:*") as t:
            for m in t.getmembers():
                _check_inside(dest, m.name)
            t.extractall(dest, filter="data")


def license_texts(root: Path, limit: int = 20000) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and re.match(r"(LICEN[CS]E|COPYING|NOTICE)", p.name, re.I):
            out.append((str(p.relative_to(root)), p.read_text(encoding="utf-8", errors="replace")[:limit]))
    return out


def reset_out(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("", encoding="utf-8")


def find_binary(root: Path, platform: str) -> Path | None:
    want = "ant.exe" if platform == "windows" else "ant"
    for p in sorted(root.rglob(want)):
        if p.is_file():
            return p
    return None


def fetch(cfg: dict[str, object], url: str, sha: str, platform: str, out: Path) -> int:
    name = str(cfg.get("archive_name") or url.rsplit("/", 1)[-1])
    with tempfile.TemporaryDirectory(prefix="bf-ant-") as tmp:
        archive = Path(tmp) / name
        try:
            got = download(url, archive)
        except OSError as e:
            raise FetchError(f"Не удалось скачать ant: {e}") from e
        if got.lower() != sha.lower():
            archive.unlink(missing_ok=True)
            raise FetchError(f"SHA-256 архива ant не совпал: ожидался {sha}, получен {got}")
        stage = Path(tmp) / "unpacked"
        extract(archive, name, stage)
        exe = find_binary(stage, platform)
        if exe is None:
            raise FetchError(f"В архиве нет {'ant.exe' if platform == 'windows' else 'ant'}")
        for item in stage.iterdir():
            shutil.move(str(item), str(out / item.name))
        if exe.parent != stage:  # the binary sits in a subfolder of the archive: also put it at the top level
            shutil.copy2(out / exe.relative_to(stage), out / exe.name)
        size = archive.stat().st_size
    print(f"ant {cfg['version']}: SHA-256 {got}, {size} байт, распаковано в {out}")
    return 0


def print_hash(url: str, name: str) -> int:
    with tempfile.TemporaryDirectory(prefix="bf-ant-") as tmp:
        archive = Path(tmp) / name
        try:
            got = download(url, archive)
        except OSError as e:
            raise FetchError(f"Не удалось скачать ant: {e}") from e
        print(f"url: {url}\narchive_name: {name}\nsha256: {got}\nsize: {archive.stat().st_size}")
        stage = Path(tmp) / "unpacked"
        try:
            extract(archive, name, stage)
        except (tarfile.TarError, zipfile.BadZipFile, OSError) as e:
            print(f"Архив не распакован: {e}")
            return 0
        found = license_texts(stage)
        if not found:
            print("Файлов лицензии в архиве нет — проверьте лицензию в репозитории вручную.")
        for rel, text in found:
            print(f"--- {rel} ---\n{text}")
    print("Закрепите url, archive_name, sha256, license в tools/ci/ant.json отдельным коммитом.")
    return 0


def run(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    out = Path(args.out)
    url = resolve_url(cfg, args.url if args.print_hash else None)
    sha = cfg.get("sha256")

    if args.print_hash:
        if not url:
            print("--print-hash: задайте --url или url в ant.json (вопрос № 125)", file=sys.stderr)
            return 1
        return print_hash(url, str(cfg.get("archive_name") or url.rsplit("/", 1)[-1]))

    skip = None
    if not url or not sha:
        skip = SKIP_NOTE
    elif not license_allowed(cfg.get("license")):
        skip = f"ant не включён: лицензия {cfg.get('license')!r} не из белого списка (вопрос № 125)"
    if skip and args.strict and sha:
        # the pins were started (a hash is there) but are not complete or not allowed: a release must not go without ant
        print(f"ОШИБКА: {skip}", file=sys.stderr)
        return 1
    if skip:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {skip}")
        if not args.dry_run:
            reset_out(out)
        return 0

    assert url and isinstance(sha, str)
    note = "" if cfg.get("verified") else " (адрес не помечен verified: защищает только SHA-256)"
    print(f"ant {cfg['version']}: {url}{note}")
    if args.dry_run:
        print(f"dry-run: скачать, сверить SHA-256 {sha}, распаковать в {out}")
        return 0
    reset_out(out)
    return fetch(cfg, url, sha, args.platform, out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", choices=["windows", "linux"], default="windows")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--url", help="only with --print-hash: the archive to measure")
    ap.add_argument("--print-hash", action="store_true", help="download, print SHA-256 and licence text only")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="release: a pinned sha256 with an incomplete or disallowed rest (url, licence) is an error (exit 1); "
        "without sha256 the step is still skipped with exit 0",
    )
    ap.add_argument("--dry-run", action="store_true", help="print the plan, download and write nothing")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except FetchError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
