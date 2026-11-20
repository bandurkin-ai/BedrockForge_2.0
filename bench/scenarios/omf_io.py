"""OMF v1 in and out (ADR 0017 «Замеры»; ТЗ 5.3, 7.24): «Импорт OMF, объём — 1 млн × 5 полей / 20 млн × 20: ci ≤ 10 с,
≤ 600 МБ; full ≤ 8 мин, пик ≤ 2 ГБ»; «Экспорт OMF, объём — ci ≤ 10 с; full ≤ 4 мин, ≤ 1 ГБ»; «Импорт / экспорт OMF,
поверхность — 1 млн треугольников: ≤ 6 с, ≤ 600 МБ / ≤ 3 с»; «Состав OMF для мастера — файл 2 ГБ ≤ 1 с».

Files, made by `prepare` with the writer of BF2 (`formats/omf_write.OmfWriter`), outside the timing:
  блоки.omf        a volume of `grid` cells (+X, +Y, +Z, regular steps, a UTM corner in float64) and `fields`
                   fields: numbers (ScalarData, NaN — empty) and the last `categories` of them categories
                    (MappedData, 7 Russian labels, −1 — empty). The values are a hash of the OMF number of the
                   cell, lognormal: the odd numbers rounded to 0.001 (assays), the even ones with every bit of
                   their mantissa (estimates — zlib can hardly squeeze them); ≈ 8 % (numbers) and
                   ≈ 4 % (categories) empty in a field, ≈ 10 % of the cells empty in every field (air over a
                   stepped top) — those are not loaded;
  оси.omf          the hard case (`axes_grid`, the counts along u, v, w): u along +Y, v against X, w along +Z, the
                   steps along Z not all equal (tensors) — read through temporary files and mmap;
  поверхность.omf  a pit surface of `triangles` triangles (`tools/gen/wireframes.pit_surface`), float64 as is.
Every run loads them into a new project (removed at the end: the runs of `--repeat` do not see each other).
Stages, seconds:
  compose_s         the composition of блоки.omf for the wizard (`omf_compose.describe`: the JSON only);
  import_s          the job `import.objects` of блоки.omf: reading, Parquet zstd `cells`, statistics, the source
                    file kept in the project;
  export_s          that model back into OMF by the writer of «Экспорт OMF» and «Объекты в файлы»
                    (`exporting.omf_job.write_objects`; `export_by` — that, or the writer itself until it is there);
  axes_import_s     the job for оси.omf;
  surface_import_s, surface_export_s   the job for поверхность.omf; that wireframe back into OMF.
Memory, MB: `import_mb` — the peak of the process up to the end of the import (it is the first stage: VmHWM on
Linux, else sampled); `export_mb`, `axes_mb`, `surface_mb` — the largest resident memory while that stage ran
(sampled every 2 ms). `omf_mb`, `cells_mb`, `export_omf_mb` — the sizes of блоки.omf, of the Parquet of its model,
of the file exported.
What is checked (a failure stops the run): the cells of both models read back from the project and the cells of
the exported file read back by `omf_volume.read_volume`, each against the values computed again from the OMF
number of the cell — numbers bit for bit, NaN where empty, the same codes; the occupied cells exactly those with a
value (`mismatches` — cells that differ, 0); the grids (counts, corner, steps); the corners of every triangle of
the wireframe in the project and of the exported surface, in the order of the file, bit for bit (`max_error_m` 0).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

X0, Y0, Z0 = 512_345.5, 6_234_567.25, -412.75
SIZE = (10.0, 10.0, 5.0)
NUMBERS = ("Au", "Ag", "Cu", "Pb", "Zn", "S", "Fe", "As", "Плотность", "Влажность", "Au ок", "Cu ок", "Расстояние",
           "Проходы", "Дисперсия", "Мощность", "Отметка", "Объём", "Запасы", "Металл")  # fmt: skip
CATEGORIES = ("Порода", "Домен", "Класс", "Зона")
LABELS = ("Руда", "Порода", "Окисленная", "Сульфидная", "Дайка", "Разлом", "Пустая")
UNITS = {"Au": "г/т", "Ag": "г/т", "Cu": "%", "Плотность": "т/м³"}
PORTION = 4_000_000  # cells a portion of the values written in `prepare` (as `omf_write.VOLUME_PORTION`)


# --- the values ---------------------------------------------------------------------------------------------


def field_specs(fields: int, categories: int) -> list[dict[str, Any]]:
    """`meta.fields` of the model: the numbers, then the categories."""
    numbers = fields - categories
    out: list[dict[str, Any]] = [
        {"name": NUMBERS[k] if k < len(NUMBERS) else f"Поле {k + 1}", "kind": "number",
         "unit": UNITS.get(NUMBERS[k]) if k < len(NUMBERS) else None}
        for k in range(numbers)
    ]  # fmt: skip
    out += [{"name": CATEGORIES[k], "kind": "category", "labels": list(LABELS)} for k in range(categories)]
    return out


def _mix(x: Any) -> Any:
    """splitmix64 of uint64 numbers (wrapping)."""
    import numpy as np

    x = x + np.uint64(0x9E3779B97F4A7C15)
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def _air(o: Any, counts: list[int]) -> Any:
    """Cells over a stepped top (u, v, w of the OMF numbers `o`): empty in every field."""
    nu, nv, nw = counts
    iu, iv, iw = o % nu, (o // nu) % nv, o // (nu * nv)
    return iw > nw - 1 - ((iu + 2 * iv) % max(1, nw // 5))


def values(o: Any, k: int, kind: str, counts: list[int]) -> Any:
    """The values of field k at the OMF numbers `o` of a volume of `counts` (u, v, w)."""
    import numpy as np

    h = _mix(np.asarray(o).astype(np.uint64) * np.uint64(64) + np.uint64(k))
    low = h & np.uint64(255)
    air = _air(np.asarray(o, dtype=np.int64), counts)
    if kind == "number":
        u = (h >> np.uint64(11)).astype(np.float64) * 2.0**-53
        v = np.exp(4.0 * u - 2.0) * (1.0 + k)
        if k % 2:  # an assay to 0.001; an even field is an estimate with every bit of its mantissa
            v = np.round(v, 3)
        v[(low < 20) | air] = np.nan
        return v
    c = (h >> np.uint64(32)) % np.uint64(len(LABELS))
    out = c.astype(np.int32)
    out[(low < 10) | air] = -1
    return out


def omf_of(idx: Any, layout: dict[str, Any]) -> Any:
    """OMF numbers of the BF2 cells `idx` (idx = x + nx·(y + ny·z)): the same, or for оси.omf (u = +Y, v = −X)
    iu = y, iv = nv − 1 − x, iw = z."""
    import numpy as np

    idx = np.asarray(idx, dtype=np.int64)
    if not layout["axes"]:
        return idx
    nu, nv, _nw = layout["counts"]
    nx, ny = nv, nu
    x, y, z = idx % nx, (idx // nx) % ny, idx // (nx * ny)
    return y + nu * ((nv - 1 - x) + nv * z)


def mismatches(idx: Any, got: dict[str, Any], layout: dict[str, Any]) -> int:
    """Cells of a portion whose values are not the ones of their OMF number, or that have no value at all."""
    import numpy as np

    o = omf_of(idx, layout)
    bad = np.zeros(len(o), dtype=bool)
    some = np.zeros(len(o), dtype=bool)
    for k, f in enumerate(layout["fields"]):
        want = values(o, k, f["kind"], layout["counts"])
        have = np.asarray(got[f["name"]])
        if f["kind"] == "number":
            empty = np.isnan(want)
            bad |= empty != np.isnan(have)
            bad |= ~empty & (np.where(empty, 0.0, want).view(np.uint64) != np.where(empty, 0.0, have).view(np.uint64))
        else:
            empty = want < 0
            bad |= want != have
        some |= ~empty
    return int((bad | ~some).sum())


def write_volume(path: Path, name: str, fields: list[dict[str, Any]], counts: list[int], tensors: list[list[float]],
                 corner: list[float]) -> int:  # fmt: skip
    """An OMF file of one volume with axes +X, +Y, +Z (counts along u, v, w); returns the occupied cells."""
    import numpy as np

    from bf_core.formats.omf_write import OmfWriter

    n = counts[0] * counts[1] * counts[2]
    some = np.zeros(n, dtype=bool)

    def cells(k: int) -> Any:
        for s in range(0, n, PORTION):
            o = np.arange(s, min(n, s + PORTION), dtype=np.int64)
            v = values(o, k, fields[k]["kind"], counts)
            some[s : s + len(o)] |= np.isfinite(v) if fields[k]["kind"] == "number" else v >= 0
            yield o, v

    grid = {"corner": corner, "counts": counts, "tensors": dict(zip("xyz", tensors, strict=True))}
    by_name = {f["name"]: k for k, f in enumerate(fields)}
    with OmfWriter(path, "Замер OMF", "Синтетическая модель для замеров") as w:
        w.add_volume(name, grid, fields, lambda field: cells(by_name[field]), color="#d08a3c")
    return int(some.sum())


def _patch_axes(path: Path, origin: list[float]) -> None:
    """The volume of the file turned: u along +Y, v against X, w along +Z, with this origin (the JSON in place)."""
    import struct

    from bf_core.formats.omf_file import HEADER_SIZE

    with open(path, "r+b") as fh:
        fh.seek(HEADER_SIZE - 8)
        (start,) = struct.unpack("<Q", fh.read(8))
        fh.seek(start)
        doc = json.loads(fh.read().decode("utf-8"))
        for obj in doc.values():
            if isinstance(obj, dict) and obj.get("__class__") == "VolumeGridGeometry":
                obj.update(origin=origin, axis_u=[0.0, 1.0, 0.0], axis_v=[-1.0, 0.0, 0.0], axis_w=[0.0, 0.0, 1.0])
        fh.seek(start)
        fh.write(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        fh.truncate()


def _axes_volume(path: Path, counts: list[int], fields: list[dict[str, Any]]) -> dict[str, Any]:
    """оси.omf and its layout: the BF2 grid it must give."""
    import math

    nu, nv, nw = counts
    tu, tv = [10.0] * nu, [12.5] * nv
    tw = [5.0 if k < nw // 2 else 2.5 for k in range(nw)]  # finer to the top: tensors
    occupied = write_volume(path, "Блоки по осям", fields, counts, [tu, tv, tw], [X0, Y0, Z0])
    origin = [X0 + math.fsum(tv), Y0, Z0]  # v runs against X from the maximum X
    _patch_axes(path, origin)
    grid = {"corner": [origin[0] - math.fsum(tv), Y0, Z0], "counts": [nv, nu, nw],
            "tensors": {"x": tv[::-1], "y": tu, "z": tw}}  # fmt: skip
    return {"counts": counts, "axes": True, "fields": fields, "occupied": occupied, "grid": grid}


# --- prepare ------------------------------------------------------------------------------------------------


def _surface(work: Path, n: int) -> None:
    import numpy as np
    from tools.gen.wireframes import pit_surface

    from bf_core.formats.omf_write import OmfWriter

    xyz, tri = pit_surface(n).mesh()
    np.savez(work / "поверхность.npz", xyz=xyz, tri=tri)
    with OmfWriter(work / "поверхность.omf", "Замер OMF") as w:
        w.add_surface("Рельеф карьера", xyz, tri, color="#8a99a8")


def make_files(work: Path, params: dict[str, Any]) -> dict[str, Any]:
    """блоки.omf, оси.omf (when `axes_grid`), поверхность.omf (when `triangles`) and their layouts."""
    fields = field_specs(int(params["fields"]), int(params.get("categories", 1)))
    counts = [int(c) for c in params["grid"]]
    tensors = [[SIZE[k]] * counts[k] for k in range(3)]
    occupied = write_volume(work / "блоки.omf", "Блоки", fields, counts, tensors, [X0, Y0, Z0])
    grid = {"corner": [X0, Y0, Z0], "counts": counts, "size": list(SIZE)}
    out: dict[str, Any] = {"main": {"counts": counts, "axes": False, "fields": fields, "occupied": occupied,
                                    "grid": grid}}  # fmt: skip
    if params.get("axes_grid"):
        out["axes"] = _axes_volume(work / "оси.omf", [int(c) for c in params["axes_grid"]], fields)
    if params.get("triangles"):
        _surface(work, int(params["triangles"]))
    return out


def prepare(params: dict[str, Any], work: Path) -> None:
    layouts = make_files(work, params)
    (work / "layouts.json").write_text(json.dumps(layouts, ensure_ascii=False), encoding="utf-8")
    load()
    small = work / "малый"  # every kernel of the import and the export compiled into the cache: small copies
    small.mkdir()
    few = make_files(small, {"grid": [6, 5, 4], "fields": 2, "categories": 1, "axes_grid": [5, 4, 3],
                             "triangles": 2000})  # fmt: skip
    got = _stages(small, few)
    assert got["mismatches"] == 0 and got["grid_ok"] and got["max_error_m"] == 0.0, got


def load() -> None:
    import psutil  # noqa: F401
    import pyarrow.parquet  # noqa: F401

    import bf_core.blocks.store  # noqa: F401
    import bf_core.formats.omf_compose  # noqa: F401
    import bf_core.formats.omf_read  # noqa: F401
    import bf_core.formats.omf_volume  # noqa: F401
    import bf_core.formats.omf_write  # noqa: F401
    import bf_core.importing.objects  # noqa: F401
    import bf_core.wireframes.source  # noqa: F401
    from bf_core.jobs.registry import load_handlers

    load_handlers()
    _omf_job()


# --- the stages ---------------------------------------------------------------------------------------------


class Peak:
    """The largest resident memory of the process while a stage runs, MB (sampled every 2 ms)."""

    def __init__(self) -> None:
        import psutil

        self._process = psutil.Process()
        self.mb = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def _rss(self) -> float:
        return float(self._process.memory_info().rss) / 2**20

    def _watch(self) -> None:
        while not self._stop.wait(0.002):
            self.mb = max(self.mb, self._rss())

    def __enter__(self) -> Peak:
        self.mb = self._rss()
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self.mb = round(max(self.mb, self._rss()), 1)


def process_peak_mb() -> float | None:
    """VmHWM of this process (Linux): the peak since it started."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        pass
    return None


def import_job(folder: Path, job_id: int, path: Path) -> dict[str, Any]:
    """The job `import.objects` of one file, as the worker runs it."""
    from bf_core.jobs.context import JobContext
    from bf_core.jobs.registry import load_handlers

    job = {"id": job_id, "params": {"files": [str(path)]}, "project_folder": str(folder)}
    ctx = JobContext(job, lambda _message: None, threading.Event())
    try:
        out = load_handlers()["import.objects"].run(ctx)
    finally:
        ctx.close()
    assert out is not None and not out["failed"], out
    return dict(out)


def _omf_job() -> Any:
    try:
        from bf_core.exporting.omf_job import write_objects
    except ImportError:  # until 11.7: the writer itself, as that function drives it
        return None
    return write_objects


def export_omf(path: Path, engine: Any, store: Any, *, wireframe_ids: Any = (), block_model_ids: Any = ()) -> str:
    """The objects into one OMF file by the writer of the export; returns what wrote it."""
    write = _omf_job()
    if write is not None:
        write(path, wireframe_ids=wireframe_ids, block_model_ids=block_model_ids, engine=engine, store=store)
        return "exporting.omf_job"
    from bf_core.blocks import store as bm
    from bf_core.db import dialect
    from bf_core.formats.omf_write import OmfWriter
    from bf_core.wireframes import source as src

    with OmfWriter(path, "Замер OMF") as w:
        for wf_id in wireframe_ids:
            with dialect.read_tx(engine) as conn:
                w.add_mesh(src.read_mesh(conn, store, int(wf_id)))
        for bm_id in block_model_ids:
            with dialect.read_tx(engine) as conn:
                info, sha = bm.block_model_info(conn, int(bm_id)), bm.cells_sha(conn, int(bm_id))

            def cells(name: str, sha: str = sha) -> Any:
                return ((i, v[name]) for i, v in bm.iter_cells(store, sha, [name]))

            w.add_volume(info["name"], info["grid"], info["fields"], cells)
    return "formats.omf_write"


def _same_grid(grid: dict[str, Any], want: dict[str, Any]) -> bool:
    keys = ("counts", "size", "tensors")
    return all(grid.get(k) == want.get(k) for k in keys) and all(
        abs(a - b) <= 1e-6 for a, b in zip(grid["corner"], want["corner"], strict=True)
    )


def check_model(engine: Any, store: Any, bm_id: int, layout: dict[str, Any]) -> dict[str, Any]:
    """The cells of a model of the project against their values: {cells, mismatches, grid_ok, cells_mb}."""
    import sqlalchemy as sa

    from bf_core.blocks import store as bm
    from bf_core.db import dialect
    from bf_core.db.schema import blobs

    with dialect.read_tx(engine) as conn:
        info, sha = bm.block_model_info(conn, bm_id), bm.cells_sha(conn, bm_id)
        size = int(conn.execute(sa.select(blobs.c.size).where(blobs.c.sha256 == sha)).scalar_one())
    bad = cells = 0
    for idx, got in bm.iter_cells(store, sha):
        bad += mismatches(idx, got, layout)
        cells += len(idx)
    bad += abs(cells - int(layout["occupied"]))
    return {"cells": cells, "mismatches": bad, "grid_ok": _same_grid(info["grid"], layout["grid"]),
            "cells_mb": round(size / 2**20, 1)}  # fmt: skip


class _CheckSink:
    """A sink of `omf_volume.read_volume` that checks the cells it is given instead of keeping them."""

    def __init__(self, layout: dict[str, Any]) -> None:
        self.layout, self.cells, self.bad = layout, 0, 0

    def write(self, idx: Any, values: Any) -> None:
        self.bad += mismatches(idx, values, self.layout)
        self.cells += len(idx)

    def finish(self) -> _CheckSink:
        return self

    def discard(self) -> None:
        pass


def check_file(path: Path, layout: dict[str, Any], tmp: Path) -> dict[str, Any]:
    """The volume of an exported OMF file against the values of its cells: {cells, mismatches, grid_ok}."""
    from bf_core.formats.omf_read import open_reader
    from bf_core.formats.omf_volume import read_volume

    with open_reader(path) as reader:
        (vol,) = reader.volumes()
        sink = _CheckSink({**layout, "axes": False})  # written along +X, +Y, +Z: the BF2 numbers
        read = read_volume(reader.file, vol, lambda _g, _f: sink, tmp_dir=tmp)
    bad = sink.bad + abs(sink.cells - int(layout["occupied"]))
    return {"cells": sink.cells, "mismatches": bad, "grid_ok": _same_grid(read.grid, layout["grid"])}


CHECK_STEP = 1 << 17  # triangles compared at a time: the check stays small next to the stages it follows


def _corners_error(xyz: Any, tri: Any, want_xyz: Any, want_tri: Any) -> float:
    """The largest difference of the corners of the triangles, in the order of the file, by portions."""
    import numpy as np

    xyz, tri = np.asarray(xyz), np.asarray(tri)
    assert tri.shape == want_tri.shape, (tri.shape, want_tri.shape)
    error = 0.0
    for s in range(0, len(tri), CHECK_STEP):
        got, want = xyz[tri[s : s + CHECK_STEP]], want_xyz[want_tri[s : s + CHECK_STEP]]
        error = max(error, float(np.abs(got - want).max(initial=0.0)))
    return error


def _stages(work: Path, layouts: dict[str, Any], *, check: bool = True) -> dict[str, Any]:
    import shutil

    import numpy as np

    from bf_core.db import dialect
    from bf_core.formats.omf_compose import describe
    from bf_core.formats.omf_read import open_reader
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import source as src

    place = work / f"прогон-{time.time_ns()}"
    place.mkdir()
    out: dict[str, Any] = {}
    try:
        folder = create_folder(place, "Замер OMF", {})
        engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
        store = BlobStore(folder)
        try:
            t0 = time.perf_counter()
            comp = describe(work / "блоки.omf")
            out["compose_s"] = round(time.perf_counter() - t0, 4)
            assert [e.kind for e in comp.elements] == ["volume"], comp.as_dict()

            with Peak() as peak:
                t0 = time.perf_counter()
                res = import_job(folder, 1, work / "блоки.omf")
                out["import_s"] = round(time.perf_counter() - t0, 3)
            out["import_mb"] = process_peak_mb() or peak.mb
            (bm_id,) = res["block_model_ids"]
            target = place / "экспорт.omf"
            with Peak() as peak:
                t0 = time.perf_counter()
                out["export_by"] = export_omf(target, engine, store, block_model_ids=[bm_id])
                out["export_s"] = round(time.perf_counter() - t0, 3)
            out["export_mb"] = peak.mb
            out["omf_mb"] = round((work / "блоки.omf").stat().st_size / 2**20, 1)
            out["export_omf_mb"] = round(target.stat().st_size / 2**20, 1)
            if check:
                main = layouts["main"]
                got = check_model(engine, store, bm_id, main)
                back = check_file(target, main, place)
                out.update(cells=got["cells"], fields=len(main["fields"]), cells_mb=got["cells_mb"],
                           mismatches=got["mismatches"] + back["mismatches"],
                           grid_ok=got["grid_ok"] and back["grid_ok"])  # fmt: skip
            target.unlink()

            if (work / "оси.omf").is_file():
                with Peak() as peak:
                    t0 = time.perf_counter()
                    res = import_job(folder, 2, work / "оси.omf")
                    out["axes_import_s"] = round(time.perf_counter() - t0, 3)
                out["axes_mb"] = peak.mb
                if check:
                    got = check_model(engine, store, res["block_model_ids"][0], layouts["axes"])
                    out["axes_cells"] = got["cells"]
                    out["mismatches"] += got["mismatches"]
                    out["grid_ok"] = out["grid_ok"] and got["grid_ok"]

            if (work / "поверхность.omf").is_file():
                with Peak() as peak:
                    t0 = time.perf_counter()
                    res = import_job(folder, 3, work / "поверхность.omf")
                    out["surface_import_s"] = round(time.perf_counter() - t0, 3)
                    (wf_id,) = res["wireframe_ids"]
                    target = place / "поверхность.omf"
                    t0 = time.perf_counter()
                    export_omf(target, engine, store, wireframe_ids=[wf_id])
                    out["surface_export_s"] = round(time.perf_counter() - t0, 3)
                out["surface_mb"] = peak.mb
                if check:
                    with np.load(work / "поверхность.npz") as f:
                        want_xyz, want_tri = f["xyz"], f["tri"]
                    with dialect.read_tx(engine) as conn:
                        mesh = src.read_mesh(conn, store, wf_id)
                    error = _corners_error(mesh.xyz, mesh.tri, want_xyz, want_tri)
                    del mesh
                    with open_reader(target) as reader:
                        (mesh,) = list(reader.surfaces())
                    out["triangles"] = int(len(want_tri))
                    out["max_error_m"] = max(error, _corners_error(mesh.xyz, mesh.tri, want_xyz, want_tri))
                    del mesh, want_xyz, want_tri
        finally:
            engine.dispose()
    finally:
        shutil.rmtree(place, ignore_errors=True)
    return out


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    layouts = json.loads((work / "layouts.json").read_text(encoding="utf-8"))
    out = _stages(work, layouts)
    assert out["mismatches"] == 0 and out["grid_ok"], out
    assert out.get("max_error_m", 0.0) == 0.0, out
    out["grid"] = "×".join(str(c) for c in params["grid"])
    return out
