"""A volume of OMF v1 as a regular block model of BF2, streamed (ТЗ 7.24, 5.3; ADR 0017 §2–3).

    volume_grid(vol)                        the BF2 grid of a VolumeElement and how its axes lie
    estimate(f, vol)                        {cells, fields, memory, disk} — bytes, before reading
    read_volume(f, vol, make_sink, …)       the occupied cells into a sink (`blocks.store.CellsWriter`) → VolumeRead

Axes. Every grid axis u, v, w lies along +X, +Y, +Z or against them (a permutation and signs, within 1e-9 —
`omf_file.placement`); a rotated grid is not loaded (the composition says so). The BF2 grid has its corner at the
minimum corner (`origin` of the geometry + origin of the project, float64; along a negative axis the origin minus
the sum of its steps), `counts` along X, Y, Z, `size` when the steps of an axis are all equal (bit for bit), else
`tensors` (the steps along +X, +Y, +Z: reversed for a negative axis). Cell numbers: `idx = i + nx·(j + ny·k)`
(`blocks/schema.py`); the OMF number of a cell is `iu + nu·(iv + nv·iw)` (Fortran, u fastest).

Fields. Cell data of the element (ScalarData — float64, NaN empty; MappedData — the codes of its first legend
with labels; StringData, DateTimeData — codes of the texts, −1 empty), named as `blocks.schema.field_names` does,
with «Единицы: …» of a description as the unit. A cell with no value in any field is not stored; without fields
every cell of the grid is (a warning).

Streaming. When the axes are +X, +Y, +Z in this order the OMF order is the BF2 order: every field is inflated in
turn by portions (`portion` cells) and the occupied cells of a portion go to the sink at once — no temporary
files. Otherwise each field is inflated once into a temporary file of `tmp_dir` (float64 / int32: n × 8 or 4
bytes, free space checked first → `disk_full`), the files are mapped (`mmap`, read only) and the BF2 cells are
taken in ascending `idx` by portions, their OMF numbers computed — nothing is sorted. Memory: a few portions of
every field (and the mapped pages the system can drop); the maps are closed before their files are removed
(Windows, ТЗ 17.2.6). A cancel (`progress` raising, also inside a block: every ≤ `portion` bytes) or a failure
leaves no temporary file and discards the sink. `MemoryError` → `out_of_memory`, a full disk → `disk_full`.
"""

from __future__ import annotations

import errno
import math
import mmap
import shutil
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.omf_file import Block, OmfFile, damaged, n_text, out_of_memory, placement, tensor
from bf_core.formats.omf_read import OmfVolume, legend_labels, text_codes
from bf_core.formats.table import Progress, no_progress

PORTION = 1 << 20  # cells a portion (= a row group of `blocks.schema.ROW_GROUP`)
DISK_MARGIN = 64 << 20
_IDENTITY = [(0, 1.0), (1, 1.0), (2, 1.0)]
HINT_DISK = "Освободите место на диске (временная папка проекта) или разделите модель на части."


class CellsSink(Protocol):
    def write(self, idx: Any, values: Mapping[str, Any]) -> None: ...

    def finish(self) -> Any: ...

    def discard(self) -> None: ...


SinkFactory = Callable[[dict[str, Any], list[dict[str, Any]]], CellsSink]


def disk_full(need: int) -> FormatError:
    return FormatError(
        "disk_full", f"Не хватает места на диске для временных файлов модели: нужно ≈ {_gb(need)} ГБ.", HINT_DISK
    )


def _gb(n: int) -> str:
    return f"{n / 2**30:.1f}".replace(".", ",")


@dataclass
class VolumeGrid:
    grid: dict[str, Any]  # meta.grid of `blocks/schema.py` (without order checks: CellsWriter checks)
    placed: list[tuple[int, float]]  # (coordinate axis, sign) of u, v, w
    omf_counts: list[int]  # nu, nv, nw

    @property
    def cells(self) -> int:
        return self.omf_counts[0] * self.omf_counts[1] * self.omf_counts[2]

    @property
    def identity(self) -> bool:
        return self.placed == _IDENTITY


@dataclass
class VolumeRead:
    """What `read_volume` gives: the staged cells (`sink.finish()`), the grid, the fields with their stats when the
    sink counts them, the occupied cells, warnings for the log and `meta.warnings`, the source and the colour."""

    name: str
    grid: dict[str, Any]
    fields: list[dict[str, Any]]
    cells: int
    staged: Any
    source: dict[str, Any]
    color: str | None = None
    warnings: list[str] = field(default_factory=list)


def volume_grid(vol: OmfVolume) -> VolumeGrid:
    g, label = vol.geometry, vol.entry.label
    tensors = [tensor(g.get(f"tensor_{a}"), f"{label}: шаги {a}") for a in "uvw"]
    placed = placement(g, label)
    if placed is None:
        raise FormatError("unsupported", f"{label}: модель повёрнута — оси сетки не вдоль X, Y, Z.",
                          "Сохраните модель без поворота или загрузите другой объём.")  # fmt: skip
    counts, corner = [0, 0, 0], [0.0, 0.0, 0.0]
    steps: list[list[float]] = [[], [], []]
    for a in range(3):
        k, sign = placed[a]
        t = tensors[a]
        counts[k] = len(t)
        steps[k] = t if sign > 0 else t[::-1]
        corner[k] = float(vol.origin[k]) if sign > 0 else float(vol.origin[k]) - math.fsum(t)
    grid: dict[str, Any] = {"corner": corner, "counts": counts}
    if all(all(s == st[0] for s in st) for st in steps):
        grid["size"] = [st[0] for st in steps]
    else:
        grid["tensors"] = {"x": steps[0], "y": steps[1], "z": steps[2]}
    grid["order"] = "F"
    return VolumeGrid(grid, placed, [len(t) for t in tensors])


def omf_numbers(idx: np.ndarray, vg: VolumeGrid) -> np.ndarray:
    """OMF cell numbers (iu + nu·(iv + nv·iw)) of BF2 cells `idx` (int64)."""
    nx, ny = int(vg.grid["counts"][0]), int(vg.grid["counts"][1])
    coord = [idx % nx, (idx // nx) % ny, idx // (nx * ny)]
    ia = []
    for a in range(3):
        k, sign = vg.placed[a]
        ia.append(coord[k] if sign > 0 else (vg.omf_counts[a] - 1) - coord[k])
    nu, nv = vg.omf_counts[0], vg.omf_counts[1]
    return ia[0] + nu * (ia[1] + nv * ia[2])  # type: ignore[no-any-return]


# --- fields -----------------------------------------------------------------------------------------------


@dataclass
class _Field:
    meta: dict[str, Any]  # {name, kind, unit, labels?}
    values: Callable[[int], Iterator[np.ndarray]]  # portion bytes → float64 / int32 arrays in OMF order
    block: Block | None
    itemsize: int


def _decoded(f: OmfFile, block: Block, what: str, n: int, convert: Callable[[np.ndarray], np.ndarray],
             portion: int) -> Iterator[np.ndarray]:  # fmt: skip
    rest = b""
    for part in f.inflate(block, what, rows=n, portion=portion):
        buf = rest + part if rest else part
        k = len(buf) // block.itemsize
        if k:
            yield convert(np.frombuffer(buf, dtype=block.dtype, count=k))
        rest = buf[k * block.itemsize :]


def _numbers(a: np.ndarray) -> np.ndarray:
    return a.astype(np.float64)


def _mapped(labels: int) -> Callable[[np.ndarray], np.ndarray]:
    def convert(idx: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            valid = (idx >= 0) & (idx < labels)
        codes = np.full(len(idx), -1, dtype=np.int32)
        codes[valid] = idx[valid].astype(np.int32)
        return codes

    return convert


def _slices(a: np.ndarray, cells: int) -> Iterator[np.ndarray]:
    for s in range(0, len(a), cells):
        yield a[s : s + cells]


def _field(f: OmfFile, data: dict[str, Any], name: str, unit: str | None, n: int, what: str) -> _Field | str:
    """A field of cell data, or the reason it is not loaded."""
    from bf_core.blocks import schema as bs

    cls = data.get("__class__")
    if cls in ("StringData", "DateTimeData"):
        values = f.array(data.get("array"), what, rows=n)
        if isinstance(values, np.ndarray):
            values = values.tolist()
        codes, labels = text_codes(values)
        if len(labels) > bs.MAX_LABELS:
            return f"больше {n_text(bs.MAX_LABELS)} разных значений"
        meta = {"name": name, "kind": bs.CATEGORY, "unit": unit, "labels": labels}
        return _Field(meta, lambda portion: _slices(codes, max(portion // 4, 1)), None, 4)
    ref = f.block_of(data.get("array"), what)
    if not isinstance(ref, Block) or ref.width != 1:
        raise damaged(f"{what}: значения ячеек должны быть одним столбцом чисел (ScalarArray).")
    block = ref
    if cls == "ScalarData":
        meta = {"name": name, "kind": bs.NUMBER, "unit": unit}
        return _Field(meta, lambda portion: _decoded(f, block, what, n, _numbers, portion), block, 8)
    labels_ = legend_labels(f, data, what)
    if labels_ is None:
        return "у данных нет легенды с подписями"
    if len(labels_) > bs.MAX_LABELS:
        return f"больше {n_text(bs.MAX_LABELS)} подписей"
    if block.dtype[1] == "b":
        raise damaged(f"{what}: номера категорий должны быть числами.")
    meta = {"name": name, "kind": bs.CATEGORY, "unit": unit, "labels": labels_}
    convert = _mapped(len(labels_))
    return _Field(meta, lambda portion: _decoded(f, block, what, n, convert, portion), block, 4)


def _fields(f: OmfFile, vol: OmfVolume, n: int) -> tuple[list[_Field], list[str]]:
    from bf_core.blocks import schema as bs

    label = vol.entry.label
    data = vol.data[: bs.MAX_FIELDS]
    notes = [f"{label}: полей больше {bs.MAX_FIELDS} — загружены первые {bs.MAX_FIELDS}"] * (len(vol.data) > len(data))
    names = bs.field_names([d.name for d, _obj in data])
    out: list[_Field] = []
    for (d, obj), name in zip(data, names, strict=True):
        got = _field(f, obj, name, d.unit, n, f"{label}: данные «{d.name}»")
        if isinstance(got, str):
            notes.append(f"{label}: данные «{d.name}» — не загружены: {got}")
        else:
            out.append(got)
    return out, notes


class _Take:
    """Exactly m values at a time from a stream of arrays of any length."""

    def __init__(self, parts: Iterator[np.ndarray], what: str) -> None:
        self.parts, self.what = parts, what
        self.buf: list[np.ndarray] = []
        self.have = 0

    def take(self, m: int) -> np.ndarray:
        while self.have < m:
            part = next(self.parts, None)
            if part is None:
                raise damaged(f"{self.what}: значений меньше, чем ячеек сетки.")
            self.buf.append(part)
            self.have += len(part)
        merged = self.buf[0] if len(self.buf) == 1 else np.concatenate(self.buf)
        out, rest = merged[:m], merged[m:]
        self.buf, self.have = ([rest] if len(rest) else []), len(rest)
        return out


class _Mapped:
    """A temporary file of one field mapped read-only; `close` unmaps it before the file is removed."""

    def __init__(self, path: Path, dtype: Any) -> None:
        self._fh: IO[bytes] | None = open(path, "rb")  # noqa: SIM115 — closed in close()
        self._mm: mmap.mmap | None = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        self.values: np.ndarray | None = np.frombuffer(self._mm, dtype=dtype)

    def take(self, at: np.ndarray) -> np.ndarray:
        assert self.values is not None
        return self.values[at]  # a copy: no view outlives the map

    def close(self) -> None:
        self.values = None
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None


# --- reading ----------------------------------------------------------------------------------------------


def estimate(f: OmfFile, vol: OmfVolume, portion: int = PORTION) -> dict[str, int]:
    """Memory and temporary disk a `read_volume` needs (bytes), from the JSON only."""
    vg = volume_grid(vol)
    n, k = vg.cells, len(vol.data)
    texts = sum(1 for d, _o in vol.data if d.cls in ("StringData", "DateTimeData"))
    memory = portion * (64 + 48 * k) + 4 * n * texts  # portions of every field, the writer's row group
    disk = 0 if vg.identity else n * 8 * k
    return {"cells": n, "fields": k, "memory": int(memory), "disk": int(disk)}


def read_volume(
    f: OmfFile,
    vol: OmfVolume,
    make_sink: SinkFactory,
    *,
    tmp_dir: str | Path,
    progress: Progress | None = None,
    portion: int = PORTION,
) -> VolumeRead:
    """The occupied cells of a volume into `make_sink(grid, fields)` by portions of ≤ `portion` cells in ascending
    `idx`; `sink.finish()` at the end (its result is `VolumeRead.staged`), `sink.discard()` on any failure."""
    tick = progress or no_progress
    vg = volume_grid(vol)
    n, label = vg.cells, vol.entry.label
    old_tick = f.tick
    sink: CellsSink | None = None
    mapped: list[_Mapped] = []
    tmp: Path | None = None
    try:
        try:
            fields, notes = _fields(f, vol, n)
            sink = make_sink(dict(vg.grid), [dict(x.meta) for x in fields])
            total = max(sum(x.block.length for x in fields if x.block is not None), 1)
            start = f.read_bytes
            share = 1.0 if vg.identity else 0.6
            f.tick = lambda b: tick(share * min(1.0, (b - start) / total), f"Чтение: {label}")
            if not fields or vg.identity:
                streams = [_Take(x.values(portion * x.itemsize), x.meta["name"]) for x in fields]
                by_cells = None if any(x.block is not None for x in fields) else (0.0, 1.0)
                kept = _write(sink, fields, n, portion, lambda s, e: [st.take(e - s) for st in streams], by_cells, tick)
            else:
                tmp = Path(tmp_dir) / f".omf-{uuid.uuid4().hex}"
                tmp.mkdir(parents=True)
                need = n * sum(x.itemsize for x in fields)
                if shutil.disk_usage(tmp).free < need + DISK_MARGIN:
                    raise disk_full(need)
                for i, x in enumerate(fields):
                    path = tmp / f"{i}.bin"
                    with open(path, "wb") as fh:
                        for part in x.values(portion * x.itemsize):
                            fh.write(part.data)
                    mapped.append(_Mapped(path, np.float64 if x.itemsize == 8 else np.int32))
                f.tick = None

                def gather(s: int, e: int) -> list[np.ndarray]:
                    at = omf_numbers(np.arange(s, e, dtype=np.int64), vg)
                    return [m.take(at) for m in mapped]

                kept = _write(sink, fields, n, portion, gather, (0.6, 1.0), tick)
            staged = sink.finish()
        except BaseException:
            if sink is not None:
                sink.discard()
            raise
        finally:
            f.tick = old_tick
            for m in mapped:
                m.close()
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
    except MemoryError:
        raise out_of_memory(f"Не хватило памяти на модель {n_text(n)} ячеек ({label}).",
                            "Закройте другие программы или разделите модель на части.") from None  # fmt: skip
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            raise disk_full(n * 8 * len(vol.data)) from None
        raise
    warnings = list(notes)
    if not fields:
        warnings.append(f"{label}: в ячейках нет данных — загружены все {n_text(n)} ячеек сетки")
    elif kept < n:
        warnings.append(f"{label}: ячеек без значений {n_text(n - kept)} из {n_text(n)} — не загружены")
    tick(1.0, f"Прочитано ячеек: {n_text(kept)}")
    fields_out = getattr(staged, "fields", None) or [dict(x.meta) for x in fields]
    source = {"format": "omf", "file": f.path.name, "element": vol.name}
    return VolumeRead(vol.name, vg.grid, fields_out, kept, staged, source, vol.entry.color, warnings)


def _write(
    sink: CellsSink,
    fields: list[_Field],
    n: int,
    portion: int,
    values: Callable[[int, int], list[np.ndarray]],
    share: tuple[float, float] | None,
    tick: Progress,
) -> int:
    """BF2 cells [s, e) portion by portion: the occupied ones (a value in some field) to the sink."""
    kept = 0
    names = [x.meta["name"] for x in fields]
    numbers = [x.itemsize == 8 for x in fields]
    for s in range(0, n, portion):
        e = min(n, s + portion)
        idx = np.arange(s, e, dtype=np.int64)
        vals = values(s, e)
        if vals:
            occupied = np.zeros(e - s, dtype=bool)
            for v, num in zip(vals, numbers, strict=True):
                occupied |= np.isfinite(v) if num else v >= 0
            if not occupied.all():
                idx = idx[occupied]
                vals = [v[occupied] for v in vals]
        sink.write(idx, dict(zip(names, vals, strict=True)))
        kept += len(idx)
        if share is not None:
            tick(share[0] + (share[1] - share[0]) * e / n, None)
    return kept
