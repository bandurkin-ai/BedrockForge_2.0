"""The profile of the blocks at a contact (ТЗ 8.3 + 8.8; Д-28, ТД28-2; ADR 0024 §Д-28; e18-api.md §8.5, §11): the
mean of the estimated field of the blocks by the steps of the signed distance of the block from the contact of two
domains — beside the profile of the samples of the contact analysis (Э13, `stats/contact.py`), in the same
answer format, to see whether the estimate keeps the character of the boundary.

    blocks_profile(model, *, field, a=None, b=None, domain_field=None, wireframe=None, status_field=None,
                   distance=None, step=None, threads=1, tick=None) -> dict
        {model_id, field, distance, step, weighted, counts, edges, mean, n, volume, a {label, n, mean},
         b {label, n, mean}, conventions}  — `contacts`, `holes` of the chart of the samples are not there
    block_distance(model, *, field, a, b, tick=None) -> float64 [rows]     the domain of a field of the model
    wireframe_distance(model, target, *, threads=1, tick=None) -> float64 [rows]     the domain of a wireframe
    row_distance(counts, size, p, codes, a, b, *, tick=None) -> (dist float64 [n], side int8 [n])
    centre_distance(counts, size, target, at) -> (D float64 [n], m int64 [n])

Two ways to say where the contact is, as in the contact analysis along the holes (ADR 0019 §4):

* `domain_field` — a category field of the model, `a` and `b` — two of its labels (the same names as the contact
  analysis of the samples). The contact is the boundary between the parent cells that hold blocks of A and those
  that hold blocks of B (a parent cell is A when one of its rows is A — the sub-blocks of a parent take the distance
  of their parent). Rows of another label, empty ones and rows of a model without the other domain at all are at no
  contact (NaN, never 0). Side A of the signed distance is below zero, B above (as in the chart of the samples).
* `wireframe` — a `geo.intersect.MeshTarget` of a closed wireframe: the distance of the centre of the block (of a
  sub-block — its own centre) to the surface, `geo.sdf` (searched up to D only: a block farther is `beyond`, its
  sign is told all the same); outside is side A («снаружи», d < 0), inside — B
  («внутри», d > 0), as the contact «до каркаса» of the samples.

The distance for a field of the model. The grid of the parents is a lattice of voxels; `scipy.ndimage.
distance_transform_edt` (exact Euclidean, the cell sizes as `sampling`: anisotropic cells and a turned grid are
fine, a turn keeps the distances) gives for every parent cell D — the distance between its centre and the centre of
the nearest parent cell of the other domain — and which cell that is. The contact lies half-way across the last
cell: d = D·(1 − 1/(2·m)), m — the largest of the shifts of the pair along the axes in cells (m = 1 — neighbours:
d = D/2; along an axis a shift of m cells: d = (m − ½)·step exactly, so the centres of the cells next to a flat
contact are half a step from it, at 1e−12 m). A parent holding both domains is at the contact: d = 0, the sign by
the domain of the row. The grid must have a regular step (tensors — 422 `variable_grid`). Memory ≈ 21 bytes a
parent cell and 22 a row besides the columns; one transform a side.

The profile. Blocks of the rows with `status_field` = 0 (the estimated ones, Д-27; without the field — all the rows)
and a finite value of `field`, a finite volume above zero (`Model.volume`: the volumes with FRAC; a block with an
empty FRAC has none) and |d| ≤ D (`distance`, 20 m) go into the steps of `step` (1 m) as the chart of the samples
takes its rows (`stats.contact.signed_chart`: the same steps, edges, mean by Σ w·c / Σ w with compensated sums —
exact for a constant: 1.0 and 3.0, not 2.9999999999999996; a step without blocks is null, never 0); the weight is
the volume, `volume` is its sum by steps. `counts` tells how many rows went where (`rows`, `no_contact`, `beyond`,
`no_value`, `no_weight`, `not_estimated`, `in_steps`). Refusals — ApiError 422 `bad_param` {param}, `no_values`,
`field_not_number` / `field_not_category` {param}, `variable_grid`; 404 `block_field_not_found`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

Tick = Callable[[float], None]

CHUNK = 1 << 20  # centres between two calls of `tick` (wireframe)
CONVENTIONS = (
    "Профиль блоков: расстояние — от центра блока до границы контакта доменов A и B; сторона A — меньше нуля, B — "
    "больше. Для домена из поля модели граница проходит по граням ячеек родителей (субблок — по родителю), для "
    "каркаса — по его поверхности (снаружи — A, внутри — B). Среднее шага — по объёму блоков со STATUS = 0; шаг "
    "без блоков пуст."
)
SIDES = ("снаружи", "внутри")  # `stats.contact.SIDES`


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, extra={"param": param})


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    if tick is None:
        return None
    return lambda f: tick(lo + (hi - lo) * float(f))


# --- the domain field: the distance transform of the grid of the parents -------------------------------------


def centre_distance(
    counts: Sequence[int], size: Sequence[float], target: np.ndarray, at: Sequence[np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    """For the cells `at` = (i, j, k) (intp arrays): the distance D of the centre of the cell to the centre of the
    nearest cell of `target` (bool, one per cell of the grid, i fastest) and the shift m of that pair — the largest
    of its |Δi|, |Δj|, |Δk| (0 — the cell is a target itself). `target` must have a cell; a ValueError otherwise."""
    from scipy.ndimage import distance_transform_edt

    nx, ny, nz = (int(c) for c in counts)
    grid = np.asarray(target, dtype=bool).reshape((nx, ny, nz), order="F")
    if not grid.any():
        raise ValueError("the target has no cell")
    dist, ind = distance_transform_edt(~grid, sampling=tuple(float(s) for s in size), return_distances=True,
                                       return_indices=True)  # fmt: skip
    i, j, k = at
    shift = np.maximum(np.maximum(np.abs(ind[0][i, j, k] - i), np.abs(ind[1][i, j, k] - j)),
                       np.abs(ind[2][i, j, k] - k)).astype(np.int64)  # fmt: skip
    return np.asarray(dist[i, j, k], dtype=np.float64), shift


def row_distance(
    grid: Any, p: Any, codes: Any, a: int, b: int, *, tick: Tick | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """(|d|, side) of the rows of a model on `grid` (`blocks.schema.GridV2`, a regular step): `p` — their parent
    cells (P), `codes` — their codes of the domain field; A, B — two codes. side: 0 — A, 1 — B, −1 — the row is
    of no domain of the two or the other domain is not in the model at all (|d| NaN). See the module docstring."""
    from bf_core.blocks import geometry

    if a == b:
        raise _bad("b", "Домены A и B должны различаться.", "Выберите два разных домена.")
    size = grid.regular("Контактный анализ блоков")
    codes = np.asarray(codes)
    p = np.asarray(p, dtype=np.uint64)
    i, j, k = geometry.row_ijk(grid, p)  # 422 for a parent off the grid
    flat = p.astype(np.intp)
    n = len(p)
    dist = np.full(n, np.nan, dtype=np.float64)
    side = np.full(n, -1, dtype=np.int8)
    rows = {0: codes == a, 1: codes == b}
    masks = {}
    for s, mine in rows.items():
        m = np.zeros(grid.cells, dtype=bool)
        m[flat[mine]] = True
        masks[s] = m
    for s, other in ((0, 1), (1, 0)):
        mine = np.flatnonzero(rows[s])
        if not len(mine) or not masks[other].any():
            continue
        at = (i[mine], j[mine], k[mine])
        d, shift = centre_distance(grid.counts, size, masks[other], at)
        with np.errstate(divide="ignore", invalid="ignore"):
            across = np.where(shift > 0, d * (1.0 - 0.5 / np.maximum(shift, 1)), 0.0)
        dist[mine], side[mine] = across, s
        if tick is not None:
            tick(0.5 * (s + 1))
    return dist, side


def _codes(labels: Sequence[str], a: Any, b: Any) -> tuple[int, int]:
    index = {name: c for c, name in enumerate(labels)}
    for key, title, name in (("a", "A", a), ("b", "B", b)):
        if not isinstance(name, str) or name == "":
            raise _bad(key, f"Не выбран домен {title}.", "Выберите домен из значений поля домена модели.")
        if name not in index:
            raise _bad(key, f"В поле домена модели нет значения «{name[:80]}».",
                       f"Выберите домен {title} из значений поля домена.")  # fmt: skip
    if a == b:
        raise _bad("b", "Домены A и B должны различаться.", "Выберите два разных домена.")
    return index[a], index[b]


def _field(model: Any, name: Any, kind: str, param: str) -> dict[str, Any]:
    """The field of the model of that kind: the 404 `block_field_not_found` of the model, 422 for another kind."""
    if not isinstance(name, str) or name == "":
        raise _bad(param, "Не выбрано поле модели.", "Выберите поле из списка полей модели.")
    spec = dict(model.field(name))
    if spec.get("kind") != kind:
        word, code = ("числовым", "field_not_number") if kind == "number" else ("категорией", "field_not_category")
        raise ApiError(422, code, f"Поле «{str(spec['name'])[:80]}» не является {word}.",
                       "Выберите другое поле модели.", extra={"param": param})  # fmt: skip
    return spec


def _read(model: Any, spec: dict[str, Any]) -> np.ndarray:
    return np.asarray(model.columns([str(spec["name"])])[str(spec["name"])])


def block_distance(model: Any, *, field: str, a: Any, b: Any, tick: Tick | None = None) -> np.ndarray:
    """The signed distance of the rows of the model from the contact of the domains `a` (below 0) and `b` (above 0)
    of the category field `field`, metres; NaN — no contact (see the module docstring); a row in a parent that
    holds both: −0.0 (A) or 0.0 (B)."""
    spec = _field(model, field, "category", "domain_field")
    ca, cb = _codes(model.labels(str(spec["name"])), a, b)
    dist, side = row_distance(model.grid, model.p(), _read(model, spec), ca, cb, tick=tick)
    return _signed(dist, side)


def _signed(dist: np.ndarray, side: np.ndarray) -> np.ndarray:
    return np.where(side == 0, -dist, np.where(side == 1, dist, np.nan))  # type: ignore[no-any-return]


# --- the domain of a wireframe ---------------------------------------------------------------------------------


def _wireframe_rows(
    model: Any, p: np.ndarray, target: Any, reach: float, threads: int, tick: Tick | None
) -> tuple[np.ndarray, np.ndarray]:
    from bf_core.blocks import geometry
    from bf_core.geo import sdf

    sw = model.subs()
    s, w = (None, None) if sw is None else sw
    n = len(p)
    dist = np.full(n, np.nan, dtype=np.float64)
    side = np.full(n, -1, dtype=np.int8)
    for lo in range(0, n, CHUNK):
        hi = min(lo + CHUNK, n)
        part = slice(lo, hi)
        centres = geometry.centres(model.grid, p[part], None if s is None else s[part], None if w is None else w[part])
        d = sdf.signed_distance_to(target, centres, reach=reach, threads=threads)  # inside < 0; inf — beyond
        dist[lo:hi] = np.abs(d)
        side[lo:hi] = np.where(np.isnan(d), -1, np.where(d > 0.0, 0, 1))  # outside — A, inside and on it — B
        if tick is not None:
            tick(hi / n)
    return dist, side


def wireframe_distance(model: Any, target: Any, *, threads: int = 1, tick: Tick | None = None) -> np.ndarray:
    """The distance of the centres of the rows to the closed wireframe `target` (`MeshTarget`), the contact sign:
    outside (A) below 0, inside (B) above 0 — the negative of `geo.sdf`."""
    dist, side = _wireframe_rows(model, model.p(), target, math.inf, threads, tick)
    return _signed(dist, side)


# --- the profile -------------------------------------------------------------------------------------------------


def blocks_profile(
    model: Any, *, field: Any, a: Any = None, b: Any = None, domain_field: Any = None, wireframe: Any = None,
    status_field: Any = None, distance: Any = None, step: Any = None, threads: int = 1, tick: Tick | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The profile of the blocks `field` at the contact: `domain_field` with its labels `a`, `b`, or `wireframe` (a
    `MeshTarget`) — exactly one of them; `status_field` — the numeric field of the estimate whose 0 marks the
    blocks that count; `distance`, `step` — D and b, metres (None — 20 and 1). The answer — see the module
    docstring. `tick(fraction)` may raise to cancel."""
    from bf_core.blocks import geometry
    from bf_core.stats import contact as sc

    if (domain_field is None) == (wireframe is None):
        raise _bad("domain_field", "Задайте домен блоков: поле модели или каркас.",
                   "Выберите поле домена модели либо каркас.")  # fmt: skip
    st = sc.steps_of(distance, step)  # StatsError `bad_param` before the model is read
    vspec = _field(model, field, "number", "field")
    sspec = None if status_field in (None, "") else _field(model, status_field, "number", "status_field")
    if wireframe is None:
        dspec = _field(model, domain_field, "category", "domain_field")
        ca, cb = _codes(model.labels(str(dspec["name"])), a, b)
    p = model.p()
    if tick is not None:
        tick(0.05)
    if wireframe is not None:
        dist, side = _wireframe_rows(model, p, wireframe, st.distance, threads, _part(tick, 0.05, 0.6))
        labels = SIDES
    else:
        dist, side = row_distance(model.grid, p, _read(model, dspec), ca, cb, tick=_part(tick, 0.05, 0.6))
        labels = (str(a), str(b))
    values = _read(model, vspec).astype(np.float64, copy=True)
    counts: dict[str, int] = {}
    if sspec is not None:
        with np.errstate(invalid="ignore"):
            done = _read(model, sspec) == 0.0
        counts["not_estimated"] = int(np.count_nonzero((side >= 0) & ~done))
        values[~done] = np.nan
    sw = model.subs()
    frac = model.role_field("frac")
    volume = geometry.row_volumes(model.grid, p, None if sw is None else sw[0], None if sw is None else sw[1],
                                  None if frac is None else _read(model, frac))  # fmt: skip
    chart = sc.signed_chart(dist, side, values, volume, labels, CONVENTIONS, distance=st.distance, step=st.step,
                            counts=counts, tick=_part(tick, 0.6, 1.0))  # fmt: skip
    out = {"model_id": int(model.id), "field": str(vspec["name"])}
    for key in ("distance", "step", "weighted", "counts", "edges", "mean", "n"):
        out[key] = chart[key]
    out["volume"] = chart["weight"]
    out.update(a=chart["a"], b=chart["b"], conventions=chart["conventions"])
    return out


def warm() -> None:
    """Compile the kernels of the distance on a few cells (the `stats`/`geo` steps of the warm-up)."""
    from bf_core.blocks.schema import GridV2

    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [3, 2, 2], "size": [1.0, 1.0, 1.0]})
    p = np.arange(12, dtype=np.uint64)
    row_distance(grid, p, np.array([0, 0, 1, 0, 0, 1, 1, 1, 0, 0, 1, 2], dtype=np.int32), 0, 1)
