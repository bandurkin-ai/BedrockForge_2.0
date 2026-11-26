"""Writing the wireframes a pit scenario makes: the DTMs of the shells and the solids of the pit (подшаг 21.12;
ADR 0028 решения 25, 26; docs/progress/e21-api.md §9, §10). Inside the write transaction of the caller.

    found = outputs_of(conn, scenario_id, OP_SURFACE)             # {shell: (wireframe id, version)} the scenario made
    out = put(conn, store, staged, scenario_id, op, shell, name=…, folder=…, source=…, found=…, dtm=…, crs=…)
    drop_levels(project_folder, [out["gone"]])                    # after the commit: the levels of the old geometry

A wireframe the scenario made is found by the graph (`lineage.store.dependents` of the scenario) and by its
`meta.source` {op: "pit_surface" | "pit_solid", scenario_id, shell, rf, version}: a second run puts the new geometry
into the SAME object («заменяется на месте»: the id, the colour, the attributes and the settings of the DTM stay;
the blobs, the counts, the extent, the name and the version are new) — no second copy of the shell in the tree.
What is not found (a new shell, a wireframe the user deleted) is made new. Either way the scenario is recorded as the
input of the wireframe in the graph (`lineage.record`), so the wireframe goes «устарел» after it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core import lineage
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects, wireframes
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as wf

OP_SURFACE = "pit_surface"
OP_SOLID = "pit_solid"
ROOT_FOLDER = "Карьер"
MAX_FOLDER = 200
_CHUNK = 400


def folder_of(scenario_name: str) -> str:
    """The folder of the scenario in the tree: «Карьер/‹имя сценария›» (no «/» inside the name, ≤ 200 characters)."""
    name = " ".join(str(scenario_name).replace("/", "-").replace("\\", "-").split()) or "Сценарий карьера"
    return f"{ROOT_FOLDER}/{name}"[:MAX_FOLDER].rstrip()


def rf_text(rf: float, digits: int | None = None) -> str:
    """«0,85», «1,0» (the shortest, one decimal at least) or, with `digits`, «1,00»."""
    if digits is not None:
        return f"{float(rf):.{digits}f}".replace(".", ",")
    text = repr(round(float(rf), 6))
    return text.replace(".", ",")


def outputs_of(conn: sa.Connection, scenario_id: int, op: str) -> dict[int, tuple[int, int]]:
    """{shell: (wireframe id, version)} of the wireframes of kind `op` that the scenario made and that are still in
    the project (the newest one when a shell has two). The graph names the candidates, `meta.source` decides."""
    node = lineage.Node("object:pit_scenario", int(scenario_id))
    ids = [n.id for n in lineage.store.dependents(conn, [node]).get(node, []) if n.kind == "wireframe"]
    out: dict[int, tuple[int, int]] = {}
    for i in range(0, len(ids), _CHUNK):
        q = sa.select(objects.c.id, objects.c.version, objects.c.meta).where(
            objects.c.kind == wf.WIREFRAME, objects.c.id.in_(ids[i : i + _CHUNK])).order_by(objects.c.id)  # fmt: skip
        for r in conn.execute(q):
            src = (r.meta or {}).get("source") or {}
            if src.get("op") == op and src.get("scenario_id") == int(scenario_id) and isinstance(src.get("shell"), int):
                out[int(src["shell"])] = (int(r.id), int(r.version))
    return out


def _replace(conn: sa.Connection, store: BlobStore, staged: wf.StagedWireframe, wid: int, version: int, *,
             name: str, meta: dict[str, Any], folder: str) -> dict[str, Any]:  # fmt: skip
    """The geometry of the wireframe `wid` replaced in place (the pattern of `model.store.replace_output`): the old
    blobs are not owned any more, the new ones are, `objects` (name, folder, meta, extent, version + 1) and `wireframes`
    (counts, origin, stats, parts). Returns {id, shas, render, shared} of the geometry that went."""
    old_shas = wf.blob_shas(conn, wid)
    old_render = conn.execute(sa.select(wireframes.c.render).where(wireframes.c.object_id == wid)).scalar()
    conn.execute(sa.delete(blob_owners).where(
        blob_owners.c.owner_kind == wf.OWNER, blob_owners.c.owner_id == wid,
        sa.or_(blob_owners.c.role.in_(list(wf.ARRAYS)), blob_owners.c.role.like("pack:%"))))  # fmt: skip
    for role, tmp in list(staged.files.items()):
        store.commit_staged(conn, tmp, media_type=wf.ARRAY_MEDIA, owner_kind=wf.OWNER, owner_id=wid, role=role)
        del staged.files[role]  # in place now: a later discard() must not touch it
    final = wf.free_name(conn, name, exclude_id=wid)
    n = conn.execute(sa.update(objects).where(objects.c.id == wid, objects.c.version == version).values(
        name=final, name_key=dialect.name_key(final), folder=folder, meta=meta, extent=staged.extent,
        version=version + 1, updated_at=dialect.utcnow())).rowcount  # fmt: skip
    if n != 1:
        raise wf.not_found()
    conn.execute(sa.update(wireframes).where(wireframes.c.object_id == wid).values(
        point_count=len(staged.xyz), triangle_count=len(staged.tri), origin=staged.origin, stats=staged.stats,
        render=staged.render))  # fmt: skip
    return {"id": wid, "shas": old_shas, "render": old_render, "shared": wf.geometry_shared(conn, old_shas),
            "name": final}  # fmt: skip


def put(conn: sa.Connection, store: BlobStore, staged: wf.StagedWireframe, scenario_id: int, op: str, shell: int, *,
        name: str, folder: str, source: Mapping[str, Any], found: Mapping[int, tuple[int, int]], dtm: bool = False,
        crs: str | None = None) -> dict[str, Any]:  # fmt: skip
    """A wireframe of the shell: the one `found` (replaced in place) or a new one (`dtm` — with the settings of a DTM);
    the scenario is its input in the graph. `source` goes to `meta.source` ({op, scenario_id, shell} are added).
    Returns {wireframe_id, name, replaced, gone} — `gone` is what `drop_levels` takes (None for a new one)."""
    src = {**source, "op": op, "scenario_id": int(scenario_id), "shell": int(shell)}
    old = found.get(int(shell))
    gone: dict[str, Any] | None = None
    if old is not None:
        row = conn.execute(sa.select(objects.c.meta).where(
            objects.c.id == old[0], objects.c.kind == wf.WIREFRAME)).first()  # fmt: skip
        if row is not None:
            meta = {**dict(row.meta or {}), "source": src}
            gone = _replace(conn, store, staged, old[0], old[1], name=name, meta=meta, folder=folder)
            wid, final = old[0], str(gone.pop("name"))
    if gone is None:
        final = wf.free_name(conn, name)
        wid = wf.commit_wireframe(conn, store, staged, name=final, meta={"source": src}, folder=folder,
                                  dtm=True if dtm else None, crs=crs)  # fmt: skip
    lineage.record(conn, lineage.Node("wireframe", wid), [lineage.Node("object:pit_scenario", int(scenario_id))])
    return {"wireframe_id": wid, "name": final, "replaced": gone is not None, "gone": gone}


def drop_levels(folder: Path, gone: Sequence[Mapping[str, Any] | None]) -> None:
    """After the commit: the levels of detail (`cache\\mesh\\`) of the geometries that went. Derived data — a failure
    here is not the failure of the job."""
    from bf_core.model.job_write import drop_levels as drop

    drop(Path(folder), [dict(g) for g in gone if g])


__all__ = ["OP_SOLID", "OP_SURFACE", "drop_levels", "folder_of", "outputs_of", "put", "rf_text"]
