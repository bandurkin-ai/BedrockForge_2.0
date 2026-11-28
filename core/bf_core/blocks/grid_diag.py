"""The diagnosis of a block file the grid cannot hold (ТЗ 7.31, 8.7.2; ADR 0030 decisions 5–6; Д-45 А6): why the
blocks do not lie, in numbers the user (and the developer, without the file) can read, and what the user may do.

    diag = diagnose(scan, found, c, d, rows=n, columns={"x": "R 8", …}, lost=unaligned)
    choices = choices_of(diag)                                   # [{id: "grid", …}, {id: "cancel", …}]
    raise unaligned.error(diag, choices)                         # 422 blocks_unaligned {…, diag, choices}

It runs only on a refusal. The statistics of the whole file are in `GridScan` (the distinct (low edge, size) pairs of
every axis with their counts); the joint fit of the candidates is counted on a sample of at most `SAMPLE` rows taken
at an equal step (`Sample`: the same rows, bit for bit, whatever the portions). The answer is small (≤ 16 КБ): the
lists are cut (`MAX_SIZES`, `MAX_FRACTIONS`, `MAX_RESIDUES`, `MAX_CANDIDATES`).

`diag` = {rows, sample, columns, axes: [{axis, parent, origin, sizes [{v, n}], distinct, fractions [{v, q}], div |
null, residues [{v, n}], quantum, suggest_tol | null, fit {total, aligned, before, crosses, off}, overflow}],
candidates [{size, corner, div, tol, aligned, crosses, off_grid, off_sub, current}], causes [код], text [строки]}.

Causes (`CAUSES`): `div_too_fine` (the lcm of the denominators is over 128), `off_lattice` (sizes with no fraction
of the parent: sub-blocks of any size), `two_origins` (the whole parents lie on two grids), `rounded` (the numbers are
rounded: they lie on the lattice at a bigger tolerance), `crosses_parent` (blocks over the border of the parent: the
parent is bigger), `off_grid` (blocks before the corner). Float64 throughout; a share is a number in [0; 1].
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from . import grid_lattice as gl
from .grid_infer import CROSSES_PARENT, INVALID, OFF_GRID, OFF_SUBCELLS, OK, GridScan, InferredGrid, Unaligned

__all__ = ["CAUSES", "SAMPLE", "Sample", "choices_of", "current_choices", "diagnose"]

SAMPLE = 200_000  # rows of the sample of the joint fit
MAX_SIZES = 12
MAX_FRACTIONS = 12
MAX_RESIDUES = 5
MAX_CANDIDATES = 5
MAX_TEXT = 12
TOLS = (0.001, 0.005, 0.01, 0.05)  # m: what a rounded column may be off, tried in turn
CAUSES = ("div_too_fine", "off_lattice", "two_origins", "rounded", "crosses_parent", "off_grid")
AXES = "XYZ"
HEIGHT = "высот"  # the word of the Z axis in the texts


class Sample:
    """The rows of the sample: every `step`-th row of the file (by the global row number: the same in any portions)."""

    def __init__(self, rows: int, limit: int = SAMPLE) -> None:
        self.step = max(1, -(-int(rows) // int(limit))) if rows > 0 else 1
        self._c: list[np.ndarray] = []
        self._d: list[np.ndarray] = []
        self.sized = False

    def add(self, first: int, centres: np.ndarray, sizes: np.ndarray | None) -> None:
        """A portion: the rows `first` … `first + n − 1` of the file."""
        take = np.flatnonzero((first + np.arange(len(centres))) % self.step == 0)
        if not len(take):
            return
        self._c.append(np.asarray(centres, dtype=np.float64)[take])
        if sizes is not None:
            self.sized = True
            self._d.append(np.asarray(sizes, dtype=np.float64)[take])

    def arrays(self) -> tuple[np.ndarray, np.ndarray | None]:
        c = np.concatenate(self._c) if self._c else np.empty((0, 3))
        return c, (np.concatenate(self._d) if self._d and self.sized else None)


def _r(v: float, places: int = 6) -> float:
    return float(round(float(v), places))


def _top(values: np.ndarray, weights: np.ndarray, k: int) -> tuple[list[dict[str, float | int]], int]:
    """The `k` most frequent values (rounded to 1e-6) with their counts, and the number of distinct values."""
    if not len(values):
        return [], 0
    u, inverse = np.unique(np.round(values, 6), return_inverse=True)
    n = np.bincount(inverse, weights=weights, minlength=len(u))
    order = np.lexsort((u, -n))[:k]
    return [{"v": float(u[i]), "n": int(n[i])} for i in order], len(u)


def _axis_fit(lo: np.ndarray, sz: np.ndarray, n: np.ndarray, parent: float, origin: float, tol: float
              ) -> tuple[gl.AxisFit, np.ndarray, np.ndarray]:  # fmt: skip
    """The fit and the residues (low edge inside its parent) of the pairs, those before the corner at 0."""
    fit = gl.fit_axis(lo, sz, n, parent=parent, origin=origin, tol=tol)
    rel = lo - origin
    rho = np.maximum(rel - np.floor((rel + tol) / parent) * parent, 0.0)
    return fit, rho, rel


def _suggest(lo: np.ndarray, sz: np.ndarray, n: np.ndarray, parent: float, origin: float, tol: float
             ) -> tuple[float | None, gl.AxisFit | None]:  # fmt: skip
    """The smallest tolerance of `TOLS` (over the one in use) at which every block of the axis lies; None — no
    tolerance of the list does (a bigger one would only drag some blocks to the nearest line, not explain them)."""
    base = gl.fit_axis(lo, sz, n, parent=parent, origin=origin, tol=tol)
    if base.aligned == base.total:
        return None, None  # it lies already
    for t in TOLS:
        if t <= tol:
            continue
        fit = gl.fit_axis(lo, sz, n, parent=parent, origin=origin, tol=t)
        if fit.aligned == fit.total:
            return t, fit
    return None, None


def _axis(scan: GridScan, found: InferredGrid, a: int) -> tuple[dict[str, Any], list[str]]:
    """The entry of `axes` and the causes of one axis."""
    lo, sz, n, over = scan.axis_pairs(a)
    parent, origin, tol = float(found.grid.regular()[a]), found.origin[a], found.tol[a]
    entry: dict[str, Any] = {"axis": AXES[a], "parent": _r(parent), "origin": _r(origin, 9), "quantum": _r(tol, 9),
                             "overflow": bool(over)}  # fmt: skip
    if over or not len(lo):
        entry.update(sizes=[], distinct=0, fractions=[], div=None, residues=[], suggest_tol=None, fit=None)
        return entry, ["off_lattice"] if over else []
    fit, rho, rel = _axis_fit(lo, sz, n, parent, origin, tol)
    keep = rel >= -tol
    sizes, distinct = _top(sz, n, MAX_SIZES)
    residues, _ = _top(rho[keep], n[keep], MAX_RESIDUES)
    values, w = np.concatenate([rho[keep], sz]), np.concatenate([n[keep], n])
    top, _ = _top(values, w, MAX_FRACTIONS)
    q = gl.denominators(np.asarray([t["v"] for t in top]), parent, tol) if top else np.empty(0, dtype=int)
    fractions = [{"v": t["v"], "q": int(k)} for t, k in zip(top, q.tolist(), strict=True)]
    whole_div = fit.div if fit.off == 0 and fit.aligned == fit.total else None
    suggest, _fit = _suggest(lo, sz, n, parent, origin, tol) if fit.aligned < fit.total else (None, None)
    entry.update(sizes=sizes, distinct=int(distinct), fractions=fractions, div=whole_div, residues=residues,
                 suggest_tol=suggest, fit={"total": fit.total, "aligned": fit.aligned, "before": fit.before,
                                           "crosses": fit.crosses, "off": fit.off})  # fmt: skip
    causes: list[str] = []
    biggest = float(scan.extent()[2][a])
    whole = sz >= biggest - max(tol, gl.FLOOR)
    if whole.any():  # the biggest blocks of one grid all sit on one lattice of their own size: two modes — two grids
        r = np.mod(lo[whole] - float(lo[whole].min()), biggest)
        r[r > biggest - max(tol, gl.FLOOR)] = 0.0
        modes, _ = _top(r, n[whole], MAX_RESIDUES)
        if len(modes) >= 2 and modes[1]["n"] >= 0.1 * float(n[whole].sum()):
            causes.append("two_origins")  # first: the blocks over the borders are its consequence
            entry["residues"] = modes
    if fit.before:
        causes.append("off_grid")
    if fit.crosses:
        causes.append("crosses_parent")
    if fit.off:
        bare = float(sum(t["n"] for t, k in zip(top, q.tolist(), strict=True) if k == 0))
        spread = bare / max(fit.total, 1)
        causes.append("off_lattice" if spread >= 0.01 or distinct > 3 * gl.MAX_DIV else "div_too_fine")
    if suggest is not None:
        causes.append("rounded")
    return entry, causes


def _options(scan: GridScan, found: InferredGrid, a: int, entry: Mapping[str, Any]) -> list[tuple[float, float, float]]:
    """The parents and corners worth trying on an axis: (parent, corner, tolerance), the best share first."""
    lo, sz, n, over = scan.axis_pairs(a)
    cur = (float(found.grid.regular()[a]), float(found.origin[a]), float(found.tol[a]))
    out = [cur]
    if over or not len(lo):
        return out
    biggest = float(scan.extent()[2][a])
    pick = gl.choose_axis(lo, sz, n, biggest=biggest, tol=cur[2], hint_parent=None, hint_origin=None)
    ranked = sorted(pick.tried, key=lambda t: -t[2])
    for parent, origin, _share in ranked:
        t = cur[2]
        suggest, _fit = _suggest(lo, sz, n, parent, origin, t)
        opt = (float(parent), float(origin), float(suggest if suggest is not None else t))
        if all(abs(opt[0] - o[0]) > 1e-9 or abs(opt[1] - o[1]) > 1e-9 or abs(opt[2] - o[2]) > 1e-12 for o in out):
            out.append(opt)
        if len(out) >= 3:
            break
    if entry.get("suggest_tol") is not None:  # the current parent with the tolerance that makes it lie
        opt = (cur[0], cur[1], float(entry["suggest_tol"]))
        if opt not in out:
            out.insert(1, opt)
    return out[:3]


def _candidates(scan: GridScan, found: InferredGrid, axes: Sequence[Mapping[str, Any]], c: np.ndarray,
                d: np.ndarray | None) -> list[dict[str, Any]]:  # fmt: skip
    """The grids worth offering, the best first: every combination of the options of the axes counted on the sample."""
    options = [_options(scan, found, a, axes[a]) for a in range(3)]
    sized = d is not None
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for i, ox in enumerate(options[0]):
        for j, oy in enumerate(options[1]):
            for k, oz in enumerate(options[2]):
                opts = (ox, oy, oz)
                parent = [o[0] for o in opts]
                origin = [o[1] for o in opts]
                tols = [o[2] for o in opts]
                div = []
                for a in range(3):
                    lo, sz, n, over = scan.axis_pairs(a)
                    div.append(1 if over or not len(lo) or not sized else
                               gl.fit_axis(lo, sz, n, parent=parent[a], origin=origin[a], tol=tols[a]).div)  # fmt: skip
                key = (*(round(p, 6) for p in parent), *(round(o, 6) for o in origin), *div,
                       *(round(t, 9) for t in tols))  # fmt: skip
                if key in seen:
                    continue
                seen.add(key)
                grid = scan.assemble(parent, div, origin, tols, manual=False)
                cells = grid.rows_of(c, d)
                ok = cells.bad != INVALID
                total = max(int(ok.sum()), 1)
                share = {"aligned": OK, "crosses": CROSSES_PARENT, "off_grid": OFF_GRID, "off_sub": OFF_SUBCELLS}
                out.append({
                    "size": [_r(v) for v in parent], "corner": [_r(v, 9) for v in grid.grid.corner],
                    "div": [int(v) for v in grid.grid.sub.div], "tol": [_r(t, 9) for t in tols],
                    **{name: _r(int((cells.bad == code).sum()) / total, 4) for name, code in share.items()},
                    "current": bool((i, j, k) == (0, 0, 0) and tuple(div) == tuple(found.grid.sub.div)),
                })  # fmt: skip
    out.sort(key=lambda x: (-x["aligned"], not x["current"], x["size"], x["corner"]))
    return out[:MAX_CANDIDATES]


def _count(n: float) -> str:
    return f"{int(n):,}".replace(",", " ")


def _text(axes: Sequence[Mapping[str, Any]], causes: dict[str, list[str]]) -> list[str]:
    """The sentences for the wizard, the main cause first; Russian."""
    lines: list[str] = []
    for a, entry in enumerate(axes):
        ax, fit = AXES[a], entry.get("fit")
        for cause in causes.get(ax, []):
            if cause == "off_lattice" and entry.get("overflow"):
                lines.append(f"По {ax} больше {_count(gl.MAX_PAIRS)} различных положений блоков: общего деления нет.")
            elif cause == "off_lattice":
                what = f"{_count(entry['distinct'])} различных {HEIGHT if ax == 'Z' else 'размеров'}"
                lines.append(f"По {ax} {what}, общего деления родителя нет — субблоки произвольного размера.")
            elif cause == "div_too_fine":
                fr = ", ".join(f"1/{f['q']}" for f in entry["fractions"] if f["q"] > 1)[:60]
                lines.append(f"По {ax} общее деление родителя больше 128" + (f" (доли {fr})." if fr else "."))
            elif cause == "crosses_parent" and fit:
                lines.append(f"По {ax} блоков через границу родителя {entry['parent']:g} м: "
                             f"{_count(fit['crosses'])} — возможно, родитель крупнее.")  # fmt: skip
            elif cause == "off_grid" and fit:
                lines.append(f"По {ax} блоков за началом сетки: {_count(fit['before'])}.")
            elif cause == "two_origins":
                lines.append(f"По {ax} целые родители лежат на двух сетках: модель склеена из двух частей?")
            elif cause == "rounded" and entry.get("suggest_tol") is not None:
                t = f"{entry['suggest_tol']:g}".replace(".", ",")
                lines.append(f"По {ax} числа записаны с округлением: с допуском {t} м блоки ложатся.")
    return lines[:MAX_TEXT]


def diagnose(scan: GridScan, found: InferredGrid, c: np.ndarray, d: np.ndarray | None, *, rows: int,
             columns: Mapping[str, str] | None = None, lost: Unaligned | None = None) -> dict[str, Any]:  # fmt: skip
    """The `diag` of a refusal (module docstring). `c`, `d` — the sample (`Sample.arrays`), `rows` — the rows of the
    file, `columns` — the type of the columns of the centre and the size ({"x": "R 8", "dx": "N 10.2"})."""
    axes: list[dict[str, Any]] = []
    causes: dict[str, list[str]] = {}
    for a in range(3):
        entry, found_causes = _axis(scan, found, a)
        axes.append(entry)
        causes[AXES[a]] = found_causes
    refused = lost.by_reason if lost is not None else {}
    if refused.get(OFF_GRID) and not any("off_grid" in v for v in causes.values()):
        causes["X"].append("off_grid")
    all_causes = [x for x in CAUSES if any(x in v for v in causes.values())]
    cand = _candidates(scan, found, axes, c, d) if len(c) else []
    text = _text(axes, causes)
    if not text and cand:  # the grid of the wizard (or of the file) is wrong, the blocks themselves are fine
        share = f"{cand[0]['aligned'] * 100:.1f}".replace(".", ",")
        text = [f"Заданная сетка не подходит блокам; лучшая найденная укладывает {share} % блоков."]
    return {
        "rows": int(rows), "sample": int(len(c)), "columns": dict(columns or {}), "axes": axes, "candidates": cand,
        "causes": all_causes, "text": text,
    }  # fmt: skip


LIST_CHOICE = {"id": "list", "label": "Импортировать списком блоков",
               "note": "показ, фильтры, калькулятор, отчёт, экспорт; оценка и карьер — после регуляризации"}


def choices_of(diag: Mapping[str, Any], rotation: float = 0.0) -> list[dict[str, Any]]:
    """What the user may do (ADR 0030 decisions 6, 14): set the grid by hand — the best candidate fills the form —,
    import the model as a list of blocks (nothing is chosen for the user), or not import the file."""
    out: list[dict[str, Any]] = []
    cand = diag.get("candidates") or []
    if cand:
        best = cand[0]
        out.append({"id": "grid", "label": "Задать сетку вручную",
                    "grid": {"corner": best["corner"], "size": best["size"], "div": best["div"],
                             "tol": max(best["tol"]), "rotation": float(rotation)},
                    "aligned": best["aligned"]})  # fmt: skip
    else:
        out.append({"id": "grid", "label": "Задать сетку вручную", "grid": None, "aligned": 0.0})
    out += [dict(LIST_CHOICE), {"id": "cancel", "label": "Не импортировать этот файл"}]
    return out


def current_choices(found: InferredGrid) -> list[dict[str, Any]]:
    """The same choices for a refusal with no `diag` (`rotation_mismatch`): the form of the grid starts from the grid
    that was inferred."""
    g = found.grid
    grid = {"corner": [_r(v, 9) for v in g.corner], "size": [_r(v) for v in g.regular()],
            "div": [int(v) for v in g.sub.div], "tol": _r(max(found.tol), 9),
            "rotation": float(g.rotation)}  # fmt: skip
    return [{"id": "grid", "label": "Задать сетку вручную", "grid": grid, "aligned": None}, dict(LIST_CHOICE),
            {"id": "cancel", "label": "Не импортировать этот файл"}]  # fmt: skip

