"""The files of the cache of sections of a series (ADR 0016 §6, решение 9): `cache/sections/<key of the
series>/<key of the wireframe>.bfa` in the project folder — derived, not part of `.bfggis`.

A file holds the lines of one wireframe on the sections `first … first + count − 1` of the series (all of them
when K ≤ 500, else k ± 50 around the section of the window; the segments of a polyline): BFA1 `series_sections`

    xyz <f8      all the points, section after section (project coordinates)
    offsets <u4  lines + 1: line i is xyz[offsets[i]:offsets[i + 1]]
    closed <u1   lines
    lines <u4    count + 1: section first + j holds the lines lines[j] … lines[j + 1] − 1

with the header {kind, format, series, wireframe, axis, first, count, total}. A request reads one section by a
few short reads of the open file (`read_slot`: no map, a mapped file could not be replaced on Windows); a file
whose header does not match its name or is damaged is a miss. The job writes every file under a temporary
name and moves them all in place at its end (`Staged`): a cancel leaves no file (`discard`), a reader sees an
old file or a new one, whole. Not more than `LIMIT_BYTES` a project: the folders used least recently (their
time is set by a write and by a read, `touch`) go first (`trim`, at the end of every job; a temporary file
older than `STALE_S` — of a worker that was killed — goes too).
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

FILE_KIND = "series_sections"
LIMIT_BYTES = 2 << 30  # a project (ADR 0016 решение 9)
STALE_S = 3600.0  # a temporary file this old is nobody's
MAX_HEADER = 1 << 16
U4 = 1 << 32


def root(folder: Path | str) -> Path:
    return Path(folder) / "cache" / "sections"


def series_dir(folder: Path | str, key: str) -> Path:
    return root(folder) / key


def file_path(folder: Path | str, key: str, wf_key: str) -> Path:
    return series_dir(folder, key) / f"{wf_key}.bfa"


# --- reading -----------------------------------------------------------------------------------------------


def _header(f: Any, key: str, wf_key: str) -> dict[str, Any] | None:
    from bf_core.viz import binary

    from .wf_sections import CACHE_FORMAT

    head = f.read(8)
    if len(head) < 8 or head[:4] != binary.MAGIC:
        return None
    (n,) = struct.unpack("<I", head[4:8])
    if n > MAX_HEADER:
        return None
    header = json.loads(f.read(n).decode("utf-8"))
    ok = (
        isinstance(header, dict)
        and header.get("kind") == FILE_KIND
        and header.get("format") == CACHE_FORMAT
        and header.get("series") == key
        and header.get("wireframe") == wf_key
        and isinstance(header.get("first"), int)
        and isinstance(header.get("count"), int)
    )
    return header if ok else None


def _part(f: Any, table: dict[str, tuple[str, int, int]], name: str, start: int, n: int) -> np.ndarray:
    dtype, count, offset = table[name]
    dt = np.dtype(dtype)
    if start < 0 or n < 0 or start + n > count:
        raise ValueError(f"{name}: {start}+{n} of {count}")
    f.seek(offset + start * dt.itemsize)
    data = f.read(n * dt.itemsize)
    if len(data) != n * dt.itemsize:
        raise ValueError(f"{name}: the file is short")
    return np.frombuffer(data, dtype=dt)


def read_slot(folder: Path | str, key: str, wf_key: str, index: int) -> Any:
    """The `geo.section.Section` of section `index` from the file of the wireframe; None — no file, another
    range, a damaged file."""
    from bf_core.geo.section import Section

    try:
        with open(file_path(folder, key, wf_key), "rb") as f:
            header = _header(f, key, wf_key)
            if header is None or not header["first"] <= index < header["first"] + header["count"]:
                return None
            table = {str(a[0]): (str(a[1]), int(a[2]), int(a[3])) for a in header["arrays"]}
            lines = _part(f, table, "lines", index - header["first"], 2)
            a, b = int(lines[0]), int(lines[1])
            offs = _part(f, table, "offsets", a, b - a + 1).astype(np.int64)
            closed = _part(f, table, "closed", a, b - a).astype(np.bool_)
            p0, p1 = int(offs[0]), int(offs[-1])
            xyz = _part(f, table, "xyz", 3 * p0, 3 * (p1 - p0)).reshape(-1, 3)
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeDecodeError):
        return None
    if b < a or p1 < p0 or (np.diff(offs) < 0).any():
        return None
    return Section(xyz, offs - p0, closed)


def coverage(folder: Path | str, key: str, wf_key: str) -> tuple[int, int] | None:
    """(first, count) of the sections the file of the wireframe holds; None — no file (or a damaged one)."""
    try:
        with open(file_path(folder, key, wf_key), "rb") as f:
            header = _header(f, key, wf_key)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None if header is None else (int(header["first"]), int(header["count"]))


def covers(folder: Path | str, key: str, wf_key: str, first: int, count: int) -> bool:
    have = coverage(folder, key, wf_key)
    return have is not None and have[0] <= first and first + count <= have[0] + have[1]


def size(folder: Path | str, key: str) -> int:
    """Bytes of the files of a series."""
    try:
        return sum(p.stat().st_size for p in series_dir(folder, key).iterdir() if p.is_file())
    except OSError:
        return 0


def touch(folder: Path | str, key: str) -> None:
    """The folder of the series was read: it is the last to go (`trim`)."""
    try:
        os.utime(series_dir(folder, key))
    except OSError:
        pass


# --- writing -----------------------------------------------------------------------------------------------


def encode_file(key: str, wf_key: str, axis: str, first: int, total: int, cuts: Sequence[Any]) -> bytes:
    """BFA1 `series_sections` of the lines `cuts` (`geo.section.Section`) of sections first, first + 1, …"""
    from bf_core.viz import binary

    from .wf_sections import CACHE_FORMAT

    counts = np.array([len(c) for c in cuts], dtype=np.int64)
    points = np.array([len(c.xyz) for c in cuts], dtype=np.int64)
    starts = np.concatenate([[0], np.cumsum(points)]).astype(np.int64)
    if int(starts[-1]) * 3 >= U4 or int(counts.sum()) >= U4:
        raise ValueError("too many points for one file of the cache")
    xyz = np.concatenate([c.xyz for c in cuts]) if cuts else np.zeros((0, 3))
    offsets = np.concatenate([[0], *[c.offsets[1:] + starts[i] for i, c in enumerate(cuts)]]).astype(np.int64)
    closed = np.concatenate([c.closed for c in cuts]) if cuts else np.zeros(0, dtype=np.bool_)
    lines = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    header = {
        "kind": FILE_KIND,
        "format": CACHE_FORMAT,
        "series": key,
        "wireframe": wf_key,
        "axis": axis,
        "first": int(first),
        "count": len(cuts),
        "total": int(total),
    }
    arrays = [("xyz", xyz, "<f8"), ("offsets", offsets, "<u4"), ("closed", closed, "<u1"), ("lines", lines, "<u4")]
    return binary.encode(header, arrays)


def _replace(tmp: Path, final: Path, tries: int = 20) -> None:
    """os.replace, again for a while when a reader holds the old file open (Windows refuses then)."""
    for i in range(tries):
        try:
            os.replace(tmp, final)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05)


class Staged:
    """The files a job writes for one series, under temporary names until `commit`."""

    def __init__(self, folder: Path | str, key: str, tag: str | int | None = None) -> None:
        self.folder = Path(folder)
        self.dir = series_dir(folder, key)
        self.tag = f"{tag if tag is not None else 'job'}-{uuid.uuid4().hex[:8]}"
        self.files: list[tuple[Path, Path]] = []
        self.made: list[Path] = []  # the folders this job created, deepest last
        self.bytes = 0

    def _folders(self) -> None:
        for d in (self.folder / "cache", root(self.folder), self.dir):
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)
                self.made.append(d)

    def add(self, wf_key: str, data: bytes) -> None:
        self._folders()
        tmp = self.dir / f"{wf_key}.{self.tag}.tmp"
        self.files.append((tmp, self.dir / f"{wf_key}.bfa"))
        tmp.write_bytes(data)
        self.bytes += len(data)

    def commit(self) -> None:
        """Every file under its own name: no tick in between, so a cancel comes before or after all of them."""
        for tmp, final in self.files:
            _replace(tmp, final)
        self.files = []
        self.made = []

    def discard(self) -> None:
        """Nothing of this job stays: its temporary files and the folders it made (when empty)."""
        for tmp, _final in self.files:
            tmp.unlink(missing_ok=True)
        self.files = []
        for d in reversed(self.made):
            try:
                d.rmdir()
            except OSError:
                break  # not empty: somebody else's files are there
        self.made = []


# --- the limit of a project --------------------------------------------------------------------------------


def trim(folder: Path | str, keep: Iterable[str] = (), limit: int = LIMIT_BYTES) -> list[str]:
    """Not more than `limit` bytes of series: the folders read or written least recently go first (`keep` —
    never); a temporary file older than `STALE_S` goes too. Returns the keys removed."""
    base = root(folder)
    kept = set(keep)
    now = time.time()
    found: list[tuple[float, str, int]] = []
    try:
        dirs = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return []
    for d in dirs:
        total = 0
        try:
            for p in d.iterdir():
                st = p.stat()
                if p.suffix == ".tmp" and now - st.st_mtime > STALE_S:
                    p.unlink(missing_ok=True)
                    continue
                total += st.st_size
            found.append((d.stat().st_mtime, d.name, total))
        except OSError:
            continue
    used = sum(t for _m, _n, t in found)
    gone = []
    for _mtime, name, total in sorted(found):
        if used <= limit:
            break
        if name in kept:
            continue
        shutil.rmtree(base / name, ignore_errors=True)
        used -= total
        gone.append(name)
    return gone
