"""Dendrogram of fields: hierarchical clustering by correlation (ТЗ 7.27; ADR 0019 §4) — the port of BF
`stats/dendrogram.py` `cluster` and of the answer of its route, with the groups of a threshold.

    TreeSpec(linkage, distance, cut)          the keys of `charts.settings`; `TreeSpec.from_settings(settings)`
        — StatsError `bad_param` {param}; Ward (and the other linkages of Euclidean distances) is refused
    cluster(r, names, spec=TreeSpec()) -> dict
        r — the k × k correlations (finite); the tree of the fields:
        {linkage [[a, b, height, size]], order, leaves, icoord, dcoord, max_distance, cut, groups, link_groups,
         group_count}
    require(correlation, names) -> correlation, or StatsError `too_few_values` / `no_spread` naming the fields
    dendrogram_chart(names, columns, weights=None, corr=None, tree=None, units=None, tick=None, threads=None)
        the answer of `POST /stats/dendrogram` as the window reads it (`DendrogramResult` of the interface):
        {fields (the names), units, correlation, pairs, rows, used, nonpositive_log, …the keys of `cluster`,
         conventions} and beside them {spec, counts, field_counts}
    warm()

Distance: "1-r" — 1 − r (fields that move against each other are far apart), "1-abs" — 1 − |r| (strength only;
"1-|r|" of BF is read too). Linkage: "average" (UPGMA, the default), "complete", "single", "weighted" (WPGMA)
— `scipy.cluster.hierarchy.linkage`. Ward is not offered: it needs Euclidean distances, which the distances of
a correlation are not (ТЗ 7.27, Т7.27-2). Fields with r = 1 join at the height 0 (Т7.27-1).

The tree. `linkage` is the table of scipy: row t joins the nodes a and b (0 … k − 1 — the fields in the order
of `names`, k + t — the node of row t) at `height`, `size` fields under it. `order` — the fields from left to
right (`leaves` — their names), a leaf sits at x = 5, 15, 25 … (as scipy `dendrogram`); `icoord[t]`,
`dcoord[t]` — the four points of the bracket of row t: [x_a, x_a, x_b, x_b], [h_a, h, h, h_b]. The matrices
`correlation` and `pairs` are in the order of `names`: the window shows them in the order of the leaves by
`order`.

Threshold. `cut` (a distance ≥ 0, None — no groups): the fields whose joins are not higher than it make
a group (`fcluster`, criterion "distance"). `groups[i]` — the number of the group of field i, 1 … `group_count`
from left to right along the leaves; `link_groups[t]` — the group of the bracket of row t, None above the
threshold (drawn without a colour). Without `cut` all three are None.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from numbers import Real
from typing import Any

import numpy as np

from .correlation import MIN_PAIRS, Correlation, CorrSpec, correlations
from .select import bad_param
from .weighted import StatsError, Tick

LINKAGES = ("average", "complete", "single", "weighted")
DISTANCES = ("1-r", "1-abs")
_DISTANCE_ALIASES = {"1-|r|": "1-abs"}  # the name of BF
_EUCLIDEAN = ("ward", "centroid", "median")  # linkages of scipy that need Euclidean distances
_LINKAGE_NAMES = "average — UPGMA, complete — полная, single — одиночная, weighted — взвешенная"
CONVENTIONS = (
    "Корреляция — с весами строк; Спирмен — корреляция средних рангов. По логарифмам значения ≤ 0 не участвуют. "
    f"Паре нужно не меньше {MIN_PAIRS} общих строк. Расстояние 1 − r или 1 − |r|; поля с r = 1 сливаются на высоте 0."
)


@dataclass(frozen=True)
class TreeSpec:
    """How the fields are clustered (the keys of `charts.settings`, ADR 0019 §9)."""

    linkage: str = "average"  # average | complete | single | weighted
    distance: str = "1-r"  # 1-r | 1-abs
    cut: float | None = None  # the threshold of the groups, a distance

    def __post_init__(self) -> None:
        if isinstance(self.linkage, str) and self.linkage.lower() in _EUCLIDEAN:
            raise bad_param(
                "linkage", f"Связь «{self.linkage}» не предлагается: ей нужны евклидовы расстояния, "
                "а расстояние по корреляции таким не является.", f"Выберите связь: {_LINKAGE_NAMES}.",
            )  # fmt: skip
        if self.linkage not in LINKAGES:
            raise bad_param("linkage", f"Связь «{self.linkage}» неизвестна.", f"Допустимо: {_LINKAGE_NAMES}.")
        if self.distance not in DISTANCES:
            raise bad_param(
                "distance", f"Расстояние «{self.distance}» неизвестно.",
                "Допустимо: 1-r — 1 − r, 1-abs — 1 − модуль r.",
            )  # fmt: skip
        cut = self.cut
        if cut is not None and (isinstance(cut, bool) or not isinstance(cut, Real) or not math.isfinite(cut)
                                or cut < 0.0):  # fmt: skip
            raise bad_param(
                "cut", "Порог групп — расстояние: число от 0.", "Введите число от 0 до 2 или очистите поле порога."
            )

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> TreeSpec:
        """The clustering of the settings of a chart; keys that are absent or None take the defaults."""
        own = {f.name for f in fields(cls)}
        given = {k: v for k, v in settings.items() if k in own and v is not None}
        if isinstance(given.get("distance"), str):
            given["distance"] = _DISTANCE_ALIASES.get(given["distance"], given["distance"])
        if isinstance(given.get("cut"), int) and not isinstance(given["cut"], bool):
            given["cut"] = float(given["cut"])
        return cls(**given)


def distances(r: Any, distance: str = "1-r") -> np.ndarray:
    """The k × k distances of the correlations: symmetric, 0 … 2, a zero diagonal."""
    m = np.asarray(r, dtype=np.float64)
    d = 1.0 - np.abs(m) if distance == "1-abs" else 1.0 - m
    d = np.clip((d + d.T) / 2.0, 0.0, 2.0)
    np.fill_diagonal(d, 0.0)
    return np.asarray(d)


def cluster(r: Any, names: Sequence[str], spec: TreeSpec | None = None) -> dict[str, Any]:
    """The tree of the fields `names` by their correlations `r` (see the module docstring)."""
    from scipy.cluster.hierarchy import fcluster, leaves_list, linkage
    from scipy.spatial.distance import squareform

    spec = TreeSpec() if spec is None else spec
    k = len(names)
    m = np.asarray(r, dtype=np.float64)
    if k < 2:
        raise bad_param("fields", "Для дендрограммы нужно 2 и больше полей.", "Выберите хотя бы два числовых поля.")
    if m.shape != (k, k):
        raise ValueError("r must be k × k for k names")
    if not np.isfinite(m).all():
        bad = sorted({names[i] for i in np.nonzero(~np.isfinite(m))[0]})
        raise StatsError(
            "too_few_values", f"Мало общих строк для корреляции: {', '.join(f'«{b}»' for b in bad[:5])}.",
            "Уберите поле или выберите другую таблицу.", param="fields",
        )  # fmt: skip
    z = linkage(squareform(distances(m, spec.distance), checks=False), method=spec.linkage)
    order = [int(i) for i in leaves_list(z)]
    x = np.empty(2 * k - 1)
    h = np.zeros(2 * k - 1)
    x[order] = 5.0 + 10.0 * np.arange(k)
    leaf = list(range(k)) + [0] * (k - 1)  # a field under every node
    icoord, dcoord = [], []
    for t in range(k - 1):
        a, b, top = int(z[t, 0]), int(z[t, 1]), float(z[t, 2])
        x[k + t], h[k + t], leaf[k + t] = (x[a] + x[b]) / 2.0, top, leaf[a]
        icoord.append([float(x[a]), float(x[a]), float(x[b]), float(x[b])])
        dcoord.append([float(h[a]), top, top, float(h[b])])
    groups = link_groups = count = None
    if spec.cut is not None:
        flat = fcluster(z, t=float(spec.cut), criterion="distance")
        number: dict[int, int] = {}
        for i in order:  # the groups are numbered from left to right
            number.setdefault(int(flat[i]), len(number) + 1)
        groups = [number[int(g)] for g in flat]
        link_groups = [groups[leaf[k + t]] if float(z[t, 2]) <= spec.cut else None for t in range(k - 1)]
        count = len(number)
    return {
        "linkage": z.tolist(),
        "order": order,
        "leaves": [names[i] for i in order],
        "icoord": icoord,
        "dcoord": dcoord,
        "max_distance": float(z[:, 2].max()),
        "cut": None if spec.cut is None else float(spec.cut),
        "groups": groups,
        "link_groups": link_groups,
        "group_count": count,
    }


def require(found: Correlation, names: Sequence[str]) -> Correlation:
    """The correlations themselves, or the refusal that names the fields that cannot be compared: fewer than
    MIN_PAIRS shared rows (`too_few_values`), no spread in the shared rows (`no_spread`)."""
    k = found.r.shape[0]
    few = [(i, j) for i in range(k) for j in range(i + 1, k) if found.n[i, j] < MIN_PAIRS]
    if few:
        told = "; ".join(f"«{names[i]}» и «{names[j]}» — {int(found.n[i, j])}" for i, j in few[:5])
        more = f" и ещё {len(few) - 5}" if len(few) > 5 else ""
        way = (
            "Выберите правило «попарно» или уберите поле с пропусками."
            if found.spec.missing == "complete"
            else "Уберите одно из полей или выберите другую таблицу."
        )
        log = " По логарифмам значения ≤ 0 не участвуют." if found.spec.log else ""
        raise StatsError(
            "too_few_values", f"Мало общих строк для корреляции (нужно не меньше {MIN_PAIRS}): {told}{more}.",
            f"{way}{log}", param="fields",
        )  # fmt: skip
    bad = ~np.isfinite(found.r)
    if bad.any():
        # a field that fails with every other one has no spread itself; the rest is named by pairs
        flat = [i for i in range(k) if k > 2 and bad[i].sum() >= k - 1]
        parts = [f"«{names[i]}»" for i in flat]
        parts += [f"«{names[i]}» и «{names[j]}»" for i in range(k) for j in range(i + 1, k)
                  if bad[i, j] and i not in flat and j not in flat]  # fmt: skip
        more = f" и ещё {len(parts) - 5}" if len(parts) > 5 else ""
        raise StatsError(
            "no_spread", f"Корреляцию не посчитать — в общих строках нет разброса значений: {', '.join(parts[:5])}"
            f"{more}.", "Уберите поле с одинаковыми значениями или проверьте правила для нулей и значений "
            "меньше нуля. Так же отвечают значения, квадрат которых не помещается в число (больше 1e150).",
            param="fields",
        )  # fmt: skip
    return found


def _matrix(m: np.ndarray) -> list[list[float | None]]:
    return [[float(v) if math.isfinite(v) else None for v in row] for row in m.tolist()]


def dendrogram_chart(
    names: Sequence[str], columns: Sequence[Any], weights: Any = None, corr: CorrSpec | None = None,
    tree: TreeSpec | None = None, *, units: Sequence[str | None] | None = None, tick: Tick | None = None,
    threads: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The answer of a dendrogram for the fields `names` with the values `columns` (arrays of one length, NaN —
    missing) and the weights of the rows (None — every row 1): the correlations by `corr`, the tree by `tree`;
    `units` of the fields go into the answer as they are (None — unknown). A field named twice is taken once.
    StatsError: `bad_param` (fewer than two fields), `too_few_values`, `no_spread` — both name the fields."""
    corr = CorrSpec() if corr is None else corr
    tree = TreeSpec() if tree is None else tree
    if len(names) != len(columns) or (units is not None and len(units) != len(names)):
        raise ValueError("a column (and a unit) for every name")
    seen: dict[str, int] = {}
    for i, name in enumerate(names):
        seen.setdefault(str(name).casefold(), i)
    keep = sorted(seen.values())
    units = [None if units is None else units[i] for i in keep]
    names, columns = [str(names[i]) for i in keep], [columns[i] for i in keep]
    if len(names) < 2:
        raise bad_param("fields", "Для дендрограммы нужно 2 и больше полей.", "Выберите хотя бы два числовых поля.")
    found = correlations(columns, weights, corr, tick=None if tick is None else lambda f: tick(0.95 * f),
                         threads=threads)  # fmt: skip
    require(found, names)
    out = {
        "fields": names,
        "units": units,
        "correlation": _matrix(found.r),
        "pairs": found.n.tolist(),
        "rows": found.counts["rows"],
        "used": found.counts["used"],
        "nonpositive_log": sum(c["nonpositive"] for c in found.fields),
        **cluster(found.r, names, tree),
        "conventions": CONVENTIONS,
        "spec": {
            "method": corr.method, "log": corr.log, "missing": corr.missing, "negatives": corr.negatives,
            "exclude_zero": corr.exclude_zero, "weighted": weights is not None, "linkage": tree.linkage,
            "distance": tree.distance,
        },
        "counts": found.counts,
        "field_counts": [{"name": name, **c} for name, c in zip(names, found.fields, strict=True)],
    }  # fmt: skip
    if tick is not None:
        tick(1.0)
    return out


def warm() -> None:
    """Compile the kernels of the correlations and load the clustering of scipy (the `stats` step of the
    warm-up)."""
    from .correlation import warm as warm_correlation

    warm_correlation()
    cluster(np.array([[1.0, 0.5, 0.1], [0.5, 1.0, 0.2], [0.1, 0.2, 1.0]]), ["a", "b", "c"], TreeSpec(cut=0.6))
