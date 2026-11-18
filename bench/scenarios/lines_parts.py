"""Parts and levels of detail of a line layer for the 3D window (ADR 0011 «Замеры»: «Части и LOD линий —
100 тыс. / 1 млн вершин ≤ 0,5 с / ≤ 2 с; повтор — 304»; ТЗ 5.3).

A layer of polylines of 100 vertices (UTM, millimetres; `lines_import._lines`) in a project database, opened
in a fresh process as the 3D window does (nothing in memory):
  first_s    the manifest — the blobs read, the lines cut into pieces, the Morton order, the parts — and every
             part at the coarsest level (32 m): what the first frame needs;
  parts_s    then every part at 4 m and at full detail;
  repeat_s   a repeated request of everything: the state of the layer from the database and the ETags of the
             manifest and of every part at every level — the answer is 304 before anything is read.
`max_error_m` — the largest distance between a source vertex (float64) and the vertex of the full level as the
window gets it (float32 offsets from the whole-metre origin of its part).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .lines_import import PER_LINE, _lines


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.formats.lineset import LineSet
    from bf_core.lines import store as ls_store
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore

    xyz = _lines(int(params["vertices"]))
    n = len(xyz)
    np.save(work / "xyz.npy", xyz)
    offsets = np.arange(0, (n + 1) * PER_LINE, PER_LINE, dtype=np.int64)
    layer = LineSet("Горизонтали", xyz.reshape(-1, 3), offsets, np.zeros(n, dtype=np.uint8))
    folder = create_folder(work, "Части линий", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    store = BlobStore(folder)
    try:
        staged = ls_store.stage_layer(store, layer)
        with dialect.write_tx(engine) as conn:
            file_id = ls_store.create_file(conn, store, name="рельеф.str", meta={"format": "micromine_str"})
            layer_id = ls_store.commit_layer(conn, store, staged, file_id=file_id)
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(f"{folder}\n{layer_id}", encoding="utf-8")
    load()  # compile the kernels into the cache outside the timing


def load() -> None:
    import numpy as np

    from bf_core.viz import lines

    xyz = np.array([[0.0, 0.0, 0.0], [3.0, 4.0, 0.0], [6.0, 0.0, 1.0], [9.0, 4.0, 1.0]] * 2) + [0.0, 0.0, 5.0]
    lay = lines.build(xyz, np.array([0, 4, 8], dtype=np.int64), np.zeros(2, dtype=np.uint8))
    for lod in range(len(lines.LOD_TOL)):
        lines.part_arrays(lay, 0, lod)
    lines.part_arrays(lines.build(xyz, None, None), 0, 1)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time
    from types import SimpleNamespace

    import numpy as np

    from bf_core.db import dialect
    from bf_core.viz import binary, lines

    folder_text, layer_text = (work / "folder.txt").read_text(encoding="utf-8").split("\n")
    folder, layer_id = Path(folder_text), int(layer_text)
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    project = SimpleNamespace(engine=engine, uid="bench", folder=folder)
    levels = range(len(lines.LOD_TOL))
    out: dict[str, Any] = {}
    try:
        t0 = time.perf_counter()
        with dialect.read_tx(engine) as conn:
            state = lines.layer_state(conn, layer_id)
        manifest = lines.manifest(project, state)
        parts = binary.decode(manifest)[0]["parts"]
        bodies = {2: [lines.part(project, state, p["k"], 2) for p in parts]}
        out["first_s"] = round(time.perf_counter() - t0, 3)
        for lod in (1, 0):
            bodies[lod] = [lines.part(project, state, p["k"], lod) for p in parts]
        out["parts_s"] = round(time.perf_counter() - t0, 3)

        t0 = time.perf_counter()
        with dialect.read_tx(engine) as conn:
            again = lines.layer_state(conn, layer_id)
        tags = [lines.layer_etag(project.uid, again)]
        tags += [lines.part_etag(project.uid, again, p["k"], lod) for p in parts for lod in levels]
        out["repeat_s"] = round(time.perf_counter() - t0, 4)
        assert tags[0] == lines.layer_etag(project.uid, state)
        assert tags[1:] == [tag for p in parts for tag in p["etags"]]  # nothing changed: 304 for every request
    finally:
        lines.forget()
        engine.dispose()

    xyz = np.load(work / "xyz.npy")
    out.update(vertices=xyz.shape[0] * PER_LINE, parts=len(parts), manifest_kb=round(len(manifest) / 1024, 1))
    error = 0.0
    seen = 0
    for lod in levels:
        points = 0
        for body in bodies[lod]:
            head, arrays = binary.decode(body)
            points += int(head["points"])
            if lod:
                continue
            # a line of 100 vertices is never cut: every polyline of the part is a whole line of the layer
            assert np.all(np.diff(arrays["offsets"].astype(np.int64)) == PER_LINE)
            shown = np.asarray(head["origin"]) + arrays["xyz"].reshape(-1, PER_LINE, 3).astype(np.float64)
            error = max(error, float(np.abs(shown - xyz[arrays["line"]]).max()))
            seen += len(arrays["line"])
        out[f"points_lod{lod}"] = points
        out[f"mb_lod{lod}"] = round(sum(len(b) for b in bodies[lod]) / 2**20, 2)
    assert seen == len(xyz) and out["points_lod0"] == out["vertices"]
    assert out["points_lod2"] < out["points_lod1"] < out["points_lod0"]
    out["max_error_m"] = error
    return out
