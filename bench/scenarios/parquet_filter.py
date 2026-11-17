"""Block model query: filter + aggregate on Parquet with DuckDB. ТЗ 5.3 (статистика БМ)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare(params: dict[str, Any], work: Path) -> None:
    from tools.gen.blockmodel import generate

    generate(
        work / "bm.parquet", int(params["nx"]), int(params["ny"]), int(params["nz"])
    )


def load() -> None:
    import duckdb  # noqa: F401


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import duckdb

    con = duckdb.connect()
    row = con.execute(
        "SELECT count(*), sum(AU * DENSITY) / sum(DENSITY) FROM read_parquet(?) WHERE AU >= 0.5",
        [str(work / "bm.parquet")],
    ).fetchone()
    con.close()
    assert row is not None
    return {"blocks_above": int(row[0]), "grade": float(row[1])}
