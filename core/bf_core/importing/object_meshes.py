"""Wireframes of the job `import.objects` (ТЗ 7.8, 7.21; ADR 0013 §3, §5; 8.15): what «Импорт файлов» does with
the wireframes a file gives (`formats.registry.ObjectsRead.meshes`). The job itself is `objects.py`.

Every wireframe of a file is an object of its own (ADR 0013 «Файл с несколькими каркасами» — вариант А):

* one at a time: taken from the reader, staged (`wireframes.source.stage_mesh`: the checks of the geometry, the
  spatial order with `seq` — the order of the file, the parts, the measures, the blobs) and written as
  `building` by a short transaction — a few small ones together (`PORTION`), a large one alone, so a file of a
  hundred wireframes is never in memory whole and a cancel comes between any two steps;
* the name of the file's wireframe, or «name (2)» when the project has it (`meta.source.as` keeps that, so an
  export gives the file its own name back); the colour and the attributes of the file; `meta.source` — the rows
  a writer needs for the same file; `meta.warnings` — what the reader and the checks said;
* the checks at loading are `geo.meshes.mesh_stats` of the stored geometry (`wireframes.stats`: area, volume,
  closed, orientation, open and non-manifold edges, degenerate triangles). What they find goes to the journal:
  degenerate triangles, edges of three triangles and more, a volume or an area of the file that the geometry
  does not confirm (TRIDB, 1e-9), a volume stated for a wireframe that is not closed. The consistency of the
  normals, self-intersections and the levels of detail are the job `wireframe.check` (8.16), started after;
* a file with several wireframes puts them into a folder of the tree named after it (under the folder of the
  wizard, if one was chosen); a single wireframe stays where the wizard put it;
* a wireframe that cannot be read is a line of the journal and an entry of `skipped`; the others are loaded. An
  error of the file itself (it is found while the wireframes are taken) removes what the file has given so far
  and is raised as `FileFailed` — the job goes on with the next file;
* in the final transaction of the job (`log_meshes`) every wireframe gets its row of `import_log`, and the
  source file — one blob — is owned by each of them (owner `import`, role `source`; the other file of a pair —
  `sidecar:str`, `sidecar:dm`): it stays in the project until the last wireframe of the file is deleted.

Until the final transaction everything is `building` under the id of the job: a cancel or a failure leaves
nothing (`jobs.store.drop_partial`), the temporary files are removed here.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, import_log, objects
from bf_core.formats.errors import FormatError
from bf_core.jobs.context import JobContext

from .object_files import MAX_NOTES, Group, Tick, n_text

WIREFRAME = "wireframe"  # `import_log.counts.kind` of a wireframe; the kind of its object
WIREFRAMES = "wireframes"  # the kind of a file entry of the result
TARGET = "objects"  # `import_log.target`
PORTION = 25  # wireframes written by one transaction…
PORTION_TRIANGLES = 100_000  # … or fewer: staged wireframes wait in memory, a large one is written at once
MAX_FOLDER = 200  # `objects.folder`
MEDIA = "application/octet-stream"
#: format → (the key of `MeshSet.source` that names the other file of the pair, …)
PAIRED = {"surpac_dtm": ("strings",), "datamine": ("file", "points_file")}


class FileFailed(Exception):
    """The file could not be read to its end: nothing of its wireframes stays. `cause` — FormatError or OSError."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


@dataclass
class Written:
    """The wireframes of a file written as `building`."""

    ids: list[int] = field(default_factory=list)
    counts: list[dict[str, Any]] = field(default_factory=list)  # `import_log.counts` of every wireframe
    skipped: list[dict[str, str]] = field(default_factory=list)  # {name, code, message, hint}
    companions: dict[str, Path] = field(default_factory=dict)  # suffix → the other file of a pair
    closed: int = 0

    @property
    def triangles(self) -> int:
        return sum(int(c["triangles"]) for c in self.counts)

    @property
    def points(self) -> int:
        return sum(int(c["points"]) for c in self.counts)


class Meter:
    """The progress of the wireframes of a file inside its share. A reader that gives them one at a time tells
    through `reading` how much of the file is read (`lazy`): storing a wireframe then takes the second half of
    what its reading took. Wireframes that are in memory already have no reading left: `write_meshes` gives
    each its share by its triangles."""

    def __init__(self) -> None:
        self.lazy = False
        self.tick: Tick = lambda fraction, message: None
        self.base = self.seen = 0.0

    def start(self, tick: Tick, lazy: bool) -> None:
        self.tick, self.lazy = tick, lazy

    def _mid(self) -> float:
        return self.base + (0.5 if self.lazy else 0.0) * (self.seen - self.base)

    def reading(self, fraction: float, message: str | None = None) -> None:
        self.seen = max(self.seen, min(max(fraction, 0.0), 1.0))
        self.tick(self._mid(), message)

    def storing(self, fraction: float, message: str | None = None) -> None:
        mid = self._mid()
        self.tick(mid + (self.seen - mid) * min(max(fraction, 0.0), 1.0), message)

    def stored(self) -> None:
        self.base = self.seen


def has_meshes(read: Any) -> bool:
    """The file gives wireframes: a reader that has not started yet, or a list that is not empty."""
    return bool(read.meshes) if isinstance(read.meshes, list) else read.meshes is not None


def file_folder(base: str | None, name: str) -> str:
    """The folder of the tree for the wireframes of the file `name`: «folder of the wizard/name»."""
    own = " ".join(name.replace("/", " ").split())
    return "/".join(part for part in (base, own) if part)[:MAX_FOLDER]


def check_notes(mesh: Any, stats: Mapping[str, Any]) -> list[str]:
    """What the checks at loading found in a wireframe (`geo.meshes.mesh_stats` of its geometry)."""
    notes: list[str] = []
    if stats.get("degenerate"):
        notes.append(f"вырожденных треугольников (без площади) — {n_text(int(stats['degenerate']))}")
    if stats.get("nonmanifold_edges"):
        notes.append(f"рёбер, к которым сходятся три треугольника и более, — {n_text(int(stats['nonmanifold_edges']))}")
    if mesh.source.get("format") == "tridb":
        from bf_core.formats.tridb import check_measures, declared

        notes.extend(check_measures(mesh.source, stats))
        if declared(mesh.source)["volume"] is not None and not stats.get("closed"):
            notes.append(
                "в файле указан объём, но каркас не замкнут (открытых рёбер — "
                f"{n_text(int(stats.get('open_edges') or 0))}): объём не определён"
            )
    return notes


def _take(read: Any, meter: Meter) -> Generator[Any, None, None]:
    """The wireframes of a file one at a time; those of a drawing leave its list as they are taken."""
    meshes = read.meshes
    if not isinstance(meshes, list):
        yield from meshes
        return
    queue = deque(meshes)
    meshes.clear()
    total, done = max(sum(m.triangle_count for m in queue), 1), 0
    while queue:
        mesh = queue.popleft()
        done += mesh.triangle_count
        meter.reading(done / total)
        yield mesh


def drop(engine: sa.Engine, ids: Sequence[int]) -> None:
    """Remove wireframes this job was building (a file that failed half-way): the objects and their blob
    references; the files themselves go with the next cleanup of the storage."""
    with dialect.write_tx(engine) as conn:
        for a in range(0, len(ids), 500):
            part = list(ids[a : a + 500])
            own = sa.and_(blob_owners.c.owner_kind == "object", blob_owners.c.owner_id.in_(part))
            conn.execute(sa.delete(blob_owners).where(own))
            conn.execute(sa.delete(objects).where(objects.c.id.in_(part), objects.c.state == "building"))


def _companions(g: Group, named: set[str]) -> dict[str, Path]:
    """The other file of a pair (the `.str` of a Surpac DTM, the second table of a Datamine wireframe): the one
    chosen with the file, else the one the reader found next to it and `named` in the source of the wireframes."""
    out = {suffix: Path(p) for suffix, p in g.sidecars.items()}
    for name in sorted(named - {g.main.name}):
        path = g.main.parent / name
        if path.suffix.lower() not in out and path.is_file():
            out[path.suffix.lower()] = path
    return out


def write_meshes(
    ctx: JobContext,
    engine: sa.Engine,
    store: Any,
    g: Group,
    read: Any,
    meter: Meter,
    folder: str | None,
) -> Written:
    """The wireframes of a read file as `building` objects of this job (see the module docstring). Raises
    `FileFailed` when the file cannot be read to its end or gives no wireframe."""
    from bf_core.drillholes.edit import author
    from bf_core.formats.mesh_io import unique_name
    from bf_core.wireframes import source as src
    from bf_core.wireframes import store as ws

    label, who = g.main.name, author()
    meta = {"format": read.format, "label": read.label, "filename": label}
    out = Written()
    pending: list[tuple[Any, Any, list[str]]] = []  # (staged, mesh, its notes)
    named: set[str] = set()  # the files of a pair as the sources of the wireframes name them
    said = 0

    def flush() -> None:
        try:
            with dialect.write_tx(engine) as conn:
                names = sa.select(objects.c.name_key).where(objects.c.kind == ws.WIREFRAME)
                taken = {str(k) for k in conn.execute(names).scalars()}
                for staged, mesh, notes in pending:
                    name = unique_name(mesh.name, taken, ws.MAX_NAME)
                    wf_id = src.commit_mesh(
                        conn, store, staged, mesh, name=name, meta={**meta, "warnings": notes[:MAX_NOTES]},
                        folder=folder, state="building", job_id=ctx.id, author=who, sort_key=float(len(out.ids)),
                    )  # fmt: skip
                    out.ids.append(wf_id)
                    out.closed += bool(staged.stats.get("closed"))
                    out.counts.append(
                        {"kind": WIREFRAME, "name": name, "points": len(staged.xyz), "triangles": len(staged.tri)}
                    )
        finally:
            for staged, _mesh, _notes in pending:
                staged.discard()
            pending.clear()

    queue = _take(read, meter)
    try:
        while True:
            try:
                mesh = next(queue, None)
            except (FormatError, OSError) as exc:  # of the reader: the file ends here
                raise FileFailed(exc) from exc
            if mesh is None:
                break
            title = f"каркас «{mesh.name}»"

            def storing(fraction: float, message: str, title: str = title) -> None:
                meter.storing(fraction, f"{title}: {message}")

            staged = src.stage_mesh(store, mesh, progress=storing)
            notes = [*mesh.warnings, *check_notes(mesh, staged.stats)]
            pending.append((staged, mesh, notes))
            named.update(str(mesh.source[k]) for k in PAIRED.get(read.format, ()) if mesh.source.get(k))
            for note in notes[: max(MAX_NOTES - said, 0)]:
                ctx.log(f"{label}: {title}: {note}", "warn")
            said += len(notes)
            meter.stored()
            if len(pending) >= PORTION or sum(len(s.tri) for s, _m, _n in pending) >= PORTION_TRIANGLES:
                flush()
            del mesh, staged
        flush()
        if not out.ids:
            first = read.unread[0].error if read.unread else None
            raise FileFailed(
                FormatError(**first) if first else FormatError("no_meshes", "В файле нет каркасов.", "Проверьте файл.")
            )
    except BaseException:
        for staged, _mesh, _notes in pending:
            staged.discard()
        pending.clear()
        if out.ids:  # a cancel is cleaned by the scheduler as well; a failed file must leave nothing now
            drop(engine, out.ids)
        raise
    finally:
        queue.close()
    if said > MAX_NOTES:
        more = n_text(said - MAX_NOTES)
        ctx.log(f"{label}: и ещё замечаний по каркасам — {more} (они сохранены в свойствах каркасов)", "warn")
    for unread in read.unread:
        error = {k: str((unread.error or {}).get(k) or "") for k in ("code", "message", "hint")}
        out.skipped.append({"name": unread.name, **error})
        ctx.log(f"{label}: каркас «{unread.name}» не загружен: {error['message']} {error['hint']}".strip(), "error")
    if len(out.ids) > 1:
        place = file_folder(folder, label)
        with dialect.write_tx(engine) as conn:
            for a in range(0, len(out.ids), 500):
                conn.execute(sa.update(objects).where(objects.c.id.in_(out.ids[a : a + 500])).values(folder=place))
    out.companions = _companions(g, named)
    ctx.log(
        f"{label}: каркасов — {n_text(len(out.ids))} (замкнутых — {n_text(out.closed)}), "
        f"треугольников — {n_text(out.triangles)}, вершин — {n_text(out.points)}"
        + (f"; не загружено — {n_text(len(out.skipped))}" if out.skipped else ""),
        "info",
    )
    return out


def entry(label: str, fmt: str, written: Written) -> dict[str, Any]:
    """The entry of the result of the job for the wireframes of a file."""
    return {
        "file": label, "format": fmt, "kind": WIREFRAMES, "object_id": written.ids[0], "object_ids": list(written.ids),
        "wireframes": len(written.ids), "triangles": written.triangles, "mesh_points": written.points,
        "skipped": len(written.skipped),
    }  # fmt: skip


def log_meshes(
    conn: sa.Connection,
    written: Written,
    *,
    label: str,
    fmt: str,
    settings: Mapping[str, Any],
    sha: str,
    sides: Mapping[str, str],
    job_id: int,
    now: Any,
) -> dict[str, Any]:
    """In the final transaction of the job: a row of `import_log` per wireframe; each owns the source file
    `sha` and the companion files `sides` {role: sha256} (owner `import`). Returns the entry of the result."""
    from bf_core.storage.blobs import add_owners

    owners: list[tuple[str, str, int, str]] = []
    for wf_id, counts in zip(written.ids, written.counts, strict=True):
        res = conn.execute(
            sa.insert(import_log).values(
                object_id=wf_id, job_id=job_id, file_name=label, format=fmt, target=TARGET, mapping=dict(settings),
                counts=counts, sha256=sha, created_at=now,
            )  # fmt: skip
        )
        log_id = int(res.inserted_primary_key[0])  # type: ignore[index]
        owners.append((sha, "import", log_id, "source"))
        owners.extend((side, "import", log_id, role) for role, side in sides.items())
    add_owners(conn, owners)
    return entry(label, fmt, written)


def committed(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """The entries of the result from the rows of `import_log` an earlier attempt wrote (id order): the rows of
    the wireframes of one file — one entry."""
    out: list[dict[str, Any]] = []
    last: tuple[str, str | None] | None = None  # (file, sha256) of the wireframes entry that is still open
    for r in rows:
        counts = dict(r.counts or {})
        if counts.get("kind") != WIREFRAME:
            out.append({"file": r.file_name, "format": r.format, "object_id": r.object_id, **counts})
            last = None
            continue
        if last != (r.file_name, r.sha256):
            out.append(
                {"file": r.file_name, "format": r.format, "kind": WIREFRAMES, "object_id": r.object_id,
                 "object_ids": [], "wireframes": 0, "triangles": 0, "mesh_points": 0, "skipped": 0}
            )  # fmt: skip
            last = (r.file_name, r.sha256)
        found = out[-1]
        found["object_ids"].append(r.object_id)
        found["wireframes"] += 1
        found["triangles"] += int(counts.get("triangles") or 0)
        found["mesh_points"] += int(counts.get("points") or 0)
    return out
