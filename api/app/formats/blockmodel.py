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
CENTRE_NAMES = {
    "x": ("X", "XC", "XCENTRE", "XCENTER", "X_CENTRE", "EAST", "EASTING", "XMID", "MIDX", "CENTROID_X"),
    "y": ("Y", "YC", "YCENTRE", "YCENTER", "Y_CENTRE", "NORTH", "NORTHING", "YMID", "MIDY", "CENTROID_Y"),
    "z": ("Z", "ZC", "ZCENTRE", "ZCENTER", "Z_CENTRE", "RL", "ELEV", "ELEVATION", "ZMID", "MIDZ", "CENTROID_Z"),
}
SIZE_NAMES = {
    "x": ("_X", "XINC", "DX", "XSIZE", "SIZE_X", "XDIM", "X_SIZE", "DIM_X"),
    "y": ("_Y", "YINC", "DY", "YSIZE", "SIZE_Y", "YDIM", "Y_SIZE", "DIM_Y"),
    "z": ("_Z", "ZINC", "DZ", "ZSIZE", "SIZE_Z", "ZDIM", "Z_SIZE", "DIM_Z"),
}
CSV_TYPES = {"DOUBLE", "NUMERIC", "CHARACTER", "REAL", "FLOAT", "INTEGER", "LONG", "SHORT", "BYTE", "TEXT", "STRING", "C", "N", "R"}
# Above this many cells of the smallest block size per parent the exact overlap test is skipped
# (the parent volume test still runs).
MAX_OVERLAP_CELLS = 60_000_000


class BlockModelError(ValueError):
    """The file is not a readable block model; the message is shown to the user."""


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


def _find(names: list[str], wanted: tuple[str, ...]) -> str | None:
    upper = {n.upper(): n for n in names}
    for w in wanted:
        if w in upper:
            return upper[w]
    return None


def find_roles(names: list[str]) -> dict[str, str | None]:
    roles: dict[str, str | None] = {}
    for axis in "xyz":
        roles[axis] = _find(names, CENTRE_NAMES[axis])
        roles["d" + axis] = _find(names, SIZE_NAMES[axis])
        # Micromine names the size after the centre with "_": EAST → _EAST, RL → _RL.
        if roles[axis] and not roles["d" + axis]:
            roles["d" + axis] = _find(names, ("_" + roles[axis].upper(),))
    return roles


def looks_like_block_model(names: list[str]) -> bool:
    """Centre fields on all three axes and either size fields or nothing that looks like a drillhole
    table (hole id, from / to, depth)."""
    roles = find_roles(names)
    if not all(roles[a] for a in "xyz"):
        return False
    if all(roles["d" + a] for a in "xyz"):
        return True
    upper = {n.upper() for n in names}
    hole_like = {"BHID", "HOLEID", "HOLE_ID", "HOLE", "FROM", "TO", "DEPTH", "AZ", "DIP", "AZIMUTH", "TDEPTH", "SAMPID"}
    return not (upper & hole_like) and len(names) >= 4


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
def read_block_model(path: Path, filename: str, progress: Progress | None = None) -> BlockTable:
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
        return _assemble(fmt, dat.columns, None, notes)
    if Path(filename).suffix.lower() in (".csv", ".txt"):
        cols, hint, fmt = _read_csv(path, progress)
        return _assemble(fmt, cols, hint, [])
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
    split = lambda raw: [c.strip().strip('"') for c in next(csv.reader([_decode_line(raw)], delimiter=sep))]  # noqa: E731
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
        if name in text_cols or s.dtype == object:
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


def _assemble(fmt: str, cols: list[Column], hint: dict | None, notes: list[str]) -> BlockTable:
    names = [c.name for c in cols]
    roles = find_roles(names)
    missing = [a.upper() for a in "xyz" if not roles[a]]
    if missing:
        raise BlockModelError(f"Не найдены поля центра блока: {', '.join(missing)} (ожидаются X, Y, Z или XC, YC, ZC)")
    by_name = {c.name: c for c in cols}
    for role in ("x", "y", "z", "dx", "dy", "dz"):
        name = roles.get(role)
        if name and by_name[name].kind != "number":
            raise BlockModelError(f"Поле {name} должно быть числовым")
    n = len(by_name[roles["x"]].values)
    centres = np.column_stack([by_name[roles[a]].values for a in "xyz"]).astype(np.float64)
    has_size = all(roles["d" + a] for a in "xyz")
    if has_size:
        sizes = np.column_stack([by_name[roles["d" + a]].values for a in "xyz"]).astype(np.float64)
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
    for c in cols:
        if c.name in used:
            continue
        attrs.append(Column(c.name, c.kind, c.values[ok], c.labels, c.source, c.unparsed))
    return BlockTable(format=fmt, centres=centres[ok], sizes=sizes[ok], attributes=attrs,
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
    lo = t.centres - t.sizes / 2
    hi = t.centres + t.sizes / 2
    if t.grid_hint:
        parent = np.array(t.grid_hint["size"], dtype=np.float64)
        o = np.array(t.grid_hint["origin"], dtype=np.float64)
        origin = o - parent / 2 if t.grid_hint["at"] == "centre" else o
    else:
        parent = t.sizes.max(axis=0) if len(t.sizes) else np.ones(3)
        origin = aligned_origin(lo, t.sizes, parent)
    tol = 1e-6 * np.maximum(parent, 1)
    i0 = np.floor((lo - origin + tol) / parent).astype(np.int64)
    i1 = np.floor((hi - origin - tol) / parent).astype(np.int64)
    inside = (i0 == i1).all(axis=1)
    counts = (i1.max(axis=0) + 1).clip(min=0) if len(i1) else np.zeros(3, np.int64)
    subblocked = bool(len(t.sizes) and (np.abs(t.sizes - parent) > tol).any())
    valid = inside & (i0 >= 0).all(axis=1)
    lin = (i0[valid, 0] * counts[1] + i0[valid, 1]) * counts[2] + i0[valid, 2] if valid.any() else np.zeros(0, np.int64)
    info = GridInfo(origin=[float(v) for v in origin], parent=[float(v) for v in parent],
                    counts=[int(v) for v in counts], subblocked=subblocked, parents_used=int(len(np.unique(lin))))
    return info, i0, inside


def check_model(t: BlockTable, grid: GridInfo, cells: np.ndarray, inside: np.ndarray) -> list[dict]:
    """Checks of 12.3: blocks off the grid, outside their parent, duplicates, overlaps, attributes."""
    out: list[dict] = []

    def add(level: str, code: str, message: str, count: int | None = None) -> None:
        out.append({"level": level, "code": code, "message": message, "count": count})

    n = len(t.centres)
    if t.dropped:
        add("warning", "dropped", "Строки без координат центра или размера пропущены", t.dropped)
    negative = int((cells < 0).any(axis=1).sum())
    if negative:
        add("error", "off_grid", "Блоки за началом сетки (отрицательный индекс родительской ячейки)", negative)
    crossing = int((~inside).sum())
    if crossing:
        add("error", "crosses_parent", "Субблоки пересекают границу родительского блока", crossing)
    if grid.subblocked and t.grid_hint is None:
        add("info", "parent_guess", "Размер родительского блока определён по самому большому блоку: "
            + " × ".join(f"{v:g}" for v in grid.parent))
    # Exact duplicates.
    key = np.round(np.hstack([t.centres, t.sizes]), 6)
    dup = n - len(np.unique(key, axis=0)) if n else 0
    if dup:
        add("error", "duplicates", "Повторяющиеся блоки (тот же центр и размер)", int(dup))
    overlap, method = _overlaps(t, grid, cells, inside)
    if overlap:
        add("error", "overlaps", f"Перекрытия блоков ({method})", overlap)
    vol = np.prod(t.sizes, axis=1)
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
    add("info", "volume", f"Объём блоков {float(vol.sum()):,.0f} м³".replace(",", " "))
    return out


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
            return int(len(blocks)), "по сетке наименьшего блока"
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
            return {"count": 0, "empty": int(len(v))}
        w = volume[ok]
        return {"count": int(ok.sum()), "empty": int((~ok).sum()), "min": float(v[ok].min()), "max": float(v[ok].max()),
                "mean": float(v[ok].mean()), "mean_by_volume": float((v[ok] * w).sum() / w.sum()) if w.sum() > 0 else None}
    counts = np.bincount(c.values[c.values >= 0], minlength=len(c.labels or [])) if len(c.values) else np.zeros(0)
    vols = np.bincount(c.values[c.values >= 0], weights=volume[c.values >= 0], minlength=len(c.labels or [])) if len(c.values) else np.zeros(0)
    order = np.argsort(-counts)[:100]
    return {"count": int((c.values >= 0).sum()), "empty": int((c.values < 0).sum()), "categories": len(c.labels or []),
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
