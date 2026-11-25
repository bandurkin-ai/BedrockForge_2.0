"""The samples of a row of the estimation plan (ТЗ 8.8.2; ADR 0024 §3; e18-api.md §3, §11; Э18, 18.5).

    s = load(project, spec, origin=None, all_domains=False, cancel=None)   # RowSamples; ApiError 422 `no_samples`
    s = prepare(xyz, z, raw, w, hole, holes, dom, row_id, span, ...)        # the same from arrays (tests, 18.14)
    canonical_order(xyz, z, *ties) -> intp[n]      lexsort (X, Y, Z, value, *ties)
    merge_coincident(xyz, dom, tol=1e-6) -> (keep, group)
    origin_of(xyz) -> f8[3];  declus_mean(z, w) -> float | None

A sample is a row of one table of a drillhole set (`stats/source.read`: rows in line order) at the middle of its
interval on the hole path (`stats/source.midpoints`). A row takes part when it has a value, a position and a domain;
the others are counted, by the first reason in that order (`counts`; a gap is never a 0):

    rows, missing (no value; `no_cut` of them have the value but not the cut one), no_position (also rows without
    FROM / TO), outside_domain (the domain is empty, or it is none of D, S — with `all_domains`, none at all),
    selected (the rest), used (after the averaging), merged_groups / merged_rows (what the averaging took),
    d, s (samples of D and of S), capped (the top cut changed the value), no_weight (a missing or not positive
    weight: NaN), plus `others` {label: n} with `all_domains`.

Value: the field of the table; the top cut — the field `*_CUT` of the table (`topcut.field`; the raw field is kept
in `raw`) or min(z, T) (`topcut.value`). Weights: the field of `weights`, by default DECL_W when the table has it
(declustering weights; a missing or not positive one is NaN, the sample takes no part in the declustered mean).
Domain (`dom`): 0 — D (the chosen `domain.values`), 1 — S (the soft boundary, `soft.domains`; the search lets a
sample of S in when it is not farther than d_м from the centre of a block, ТЗ 8.8.2, № 79), 2 + k — another label
(`dom_labels[k]`, only with `all_domains`). No domain in the spec — every sample is D. The domain field is a text or
a numeric one (a number is compared as `format(x, ".15g")`) of the same table or of another interval table (the
interval the middle of the sample lies in).

Samples of one domain with coincident coordinates (≤ 1e−6 m, also through a chain) are averaged before any
calculation: value and raw — the mean, weight — the sum of the finite ones (the cell weights of declustering share
a point), position, hole, interval, row — those of the first of the group in the canonical order; `merged` is
their number and `notes` tells it for the journal. The order after that is canonical, lexsort (X, Y, Z, value, then
domain, hole, row id): the «number of a sample» of the rules of equality (NN, the search) is the position in it
(№ 76), and it does not depend on the order of the rows of the table (Т8.8-9: a permutation gives bitwise the same
arrays). `origin` — the centre of the extent of the samples of the row (given: the stored one, for xval and the
preview); `local = xyz − origin`, the kernels count in it (8.0.1).

`means`: naive and declustered (Σ w·z / Σ w over the finite positive w) means of D — the m of SK; `declus_mean`
over an indicator gives the m_k of IK. `take(rows)` keeps the means (cross-validation keeps the global mean).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from .spec_parts import SampleSel

TOL = 1e-6  # m: samples of one domain closer than this are one
DECL_W = "DECL_W"
D, S = 0, 1  # the codes of `dom`; ≥ 2 — the other labels
STAGE_READ = "Чтение проб"
STAGE_TRAJ = "Траектории"
STAGE_PREP = "Подготовка проб"

Cancel = Callable[[], None]


@dataclass(frozen=True)
class RowSamples:
    xyz: np.ndarray  # float64 [n, 3]: of the project
    local: np.ndarray  # float64 [n, 3]: xyz − origin
    z: np.ndarray  # float64 [n]: the value used (top cut, averaging)
    raw: np.ndarray  # float64 [n]: the value before the top cut (averaged)
    w: np.ndarray | None  # float64 [n], NaN — no weight; None — the row has no weights
    hole: np.ndarray  # int32 [n]: dense index into `holes` (the names of the holes of the samples, sorted)
    holes: list[str]
    dom: np.ndarray  # int16 [n]: 0 — D; 1 — S; ≥ 2 — another label
    dom_labels: list[str]  # label of the code 2 + k
    row_id: np.ndarray  # int64 [n]: dh_rows.id of the first row
    span: np.ndarray  # float64 [n, 2]: FROM, TO of the first row
    merged: np.ndarray  # int16 [n]: rows averaged into the sample (1 — none)
    origin: np.ndarray  # float64 [3]
    counts: dict[str, Any]
    inputs: list[dict[str, Any]]
    means: dict[str, float | None]  # "naive", "declus": of the samples of D
    notes: list[str] = field(default_factory=list)  # lines for the journal of the job

    @property
    def n(self) -> int:
        return int(self.z.shape[0])

    @property
    def in_d(self) -> np.ndarray:
        return self.dom == D

    def take(self, rows: np.ndarray) -> RowSamples:
        """The samples `rows` (positions; the canonical order is kept if they ascend): the origin, the means and
        the counts stay those of the row (the cross-validation of 18.14 leaves a sample or a hole out)."""
        r = np.asarray(rows)
        return replace(self, xyz=self.xyz[r], local=self.local[r], z=self.z[r], raw=self.raw[r],
                       w=None if self.w is None else self.w[r], hole=self.hole[r], dom=self.dom[r],
                       row_id=self.row_id[r], span=self.span[r], merged=self.merged[r])  # fmt: skip


# --- arrays: order, averaging, means ------------------------------------------------------------------------


def origin_of(xyz: np.ndarray) -> np.ndarray:
    """The centre of the extent of the points (8.0.1)."""
    a = np.asarray(xyz, dtype=np.float64)
    return np.asarray(0.5 * (a.min(axis=0) + a.max(axis=0)), dtype=np.float64)


def canonical_order(xyz: np.ndarray, z: np.ndarray, *ties: np.ndarray) -> np.ndarray:
    """lexsort by (X, Y, Z, value), then by `ties` (№ 76); the order of equal rows is the order of the input, so
    `ties` must tell the rows that can be equal apart (domain, hole, row id)."""
    keys = [*reversed(ties), z, xyz[:, 2], xyz[:, 1], xyz[:, 0]]
    return np.lexsort(keys).astype(np.intp, copy=False)


def merge_coincident(xyz: np.ndarray, dom: np.ndarray, tol: float = TOL) -> tuple[np.ndarray, np.ndarray]:
    """Groups of samples of one domain no farther than `tol` apart (a chain counts): `keep` — the first sample of
    every group (ascending positions: put the input in the canonical order and it is the first of it), `group[i]` —
    the position of the group of i in `keep`."""
    n = int(xyz.shape[0])
    ident = np.arange(n, dtype=np.intp)
    if n < 2:
        return ident, ident.copy()
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    pairs = cKDTree(xyz).query_pairs(tol, output_type="ndarray")
    if pairs.shape[0]:
        pairs = pairs[dom[pairs[:, 0]] == dom[pairs[:, 1]]]
    if pairs.shape[0] == 0:
        return ident, ident.copy()
    g = coo_matrix((np.ones(pairs.shape[0], dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, label = connected_components(g, directed=False)
    first = np.full(int(label.max()) + 1, n, dtype=np.intp)
    np.minimum.at(first, label, ident)  # the first position of every component
    keep = np.sort(first)
    rank = np.empty(first.shape[0], dtype=np.intp)
    rank[np.argsort(first, kind="stable")] = np.arange(first.shape[0], dtype=np.intp)
    return keep, rank[label]


def declus_mean(z: np.ndarray, w: np.ndarray | None) -> float | None:
    """Σ w·z / Σ w over the samples with a finite value and a finite positive weight; None — there are none
    (or no weights)."""
    if w is None:
        return None
    ok = np.isfinite(z) & np.isfinite(w) & (w > 0.0)
    if not ok.any():
        return None
    return float(np.sum(w[ok] * z[ok]) / np.sum(w[ok]))


def _clean_weights(w: np.ndarray | None) -> np.ndarray | None:
    if w is None:
        return None
    w = np.asarray(w, dtype=np.float64)
    return np.where(np.isfinite(w) & (w > 0.0), w, np.nan)


def _bad_shape(name: str) -> ApiError:
    return ApiError(500, "internal", f"Пробы: массив «{name}» другой длины.", "Сообщите разработчику.")


def prepare(xyz: np.ndarray, z: np.ndarray, raw: np.ndarray | None, w: np.ndarray | None, hole: np.ndarray,
            holes: list[str], dom: np.ndarray, row_id: np.ndarray, span: np.ndarray, *,
            origin: np.ndarray | None = None, dom_labels: list[str] | None = None,
            counts: dict[str, Any] | None = None, inputs: list[dict[str, Any]] | None = None,
            tol: float = TOL, cancel: Cancel | None = None) -> RowSamples:  # fmt: skip
    """The samples from the arrays of the rows that take part (finite value and position, a domain): the origin,
    the canonical order, the averaging of the coincident ones, the order again, the means (module docstring).
    `hole` indexes `holes`; those of `holes` no sample belongs to are dropped. `raw` None — the same as `z`."""
    xyz = np.ascontiguousarray(xyz, dtype=np.float64)
    n = int(xyz.shape[0])
    z = np.asarray(z, dtype=np.float64)
    raw_a = z if raw is None else np.asarray(raw, dtype=np.float64)
    for name, a in (("z", z), ("raw", raw_a), ("hole", hole), ("dom", dom), ("row_id", row_id), ("span", span)):
        if a.shape[0] != n:
            raise _bad_shape(name)
    if w is not None and w.shape[0] != n:
        raise _bad_shape("w")
    wa = _clean_weights(w)
    org = origin_of(xyz) if origin is None and n else (np.zeros(3) if origin is None else np.asarray(origin, float))
    names = np.asarray(holes, dtype=object)
    order = canonical_order(xyz, z, np.asarray(dom, dtype=np.int16), np.asarray(hole), np.asarray(row_id))
    xyz, z, raw_a = xyz[order], z[order], raw_a[order]
    wa = None if wa is None else wa[order]
    hole_i, dom_i, rid, sp = np.asarray(hole)[order], np.asarray(dom)[order], np.asarray(row_id)[order], span[order]
    if cancel is not None:
        cancel()
    keep, group = merge_coincident(xyz - org, dom_i, tol)
    m = int(keep.shape[0])
    size = np.bincount(group, minlength=m)
    stats = {"merged_groups": int((size > 1).sum()), "merged_rows": int(size[size > 1].sum())}
    if m < n:
        cnt = size.astype(np.float64)
        z = np.bincount(group, weights=z, minlength=m) / cnt
        raw_a = np.bincount(group, weights=raw_a, minlength=m) / cnt
        if wa is not None:
            finite = np.isfinite(wa)
            total = np.bincount(group, weights=np.where(finite, wa, 0.0), minlength=m)
            wa = np.where(np.bincount(group, weights=finite.astype(np.float64), minlength=m) > 0.0, total, np.nan)
        xyz, hole_i, dom_i, rid, sp = xyz[keep], hole_i[keep], dom_i[keep], rid[keep], sp[keep]
        order2 = canonical_order(xyz, z, dom_i.astype(np.int16), hole_i, rid)
        z, raw_a, xyz, hole_i, dom_i, rid, sp = (a[order2] for a in (z, raw_a, xyz, hole_i, dom_i, rid, sp))
        wa = None if wa is None else wa[order2]
        size = size[order2]
    used = np.unique(hole_i)
    remap = np.zeros(max(int(hole_i.max()) + 1 if m else 0, 1), dtype=np.int32)
    remap[used] = np.arange(used.shape[0], dtype=np.int32)
    d = dom_i == D
    means = {"naive": float(np.mean(z[d])) if d.any() else None,
             "declus": declus_mean(z[d], None if wa is None else wa[d])}  # fmt: skip
    c = dict(counts or {})
    c.update(stats, used=m, d=int(d.sum()), s=int((dom_i == S).sum()),
             no_weight=0 if wa is None else int(np.isnan(wa).sum()))  # fmt: skip
    notes = []
    if stats["merged_rows"]:
        notes.append(f"Усреднено проб с совпадающими координатами (≤ 1e−6 м, один домен): "
                     f"{stats['merged_rows']} в {stats['merged_groups']} точках.")  # fmt: skip
    return RowSamples(
        xyz=xyz, local=xyz - org, z=z, raw=raw_a, w=wa, hole=remap[hole_i].astype(np.int32),
        holes=[str(h) for h in names[used]] if m else [], dom=dom_i.astype(np.int16),
        dom_labels=list(dom_labels or []), row_id=rid.astype(np.int64), span=sp,
        merged=size.astype(np.int16), origin=org, counts=c, inputs=list(inputs or []), means=means, notes=notes,
    )  # fmt: skip


# --- the table ------------------------------------------------------------------------------------------------


def _field_error(code: str, message: str, hint: str, param: str) -> ApiError:
    return ApiError(422, code, message, hint, extra={"param": param})


def _find(infos: list[Any], name: str, param: str, *, number: bool) -> Any:
    for f in infos:
        if f.name.casefold() == str(name).casefold():
            if number and f.kind != "number":
                raise _field_error("field_not_number", f"Поле «{f.name}» — текстовое.", "Выберите числовое поле.",
                                   param)  # fmt: skip
            return f
    raise _field_error("field_not_found", f"В таблице нет поля «{str(name)[:80]}».",
                       "Выберите поле из списка колонок.", param)  # fmt: skip


def _number_at(cols: Any, dom: Any, name: str) -> np.ndarray:
    """A numeric field of `dom` at the middle of every row of `cols` (as `source.domain_at` does for a text one)."""
    from bf_core.stats import source_kernels as sk

    values = np.asarray(dom.number(name))
    if dom.table.id == cols.table.id:
        return values
    lo, hi = sk.hole_ranges(cols.holes, dom.holes, dom.starts)
    rows, _ = sk.rows_covering(cols.starts, 0.5 * (cols.from_m + cols.to_m), lo, hi, dom.from_m, dom.to_m)
    out = np.full(cols.n, np.nan)
    ok = rows >= 0
    out[ok] = values[rows[ok]]
    return out


def _domain_codes(cols: Any, dom_cols: Any, name: str, kind: str) -> tuple[np.ndarray, list[str]]:
    """(codes int32 [n], −1 — none; labels) of the domain field at the middle of every row."""
    from bf_core.stats import source

    if kind == "text":
        got = source.domain_at(cols, dom_cols, name)
        return np.asarray(got.codes), list(got.labels)
    x = _number_at(cols, dom_cols, name)
    finite = np.isfinite(x)
    keys = np.unique(x[finite])
    codes = np.full(x.shape[0], -1, dtype=np.int32)
    codes[finite] = np.searchsorted(keys, x[finite]).astype(np.int32)
    return codes, [format(float(v), ".15g") for v in keys]


def _classify(codes: np.ndarray, labels: list[str], d_values: tuple[str, ...], s_values: tuple[str, ...],
              all_domains: bool) -> tuple[np.ndarray, list[str]]:  # fmt: skip
    """dom of every row: 0 — D, 1 — S, 2 + k — another label (`all_domains`), −1 — none of them."""
    stripped = [s.strip() for s in labels]
    d_set, s_set = set(d_values), set(s_values)
    others = sorted({s for s in stripped if s not in d_set and s not in s_set}) if all_domains else []
    table = np.full(len(labels) + 1, -1, dtype=np.int16)  # the last — the code −1
    for k, s in enumerate(stripped):
        table[k] = D if s in d_set else (S if s in s_set else (2 + others.index(s) if all_domains else -1))
    return table[codes], others


def _inputs(project: Any, tables: list[Any], set_id: int) -> list[dict[str, Any]]:
    from bf_core.composites import source as dh_source

    st = dh_source.trajectories(project, set_id)
    out: list[dict[str, Any]] = [{"kind": "table", "id": t.id, "name_key": t.name_key, "token": t.token,
                                  "rev": t.rev} for t in tables]  # fmt: skip
    out.append({"kind": "trajectories", "key": [str(k) for k in st.key[2:]]})
    return out


def load(project: Any, spec: Any, *, origin: np.ndarray | None = None, all_domains: bool = False,
         cancel: Cancel | None = None) -> RowSamples:  # fmt: skip
    """The samples of `spec.samples` (an `EstimationSpec` or a `SampleSel`; module docstring). `project` needs
    `engine`, `uid`, `folder`. `cancel()` is called between the stages and raises when the job is cancelled
    (`lambda: tick(None, None)`). ApiError 422 `no_samples` {counts}: no sample of D."""
    from bf_core.db import dialect
    from bf_core.stats import source

    sel: SampleSel = getattr(spec, "samples", spec)
    beat = cancel or (lambda: None)
    beat()
    with dialect.read_tx(project.engine) as conn:
        table = source.find_table(conn, sel.set_id, sel.table)
        infos = source.fields_of(conn, table)
        dom_table = table
        dom_infos = infos
        if sel.domain is not None and sel.domain.table not in (None, "") and sel.domain.table != sel.table:
            dom_table = source.find_table(conn, sel.set_id, sel.domain.table)
            dom_infos = infos if dom_table.id == table.id else source.fields_of(conn, dom_table)
    value = _find(infos, sel.field, "samples.field", number=True).name
    names = [value]
    cut = weights = None
    if sel.topcut is not None and sel.topcut[0] == "field":
        cut = _find(infos, str(sel.topcut[1]), "samples.topcut.field", number=True).name
        names.append(cut)
    if isinstance(sel.weights, str):
        weights = _find(infos, sel.weights, "samples.weights", number=True).name
    elif sel.weights is None:
        hit = next((f for f in infos if f.name.casefold() == DECL_W.casefold() and f.kind == "number"), None)
        weights = None if hit is None else hit.name
    if weights is not None:
        names.append(weights)
    dom_name, dom_kind = None, "text"
    if sel.domain is not None:
        found = _find(dom_infos, sel.domain.field, "samples.domain.field", number=False)
        dom_name, dom_kind = found.name, found.kind
        if dom_table.id == table.id:
            names.append(dom_name)
    names = list(dict.fromkeys(names))
    cols = source.read(project, sel.set_id, table.id, names)
    dom_cols = cols if dom_table.id == table.id else source.read(project, sel.set_id, dom_table.id, [dom_name or ""])
    beat()
    raw_all = np.asarray(cols.number(value))
    z_all = raw_all
    if cut is not None:
        z_all = np.asarray(cols.number(cut))
    elif sel.topcut is not None:
        z_all = np.minimum(raw_all, float(sel.topcut[1]))  # NaN stays NaN
    n_rows = cols.n
    if sel.domain is None:
        dom_all = np.zeros(n_rows, dtype=np.int16)
        dom_labels: list[str] = []
    else:
        codes, labels = _domain_codes(cols, dom_cols, str(dom_name), dom_kind)
        soft = () if sel.soft is None else sel.soft[0]
        dom_all, dom_labels = _classify(codes, labels, sel.domain.values, soft, all_domains)
    xyz_all = source.midpoints(project, cols)
    beat()
    has_value = np.isfinite(z_all)
    has_pos = np.isfinite(xyz_all).all(axis=1)
    in_dom = dom_all >= 0
    ok = has_value & has_pos & in_dom
    rows = np.flatnonzero(ok)
    counts: dict[str, Any] = {
        "rows": n_rows + cols.skipped,
        "missing": int((~has_value).sum()),
        "no_cut": int((np.isfinite(raw_all) & ~has_value).sum()),
        "no_position": cols.skipped + int((has_value & ~has_pos).sum()),
        "outside_domain": int((has_value & has_pos & ~in_dom).sum()),
        "selected": int(rows.size),
        "capped": int((z_all[rows] < raw_all[rows]).sum()),
    }
    if all_domains and dom_labels:
        counts["others"] = {lab: int((dom_all[rows] == 2 + k).sum()) for k, lab in enumerate(dom_labels)}
    n_d = int((dom_all[rows] == D).sum())
    if rows.size == 0 or (n_d == 0 and not all_domains):
        raise ApiError(422, "no_samples", "Нет проб для оценки: у проб нет значения, положения или домена.",
                       "Проверьте поле, домен проб и траектории скважин.",
                       {"counts": counts, "param": "samples"})  # fmt: skip
    w = None if weights is None else np.asarray(cols.number(weights))[rows]
    hole_all = cols.hole_of()
    span = np.column_stack((cols.from_m, cols.to_m))[rows]
    beat()
    return prepare(xyz_all[rows], z_all[rows], raw_all[rows], w, hole_all[rows], list(cols.holes), dom_all[rows],
                np.asarray(cols.row_ids)[rows], span, origin=origin, dom_labels=dom_labels, counts=counts,
                inputs=_inputs(project, [table] if dom_table.id == table.id else [table, dom_table], sel.set_id),
                cancel=cancel)  # fmt: skip
