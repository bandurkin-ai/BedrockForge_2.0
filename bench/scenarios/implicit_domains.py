"""The domains of lithology: the job `implicit.build` of five layers (ADR 0026 «Замеры»; ТЗ 8.9.4: 5 доменов × 50 тыс.
точек ≤ 5 мин на 4 ядрах; подшаги 19.12, 19.13).

A project of `implicit_common` (the set, «Литология» of five layers on folded interfaces: `holes` holes) and a model of
the five domains L1 … L5 in the box of the grid, the step `h` (not given — diagonal / 200 of 8.9.4), the defaults of
8.9.4 for everything else (linear kernel, N_уч = 1000, «Вручную» with s = 0); then `model.job.run_build` — the body
of the job: the data, the patches, the isosurfaces, the bodies, the checks, the files and the wireframes in one
transaction.
  job_s       the whole job (the time of ТЗ);  data_s, patches_s, iso_s, bodies_s, check_s, write_s — its stages
              (`stages` of the answer: the seconds from the beginning of the stage to the next one)
  points      the data points of the model (all the groups);  by_group — of every domain
  patches     the patches solved;  triangles — of the outputs;  balance — Σ volumes of the bodies against the box
What is checked (a failure stops the run): the job ends `ready` with a surface and a body for every domain boundary;
the volumes of the bodies add up to the volume of the boundary to `BALANCE` of it (Т8.9-5: the domains are the
differences of the same shells); the data are reproduced (`max_residual` ≤ `RESIDUAL`·max|f|)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import blocks_common as bc
from . import implicit_common as ic

BALANCE = 1e-9
RESIDUAL = 1e-6  # of the units of f (metres); the job's own sample of the data


def prepare(params: dict[str, Any], work: Path) -> None:
    sc = ic.Scene.of(params)
    meta = ic.write_project(work, sc, assays=False)
    ctx = bc.Ctx.open(work)
    try:
        over: dict[str, Any] = {}
        if params.get("n_patch"):
            over["pou"] = {"n_patch": int(params["n_patch"]), "n_min": int(params.get("n_min", 100))}
        mid = ic.create_model(ctx, meta, "Домены", ic.domains(meta), h=params.get("h"), **over)
    finally:
        ctx.close()
    meta["model"] = mid
    ic.keep(work, meta)


def load() -> None:
    from bf_core.model.warm import warm

    warm()


def stages_of(answer: dict[str, Any]) -> dict[str, float]:
    """The seconds of the stages by short names (the ones the job names: «Данные», «Участки», …)."""
    names = {"Данные": "data_s", "Участки": "patches_s", "Изоповерхности": "iso_s", "Тела": "bodies_s",
             "Проверки": "check_s", "Запись": "write_s"}  # fmt: skip
    return {names[k]: round(float(v), 3) for k, v in answer["stages"].items() if k in names}


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    threads = ic.limit_threads(params)
    meta = ic.read_meta(work)
    ctx = bc.Ctx.open(work)
    try:
        seconds, out = ic.seconds_of(lambda: ic.run_job(ctx, int(meta["model"]), threads=threads))
    finally:
        ctx.close()
    box = meta["box"]
    volume = (box[3] - box[0]) * (box[4] - box[1]) * (box[5] - box[2])
    bodies = [o for o in out["outputs"] if o["kind"] == "body"]
    balance = abs(sum(o["volume"] for o in bodies) - volume) / volume
    s = out["stats"]
    assert out["state"] == "ready" and out["mode"] == "build", out["state"]
    groups = {o["group"] for o in out["outputs"] if o["group"] is not None}
    assert groups == set(range(1, len(ic.domains(meta)) + 1)), f"the outputs are of the groups {sorted(groups)}"
    assert all(o["triangles"] > 0 and o["volume"] > 0 for o in bodies), "an empty body"
    assert balance <= BALANCE, f"the volumes of the bodies are off the volume of the boundary by {balance:.2e}"
    assert s["max_residual"] is not None and s["max_residual"] <= RESIDUAL, f"residual {s['max_residual']}"
    info: dict[str, Any] = {"threads": threads, "holes": meta["holes"], "job_s": round(seconds, 3),
                            "points": s["points"], "patches": s["patches"],
                            "triangles": sum(o["triangles"] for o in out["outputs"]), "bodies": len(bodies),
                            "balance": balance, "max_residual": s["max_residual"], "cond_max": s["cond_max"],
                            "by_group": ", ".join(str(g["points"]) for g in s["by_group"]),
                            "process_mb": ic.peak_mb(), "us_point": round(1e6 * seconds / max(s["points"], 1), 1)}
    info.update(stages_of(out))
    return info
