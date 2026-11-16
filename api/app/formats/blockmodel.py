"""Block models from Micromine DAT and CSV: columns, the parent grid, checks.

A block is a centre and a size (X, Y, Z and _X, _Y, _Z in Micromine; XC, YC, ZC and XINC, YINC,
ZINC in Datamine). Sub-blocked models (Micromine: sizes of whole metres, not powers of two) are
kept as such: a parent grid (corner origin, parent size, ni × nj × nk) plus the list of blocks.
A model without size fields is regular: the size comes from the grid line of a Micromine CSV or
from the spacing of the centres.

Micromine CSV has three header lines: field names; types (DOUBLE, NUMERIC, CHARACTER, ...); the
grid "x0, dx, y0, dy, z0, dz, CENTRE" (centre of the first parent block, or CORNER).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from app.formats.micromine import MAGIC, Column, MicromineFormatError, read_columns, read_header
from app.formats.tabular import unique_names

Progress = Callable[[float, str], None]

# Field roles by name (case does not matter), in order of preference.
# Datamine: XC/XINC (8 characters), Micromine: EAST/_EAST, Surpac: X_CENTRE/DIM_X, Vulcan: XCENTRE/XLENGTH,
# Leapfrog / Seequent: X/DX, "Centroid X"/"Size X", Deswik and CSV exports: XCENTROID/XSIZE.
CENTRE_NAMES = {
    "x": ("X", "XC", "XCENTRE", "XCENTER", "X_CENTRE", "X_CENTER", "XCENTROID", "CENTROID_X", "CENTRE_X", "CENTER_X",
          "EAST", "EASTING", "XMID", "MIDX", "MID_X", "XPT", "X_COORD", "ВОСТОК", "Х"),
    "y": ("Y", "YC", "YCENTRE", "YCENTER", "Y_CENTRE", "Y_CENTER", "YCENTROID", "CENTROID_Y", "CENTRE_Y", "CENTER_Y",
          "NORTH", "NORTHING", "YMID", "MIDY", "MID_Y", "YPT", "Y_COORD", "СЕВЕР", "У"),
    "z": ("Z", "ZC", "ZCENTRE", "ZCENTER", "Z_CENTRE", "Z_CENTER", "ZCENTROID", "CENTROID_Z", "CENTRE_Z", "CENTER_Z",
          "RL", "ELEV", "ELEVATION", "ZMID", "MIDZ", "MID_Z", "ZPT", "Z_COORD", "ОТМЕТКА"),
}
SIZE_NAMES = {
    "x": ("_X", "XINC", "DX", "XSIZE", "SIZE_X", "XDIM", "X_SIZE", "DIM_X", "XLENGTH", "X_LENGTH", "LENGTH_X", "XL",
          "BLOCK_SIZE_X", "SIZEX", "XBLOCK", "РАЗМЕР_X"),
    "y": ("_Y", "YINC", "DY", "YSIZE", "SIZE_Y", "YDIM", "Y_SIZE", "DIM_Y", "YLENGTH", "Y_LENGTH", "LENGTH_Y", "YL",
          "BLOCK_SIZE_Y", "SIZEY", "YBLOCK", "РАЗМЕР_Y"),
    "z": ("_Z", "ZINC", "DZ", "ZSIZE", "SIZE_Z", "ZDIM", "Z_SIZE", "DIM_Z", "ZLENGTH", "Z_LENGTH", "LENGTH_Z", "ZL",
          "BLOCK_SIZE_Z", "SIZEZ", "ZBLOCK", "РАЗМЕР_Z"),
}
CSV_TYPES = {"DOUBLE", "NUMERIC", "CHARACTER", "REAL", "FLOAT", "INTEGER", "LONG", "SHORT", "BYTE", "TEXT", "STRING", "C", "N", "R"}
# Above this many cells of the smallest block size per parent the exact overlap test is skipped
# (the parent volume test still runs).
MAX_OVERLAP_CELLS = 60_000_000


class BlockModelError(ValueError):
    """The file is not a readable block model; the message is shown to the user."""


@dataclass
class RegularGrid:
    """A model of one block per cell of a regular grid (an OMF volume): the grid is known, so blocks
    cannot overlap, repeat or cross a cell, and the heavy checks of arbitrary sub-blocks are skipped."""

    origin: list[float]  # corner of cell (0, 0, 0), x y z
    parent: list[float]  # cell size, x y z
    counts: list[int]  # cells along x, y, z
    cells: np.ndarray  # int64 per block: ix + nx·(iy + ny·iz)


@dataclass
class BlockTable:
    format: str
    centres: np.ndarray  # (n, 3) float64
    sizes: np.ndarray  # (n, 3) float64
    attributes: list[Column]
    roles: dict[str, str]  # x, y, z, dx, dy, dz -> field name ("" when the size is not in the file)
    grid_hint: dict | None = None  # origin_centre / origin_corner and size from the file
    notes: list[str] = field(default_factory=list)
    dropped: int = 0  # rows without a centre or a size
    regular: RegularGrid | None = None


def _find(names: list[str], wanted: tuple[str, ...], used: set[str] = frozenset()) -> str | None:
    """Exact names (any case) first; then the looser match of app.importing.names (units, separators,
    names cut to 8 or 10 characters) among names not starting with "_" — in Micromine "_EAST" is a size."""
    upper = {n.upper(): n for n in names if n not in used}
    for w in wanted:
        if w in upper:
            return upper[w]
    from app.importing.names import match_column

    return match_column([n for n in names if not n.startswith("_")], wanted, set(used))


def find_roles(names: list[str]) -> dict[str, str | None]:
    roles: dict[str, str | None] = {}
    used: set[str] = set()
    for axis in "xyz":
        roles[axis] = _find(names, CENTRE_NAMES[axis], used)
        if roles[axis]:
            used.add(roles[axis])
    for axis in "xyz":
        roles["d" + axis] = _find(names, SIZE_NAMES[axis], used)
        # Micromine names the size after the centre with "_": EAST → _EAST, RL → _RL.
        if roles[axis] and not roles["d" + axis]:
            roles["d" + axis] = _find(names, ("_" + roles[axis].upper(),), used)
        if roles["d" + axis]:
            used.add(roles["d" + axis])
    return roles


def looks_like_block_model(names: list[str]) -> bool:
    """Centre fields on all three axes and either size fields or nothing that looks like a drillhole
    table (hole id, from / to, depth)."""
    roles = find_roles(names)
    if not all(roles[a] for a in "xyz"):
        return False
    if all(roles["d" + a] for a in "xyz"):
        return True
    # Hole tables by the same loose names as the drillhole wizard: "DrillholeI", "Depth_m", "Dip (deg)".
    from app.importing.names import match_column
    from app.importing.schema import SCHEMAS

    hole_like = ("BHID", "HOLEID", "HOLE_ID", "HOLE", "FROM", "TO", "DEPTH", "AZ", "DIP", "AZIMUTH", "TDEPTH", "SAMPID")
    hole_names = next(f.aliases for f in SCHEMAS["collar"] if f.key == "hole_id")
    named = [n for n in names if n not in {roles[a] for a in "xyz"}]
    if match_column(named, hole_like) or match_column(named, tuple(a for a in hole_names if a not in ("NAME", "ID"))):
        return False
    return len(names) >= 4


# ---------------------------------------------------------------------------------- sniffing
def header_fields(path: Path, filename: str) -> list[str] | None:
    """Field names of a DAT or CSV from the start of the file, or None if it is neither."""
    suffix = Path(filename).suffix.lower()
    try:
        with path.open("rb") as f:
            head = f.read(4)
        if head == MAGIC[:4]:
            _, fields, _ = read_header(path)
            return unique_names([fd.name for fd in fields])
        if suffix in (".csv", ".txt"):
            with path.open("rb") as f:
                first = f.read(65536).splitlines()
            if not first:
                return None
            line = _decode_line(first[0])
            return [c.strip().strip('"') for c in next(csv.reader([line], delimiter=_delimiter(line)))]
    except (OSError, MicromineFormatError, StopIteration):
        return None
    return None


def _decode_line(raw: bytes) -> str:
    for codec in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _delimiter(line: str) -> str:
    counts = {d: line.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=counts.get) if max(counts.values()) else ","


# ------------------------------------------------------------------------------------ readers
def read_block_model(path: Path, filename: str, progress: Progress | None = None, roles: dict[str, str | None] | None = None) -> BlockTable:
    """roles: the user's choice of centre and size fields (x, y, z, dx, dy, dz; "" — no such field); the rest found by name."""
    progress = progress or (lambda _f, _m: None)
    with path.open("rb") as f:
        head = f.read(len(MAGIC))
    if head == MAGIC:
        try:
            dat = read_columns(path, progress=lambda fr, m: progress(0.05 + 0.6 * fr, m))
        except MicromineFormatError as exc:
            raise BlockModelError(f"Файл Micromine DAT не читается: {exc}") from exc
        notes = []
        if dat.trailing_bytes:
            notes.append(f"В конце файла {dat.trailing_bytes} байт не составляют запись — пропущены")
        fmt = f"Micromine DAT ({dat.codepage})"
        return _assemble(fmt, dat.columns, None, notes, roles)
    if Path(filename).suffix.lower() in (".csv", ".txt"):
        cols, hint, fmt = _read_csv(path, progress)
        return _assemble(fmt, cols, hint, [], roles)
    raise BlockModelError("Блочная модель читается из Micromine DAT или CSV")


def _read_csv(path: Path, progress: Progress) -> tuple[list[Column], dict | None, str]:
    with path.open("rb") as f:
        lines = [f.readline() for _ in range(3)]
    first = _decode_line(lines[0])
    sep = _delimiter(first)
    encoding = "utf-8-sig"
    try:
        b"".join(lines).decode("utf-8")
    except UnicodeDecodeError:
        encoding = "cp1251"
    split = lambda raw: [c.strip().strip('"') for c in next(csv.reader([_decode_line(raw)], delimiter=sep))]
    names = unique_names(split(lines[0]))
    skip = 0
    types: list[str] | None = None
    hint = None
    fmt = "CSV"
    second = split(lines[1]) if len(lines) > 1 and lines[1] else []
    if second and all(t.upper() in CSV_TYPES for t in second if t):
        types = [t.upper() for t in second]
        skip = 1
        fmt = "Micromine CSV"
        third = split(lines[2]) if len(lines) > 2 and lines[2] else []
        hint = _grid_line(third)
        if hint is not None:
            skip = 2
    progress(0.05, "Чтение CSV")
    text_cols = {n for n, t in zip(names, types or []) if t in ("CHARACTER", "TEXT", "STRING", "C")}
    frame = pd.read_csv(path, sep=sep, header=None, names=names, skiprows=1 + skip, encoding=encoding,
                        dtype={n: str for n in text_cols}, keep_default_na=True, na_values=[""], low_memory=False,
                        skipinitialspace=True)
    progress(0.5, "Столбцы")
    cols: list[Column] = []
    for name in names:
        s = frame[name]
        # Text comes as `object` in pandas 2 and as the string dtype in pandas 3.
        if name in text_cols or not pd.api.types.is_numeric_dtype(s):
            numeric = pd.to_numeric(s.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")
            filled = s.notna() & (s.astype(str).str.strip() != "")
            if name not in text_cols and filled.any() and numeric[filled].notna().all():
                cols.append(Column(name, "number", numeric.to_numpy(np.float64), source="CSV"))
                continue
            codes, labels = pd.factorize(s.astype("string").str.strip().replace("", pd.NA), use_na_sentinel=True)
            cols.append(Column(name, "category", codes.astype(np.int32), labels=[str(x) for x in labels], source="CSV"))
        else:
            cols.append(Column(name, "number", s.to_numpy(np.float64), source="CSV"))
    return cols, hint, fmt


def _grid_line(cells: list[str]) -> dict | None:
    """'x0, dx, y0, dy, z0, dz, CENTRE' -> {origin, size, at}."""
    vals = [c for c in cells if c != ""]
    if len(vals) < 6:
        return None
    try:
        nums = [float(v.replace(",", ".")) for v in vals[:6]]
    except ValueError:
        return None
    at = vals[6].upper() if len(vals) > 6 else "CENTRE"
    if not re.match(r"^(CENTRE|CENTER|CORNER|ORIGIN)$", at):
        return None
    if min(nums[1], nums[3], nums[5]) <= 0:
        return None
    return {"origin": [nums[0], nums[2], nums[4]], "size": [nums[1], nums[3], nums[5]],
            "at": "corner" if at in ("CORNER", "ORIGIN") else "centre"}


def _assemble(fmt: str, cols: list[Column], hint: dict | None, notes: list[str], chosen: dict[str, str | None] | None = None) -> BlockTable:
    names = [c.name for c in cols]
    roles = find_roles(names)
    for role, name in (chosen or {}).items():
        if role not in ("x", "y", "z", "dx", "dy", "dz"):
            continue
        if name and name not in names:
            raise BlockModelError(f"Поля «{name}» в файле нет")
        roles[role] = name or None
    missing = [a.upper() for a in "xyz" if not roles[a]]
    if missing:
        raise BlockModelError(f"Не найдены поля центра блока: {', '.join(missing)} (ожидаются X, Y, Z или XC, YC, ZC)")
    by_name = {c.name: c for c in cols}
    for role in ("x", "y", "z", "dx", "dy", "dz"):
        name = roles.get(role)
        if name and by_name[name].kind != "number":
            raise BlockModelError(f"Поле {name} должно быть числовым")
    n = len(by_name[roles["x"]].values)
    centres = np.column_stack([by_name[roles[a]].values for a in "xyz"]).astype(np.float64, copy=False)
    has_size = all(roles["d" + a] for a in "xyz")
    if has_size:
        sizes = np.column_stack([by_name[roles["d" + a]].values for a in "xyz"]).astype(np.float64, copy=False)
    else:
        size = np.array(hint["size"]) if hint else _spacing(centres)
        sizes = np.broadcast_to(size, (n, 3)).astype(np.float64).copy()
        notes.append("Размеров блоков в файле нет: модель регулярная, размер "
                     + " × ".join(f"{v:g}" for v in sizes[0]) + (" из строки сетки" if hint else " по шагу центров"))
    ok = np.isfinite(centres).all(axis=1) & np.isfinite(sizes).all(axis=1) & (sizes > 0).all(axis=1)
    dropped = int(n - ok.sum())
    if dropped:
        notes.append(f"Блоков без координат или с размером ≤ 0: {dropped} — пропущены")
    used = {v for v in roles.values() if v}
    attrs = []
    # Every row kept (the usual case): no copy of a column (a big model is gigabytes of them).
    keep = (lambda v: v) if not dropped else (lambda v: v[ok])
    for c in cols:
        if c.name in used:
            continue
        attrs.append(Column(c.name, c.kind, keep(c.values), c.labels, c.source, c.unparsed))
    return BlockTable(format=fmt, centres=keep(centres), sizes=keep(sizes), attributes=attrs,
                      roles={k: (v or "") for k, v in roles.items()}, grid_hint=hint, notes=notes, dropped=dropped)


def _spacing(centres: np.ndarray) -> np.ndarray:
    out = []
    for a in range(3):
        u = np.unique(centres[np.isfinite(centres[:, a]), a])
        d = np.diff(u)
        d = d[d > 1e-6]
        out.append(float(d.min()) if len(d) else 1.0)
    return np.array(out)


# ------------------------------------------------------------------------------- grid, checks
@dataclass
class GridInfo:
    origin: list[float]  # corner of parent (0, 0, 0)
    parent: list[float]
    counts: list[int]
    subblocked: bool
    parents_used: int
    rotation: float = 0.0


def aligned_origin(lo: np.ndarray, sizes: np.ndarray, parent: np.ndarray) -> np.ndarray:
    """Corner of parent (0, 0, 0) when the file does not give the grid. The lowest corner of the model
    is not always on the grid (a model trimmed to the ore or under the topography starts with a
    sub-block in the middle of its parent): the grid is where blocks of the full parent size stand."""
    if not len(lo):
        return np.zeros(3)
    origin = lo.min(axis=0).astype(np.float64)
    for a in range(3):
        p = float(parent[a])
        if p <= 0:
            continue
        whole = np.abs(sizes[:, a] - p) <= 1e-6 * max(p, 1)
        corners = lo[whole, a] if whole.any() else lo[:, a]
        if len(corners) > 2_000_000:
            corners = corners[:: len(corners) // 2_000_000 + 1]
        residue = np.round(np.mod(corners - origin[a], p), 4) % round(p, 4)
        values, counts = np.unique(residue, return_counts=True)
        r = float(values[np.argmax(counts)])
        origin[a] += r - p if r > 0 else 0.0  # one step down so that every block has an index ≥ 0
    return origin


def describe_grid(t: BlockTable) -> tuple[GridInfo, np.ndarray, np.ndarray]:
    """The parent grid and, per block, its parent cell (i, j, k) and whether it lies inside it."""
    n = len(t.centres)
    if t.grid_hint:
        parent = np.array(t.grid_hint["size"], dtype=np.float64)
        o = np.array(t.grid_hint["origin"], dtype=np.float64)
        origin = o - parent / 2 if t.grid_hint["at"] == "centre" else o
    else:
        parent = t.sizes.max(axis=0) if n else np.ones(3)
        origin = aligned_origin(t.centres - t.sizes / 2, t.sizes, parent) if n else np.zeros(3)
    tol = 1e-6 * np.maximum(parent, 1)
    # Axis by axis, cells in int32: a model of millions of blocks keeps to a few hundred MB here.
    i0 = np.empty((n, 3), np.int32)
    inside = np.ones(n, bool)
    counts = np.zeros(3, np.int64)
    subblocked = False
    for a in range(3):
        c, half = t.centres[:, a], t.sizes[:, a] / 2
        first = np.floor((c - half - origin[a] + tol[a]) / parent[a])
        last = np.floor((c + half - origin[a] - tol[a]) / parent[a])
        i0[:, a] = np.clip(first, -(2**31), 2**31 - 1)
        inside &= first == last
        if n:
            counts[a] = max(0, int(last.max()) + 1)
            subblocked = subblocked or bool((np.abs(t.sizes[:, a] - parent[a]) > tol[a]).any())
        del first, last, half
    valid = inside & (i0 >= 0).all(axis=1)
    lin = ((i0[valid, 0].astype(np.int64) * counts[1] + i0[valid, 1]) * counts[2] + i0[valid, 2]) if valid.any() else np.zeros(0, np.int64)
    info = GridInfo(origin=[float(v) for v in origin], parent=[float(v) for v in parent],
                    counts=[int(v) for v in counts], subblocked=subblocked, parents_used=_distinct(lin))
    return info, i0, inside


def _distinct(values: np.ndarray) -> int:
    """How many different values (by sorting: np.unique hashes and is several times slower on millions)."""
    if not len(values):
        return 0
    v = np.sort(values)
    return int(1 + np.count_nonzero(v[1:] != v[:-1]))


def check_model(t: BlockTable, grid: GridInfo, cells: np.ndarray, inside: np.ndarray) -> list[dict]:
    """Checks of 12.3: blocks off the grid, outside their parent, duplicates, overlaps, attributes."""
    out: list[dict] = []

    def add(level: str, code: str, message: str, count: int | None = None) -> None:
        out.append({"level": level, "code": code, "message": message, "count": count})

    n = len(t.centres)
    negative = int((cells < 0).any(axis=1).sum())
    if negative:
        add("error", "off_grid", "Блоки за началом сетки (отрицательный индекс родительской ячейки)", negative)
    crossing = int((~inside).sum())
    if crossing:
        add("error", "crosses_parent", "Субблоки пересекают границу родительского блока", crossing)
    if grid.subblocked and t.grid_hint is None:
        add("info", "parent_guess", "Размер родительского блока определён по самому большому блоку: "
            + " × ".join(f"{v:g}" for v in grid.parent))
    dup = _duplicates(t.centres, t.sizes)
    if dup:
        add("error", "duplicates", "Повторяющиеся блоки (тот же центр и размер)", int(dup))
    overlap, method = _overlaps(t, grid, cells, inside)
    if overlap:
        add("error", "overlaps", f"Перекрытия блоков ({method})", overlap)
    out.extend(attribute_checks(t, float(np.prod(t.sizes, axis=1, dtype=np.float64).sum())))
    return out


def attribute_checks(t: BlockTable, volume: float) -> list[dict]:
    """Checks of the fields (empty, constant, unreadable values) and the total volume."""
    out: list[dict] = []

    def add(level: str, code: str, message: str, count: int | None = None) -> None:
        out.append({"level": level, "code": code, "message": message, "count": count})

    if t.dropped:
        add("warning", "dropped", "Строки без координат центра или размера пропущены", t.dropped)
    for c in t.attributes:
        if c.kind == "number":
            nan = int(np.isnan(c.values).sum())
            if nan:
                add("warning", "empty_values", f"{c.name}: пустые значения", nan)
            finite = c.values[np.isfinite(c.values)]
            if len(finite) > 1 and finite.min() == finite.max():
                add("info", "constant", f"{c.name}: одно значение {finite[0]:g} у всех блоков")
            if c.unparsed:
                add("warning", "unparsed", f"{c.name}: значения не читаются как числа", c.unparsed)
        elif (c.values < 0).any():
            add("warning", "empty_values", f"{c.name}: пустые значения", int((c.values < 0).sum()))
    add("info", "volume", f"Объём блоков {volume:,.0f} м³".replace(",", " "))
    return out


def _duplicates(centres: np.ndarray, sizes: np.ndarray) -> int:
    """Blocks repeating another (the same centre and size to a micrometre): extra copies counted.
    A 64-bit hash of the six numbers, sorted; rows with equal hashes are compared exactly. Without
    a table of n × 6 and its sorted copy (gigabytes for a big model)."""
    n = len(centres)
    if n < 2:
        return 0
    h = np.zeros(n, np.uint64)
    with np.errstate(over="ignore"):
        for col in (*centres.T, *sizes.T):
            q = np.round(col * 1e6).astype(np.int64).view(np.uint64)
            h = (h ^ q) * np.uint64(0x100000001B3)
            h ^= h >> np.uint64(29)
            del q
    order = np.argsort(h, kind="stable")
    hs = h[order]
    del h
    same = np.zeros(n, dtype=bool)
    same[1:] = hs[1:] == hs[:-1]
    if not same.any():
        return 0
    same[:-1] |= same[1:]
    rows = order[same]
    key = np.round(np.hstack([centres[rows], sizes[rows]]), 6)
    return int(len(rows) - len(np.unique(key, axis=0)))


def _overlaps(t: BlockTable, grid: GridInfo, cells: np.ndarray, inside: np.ndarray) -> tuple[int, str]:
    """Blocks sharing space with another block. Exact on the grid of the smallest block size when
    every block is aligned to it; otherwise parents whose blocks hold more than the parent volume."""
    if not len(t.sizes):
        return 0, ""
    parent = np.array(grid.parent)
    origin = np.array(grid.origin)
    unit = t.sizes.min(axis=0)
    per_parent = np.rint(parent / unit).astype(np.int64)
    lo = t.centres - t.sizes / 2 - origin
    steps_lo = lo / unit
    steps_n = t.sizes / unit
    aligned = (np.abs(steps_lo - np.rint(steps_lo)) < 1e-4).all() and (np.abs(steps_n - np.rint(steps_n)) < 1e-4).all() \
        and (np.abs(parent / unit - per_parent) < 1e-4).all()
    ok = inside & (cells >= 0).all(axis=1)
    if not grid.subblocked:
        # Every block fills its parent cell: blocks overlap exactly when they share a cell.
        c = cells[ok].astype(np.int64)
        lin = np.sort((c[:, 0] * grid.counts[1] + c[:, 1]) * grid.counts[2] + c[:, 2])
        del c
        same = np.zeros(len(lin), dtype=bool)
        same[1:] = lin[1:] == lin[:-1]
        same[:-1] |= same[1:]
        return int(same.sum()), "по сетке наименьшего блока"
    if aligned:
        nsub = np.rint(steps_n).astype(np.int64)
        total = int(np.prod(nsub, axis=1)[ok].sum())
        if total <= MAX_OVERLAP_CELLS:
            start = np.rint(steps_lo).astype(np.int64)[ok]
            nsub = nsub[ok]
            dims = np.array(grid.counts) * per_parent
            # Every sub-cell a block covers, as one linear index; repeated indices are overlaps.
            reps = np.prod(nsub, axis=1)
            owner = np.repeat(np.arange(len(nsub)), reps)
            local = np.arange(total) - np.repeat(np.cumsum(reps) - reps, reps)
            nyz = nsub[owner, 1] * nsub[owner, 2]
            ix = start[owner, 0] + local // nyz
            iy = start[owner, 1] + (local % nyz) // nsub[owner, 2]
            iz = start[owner, 2] + local % nsub[owner, 2]
            lin = (ix * dims[1] + iy) * dims[2] + iz
            order = np.argsort(lin, kind="stable")
            s = lin[order]
            same = np.zeros(total, dtype=bool)
            same[1:] = s[1:] == s[:-1]
            same[:-1] |= same[1:]
            blocks = np.unique(owner[order][same])
            return len(blocks), "по сетке наименьшего блока"
    # Fallback: volume per parent.
    vol = np.prod(t.sizes, axis=1)[ok]
    c = cells[ok]
    lin = (c[:, 0] * grid.counts[1] + c[:, 1]) * grid.counts[2] + c[:, 2]
    uniq, inv = np.unique(lin, return_inverse=True)
    filled = np.bincount(inv, weights=vol)
    over = filled > np.prod(parent) * (1 + 1e-6)
    return int(over[inv].sum()), "объём блоков в родительской ячейке больше её объёма"


# ---------------------------------------------------------------------------------- units
_GRAMS = ("AU", "AG", "PT", "PD", "RH", "IR", "OS", "RU")
_PERCENT = ("CU", "PB", "ZN", "NI", "MO", "FE", "MN", "S", "CO", "SN", "W", "AS", "SB", "BI", "CR", "TI", "V", "AL", "SI", "MG", "CA", "K", "NA")


def guess_unit(name: str) -> str | None:
    """g/t for precious metals, % for base metals, by the element at the start of the name."""
    upper = name.upper()
    head = re.sub(r"\d+$", "", re.split(r"[_\s\-.]", upper, maxsplit=1)[0])
    if "PPM" in upper:
        return "ppm" if head in _GRAMS + _PERCENT or head.endswith("PPM") else None
    if "PPB" in upper:
        return "ppb" if head in _GRAMS + _PERCENT or head.endswith("PPB") else None
    if head in _GRAMS:
        return "g/t"
    if head in _PERCENT:
        return "%"
    return None


def stats_of(c: Column, volume: np.ndarray) -> dict:
    if c.kind == "number":
        v = c.values
        ok = np.isfinite(v)
        if not ok.any():
            return {"count": 0, "empty": len(v)}
        w = volume[ok]
        return {"count": int(ok.sum()), "empty": int((~ok).sum()), "min": float(v[ok].min()), "max": float(v[ok].max()),
                "mean": float(v[ok].mean()), "mean_by_volume": float((v[ok] * w).sum() / w.sum()) if w.sum() > 0 else None}
    ok = c.values >= 0  # one mask and one copy of the codes: 50 million blocks make each of them big
    codes = c.values[ok]
    counts = np.bincount(codes, minlength=len(c.labels or []))
    vols = np.bincount(codes, weights=volume[ok], minlength=len(c.labels or []))
    del codes
    order = np.argsort(-counts)[:100]
    filled = int(ok.sum())
    return {"count": filled, "empty": int(len(c.values) - filled), "categories": len(c.labels or []),
            "top": [{"label": (c.labels or [])[i], "count": int(counts[i]), "volume": float(vols[i])} for i in order]}


def to_csv_preview(t: BlockTable, rows: int = 5) -> str:
    """A few rows as text, for the import preview."""
    buf = io.StringIO()
    names = ["X", "Y", "Z", "_X", "_Y", "_Z"] + [c.name for c in t.attributes]
    buf.write(",".join(names) + "\n")
    for r in range(min(rows, len(t.centres))):
        vals = [*t.centres[r], *t.sizes[r]]
        for c in t.attributes:
            vals.append(c.values[r] if c.kind == "number" else ((c.labels or [""])[c.values[r]] if c.values[r] >= 0 else ""))
        buf.write(",".join(f"{v:g}" if isinstance(v, float) else str(v) for v in vals) + "\n")
    return buf.getvalue()
