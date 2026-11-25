"""The categories of a block model by a scheme of rules (подшаг 17.26; ТЗ 8.7.5; ADR 0023 §9, decision 13, № 68; the
forms — `docs/progress/e17-api.md` §9). The kernels only: the job `blocks.classify`, its route and the «устарел» of
the field are 17.29; the reference of the schemes is `schemes.py`.

    spec = parse(body, lookup)                    # `rules: [{label, expr}]` or `scheme: "jorc"`; 422 `bad_param`,
                                                  # `scheme_incomplete {param: "rules.N.expr"}`, 404 `scheme_not_found`
    plan = prepare(model, spec, overrides)        # the rules against the fields of the model (422, below)
    r = restrict.prepare(model, tree, conn=conn, store=store)          # None — every block
    res = classify(model, plan, r, chunk=CHUNK, cancel=check)          # Result: codes int16 [rows] of the whole model
    for part in res.iter_codes():                 # int32 [≤ 2²⁰]: the memory form of a category (−1 — empty)
        writer.write({spec.out: part})

A block gets the label of the FIRST rule it fulfils (a condition of 8.7.4; a number is a condition too: not 0 is true,
NaN is false). The order of the work, for every block:
  1. outside the restrictor (a share f = 0) — no category (empty);
  2. «без оценки — без категории»: a block with no value in the field `estimate` (a gap, NaN; a category −1) is not
     classified, whatever the rules say (`estimate` None — every block of the restrictor has an estimate; a flag
     field with 0 is a value, not a gap);
  3. the rules in order, the first fulfilled gives its label; none — no category (counted in `unclassified`);
  4. a wireframe of a category overrides the rule (`overrides`: the label and a function `chunk → share`, the first
     of them in the list that holds the block, share > 0; only for a block that has an estimate and is inside);
  5. the majority filter 3×3×3 over the parents, `majority` times (0 — none): every parent takes the category that
     most of the 27 parents around it (itself included) have; a tie keeps its own category if it is among the leaders,
     else the smaller code wins; a parent without a category never gets one and does not vote; in a model with
     sub-blocks the category of a parent is first the mode of its rows by volume, then every classified row of the
     parent takes the filtered one. At most 16 categories (422 `bad_param`).

Fields the rules need: one the model lacks — 422 `classify_field_missing {field, model, label, param: "rules.N.expr",
pos}`, never a quiet skip: the schemes name PASS, SLOPE, NH, AD, which a model has only after an estimate (Э18). A rule
without a condition (the template ГКЗ) — 422 `scheme_incomplete`. `prefix` («Префикс полей оценки», question № 78;
empty — none): the fields of an estimate in a rule (PASS, KV, SLOPE, NS, NH, AD, … `schemes.ESTIMATE_FIELDS`) are read
as `<prefix>PASS` …; the rules stay as written, the prefix is kept in `params`. The result field is `cat`
(`role: class`), its
labels are the labels of the rules in order, every label stays in the list even if no block got it.

Every block is classified from its own row alone, so the portions of any size give the same bits; the filter works on
the whole column (int16, 2 bytes a row) and does not depend on the portions at all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.calc import ExprError, ExprFieldError, ExprTypeError, compile

from . import geometry
from . import schema as bs
from .model import CHUNK, Chunk, Model
from .schemes import MAX_RULES, check_prefix, check_rules, prefixed, prefixed_name

ALGO = "classify-1"
DEFAULT_OUT = "CLASS"
MAX_MAJORITY = 10
MAX_VOTE_CLASSES = 16
STEP = 1 << 20  # parents per slice of the majority filter
DENSE_MAX = 64_000_000  # cells of a grid up to which a table of parents instead of a search is used
DENSE_FACTOR = 8  # … and only while the grid has at most this many cells per parent
NEIGHBOURS = [(di, dj, dk) for dk in (-1, 0, 1) for dj in (-1, 0, 1) for di in (-1, 0, 1)]
Weights = Callable[[Chunk], np.ndarray]


def bad_param(param: str, message: str, hint: str = "Проверьте параметры категорий.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def incomplete(index: int, label: str) -> ApiError:
    return ApiError(
        422, "scheme_incomplete", f"У категории «{label}» не задано условие.",
        "Заполните условия схемы (справочник схем категорий) или выберите другую схему.",
        {"param": f"rules.{index}.expr", "label": label},
    )  # fmt: skip


def field_missing(name: str, model: str, label: str, param: str, pos: int | None = None) -> ApiError:
    extra: dict[str, Any] = {"field": name, "model": model, "label": label, "param": param}
    if pos is not None:
        extra["pos"] = pos
    return ApiError(
        422, "classify_field_missing", f"В модели «{model}» нет поля «{name}», которое нужно категории «{label}».",
        "Создайте поле оценкой или калькулятором, либо выберите другую схему или правило.", extra,
    )  # fmt: skip


# --- the body ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    rules: tuple[tuple[str, str], ...]  # (label, expr) in order
    scheme: str | None
    overrides: tuple[tuple[int, str], ...]  # (wireframe id, label)
    majority: int
    estimate: str | None
    out: str
    prefix: str = ""  # «Префикс полей оценки» (question № 78): `PASS` of a rule reads `<prefix>PASS`

    @property
    def labels(self) -> list[str]:
        return [label for label, _ in self.rules]

    def params(self) -> dict[str, Any]:
        """What `source.params` of the field keeps for «Перестроить»: the rules as they were (the scheme may change)."""
        return {
            "rules": [{"label": a, "expr": b} for a, b in self.rules], "scheme": self.scheme,
            "overrides": [{"wireframe_id": w, "label": label} for w, label in self.overrides],
            "majority": self.majority, "estimate": self.estimate, "out": self.out, "prefix": self.prefix,
        }  # fmt: skip


def parse(body: Mapping[str, Any], lookup: Callable[[str], Mapping[str, Any]] | None = None) -> Spec:
    """The body of `POST /blocks/{id}/classify` checked as to its shape; `lookup(id)` gives a scheme of the reference
    (`schemes.get`; it raises 404 `scheme_not_found`). The model is checked by `prepare`."""
    raw_rules, scheme = body.get("rules"), body.get("scheme")
    if (raw_rules is None) == (scheme is None):
        raise bad_param("rules", "Нужно ровно одно: правила или схема.", "Выберите схему или задайте правила.")
    scheme_id: str | None = None
    if scheme is not None:
        if not isinstance(scheme, str) or not scheme.strip() or lookup is None:
            raise bad_param("scheme", "Выберите схему из списка.")
        scheme_id = scheme.strip()
        raw_rules = lookup(scheme_id)["rules"]
    rules = check_rules(raw_rules)
    for i, r in enumerate(rules):
        if r["expr"] is None:
            raise incomplete(i, r["label"])
    labels = [r["label"].casefold() for r in rules]
    overrides: list[tuple[int, str]] = []
    raw_over = body.get("overrides") or []
    if not isinstance(raw_over, list) or len(raw_over) > MAX_RULES:
        raise bad_param("overrides", f"Каркасы категорий — список не длиннее {MAX_RULES}.")
    for i, o in enumerate(raw_over):
        wid = o.get("wireframe_id") if isinstance(o, Mapping) else None
        label = o.get("label") if isinstance(o, Mapping) else None
        if isinstance(wid, bool) or not isinstance(wid, int) or wid < 1:
            raise bad_param(f"overrides.{i}.wireframe_id", "Нужен каркас категории.")
        if not isinstance(label, str) or " ".join(label.split()).casefold() not in labels:
            raise bad_param(f"overrides.{i}.label", "Категории каркаса нет в правилах.",
                            "Выберите подпись из списка категорий схемы.")  # fmt: skip
        overrides.append((wid, rules[labels.index(" ".join(label.split()).casefold())]["label"]))
    majority = body.get("majority", 0)
    if isinstance(majority, bool) or not isinstance(majority, int) or not 0 <= majority <= MAX_MAJORITY:
        raise bad_param("majority", f"Число проходов мажоритарного фильтра — целое от 0 до {MAX_MAJORITY}.")
    if majority and len(rules) > MAX_VOTE_CLASSES:
        raise bad_param("majority", f"Мажоритарный фильтр работает с категориями не больше {MAX_VOTE_CLASSES}.",
                        "Сократите число категорий или отключите фильтр.")  # fmt: skip
    estimate = body.get("estimate")
    if estimate is not None and (not isinstance(estimate, str) or not estimate.strip()):
        raise bad_param("estimate", "Укажите поле оценки или оставьте пустым.")
    out = body.get("out", DEFAULT_OUT)
    out = " ".join(out.split()) if isinstance(out, str) else ""
    if not out or len(out) > bs.MAX_FIELD_NAME or out.casefold() == bs.IDX:
        raise bad_param("out", f"Имя поля — от 1 до {bs.MAX_FIELD_NAME} знаков, не «{bs.IDX}».")
    prefix = check_prefix(body.get("prefix"))
    return Spec(tuple((r["label"], r["expr"]) for r in rules), scheme_id, tuple(overrides), majority,
                estimate.strip() if isinstance(estimate, str) else None, out, prefix)  # fmt: skip


# --- the plan ---------------------------------------------------------------------------------------------------------


@dataclass
class Plan:
    spec: Spec
    fields: list[str]  # stored names a portion needs: the fields of the rules and the estimate
    programs: list[Any]
    estimate: str | None
    estimate_kind: str
    overrides: list[tuple[int, Weights]]  # (code of the label, the share of the wireframe)


def prepare(model: Model, spec: Spec, overrides: Sequence[Weights] = (), extra_fields: Sequence[str] = ()) -> Plan:
    """The rules of `spec` against `model`: 422 `classify_field_missing`, `expr_syntax`, `expr_type` (a rule must be a
    condition), `bad_param {param: "estimate"}`. `overrides` — a function `chunk → share` for every item of
    `spec.overrides` in order (the leaf «каркас» of `restrict.LEAVES`, 17.20), `extra_fields` — what they read."""
    from .calc_job import expr_api_error, schema_of

    if len(overrides) != len(spec.overrides):
        raise ValueError("one function for every wireframe of a category is needed")
    schema = schema_of(model)
    programs: list[Any] = []
    names: dict[str, None] = {}
    for i, (label, expr) in enumerate(spec.rules):
        param = f"rules.{i}.expr"
        try:  # with a prefix the names of the estimate are renamed in the tree, the places stay those of the text
            prog = compile(prefixed(expr, spec.prefix) if spec.prefix else expr, schema, text=expr)
        except ExprFieldError as e:
            err = expr_api_error(e, expr, param)
            name = prefixed_name(str(err.extra.get("field", "")), spec.prefix)
            raise field_missing(name, model.name, label, param, e.pos) from e
        except ExprError as e:
            raise expr_api_error(e, expr, param) from e
        if prog.out_kind not in ("bool", "num"):
            hint = "Условие — сравнение, например PASS = 1 AND NH >= 3."
            type_err = ExprTypeError(expr, prog.expr.pos, f"Правило категории «{label}» должно быть условием", hint)
            raise expr_api_error(type_err, expr, param)
        programs.append(prog)
        names.update(dict.fromkeys(prog.fields_used))
    est_name, est_kind = None, bs.NUMBER
    if spec.estimate is not None:
        have = {str(f["name"]).casefold(): f for f in model.fields}
        f = have.get(spec.estimate.casefold())
        if f is None:
            raise ApiError(422, "classify_field_missing", f"В модели «{model.name}» нет поля оценки «{spec.estimate}».",
                           "Выберите поле оценки из списка полей модели.",
                           {"field": spec.estimate, "model": model.name, "param": "estimate"})  # fmt: skip
        est_name, est_kind = str(f["name"]), str(f["kind"])
        names[est_name] = None
    codes = {label.casefold(): i for i, label in enumerate(spec.labels)}
    over = [(codes[label.casefold()], fn) for (_, label), fn in zip(spec.overrides, overrides, strict=True)]
    names.update(dict.fromkeys(extra_fields))
    return Plan(spec, list(names), programs, est_name, est_kind, over)


# --- the portions -----------------------------------------------------------------------------------------------------


def has_estimate(plan: Plan, cols: Mapping[str, np.ndarray], n: int) -> np.ndarray:
    """bool [n]: the block has a value in the field of the estimate (all do when the plan has none)."""
    if plan.estimate is None:
        return np.ones(n, dtype=bool)
    v = cols[plan.estimate]
    return v >= 0 if plan.estimate_kind == bs.CATEGORY else ~np.isnan(v)


def classify_chunk(plan: Plan, chunk: Chunk, inside: np.ndarray | None = None) -> np.ndarray:
    """int16 [n]: the code (the number of the rule, −1 — none) of every block of one portion, steps 1–4 of the module
    text; `inside` — bool [n] from the restrictor (None — all)."""
    n = chunk.n
    active = has_estimate(plan, chunk.cols, n)
    if inside is not None:
        active &= inside
    codes = np.full(n, -1, dtype=np.int16)
    for i, prog in enumerate(plan.programs):
        v = prog.eval_chunk(chunk.cols, n)
        hit = v if prog.out_kind == "bool" else (v < 0) | (v > 0)
        take = active & (codes < 0) & hit
        codes[take] = i
    for code, share in reversed(plan.overrides):  # the first of the list wins: it is written last
        codes[active & (share(chunk) > 0.0)] = code
    return codes


@dataclass
class Result:
    """The categories of the whole model. `codes` int16 [rows]; the counts are of the final categories."""

    codes: np.ndarray
    labels: list[str]
    rows: int
    passed: int  # blocks inside the restrictor
    no_estimate: int  # inside but without an estimate
    unclassified: int  # inside, with an estimate, no rule fulfilled
    counts: list[int]  # blocks of every label after the filter

    def iter_codes(self, chunk: int = CHUNK) -> Iterator[np.ndarray]:
        """The codes by portions in the memory form of a category (int32, −1 — empty), in the order of the rows."""
        for a in range(0, self.rows, chunk):
            yield self.codes[a : a + chunk].astype(np.int32)


def classify(
    model: Model, plan: Plan, restrictor: Any = None, *, chunk: int = CHUNK, cancel: Callable[[], None] | None = None
) -> Result:
    """The categories of the model by portions of `chunk` rows, then the majority filter on the whole column.
    `restrictor` — what `restrict.prepare` made (None — every block passes)."""
    names = list(dict.fromkeys([*plan.fields, *(restrictor.fields if restrictor is not None else [])]))
    codes = np.full(model.rows, -1, dtype=np.int16)
    passed = no_estimate = 0
    for ch in model.iter(names, chunk):
        if cancel is not None:
            cancel()
        inside = None if restrictor is None else restrictor.weights(ch) > 0.0
        codes[ch.start : ch.start + ch.n] = classify_chunk(plan, ch, inside)
        ok = np.ones(ch.n, dtype=bool) if inside is None else inside
        passed += int(ok.sum())
        no_estimate += int((ok & ~has_estimate(plan, ch.cols, ch.n)).sum())
    unclassified = passed - no_estimate - int((codes >= 0).sum())
    if plan.spec.majority:
        if cancel is not None:
            cancel()
        codes = majority_model(model, codes, len(plan.spec.rules), plan.spec.majority, cancel)
    counts = np.bincount(codes[codes >= 0].astype(np.int64), minlength=len(plan.spec.rules)).tolist()
    return Result(codes, plan.spec.labels, model.rows, passed, no_estimate, unclassified, [int(c) for c in counts])


# --- the majority filter ----------------------------------------------------------------------------------------------


def majority_model(
    model: Model, codes: np.ndarray, k: int, iterations: int, cancel: Callable[[], None] | None = None
) -> np.ndarray:
    """`majority` on a model: the parents are the cells P of the grid, the volumes of the rows (for the mode of a
    parent with sub-blocks) are geometric — the same as the report weighs the blocks by."""
    p = model.p()
    volume = None
    if model.sub:
        sw = model.subs()
        assert sw is not None
        volume = geometry.row_volumes(model.grid, p, sw[0], sw[1])
    return majority(codes, p, model.grid.counts, k, iterations, volume, cancel)


def _runs(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(the distinct P ascending, the number of the parent of every row) of rows sorted by P."""
    start = np.empty(len(p), dtype=bool)
    start[0] = True
    np.not_equal(p[1:], p[:-1], out=start[1:])
    return p[start], np.cumsum(start, dtype=np.int64) - 1


def _parent_mode(codes: np.ndarray, inv: np.ndarray, m: int, k: int, volume: np.ndarray) -> np.ndarray:
    """int16 [m]: the category with the largest volume among the rows of a parent (a tie — the smaller code), −1 —
    none classified."""
    best = np.zeros(m, dtype=np.float64)
    own = np.full(m, -1, dtype=np.int16)
    for c in range(k):
        w = np.bincount(inv, weights=np.where(codes == c, volume, 0.0), minlength=m)
        better = w > best
        best[better] = w[better]
        own[better] = c
    return own


def majority(
    codes: np.ndarray,
    p: np.ndarray,
    counts: Sequence[int],
    k: int,
    iterations: int,
    volume: np.ndarray | None = None,
    cancel: Callable[[], None] | None = None,
) -> np.ndarray:
    """The majority filter 3×3×3 of the module text on `codes` int16 [rows] (−1 — none) of the rows with `p` (uint64,
    ascending) of a grid of `counts` parents; `k` — the number of categories; `volume` — float64 [rows] for the mode
    of a parent with several rows (None — one row a parent). Returns a new array."""
    rows = len(codes)
    if iterations <= 0 or rows == 0:
        return codes
    nx, ny, nz = (int(c) for c in counts)
    ids, inv = _runs(p)
    m = len(ids)
    single = m == rows
    if single:
        own = codes.astype(np.int16)
    else:
        if volume is None:
            raise ValueError("the volumes of the rows are needed for a model with sub-blocks")
        own = _parent_mode(codes, inv, m, k, volume)
    ids64 = ids.astype(np.int64)
    ii, jj, kk = ids64 % nx, (ids64 // nx) % ny, ids64 // (nx * ny)
    cells = nx * ny * nz
    table: np.ndarray | None = None
    if cells <= DENSE_MAX and cells <= DENSE_FACTOR * m:
        table = np.full(cells, -1, dtype=np.int32)
        table[ids64] = np.arange(m, dtype=np.int32)
    for _ in range(iterations):
        padded = np.append(own, np.int16(-1))  # the index −1 is the last cell: no neighbour, no vote
        new = np.empty(m, dtype=np.int16)
        for a in range(0, m, STEP):
            if cancel is not None:
                cancel()
            b = min(a + STEP, m)
            votes = [np.zeros(b - a, dtype=np.uint8) for _ in range(k)]
            for di, dj, dk in NEIGHBOURS:
                ni, nj, nk = ii[a:b] + di, jj[a:b] + dj, kk[a:b] + dk
                valid = (ni >= 0) & (ni < nx) & (nj >= 0) & (nj < ny) & (nk >= 0) & (nk < nz)
                nid = ids64[a:b] + (di + nx * dj + nx * ny * dk)
                if table is not None:
                    pos = np.where(valid, table[np.where(valid, nid, 0)], -1).astype(np.int64)
                else:
                    at = np.minimum(np.searchsorted(ids64, np.where(valid, nid, 0)), m - 1)
                    pos = np.where(valid & (ids64[at] == nid), at, -1)
                cn = padded[pos]
                for c in range(k):
                    votes[c] += cn == c
            mine = own[a:b]
            best, lead = votes[0].copy(), np.zeros(b - a, dtype=np.int16)
            keep = np.zeros(b - a, dtype=np.uint8)
            for c in range(k):
                if c:
                    better = votes[c] > best
                    best[better] = votes[c][better]
                    lead[better] = c
                keep = np.where(mine == c, votes[c], keep)
            new[a:b] = np.where(mine < 0, -1, np.where(keep == best, mine, lead))
        own = new
    if single:
        return own
    out = np.full(rows, -1, dtype=np.int16)
    take = codes >= 0
    out[take] = own[inv[take]]
    return out
