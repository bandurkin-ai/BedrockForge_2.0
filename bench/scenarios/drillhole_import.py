"""Import of an assay table (ТЗ 7.7 «5 млн проб без нехватки памяти»; ADR 0003: 5 million rows ≤ 90 s).

CSV with hole, from, to and three grades → read, normalise, write into a project database in portions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HOLE_ROWS = 100  # samples per hole


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.drillholes.store import create_set
    from bf_core.projects.project import create_folder

    n = int(params["rows"])
    rng = np.random.default_rng(7)
    holes = np.arange(n) // HOLE_ROWS
    depth = (np.arange(n) % HOLE_ROWS).astype(float)
    grades = rng.lognormal(0.0, 1.0, size=(n, 3)).round(3)
    with open(work / "assay.csv", "w", encoding="utf-8") as fh:
        fh.write("HOLE_ID,FROM,TO,AU_PPM,AG_PPM,CU_PCT\n")
        step = 200_000
        for start in range(0, n, step):
            stop = min(n, start + step)
            fh.writelines(
                f"DH{holes[i]:06d},{depth[i]:.1f},{depth[i] + 1:.1f},{grades[i, 0]},{grades[i, 1]},{grades[i, 2]}\n"
                for i in range(start, stop)
            )
    folder = create_folder(work, "Замер", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    with dialect.write_tx(engine) as conn:
        create_set(conn, "Набор")
    engine.dispose()
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")


def load() -> None:
    import bf_core.drillholes.store  # noqa: F401
    import bf_core.formats.registry  # noqa: F401
    import bf_core.importing.service  # noqa: F401


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import objects
    from bf_core.drillholes.store import write_table
    from bf_core.formats.registry import read_table
    from bf_core.importing.service import normalize, suggest_components, suggest_mapping

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    table = read_table(work / "assay.csv")
    mapping = suggest_mapping("assay", table.names)
    norm, issues = normalize(
        table, "assay", mapping, suggest_components(table, mapping), table_name="Пробы"
    )
    assert norm is not None and norm.table is not None, issues
    del table
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.read_tx(engine) as conn:
            set_id = conn.execute(sa.select(objects.c.id)).scalar_one()
        res = write_table(
            engine, set_id, norm.table, job_id=None, tick=lambda _f, _m: None
        )
    finally:
        engine.dispose()
    return {"rows": res["rows"]}
