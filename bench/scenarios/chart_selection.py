"""The selection of samples of a chart shown in 3D — what a frame or a lasso on a chart asks (ТЗ 8.3 № 237;
ADR 0019 §8, «Замеры»: 200 тыс. строк → отрезки — ≤ 1 с, ci и full; the table — 500 тыс. and 5 млн проб).

The set of `_samples`; 200 000 ids — the limit of a selection: rows of «Пробы» spread evenly over the table, given
in a shuffled order, five of them no rows of it. The routes as functions: `POST /stats/selection {rows}`
(the token of the selection) and `GET /stats/selection/{token}/segments` (BFA1: the start and the end of every
interval on the path of its hole — the layer of segments of the 3D window). The trajectories of the set are
built before the clock, as the 3D window that shows the holes has them (`traj_s` — a line of the report):
  rows_s       the body parsed, the ids found among the rows of the table (`rows_find_s`: `selection.known` —
               the lines of the table from its snapshot, `rows_read_s`, and the order of its ids), the token;
  segments_s   `segments_find_s` — the rows of the selection in the lines of the table, the positions of their
               ends on the trajectories, `segments_encode_s` — BFA1 (`segments_kb`);
  select_s     the two together (a budget: ≤ 1 с);
  again_s      another selection of 200 000 rows and its segments — the lines and the order of the ids are in
               the cache (the next frame on the chart);
  lasso_s      a lasso of 200 vertices on the scatter AU × AG with logarithmic axes — the core picks the pairs
               inside it (`lasso_rows`) — and its segments, from the cache.
What is checked (a failure stops the run): the selection holds the rows asked and counts the five strangers;
a segment for every row but those of the holes without a path (their number is that of the middles without a
place), the rows and the depths are those asked; a segment is not longer than its interval and its middle is
the middle of the interval on the path (1 cm: the chord of 1 m of a curved hole); the lasso takes every pair
inside the ellipse it is inscribed around and none outside the one it is inscribed in.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from . import _charts, _samples

ROWS, STRANGERS, VERTICES = 200_000, 5, 200
X, Y = "AU", "AG"
CENTRE, RADIUS = (1.3, 1.8), (0.25, 0.35)  # of the lasso, log10 of AU and AG: the rich corner of the cloud


def spots() -> _charts.Spots:
    from bf_core.stats import selection, source
    from bf_core.viz import binary

    return ((source, "_read_lines", "read"), (selection, "known", "find"), (selection, "lines_of", "find"),
            (binary, "encode", "encode"))  # fmt: skip


def lasso(meta: dict[str, Any]) -> dict[str, Any]:
    turn = 2.0 * math.pi / VERTICES
    (cx, cy), (rx, ry) = CENTRE, RADIUS
    polygon = [
        [10.0 ** (cx + rx * math.cos(k * turn)), 10.0 ** (cy + ry * math.sin(k * turn))] for k in range(VERTICES)
    ]
    chart = {"kind": "scatter", "set_id": meta["set_id"], "table": str(meta["samples"]), "x": X, "y": Y,
             "log_x": True, "log_y": True}  # fmt: skip
    return {"chart": chart, "polygon": polygon}


def select(svc: Any, text: str, key: str, info: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """A selection by the body `text` and its segments: `<key>_s` and the clocks of the stages of each."""
    from bf_core.stats import routes_selection

    stages = {"read": ("read",), "find": ("find",), "encode": ("encode",)}
    with _samples.stages(*spots()) as took:
        t0 = time.perf_counter()
        picked = routes_selection.post_selection(svc, json.loads(text))
        answer = json.dumps(picked, ensure_ascii=False)
        t1 = time.perf_counter()
    info[f"{key}_rows_s"] = round(t1 - t0, 3)
    info.update(_samples.timings(took, stages, f"{key}_rows_"))
    with _samples.stages(*spots()) as took:
        t0 = time.perf_counter()
        body = bytes(routes_selection.selection_segments(svc, picked["token"]).body)
        t1 = time.perf_counter()
    info[f"{key}_segments_s"] = round(t1 - t0, 3)
    info.update(_samples.timings(took, stages, f"{key}_segments_"))
    info[f"{key}_s"] = round(info[f"{key}_rows_s"] + info[f"{key}_segments_s"], 3)
    info[f"{key}_kb"] = round(len(body) / 1024.0, 1)
    assert json.loads(answer)["token"] == picked["token"]
    return picked, body


def bodies(row_ids: Any) -> tuple[list[Any], list[str]]:
    """Two selections of ROWS ids, rows spread over the table (the second — shifted by a row), shuffled; in the
    first STRANGERS of them are not rows: (the rows of each, ascending; the ids given)."""
    import numpy as np

    n = row_ids.shape[0]
    at = np.unique(np.rint(np.linspace(0.0, n - 2.0, ROWS)).astype(np.int64))
    assert at.size == ROWS, (at.size, n)
    rng = np.random.default_rng(1335)
    ids = [np.sort(row_ids[at[STRANGERS:]]), np.sort(row_ids[at + 1])]
    strange = (int(row_ids.max()) + 1 + np.arange(STRANGERS)).tolist()
    given = [rng.permutation(ids[0]).tolist() + strange, rng.permutation(ids[1]).tolist()]
    return ids, given


def check_segments(body: bytes, picked: dict[str, Any], ids: Any, cols: Any, points: Any) -> int:
    import numpy as np

    from bf_core.viz import binary

    head, arrays = binary.decode(body)
    lines = np.flatnonzero(np.isin(cols.row_ids, ids))  # the rows of the selection in line order
    placed = np.isfinite(points[lines]).all(axis=1)
    k = int(placed.sum())
    assert head["kind"] == "selection_segments" and head["n"] == picked["n"] == len(lines), (head["n"], picked["n"])
    assert head["segments"] == k and head["no_position"] == len(lines) - k, (head["segments"], k)
    rows = lines[placed]
    assert np.array_equal(arrays["row"], cols.row_ids[rows]), "the rows of the segments"
    assert np.array_equal(arrays["from"], cols.from_m[rows]) and np.array_equal(arrays["to"], cols.to_m[rows])
    a = arrays["a"].reshape(-1, 3).astype(np.float64) + np.array(head["origin"])
    b = arrays["b"].reshape(-1, 3).astype(np.float64) + np.array(head["origin"])
    length = cols.to_m[rows] - cols.from_m[rows]
    chord = np.sqrt(((b - a) ** 2).sum(axis=1))
    assert (chord <= length + 2e-3).all() and (chord >= 0.9 * length - 2e-3).all(), float((length - chord).max())
    assert float(np.abs(0.5 * (a + b) - points[rows]).max()) < 0.01, "the middles of the segments"
    return k


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def small(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        svc = _samples.service(engine, folder)
        cols = _charts.columns(svc.require_current(), meta["set_id"], [], meta["samples"])
        text = json.dumps({"set_id": meta["set_id"], "table": str(meta["samples"]), "rows": cols.row_ids[::3].tolist()})
        select(svc, text, "warm", {})
        select(svc, json.dumps(lasso(meta)), "warm", {})

    _samples.warm(small)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import numpy as np

    from bf_core.drillholes.trajectory import trajectories
    from bf_core.stats import routes_selection, source

    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"], "selected": ROWS}
    try:
        svc = _samples.service(engine, folder)
        project = svc.require_current()
        head = {"set_id": meta["set_id"], "table": str(meta["samples"])}
        ids, given = bodies(_charts.columns(project, meta["set_id"], [], meta["samples"]).row_ids)
        texts = [json.dumps({**head, "rows": rows}) for rows in given]
        t0 = time.perf_counter()
        trajectories(project, meta["set_id"], "minimum_curvature")  # the 3D window that shows the holes has them
        info["traj_s"] = round(time.perf_counter() - t0, 3)
        _charts.forget()
        first, seg = select(svc, texts[0], "select", info)
        again, seg_again = select(svc, texts[1], "again", info)
        picked, seg_lasso = select(svc, json.dumps(lasso(meta)), "lasso", info)
        info["select_mb"] = _samples.peak_mb()
        cols = _charts.columns(project, meta["set_id"], [X, Y], meta["samples"])
        points = source.midpoints(project, cols)
        assert first["n"] == ROWS - STRANGERS and first["unknown"] == STRANGERS and first["source"] == "rows", first
        assert again["n"] == ROWS and again["unknown"] == 0 and again["token"] != first["token"], again
        info["segments"] = check_segments(seg, first, ids[0], cols, points)
        check_segments(seg_again, again, ids[1], cols, points)
        # the lasso: a polygon of VERTICES vertices inscribed in an ellipse of the axes (log10)
        got = np.array(routes_selection.selection_rows(svc, picked["token"])["rows"], dtype=np.int64)
        x, y = cols.number(X), cols.number(Y)
        with np.errstate(invalid="ignore", divide="ignore"):
            r2 = ((np.log10(x) - CENTRE[0]) / RADIUS[0]) ** 2 + ((np.log10(y) - CENTRE[1]) / RADIUS[1]) ** 2
        inner = math.cos(math.pi / VERTICES) ** 2  # the ellipse inscribed in the polygon
        inside = np.isin(cols.row_ids, got)
        assert picked["source"] == "scatter" and 0 < picked["n"] == got.size <= ROWS, picked
        assert inside[r2 < inner * (1.0 - 1e-9)].all() and not inside[~(r2 <= 1.0 + 1e-9)].any(), picked["n"]
        check_segments(seg_lasso, picked, got, cols, points)
        info["lasso_rows"] = picked["n"]
    finally:
        engine.dispose()
    info["peak_mb"] = _samples.peak_mb()
    return info
