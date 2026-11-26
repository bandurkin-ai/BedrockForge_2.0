"""The stack of relations, a fault and an intrusion (ADR 0026 «Замеры»: `implicit_stack` — ориентир ≤ 2 ×
`implicit_domains` на точку, вне ТЗ; ТЗ Д-24, Д-31, Т8.9-8, Т8.9-13; подшаг 19.36).

A project of `implicit_common` (the set, the table «Литология» of five layers on folded interfaces) with the hand data
of two more things — a vertical fault through the middle of the grid and a sphere — and two models of the same five
groups: «Основа» (the old data) and «Полная» (the new table of the campaign):
  INT (the sphere, hand data: points on it, in it and around it) — «Интрузия»: it replaces the older groups;
  L1 «Эрозия», L3 «Налегание» (the stack of relations), L2 and L3 are cut by the fault (the wings of their fields);
  L4 conformable; the fault — a group of its own whose data are three layers of points (on the plane, 15 m each side).
  build_s     the full build of the old data (the first run of the process: a warm one) — the scene of the target;
  update_s    «Обновить» after the campaign (`share` of the holes: their contacts moved by 3 m, the table «Кампания»);
  full_s      the full build of the new data (the model «Полная»), the same code, the same process;
  share_s     update_s / full_s (the target of 8.9.4, a model without a fault: ≤ 0,25);  vs_build — update_s / build_s
  us_point    microseconds of the full build per data point — to be put beside the one of `implicit_domains`
What is checked (a failure stops the run): the job ends `ready`; the stack did drop points (erosion, onlap and
intrusion are in `stats.stack`); a fault gives the wings (two bodies of the fault and of every cut group, the space of
the wings and the rock add up to the boundary to `BALANCE`); the data are reproduced (`max_residual` ≤ `RESIDUAL`); the
update is the full build of the new data bit for bit — the vertices and the triangles of every output and the sha256 of
the three files (Т8.9-8, Т8.9-13) — and it took patches (0 < taken < all); the same bytes in the next run of `--repeat`
(every run starts from the old table again)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import implicit_common as ic

BALANCE = 1e-9
RESIDUAL = 1e-6
INT_ID, FAULT_ID = 6, 7
CUT = (2, 3)  # the layers the fault cuts
RELATIONS = {1: "erode", 3: "onlap"}  # the relation of the younger group of the stack (L1 erodes, L3 onlaps on L4)
GAP = 15.0  # the points beside the fault and the sphere: ∓D_max / 2


def _sphere(centre: np.ndarray, radius: float, count: int) -> np.ndarray:
    """`count` points of a sphere (the golden spiral): deterministic, evenly spread."""
    k = np.arange(count) + 0.5
    z = 1.0 - 2.0 * k / count
    ring = np.sqrt(1.0 - z * z)
    phi = np.pi * (3.0 - np.sqrt(5.0)) * k
    return centre + radius * np.column_stack([ring * np.cos(phi), ring * np.sin(phi), z])


def hand_data(meta: dict[str, Any]) -> list[tuple[str, int, str, np.ndarray]]:
    """(name, group, role, points) of the hand data: the three layers of the fault plane x = the middle of the box and
    the three of the sphere INT in the middle of it."""
    lo, hi = np.asarray(meta["box"][:3]), np.asarray(meta["box"][3:])
    mid = 0.5 * (lo + hi)
    ys = np.linspace(lo[1] + 5.0, hi[1] - 5.0, max(8, int((hi[1] - lo[1]) / 30.0)))
    zs = np.linspace(lo[2] + 5.0, hi[2] - 5.0, max(5, int((hi[2] - lo[2]) / 30.0)))
    gy, gz = np.meshgrid(ys, zs, indexing="ij")
    out: list[tuple[str, int, str, np.ndarray]] = []
    for name, role, dx in (("Разлом: контакт", "contact", 0.0), ("Разлом: слева", "inside", -GAP),
                           ("Разлом: справа", "outside", GAP)):  # fmt: skip
        xyz = np.column_stack([np.full(gy.size, mid[0] + dx), gy.ravel(), gz.ravel()])
        out.append((name, FAULT_ID, role, xyz))
    radius = min(0.15 * (hi[0] - lo[0]), 100.0)
    centre = np.array([mid[0] + 0.25 * (hi[0] - lo[0]), mid[1], lo[2] + 0.55 * (hi[2] - lo[2])])
    rng = np.random.default_rng(7)
    inner = rng.normal(size=(300, 3))
    inner = centre + 0.6 * radius * inner / np.linalg.norm(inner, axis=1)[:, None] * rng.uniform(0.2, 1.0, (300, 1))
    out.append(("INT: контакт", INT_ID, "contact", _sphere(centre, radius, 800)))
    out.append(("INT: внутри", INT_ID, "inside", inner))
    out.append(("INT: снаружи", INT_ID, "outside", _sphere(centre, 1.45 * radius, 800)))
    return out


def write_hand_data(ctx: Any) -> list[dict[str, Any]]:
    """The layers of `hand_data` in a file of the project → `manual` of the model."""
    from bf_core.db import dialect
    from bf_core.formats.lineset import from_points
    from bf_core.lines import store as ls

    manual = []
    with dialect.write_tx(ctx.engine) as conn:
        file_id = ls.create_file(conn, ctx.store, name="Разлом и интрузия", meta={"format": "bench"})
        for name, gid, role, xyz in ctx.hand:
            layer = ls.put_layer(conn, ctx.store, from_points(name, xyz), file_id=file_id)
            manual.append({"layer_id": layer, "role": role, "group": gid})
    return manual


def groups(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """The youngest first: the sphere, the layers L1 … L4, the fault last."""
    layers = ic.domains(meta, 4)
    for g in layers:
        if g["id"] in RELATIONS:
            g["relation"] = RELATIONS[g["id"]]
        if g["id"] in CUT:
            g["faults"] = [FAULT_ID]
    sphere = {"id": INT_ID, "name": "INT", "type": "domain", "codes": [], "relation": "intrusion"}
    fault = {"id": FAULT_ID, "name": "Разлом", "type": "fault", "codes": [], "params": {"radius": None}}
    return [sphere, *layers, fault]


def prepare(params: dict[str, Any], work: Path) -> None:
    sc = ic.Scene.of(params)
    meta = ic.write_project(work, sc, assays=False)
    ctx = bc.Ctx.open(work)
    try:
        names = np.array([f"H{i + 1:05d}" for i in range(sc.n_holes)], dtype=object)
        edited = ic.edit_litho(sc, float(params.get("share", 0.05)))
        meta["edited_table"] = ic.write_litho(ctx, meta["set_id"], names, edited, "Кампания")
        meta["edited_holes"] = int(np.unique(edited["hole"][(edited["to"] != sc.litho()["to"])]).size)
        ctx.hand = hand_data(meta)
        manual = write_hand_data(ctx)
        over: dict[str, Any] = {"manual": manual}
        if params.get("n_patch"):
            over["pou"] = {"n_patch": int(params["n_patch"]), "n_min": int(params.get("n_min", 100))}
        h = params.get("h")
        meta["base"] = ic.create_model(ctx, meta, "Основа", groups(meta), h=h, **over)
        meta["full"] = ic.create_model(ctx, meta, "Полная", groups(meta), h=h, table=meta["edited_table"], **over)
    finally:
        ctx.close()
    ic.keep(work, meta)


def load() -> None:
    from bf_core.model.warm import warm

    warm()


def stages_of(answer: dict[str, Any]) -> dict[str, float]:
    from .implicit_domains import stages_of as of

    return {k: v for k, v in of(answer).items() if k in ("data_s", "patches_s", "iso_s", "bodies_s")}


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    threads = ic.limit_threads(params)
    meta = ic.read_meta(work)
    ctx = bc.Ctx.open(work)
    try:
        base, full = int(meta["base"]), int(meta["full"])
        ic.point_at(ctx, base, meta, int(meta["litho"]))  # a repeat starts from the old data again
        build_s, first = ic.seconds_of(lambda: ic.run_job(ctx, base, threads=threads))
        old = ic.fingerprint(ctx, base)
        ic.point_at(ctx, base, meta, int(meta["edited_table"]))
        update_s, upd = ic.seconds_of(lambda: ic.run_job(ctx, base, update=True, threads=threads))
        full_s, ref = ic.seconds_of(lambda: ic.run_job(ctx, full, threads=threads))
        new = ic.fingerprint(ctx, base)
        same = new == ic.fingerprint(ctx, full)
    finally:
        ctx.close()
    s = first["stats"]
    assert first["state"] == "ready" and first["mode"] == "build" and upd["mode"] == "update"
    assert same, "the update is not the full build of the new data (the outputs or the files differ)"
    assert new != old, "the campaign changed nothing"
    stack = s["stack"]
    assert stack["dropped_total"] > 0 and {d["relation"] for d in stack["dropped"]} >= {"erode", "intrusion"}, stack
    by = {(o["kind"], o["group"], o["wing"]): o for o in first["outputs"]}
    for gid in (FAULT_ID, *CUT):
        assert ("body", gid, 0) in by and ("body", gid, 1) in by, f"group {gid} has no wings"
    rock = sum(o["volume"] for o in first["outputs"] if o["kind"] == "body" and not o.get("region"))
    box = meta["box"]
    volume = (box[3] - box[0]) * (box[4] - box[1]) * (box[5] - box[2])
    balance = abs(rock - volume) / volume
    assert balance <= BALANCE, f"the bodies are off the volume of the boundary by {balance:.2e}"
    assert s["max_residual"] is not None and s["max_residual"] <= RESIDUAL, f"residual {s['max_residual']}"
    total = int(ref["stats"]["patches"])
    taken = upd["reused"]
    assert upd["stats"]["patches"] == total and 0 < taken["patches"] < total, f"taken {taken['patches']}/{total}"
    offsets = [g["offset"]["mean"] for g in s["by_group"] if g.get("offset")]
    info: dict[str, Any] = {
        "threads": threads, "holes": meta["holes"], "edited": meta["edited_holes"], "points": s["points"],
        "patches": total, "build_s": round(build_s, 3), "update_s": round(update_s, 3), "full_s": round(full_s, 3),
        "share_s": round(update_s / full_s, 4), "vs_build": round(update_s / build_s, 4),
        "us_point": round(1e6 * build_s / max(s["points"], 1), 1), "dropped": stack["dropped_total"],
        "triangles": sum(o["triangles"] for o in first["outputs"]), "outputs": len(first["outputs"]),
        "balance": balance, "max_residual": s["max_residual"], "offset_mean": round(max(offsets), 3),
        "reused_patches": int(taken["patches"]), "reused_bricks": int(taken["bricks"]),
        "patch_share": round(taken["patches"] / total, 4), "bits": int(same),
        "by_group": ", ".join(f"{g['group']}:{g['wing']}:{g['points']}" for g in s["by_group"]),
        "process_mb": ic.peak_mb()}  # fmt: skip
    info.update({f"build_{k}": v for k, v in stages_of(first).items()})
    info.update({f"upd_{k}": v for k, v in stages_of(upd).items()})
    info.update({f"full_{k}": v for k, v in stages_of(ref).items()})
    return info

