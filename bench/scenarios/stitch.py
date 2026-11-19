"""«Собрать из контуров»: the operation over a series of sections and the preview of the editor (ТЗ 7.23; ADR 0015
«Замеры»: 50 contours × 200 / 500 × 500 vertices — by the first measurement, ориентир ≤ 2 s / ≤ 20 s; the preview
of 20 contours × 500 vertices in the process of the API ≤ 100 ms).

The series: closed contours of an ore body on vertical sections 20 m apart at UTM coordinates — ellipses that
change size, elongation, centre and start vertex from section to section, every other one drawn the other way,
with survey noise. `geo/stitch.stitch_contours` as the operation `stitch` calls it: flat caps, the check of
self-intersections, strips in threads:
  stitch_s         the whole call.
The preview: 20 plan contours of 500 vertices 5 m apart (as `tests/test_ops_api.py`), the body of
`POST /geometry/stitch/preview` as JSON — what the process of the API does with it: the body checked by the
model of the router (pydantic), then `ops/preview.stitch_preview` (no check of self-intersections, the answer
BFA1); the kernels are loaded before (`warm()` of the quick worker):
  preview_s        the median of 7 requests; preview_max_s — the slowest.
What is checked:
  open_edges         open and non-manifold edges of the solid (0: closed);
  vertex_mismatches  input vertices not found bit for bit at their place in the result;
  threads_mismatch   1 if the preview series stitched in one thread differs by a bit from the one in threads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ORIGIN = (512_345.678, 6_789_012.345, 250.0)


def _series(contours: int, vertices: int) -> list[Any]:
    import numpy as np

    rng = np.random.default_rng(23)
    a = np.radians(30.0)
    strike, across = np.array([np.cos(a), np.sin(a), 0.0]), np.array([-np.sin(a), np.cos(a), 0.0])
    out = []
    for i in range(contours):
        t = np.linspace(0.0, 2.0 * np.pi, vertices, endpoint=False) + 0.1 * i
        r = 1.0 + rng.normal(0.0, 0.004, vertices)
        x = 4.0 * np.sin(0.3 * i) + (30.0 + 5.0 * np.sin(i)) * r * np.cos(t)
        z = (12.0 + i % 7) * r * np.sin(t)
        ring = np.asarray(ORIGIN) + x[:, None] * strike + (20.0 * i) * across + z[:, None] * [0.0, 0.0, 1.0]
        out.append(ring if i % 2 else ring[::-1].copy())
    return out


def _preview_body() -> bytes:
    import numpy as np

    a = np.linspace(0, 2 * np.pi, 501)[:-1]
    rings = [np.c_[(10 + k % 3) * np.cos(a), (10 + k % 3) * np.sin(a), np.full(500, 5.0 * k)] + [5e5, 7e6, 0.0]
             for k in range(20)]  # fmt: skip
    return json.dumps({"contours": [r.tolist() for r in rings], "ties": []}).encode("utf-8")


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    np.save(work / "series.npy", np.stack(_series(int(params["contours"]), int(params["vertices"]))))
    (work / "preview.json").write_bytes(_preview_body())
    load()


def load() -> None:
    import bf_core.ops.router  # noqa: F401 - the model of the body, FastAPI
    from bf_core.ops.preview import warm

    warm()


def _preview(raw: bytes) -> bytes:
    from bf_core.ops.preview import stitch_preview
    from bf_core.ops.router import PreviewBody

    body = PreviewBody.model_validate_json(raw)
    return stitch_preview(
        body.contours, body.ties, start_cap=body.start_cap, end_cap=body.end_cap, cap_distance=body.cap_distance
    )


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import statistics
    import time

    import numpy as np

    from bf_core.geo.meshes import edge_summary
    from bf_core.geo.stitch import stitch_contours
    from bf_core.viz import binary

    series = list(np.load(work / "series.npy"))
    t0 = time.perf_counter()
    result = stitch_contours(series, start_cap="flat", end_cap="flat")
    stitch_s = time.perf_counter() - t0
    given = np.vstack(series)
    vertex_mismatches = int(np.count_nonzero((result.vertices[: len(given)] != given).any(axis=1)))

    raw = (work / "preview.json").read_bytes()
    times, header = [], {}
    for _ in range(7):
        t0 = time.perf_counter()
        answer = _preview(raw)
        times.append(time.perf_counter() - t0)
        header = binary.decode(answer)[0]
    rings = [np.asarray(c, dtype=np.float64) for c in json.loads(raw)["contours"]]
    one, many = (stitch_contours(rings, check=False, threads=n) for n in (1, None))
    same = one.vertices.tobytes() == many.vertices.tobytes() and one.triangles.tobytes() == many.triangles.tobytes()
    return {
        "contours": len(series),
        "vertices": int(len(given)),
        "stitch_s": round(stitch_s, 4),
        "triangles": int(len(result.triangles)),
        "open_edges": int(sum(edge_summary(result.triangles, len(result.vertices)))),
        "vertex_mismatches": vertex_mismatches,
        "preview_s": round(statistics.median(times), 4),
        "preview_max_s": round(max(times), 4),
        "preview_triangles": int(header.get("triangles", 0)),
        "threads_mismatch": int(not same),
    }
