"""«Обновить» after an edit of a part of the data (ADR 0026 «Замеры», «Варианты» → «Обновить»; ТЗ 8.9.3, 8.9.4:
обновление после изменения ≈ 5 % данных ≤ 25 % полного построения; Т8.9-8; подшаги 19.12).

A project of `implicit_common` and two models of its five domains: «Основа» and «Полная». The campaign: the contacts of
`share` of the holes (the nearest to the corner of the grid: the new holes of a campaign are neighbours; `scatter` —
spread over the grid, the worst case for a cache of patches) are moved by 3 m, as a new table «Литология».
  build_s     the full build of the old data (the first run of the process: a warm one, the kernels are compiled)
  update_s    «Обновить»: the model reads the new table, `run_build(update=True)` takes from the cache what has not
              changed;  full_s — the full build of the new data (the model «Полная»), the same code, the same process
  share_s     update_s / full_s: the figure of the target (≤ 0,25);  vs_build — update_s / build_s
  reused_*    the patches and the bricks the update took (of the total); edited — the holes changed
What is checked (a failure stops the run): the update is the full build of the new data bit for bit — the vertices and
the triangles of every output and the sha256 of the three files of the model (Т8.9-8); it solved the patches that hold
the changed points and took the rest; it took bricks. The target `SHARE` is a limit in `budgets.json` (`share_s`), not
an assert: the figure is the measurement of the step."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import implicit_common as ic

SHARE = 0.25  # ТЗ 8.9.4


def prepare(params: dict[str, Any], work: Path) -> None:
    sc = ic.Scene.of(params)
    meta = ic.write_project(work, sc, assays=False)
    ctx = bc.Ctx.open(work)
    try:
        names = [f"H{i + 1:05d}" for i in range(sc.n_holes)]
        edited = ic.edit_litho(sc, float(params.get("share", 0.05)), scatter=bool(params.get("scatter", False)))
        meta["edited_table"] = ic.write_litho(ctx, meta["set_id"], np.array(names, dtype=object), edited, "Кампания")
        meta["edited_holes"] = int(np.unique(edited["hole"][(edited["to"] != sc.litho()["to"])]).size)
        over: dict[str, Any] = {}
        if params.get("n_patch"):
            over["pou"] = {"n_patch": int(params["n_patch"]), "n_min": int(params.get("n_min", 100))}
        h = params.get("h")
        meta["base"] = ic.create_model(ctx, meta, "Основа", ic.domains(meta), h=h, **over)
        meta["full"] = ic.create_model(ctx, meta, "Полная", ic.domains(meta), h=h, table=meta["edited_table"], **over)
    finally:
        ctx.close()
    ic.keep(work, meta)


def load() -> None:
    from bf_core.model.warm import warm

    warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    threads = ic.limit_threads(params)
    meta = ic.read_meta(work)
    ctx = bc.Ctx.open(work)
    try:
        base, full = int(meta["base"]), int(meta["full"])
        ic.point_at(ctx, base, meta, int(meta["litho"]))  # a repeat starts from the old data again
        build_s, first = ic.seconds_of(lambda: ic.run_job(ctx, base, threads=threads))
        ic.point_at(ctx, base, meta, int(meta["edited_table"]))
        update_s, upd = ic.seconds_of(lambda: ic.run_job(ctx, base, update=True, threads=threads))
        full_s, ref = ic.seconds_of(lambda: ic.run_job(ctx, full, threads=threads))
        same = ic.fingerprint(ctx, base) == ic.fingerprint(ctx, full)
    finally:
        ctx.close()
    total = int(ref["stats"]["patches"])
    taken = upd["reused"]
    assert upd["mode"] == "update" and ref["mode"] == "build" and first["mode"] == "build"
    assert same, "the update is not the full build of the same data (the outputs or the files differ)"
    assert upd["stats"]["patches"] == total and 0 < taken["patches"] < total, f"taken {taken['patches']}/{total}"
    assert taken["bricks"] > 0, "the update took no brick"
    info: dict[str, Any] = {"threads": threads, "holes": meta["holes"], "edited": meta["edited_holes"],
                            "points": ref["stats"]["points"], "patches": total, "build_s": round(build_s, 3),
                            "update_s": round(update_s, 3), "full_s": round(full_s, 3),
                            "share_s": round(update_s / full_s, 4), "vs_build": round(update_s / build_s, 4),
                            "reused_patches": int(taken["patches"]), "reused_bricks": int(taken["bricks"]),
                            "patch_share": round(taken["patches"] / total, 4), "bits": int(same),
                            "process_mb": ic.peak_mb()}  # fmt: skip
    info.update({f"upd_{k}": v for k, v in _stages(upd).items()})
    info.update({f"full_{k}": v for k, v in _stages(ref).items()})
    return info


def _stages(answer: dict[str, Any]) -> dict[str, float]:
    from .implicit_domains import stages_of

    return {k: v for k, v in stages_of(answer).items() if k in ("data_s", "patches_s", "iso_s", "bodies_s")}
