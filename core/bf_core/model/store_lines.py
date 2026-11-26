"""The layers of strings of the outputs of an implicit model (подшаг 19.36; ТЗ Д-39; e19-api §10): the lines where the
veins of a system cross.

    staged = stage(store, "Модель", outs)                      # outside the write transaction: the files of the layers
    entries = commit(conn, store, model_id, "Модель", staged)  # inside it: a file of the model, a layer a pair
    gone = drop(conn, model_id)                                 # the file and the layers the last run made

The layers are the children of a file of lines whose own parent is the model (`objects.parent_id`), so that the model
owns them like its wireframes: `store.delete` removes them with it and a `.bfggis` renumbers them with the rest
(`remap`: `outputs[].line_layer_id`). A run writes the lines again: the old file goes, a new one comes (the layer is
not edited in place: its versions are those of the editor of strings, 7.22). The entry of `meta.outputs` is
`{line_layer_id, kind: "line", group, wing: null, name, pair: [a, b]}`; `listed` gives the same with the numbers of the
layer (`lines`, `length`) for the answers of the routes and of the job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.db.schema import line_layers, objects
from bf_core.formats.lineset import LineSet, LineSetBuilder
from bf_core.lines import store as ls

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from bf_core.lines.store import StagedLayer
    from bf_core.storage.blobs import BlobStore

    from .job_stages import LineOut

KIND = "line"
FILE_FORMAT = "implicit"


@dataclass
class Staged:
    items: list[tuple[LineOut, str, StagedLayer]] = field(default_factory=list)

    def discard(self) -> None:
        for _out, _name, staged in self.items:
            staged.discard()


def layer_name(model: str, out: LineOut) -> str:
    return f"{model} · пересечение {out.a} × {out.b}"


def line_set(name: str, out: LineOut) -> LineSet:
    """The layer of the lines of a pair: a string per polyline (closed ones closed), the pair and the length of every
    string as attributes."""
    b = LineSetBuilder(name)
    for one in out.lines:
        pts = np.asarray(one["points"], dtype=np.float64).reshape(-1, 3)
        edges = np.diff(pts, axis=0)
        length = float(np.linalg.norm(edges, axis=1).sum()) if len(pts) > 1 else 0.0
        if one.get("closed") and len(pts) > 2:
            length += float(np.linalg.norm(pts[0] - pts[-1]))
        b.add(pts, bool(one.get("closed")), attrs={"Пара": f"{out.a} × {out.b}", "Длина, м": length})
    return b.build()


def stage(store: BlobStore, model: str, outs: Sequence[LineOut], tick: Callable[[], None] | None = None) -> Staged:
    """The files of the layers into temp files (no database access); a cancel from `tick` discards what is staged."""
    done = Staged()
    try:
        for out in outs:
            name = layer_name(model, out)
            done.items.append((out, name, ls.stage_layer(store, line_set(name, out), tick)))
    except BaseException:
        done.discard()
        raise
    return done


def files_of(conn: sa.Connection, model_id: int) -> list[int]:
    """The files of lines the model owns."""
    return [int(i) for i in conn.execute(sa.select(objects.c.id).where(
        objects.c.parent_id == model_id, objects.c.kind == ls.LINE_FILE).order_by(objects.c.id)).scalars()]  # fmt: skip


def drop(conn: sa.Connection, model_id: int) -> list[int]:
    """The files of lines of the model with their layers go (the last run's lines, a deleted model). Returns the ids
    that are gone (the files and the layers)."""
    gone: list[int] = []
    for file_id in files_of(conn, model_id):
        gone.extend(ls.delete_file(conn, file_id))
    return gone


def commit(conn: sa.Connection, store: BlobStore, model_id: int, model: str, staged: Staged) -> list[dict[str, Any]]:
    """Inside the write transaction: a file of the model and a layer for every pair; the entries of `meta.outputs`."""
    if not staged.items:
        return []
    file_id = ls.create_file(conn, store, name=f"{model} · линии пересечения", parent_id=model_id,
                             meta={"format": FILE_FORMAT, "model_id": model_id})  # fmt: skip
    entries = []
    for k, (out, name, layer) in enumerate(staged.items):
        lid = ls.commit_layer(conn, store, layer, file_id=file_id, sort_key=float(k))
        entries.append({"line_layer_id": lid, "kind": KIND, "group": out.group, "wing": None, "name": name,
                        "pair": [out.a, out.b]})  # fmt: skip
    return entries


def listed(conn: sa.Connection, entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The line outputs of `meta.outputs` as the routes show them: `{line_layer_id, kind: "line", group, wing,
    threshold, name, lines, length}`; an output whose layer is gone is left out."""
    given = [e for e in entries if isinstance(e.get("line_layer_id"), int)]
    if not given:
        return []
    ids = [int(e["line_layer_id"]) for e in given]
    q = sa.select(objects.c.id, objects.c.name, line_layers.c.line_count, line_layers.c.length).join(
        line_layers, line_layers.c.object_id == objects.c.id).where(objects.c.id.in_(ids))
    info = {int(r.id): r for r in conn.execute(q)}
    out = []
    for e in given:
        r = info.get(int(e["line_layer_id"]))
        if r is not None:
            out.append({"line_layer_id": int(r.id), "kind": KIND, "group": e.get("group"), "wing": None,
                        "threshold": None, "name": str(r.name), "lines": int(r.line_count),
                        "length": None if r.length is None else float(r.length)})  # fmt: skip
    return out


__all__ = ["KIND", "Staged", "commit", "drop", "files_of", "layer_name", "line_set", "listed", "stage"]
