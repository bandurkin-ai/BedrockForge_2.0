"""Applying a saved view in the core (ТЗ 7.35 Д-09, ADR 0008 «Замеры»: POST /views/{id}/apply ≤ 50 мс): every
view key written in one transaction and read back normalized, with 2000 hidden keys and 200 views in the project."""

from __future__ import annotations

from pathlib import Path
from typing import Any

CAMERA = {
    "position": [5000.0, 8000.0, 650.0],
    "target": [5010.0, 8010.0, 300.0],
    "up": [0, 0, 1],
    "zoom": 1.0,
    "ortho": True,
    "fov": 45,
}


def _engine(work: Path) -> Any:
    from bf_core.db import dialect

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    return dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.db import dialect
    from bf_core.drillholes.store import create_set
    from bf_core.projects.project import create_folder
    from bf_core.viewstate import views
    from bf_core.viewstate.service import write

    folder = create_folder(work, "Виды", {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    engine = _engine(work)
    try:
        with dialect.write_tx(engine) as conn:
            ids = [create_set(conn, f"Набор {i}") for i in range(int(params["sets"]))]
            write(
                conn,
                "hidden",
                [f"set:{i}:{p}" for i in ids for p in ("collars", "paths")][:2000],
                0,
            )
            write(
                conn,
                "hide_all",
                {"on": True, "shown": [f"set:{i}" for i in ids[:1000]]},
                0,
            )
            write(conn, "scene", {"vexag": 2}, 0)
            for _ in range(int(params["views"])):
                views.create(conn, CAMERA)
    finally:
        engine.dispose()


def load() -> None:
    import bf_core.viewstate.views  # noqa: F401


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    from bf_core.db import dialect
    from bf_core.viewstate import views

    engine = _engine(work)
    try:
        with dialect.read_tx(engine) as conn:
            view_id = views.list_views(conn)[-1]["id"]
        t0 = time.perf_counter()
        with dialect.write_tx(engine) as conn:
            out = views.apply(conn, view_id)
        apply_s = time.perf_counter() - t0
    finally:
        engine.dispose()
    return {
        "apply_s": round(apply_s, 4),
        "hidden": len(out["values"]["hidden"]["value"]),
    }
