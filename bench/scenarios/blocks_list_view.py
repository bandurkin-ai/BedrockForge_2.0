"""The display of a list of blocks: `POST /viz/blocks/{id}/shell` → BFA1 `blocks` (ADR 0030 «Замеры»; ТЗ 8.0.2,
8.7.6: «первый кадр ≤ 3 с»; подшаг 27.4г).

The model of `blocks_files.write_list_dat` imported as a list (1 million blocks, 6 million faces): the route as a
function (`viz_routes.shell`), the six faces of every block, the faces two blocks share are hidden, no merge;
above the limit of faces the blocks are coarsened to cells of 2^ℓ of the enclosing grid (`list_shell.view`):
  first_s    the shell, the colour AU on a scale — the first request: the boxes and AU are read from the file
  filter_s   the shell under the quick restrictor F2 > 0.5 (a half of the blocks: rough bodies)
  all_s      the mode «все блоки» (6 million faces before LOD: above the limit of 5 million LOD must work)
  again_s    the request of `filter` once more (the answer is kept)
  <name>_faces, _raw, _lod, _kb   the faces drawn, those before LOD, the level, the kilobytes of the answer
Checked (a failure stops the run): the kind and the rows, the blocks that passed (F2 > 0.5 by numpy, exactly), the faces
at most those before LOD, LOD works above the limit (`all`: lod ≥ 1 and the faces within the limit), the request
once more gives the same bytes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import blocks_files

LIMIT = 5_000_000
RESTRICT = {"cond": {"field": "F2", "op": ">", "value": 0.5}}
CASES = {
    "first": {"mode": "shell", "colour": {"field": "AU", "kind": "scale"}},
    "filter": {"mode": "shell", "restrict": RESTRICT, "colour": {"field": "AU", "kind": "scale"}},
    "all": {"mode": "all", "colour": {"field": "AU", "kind": "scale"}},
    "again": {"mode": "shell", "restrict": RESTRICT, "colour": {"field": "AU", "kind": "scale"}},
}


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**blocks_files.list_model(ctx, work, int(params["rows"]), int(params.get("fields", 20))))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


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
        want = sum(int((c.cols["F2"] > 0.5).sum()) for c in ctx.model(mid).iter(["F2"], bc.PORTION))
        for key, h in heads.items():
            assert h["kind"] == "blocks" and h["model"] == mid and h["rows"] == meta["rows"], (key, h)
            expect = meta["rows"] if key in ("first", "all") else want
            assert h["passed"] == expect, (key, h["passed"], expect)
            assert 0 < h["faces"] <= h["faces_raw"] and (h["lod"] == 0 or h["faces"] <= limit), (key, h["faces"])
            a = arrays[key]
            assert len(a["a"]) == len(a["k"]) == h["faces"] and len(a["c"]) == 3 * h["faces"], key
        assert heads["all"]["faces_raw"] > limit and heads["all"]["lod"] >= 1, heads["all"]  # LOD works above the limit
        assert bodies["again"] == bodies["filter"], "the request once more gave another answer"
        info["passed"] = want
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
