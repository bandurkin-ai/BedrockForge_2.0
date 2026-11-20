"""The job `export.objects` — «Объекты в файлы» (ADR 0017 «Замеры»: «`export.objects` — демо / 1 ГБ данных: ci ≤ 30 с;
full ≤ 3 мин (ADR 0010), ≤ 1,5 ГБ»; ТЗ 7.35 Д-17).

The project, made in `prepare`: the demo «Демо: участок Северный» (every part of `bf_core/demo`, its seed) and, when
`mb` is given, objects of about that many MiB of data more — the data as the job counts them (`registry.Item.size`,
its progress goes by them: 24 B a vertex and 12 B a triangle, 24 B a point of a line or a cloud, 100 B a row of a
drillhole set, 8 B × (1 + fields) a cell of a block model), shared as in a working project:
  35 %  wireframes — `WIREFRAMES` pit surfaces (`tools/gen/wireframes.pit_surface`, UTM float64), from one OMF file;
  25 %  a point cloud of a LAS file (8 points a square metre with noise, classes 1–6);
  10 %  a line file of contours (closed and open polylines, 2 000 vertices each), from the same OMF file;
  10 %  a drillhole set: collars, two surveys a hole, an assay table of three grades (≈ 8 % empty);
  20 %  a block model of 5 fields (`omf_io.values`: numbers and a category, empty cells), from the same OMF file.
The OMF and the LAS files are loaded by the job `import.objects`, as the wizard does; the drillhole set — by the
store of the sets. Every type goes into its default format (`GET /export/formats`: DXF, Micromine STR, LAS, CSV,
OMF); of the demo, the survey points (a layer of points: no line for Micromine STR) are skipped with the reason.
Stage: `export_s` — the job, from its plan to its report (writers, the rename of the folder, `export_log`);
`‹тип›_s` — the part of it the writers of that type took (`wireframe_s`, `cloud_s`, `line_file_s`, …);
`export_mb` — the peak of the process up to its end (VmHWM; the job is the first thing the run does). The export
goes into a folder of its own, removed after the run (the runs of `--repeat` see the same project).
What is checked (a failure stops the run): every object is a file or a skipped entry with its reason, the
skipped are the expected ones, every file of the report is on the disk with its size, the point count in the
header of every LAS and the cells of the block model in the OMF written (`omf_compose.describe`); `problems` —
the first of what failed.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

PROJECT = "Замер экспорта"
SHARES = {"wireframe": 0.35, "cloud": 0.25, "line_file": 0.10, "drillhole_set": 0.10, "block_model": 0.20}
WIREFRAMES = 4
LINE_VERTICES = 2_000
HOLE_ROWS = 100
MODEL_FIELDS, MODEL_CATEGORIES = 5, 1
X0, Y0 = 600_123.25, 6_300_456.5


# --- the data -----------------------------------------------------------------------------------------------


def _surfaces(w: Any, total: int) -> None:
    from tools.gen.wireframes import pit_surface

    for k in range(WIREFRAMES):
        xyz, tri = pit_surface(total // WIREFRAMES, seed=20 + k, origin=(X0 + 6_000.0 * k, Y0, 400.0)).mesh()
        w.add_surface(f"Поверхность {k + 1}", xyz, tri, color="#8a99a8")


def _contours(w: Any, vertices: int) -> None:
    import numpy as np

    from bf_core.formats.lineset import LineSet

    lines = max(1, vertices // LINE_VERTICES)
    closed = (np.arange(lines) % 4 != 3).astype(np.uint8)  # every fourth one open: an arc
    xyz = np.empty((lines * LINE_VERTICES, 3))
    for k in range(lines):
        t = np.linspace(0.0, (2.0 if closed[k] else 1.5) * np.pi, LINE_VERTICES)
        r = 50.0 + 3.0 * (k % 300) + 4.0 * np.sin(5.0 * t + k)
        cx, cy = X0 + 2_000.0 * (k // 300), Y0 - 2_000.0
        part = np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t), np.full_like(t, 300.0 - 0.5 * (k % 300))])
        if closed[k]:
            part[-1] = part[0]  # a closed line repeats its first vertex
        xyz[k * LINE_VERTICES : (k + 1) * LINE_VERTICES] = part
    offsets = np.arange(lines + 1, dtype=np.int64) * LINE_VERTICES
    w.add_lines(LineSet("Горизонтали", xyz, offsets, closed, color="#3c7ad0"))


def _volume(w: Any, cells: int) -> None:
    import math

    from bench.scenarios.omf_io import field_specs, values

    side = max(2, round(math.pow(cells / 2.0, 1.0 / 3.0)))
    counts = [2 * side, side, side]
    fields = field_specs(MODEL_FIELDS, MODEL_CATEGORIES)
    n = counts[0] * counts[1] * counts[2]
    by_name = {f["name"]: k for k, f in enumerate(fields)}

    def portions(name: str) -> Any:
        import numpy as np

        k = by_name[name]
        for s in range(0, n, 4_000_000):
            o = np.arange(s, min(n, s + 4_000_000), dtype=np.int64)
            yield o, values(o, k, fields[k]["kind"], counts)

    grid = {"corner": [X0, Y0 + 3_000.0, -200.0], "counts": counts, "size": [10.0, 10.0, 5.0]}
    w.add_volume("Блочная модель", grid, fields, portions, color="#d08a3c")


def _cloud(path: Path, points: int) -> None:
    import numpy as np

    from bf_core.formats.las_write import write_las

    rng = np.random.default_rng(5)
    side = np.sqrt(points / 8.0)  # 8 points a square metre
    x, y = rng.uniform(0.0, side, points), rng.uniform(0.0, side, points)
    z = 350.0 + 6.0 * np.sin(x / 40.0) * np.cos(y / 55.0) + rng.normal(0.0, 0.03, points)
    xyz = np.column_stack([X0 + 30_000.0 + x, Y0 + y, z])
    write_las(path, xyz, rng.integers(1, 7, points).astype(np.uint8), offset=[X0, Y0, 0.0])


def _drillholes(engine: Any, rows: int) -> None:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.drillholes.store import (
        CollarRow,
        ColumnSpec,
        TableData,
        create_set,
        replace_collars,
        replace_surveys_in,
        write_table,
    )

    holes = max(1, rows // HOLE_ROWS)
    names = [f"Э-{h + 1:05d}" for h in range(holes)]
    side = int(np.ceil(np.sqrt(holes)))
    with dialect.write_tx(engine) as conn:
        set_id = create_set(conn, "Скважины крупные")
        replace_collars(conn, set_id, [
            CollarRow(nm, X0 + 25.0 * (h % side), Y0 + 6_000.0 + 25.0 * (h // side), 320.0, float(HOLE_ROWS))
            for h, nm in enumerate(names)
        ])  # fmt: skip
        replace_surveys_in(conn, set_id, [(nm, d, 45.0, -60.0) for nm in names for d in (0.0, 50.0)], lambda *_a: None)
    rng = np.random.default_rng(9)
    n = holes * HOLE_ROWS
    grades = rng.lognormal(-1.0, 1.0, (3, n)).round(3)
    grades[rng.random((3, n)) < 0.08] = np.nan
    depth = np.tile(np.arange(HOLE_ROWS, dtype=float), holes)
    table = TableData(
        "Пробы", "assay", [nm for nm in names for _ in range(HOLE_ROWS)], depth, depth + 1.0,
        [ColumnSpec("AU", "number", "г/т", "component"), ColumnSpec("AG", "number", "г/т", "component"),
         ColumnSpec("CU", "number", "%", "component")], [grades[0], grades[1], grades[2]],
    )  # fmt: skip
    write_table(engine, set_id, table, job_id=None, tick=lambda *_a: None)


def _demo(engine: Any, store: Any) -> list[int]:
    import random

    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import objects
    from bf_core.demo.project import PARTS, SEED

    rng = random.Random(SEED)
    for _name, part in PARTS:
        with dialect.write_tx(engine) as conn:
            part(conn, store, rng)
    with dialect.read_tx(engine) as conn:
        return [int(i) for i in conn.execute(sa.select(objects.c.id).where(objects.c.parent_id.is_(None))).scalars()]


def _big(work: Path, folder: Path, engine: Any, mb: float) -> None:
    from bench.scenarios.omf_io import import_job
    from bf_core.formats.omf_write import OmfWriter

    total = mb * 2**20
    files = work / "файлы"
    files.mkdir()
    with OmfWriter(files / "Крупные объекты.omf", PROJECT, "Синтетические объекты для замера экспорта") as w:
        _surfaces(w, int(SHARES["wireframe"] * total / 24))
        _contours(w, int(SHARES["line_file"] * total / 24))
        _volume(w, int(SHARES["block_model"] * total / (8 * (1 + MODEL_FIELDS))))
    _cloud(files / "Съёмка.las", int(SHARES["cloud"] * total / 24))
    import_job(folder, 1, files / "Крупные объекты.omf")
    import_job(folder, 2, files / "Съёмка.las")
    _drillholes(engine, int(SHARES["drillhole_set"] * total / 100))


# --- prepare ------------------------------------------------------------------------------------------------


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.db import dialect
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore

    load()
    folder = create_folder(work, PROJECT, {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        demo = _demo(engine, BlobStore(folder))
        if params.get("mb"):
            _big(work, folder, engine, float(params["mb"]))
        dialect.checkpoint(engine)
    finally:
        engine.dispose()
    (work / "project.json").write_text(json.dumps({"folder": str(folder), "demo": demo}), encoding="utf-8")
    got = _stages(work, ids=demo)  # every writer and its kernels compiled into the cache: the demo once
    assert got["mismatches"] == 0, got


def load() -> None:
    import psutil  # noqa: F401
    import pyarrow.parquet  # noqa: F401

    import bf_core.demo.project  # noqa: F401
    import bf_core.drillholes.export  # noqa: F401
    import bf_core.drillholes.export_book  # noqa: F401
    import bf_core.formats.las_write  # noqa: F401
    import bf_core.formats.omf_write  # noqa: F401
    import bf_core.lines.cloud_export  # noqa: F401
    import bf_core.lines.export  # noqa: F401
    import bf_core.wireframes.export  # noqa: F401
    from bf_core.exporting import registry as reg
    from bf_core.jobs.registry import load_handlers

    load_handlers()
    reg.load_writers()


# --- the run ------------------------------------------------------------------------------------------------


def defaults(conn: Any, store: Any) -> dict[str, str]:
    """The format of every type the window shows by default (`GET /export/formats`)."""
    from bf_core.exporting import registry as reg

    table = reg.formats(conn, reg.Env(store=store))
    return {t["kind"]: t["default"] for t in table["types"] if t["count"] and t["default"]}


def _las_points(path: Path) -> int:
    from bf_core.formats.las_header import read_header

    return int(read_header(path).point_count)


def _check(result: dict[str, Any], items: list[Any], demo: list[int]) -> dict[str, Any]:
    from bf_core.formats.omf_compose import describe

    bad: list[str] = []
    files = result["files"]
    for f in files:
        p = Path(f["path"])
        if not p.is_file() or p.stat().st_size != f["size"]:
            bad.append(f"файл {p.name}: нет или не того размера")
    by_id = {i.id: i for i in items}
    written = {int(f["object_id"]) for f in files}
    skipped = {int(s["object_id"]): s for s in result["skipped"]}
    bad += [f"объект {i}: ни файла, ни причины" for i in set(by_id) - written - set(skipped)]
    bad += [f"объект {i}: и файл, и пропуск" for i in written & set(skipped)]
    for s in skipped.values():  # of the demo only, with its reason (the survey points: no line for STR)
        if int(s["object_id"]) not in demo or not s["reason"]:
            bad.append(f"пропущен «{s['object']}»: {s['reason']}")
    for f in files:
        item = by_id[int(f["object_id"])]
        if f["format"] == "las" and _las_points(Path(f["path"])) != int(item.info["points"]):
            bad.append(f"LAS «{item.name}»: не то число точек")
        elif item.kind == "block_model":
            (vol,) = describe(Path(f["path"])).elements
            if vol.cells != int(item.info["grid_cells"]):
                bad.append(f"OMF «{item.name}»: {vol.cells} ячеек")
    return {"files": len(files), "skipped": len(skipped), "mismatches": len(bad), "problems": bad[:5]}


def _timed_context(job: dict[str, Any]) -> Any:
    """The context of the job that also notes when each object starts («‹имя› → ‹формат›»), before the limit of
    5 messages a second drops some."""
    from bf_core.jobs.context import JobContext

    class Timed(JobContext):
        def __init__(self) -> None:
            super().__init__(job, lambda _message: None, threading.Event())
            self.marks: list[tuple[float, str]] = []

        def tick(self, progress: float | None = None, message: str | None = None) -> None:
            if message is not None and (message.startswith("«") and "» → " in message or message.startswith("Перенос")):
                self.marks.append((time.perf_counter(), message))
            super().tick(progress, message)

    return Timed()


def _by_kind(marks: list[tuple[float, str]], items: list[Any]) -> dict[str, float]:
    """Seconds the writers of each type took: from the start of an object to the start of the next one."""
    kind = {i.name: i.kind for i in items}
    out: dict[str, float] = {}
    for (t0, message), (t1, _next) in zip(marks, marks[1:], strict=False):
        if "» → " in message:
            k = kind.get(message[1 : message.index("» → ")], "?")
            out[f"{k}_s"] = round(out.get(f"{k}_s", 0.0) + t1 - t0, 3)
    return out


def _stages(work: Path, ids: list[int] | None = None) -> dict[str, Any]:
    import shutil

    from bf_core.db import dialect
    from bf_core.exporting import job as ej
    from bf_core.exporting import registry as reg
    from bf_core.jobs.registry import load_handlers
    from bf_core.storage.blobs import BlobStore

    from .omf_io import process_peak_mb

    project = json.loads((work / "project.json").read_text(encoding="utf-8"))
    folder = Path(project["folder"])
    store = BlobStore(folder)
    place = work / f"выгрузка-{time.time_ns()}"
    place.mkdir()
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.read_tx(engine) as conn:
            formats = defaults(conn, store)
            params = ej.request(conn, folder=str(place), formats=formats, ids=ids, project=PROJECT, store=store)
            items = reg.items(conn, params["types"], ids)
        job = {"id": time.time_ns() % 2**31, "params": params, "project_folder": str(folder)}
        ctx = _timed_context(job)
        try:
            t0 = time.perf_counter()
            result = load_handlers()[ej.KIND].run(ctx)
            out: dict[str, Any] = {"export_s": round(time.perf_counter() - t0, 3)}
            assert result is not None
        finally:
            ctx.close()
        out["export_mb"] = process_peak_mb()
        out.update(_by_kind(ctx.marks, items))
        out.update(_check(result, items, project["demo"]))
        out.update(objects=len(items), data_mb=round(params["bytes"] / 2**20, 1),
                   written_mb=round(result["bytes"] / 2**20, 1), formats=",".join(sorted(set(formats.values()))))
    finally:  # fmt: skip
        engine.dispose()
        shutil.rmtree(place, ignore_errors=True)
    return out


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    out = _stages(work)
    assert out["mismatches"] == 0, out
    return out
