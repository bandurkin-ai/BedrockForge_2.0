"""The display of a block model: the route `POST /viz/blocks/{id}/shell` → BFA1 `blocks` (ТЗ 8.7.6, 5.3: «первый кадр
≤ 3 с»; ADR 0023 §11 and «Замеры»: ≤ 3 s at 20 млн блоков).

The model of `tools/gen/blockmodel.py` with sub-blocks (`rows` rows; 25 % of the parents split into eight; AU, DENSITY,
F2, F3, the smooth field SM and the category DOM), the route as a function (`viz_routes.shell`: the body checked, a
slot of the heavy ones, the answer built and kept) in a process that has read nothing yet:
  first_s     the shell of every block, the colour AU on a scale — the first request: the columns `idx`, `s`, `w`, AU
              are read from the file of the model into the cache of the process (what the window waits for first)
  filter_s    the same under the quick restrictor SM > 0.78 (bodies with rough sides: some millions of faces on 20 млн)
  classes_s   the same restrictor, the colour DOM by categories; again_s — the request of `filter` once more (the answer
              is kept: the cache of ≤ 192 МБ)
  high_s      a narrower restrictor SM > 1.0 (a body of a few million faces: under the limit, no LOD)
  all_s       the mode «все блоки» under the same restrictor (every face before the merge: LOD above the limit `limit`)
  <name>_faces, _raw, _lod, _kb   the faces drawn, those before the merge, the level of LOD, the kilobytes of the answer
What is checked (a failure stops the run): the header of every answer — the kind `blocks`, the rows of the model, the
blocks that passed (the numbers of SM > 0.78 and > 1.0 by numpy, exactly), the faces at most those before the merge
(and at most the limit once LOD works), the arrays of the length of the faces; the request once more gives the same
bytes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

LEVEL = 0.78
HIGH = 1.0  # a narrower body: its faces stay under the limit (no LOD)
RESTRICT = {"cond": {"field": "SM", "op": ">", "value": LEVEL}}
LIMIT = 5_000_000
CASES = {
    "first": {"mode": "shell", "colour": {"field": "AU", "kind": "scale"}},
    "filter": {"mode": "shell", "restrict": RESTRICT, "colour": {"field": "AU", "kind": "scale"}},
    "classes": {"mode": "shell", "restrict": RESTRICT, "colour": {"field": "DOM", "kind": "categories"}},
    "again": {"mode": "shell", "restrict": RESTRICT, "colour": {"field": "AU", "kind": "scale"}},
    "high": {"mode": "shell", "restrict": {"cond": {"field": "SM", "op": ">", "value": HIGH}},
             "colour": {"field": "AU", "kind": "scale"}},  # fmt: skip
    "all": {"mode": "all", "restrict": RESTRICT, "colour": {"field": "AU", "kind": "scale"}},
}


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.sub_model(ctx, int(params["rows"]), fields=int(params.get("fields", 4))))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def passed(ctx: bc.Ctx, model_id: int, level: float) -> int:
    """The blocks SM > `level` by numpy over the portions of the column."""
    return sum(int((c.cols["SM"] > level).sum()) for c in ctx.model(model_id).iter(["SM"], bc.PORTION))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import viz_routes
    from bf_core.viz import binary

    bc.limit_threads(params)
    limit = int(params.get("limit", LIMIT))
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"], "parents": meta["parents"]}
    try:
        bodies: dict[str, bytes] = {}
        heads: dict[str, dict[str, Any]] = {}
        arrays: dict[str, dict[str, np.ndarray]] = {}
        for key, case in CASES.items():
            t0 = time.perf_counter()
            body = bytes(viz_routes.shell(ctx.svc, mid, {**case, "limit": limit}).body)
            info[f"{key}_s"] = round(time.perf_counter() - t0, 3)
            bodies[key] = body
            heads[key], arrays[key] = binary.decode(body)
            h = heads[key]
            info.update({f"{key}_faces": h["faces"], f"{key}_raw": h["faces_raw"], f"{key}_lod": h["lod"],
                         f"{key}_kb": round(len(body) / 1024.0, 1)})  # fmt: skip
        want = passed(ctx, mid, LEVEL)
        high = passed(ctx, mid, HIGH)
        for key, h in heads.items():
            assert h["kind"] == "blocks" and h["model"] == mid and h["rows"] == meta["rows"], (key, h)
            expect = {"first": meta["rows"], "high": high}.get(key, want)
            assert h["passed"] == expect, (key, h["passed"], expect)
            assert 0 < h["faces"] <= h["faces_raw"] and (h["lod"] == 0 or h["faces"] <= limit), (key, h["faces"])
            a = arrays[key]
            assert len(a["a"]) == len(a["k"]) == h["faces"] and len(a["c"]) == 3 * h["faces"], key
            assert len(a["h"]) == 2 * h["faces"], key
        assert bodies["again"] == bodies["filter"], "the request once more gave another answer"
        assert heads["classes"]["labels"] == ["a", "b", "c"], heads["classes"]["labels"]
        info["passed"] = want
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
