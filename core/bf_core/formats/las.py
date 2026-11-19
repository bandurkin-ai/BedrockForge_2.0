"""LAS 1.2–1.4 point clouds: the point records of formats 0–10 as arrays, chunk by chunk (ТЗ 7.35 Д-22, 9.12;
ADR 0014 §3). The format is ASPRS LAS 1.4 R15. No laspy: a record is a packed structure of a fixed length, read
through a structured dtype of numpy. The header, VLR / EVLR, the CRS and the refusals are in `las_header`.

`open_las(path)` → `LasFile`: the header is read and checked (`header: LasHeader`), the points are not touched.
`LasFile.chunks(progress, every=1, chunk=1_000_000)` yields `LasChunk`s in the order of the file:
  * `xyz` `<f8 n×3` — `X · scale + offset` in doubles as the specification says (one product, one sum);
  * `cls` `u1` — the class: five bits in formats 0–5, the whole byte 0–255 in formats 6–10;
  * `ret` `u1` — the return number in the low four bits, the number of returns in the high four;
  * `seq` `<u4` — the number of the record in the file (`every` = k takes the records 0, k, 2k, …).
  The records of a chunk are mapped (`numpy.memmap`) only while they are decoded: memory is the arrays of one
  chunk, neither a map nor a handle is held between chunks (Windows locks a mapped file; mapped pages count as
  memory of the process), and a pass can be repeated — a cloud is built in two. `progress(fraction, message)` is
  called between chunks and may raise to cancel. After a whole pass `flags` (withheld, overlap) and `classes`
  are known and `warnings` tell of them.
`LasFile.read()` — the whole cloud as one chunk; `records(start, stop)` — raw records (a copy).
`inspect_las(path)` → `LasInspect` for the wizard: the header, the classes by a sample of 1 млн records taken in
  blocks all over the file, the extent, what is not loaded, «every k-th record» for a big cloud.
Not read: intensity, RGB / NIR, GPS time, scan angle, user data, source id, wave packets, extra bytes.
A cloud of more than `SUGGEST_EVERY` points is offered «every k-th record»; more than `MAX_POINTS` after the
thinning is refused (`too_many_points`), as is a file of more than 2³² records (`seq` is a uint32).
Error codes: those of `las_header` and too_many_points.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.las_header import (
    FORMAT_LENGTH,
    MAX_RECORDS,
    MAX_WKT,
    LasHeader,
    LasVlr,
    fail,
    n_text,
    point_dtype,
    read_header,
)
from bf_core.formats.table import Progress, no_progress

CHUNK = 1_000_000  # records decoded at a time
SAMPLE, SAMPLE_BLOCKS = 1_000_000, 64  # `inspect_las`: records looked at, in blocks all over the file
SUGGEST_EVERY = 50_000_000  # a bigger cloud is offered «every k-th record»
MAX_POINTS = 200_000_000  # … and a cloud bigger than this after the thinning is refused
_FLAGS = {"withheld": "с признаком «исключена» (withheld)", "overlap": "из перекрытия полос съёмки (overlap)"}


@dataclass(frozen=True)
class LasChunk:
    start: int  # the number of the first record of the chunk in the file
    seq: np.ndarray  # <u4 n: the numbers of the records
    xyz: np.ndarray  # <f8 n×3
    cls: np.ndarray  # u1 n: the ASPRS class
    ret: np.ndarray  # u1 n: the return number | the number of returns << 4

    def __len__(self) -> int:
        return len(self.seq)

    def columns(self) -> dict[str, np.ndarray]:
        """The arrays by the roles of the blobs of a cloud."""
        return {"xyz": self.xyz, "seq": self.seq, "class": self.cls, "ret": self.ret}


def _too_many(count: int) -> FormatError:
    return fail(
        "too_many_points",
        f"В облаке {n_text(count)} точек: больше {n_text(MAX_POINTS)} не загружается.",
        "Загрузите каждую k-ю запись (поле «каждая k-я запись» мастера) или разделите облако на части.",
    )


@contextmanager
def map_records(path: Path, dtype: np.dtype, offset: int, count: int) -> Iterator[np.ndarray]:
    """`count` records of a file from the byte `offset` as a plain array, mapped while the block lasts. Nothing
    made inside may refer to the map afterwards: it is closed at once, not when the collector comes — on Windows
    an open map keeps the file locked."""
    try:
        mapped: Any = np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=(count,))
    except ValueError as exc:  # the file became shorter after its header was read
        raise fail(
            "truncated",
            "Файл LAS изменился во время чтения: записей в нём стало меньше.",
            "Дождитесь окончания записи файла и загрузите его снова.",
        ) from exc
    try:
        yield mapped.view(np.ndarray)
    finally:
        mm = mapped._mmap
        del mapped
        try:
            mm.close()
        except BufferError:  # an error inside the block keeps views alive: the map goes with them
            pass


def classes_of(rec: np.ndarray, point_format: int) -> tuple[np.ndarray, int, int]:
    """The class of each record (a new array) and how many records are withheld and of an overlap."""
    if point_format <= 5:  # class 0–31, then synthetic, key-point, withheld; an overlap is the class 12
        byte = rec["classification"]
        cls = byte & 31
        return cls, int(np.count_nonzero(byte & 0x80)), int(np.count_nonzero(cls == 12))
    flags = rec["flags"]  # synthetic, key-point, withheld, overlap, …
    return rec["classification"].copy(), int(np.count_nonzero(flags & 4)), int(np.count_nonzero(flags & 8))


def _decode(rec: np.ndarray, h: LasHeader) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """Records (a view of the map) → new arrays: nothing of the map is referred to after the return."""
    xyz = np.empty((len(rec), 3), dtype="<f8")
    for k, name in enumerate("XYZ"):
        column = xyz[:, k]
        np.multiply(rec[name], h.scale[k], out=column)  # int32 → double is exact; then one product and one sum
        column += h.offset[k]
    cls, withheld, overlap = classes_of(rec, h.point_format)
    returns = rec["returns"]
    # formats 0–5: three bits of the return number, three of the number of returns; 6–10: four and four
    ret = (returns & 7) | ((returns << 1) & 0x70) if h.point_format <= 5 else returns.copy()
    return xyz, cls, ret, withheld, overlap


class LasFile:
    """An opened LAS file: its header and passes over its points (see the module docstring)."""

    def __init__(self, path: Path, header: LasHeader) -> None:
        self.path, self.header = path, header
        self.dtype = point_dtype(header.point_format, header.record_length)
        self.flags: dict[str, int] | None = None  # of the last whole pass: {"withheld", "overlap"}
        self.classes: dict[int, int] | None = None  # … and its classes: code → points

    @property
    def point_count(self) -> int:
        return self.header.point_count

    @property
    def warnings(self) -> list[str]:
        """Those of the header and, after a whole pass, of the flags of its points."""
        out = list(self.header.warnings)
        for key, what in _FLAGS.items():
            if self.flags and self.flags[key]:
                out.append(f"Точек {what}: {n_text(self.flags[key])} — они загружены вместе с остальными.")
        return out

    def count(self, every: int = 1) -> int:
        """How many records a pass with `every` gives."""
        return -(-self.header.point_count // every)

    def map(self, a: int, b: int) -> AbstractContextManager[np.ndarray]:
        """The raw records `a:b` for the time of a `with` block (see `map_records`)."""
        h = self.header
        return map_records(self.path, self.dtype, h.offset_to_points + a * h.record_length, b - a)

    def records(self, start: int = 0, stop: int | None = None) -> np.ndarray:
        """Raw records `start:stop` with every field of the format (a copy)."""
        stop = self.header.point_count if stop is None else min(stop, self.header.point_count)
        if not 0 <= start < stop:
            return np.empty(0, dtype=self.dtype)
        with self.map(start, stop) as rec:
            return rec.copy()

    def chunks(
        self, progress: Progress | None = None, *, every: int = 1, chunk: int | None = None
    ) -> Iterator[LasChunk]:
        """The points of the file chunk by chunk (`chunk` records of the file each, `CHUNK` by default), every
        `every`-th record. `progress` may raise to cancel."""
        size = CHUNK if chunk is None else chunk
        if every < 1 or size < 1:
            raise ValueError("every and chunk: positive numbers are expected")
        if self.count(every) > MAX_POINTS:
            raise _too_many(self.count(every))
        return self._chunks(progress or no_progress, every, size)

    def _chunks(self, tick: Progress, every: int, size: int) -> Iterator[LasChunk]:
        h, n = self.header, self.header.point_count
        withheld = overlap = 0
        classes = np.zeros(256, dtype=np.int64)
        for a in range(0, n, size):
            b = min(n, a + size)
            first = -(-a // every) * every
            tick(a / n, f"Чтение точек: {n_text(a)} из {n_text(n)}")
            if first >= b:
                continue
            with self.map(a, b) as rec:
                xyz, cls, ret, hidden, lapped = _decode(rec[first - a :: every], h)
                del rec
            withheld, overlap = withheld + hidden, overlap + lapped
            classes += np.bincount(cls, minlength=256)
            yield LasChunk(first, np.arange(first, b, every, dtype="<u4"), xyz, cls, ret)
        self.flags = {"withheld": withheld, "overlap": overlap}
        self.classes = {int(k): int(classes[k]) for k in np.flatnonzero(classes)}
        tick(1.0, f"Прочитано точек: {n_text(self.count(every))}")

    def read(self, progress: Progress | None = None, *, every: int = 1) -> LasChunk:
        """The whole cloud in memory as one chunk (30 bytes a point)."""
        parts = self.chunks(progress, every=every)
        total = self.count(every)
        xyz, seq = np.empty((total, 3), dtype="<f8"), np.empty(total, dtype="<u4")
        cls, ret = np.empty(total, dtype=np.uint8), np.empty(total, dtype=np.uint8)
        at = 0
        for part in parts:
            stop = at + len(part)
            xyz[at:stop], seq[at:stop], cls[at:stop], ret[at:stop] = part.xyz, part.seq, part.cls, part.ret
            at = stop
        return LasChunk(0, seq, xyz, cls, ret)


def open_las(path: str | Path) -> LasFile:
    """Open a LAS file: read and check its header. Raises `FormatError` (see the module docstring)."""
    path = Path(path)
    header = read_header(path)
    if header.point_count > MAX_RECORDS:
        raise _too_many(header.point_count)
    return LasFile(path, header)


# ---------------------------------------------------------------- the wizard


@dataclass
class LasInspect:
    header: LasHeader
    classes: dict[int, int]  # code → points among the `sampled` records
    sampled: int
    bbox: tuple[float, ...] | None  # x, y, z min, x, y, z max
    bbox_exact: bool  # of the header or of all the records, not of a sample
    not_loaded: list[str]
    every: int  # «every k-th record» that brings the cloud under `SUGGEST_EVERY` points
    summary: str  # one line for the wizard
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        h = self.header
        crs = None
        if h.crs_text():
            crs = {"wkt": h.wkt, "epsg": h.epsg, "geographic": h.geographic, "name": h.crs_name()}
        return {
            "format": "las", "version": f"{h.version[0]}.{h.version[1]}", "point_format": h.point_format,
            "record_length": h.record_length, "point_count": h.point_count, "declared_count": h.declared_count,
            "scale": list(h.scale), "offset": list(h.offset), "bbox": None if self.bbox is None else list(self.bbox),
            "bbox_exact": self.bbox_exact, "crs": crs, "classes": {str(k): v for k, v in self.classes.items()},
            "sampled": self.sampled, "not_loaded": self.not_loaded, "every": self.every, "software": h.software,
            "system_id": h.system_id, "created": list(h.created), "summary": self.summary, "warnings": self.warnings,
        }  # fmt: skip


def _sample(cloud: LasFile, sample: int) -> tuple[dict[int, int], int, np.ndarray | None]:
    """Classes and the extent (2×3 integers of the records) of `sample` records in blocks spread over the file."""
    n = cloud.point_count
    if sample <= 0:
        return {}, 0, None
    blocks = 1 if n <= sample else min(SAMPLE_BLOCKS, sample)
    size = n if n <= sample else sample // blocks
    classes = np.zeros(256, dtype=np.int64)
    box = np.array([[np.iinfo(np.int32).max] * 3, [np.iinfo(np.int32).min] * 3], dtype=np.int64)
    for k in range(blocks):
        start = (n - size) * k // max(1, blocks - 1)
        with cloud.map(start, start + size) as rec:
            classes += np.bincount(classes_of(rec, cloud.header.point_format)[0], minlength=256)
            for axis, name in enumerate("XYZ"):
                box[0, axis] = min(box[0, axis], rec[name].min())
                box[1, axis] = max(box[1, axis], rec[name].max())
            del rec
    return {int(k): int(classes[k]) for k in np.flatnonzero(classes)}, blocks * size, box


def inspect_las(path: str | Path, sample: int | None = None) -> LasInspect:
    """What is in a LAS file, for the wizard: bounded in time and memory (`sample` records are looked at,
    `SAMPLE` by default)."""
    cloud = open_las(path)
    h = cloud.header
    classes, sampled, box = _sample(cloud, SAMPLE if sample is None else sample)
    warnings = list(h.warnings)
    bbox: tuple[float, ...] | None = h.bbox
    exact = bbox is not None
    if box is not None:
        ends = np.sort(box * np.array(h.scale) + np.array(h.offset), axis=0)  # a negative scale swaps the ends
        slack = np.abs(h.scale)
        if bbox is None or (ends[0] < np.array(bbox[:3]) - slack).any() or (ends[1] > np.array(bbox[3:]) + slack).any():
            what = "В заголовке LAS нет охвата облака" if bbox is None else "Охват в заголовке LAS не тот, что у точек"
            exact = sampled == h.point_count
            warnings.append(f"{what}: показан охват по {'точкам' if exact else 'выборке точек'}.")
            bbox = tuple(float(v) for v in ends.reshape(-1))
    every = max(1, -(-h.point_count // SUGGEST_EVERY))
    if every > 1:
        warnings.append(f"В облаке {n_text(h.point_count)} точек: предлагается загрузить каждую {every}-ю запись.")
    name = h.crs_name()
    summary = (
        f"LAS {h.version[0]}.{h.version[1]}, формат точек {h.point_format}: точек {n_text(h.point_count)}, "
        + (f"СК: {name}" if name else "СК не указана")
    )
    return LasInspect(h, classes, sampled, bbox, exact, h.not_loaded(), every, summary, warnings)


__all__ = [
    "CHUNK", "FORMAT_LENGTH", "MAX_POINTS", "MAX_WKT", "SUGGEST_EVERY", "LasChunk", "LasFile", "LasHeader",
    "LasInspect", "LasVlr", "classes_of", "inspect_las", "map_records", "open_las", "point_dtype", "read_header",
]  # fmt: skip
