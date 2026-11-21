"""The preview of one hole in the windows of sample processing (ADR 0018 решения 12, 26, 27: «предпросмотр скважины
≤ 50 мс (ci) / 100 мс (full)» — 5 тыс. скважин и 500 тыс. проб / 50 тыс. и 5 млн).

The set of `_samples`. The routes `POST /composites/{op}/preview` are called as functions, as the API runs them in
its thread pool (решение 26: synchronous, the same computation as the job, on the rows of one hole read from
`dh_rows` by its index), for `shown` holes spread over the set, one op of `ops` after another:
  length, domain, bench   composites (`routes_run.preview`): by length, inside the rocks of «Литология», by benches;
  merge, coords           «Пробы» + «Литология»; XB…ZE of the samples;
  topcut                  the top cut of AU by the percentile 99 of every rock — the thresholds come from the whole
                          table, computed at the first call and kept (`routes_prep`);
  equivalent              AUEQ of AU, AG and CU.
Per op: `<op>_first_s` — the first call of the process for this op (the modules; the trajectories of the whole set
for the first op that needs them; the thresholds of the top cut), `<op>_s` — the median of the other calls,
`<op>_max_s` — the slowest of them. `preview_s` — the largest of the medians: what the budget of решение 27 is
for; `preview_max_s` — the slowest call after a first one; `first_s` — the slowest first call; `answer_kb` — the
largest answer as JSON. The set has more than 200 000 samples, so no call computes the summary of the whole set
(`set_summary` is null — the job's report has it). What is checked (a failure stops the run): every answer has
the samples of its hole and rows of the result with every value a number or null.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

from . import _samples
from . import composites as comp

RUN_OPS = ("length", "domain", "bench", "merge", "coords")  # `routes_run.preview`
OPS = (*RUN_OPS, "topcut", "equivalent")


def request(meta: dict[str, Any], op: str, params: dict[str, Any]) -> dict[str, Any]:
    """`params` of the body, as the window of the op sends them."""
    table = str(meta["samples"])
    if op in ("length", "domain", "bench"):
        return {k: v for k, v in comp.body(meta, op, params).items() if k != "name"}
    if op == "merge":
        return {"set_id": meta["set_id"], "tables": [table, str(meta["litho"])]}
    if op == "coords":
        return {"set_id": meta["set_id"], "table": table}
    if op == "topcut":
        return {"set_id": meta["set_id"], "table": table, "field": "AU", "percentile": 99, "domain": _samples.ROCK}
    if op == "equivalent":
        return {"set_id": meta["set_id"], "table": table, "base": "AU", "base_price": 2000, "base_recovery": 0.9,
                "others": [{"field": "AG", "price": 25, "recovery": 0.8},
                           {"field": "CU", "price": 9000, "recovery": 0.85}]}  # fmt: skip
    raise ValueError(op)


def call(svc: Any, op: str, body: dict[str, Any]) -> dict[str, Any]:
    from bf_core.composites import routes_prep, routes_run

    if op in RUN_OPS:
        return routes_run.preview(svc, op, body)
    return {"topcut": routes_prep.topcut_preview, "equivalent": routes_prep.equivalent_preview}[op](svc, body)


def hole_ids(engine: Any, set_id: int, shown: int) -> list[int]:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import dh_holes

    with dialect.read_tx(engine) as conn:
        ids = conn.execute(sa.select(dh_holes.c.id).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id))
        ids = [int(i) for i in ids.scalars()]
    step = max(1, len(ids) // max(1, shown))
    return ids[step // 2 :: step][:shown]


def check(op: str, got: dict[str, Any]) -> None:
    samples, result = got["samples"], got["result"]
    assert got["hole"] and len(samples["from"]) > 0 and len(result["from"]) == len(result["to"]) > 0, (op, got["hole"])
    assert result["values"] and all(len(v) == len(result["from"]) for v in result["values"].values()), op
    assert not any(v != v for col in result["values"].values() for v in col if isinstance(v, float)), op  # no NaN


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        svc = _samples.service(engine, folder)
        hole = hole_ids(engine, meta["set_id"], 1)[0]
        for op in OPS:
            call(svc, op, {"params": request(meta, op, {}), "hole_id": hole})

    _samples.warm(every)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"], "holes": meta["holes"]}
    medians, slowest, firsts, size = [], [], [], 0
    try:
        svc = _samples.service(engine, folder)
        holes = hole_ids(engine, meta["set_id"], int(params.get("shown", 30)))
        assert len(holes) >= 3, holes
        for op in params.get("ops", OPS):
            body = request(meta, op, params)
            took = []
            for hole in holes:
                t0 = time.perf_counter()
                got = call(svc, op, {"params": body, "hole_id": hole})
                took.append(time.perf_counter() - t0)
                check(op, got)
                size = max(size, len(json.dumps(got, ensure_ascii=False).encode()))
            info[f"{op}_first_s"] = round(took[0], 4)
            info[f"{op}_s"] = round(statistics.median(took[1:]), 4)
            info[f"{op}_max_s"] = round(max(took[1:]), 4)
            firsts.append(took[0])
            medians.append(info[f"{op}_s"])
            slowest.append(info[f"{op}_max_s"])
    finally:
        engine.dispose()
    info.update({"preview_s": max(medians), "preview_max_s": max(slowest), "first_s": round(max(firsts), 3),
                 "shown": len(holes), "answer_kb": round(size / 1024.0, 1)})  # fmt: skip
    return info
