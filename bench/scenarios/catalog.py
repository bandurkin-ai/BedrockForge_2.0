"""The catalog of «Менеджер данных» (ADR 0017 «Замеры»: «Каталог — 1 000 объектов: ядро ≤ 50 мс; перенос 1 000 в
папку ≤ 0,5 с»; ТЗ 7.35 Д-20; `bf_core/projects/catalog.py`). The list of 1 000 objects in the window is not in the
E2E of 11.19: this scenario stands for it on the side of the core.

A project of `objects` data objects, made in `prepare` as rows of its database (the catalog reads no blob file),
in the mix of a working project: 40 % wireframes (every fifth a DTM; a quarter of them small — their geometry is an
entry of a pack `pack:<n>` 20 wireframes share; every one with the source file 10 wireframes of one TRIDB share),
30 % line files of 4 layers with 3 blobs each (every tenth a cloud — one layer of 4 columns), 10 % drillhole sets
(30 holes, 2 surveys each, 2 tables, 4 imports of their files), 10 % rasters (the source and 2 display copies), 10 %
block models (the cells; the OMF of 5 models shared); two thirds in folders of the user two levels deep, the
rest in the root; Russian names; the coordinate system on half of them.
Stages, seconds (the median of `REPEAT` calls):
  page_s     what «Менеджер данных» asks first, `GET /catalog?offset=0&limit=2000` (`ui/src/modules/datamanager/
             api.ts`): `catalog.page` in its read transaction, as the router calls it, and its answer in JSON;
  api_s      the same request by HTTP to the router of the core (`TestClient`): the parameters, the answer;
  sorted_s   sorted by the size, descending; search_s — the folder «Участок 1» and its folders with a search;
  folders_s  `GET /catalog/folders`;
  move_s     `catalog.move` of every object into one folder in its write transaction (`POST /catalog/move`);
             the folders are put back after it, outside the timing (the runs of `--repeat` see the same project).
What is checked (a failure stops the run): every object is a row, the kinds of the rows (DTM, clouds), the size of
every row — the bytes of its blobs, a shared one by the share of its owners, as computed in `prepare` — and the
rows of every drillhole set; the folders and their counts; after the move — every object in the new folder.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

REPEAT = 5
CRS = "EPSG:32637"
PAGE = {"offset": 0, "limit": 2000}  # `PAGE` of `ui/src/modules/datamanager/api.ts`
MOVED = "Перенесённые/Все объекты"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _folder(i: int) -> str | None:
    if i % 3 == 0:
        return None
    top = f"Участок {i % 7 + 1}"
    return f"{top}/Борт {i % 4 + 1}" if i % 2 else top


class _Rows:
    """The rows of the project and, as they are added, the size and the rows each catalog row must show."""

    def __init__(self, now: Any) -> None:
        self.now = now
        self.objects: list[dict[str, Any]] = []
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.blob_size: dict[str, int] = {}
        self.owners: dict[str, list[int]] = {}  # blob → the catalog object of each of its owners
        self.next_id = {"object": 1, "import": 1, "hole": 1, "survey": 1, "table": 1}
        self.kind: dict[int, str] = {}
        self.set_rows: dict[int, int] = {}

    def new(self, what: str) -> int:
        n = self.next_id[what]
        self.next_id[what] = n + 1
        return n

    def add(self, table: str, row: dict[str, Any]) -> None:
        self.tables.setdefault(table, []).append(row)

    def obj(self, kind: str, name: str, folder: str | None, meta: dict[str, Any], *, parent: int | None = None,
            crs: str | None = None) -> int:  # fmt: skip
        oid = self.new("object")
        self.objects.append({
            "id": oid, "parent_id": parent, "kind": kind, "name": name, "name_key": name.casefold(), "folder": folder,
            "sort_key": float(oid), "state": "ready", "version": 1, "meta": meta, "crs": crs,
            "created_at": self.now, "updated_at": self.now,
        })  # fmt: skip
        return oid

    def blob(self, key: str, size: int) -> str:
        sha = _sha(key)
        if sha not in self.blob_size:
            self.blob_size[sha] = size
            self.add("blobs", {"sha256": sha, "size": size, "media_type": "application/octet-stream",
                               "created_at": self.now})  # fmt: skip
        return sha

    def own(self, sha: str, owner_kind: str, owner_id: int, role: str, of: int) -> None:
        """`of` — the catalog object the owner counts for (the object, the line file of a layer, the object
        of an import)."""
        self.add("blob_owners", {"sha256": sha, "owner_kind": owner_kind, "owner_id": owner_id, "role": role})
        self.owners.setdefault(sha, []).append(of)

    def imported(self, oid: int, file_name: str, fmt: str, source: str | None, target: str = "objects") -> int:
        log = self.new("import")
        self.add("import_log", {"id": log, "object_id": oid, "job_id": None, "file_name": file_name, "format": fmt,
                                "target": target, "mapping": {}, "counts": {}, "sha256": source,
                                "created_at": self.now})  # fmt: skip
        if source is not None:
            self.own(source, "import", log, "source", oid)
        return log

    def sizes(self) -> dict[int, int]:
        out: dict[int, float] = {}
        for sha, of in self.owners.items():
            for oid in of:
                out[oid] = out.get(oid, 0.0) + self.blob_size[sha] / len(of)
        return {k: int(round(v)) for k, v in out.items()}


def _wireframe(r: _Rows, i: int, k: int) -> None:
    dtm = k % 5 == 4
    name = f"{'ЦТМ' if dtm else 'Каркас'} {k + 1:04d}"
    oid = r.obj("wireframe", name, _folder(i), {"v": 1}, crs=CRS if i % 2 else None)
    r.kind[oid] = "dtm" if dtm else "wireframe"
    points, triangles = 2_000 + 37 * k, 4_000 + 71 * k
    r.add("wireframes", {"object_id": oid, "point_count": points, "triangle_count": triangles,
                         "origin": [512_000.0, 6_234_000.0, 0.0], "stats": {}, "attrs": {},
                         "dtm": {"source": "points"} if dtm else None})  # fmt: skip
    if k % 4 == 0:  # a small one: an entry of a pack of 20
        r.own(r.blob(f"pack:{k // 80}", 3_000_000 + k), "object", oid, f"pack:{k // 80}", oid)
    else:
        r.own(r.blob(f"wf{k}:xyz", 24 * points), "object", oid, "xyz", oid)
        r.own(r.blob(f"wf{k}:tri", 12 * triangles), "object", oid, "tri", oid)
    r.imported(oid, f"C:\\Данные\\Каркасы\\Тело {k // 10 + 1}.tridb", "tridb",
               r.blob(f"tridb{k // 10}", 50_000_000 + 1_000 * k))  # fmt: skip


def _line_file(r: _Rows, i: int, k: int) -> None:
    cloud = k % 10 == 9
    name = f"{'Облако' if cloud else 'Линии'} {k + 1:04d}.{'las' if cloud else 'str'}"
    oid = r.obj("line_file", name, _folder(i), {"v": 1}, crs=CRS if i % 2 else None)
    r.kind[oid] = "cloud" if cloud else "line_file"
    r.own(r.blob(f"lf{k}:source", 400_000 + 13 * k), "object", oid, "source", oid)
    r.imported(oid, f"D:\\Съёмка\\{name}", "las" if cloud else "str", None)
    for n in range(1 if cloud else 4):
        layer = r.obj("line_layer", f"Слой {n + 1}", None, {"cloud": {"points": 10_000}} if cloud else {},
                      parent=oid)  # fmt: skip
        r.add("line_layers", {"object_id": layer, "line_count": 0 if cloud else 50, "point_count": 10_000,
                              "length": 1_000.0, "closed_count": 0, "flat": False, "color": "#8a99a8",
                              "attr_schema": [], "points_only": cloud, "origin": [512_000.0, 6_234_000.0, 0.0]})
        for role in ("x", "y", "z", "class") if cloud else ("xyz", "offsets", "closed"):
            r.own(r.blob(f"lf{k}:{n}:{role}", 80_000 + 7 * k + n), "object", layer, role, oid)


def _drillhole_set(r: _Rows, i: int, k: int) -> None:
    oid = r.obj("drillhole_set", f"Скважины {k + 1:03d}", _folder(i), {"v": 1}, crs=CRS if i % 2 else None)
    r.kind[oid] = "drillhole_set"
    holes = 30
    for h in range(holes):
        hid = r.new("hole")
        r.add("dh_holes", {"id": hid, "set_id": oid, "name": f"СКВ-{k:03d}-{h:03d}", "x": 512_000.0 + h,
                           "y": 6_234_000.0 + k, "z": 300.0, "td": 120.0, "has_collar": True})  # fmt: skip
        for d in (0.0, 60.0):
            r.add("dh_surveys", {"id": r.new("survey"), "set_id": oid, "hole_id": hid, "depth": d, "azimuth": 45.0,
                                 "dip": -60.0})  # fmt: skip
    counts = (3_000 + k, 500 + k)
    for name, n in zip(("Пробы", "Литология"), counts, strict=True):
        r.add("dh_tables", {"id": r.new("table"), "set_id": oid, "name": name, "name_key": name.casefold(),
                            "kind": "assay" if name == "Пробы" else "interval", "row_count": n, "rev": 1,
                            "state": "ready", "created_at": r.now, "updated_at": r.now})  # fmt: skip
    r.set_rows[oid] = holes + 2 * holes + sum(counts)
    for file_name, target in (("Устья.csv", "collars"), ("Инклинометрия.txt", "surveys"), ("Пробы.csv", "assay"),
                              ("Литология.xlsx", "interval")):  # fmt: skip
        r.imported(oid, f"C:\\Скважины\\{k}\\{file_name}", file_name.rsplit(".", 1)[1],
                   r.blob(f"dh{k}:{file_name}", 200_000 + 3 * k), target)  # fmt: skip


def _raster(r: _Rows, i: int, k: int) -> None:
    oid = r.obj("raster", f"План {k + 1:03d}.tif", _folder(i), {"v": 1}, crs=CRS if i % 2 else None)
    r.kind[oid] = "raster"
    r.add("rasters", {"object_id": oid, "width": 4096, "height": 3072, "georef": None, "elevation": None,
                      "vertical": False, "opacity": 1.0, "version": 1})  # fmt: skip
    r.own(r.blob(f"ras{k}:source", 12_000_000 + k), "object", oid, "source", oid)
    for n in range(2):
        r.own(r.blob(f"ras{k}:display{n}", 3_000_000 // (n + 1) + k), "object", oid, f"display:{n}", oid)
    r.imported(oid, f"E:\\Планы\\План {k + 1:03d}.tif", "tif", None)


def _block_model(r: _Rows, i: int, k: int) -> None:
    cells = 1_000_000 + 1_000 * k
    meta = {"v": 1, "cells": cells, "grid": {"counts": [100, 100, 100 + k]}, "fields": [{"name": "Au"}]}
    oid = r.obj("block_model", f"Модель {k + 1:03d}", _folder(i), meta, crs=CRS if i % 2 else None)
    r.kind[oid] = "block_model"
    r.own(r.blob(f"bm{k}:cells", 16 * cells), "object", oid, "cells", oid)
    r.imported(oid, f"C:\\Модели\\Модели {k // 5 + 1}.omf", "omf", r.blob(f"omf{k // 5}", 90_000_000 + k))


MIX = ((0.4, _wireframe), (0.3, _line_file), (0.1, _drillhole_set), (0.1, _raster), (0.1, _block_model))


def build(n: int, now: Any) -> _Rows:
    import random

    r = _Rows(now)
    order = [make for share, make in MIX for _ in range(round(share * n))]
    assert len(order) == n, len(order)
    random.Random(11).shuffle(order)  # the kinds mixed in the order of the ids, as loaded over time
    seen: dict[Any, int] = {}
    for i, make in enumerate(order):
        make(r, i, seen.get(make, 0))
        seen[make] = seen.get(make, 0) + 1
    return r


def prepare(params: dict[str, Any], work: Path) -> None:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db import schema as s
    from bf_core.projects.project import create_folder

    folder = create_folder(work, "Замер каталога", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    r = build(int(params["objects"]), dialect.utcnow())
    try:
        with dialect.write_tx(engine) as conn:
            conn.execute(sa.insert(s.objects), r.objects)
            for table in ("wireframes", "line_layers", "rasters", "dh_holes", "dh_surveys", "dh_tables", "blobs",
                          "blob_owners", "import_log"):  # fmt: skip
                conn.execute(sa.insert(getattr(s, table)), r.tables[table])
        dialect.checkpoint(engine)
    finally:
        engine.dispose()
    top = [o for o in r.objects if o["kind"] != "line_layer"]
    want = {
        "folder": str(folder), "kind": {str(k): v for k, v in r.kind.items()},
        "size": {str(k): v for k, v in r.sizes().items()}, "rows": {str(k): v for k, v in r.set_rows.items()},
        "folders": {o["id"]: o["folder"] for o in top},
    }  # fmt: skip
    (work / "want.json").write_text(json.dumps(want, ensure_ascii=False), encoding="utf-8")


def load() -> None:
    from fastapi import FastAPI  # noqa: F401
    from fastapi.testclient import TestClient  # noqa: F401

    import bf_core.projects.catalog  # noqa: F401
    import bf_core.projects.catalog_router  # noqa: F401


# --- the stages ---------------------------------------------------------------------------------------------


def _median(call: Any) -> tuple[float, Any]:
    times, got = [], None
    for _ in range(REPEAT):
        t0 = time.perf_counter()
        got = call()
        times.append(time.perf_counter() - t0)
    return round(statistics.median(times), 4), got


class _Project:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def require_current(self, writable: bool = False) -> _Project:
        return self


def _client(engine: Any) -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bf_core.projects.catalog_router import router
    from bf_core.projects.service import projects

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[projects] = lambda: _Project(engine)
    return TestClient(app, base_url="http://127.0.0.1")


def _check_rows(rows: list[dict[str, Any]], want: dict[str, Any]) -> int:
    """Rows that differ from the project as made: their kind, size, drillhole rows, folder."""
    bad = abs(len(rows) - len(want["kind"]))
    for row in rows:
        oid = str(row["id"])
        bad += row["kind"] != want["kind"].get(oid)
        bad += row["size"] != want["size"].get(oid, 0)
        rows = want["rows"].get(oid)  # null for every kind but a drillhole set
        bad += row["rows"] != rows
        bad += row["folder"] != want["folders"].get(oid)
    return int(bad)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import objects
    from bf_core.projects import catalog

    want = json.loads((work / "want.json").read_text(encoding="utf-8"))
    n = len(want["kind"])
    engine = dialect.create_engine(dialect.sqlite_url(str(Path(want["folder"]) / "project.sqlite")))
    out: dict[str, Any] = {"objects": n}
    try:

        def page(**query: Any) -> Any:
            with dialect.read_tx(engine) as conn:
                return json.dumps(catalog.page(conn, **{**PAGE, **query}), ensure_ascii=False)

        out["page_s"], text = _median(page)
        first = json.loads(text)
        out["mismatches"] = _check_rows(first["rows"], want) + abs(first["total"] - n)
        with _client(engine) as c:
            out["api_s"], r = _median(lambda: c.get("/catalog", params=PAGE))
            assert r.status_code == 200, r.text
            out["mismatches"] += int(r.json() != first)
        out["sorted_s"], text = _median(lambda: page(sort="size", desc=True))
        sizes = [row["size"] for row in json.loads(text)["rows"]]
        out["mismatches"] += int(sizes != sorted(sizes, reverse=True))
        out["search_s"], text = _median(lambda: page(folder="Участок 1", q="1"))
        found = json.loads(text)["rows"]
        out["mismatches"] += sum(not (row["folder"] or "").startswith("Участок 1") for row in found)
        out["found"] = len(found)

        def folders() -> Any:
            with dialect.read_tx(engine) as conn:
                return catalog.folders(conn)

        out["folders_s"], got = _median(folders)
        counted: dict[str, int] = {}
        for f in want["folders"].values():
            if f is not None:
                counted[f] = counted.get(f, 0) + 1
        out["mismatches"] += int({f["folder"]: f["count"] for f in got["folders"]} != counted)
        out["folders"] = len(got["folders"])

        ids = [int(k) for k in want["kind"]]
        t0 = time.perf_counter()
        with dialect.write_tx(engine) as conn:
            moved = catalog.move(conn, ids, MOVED)
        out["move_s"] = round(time.perf_counter() - t0, 4)
        with dialect.read_tx(engine) as conn:
            there = conn.execute(
                sa.select(sa.func.count()).select_from(objects).where(objects.c.folder == MOVED)
            ).scalar_one()
        out["mismatches"] += abs(sum(len(v) for v in moved.values()) - n) + abs(int(there) - n)
        with dialect.write_tx(engine) as conn:  # back where they were: the next run sees the same project
            for oid, f in want["folders"].items():
                conn.execute(sa.update(objects).where(objects.c.id == int(oid)).values(folder=f))
    finally:
        engine.dispose()
    assert out["mismatches"] == 0, out
    return out
