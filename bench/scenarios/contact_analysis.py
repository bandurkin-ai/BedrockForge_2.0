"""The contact analysis of two domains along the holes — what the window «Контактный анализ» asks (ТЗ 8.3 № 227;
ADR 0019 «Замеры»: full — 50 тыс. скважин, 5 млн проб, ≤ 10 с; ci — 5 тыс. скважин, ориентир ≤ 2 с).

The set of `_samples`: AU of «Пробы» (quartz is four times richer than the other rocks, 2 % of the samples follow
a gap of 0,25 m in sampling, 1 % have no value), the domain — ROCK of «Литология» (20 intervals a hole with
contacts at 0,1 m inside the samples; the rock of a sample is that of its middle), the contact «КВЦ | ГРН», the
samples within 20 m of it in steps of 1 m, weights by length. The route `POST /stats/contact` as a function
(`_charts.ask`):
  contact_s        from the snapshots of both tables; stages: `contact_read_s` (the columns of both),
                   `contact_domain_s` (the rock of every sample, `source.domain_at`), `contact_lines_s` (the rows
                   by holes and depth, `intervals.build_lines`), `contact_runs_s` (the stretches of one rock and
                   their contacts, `contact.domain_contacts`), `contact_select_s` (the rules of the selection),
                   `contact_steps_s` (the sums of the steps);
  contact_again_s  the same once more: the columns and the rocks of the samples are in the cache;
  mirror_s         «ГРН | КВЦ» — from the cache; for the check;
  rock_s           the contact by ROCK of «Пробы» itself (a text field of the same table): ROCK is read.
What is checked (a failure stops the run), against the columns by numpy (the rule written anew: runs of one rock
without a gap, a contact between adjacent runs of the two rocks, the distance of a middle from the nearer
contact of its own run): the numbers of contacts and of holes with one, the number of samples of every step
exactly, their means weighted by length (1e-9); quartz is richer on its side; the mirror gives the same steps in
the reverse order bit for bit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "domain": ("domain",), "lines": ("lines",), "runs": ("runs",), "select": ("select",),
          "steps": ("steps",)}  # fmt: skip
FIELD, A, B = "AU", "КВЦ", "ГРН"
DISTANCE, STEP, EPS = 20.0, 1.0, 1e-6


def spots() -> _charts.Spots:
    from bf_core.composites import intervals
    from bf_core.stats import contact, source

    return (
        (source, "_read_lines", "read"), (source, "domain_at", "domain"), (intervals, "build_lines", "lines"),
        (contact, "domain_contacts", "runs"), (contact, "_select", "select"), (contact, "_chart", "steps"),
    )  # fmt: skip


def bodies(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD, "domain": _samples.ROCK,
            "distance": DISTANCE, "step": STEP}  # fmt: skip
    litho = {**base, "domain_table": _samples.LITHO}
    return {"contact": {**litho, "a": A, "b": B}, "mirror": {**litho, "a": B, "b": A}, "rock": {**base, "a": A, "b": B}}


def reference(cols: Any, codes: Any, a: int, b: int) -> dict[str, Any]:
    """The answer by numpy from the rule of ADR 0019 §4 (the rows are lines: by hole and depth, no overlaps)."""
    import numpy as np

    hole, frm, to = cols.hole_of().astype(np.int64), cols.from_m, cols.to_m
    n = cols.n
    new = np.ones(n, dtype=bool)  # a row starts a run: another hole, another rock, a gap in sampling
    new[1:] = (hole[1:] != hole[:-1]) | (codes[1:] != codes[:-1]) | (frm[1:] - to[:-1] > EPS)
    run = np.cumsum(new) - 1
    first = np.flatnonzero(new)
    last = np.append(first[1:], n) - 1
    r_hole, r_code, r_frm, r_to = hole[first], codes[first], frm[first], to[last]
    side = np.where(r_code == a, 0, np.where(r_code == b, 1, -1))
    other = np.where(side == 0, b, a)
    joined = np.zeros(len(first), dtype=bool)  # a run touches the run above it
    joined[1:] = (r_hole[1:] == r_hole[:-1]) & (r_frm[1:] - r_to[:-1] <= EPS)
    top = np.zeros(len(first), dtype=bool)
    top[1:] = (side[1:] >= 0) & joined[1:] & (r_code[:-1] == other[1:])
    bottom = np.zeros(len(first), dtype=bool)
    bottom[:-1] = (side[:-1] >= 0) & joined[1:] & (r_code[1:] == other[:-1])
    mid = 0.5 * (frm + to)
    d = np.where(top[run], mid - r_frm[run], np.inf)
    d = np.where(bottom[run] & (r_to[run] - mid < d), r_to[run] - mid, d)
    v, w = cols.number(FIELD), cols.number("length")
    taken = np.isfinite(v) & (w > 0.0) & (d <= DISTANCE + EPS)
    n_side = int(np.ceil(DISTANCE / STEP))
    k = np.minimum(np.floor((d[taken] + EPS) / STEP).astype(np.int64), n_side - 1)
    q = np.where(side[run][taken] == 0, n_side - 1 - k, n_side + k)
    count = np.bincount(q, minlength=2 * n_side)
    weight = np.bincount(q, weights=w[taken], minlength=2 * n_side)
    metal = np.bincount(q, weights=(w * v)[taken], minlength=2 * n_side)
    return {"contacts": int(top.sum()), "holes": len(np.unique(r_hole[top])), "n": count.tolist(),
            "mean": (metal / np.maximum(weight, 1e-300)).tolist(), "overlap": int((frm[1:] < to[:-1] - EPS)[
                hole[1:] == hole[:-1]].sum())}  # fmt: skip


def check(got: dict[str, dict[str, Any]], ref: dict[str, Any]) -> None:
    answer, mirror = got["contact"], got["mirror"]
    assert ref["overlap"] == 0 == answer["counts"]["overlap"] and answer["counts"]["no_depth"] == 0, answer["counts"]
    assert answer["contacts"] == ref["contacts"] > 0 and answer["holes"] == ref["holes"], (answer["contacts"], ref)
    assert answer["n"] == ref["n"] and sum(answer["n"]) == answer["counts"]["in_steps"], (answer["n"], ref["n"])
    assert len(answer["edges"]) == len(answer["n"]) + 1 == 2 * int(DISTANCE / STEP) + 1 and min(answer["n"]) > 0
    for mean, want in zip(answer["mean"], ref["mean"], strict=True):
        assert _samples.close(mean, want, 1e-9), (mean, want)
    assert answer["a"]["label"] == A and answer["a"]["mean"] > 2.0 * answer["b"]["mean"], (answer["a"], answer["b"])
    assert answer["domain_table"] == _samples.LITHO and answer["weighted"] and answer["domain_outside"] > 0
    assert mirror["n"] == answer["n"][::-1] and mirror["mean"] == answer["mean"][::-1], "the mirror differs"
    assert mirror["contacts"] == answer["contacts"] and mirror["a"] == answer["b"] and mirror["b"] == answer["a"]
    rock = got["rock"]
    assert rock["domain_table"] is None and rock["domain_outside"] == 0 and rock["contacts"] > 0, rock["contacts"]
    assert sum(rock["n"]) == rock["counts"]["in_steps"] > 0 and rock["a"]["mean"] > rock["b"]["mean"]


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for body in bodies(meta).values():
            _charts.ask(_charts.project_of(engine, folder), "contact", body)

    _samples.warm(every)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.stats import source

    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"], "litho_rows": meta["litho_rows"]}
    try:
        project = _charts.project_of(engine, folder)
        ask = bodies(meta)
        got = {"contact": _charts.measure(project, "contact", ask["contact"], "contact", info, spots(), STAGES)}
        for key in ("mirror", "rock"):
            got[key] = _charts.measure(project, "contact", ask[key], key, info, cold=False, again=False)
        info["contact_mb"] = _samples.peak_mb()
        cols = _charts.columns(project, meta["set_id"], [FIELD, "length"], meta["samples"])
        litho = _charts.columns(project, meta["set_id"], [_samples.ROCK], meta["litho"])
        dom = source.domain_at(cols, litho, _samples.ROCK)
        check(got, reference(cols, dom.codes, dom.labels.index(A), dom.labels.index(B)))
    finally:
        engine.dispose()
    answer = got["contact"]
    info.update(contacts=answer["contacts"], holes_with=answer["holes"], in_steps=answer["counts"]["in_steps"],
                mean_a=round(answer["a"]["mean"], 4), mean_b=round(answer["b"]["mean"], 4))  # fmt: skip
    info["peak_mb"] = _samples.peak_mb()
    return info
