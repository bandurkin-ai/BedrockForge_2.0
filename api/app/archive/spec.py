"""What a project archive holds: the tables, in the order they are restored, and how they hang
together. A new table must be listed either in TABLES or in SKIPPED (tests/test_project_archive.py)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Table:
    name: str
    # (column, parent table); None for the project itself.
    parent: tuple[str, str] | None
    # "serial": an integer key from the table's sequence; "hex": a 32-character random string.
    key: str = "serial"


TABLES: tuple[Table, ...] = (
    Table("project", None),
    Table("dataset", ("project_id", "project")),
    Table("style", ("project_id", "project")),
    Table("assistant_chat", ("project_id", "project"), key="hex"),
    Table("wireframe", ("dataset_id", "dataset")),
    Table("line_layer", ("dataset_id", "dataset")),
    Table("line_layer_version", ("layer_id", "line_layer")),
    Table("raster", ("dataset_id", "dataset")),
    Table("collar", ("set_id", "dataset")),
    Table("survey", ("set_id", "dataset")),
    Table("assay", ("set_id", "dataset")),
    Table("component", ("set_id", "dataset")),
    Table("interval_table", ("set_id", "dataset")),
    Table("interval_row", ("table_id", "interval_table")),
    Table("import_job", ("set_id", "dataset")),
    Table("audit_run", ("set_id", "dataset")),
    Table("audit_finding", ("run_id", "audit_run")),
    Table("chart", ("dataset_id", "dataset")),
    Table("data_edit", ("set_id", "dataset")),
)

# Not part of a project archive: transient state, the spending ledger, the store's registry
# (rebuilt from the files of the archive) and the migration marker.
SKIPPED = frozenset({"upload", "job", "assistant_usage", "blob", "alembic_version"})

BY_NAME = {t.name: t for t in TABLES}

# Tables whose `blobs` JSONB column references stored files (as storage/gc.py OWNERS).
BLOB_OWNERS = ("dataset", "wireframe", "line_layer", "line_layer_version", "raster")

# id maps returned by an import (for the browser settings and the tests); the big row tables are
# left out on purpose.
RETURNED_MAPS = ("dataset", "chart", "wireframe", "line_layer", "raster", "interval_table", "style")


def scope_sql(table: str, project_id: int) -> str:
    """A WHERE clause selecting the rows of `table` that belong to the project."""
    t = BY_NAME[table]
    if t.parent is None:
        return f"id = {int(project_id)}"
    column, parent = t.parent
    if parent == "project":
        return f"{column} = {int(project_id)}"
    return f"{column} IN (SELECT id FROM {parent} WHERE {scope_sql(parent, project_id)})"
