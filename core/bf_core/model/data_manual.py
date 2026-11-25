"""Hand data and grades of a model (ТЗ 8.9.2; ADR 0026 решения 5, 6).

Hand data are layers of strings (`objects(kind="line_layer")`, ТЗ 7.18) given to a group with a role: the vertices of
its lines and the points every Δ_т along the segments, with the value of the role —

    contact 0 · inside −D_max/2 · outside +D_max/2

    pts = layer_points(xyz, offsets, closed, role="contact", d_max=30, step=5, group=1, layer="Контакт")
    pts = group_layers(project, spec, gid, used, tick)       # every layer of the spec given to group `gid`

The vertices of a closed line are those before its repeated first vertex; a vertex without a Z (NaN) is not a point
(and no point is put on the segments that end in it) — never a zero. A point of a line between two vertices carries the
number of the first of them in `vertex`.

Grades of shells (решение 6): `transform` of the values of a numeric shell and the thresholds of its levels

    graded = grade_points(xyz, g, transform="linear" | "log" | "nscore", thresholds=[…])
    values, notes = graded                    # T(g) per row; NaN — no point (g missing, g ≤ 0 in log, no coordinates)
    graded.levels                             # T(t) of every threshold; graded.indicators — [g ≥ t] − 0,5, (n, K)

`nscore` is the normal score of the values with the ties on the mean rank (`stats.transforms.NormalScoreTransform`):
equal grades have one score, whatever the order of the rows; a threshold goes through the same table. An indicator
shell does not use T: its points carry the grade itself and `data.indicator(g, t)` gives its values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .data import KIND, WING_NONE, Points, Tick, check_size
from .spec_parts import ROLES, TRANSFORMS

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from .spec import ModelSpec

F8 = NDArray[np.float64]
_ROLE_FACTOR = {"contact": 0.0, "inside": -0.5, "outside": 0.5}  # × D_max


def _bad(message: str, hint: str, param: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- layers of strings -------------------------------------------------------------------------------------------


def layer_points(
    xyz: Any,
    offsets: Any,
    closed: Any = None,
    *,
    role: str,
    d_max: float,
    step: float,
    group: int,
    layer: int | str = 0,
    name: str | None = None,
) -> Points:
    """The points of one layer of strings with a role (module docstring). `xyz` (v, 3) float64, `offsets` int64
    (lines + 1), `closed` per line (a closed line repeats its first vertex); `layer` — the number or the name of the
    layer (the source of its points is «слой …»)."""
    if role not in ROLES:
        raise _bad("Роль слоя неизвестна.", "Выберите «Контакт», «Внутри» или «Снаружи».", "manual.role")
    p = np.ascontiguousarray(xyz, dtype=np.float64).reshape(-1, 3)
    off = np.ascontiguousarray(offsets, dtype=np.int64)
    v = p.shape[0]
    if off.shape[0] < 1 or off[0] != 0 or off[-1] != v or bool((np.diff(off) <= 0).any()):
        raise ValueError("offsets must start at 0, end at the number of vertices and have no empty line")
    nl = off.shape[0] - 1
    cl = np.zeros(nl, dtype=bool) if closed is None else np.asarray(closed, dtype=bool).reshape(-1)
    line = np.repeat(np.arange(nl), np.diff(off))
    last = off[1:] - 1
    repeat = np.zeros(v, dtype=bool)
    repeat[last[cl & (np.diff(off) > 1)]] = True  # the repeated first vertex of a closed line is not a point
    finite = np.asarray(np.isfinite(p).all(axis=1), dtype=bool)
    value = _ROLE_FACTOR[role] * float(d_max)
    seg = np.flatnonzero((line[1:] == line[:-1]) & finite[1:] & finite[:-1]) if v > 1 else np.zeros(0, np.int64)
    length = np.sqrt(((p[seg + 1] - p[seg]) ** 2).sum(axis=1)) if seg.size else np.zeros(0)
    cells = np.maximum(1, np.ceil(length / float(step) - 1e-9).astype(np.int64))  # pieces of a segment
    extra = cells - 1
    check_size(int((finite & ~repeat).sum()) + int(extra.sum()))
    vert = np.flatnonzero(finite & ~repeat)
    first = np.repeat(seg, extra)  # the segment of every interior point
    k = np.arange(int(extra.sum())) - np.repeat(np.cumsum(extra) - extra, extra) + 1
    frac = (k / np.repeat(cells, extra))[:, None]
    inner = p[first] + frac * (p[first + 1] - p[first])
    pts = np.concatenate([p[vert], inner]) + 0.0
    n = int(pts.shape[0])
    skipped = int((~finite).sum())
    notes = [f"Слой «{name or layer}»: вершин без координат — {skipped}, точек в них нет."] if skipped else []
    label = f"слой «{name}»" if name else f"слой {layer}"
    return _assembled(Points(pts, np.full(n, value), np.full(n, KIND["manual"], np.int8), np.full(n, group, np.int32),
                             np.full(n, WING_NONE, np.int8), np.zeros(n, np.int32), np.full((n, 2), np.nan),
                             np.concatenate([vert, first]).astype(np.int64), [label] if n else [],
                             {"points": n}, notes))  # fmt: skip


def _assembled(p: Points) -> Points:
    from .data import assemble

    return assemble([p])


def read_layers(project: Any, layer_ids: Sequence[int]) -> Iterator[tuple[int, Any]]:
    """The layers (`formats.lineset.LineSet`) of the project by id, one at a time (404 `layer_not_found`)."""
    from bf_core.db import dialect
    from bf_core.db.schema import line_layers
    from bf_core.lines import store as ls
    from bf_core.storage.blobs import BlobStore

    store = BlobStore(project.folder)
    for lid in layer_ids:
        with dialect.read_tx(project.engine) as conn:
            if conn.execute(sa.select(line_layers.c.object_id).where(line_layers.c.object_id == lid)).first() is None:
                raise ApiError(404, "layer_not_found", "Слой стрингов не найден.", "Обновите дерево «Линии».")
            yield lid, ls.read_layer(conn, store, lid)


def group_layers(project: Any, spec: ModelSpec, gid: int, used: int, tick: Tick | None = None) -> list[Points]:
    """The points of every layer given to group `gid` (`spec.manual`), in the order of the spec; `used` — the points
    of the model so far (422 `too_many_points` over 2 million)."""
    mine = [m for m in spec.manual if m.group == gid]
    d_max, step = (spec.data.d_max, spec.data.step) if spec.data else (30.0, 5.0)
    if spec.data is None:
        from .spec_parts import D_MAX, STEP

        d_max, step = D_MAX, STEP
    out: list[Points] = []
    roles = {m.layer_id: m.role for m in mine}
    for lid, ls in read_layers(project, [m.layer_id for m in mine]):
        if tick is not None:
            tick(None, f"Слой «{ls.name}»")
        p = layer_points(ls.xyz, ls.offsets if ls.offsets is not None else np.arange(len(ls.xyz) + 1), ls.closed,
                         role=roles[lid], d_max=d_max, step=step, group=gid, layer=lid, name=ls.name)  # fmt: skip
        used += p.n
        check_size(used)
        out.append(p)
    return out


# --- grades --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class GradeValues:
    values: F8  # T(g) per row; NaN — no point
    notes: list[str]
    levels: F8 | None  # T(t) of every threshold (None — no thresholds)
    indicators: F8 | None  # [g ≥ t] − 0,5, (n, K); NaN where there is no point

    def __iter__(self) -> Iterator[Any]:
        """`values, notes = grade_points(…)`."""
        yield self.values
        yield self.notes


def _nscore_table(g: F8) -> Any:
    """The normal-score table of the values: unique ascending values, the score of the mean rank of each (ties are
    one score, whatever the order of the rows), the end values as the trimming limits."""
    from scipy.special import ndtri

    from bf_core.stats.transforms import NormalScoreTransform

    uniq, count = np.unique(g, return_counts=True)
    p = (np.cumsum(count) - 0.5 * count) / float(g.shape[0])
    return NormalScoreTransform(uniq, np.asarray(ndtri(p), dtype=np.float64), float(uniq[0]), float(uniq[-1]))


def grade_points(
    xyz: Any, g: Any, *, transform: str = "linear", thresholds: Sequence[float] | None = None
) -> GradeValues:
    """The values of a shell's data (module docstring). A row with a missing or infinite g, with a coordinate that is
    NaN, or (log) with g ≤ 0 is no point: its value is NaN and it is counted in the notes."""
    if transform not in TRANSFORMS:
        raise _bad(
            "Преобразование неизвестно.", "Выберите «Линейное», «Логарифм» или «Нормальные оценки».", "transform"
        )
    p = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    v = np.asarray(g, dtype=np.float64).reshape(-1)
    if p.shape[0] != v.shape[0]:
        raise ValueError("xyz and g differ in length")
    placed = np.isfinite(p).all(axis=1)
    ok = np.isfinite(v) & placed
    notes: list[str] = []
    if (~np.isfinite(v)).any():
        notes.append(f"Проб без содержания — {int((~np.isfinite(v)).sum())}: точек нет (не нули).")
    if (np.isfinite(v) & ~placed).any():
        notes.append(f"Проб без координат (нет траектории) — {int((np.isfinite(v) & ~placed).sum())}: точек нет.")
    values = np.full(v.shape[0], np.nan)
    levels: F8 | None = None
    t = None if thresholds is None else np.asarray(list(thresholds), dtype=np.float64)
    if transform == "linear":
        values[ok] = v[ok]
        levels = t
    elif transform == "log":
        pos = ok & (v > 0.0)
        if int((ok & ~pos).sum()):
            notes.append(f"Логарифм: значений g ≤ 0 — {int((ok & ~pos).sum())}: точек нет.")
        values[pos] = np.log(v[pos])
        if t is not None:
            if bool((t <= 0.0).any()):
                raise _bad("Порог ≤ 0 при логарифмическом преобразовании.", "Задайте положительные пороги.",
                           f"thresholds.{int(np.argmax(t <= 0.0))}")  # fmt: skip
            levels = np.log(t)
    else:
        if ok.any():
            table = _nscore_table(v[ok])
            values[ok] = table.forward(v[ok])
            levels = None if t is None else np.asarray(table.forward(t), dtype=np.float64)
        elif t is not None:
            levels = np.full(t.shape[0], np.nan)
    indicators = None
    if t is not None:
        indicators = np.where(v[:, None] >= t[None, :], 0.5, -0.5)
        indicators[~np.isfinite(values)] = np.nan
    return GradeValues(values, notes, levels, indicators)
