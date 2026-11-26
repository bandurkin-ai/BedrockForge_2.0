"""The scenes of the jobs of Э19б (подшаг 19.36; the stack, the faults, the systems of veins) on the fixture scene of
`tools/gen/implicit.py` (100 holes through folded layers, the sphere INT, the vein VN):

  * `fault_layers(w, x0)` — three layers of points (the contact, inside, outside) of a vertical fault x = x0: the hand
    data of a fault group (a fault takes codes or layers of strings: `manual` of the model);
  * `veins_world(db_url, tmp_path)` — `World` with a lithology that has a second vein VN2 crossing VN along a line
    (the rock of the fixture and the slab VN2: the holes are sampled again, the contacts are bisected);
  * `make_b(w, name, groups, manual=None, **over)` — a stored model (a coarse step for the speed), `rows_of(w, mid)`
    — what the job wrote, as bytes (the geometry, the three files, the layers of strings) for the comparison of two
    runs;
  * the standard groups: `stack_groups()` (INT intrudes, ALV erodes, SS is cut by the fault), `fault_group(gid)`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.formats.lineset import from_points
from bf_core.lines import store as ls_store
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model.spec import parse_model
from bf_core.wireframes import store as wfs
from tests.test_model_data import read_csv
from tests.test_model_data_load import World
from tests.test_model_job import BOX, groups_of, shas

FAULT_ID = 9
SLAB2 = {"point": (320.0, 200.0, 200.0), "normal": (0.8, 0.0, -0.6), "half": 2.0, "y": (60.0, 340.0)}
CODES = ("ALV", "SS", "SH", "GR", "INT", "VN", "VN2")


def small_patches() -> dict[str, Any]:
    return {"pou": {"n_patch": 300, "n_min": 100, "delta": 0.25}}


# --- the faults -------------------------------------------------------------------------------------------------------


def fault_layers(w: Any, x0: float = 200.0) -> list[int]:
    """Three layers of points of the plane x = x0 over the box: on it (contact), 15 m on each side (inside / outside:
    ∓D_max/2)."""
    ys, zs = np.arange(0.0, 421.0, 35.0), np.arange(120.0, 331.0, 35.0)
    gy, gz = np.meshgrid(ys, zs, indexing="ij")
    out = []
    with dialect.write_tx(w.engine) as conn:
        fid = ls_store.create_file(conn, w.store, name="Разлом", meta={"format": "test"})
        for name, dx in (("контакт", 0.0), ("внутри", -15.0), ("снаружи", 15.0)):
            xyz = np.column_stack([np.full(gy.size, x0 + dx), gy.ravel(), gz.ravel()])
            out.append(ls_store.put_layer(conn, w.store, from_points(name, xyz), file_id=fid))
    return out


def fault_group(gid: int = FAULT_ID, name: str = "Разлом", radius: float | None = None) -> dict[str, Any]:
    return {"id": gid, "name": name, "type": "fault", "codes": [], "params": {"radius": radius}}


def fault_manual(layers: list[int], gid: int = FAULT_ID) -> list[dict[str, Any]]:
    return [{"layer_id": layer, "role": role, "group": gid}
            for layer, role in zip(layers, ("contact", "inside", "outside"), strict=True)]  # fmt: skip


def stack_groups(*, faults: tuple[int, ...] = (FAULT_ID,), cut: tuple[str, ...] = ("SS", "SH")) -> list[dict[str, Any]]:
    """INT intrudes the older groups, ALV erodes them, `cut` are cut by `faults` (the groups of the fixture, the
    youngest first)."""
    g = groups_of("INT", "ALV", "SS", "SH", "GR")
    g[0]["relation"] = "intrusion"
    g[1]["relation"] = "erode"
    for one in g:
        if one["name"] in cut:
            one["faults"] = list(faults)
    return g


# --- the veins --------------------------------------------------------------------------------------------------------


def _rock(x: Any, y: Any, z: Any) -> Any:
    """The rock of the fixture and the slab VN2 (code 6) that crosses the slab VN along the line x = 320, z = 200."""
    from tools.gen import implicit as gen

    code = np.asarray(gen.rock(x, y, z))
    s = SLAB2
    n = np.asarray(s["normal"])
    d = (np.asarray(x) - s["point"][0]) * n[0] + (np.asarray(y) - s["point"][1]) * n[1]
    d = d + (np.asarray(z) - s["point"][2]) * n[2]
    inside = (np.abs(d) <= s["half"]) & (np.asarray(y) > s["y"][0]) & (np.asarray(y) < s["y"][1])
    return np.where(inside, 6, code)


def veins_lithology() -> list[dict[str, str]]:
    """The rows of the lithology of the fixture holes with the rock of `_rock`: the stretches of every code, the
    contacts bisected to a centimetre."""
    from tools.gen import implicit as gen

    collars = read_csv("collars.csv")
    rows: list[dict[str, str]] = []
    for c in collars:
        az, dip = np.radians(float(c["AZIMUTH"])), np.radians(float(c["DIP"]))
        d = np.array([np.sin(az) * np.cos(dip), np.cos(az) * np.cos(dip), np.sin(dip)])
        p0 = np.array([float(c["X"]), float(c["Y"]), float(c["Z"])])
        td = float(c["TD"])

        def at(depth: Any, p0: Any = p0, d: Any = d) -> Any:
            q = p0[None, :] + np.asarray(depth)[:, None] * d[None, :]
            return _rock(q[:, 0], q[:, 1], q[:, 2])

        depth = np.append(np.arange(0.0, td, gen.DEPTH_STEP), td)
        code = at(depth)
        change = np.flatnonzero(code[1:] != code[:-1])
        lo, hi, c_lo = depth[change].copy(), depth[change + 1].copy(), code[change]
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            same = at(mid) == c_lo
            lo, hi = np.where(same, mid, lo), np.where(same, hi, mid)
        cuts = [*np.round(0.5 * (lo + hi), 2).tolist(), td]
        codes = [int(code[0]), *[int(v) for v in code[change + 1]]]
        prev = 0.0
        for b, k in zip(cuts, codes, strict=True):
            if b <= prev:
                continue
            if rows and rows[-1]["HOLE"] == c["HOLE"] and rows[-1]["CODE"] == CODES[k]:
                rows[-1]["TO"] = f"{b:.2f}"
            else:
                rows.append({"HOLE": c["HOLE"], "FROM": f"{prev:.2f}", "TO": f"{b:.2f}", "CODE": CODES[k]})
            prev = b
    return rows


def veins_world(db_url: str, tmp_path: Path) -> World:
    return World(db_url, tmp_path, litho=veins_lithology())


def vein_system_group(gid: int | None = None, name: str = "Жилы", codes: tuple[str, ...] = ("VN", "VN2"),
                      **vein: Any) -> dict[str, Any]:  # fmt: skip
    veins = [{"code": c, "name": c, **vein} for c in codes]
    return {**({} if gid is None else {"id": gid}), "name": name, "type": "vein_system", "codes": list(codes),
            "params": {"veins": veins}}  # fmt: skip


# --- the models and what the job wrote --------------------------------------------------------------------------------


def make_b(w: World, name: str, groups: list[dict[str, Any]], manual: list[dict[str, Any]] | None = None, *,
           h: float = 10.0, table: Any = None, **over: Any) -> dict[str, Any]:  # fmt: skip
    """A stored model of the scene with `groups` and the hand data `manual` (the step `h` is coarse)."""
    spec = w.spec(groups, manual or [], **({"table": str(table)} if table is not None else {}))
    body = {**spec.canonical(), "boundary": {"box": BOX}, "h": h, "name": name, **over}
    with dialect.write_tx(w.engine) as conn:
        return st.create(conn, w.store, w.project, parse_model(body))


def lines_of(w: World, mid: int) -> list[tuple[str, bytes]]:
    """(name, the vertices as bytes) of the layers of strings of the model, by name."""
    from bf_core.lines import store as ls

    with dialect.read_tx(w.engine) as conn:
        meta = sb.stored(conn, mid).meta
        ids = [int(o["line_layer_id"]) for o in meta.get("outputs") or [] if o.get("line_layer_id") is not None]
        got = [(str(conn.execute(sa.select(objects.c.name).where(objects.c.id == i)).scalar_one()),
                ls.read_layer(conn, w.store, i)) for i in ids]  # fmt: skip
    return sorted((n.split(" · ", 1)[1], np.ascontiguousarray(lset.xyz).tobytes()) for n, lset in got)


def rows_of(w: World, mid: int) -> dict[str, Any]:
    """What a run wrote, for the comparison of two runs: the outputs by (kind, group, wing, code) with the bytes of
    their geometry, the three files, the layers of strings."""
    with dialect.read_tx(w.engine) as conn:
        outs = st.outputs_of(conn, sb.stored(conn, mid).meta)
        geo = {}
        for o in outs:
            key = (o["kind"], o["group"], o["wing"], o.get("code"), bool(o.get("common")))
            geo[key] = tuple(a.tobytes() for a in wfs.read_wireframe(conn, w.store, o["wireframe_id"]))
    return {"geometry": geo, "files": shas(w, mid), "lines": lines_of(w, mid)}
