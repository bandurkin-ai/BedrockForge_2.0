"""Graph of dependencies (ADR 0025 «Производительность», ТЗ 7.35 Д-35, подшаг 18.Г2): `lineage.status` of 2000 nodes of
depth 6 ≤ 50 ms (a section of the tree opened at once) and `lineage.impact` of a source with 500 dependents ≤ 30 ms.
The calls are those of the routes `/lineage/status` and `/lineage/impact` (one read transaction each, a warm process:
the first call compiles the statements, the tree asks again at every change). Times — the median of 5."""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

KIND = "model_stub"  # an object of no module: the generic provider (fingerprint — the version)
REPEAT = 5


def _engine(work: Path) -> Any:
    from bf_core.db import dialect

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    return dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))


def _objects(conn: Any, names: list[str]) -> list[int]:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import objects

    now = dialect.utcnow()
    rows = [
        {"kind": KIND, "name": n, "name_key": dialect.name_key(n), "sort_key": 0.0, "state": "ready", "version": 1,
         "meta": {}, "created_at": now, "updated_at": now}
        for n in names
    ]  # fmt: skip
    conn.execute(sa.insert(objects), rows)
    ids = conn.execute(sa.select(objects.c.id).where(objects.c.name.in_(names)).order_by(objects.c.id)).scalars()
    return [int(i) for i in ids]


def _link(conn: Any, dst: int, srcs: list[int], names: dict[int, str]) -> None:
    from bf_core import lineage
    from bf_core.lineage.store import InputRef, Node

    k = f"object:{KIND}"
    lineage.record(conn, Node(k, dst), [InputRef(Node(k, s), "1", names[s]) for s in srcs])


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.db import dialect
    from bf_core.projects.project import create_folder

    levels, width = int(params["levels"]), int(params["width"])
    fan, fan_levels = int(params["fan"]), int(params["fan_levels"])
    folder = create_folder(work, "Граф", {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    engine = _engine(work)
    try:
        with dialect.write_tx(engine) as conn:
            names: dict[int, str] = {}
            grid: list[list[int]] = []
            for lv in range(levels):  # the chain: every node reads two of the level above
                ids = _objects(conn, [f"G{lv}-{i}" for i in range(width)])
                names.update({i: f"G{lv}-{n}" for n, i in enumerate(ids)})
                grid.append(ids)
            for lv in range(1, levels):
                for n, oid in enumerate(grid[lv]):
                    _link(conn, oid, [grid[lv - 1][n], grid[lv - 1][(n + 1) % width]], names)
            src = _objects(conn, ["Источник"])[0]
            names[src] = "Источник"
            prev = [src] * fan  # the source feeds `fan` nodes, each of them a chain of `fan_levels`
            for lv in range(fan_levels):
                ids = _objects(conn, [f"F{lv}-{i}" for i in range(fan)])
                names.update({i: f"F{lv}-{n}" for n, i in enumerate(ids)})
                for oid, parent in zip(ids, prev, strict=True):
                    _link(conn, oid, [parent], names)
                prev = ids
        (work / "nodes.txt").write_text(
            ",".join(str(i) for level in grid for i in level) + "\n" + str(src), encoding="utf-8"
        )
    finally:
        engine.dispose()


def load() -> None:
    from bf_core import lineage  # noqa: F401  # the providers and their modules are imported outside the timing


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core import lineage
    from bf_core.db import dialect
    from bf_core.lineage.store import Node

    first, last = (work / "nodes.txt").read_text(encoding="utf-8").splitlines()
    k = f"object:{KIND}"
    nodes = [Node(k, int(i)) for i in first.split(",")]
    src = Node(k, int(last))
    engine = _engine(work)
    try:
        with dialect.read_tx(engine) as conn:
            lineage.status(conn, nodes[:20])  # the first call compiles the statements
            lineage.impact(conn, src)
            status_s: list[float] = []
            impact_s: list[float] = []
            states: set[str] = set()
            impact: dict[str, Any] = {}
            for _ in range(REPEAT):
                t0 = time.perf_counter()
                got = lineage.status(conn, nodes)
                status_s.append(time.perf_counter() - t0)
                t0 = time.perf_counter()
                impact = lineage.impact(conn, src)
                impact_s.append(time.perf_counter() - t0)
                states = {s.state for s in got.values()}
    finally:
        engine.dispose()
    expected = int(params["fan"]) * int(params["fan_levels"])
    wrong = int(states != {lineage.CURRENT}) + int(impact["total"] != expected)
    return {"nodes": len(nodes), "dependents": impact["total"], "status_s": round(statistics.median(status_s), 4),
            "impact_s": round(statistics.median(impact_s), 4), "wrong": wrong}  # fmt: skip
