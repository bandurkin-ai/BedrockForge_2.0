"""Import and export of wireframes: Micromine TRIDB (ADR 0013 «Замеры»: «Импорт TRIDB — ci ≤ 6 с / 600 МБ;
full ≤ 60 с / 2 ГБ»; ТЗ 5.3, 9.4, 9.12).

A TRIDB of 45 wireframes with names, colours and attributes (`tools/gen/wireframes.py::scene`: a pit surface
with 62 % of the triangles, a fault, three ore bodies, forty lenses; UTM coordinates, float64 as they are; the
triangles in the order of the file, not in the spatial order of the store) is loaded into a project database
the way `import.objects` does it, wireframe by wireframe:
  read_s    `formats/tridb.py`: the composition of the file and the geometry BLOB of every wireframe in portions;
  store_s   `wireframes/source.py::stage_mesh` (check, spatial order, `seq`, parts, area and volume, the blobs)
            and `commit_mesh` in a short transaction;
  import_s  both: the wireframes are `ready` in the project with their measures (the levels of detail and the
            checks are the job `wireframe.check` — `wireframe_check.py`);
then `export.wireframes` writes them back as a TRIDB:
  export_s  `source.read_mesh` (the triangles back in the order of the file) → `formats/tridb_write.py`.
The file written is compared with the file read (ТЗ 5.4 «побитно», ADR 0013 §2):
  blob_mismatches     geometry BLOBs that are not the same byte for byte — so every coordinate and the order of
                      the triangles went through the project unchanged;
  row_mismatches      rows of `GeneralInformation`, `GeometryProperties` and of the attributes that differ;
  measure_mismatches  wireframes whose `Volume` or `SurfaceArea` in the file differ from the measures of the
                      store by more than 1e-9 (relative).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SOURCE = "карьер.tridb"
TABLES = ("GeneralInformation", "GeometryProperties", "UserAttributeDefinition", "UserAttributeValue")
NOW = 1_795_000_000  # the dates of the file: the same file from the same wireframes


def _meshes(triangles: int) -> list[Any]:
    from tools.gen.wireframes import scene

    from bf_core.formats.meshset import MeshSet

    return [MeshSet(m["name"], m["xyz"], m["tri"], color=m["color"], attrs=m["attrs"]) for m in scene(triangles)]


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.formats.tridb_write import write_tridb
    from bf_core.projects.project import create_folder

    write_tridb(work / SOURCE, _meshes(int(params["triangles"])), now=NOW, author="bench")
    folder = create_folder(work, "Каркасы", {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    load()  # compile into the cache outside the timing


def load() -> None:
    import numpy as np
    import pyarrow  # noqa: F401

    import bf_core.formats.tridb  # noqa: F401
    import bf_core.formats.tridb_write  # noqa: F401
    import bf_core.wireframes.source  # noqa: F401
    from bf_core.geo import meshes

    v = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 4.0]])
    t = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]], dtype=np.int32)
    order = meshes.morton_order(v, t)
    meshes.parts_table(v, t[order])
    meshes.mesh_stats(v, t)


def _compare(a: Path, b: Path) -> tuple[int, int]:
    """(geometry BLOBs, rows) of two TRIDB files that differ; the BLOBs are compared in portions."""
    blobs = rows = 0
    with sqlite3.connect(f"file:{a.as_posix()}?mode=ro", uri=True) as ca, sqlite3.connect(
        f"file:{b.as_posix()}?mode=ro", uri=True
    ) as cb:
        ids = [[r[0] for r in c.execute("SELECT rowid FROM Geometry ORDER BY rowid")] for c in (ca, cb)]
        blobs += len(set(ids[0]) ^ set(ids[1]))
        for rowid in sorted(set(ids[0]) & set(ids[1])):
            with ca.blobopen("Geometry", "Geometry", rowid, readonly=True) as fa, cb.blobopen(
                "Geometry", "Geometry", rowid, readonly=True
            ) as fb:
                same = len(fa) == len(fb)
                while same:
                    x, y = fa.read(16 << 20), fb.read(16 << 20)
                    same = x == y
                    if not x:
                        break
                blobs += not same
        for table in TABLES:
            left, right = (c.execute(f"SELECT rowid, * FROM {table} ORDER BY rowid").fetchall() for c in (ca, cb))
            rows += abs(len(left) - len(right)) + sum(x != y for x, y in zip(left, right, strict=False))
    return blobs, rows


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    from bf_core.db import dialect
    from bf_core.formats.tridb import check_measures, open_tridb
    from bf_core.formats.tridb_write import write_tridb
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import source

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    store = BlobStore(folder)
    ids: list[int] = []
    triangles = points = measures = 0
    read_s = 0.0
    out = work / "экспорт.tridb"
    try:
        t0 = time.perf_counter()
        with open_tridb(work / SOURCE) as f:
            for entry in f.entries:
                a = time.perf_counter()
                mesh = f.read(entry)
                read_s += time.perf_counter() - a
                staged = source.stage_mesh(store, mesh)
                try:
                    with dialect.write_tx(engine) as conn:
                        ids.append(source.commit_mesh(conn, store, staged, mesh, folder=SOURCE))
                finally:
                    staged.discard()
                measures += bool(check_measures(mesh.source, staged.stats))
                triangles += mesh.triangle_count
                points += mesh.point_count
                del mesh, staged
        t1 = time.perf_counter()

        def stored() -> Any:
            for wf_id in ids:
                with dialect.read_tx(engine) as conn:
                    mesh = source.read_mesh(conn, store, wf_id)
                yield mesh

        written = write_tridb(out, stored(), total=len(ids), now=NOW, author="bench")
        t2 = time.perf_counter()
    finally:
        engine.dispose()

    blobs, rows = _compare(work / SOURCE, out)
    assert written.meshes == len(ids) and written.triangles == triangles and written.points == points, written
    assert blobs == 0 and rows == 0 and measures == 0, (blobs, rows, measures)
    return {
        "wireframes": len(ids),
        "triangles": triangles,
        "points": points,
        "file_mb": round((work / SOURCE).stat().st_size / 1e6, 1),
        "read_s": round(read_s, 3),
        "store_s": round(t1 - t0 - read_s, 3),
        "import_s": round(t1 - t0, 3),
        "export_s": round(t2 - t1, 3),
        "blob_mismatches": blobs,
        "row_mismatches": rows,
        "measure_mismatches": measures,
        "warnings": len(written.warnings),
    }
