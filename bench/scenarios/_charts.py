"""What the benches of charts and swath plots share (ADR 0019 «Замеры», решение 18): `chart_histogram`, `chart_qq`,
`chart_scatter`, `chart_box`, `chart_dendrogram`, `swath_blocks`, `swath_samples`, `contact_analysis`, `stats_tab`,
`chart_selection`.

    project = project_of(engine, folder)             what a route takes of `svc.require_current()`
    answer = measure(project, "histogram", body, "first", info, spots, STAGES)
        info["first_s"]         the request from the snapshot of the table: the caches of columns are emptied
                                first (`forget`), as in a process that has not read the table yet
        info["first_<stage>_s"] the clocks of its stages (`_samples.stages`)
        info["first_again_s"]   the same request once more — the columns are in the cache (what a window asks
                                when a setting is changed); `again=False` — not asked
        info["first_kb"]        the JSON of the answer
    answer, seconds, size = ask(project, kind, body)  one request, nothing else

The time of ТЗ is «от запроса до ответа API при готовом снимке таблицы»: a request is the route as a function —
the settings checked as the stored ones (`routes_charts.checked`), the calculation in a slot of the heavy ones
(`routes_charts.run`: the refusals of the kernels, NaN → null), the answer written as JSON. The set is that of
`_samples` (ci — 5 тыс. скважин, 500 тыс. проб; full — 50 тыс., 5 млн), every table opened once (its snapshot is
in `cache/tables`); the model of the swath scenarios — `tools/gen/blockmodel.py` (`model`).
"""

from __future__ import annotations

import gc
import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import _samples

Spots = Sequence[tuple[Any, str, str]]


def project_of(engine: Any, folder: Path) -> Any:
    return _samples.service(engine, folder).require_current()


def forget(models: bool = False) -> None:
    """The columns of tables (and, with `models`, of block models) kept by the process are dropped."""
    from bf_core.stats import blocks_cache, source

    source.COLUMNS.clear()
    if models:
        blocks_cache.MODELS.clear()
    gc.collect()


def calculator(kind: str) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
    from bf_core.stats import router, routes_charts  # noqa: F401 — the routes register their kinds

    return routes_charts.CALCULATORS[kind]


Calc = Callable[[Any, dict[str, Any]], dict[str, Any]]


def ask(project: Any, kind: str | Calc, body: Mapping[str, Any]) -> tuple[dict[str, Any], float, int]:
    """(the answer, seconds, bytes of its JSON) of `POST /stats/<kind>` with the settings `body`; `kind` may be
    the calculation of a route that keeps no settings (the tab «Статистика»): its body is taken as it is."""
    from fastapi.responses import JSONResponse

    from bf_core.stats import routes_charts as rc

    calc = calculator(kind) if isinstance(kind, str) else kind
    t0 = time.perf_counter()
    settings = rc.checked(kind, body) if isinstance(kind, str) else dict(body)
    answer = rc.run(lambda: calc(project, settings))
    size = len(JSONResponse(answer).body)
    return answer, time.perf_counter() - t0, size


def measure(
    project: Any, kind: str | Calc, body: Mapping[str, Any], key: str, info: dict[str, Any], spots: Spots = (),
    stages: Mapping[str, tuple[str, ...]] | None = None, *, cold: bool = True, again: bool = True,
) -> dict[str, Any]:  # fmt: skip
    """One request timed into `info` (module docstring); `cold=False` — the caches are left as they are."""
    if cold:
        forget()
    with _samples.stages(*spots) as took:
        answer, seconds, size = ask(project, kind, body)
    info[f"{key}_s"] = round(seconds, 3)
    if stages:
        info.update(_samples.timings(took, dict(stages), f"{key}_"))
    info[f"{key}_kb"] = round(size / 1024.0, 1)
    if again:
        second, seconds, _ = ask(project, kind, body)
        info[f"{key}_again_s"] = round(seconds, 3)
        assert digest(second) == digest(answer), f"{key}: the answer from the cached columns differs"
    return answer


def digest(answer: Any) -> str:
    """SHA-256 of an answer as JSON with sorted keys: equal answers — equal bits of every number."""
    text = json.dumps(answer, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def columns(project: Any, set_id: int, fields: Sequence[str], table: str | int | None = None) -> Any:
    """The columns of the table as the charts read them (`stats.source.read`) — for the checks after a run."""
    from bf_core.stats import source

    return source.read(project, set_id, table, list(fields))


# --- domains of another table ---------------------------------------------------------------------------------

ZONES, ZONE, ZONE_COUNT, ZONE_M, ZONE_SKIP = "Домены", "ZONE", 10, 20.0, 37


def zones(meta: dict[str, Any], engine: Any, folder: Path) -> dict[str, Any]:
    """The interval table «Домены» with the text field ZONE of ten values (for `_samples.build(…, more=…)`): a
    hole is cut into whole stretches of 20 m from its top, the stretch j of the hole h (in the order of the
    names) lies in the zone (h + j) mod 10; what is left below the last whole stretch and every 37th hole have
    no zone — their samples are «вне доменов». The keys of the meta — `zones` (the table), `zone_rows`."""
    import numpy as np

    from bf_core.composites import source
    from bf_core.drillholes import store

    got = source.read(engine, meta["set_id"], meta["samples"], [], cache_dir=folder / "cache" / "tables")
    bottom = np.maximum.reduceat(got.to_m, got.starts[:-1])
    names: list[str] = []
    frm: list[float] = []
    text: list[str] = []
    for h, (name, depth) in enumerate(zip(got.holes, bottom.tolist(), strict=True)):
        if h % ZONE_SKIP == 4:
            continue
        for j in range(int(depth // ZONE_M)):
            names.append(name)
            frm.append(j * ZONE_M)
            text.append(zone_label((h + j) % ZONE_COUNT))
    start = np.array(frm, dtype=np.float64)
    data = store.TableData(ZONES, "interval", names, start, start + ZONE_M, [store.ColumnSpec(ZONE, "text")],
                           [np.array(text, dtype=object)])  # fmt: skip
    table_id = int(store.write_table(engine, meta["set_id"], data, job_id=None, tick=_samples._noop)["table_id"])
    _samples.snapshot(engine, folder, meta["set_id"], [table_id])
    return {"zones": table_id, "zone_rows": len(names)}


def zone_label(code: int) -> str:
    return f"Зона {code + 1:02d}"


def zone_codes(cols: Any) -> Any:
    """What `zones` wrote, for the rows of «Пробы» (`columns`): the number of the zone of the middle of every
    sample, −1 — none; computed from the depths, not read from the table."""
    import numpy as np

    h = cols.hole_of().astype(np.int64)
    bottom = np.maximum.reduceat(cols.to_m, cols.starts[:-1])
    j = np.floor(0.5 * (cols.from_m + cols.to_m) / ZONE_M).astype(np.int64)
    inside = (h % ZONE_SKIP != 4) & (j < np.floor(bottom / ZONE_M).astype(np.int64)[h])
    return np.where(inside, (h + j) % ZONE_COUNT, -1)


# --- the block model ------------------------------------------------------------------------------------------

MODEL = "Модель замера"
ORE = "руда"
SIZE = (10.0, 10.0, 5.0)  # of a block; the corner is that of the holes of `tools/gen/drillholes.py`
CORNER = (500_000.0, 7_000_000.0, -200.0)


def model(meta: dict[str, Any], engine: Any, folder: Path, grid: Sequence[int]) -> dict[str, Any]:
    """The model of `tools/gen/blockmodel.py` (`grid` — nx, ny, nz cells; its file — centres in Morton order) as
    a regular model of the project: AU, DENSITY and the category ZONE («руда» — AU ≥ 0.5 г/т, else «порода»).
    For `_samples.build(…, more=…)`: the keys of the meta — `model_id`, `cells`, `ore_cells`."""
    import numpy as np
    import pyarrow.parquet as pq
    from tools.gen.blockmodel import generate

    from bf_core.blocks import store
    from bf_core.db import dialect
    from bf_core.storage.blobs import BlobStore

    nx, ny, nz = (int(n) for n in grid)
    path = folder.parent / "model.parquet"
    cells = generate(path, nx, ny, nz, size=SIZE)
    t = pq.read_table(path)
    path.unlink()
    idx = np.zeros(cells, dtype=np.int64)
    for k, (name, n) in reversed(list(enumerate((("XC", nx), ("YC", ny), ("ZC", nz))))):
        at = np.floor((t.column(name).to_numpy() - CORNER[k]) / SIZE[k]).astype(np.int64)
        assert int(at.min()) == 0 and int(at.max()) == n - 1, name
        idx = idx * n + at
        del at
    order = np.argsort(idx)
    au, rho = t.column("AU").to_numpy()[order], t.column("DENSITY").to_numpy()[order]
    del t
    idx = idx[order]
    del order
    gc.collect()
    fields = [{"name": "AU", "kind": "number", "unit": "г/т"}, {"name": "DENSITY", "kind": "number", "unit": "т/м³"},
              {"name": "ZONE", "kind": "category", "labels": [ORE, "порода"]}]  # fmt: skip
    bounds = {"corner": list(CORNER), "counts": [nx, ny, nz], "size": list(SIZE)}
    ore = 0
    with store.CellsWriter(BlobStore(folder), bounds, fields) as w:
        for a in range(0, cells, 1 << 20):
            b = min(a + (1 << 20), cells)
            zone = np.where(au[a:b] >= 0.5, 0, 1).astype(np.int32)
            ore += int((zone == 0).sum())
            w.write(idx[a:b], {"AU": au[a:b], "DENSITY": rho[a:b], "ZONE": zone})
        staged = w.finish()
    try:
        with dialect.write_tx(engine) as conn:
            model_id = store.commit_block_model(conn, BlobStore(folder), staged, name=MODEL)
    finally:
        staged.discard()
    del idx, au, rho
    gc.collect()
    return {"model_id": int(model_id), "cells": int(cells), "ore_cells": ore, "grid": [nx, ny, nz]}
