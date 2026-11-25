"""The writing of a built implicit model (подшаг 19.12; ADR 0026 решения 23–25; docs/progress/e19-api.md §2, §6): the
files outside the write transaction, then one transaction that moves everything into place, or nothing.

    files = stage_files(store, spec, points, built, prog)          # blobs and the wireframes of the outputs, staged
    done = commit(engine, store, model_id, files, …)               # one transaction: blobs, outputs, `record_run`
    files.discard()                                                # always (what was moved is not touched)

Atomic (the model is either as it was or the new one): nothing is in the project before `commit`; a cancel before it
(the progress of the staging raises) or a failure leaves the temp files only (`discard`); in the transaction the blobs
(`store.commit`), the outputs (`store.commit_output` — a new wireframe, `store.replace_output` — the geometry of an
output of the last run replaced in place, the same id; the outputs that are no more are deleted) and `record_run` (the
state, `meta.outputs`, `stats`, `inputs`, the rows of the graph) are one unit. An output other objects refer to cannot
be deleted (422 `wireframe_in_use`): the whole run is rolled back and says so.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects

from . import store as st
from . import store_base as sb
from . import store_blobs as bl
from .job_stages import S_WRITE, Built, Part, Progress

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes.store import StagedWireframe

    from .bodies import Output
    from .data import Points
    from .spec import ModelSpec
    from .store_check import Prepared

AT_STAGE, AT_COMMIT = 0.94, 0.99


# --- the staged files -------------------------------------------------------------------------------------------------


@dataclass
class Files:
    blobs: dict[str, bl.Staged] = field(default_factory=dict)
    wireframes: list[tuple[Output, str, StagedWireframe]] = field(default_factory=list)

    def discard(self) -> None:
        bl.discard(self.blobs)
        for _o, _name, staged in self.wireframes:
            staged.discard()


def output_name(model: str, spec: ModelSpec, o: Output) -> str:
    """«‹модель› · ‹группа›», «… · поверхность», «‹модель› · Прочее»; a shell — «… · ‹порог›» (e19-api §2)."""
    if o.group is None:
        return f"{model} · {o.name}"
    group = spec.group(int(o.group)).name if any(g.id == o.group for g in spec.groups) else o.name
    if o.kind == "surface":
        return f"{model} · {group} · поверхность"
    if o.threshold is not None:
        return f"{model} · {group} · {o.threshold:g}".replace(".", ",")
    return f"{model} · {group}"


def _points_columns(points: Points) -> dict[str, Any]:
    return {"x": points.xyz[:, 0], "y": points.xyz[:, 1], "z": points.xyz[:, 2], "f": points.f, "kind": points.kind,
            "group": points.group, "wing": points.wing, "src": points.src, "from": points.span[:, 0],
            "to": points.span[:, 1], "vertex": points.vertex}  # fmt: skip


def _field_columns(parts: list[Part]) -> tuple[dict[str, Any], Any]:
    """`implicit_patches` (a row per patch) and `implicit_solution` (λ then c of every row) of the solved fields: the
    rows go by (group, wing), a group of several fields (the thresholds of a shell) numbers its patches `j` on."""
    rows: dict[str, list[Any]] = {k: [] for k in bl.PATCH_COLUMNS}
    structural = any(p.record.matrices is not None for p in parts)  # the columns of A_j: all the rows or none
    if structural:
        rows.update({k: [] for k in bl.MATRIX_COLUMNS})
    solution: list[np.ndarray] = []
    offset = 0
    next_j: dict[tuple[int, int], int] = {}
    for part in sorted(parts, key=lambda p: (p.group, p.wing)):
        rec, key = part.record, (part.group, part.wing)
        ps = rec.patches
        for j, sol in enumerate(rec.solutions):
            n = len(sol.lam)
            jj = next_j.get(key, 0)
            next_j[key] = jj + 1
            rows["group"].append(part.group)
            rows["wing"].append(part.wing)
            rows["j"].append(jj)
            rows["cx"].append(ps.centers[j, 0])
            rows["cy"].append(ps.centers[j, 1])
            rows["cz"].append(ps.centers[j, 2])
            rows["r"].append(ps.radii[j])
            rows["n"].append(n)
            rows["offset"].append(offset)
            rows["hash"].append(rec.hashes[j])
            rows["eta"].append(sol.eta)
            rows["cond"].append(sol.cond1)
            if structural:  # a patch without A_j (never in one model) is NaN, not an identity
                a = np.full(9, np.nan) if rec.matrices is None else np.asarray(rec.matrices[j]).reshape(9)
                for name, v in zip(bl.MATRIX_COLUMNS, a, strict=True):
                    rows[name].append(v)
            solution.append(np.asarray(sol.lam, dtype=np.float64))
            solution.append(np.asarray(sol.poly, dtype=np.float64))
            offset += n + 4
    kinds = {**bl.PATCH_COLUMNS, **bl.MATRIX_COLUMNS}
    cols: dict[str, Any] = {k: np.asarray(v, dtype=kinds[k]) for k, v in rows.items() if k != "hash"}
    cols["hash"] = np.frombuffer(b"".join(rows["hash"]), dtype=f"S{bl.HASH_SIZE}")  # the bytes as they are
    return cols, (np.concatenate(solution) if solution else np.zeros(0))


def _progress(win: Callable[[float | None, str | None], None]) -> Callable[[float, str], None]:
    def inner(share: float, text: str) -> None:
        win(share, f"{S_WRITE}: {text[:1].lower()}{text[1:]}")

    return inner


def stage_files(store: BlobStore, spec: ModelSpec, model_name: str, points: Points, built: Built,
                prog: Progress) -> Files:  # fmt: skip
    """The three blobs and the wireframes of the outputs, written into temp files (no database access). The progress
    may raise (a cancel): what is staged is then discarded here."""
    from bf_core.wireframes import store as wf

    files = Files()
    try:
        prog(AT_STAGE, f"{S_WRITE}: файлы модели")
        gids = [int(g.id or 0) for g in spec.groups]
        files.blobs[sb.ROLE_POINTS] = bl.stage_points(store, _points_columns(points), groups=gids,
                                                      sources=points.sources)  # fmt: skip
        patches, solution = _field_columns(built.parts)
        files.blobs[sb.ROLE_PATCHES] = bl.stage_patches(store, patches)
        files.blobs[sb.ROLE_SOLUTION] = bl.stage_solution(store, solution)
        n = max(len(built.outputs), 1)
        for i, o in enumerate(built.outputs):
            win = prog.window(AT_STAGE + (AT_COMMIT - AT_STAGE) * (0.2 + 0.8 * i / n),
                              AT_STAGE + (AT_COMMIT - AT_STAGE) * (0.2 + 0.8 * (i + 1) / n))
            staged = wf.stage_wireframe(store, o.vertices, o.triangles, progress=_progress(win))
            files.wireframes.append((o, output_name(model_name, spec, o), staged))
    except BaseException:
        files.discard()
        raise
    return files


# --- the transaction --------------------------------------------------------------------------------------------------


def _key(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    th = entry.get("threshold")
    return (entry.get("kind"), entry.get("group"), entry.get("wing"), None if th is None else round(float(th), 12))


@dataclass
class Done:
    version: int
    outputs: list[dict[str, Any]]
    removed: list[dict[str, Any]]  # the geometries that went (replaced and deleted): their levels of detail
    deleted: list[int]
    replaced: list[int]
    created: list[int]


def commit(engine: sa.Engine, store: BlobStore, model_id: int, files: Files, *, spec: ModelSpec, prep: Prepared,
           expected_version: int | None, last_run: Mapping[str, Any], stats: Mapping[str, Any],
           ) -> Done:  # fmt: skip
    """One write transaction (the module text). 404 `model_not_found`, 409 `version_conflict` (the model was edited
    while it was built — the result is not written)."""
    from bf_core import lineage
    from bf_core.wireframes import store as wf
    from bf_core.wireframes.cloud_dz import forget_surface

    with dialect.write_tx(engine) as conn:
        row = sb.stored(conn, model_id)
        if expected_version is not None and int(row.version) != int(expected_version):
            raise sb.conflict(int(row.version))
        meta = dict(row.meta or {})
        kids = set(conn.execute(sa.select(objects.c.id).where(
            objects.c.parent_id == model_id, objects.c.kind == wf.WIREFRAME)).scalars())  # fmt: skip
        old: dict[tuple[Any, ...], list[int]] = {}
        for e in meta.get("outputs") or []:
            wid = e.get("wireframe_id")
            if isinstance(wid, int) and wid in kids:
                old.setdefault(_key(e), []).append(wid)
        st.commit(conn, store, model_id, files.blobs)
        outputs: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        replaced: list[int] = []
        created: list[int] = []
        for o, name, staged in files.wireframes:
            entry_in = {"kind": o.kind, "group": o.group, "wing": o.wing, "threshold": o.threshold}
            same = old.get(_key(entry_in))
            if same:
                wid = same.pop(0)
                entry, gone = st.replace_output(conn, store, staged, model_id, wid, name=name, kind=o.kind,
                                                group=o.group, wing=o.wing, threshold=o.threshold)  # fmt: skip
                removed.append(gone)
                replaced.append(wid)
            else:
                entry = st.commit_output(conn, store, staged, model_id, name=name, kind=o.kind, group=o.group,
                                         wing=o.wing, threshold=o.threshold)  # fmt: skip
                created.append(int(entry["wireframe_id"]))
            outputs.append(entry)
        deleted: list[int] = []
        for ids in old.values():
            for wid in ids:
                removed.append(wf.delete_wireframe(conn, wid))  # 422 `wireframe_in_use`: all is rolled back
                forget_surface(conn, wid)
                lineage.forget(conn, lineage.Node("wireframe", wid))
                deleted.append(wid)
        st.record_run(conn, model_id, spec, last_run, prep.inputs, outputs=outputs, stats=stats)
        version = int(sb.stored(conn, model_id).version)
    return Done(version, outputs, removed, deleted, replaced, created)


def drop_levels(folder: Path, removed: list[dict[str, Any]]) -> None:
    """After the commit: the levels of detail (`cache\\mesh\\`) of the geometries that went and are nobody's now.
    Derived data: a failure here is not the failure of the run."""
    from bf_core.viz import meshes

    for gone in removed:
        if gone.get("shas") and not gone.get("shared"):
            try:
                meshes.drop_cache(folder, meshes.cache_key(tuple(sorted(gone["shas"].items())), gone["render"]))
            except Exception:  # noqa: BLE001, S110
                pass


def events(model_id: int, done: Done | None) -> list[list[Any]]:
    """The events of the job: the model and its wireframes (created, replaced, deleted)."""
    out: list[list[Any]] = [["implicit.changed", {"kind": sb.KIND, "id": model_id}]]
    if done is not None:
        for wid in [*done.created, *done.replaced]:
            out.append(["objects.changed", {"kind": "wireframe", "id": wid}])
        for wid in done.deleted:
            out.append(["objects.changed", {"kind": "wireframe", "id": wid, "deleted": True}])
    return out


__all__ = ["Done", "Files", "commit", "drop_levels", "events", "output_name", "stage_files"]
