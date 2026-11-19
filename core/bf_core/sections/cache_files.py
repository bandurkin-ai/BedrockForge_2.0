"""The files of the cache of sections of a series (ADR 0016 §6, решение 9): `cache/sections/<key of the
series>/<key of the wireframe>.bfa` in the project folder — derived, not part of `.bfggis`.

A file holds the lines of one wireframe on the sections `first … first + count − 1` of the series (all of them
when K ≤ 500, else k ± 50 around the section of the window; the segments of a polyline): BFA1 `series_sections`

    xyz <f8      all the points, section after section (project coordinates)
    offsets <u4  lines + 1: line i is xyz[offsets[i]:offsets[i + 1]]
    closed <u1   lines
    lines <u4    count + 1: section first + j holds the lines lines[j] … lines[j + 1] − 1

with the header {kind, format, series, wireframe, axis, first, count, total, made}. A request reads one section by a
few short reads of the open file (`read_slot`: no map, a mapped file could not be replaced on Windows); a file
whose header does not match its name or is damaged is a miss. The job writes every file under a temporary
name, section after section (`Staged.write`: one section in memory; the header — `HEAD_BYTES` reserved at the
start — is written last), and moves them all in place at its end (`Staged.commit`): a cancel leaves no file
(`discard`), a reader sees an old file or a new one, whole; a job cancelled after its last tick does not put
its window over the file of the job that replaced it (`made` in the header). Not more than `LIMIT_BYTES` a
project: the folders used least recently (their time is set by a write and by a read, `touch`) go first
(`trim`, at the end of every job), never one with a fresh temporary file (a job writes into it); a temporary
file older than `STALE_S` — of a worker that was killed — goes.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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

HEAD_BYTES = 4096  # b"BFA1", u32 length, the JSON header padded with spaces: `xyz` starts right after it


class _Layout:
    """The layout of one file, section by section: `add` gives the bytes of the points of a section to write
    next (a view, no copy), `tail` the small arrays after them, `head` the first `HEAD_BYTES` — the header is
    known only at the end, so its place is reserved (JSON may end in spaces)."""

    def __init__(self, key: str, wf_key: str, axis: str, first: int, total: int, made: int) -> None:
        from .wf_sections import CACHE_FORMAT

        self.header: dict[str, Any] = {
            "kind": FILE_KIND, "format": CACHE_FORMAT, "series": key, "wireframe": wf_key, "axis": axis,
            "first": int(first), "count": 0, "total": int(total), "made": int(made),
        }  # fmt: skip
        self.points = 0
        self.offsets: list[np.ndarray] = [np.zeros(1, dtype=np.int64)]
        self.closed: list[np.ndarray] = []
        self.lines = [0]
        self.table: list[tuple[str, str, int, int]] = []

    def add(self, cut: Any) -> memoryview:
        xyz = np.ascontiguousarray(cut.xyz, dtype="<f8").reshape(-1)
        n = len(xyz) // 3
        if (self.points + n) * 3 >= U4 or self.lines[-1] + len(cut) >= U4:
            raise ValueError("too many points for one file of the cache")
        self.offsets.append(np.asarray(cut.offsets[1:], dtype=np.int64) + self.points)
        self.closed.append(np.asarray(cut.closed, dtype=np.bool_))
        self.lines.append(self.lines[-1] + len(cut))
        self.points += n
        self.header["count"] += 1
        return memoryview(xyz).cast("B")

    def tail(self) -> list[Any]:
        at = HEAD_BYTES + self.points * 24  # f8×3: a multiple of 8
        self.table = [("xyz", "<f8", 3 * self.points, HEAD_BYTES)]
        out: list[Any] = []
        arrays = [
            ("offsets", np.concatenate(self.offsets).astype("<u4"), "<u4"),
            ("closed", np.concatenate(self.closed).astype("<u1") if self.closed else np.zeros(0, "<u1"), "<u1"),
            ("lines", np.asarray(self.lines, dtype="<u4"), "<u4"),
        ]
        for name, arr, dtype in arrays:
            self.table.append((name, dtype, int(arr.size), at))
            pad = -arr.nbytes % 8
            out += [memoryview(arr).cast("B"), b"\0" * pad]
            at += arr.nbytes + pad
        return out

    def head(self) -> bytes:
        from bf_core.viz import binary

        text = json.dumps({**self.header, "arrays": self.table}, ensure_ascii=False, separators=(",", ":"))
        body = text.encode("utf-8")
        if len(body) > HEAD_BYTES - 8:
            raise ValueError("the header of a file of the cache is too long")
        return binary.MAGIC + struct.pack("<I", HEAD_BYTES - 8) + body + b" " * (HEAD_BYTES - 8 - len(body))


def encode_file(key: str, wf_key: str, axis: str, first: int, total: int, cuts: Sequence[Any], made: int = 0) -> bytes:
    """BFA1 `series_sections` of the lines `cuts` (`geo.section.Section`) of sections first, first + 1, … in one
    buffer (the job writes the file section by section, `Staged.write`)."""
    layout = _Layout(key, wf_key, axis, first, total, made)
    body = [layout.add(c) for c in cuts]
    tail = layout.tail()
    return b"".join([layout.head(), *body, *tail])


def _stamp(path: Path, key: str, wf_key: str) -> tuple[int, int, int] | None:
    """(first, count, made — the start of the job that wrote it, ns) of a file of the cache; None — none."""
    try:
        with open(path, "rb") as f:
            header = _header(f, key, wf_key)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if header is None:
        return None
    made = header.get("made")
    return int(header["first"]), int(header["count"]), int(made) if isinstance(made, int) else 0


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


@dataclass
class _File:
    tmp: Path
    final: Path
    wf_key: str
    first: int
    count: int


class Staged:
    """The files a job writes for one series, under temporary names until `commit`.

    `made` — when the job began (ns, in the header of its files): a job cancelled after its last tick still
    commits, and its window must not go over the file of the job that replaced it (`commit`)."""

    def __init__(self, folder: Path | str, key: str, tag: str | int | None = None) -> None:
        self.folder = Path(folder)
        self.key = key
        self.dir = series_dir(folder, key)
        self.tag = f"{tag if tag is not None else 'job'}-{uuid.uuid4().hex[:8]}"
        self.made = time.time_ns()
        self.files: list[_File] = []
        self.made_dirs: list[Path] = []  # the folders this job created, deepest last
        self.keys: set[str] = set()  # of the wireframes written
        self.bytes = 0

    def _folders(self) -> None:
        for d in (self.folder / "cache", root(self.folder), self.dir):
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)
                self.made_dirs.append(d)

    def _open(self, tmp: Path) -> Any:
        self._folders()
        try:
            return open(tmp, "wb")
        except FileNotFoundError:  # `trim` of another job took the folder just now
            self._folders()
            return open(tmp, "wb")

    def write(self, wf_key: str, axis: str, first: int, total: int, cuts: Iterable[Any]) -> None:
        """The file of a wireframe from its lines on the sections first, first + 1, … — computed one by one by
        `cuts` (a generator): one section in memory at a time. An error (a cancel among them) leaves no file."""
        if wf_key in self.keys:
            raise ValueError(f"the file of {wf_key} is written already")
        tmp = self.dir / f"{wf_key}.{self.tag}.tmp"
        layout = _Layout(self.key, wf_key, axis, first, total, self.made)
        try:
            with self._open(tmp) as f:
                f.write(b"\0" * HEAD_BYTES)
                for cut in cuts:
                    f.write(layout.add(cut))
                for part in layout.tail():
                    f.write(part)
                size = f.tell()
                f.seek(0)
                f.write(layout.head())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            close = getattr(cuts, "close", None)
            if close is not None:
                close()  # a generator over mapped blobs lets them go now
        self.files.append(_File(tmp, self.dir / f"{wf_key}.bfa", wf_key, int(first), int(layout.header["count"])))
        self.keys.add(wf_key)
        self.bytes += size

    def _keep_old(self, f: _File) -> bool:
        """The file in place stays: a later job wrote it (its `made` is later), or it holds this window."""
        have = _stamp(f.final, self.key, f.wf_key)
        if have is None:
            return False
        first, count, made = have
        return made > self.made or (first <= f.first and f.first + f.count <= first + count)

    def commit(self) -> list[str]:
        """Every file under its own name: no tick in between, so a cancel comes before or after all of them.
        A file in place that a later job wrote, or that holds this window, stays. Returns the wireframes whose
        files are not in place because their folder went meanwhile (`trim` of another job): not an error —
        those sections are just not cached."""
        lost = []
        for f in self.files:
            if self._keep_old(f):
                f.tmp.unlink(missing_ok=True)
                continue
            try:
                _replace(f.tmp, f.final)
            except FileNotFoundError:
                f.tmp.unlink(missing_ok=True)
                lost.append(f.wf_key)
        self.files = []
        self.made_dirs = []
        return lost

    def discard(self) -> None:
        """Nothing of this job stays: its temporary files and the folders it made (when empty)."""
        for f in self.files:
            f.tmp.unlink(missing_ok=True)
        self.files = []
        for d in reversed(self.made_dirs):
            try:
                d.rmdir()
            except OSError:
                break  # not empty: somebody else's files are there
        self.made_dirs = []


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
    busy: set[str] = set()
    for d in dirs:
        total = 0
        try:
            for p in d.iterdir():
                st = p.stat()
                if p.suffix == ".tmp":
                    if now - st.st_mtime > STALE_S:
                        p.unlink(missing_ok=True)
                        continue
                    busy.add(d.name)  # a job is writing into it: its commit would find no folder
                total += st.st_size
            found.append((d.stat().st_mtime, d.name, total))
        except OSError:
            continue
    used = sum(t for _m, _n, t in found)
    gone = []
    for _mtime, name, total in sorted(found):
        if used <= limit:
            break
        if name in kept or name in busy:
            continue
        shutil.rmtree(base / name, ignore_errors=True)
        used -= total
        gone.append(name)
    return gone
