"""Wireframe files: writing and reading DXF, OBJ, STL, Surpac DTM and reading Datamine (ADR 0013 «Замеры»:
«DXF, OBJ, STL: чтение и запись — 1 млн треугольников: ≤ 15 с и ≤ 10 с»; ТЗ 7.21, 9.5, 9.12).

Two wireframes (`tools/gen/wireframes.py`: a pit surface with 80 % of the triangles and a closed ore body; UTM
coordinates, float64 as they are — nothing rounded to millimetres) are written by every writer and read back by
its reader, one wireframe in memory at a time:
  dxf        ASCII DXF, polyface meshes of at most 32 767 vertices (`dxf_write_mesh.py`, `dxf_meshes.py` — the
             reader of lines and meshes in one pass);
  obj        Wavefront OBJ (`obj.py`);
  stl        binary STL: float32, both wireframes become one solid (`stl.py`);
  stl_text   text STL with `repr` coordinates;
  dtm        Surpac DTM with its string file (`dtm_surpac.py`, `str_surpac.py`);
  datamine   a pair of Datamine tables — triangles and points, the wireframes told by the field SURFACE
             (`datamine_objects.py`; no writer: the tables are built by `core/tests/formats/dm_builder.py`).
`<format>_write_s`, `<format>_read_s` — seconds; `<format>_mb` — the size of the file(s).
What is checked — the corners of every triangle, in the order of the triangles, against those written:
  max_error_m   the largest difference of a coordinate over the formats that carry float64 (all but the binary
                STL): 0 — bit for bit;
  stl_error_m   the same for the binary STL: the rounding to float32, at most half its step — 0.25 m on a
                northing of 6 million (the file says so in a warning).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

FORMATS = ("dxf", "obj", "stl", "stl_text", "dtm", "datamine")
EXACT = ("dxf", "obj", "stl_text", "dtm", "datamine")
FILES = {"dxf": "каркасы.dxf", "obj": "каркасы.obj", "stl": "каркасы.stl", "stl_text": "каркасы-текст.stl",
         "dtm": "karkasy.dtm", "datamine": "karkasytr.dm"}  # fmt: skip


def _meshes(work: Path) -> list[Any]:
    import numpy as np

    from bf_core.formats.meshset import MeshSet

    out = []
    for name, color in (("Рельеф карьера", "#8a99a8"), ("Рудное тело", "#e0a030")):
        with np.load(work / f"{name}.npz") as f:
            out.append(MeshSet(name, f["xyz"], f["tri"], color=color))
    return out


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np
    from tools.gen.wireframes import body_of, ore_body, pit_surface

    from tests.formats.dm_builder import F, build_numbers

    n = int(params["triangles"])
    surface = pit_surface(round(0.8 * n))
    meshes = [surface.mesh(), ore_body(round(0.2 * n), **body_of(surface, 0))]
    for name, (v, t) in zip(("Рельеф карьера", "Рудное тело"), meshes, strict=True):
        np.savez(work / f"{name}.npz", xyz=v, tri=t)
    # Datamine: one table of points numbered from 1 and one of triangles with the number of their wireframe
    points = np.concatenate([v for v, _t in meshes])
    base = np.cumsum([0] + [len(v) for v, _t in meshes])
    rows = [np.column_stack([t + 1 + base[k], np.full(len(t), k + 1.0)]) for k, (_v, t) in enumerate(meshes)]
    tri = np.concatenate(rows)
    tr = [F("TRIANGLE"), F("PID1"), F("PID2"), F("PID3"), F("SURFACE")]
    (work / FILES["datamine"]).write_bytes(build_numbers(tr, np.column_stack([np.arange(1, len(tri) + 1), tri])))
    pt = [F("PID"), F("XP"), F("YP"), F("ZP")]
    (work / "karkasypt.dm").write_bytes(build_numbers(pt, np.column_stack([np.arange(1, len(points) + 1), points])))
    load()
    small = work / "малый"  # every kernel of the readers and the writers compiled into the cache: a small copy
    small.mkdir()
    v, t = pit_surface(400).mesh()
    np.savez(small / "Рельеф карьера.npz", xyz=v, tri=t)
    np.savez(small / "Рудное тело.npz", xyz=v + [0.0, 0.0, -50.0], tri=t)
    for fmt in FORMATS[:-1]:
        _cycle(fmt, small, _meshes(small))


def load() -> None:
    import ezdxf  # noqa: F401
    import pyarrow  # noqa: F401

    import bf_core.formats.datamine_objects  # noqa: F401
    import bf_core.formats.dtm_surpac  # noqa: F401
    import bf_core.formats.dxf_meshes  # noqa: F401
    import bf_core.formats.dxf_write_mesh  # noqa: F401
    import bf_core.formats.obj  # noqa: F401
    import bf_core.formats.stl  # noqa: F401
    import bf_core.geo.meshes  # noqa: F401


def _cycle(fmt: str, work: Path, meshes: list[Any]) -> tuple[float, float, float, list[Any]]:
    """Write `meshes` as `fmt` and read the file back: (seconds of writing, of reading, MB, the wireframes read)."""
    import time

    from bf_core.formats.datamine_objects import read_dm_wireframes
    from bf_core.formats.dtm_surpac import read_dtm, write_dtm
    from bf_core.formats.dxf_meshes import read_dxf_meshes
    from bf_core.formats.dxf_write_mesh import write_dxf_meshes
    from bf_core.formats.obj import read_obj, write_obj
    from bf_core.formats.stl import read_stl, write_stl

    path = work / FILES[fmt]
    t0 = time.perf_counter()
    size = 0
    if fmt == "datamine":
        size = path.stat().st_size + path.with_name("karkasypt.dm").stat().st_size
    elif fmt == "stl_text":
        size = write_stl(path, iter(meshes), total=len(meshes), binary=False).size
    else:
        writer = {"dxf": write_dxf_meshes, "obj": write_obj, "stl": write_stl, "dtm": write_dtm}[fmt]
        size = writer(path, iter(meshes), total=len(meshes)).size
    t1 = time.perf_counter()
    if fmt == "datamine":
        read = list(read_dm_wireframes(path, split_by="SURFACE"))
    else:
        reader = {"dxf": read_dxf_meshes, "obj": read_obj, "stl": read_stl, "stl_text": read_stl, "dtm": read_dtm}[fmt]
        read = list(reader(path))
    t2 = time.perf_counter()
    return t1 - t0, t2 - t1, size / 1e6, read


def _corners(meshes: list[Any]) -> Any:
    import numpy as np

    return np.concatenate([m.xyz[m.tri] for m in meshes])


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import numpy as np

    meshes = _meshes(work)
    given = _corners(meshes)
    out: dict[str, Any] = {"triangles": int(len(given)), "wireframes": len(meshes)}
    errors = {}
    for fmt in FORMATS:
        write_s, read_s, mb, read = _cycle(fmt, work, meshes)
        got = _corners(read)
        assert got.shape == given.shape, (fmt, got.shape, given.shape)
        errors[fmt] = float(np.abs(got - given).max())
        if fmt != "datamine":
            out[f"{fmt}_write_s"] = round(write_s, 3)
        out[f"{fmt}_read_s"] = round(read_s, 3)
        out[f"{fmt}_mb"] = round(mb, 1)
        out[f"{fmt}_triangles"] = int(len(got))
        out[f"{fmt}_wireframes"] = len(read)
        del read, got
    out["max_error_m"] = max(errors[fmt] for fmt in EXACT)
    out["stl_error_m"] = errors["stl"]
    assert out["max_error_m"] == 0.0 and 0.0 < out["stl_error_m"] <= 0.25, errors
    return out
