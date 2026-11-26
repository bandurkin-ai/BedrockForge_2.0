"""Two estimates of one block model against each other: the plan and the tally of Д-37 (подшаг 20.10; ТЗ 07-35 Д-37;
ADR 0027 решения 25, 28; вопрос № 108; `docs/progress/e20-api.md` §9.2). The job is `compare_job.py`.

    plan = plan_of(model, spec)          # 404 `block_field_not_found {param}`, 422 `bad_param`, 409 `field_exists`
    tally = Tally(plan, rows)            # fed with the portions of the model, one after another:
    delta = tally.add(ch, vol)           # Δ = A − B of the portion (NaN where A or B has a gap — never 0)
    out = tally.result()                 # {stats, histogram, domains, status}

Δ is the field written by the job (new field of the model, `source {op: "compare", a, b}`). The numbers:
  * `stats` — over the blocks with a Δ (A and B both have a value): n, mean, std (population: as the statistics of
    every field of the model — `fields_store.NumberStats`, merged portion by portion), min, max, the percentiles
    1, 5, 25, 50, 75, 95, 99 (the Hazen rule, as the statistics of `stats/describe`; no value — null);
  * `histogram` — `HIST_BINS` equal bins from min to max, by the number of blocks (№ 108);
  * `domains` — one row per label of the domain field (the field `domain`, else the first field of the role `code`,
    else `class`; none — one row «Все»; a block with no label — «(пусто)», only when there is one): `blocks` (with a Δ),
    `missing` (a gap in A or B), `volume` and the means of A, B and Δ weighted by the volume of the blocks (№ 108; a
    block with no volume — empty FRAC — is not in the weights), `status_changed` — blocks where both `<префикс>STATUS`
    have a value and differ (null when A and B are not both fields of an estimate that have a STATUS);
  * `status` — {a, b, changed} the names of the two STATUS fields and the total of the rows, or null.
A domain with no block at all is not in the list; with blocks but none compared — means are null.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError

from . import geometry
from . import schema as bs
from .fields_store import NumberStats
from .model import Chunk, Model, field_not_found

if TYPE_CHECKING:
    from bf_core.resources.spec import FieldCompare

ALGO = "compare-1"
OP = "compare"
HIST_BINS = 50
PERCENTILES = (1, 5, 25, 50, 75, 95, 99)
ALL_LABEL, EMPTY_LABEL = "Все", "(пусто)"
STATUS_KEYS = ("EST", "ETYPE")
DESCRIPTION = "Разность двух оценок A − B"


def bad(param: str, message: str, hint: str = "Проверьте параметры сравнения.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


@dataclass(frozen=True)
class Plan:
    """What the job reads and writes: the stored names of A, B, the domain field and the STATUS fields."""

    a: str
    b: str
    out: str
    replace: bool  # the field Δ of an earlier comparison is rewritten
    domain: str | None
    labels: tuple[str, ...]  # of the domain field (empty — one row «Все»)
    frac: str | None
    status_a: str | None
    status_b: str | None
    unit: str | None
    warnings: tuple[str, ...]

    @property
    def read(self) -> list[str]:
        names = [self.a, self.b, self.domain, self.frac, self.status_a, self.status_b]
        return [n for n in dict.fromkeys(x for x in names if x)]


def _number(model: Model, name: str, param: str) -> dict[str, Any]:
    try:
        spec = model.field(name)
    except ApiError:
        raise field_not_found(name, param) from None
    if spec.get("kind") != bs.NUMBER:
        raise bad(param, f"Поле «{spec['name']}» не числовое: сравниваются числовые поля.",
                  "Выберите поле оценки (например, EST) или другое числовое поле.")  # fmt: skip
    return dict(spec)


def status_of(model: Model, field: Mapping[str, Any]) -> str | None:
    """The `<префикс>STATUS` of a field of an estimate (`source.op` = estimate, the name ends with EST / ETYPE); None —
    not a field of an estimate or the model has no such STATUS."""
    src = field.get("source")
    if not isinstance(src, Mapping) or src.get("op") != "estimate":
        return None
    name = str(field["name"])
    for key in STATUS_KEYS:
        if name.upper().endswith(key) and len(name) > len(key):
            try:
                return str(model.field(name[: -len(key)] + "STATUS")["name"])
            except ApiError:
                return None
    return None


def default_domain(model: Model) -> str | None:
    for role in ("code", "class"):
        f = model.role_field(role)
        if f is not None and f.get("kind") == bs.CATEGORY:
            return str(f["name"])
    return None


def plan_of(model: Model, spec: FieldCompare) -> Plan:
    """The plan of `spec` on `model` (every refusal before any work: the route calls it, the job again)."""
    fa, fb = _number(model, spec.field_a, "field_a"), _number(model, spec.field_b, "field_b")
    a, b = str(fa["name"]), str(fb["name"])
    out = spec.out or f"{a}_{b}_D"
    if len(out) > bs.MAX_FIELD_NAME:
        raise bad("out", f"Имя поля разности длиннее {bs.MAX_FIELD_NAME} знаков.", "Задайте имя короче.")
    if out.casefold() in (a.casefold(), b.casefold()):
        raise bad("out", "Поле разности не может называться как сравниваемое поле.", "Задайте другое имя.")
    old = next((f for f in model.fields if str(f["name"]).casefold() == out.casefold()), None)
    replace = old is not None
    if old is not None and (old.get("source") or {}).get("op") != OP:
        raise ApiError(409, "field_exists", f"Поле «{old['name']}» в модели уже есть.",
                       "Задайте другое имя поля разности.", {"field": old["name"]})  # fmt: skip
    domain: str | None = None
    labels: tuple[str, ...] = ()
    if spec.domain:
        try:
            dom = model.field(spec.domain)
        except ApiError:
            raise field_not_found(spec.domain, "domain") from None
        if dom.get("kind") != bs.CATEGORY:
            raise bad("domain", f"Поле доменов «{dom['name']}» не категорийное.",
                      "Выберите поле с подписями (CLASS, DOM).")  # fmt: skip
        domain = str(dom["name"])
    else:
        domain = default_domain(model)
    if domain is not None:
        labels = tuple(str(s) for s in model.field(domain).get("labels") or ())
    frac = model.role_field("frac")
    sa, sb = status_of(model, fa), status_of(model, fb)
    both = sa is not None and sb is not None
    notes: list[str] = []
    if fa.get("unit") != fb.get("unit"):
        notes.append(
            f"Единицы полей разные ({fa.get('unit') or '—'} и {fb.get('unit') or '—'}): у поля разности единицы нет."
        )
    unit = fa.get("unit") if fa.get("unit") == fb.get("unit") else None
    return Plan(a, b, out, replace, domain, labels, None if frac is None else str(frac["name"]), sa if both else None,
                sb if both else None, unit, tuple(notes))  # fmt: skip


def volumes(model: Model, ch: Chunk, frac: np.ndarray | None) -> np.ndarray:
    """Volumes of the rows of a portion (NaN where FRAC is empty); one number for a plain grid."""
    if ch.s is None and model.grid.tensors is None:
        vol_c = float(geometry.row_volumes(model.grid, ch.p[:1], None, None, None)[0])
        return np.full(ch.n, vol_c) if frac is None else vol_c * np.asarray(frac, dtype=np.float64)
    return geometry.row_volumes(model.grid, ch.p, ch.s, ch.w, frac)  # type: ignore[no-any-return]


def delta_of(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Δ = A − B, NaN where A or B has a gap (or the difference is not finite): never 0."""
    d = np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    return np.where(np.isfinite(d), d, np.nan)


class Tally:
    """The accumulators of the comparison over the portions of the model (module docstring)."""

    def __init__(self, plan: Plan, model: Model) -> None:
        self.plan, self.model = plan, model
        self.k = len(plan.labels) if plan.domain else 0  # the index `k` of a block with no label (or no domain field)
        n = self.k + 1
        self.blocks = np.zeros(n, dtype=np.int64)  # every block
        self.compared = np.zeros(n, dtype=np.int64)  # with a Δ
        self.weight, self.w_a, self.w_b, self.w_d = (np.zeros(n) for _ in range(4))
        self.changed = np.zeros(n, dtype=np.int64)
        self.stats = NumberStats()
        self.buf = np.empty(model.rows)  # every Δ that is there: the percentiles and the histogram
        self.fill = 0

    def add(self, ch: Chunk) -> np.ndarray:
        p = self.plan
        a, b = ch.cols[p.a], ch.cols[p.b]
        d = delta_of(a, b)
        ok = np.isfinite(d)
        got = d[ok]
        self.buf[self.fill : self.fill + got.size] = got
        self.fill += int(got.size)
        self.stats.add(d)
        if p.domain is None:
            code = np.full(ch.n, self.k, dtype=np.int64)
        else:
            raw = np.asarray(ch.cols[p.domain])
            code = np.where((raw < 0) | (raw >= self.k), self.k, raw).astype(np.int64)
        size = self.k + 1
        self.blocks += np.bincount(code, minlength=size)
        self.compared += np.bincount(code[ok], minlength=size)
        vol = volumes(self.model, ch, None if p.frac is None else ch.cols[p.frac])
        w = ok & np.isfinite(vol)
        wv = np.where(w, vol, 0.0)
        for acc, vals in ((self.weight, np.ones(ch.n)), (self.w_a, np.where(w, a, 0.0)),
                          (self.w_b, np.where(w, b, 0.0)), (self.w_d, np.where(w, d, 0.0))):  # fmt: skip
            acc += np.bincount(code, weights=wv * vals, minlength=size)
        if p.status_a is not None and p.status_b is not None:
            sa, sb = ch.cols[p.status_a], ch.cols[p.status_b]
            diff = np.isfinite(sa) & np.isfinite(sb) & (sa != sb)
            self.changed += np.bincount(code[diff], minlength=size)
        return d

    # --- the answer ---------------------------------------------------------------------------------------------

    def _stats(self) -> dict[str, Any]:
        s = self.stats.result()
        vals = self.buf[: self.fill]
        keys = [str(q) for q in PERCENTILES]
        if not vals.size:
            return {"n": 0, "mean": None, "std": None, "min": None, "max": None, "percentiles": dict.fromkeys(keys)}
        qs = np.percentile(vals, PERCENTILES, method="hazen")
        return {"n": int(s["count"]), "mean": float(s["mean"]), "std": float(s["std"]), "min": float(s["min"]),
                "max": float(s["max"]),
                "percentiles": {k: float(v) for k, v in zip(keys, qs, strict=True)}}  # fmt: skip

    def _histogram(self) -> dict[str, Any]:
        vals = self.buf[: self.fill]
        if not vals.size:
            return {"edges": [], "counts": []}
        counts, edges = np.histogram(vals, bins=HIST_BINS)
        return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}

    def _domains(self) -> list[dict[str, Any]]:
        p = self.plan
        names = list(p.labels) + [EMPTY_LABEL] if p.domain else [ALL_LABEL]
        has_status = p.status_a is not None
        out = []
        for i, name in enumerate(names):
            if not self.blocks[i]:
                continue
            w = float(self.weight[i])
            out.append({"domain": name, "blocks": int(self.compared[i]),
                        "missing": int(self.blocks[i] - self.compared[i]), "volume": w if self.compared[i] else 0.0,
                        "mean_a": float(self.w_a[i] / w) if w > 0.0 else None,
                        "mean_b": float(self.w_b[i] / w) if w > 0.0 else None,
                        "mean_delta": float(self.w_d[i] / w) if w > 0.0 else None,
                        "status_changed": int(self.changed[i]) if has_status else None})  # fmt: skip
        return out

    def result(self) -> dict[str, Any]:
        p = self.plan
        status = None
        if p.status_a is not None and p.status_b is not None:
            status = {"a": p.status_a, "b": p.status_b, "changed": int(self.changed.sum())}
        return {"stats": self._stats(), "histogram": self._histogram(), "domains": self._domains(), "status": status}


__all__ = ["ALGO", "ALL_LABEL", "EMPTY_LABEL", "HIST_BINS", "OP", "PERCENTILES", "Plan", "Tally", "delta_of",
           "plan_of", "status_of", "volumes"]  # fmt: skip
