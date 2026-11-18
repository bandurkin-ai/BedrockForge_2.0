"""Import of line files and export of a layer file (ADR 0011 «Замеры»; ТЗ 7.35 Д-13 «SHP и MIF/MID — 1 млн
вершин ≤ 10 с»; ТЗ 7.18).

The same polylines (UTM coordinates in millimetres, 100 vertices a line) as five files, each loaded by the job
`import.objects` into one project database — reading, staging the blobs, the objects, the source file:
  str_s         Micromine STR, 20 layers (the STRING code);
  str_layers_s  Micromine STR where every few lines are a layer of their own (`layers`: 200 / 2000) — small
                layers go into packs (`lines/pack.py`): before them this was 14.9 s for 2000 layers;
  dxf_s         ASCII DXF, 20 layers of 3D polylines;
  shp_s         SHP PolyLineZ with a DBF row a line;
  mif_s         MIF/MID with a row a line (no Z in the format: the plan coordinates are compared);
then `export.lines` writes the layers of the first file as a Micromine STR (`export_s`).
`max_error_m` — the largest difference between a coordinate written to a file and the one read back from the
project, over all five (the text formats carry millimetres, SHP float64: 0).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

PER_LINE = 100
LAYERS = 20
X0, Y0 = 466_570.0, 5_085_061.0
FILES = ("lines.str", "layers.str", "lines.dxf", "lines.shp", "lines.mif")
KEYS = ("str_s", "str_layers_s", "dxf_s", "shp_s", "mif_s")


def _lines(vertices: int) -> Any:
    """(lines, PER_LINE, 3): walks of 1–4 m steps over 20 km × 20 km, rounded to millimetres."""
    import numpy as np

    rng = np.random.default_rng(13)
    lines = vertices // PER_LINE
    start = np.column_stack(
        [X0 + rng.uniform(0, 20_000, lines), Y0 + rng.uniform(0, 20_000, lines), rng.uniform(100, 900, lines)]
    )
    azimuth = rng.uniform(0, 2 * np.pi, (lines, 1)) + rng.normal(0, 0.2, (lines, PER_LINE - 1)).cumsum(axis=1)
    step = rng.uniform(1.0, 4.0, (lines, PER_LINE - 1))
    delta = np.stack([step * np.sin(azimuth), step * np.cos(azimuth), rng.normal(0, 0.2, step.shape)], axis=2)
    xyz = np.concatenate([start[:, None, :], start[:, None, :] + delta.cumsum(axis=1)], axis=1)
    return np.round(xyz, 3)


def _layers(xyz: Any, count: int, prefix: str) -> list[Any]:
    """The lines as `count` layers of consecutive lines (so the order of the file is the order of `xyz`)."""
    import numpy as np

    from bf_core.formats.lineset import LineSet

    out = []
    for k, block in enumerate(np.array_split(xyz, count)):
        n = len(block)
        if not n:
            continue
        offsets = np.arange(0, (n + 1) * PER_LINE, PER_LINE, dtype=np.int64)
        seq = np.arange(n * PER_LINE, dtype=np.uint32)
        out.append(LineSet(f"{prefix}{k:04d}", block.reshape(-1, 3), offsets, np.zeros(n, dtype=np.uint8), seq))
    return out


def _write_shp(path: Path, xyz: Any) -> None:
    """PolyLineZ records and a DBF row a line, byte by byte from the ESRI description (no writer in the core)."""
    import struct

    import numpy as np

    lines = len(xyz)
    records = np.zeros(lines, dtype=[
        ("number", ">i4"), ("length", ">i4"), ("type", "<i4"), ("box", "<f8", 4), ("parts", "<i4"), ("points", "<i4"),
        ("start", "<i4"), ("xy", "<f8", (PER_LINE, 2)), ("zbox", "<f8", 2), ("z", "<f8", PER_LINE),
        ("mbox", "<f8", 2), ("m", "<f8", PER_LINE),
    ])  # fmt: skip
    records["number"], records["length"] = np.arange(1, lines + 1), (records.itemsize - 8) // 2
    records["type"], records["parts"], records["points"] = 13, 1, PER_LINE
    records["xy"], records["z"], records["m"] = xyz[:, :, :2], xyz[:, :, 2], -1e39
    lo, hi = xyz.reshape(-1, 3).min(axis=0), xyz.reshape(-1, 3).max(axis=0)
    words = (100 + records.nbytes) // 2
    head = struct.pack(">7i", 9994, 0, 0, 0, 0, 0, words)
    head += struct.pack("<2i8d", 1000, 13, lo[0], lo[1], hi[0], hi[1], lo[2], hi[2], 0.0, 0.0)
    path.write_bytes(head + records.tobytes())
    table = np.zeros(lines, dtype=[("del", "S1"), ("ID", "S8")])
    table["del"], table["ID"] = b" ", np.char.rjust(np.char.mod(b"%d", np.arange(lines)), 8)
    dbf = struct.pack("<4BIHH20x", 3, 126, 11, 18, lines, 32 + 32 + 1, table.itemsize)
    dbf += struct.pack("<11sc4xBB14x", b"ID", b"N", 8, 0) + b"\r"
    path.with_suffix(".dbf").write_bytes(dbf + table.tobytes() + b"\x1a")


def _write_mif(path: Path, xyz: Any) -> None:
    import numpy as np

    lines = len(xyz)
    x, y = (np.char.mod("%.3f", xyz[:, :, c].ravel()) for c in (0, 1))
    rows = np.char.add(np.char.add(x, " "), y).reshape(lines, PER_LINE).tolist()
    head = 'Version 300\nCharset "WindowsCyrillic"\nDelimiter ","\nColumns 2\n  NAME Char(12)\n  ID Integer\nData\n\n'
    body = "".join(f"Pline {PER_LINE}\n" + "\n".join(row) + "\n    Pen (1,2,0)\n" for row in rows)
    path.write_text(head + body, encoding="cp1251", newline="\n")
    mid = "".join(f'"L-{i}",{i}\n' for i in range(lines))
    path.with_suffix(".mid").write_text(mid, encoding="cp1251", newline="\n")


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.formats.dxf_write import write_dxf
    from bf_core.formats.str_micromine import write_str
    from bf_core.projects.project import create_folder

    xyz = _lines(int(params["vertices"]))
    np.save(work / "xyz.npy", xyz)
    write_str(work / "lines.str", _layers(xyz, LAYERS, "H"))
    write_str(work / "layers.str", _layers(xyz, int(params["layers"]), "S"))
    write_dxf(work / "lines.dxf", _layers(xyz, LAYERS, "L"))
    _write_shp(work / "lines.shp", xyz)
    _write_mif(work / "lines.mif", xyz)
    folder = create_folder(work, "Линии", {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    load()  # compile the kernels of the readers into the cache outside the timing


WARM_LINES = 20  # a DXF above 64 KB: smaller files are scanned without the compiled kernel


def _warm(folder: Path) -> list[Path]:
    """The four formats with a few lines each: reading them loads (the first time — compiles) the kernels."""
    from bf_core.formats.dxf_write import write_dxf
    from bf_core.formats.str_micromine import write_str

    xyz = _lines(WARM_LINES * PER_LINE)
    write_str(folder / "lines.str", _layers(xyz, 1, "H"))
    write_dxf(folder / "lines.dxf", _layers(xyz, 1, "L"))
    _write_shp(folder / "lines.shp", xyz)
    _write_mif(folder / "lines.mif", xyz)
    return [folder / name for name in FILES if name != "layers.str"]


def _job(job_id: int, folder: Path, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    from bf_core.jobs.context import JobContext
    from bf_core.jobs.registry import load_handlers

    job = {"id": job_id, "params": params, "project_folder": str(folder)}
    ctx = JobContext(job, lambda _message: None, threading.Event())
    try:
        out = load_handlers()[kind].run(ctx)
    finally:
        ctx.close()
    assert out is not None
    return dict(out)


def load() -> None:
    import tempfile

    import pyarrow.parquet  # noqa: F401

    import bf_core.importing.objects  # noqa: F401
    import bf_core.lines.export  # noqa: F401
    import bf_core.lines.store  # noqa: F401
    from bf_core.formats.registry import read_objects

    with tempfile.TemporaryDirectory(prefix="bf-bench-warm-") as tmp:
        for path in _warm(Path(tmp)):
            assert read_objects(path).layers[0].point_count == WARM_LINES * PER_LINE, path


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import blobs
    from bf_core.lines import store as ls_store
    from bf_core.storage.blobs import BlobStore

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    xyz = np.load(work / "xyz.npy").reshape(-1, 3)
    stamp = time.time_ns() // 1_000_000 % 1_000_000_000  # a repeated run is a new job
    out: dict[str, Any] = {"vertices": len(xyz), "layers": int(params["layers"])}
    files: dict[str, int] = {}
    for k, (name, key) in enumerate(zip(FILES, KEYS, strict=True)):
        t0 = time.perf_counter()
        result = _job(stamp + k, folder, "import.objects", {"files": [str(work / name)]})
        out[key] = round(time.perf_counter() - t0, 3)
        assert result["vertices"] == len(xyz) and not result["failed"], (name, result)
        files[name] = int(result["objects"][0])
    t0 = time.perf_counter()
    target = work / f"export-{stamp}.str"
    params = {"path": str(target), "format": "str", "file_id": files[FILES[0]]}
    written = _job(stamp + 9, folder, "export.lines", params)
    out["export_s"] = round(time.perf_counter() - t0, 3)
    assert written["points"] == len(xyz)
    out["export_mb"] = round(target.stat().st_size / 2**20, 1)
    target.unlink()

    # what the project holds against what the files were written from
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    store = BlobStore(folder)
    error = 0.0
    try:
        with dialect.read_tx(engine) as conn:
            for name, file_id in files.items():
                ids = ls_store.layer_ids(conn, file_id)
                parts = [ls_store.layer_files(store, ls_store.layer_blobs(conn, i)).array("xyz") for i in ids]
                got = np.concatenate(parts).reshape(-1, 3)  # type: ignore[arg-type]
                columns = slice(0, 2) if name.endswith(".mif") else slice(0, 3)
                error = max(error, float(np.abs(got[:, columns] - xyz[:, columns]).max()))
                if name == "layers.str":
                    out["layer_objects"] = len(ids)
            out["blobs"] = int(conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar_one())
    finally:
        engine.dispose()
    out["max_error_m"] = error
    return out
