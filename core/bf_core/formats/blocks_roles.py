"""Roles of the columns of a block file (ТЗ 7.31 «Поля блока», 8.7.2; ADR 0023 decisions 5–6; Д-13): which column is the
centre and the size of a block, which one is Datamine's IJK, a volume fraction, a constant of the grid, and what a
data field is (grade, density, fraction, code, class). Names of DAT (Micromine), CSV, DM/FBM (Datamine) and the
exports of other programs; matching ignores case, separators and units and takes names cut to 8–10 characters
(`importing.names`). The suggestion is a start — the wizard of the import lets the user correct every role.

Conventions of the programs (ADR 0023 §3, «Соглашения»):
* Micromine — a table: centres `EAST NORTH RL`, sizes `_EAST _NORTH _RL` (a size is named after its centre with `_`);
  the grid is not in a DAT; a Micromine CSV has it in line 3, `x0, dx, y0, dy, z0, dz, CENTRE|CORNER`
  (`micromine_grid_line`); without it the grid is inferred (`blocks.grid_infer`);
* Datamine — centres `XC YC ZC`, sizes `XINC YINC ZINC` (per record or implicit), the origin `XMORIG YMORIG ZMORIG`
  is the CORNER of the model, `NX NY NZ` the number of cells; `IJK` is never read from the file, it is recomputed
  (ADR 0009); the turn is `ANGLE1…3` about the axes `ROTAXIS1…3` (1 = X, 2 = Y, 3 = Z; the sign is provisional, the
  three-centre check of `grid_infer` catches a wrong one) (`datamine_hint`).

Pure: no I/O, no numpy at import time.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from bf_core.blocks.grid_infer import GridHint
from bf_core.blocks.schema import rotation_of_angles
from bf_core.importing.names import canon, match_column

__all__ = [
    "EMPTY_CODES",
    "ColumnRoles",
    "classify",
    "datamine_hint",
    "empty_candidates",
    "field_role",
    "find_roles",
    "guess_unit",
    "looks_like_block_model",
    "micromine_grid_line",
]

GEOMETRY = ("x", "y", "z", "dx", "dy", "dz")
EMPTY_CODES = (-99.0,)  # offered as «пусто» when it occurs in a field (the user may add others in the wizard)

# Centre of a block (priority order). Datamine XC, Micromine EAST, Surpac X_CENTRE, Vulcan XCENTRE, Leapfrog X /
# "Centroid X", Deswik and CSV exports XCENTROID.
CENTRE_NAMES = {
    "x": ("X", "XC", "XCENTRE", "XCENTER", "X_CENTRE", "X_CENTER", "XCENTROID", "CENTROID_X", "CENTRE_X", "CENTER_X",
          "EAST", "EASTING", "XMID", "MIDX", "MID_X", "XPT", "X_COORD", "ВОСТОК", "Х"),
    "y": ("Y", "YC", "YCENTRE", "YCENTER", "Y_CENTRE", "Y_CENTER", "YCENTROID", "CENTROID_Y", "CENTRE_Y", "CENTER_Y",
          "NORTH", "NORTHING", "YMID", "MIDY", "MID_Y", "YPT", "Y_COORD", "СЕВЕР", "У"),
    "z": ("Z", "ZC", "ZCENTRE", "ZCENTER", "Z_CENTRE", "Z_CENTER", "ZCENTROID", "CENTROID_Z", "CENTRE_Z", "CENTER_Z",
          "RL", "ELEV", "ELEVATION", "ZMID", "MIDZ", "MID_Z", "ZPT", "Z_COORD", "ОТМЕТКА"),
}  # fmt: skip
# Size of a block along an axis.
SIZE_NAMES = {
    "x": ("_X", "XINC", "DX", "XSIZE", "SIZE_X", "XDIM", "X_SIZE", "DIM_X", "XLENGTH", "X_LENGTH", "LENGTH_X", "XL",
          "BLOCK_SIZE_X", "SIZEX", "XBLOCK", "РАЗМЕР_X"),
    "y": ("_Y", "YINC", "DY", "YSIZE", "SIZE_Y", "YDIM", "Y_SIZE", "DIM_Y", "YLENGTH", "Y_LENGTH", "LENGTH_Y", "YL",
          "BLOCK_SIZE_Y", "SIZEY", "YBLOCK", "РАЗМЕР_Y"),
    "z": ("_Z", "ZINC", "DZ", "ZSIZE", "SIZE_Z", "ZDIM", "Z_SIZE", "DIM_Z", "ZLENGTH", "Z_LENGTH", "LENGTH_Z", "ZL",
          "BLOCK_SIZE_Z", "SIZEZ", "ZBLOCK", "РАЗМЕР_Z"),
}  # fmt: skip
# Datamine constants of the grid: the corner, the counts; the turns.
ORIGIN_NAMES = {"x": ("XMORIG", "X0"), "y": ("YMORIG", "Y0"), "z": ("ZMORIG", "Z0")}
COUNT_NAMES = {"x": ("NX",), "y": ("NY",), "z": ("NZ",)}
IJK_NAMES = ("IJK",)
FRAC_NAMES = ("FRAC", "FRACTION", "VOLFRAC", "VOL_FRAC", "VOLUME_FRACTION", "ДОЛЯ")
DENSITY_NAMES = ("SG", "DENSITY", "DENS", "BD", "BULK_DENSITY", "SPECIFIC_GRAVITY", "ПЛОТНОСТЬ", "ОБЪЕМНЫЙ_ВЕС")
CODE_NAMES = (
    "CODE",
    "ZONE",
    "DOMAIN",
    "ROCK",
    "ROCKCODE",
    "LITH",
    "LITHO",
    "LITHOLOGY",
    "WF",
    "ЗОНА",
    "ДОМЕН",
    "ПОРОДА",
)
CLASS_NAMES = ("CLASS", "CLASSIFICATION", "CATEGORY", "RESCAT", "RES_CAT", "RESOURCE_CLASS", "КАТЕГОРИЯ", "КЛАСС")

# Hole tables carry centres too (collars, surveys): a table with these names is not a block model.
_HOLE_LIKE = ("BHID", "HOLEID", "HOLE_ID", "HOLE", "FROM", "TO", "DEPTH", "AZ", "DIP", "AZIMUTH", "TDEPTH", "SAMPID")
_GRAMS = ("AU", "AG", "PT", "PD", "RH", "IR", "OS", "RU")
_PERCENT = ("CU", "PB", "ZN", "NI", "MO", "FE", "MN", "S", "CO", "SN", "W", "AS", "SB", "BI", "CR", "TI", "V", "AL",
            "SI", "MG", "CA", "K", "NA")  # fmt: skip
_CUT = re.compile(r"(_|^)CUT$|CUT$")
_HEAD = re.compile(r"[_\s\-.]")


def _find(names: Sequence[str], wanted: Sequence[str], used: set[str]) -> str | None:
    """Exact names (any case) first; then the looser match of `importing.names` among names not starting with «_»
    (in Micromine `_EAST` is a size, not a centre)."""
    upper = {n.upper(): n for n in names if n not in used}
    for w in wanted:
        if w in upper:
            return upper[w]
    return match_column([n for n in names if not n.startswith("_")], list(wanted), used)


def find_roles(names: Sequence[str]) -> dict[str, str | None]:
    """The columns of the centre (x, y, z) and the size (dx, dy, dz) of a block, None — not found."""
    roles: dict[str, str | None] = {}
    used: set[str] = set()
    for axis in "xyz":
        roles[axis] = _find(names, CENTRE_NAMES[axis], used)
        if roles[axis]:
            used.add(roles[axis])  # type: ignore[arg-type]
    for axis in "xyz":
        name = _find(names, SIZE_NAMES[axis], used)
        centre = roles[axis]
        if not name and centre:  # Micromine names a size after its centre: EAST → _EAST, RL → _RL
            name = _find(names, ("_" + centre.upper(),), used)
        roles["d" + axis] = name
        if name:
            used.add(name)
    return roles


def looks_like_block_model(names: Sequence[str]) -> bool:
    """Centres on all three axes and either sizes on all three or nothing that looks like a drillhole table."""
    roles = find_roles(names)
    if not all(roles[a] for a in "xyz"):
        return False
    if all(roles["d" + a] for a in "xyz"):
        return True
    from bf_core.importing.schema import SCHEMAS

    hole_names = next(f.aliases for f in SCHEMAS["collar"] if f.key == "hole_id")
    named = [n for n in names if n not in {roles[a] for a in "xyz"}]
    if match_column(named, list(_HOLE_LIKE)) or match_column(named, [a for a in hole_names if a not in ("NAME", "ID")]):
        return False
    return len(names) >= 4


class ColumnRoles:
    """What the columns of a file are. `geometry` — x y z dx dy dz → column or None; `origin`, `counts` — Datamine
    constants of the grid per axis; `ijk`, `frac`; `turns` — [(ANGLEn column, ROTAXISn column or None)]; `skip` — the
    columns that are not data fields (geometry, grid constants, IJK, turns); `fields` — the rest, in file order."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = list(names)
        self.geometry = find_roles(self.names)
        used = {n for n in self.geometry.values() if n}
        self.origin = {a: self._pick(ORIGIN_NAMES[a], used) for a in "xyz"}
        self.counts = {a: self._pick(COUNT_NAMES[a], used) for a in "xyz"}
        self.ijk = self._pick(IJK_NAMES, used)
        self.frac = self._pick(FRAC_NAMES, used, loose=True)
        self.turns = self._turns()
        grid_cols = [n for d in (self.origin, self.counts) for n in d.values() if n]
        for a in "xyz":  # X0 Y0 Z0 repeat XMORIG YMORIG ZMORIG in a `.dm`: not data
            if self.origin[a]:
                grid_cols += [n for n in self.names if n.upper() in ORIGIN_NAMES[a]]
        turn_cols = [n for t in self.turns for n in t if n]
        skip_set = {*used, *grid_cols, *turn_cols, *([self.ijk] if self.ijk else [])}
        self.skip = [n for n in self.names if n in skip_set]
        self.fields = [n for n in self.names if n not in skip_set]

    def _pick(self, aliases: Sequence[str], used: set[str], *, loose: bool = False) -> str | None:
        by_upper = {n.upper(): n for n in self.names if n not in used}
        for alias in aliases:
            if alias in by_upper:
                return by_upper[alias]
        if loose:
            return match_column([n for n in self.names if n not in used], list(aliases))
        return None

    def _turns(self) -> list[tuple[str, str | None]]:
        by_upper = {n.upper(): n for n in self.names}
        out: list[tuple[str, str | None]] = []
        for k in "123":
            angle = by_upper.get(f"ANGLE{k}")
            if angle:
                out.append((angle, by_upper.get(f"ROTAXIS{k}")))
        return out

    @property
    def has_size(self) -> bool:
        return all(self.geometry["d" + a] for a in "xyz")

    @property
    def has_centre(self) -> bool:
        return all(self.geometry[a] for a in "xyz")

    def as_dict(self) -> dict[str, Any]:
        return {"geometry": dict(self.geometry), "origin": dict(self.origin), "counts": dict(self.counts),
                "ijk": self.ijk, "frac": self.frac, "turns": [list(t) for t in self.turns], "skip": list(self.skip),
                "fields": list(self.fields)}  # fmt: skip


def classify(names: Iterable[str]) -> ColumnRoles:
    return ColumnRoles(list(names))


def guess_unit(name: str) -> str | None:
    """g/t for precious metals, % for base metals, by the element at the start of the name; ppm / ppb by the name."""
    upper = name.upper()
    head = re.sub(r"\d+$", "", _HEAD.split(upper, maxsplit=1)[0])
    if "PPM" in upper:
        return "ppm" if head in _GRAMS + _PERCENT or head.endswith("PPM") else None
    if "PPB" in upper:
        return "ppb" if head in _GRAMS + _PERCENT or head.endswith("PPB") else None
    if head in _GRAMS:
        return "г/т"
    if head in _PERCENT:
        return "%"
    return None


def field_role(name: str, kind: str = "number", *, varying: bool = True) -> str | None:
    """Role of a data field by its name: number — grade | density | frac, category — code | class; None — no guess.
    `varying` False (a constant field, e.g. `DENSITY` = 2,6 in every row) is never offered as a density or a grade."""
    key = canon(name)
    squeezed = key.replace("_", "")
    if kind == "category":
        if key in CLASS_NAMES or squeezed in CLASS_NAMES:
            return "class"
        return "code" if key in CODE_NAMES or squeezed in CODE_NAMES else None
    if key in FRAC_NAMES or squeezed in FRAC_NAMES:
        return "frac"
    if not varying:
        return None
    if key in DENSITY_NAMES or squeezed in DENSITY_NAMES:
        return "density"
    if guess_unit(name) is not None or _CUT.search(key):
        return "grade"
    return None


def empty_candidates(values: Any) -> list[float]:
    """The empty codes (`EMPTY_CODES`) that occur in a numeric array: the wizard offers them as «пусто» (never 0)."""
    import numpy as np

    arr = np.asarray(values)
    if arr.dtype.kind not in "fiu":
        return []
    return [c for c in EMPTY_CODES if bool((arr == c).any())]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) and abs(out) < 1e29 else None  # 1e30 — «absent» of Datamine


def datamine_hint(constants: Mapping[str, Any]) -> GridHint | None:
    """The grid of a Datamine model from the implicit fields of its header (`DmHeader.constants`): the corner
    `XMORIG YMORIG ZMORIG` (else `X0 Y0 Z0`), `NX NY NZ`, `XINC YINC ZINC` if they are implicit, the turn `ANGLEn` about
    `ROTAXISn` (a turn about X or Y → 422 `rotation_unsupported`). None — nothing of the grid in the file."""
    up = {str(k).strip().upper(): v for k, v in constants.items()}

    def triple(names: Mapping[str, Sequence[str]], cast: Any = float) -> tuple[Any, Any, Any] | None:
        out = []
        for a in "xyz":
            got = next((v for n in names[a] if (v := _num(up.get(n))) is not None), None)
            if got is None:
                return None
            out.append(cast(got))
        return (out[0], out[1], out[2])

    corner = triple(ORIGIN_NAMES)
    counts = triple(COUNT_NAMES, lambda v: int(round(v)))
    size = triple({a: (f"{a.upper()}INC",) for a in "xyz"})
    turns = []
    for k in "123":
        angle = _num(up.get(f"ANGLE{k}"))
        if angle:
            axis = _num(up.get(f"ROTAXIS{k}"))
            turns.append((int(axis) if axis is not None else 3, angle))
    rotation = rotation_of_angles(turns) if turns else 0.0
    if corner is None and counts is None and size is None and not turns:
        return None
    if size is not None and min(size) <= 0:
        size = None
    if counts is not None and min(counts) < 1:
        counts = None
    notes = ["Поворот Datamine (ANGLE/ROTAXIS) принят по справке Studio, знак предварительный: проверьте центры."]
    return GridHint(corner=corner, size=size, counts=counts, rotation=rotation, notes=tuple(notes if turns else ()))


def micromine_grid_line(cells: Sequence[str]) -> GridHint | None:
    """Line 3 of a Micromine CSV, `x0, dx, y0, dy, z0, dz, CENTRE|CORNER`: x0 y0 z0 — the centre of the first parent
    block (CENTRE, the default) or its corner (CORNER); the hint holds the corner. None — not a grid line."""
    vals = [str(c).strip() for c in cells if str(c).strip() != ""]
    if len(vals) < 6:
        return None
    try:
        nums = [float(v.replace(",", ".")) for v in vals[:6]]
    except ValueError:
        return None
    at = vals[6].upper() if len(vals) > 6 else "CENTRE"
    if at not in ("CENTRE", "CENTER", "CORNER", "ORIGIN") or min(nums[1], nums[3], nums[5]) <= 0:
        return None
    size = (nums[1], nums[3], nums[5])
    origin = (nums[0], nums[2], nums[4])
    half = (size[0] / 2, size[1] / 2, size[2] / 2)
    corner = origin if at in ("CORNER", "ORIGIN") else (origin[0] - half[0], origin[1] - half[1], origin[2] - half[2])
    return GridHint(corner=corner, size=size)
