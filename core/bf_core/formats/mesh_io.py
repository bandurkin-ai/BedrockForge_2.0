"""What the wireframe readers and writers of DXF, OBJ, STL and Surpac DTM share (ADR 0013 §2).

Reading a text file: `TextBlocks(path, progress)` gives the file in blocks of whole lines cut into tokens
(`Tokens`) by the numba kernels of `_meshkernel` — a reader works on arrays, never on a Python object per line;
numbers are converted by Arrow (correctly rounded: a coordinate written with `repr` comes back bit for bit).
`tidy` makes the vertex list of a file and the triangles on it the mesh a `MeshSet` takes: triangles on a vertex
without a coordinate are left out (never a 0 instead), unused vertices too, coincident ones are welded exactly.

Writing: `part_files` writes next to the place as `<name>.part` and renames at the end (a failure or a cancel
leaves neither), `numbered` gives every wireframe its share of the progress, `unique_names` — names that differ.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshSet, drop_unused, n_text, name_key, weld_exact
from bf_core.formats.str_layers import scaled
from bf_core.formats.table import Progress, no_progress

BLOCK_BYTES = 4 << 20
MAX_LINE_BYTES = 64 << 20  # a line longer than this is not a line of a mesh file
MAX_TRIANGLES = 50_000_000  # of a file: five times what a wireframe is promised to hold (ТЗ 7.21)
HINT = "Проверьте исходный файл каркаса."


@dataclass
class MeshWritten:
    path: Path
    meshes: int
    points: int
    triangles: int
    size: int  # bytes of all the files written
    warnings: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)  # every file written (a Surpac DTM comes with its STR)


def fail(code: str, message: str, hint: str = HINT) -> FormatError:
    return FormatError(code, message, hint)


def word(text: bytes) -> int:
    """A keyword as `Tokens.words` gives it: its first eight bytes."""
    return int.from_bytes(text.lower()[:8], "little")


def too_many(limit: int = MAX_TRIANGLES) -> FormatError:
    return fail(
        "too_large",
        f"В файле больше {n_text(limit)} треугольников: он слишком велик.",
        "Разделите каркас на части в исходной программе.",
    )


class Tokens:
    """A block of whole lines cut into tokens: `first[i]` — the first token of line i, `first[lines]` — the
    number of tokens; `start`, `end` — the bytes of every token in `raw`."""

    def __init__(self, raw: bytes, start: np.ndarray, end: np.ndarray, first: np.ndarray, base: int) -> None:
        self.raw, self.start, self.end, self.first = raw, start, end, first
        self.buf = np.frombuffer(raw, dtype=np.uint8)
        self.lines = len(first) - 1
        self.base = base  # the number of the first line of the block in the file (from 0)

    def counts(self) -> np.ndarray:
        """Tokens of every line."""
        return np.diff(self.first)

    def words(self, tok: np.ndarray) -> np.ndarray:
        """The tokens as keywords (`word`): their first eight bytes, ASCII letters in lower case."""
        from bf_core.formats import _meshkernel as k

        out = np.empty(len(tok), dtype=np.uint64)
        k.words(self.buf, self.start, self.end, np.ascontiguousarray(tok, dtype=np.int64), out)
        return out

    def ints(self, tok: np.ndarray, slash: bool = False) -> np.ndarray:
        """The tokens as whole numbers (int64; `_meshkernel.MISSING` — not one)."""
        from bf_core.formats import _meshkernel as k

        out = np.empty(len(tok), dtype=np.int64)
        k.ints(self.buf, self.start, self.end, np.ascontiguousarray(tok, dtype=np.int64), slash, out)
        return out

    def floats(self, tok: np.ndarray) -> np.ndarray:
        """The tokens as float64, each the same double as `float()` gives; NaN — not a number."""
        import pyarrow as pa
        import pyarrow.compute as pc

        from bf_core.formats import _meshkernel as k

        count = len(tok)
        if count == 0:
            return np.empty(0, dtype=np.float64)
        tok = np.ascontiguousarray(tok, dtype=np.int64)
        pk = np.empty(int((self.end[tok] - self.start[tok]).sum()), dtype=np.uint8)
        poff = np.zeros(count + 1, dtype=np.int32)
        k.pack(self.buf, self.start, self.end, tok, pk, poff)
        try:
            cells = pa.Array.from_buffers(pa.string(), count, [None, pa.py_buffer(poff), pa.py_buffer(pk)])
            found = pc.cast(cells, pa.float64())  # (its `to_numpy` would import pandas: the buffer is read directly)
            return np.frombuffer(found.buffers()[1], dtype=np.float64, count=count, offset=found.offset * 8).copy()
        except pa.ArrowInvalid:  # a text Arrow does not take: the grammar of float(), a broken one is NaN
            raw = pk.tobytes()
            out = np.full(count, np.nan)
            for i, (a, b) in enumerate(zip(poff[:-1].tolist(), poff[1:].tolist(), strict=True)):
                try:
                    out[i] = float(raw[a:b])
                except ValueError:
                    pass
            return out

    def text(self, a: int, b: int) -> bytes:
        """The bytes from the start of token `a` to the end of token `b`."""
        return self.raw[int(self.start[a]) : int(self.end[b])]


class TextBlocks:
    """The blocks of a text file in file order; iterate once. `comma` — a comma separates tokens as a space
    does. `progress(fraction of the file, message)` may raise to cancel. Error code: too_large (a line longer
    than `MAX_LINE_BYTES`)."""

    def __init__(
        self,
        path: Path,
        progress: Progress | None = None,
        *,
        comma: bool = False,
        message: str = "Чтение файла",
        block_bytes: int | None = None,
        skip: int = 0,
    ) -> None:
        self.path, self.tick, self.comma, self.message = path, progress or no_progress, comma, message
        self.block_bytes, self.skip = max(int(block_bytes or BLOCK_BYTES), 16), skip
        self.size = path.stat().st_size
        self.lines = 0  # lines given so far

    def __iter__(self) -> Iterator[Tokens]:
        from bf_core.formats import _meshkernel as k

        done, carry, final = self.skip, b"", False
        with self.path.open("rb") as f:
            f.seek(self.skip)
            while not final:
                chunk = f.read(self.block_bytes)
                final = len(chunk) < self.block_bytes
                done += len(chunk)
                data = carry + chunk if carry else chunk
                if not data:
                    break
                cap = len(data) // 2 + 2
                start, end = np.empty(cap, dtype=np.int32), np.empty(cap, dtype=np.int32)
                first = np.empty(data.count(b"\n") + 2, dtype=np.int64)
                buf = np.frombuffer(data, dtype=np.uint8)
                lines, tokens, pos = (int(v) for v in k.scan(buf, len(data), final, self.comma, start, end, first))
                carry = data[pos:]
                if len(carry) > MAX_LINE_BYTES:
                    raise fail(
                        "too_large",
                        f"Строка {n_text(self.lines + lines + 1)} файла длиннее {MAX_LINE_BYTES >> 20} МБ.",
                        "Файл не похож на текстовый файл каркаса: проверьте его формат.",
                    )
                if lines:
                    yield Tokens(data, start[:tokens], end[:tokens], first[: lines + 1], self.lines)
                    self.lines += lines
                self.tick(min(done / max(self.size, 1), 1.0), f"{self.message}: строк {n_text(self.lines)}")


def tidy(
    xyz: np.ndarray, tri: np.ndarray, warnings: list[str], progress: Progress | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """The vertex list of a file and triangles on it (numbers from 0, all in range) → vertices and triangles of
    the mesh: triangles on a vertex with a coordinate that is not a number are left out, then the vertices no
    triangle uses; coincident vertices are welded exactly. The order of both is kept. Warnings are added."""
    used = np.zeros(len(xyz), dtype=bool)
    used[tri.reshape(-1)] = True
    bad = used & ~np.isfinite(xyz).all(axis=1)
    if bad.any():
        keep = ~bad[tri].any(axis=1)
        warnings.append(
            f"Треугольников на вершинах без координат: {n_text(int((~keep).sum()))} — пропущены "
            f"(вершин без координат: {n_text(int(bad.sum()))})"
        )
        tri = tri[keep]
    xyz, tri, _ = drop_unused(xyz, tri)
    xyz, tri, merged = weld_exact(xyz, tri, progress)
    if merged:
        warnings.append(f"Совпадающих вершин сшито: {n_text(merged)}")
    return np.ascontiguousarray(xyz, dtype=np.float64), np.ascontiguousarray(tri, dtype=np.int32)


# --- writing ---------------------------------------------------------------------------------------------


@contextmanager
def part_files(*paths: Path) -> Iterator[list[BinaryIO]]:
    """Files to write as `<name>.part` next to their places; renamed when the block ends without an error."""
    parts = [p.with_name(p.name + ".part") for p in paths]
    try:
        with ExitStack() as stack:
            files: list[BinaryIO] = [stack.enter_context(part.open("wb")) for part in parts]
            yield files
            for f in files:
                f.flush()
                os.fsync(f.fileno())
        for part, path in zip(parts, paths, strict=True):
            os.replace(part, path)
    finally:
        for part in parts:
            part.unlink(missing_ok=True)


def numbered(
    meshes: Iterable[MeshSet], total: int | None, progress: Progress | None
) -> Iterator[tuple[int, MeshSet, Progress]]:
    """(number from 0, wireframe, its share of the progress) of the wireframes to write; `meshes` may be a
    generator — then `total` tells their number (without it the progress stays at 0)."""
    tick = progress or no_progress
    count = total if total is not None else len(meshes) if hasattr(meshes, "__len__") else None  # type: ignore[arg-type]
    for i, mesh in enumerate(meshes):
        a, b = (min(1.0, i / count), min(1.0, (i + 1) / count)) if count else (0.0, 0.0)
        yield i, mesh, scaled(tick, a, b)


def unique_name(name: str, taken: set[str], limit: int = 200) -> str:
    """`name`, or `name (2)`, `name (3)`… — the first that is not `taken` (compared as `name_key`); it is taken."""
    out, k = name, 1
    while name_key(out) in taken:
        k += 1
        suffix = f" ({k})"
        out = name[: limit - len(suffix)] + suffix
    taken.add(name_key(out))
    return out


def lost_attrs(meshes_with_attrs: int, what: str) -> list[str]:
    return [f"Атрибуты каркасов в {what} не записываются: каркасов с атрибутами {n_text(meshes_with_attrs)}"] * bool(
        meshes_with_attrs
    )


def chunks(n: int, step: int) -> Sequence[tuple[int, int]]:
    return [(a, min(a + step, n)) for a in range(0, n, step)]


def no_meshes(what: str) -> FormatError:
    return fail("no_meshes", f"Нет каркасов для записи в {what}.", "Выберите каркасы для экспорта.")


def entry_of(key: Any, mesh: MeshSet) -> Any:
    """A `MeshEntry` of a wireframe that was read."""
    from bf_core.formats.meshset import MeshEntry

    return MeshEntry(
        key, mesh.name, mesh.point_count, mesh.triangle_count, mesh.color, mesh.attrs, mesh.extent(), mesh.warnings
    )
