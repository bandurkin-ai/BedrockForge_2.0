"""What a BF2 `.bfggis` holds (ТЗ 7.4) and how ids are renumbered on import.

Every project table is listed in TABLES (restored, in this order) or SKIPPED — tests/test_archive.py fails
otherwise. Columns holding ids of other rows are listed in ID_COLUMNS; owner/scope pairs in KIND_COLUMNS.
"""

from __future__ import annotations

from dataclasses import dataclass

APP = "bf2"


@dataclass(frozen=True)
class Table:
    name: str
    #: The table has an integer key `id` that is renumbered (else rows are copied with remapped references).
    renumber: bool = False
    #: Optional (ТЗ 7.4 «По желанию — настройки вида»).
    view: bool = False


TABLES: tuple[Table, ...] = (
    Table("objects", renumber=True),
    Table("object_refs"),
    Table("dh_holes", renumber=True),
    Table("dh_surveys"),  # nobody refers to readings or interval rows: copied in portions, no id map
    Table("dh_tables", renumber=True),
    Table("dh_columns", renumber=True),
    Table("dh_rows"),
    Table("import_log", renumber=True),
    Table("export_log", renumber=True),
    Table("audit_runs", renumber=True),
    Table("audit_findings"),
    Table("blob_owners"),
    Table("edit_log", renumber=True),
    Table("settings", view=True),
)

#: project_meta is written from the manifest; jobs are not part of a project archive (ТЗ 7.4); blobs are
#: rebuilt from the files of the archive; the migration marker belongs to the target database.
SKIPPED = frozenset({"project_meta", "jobs", "job_log", "blobs", "alembic_version"})

#: (table, column) → table whose renumbered ids it holds. None: the reference is dropped (set NULL).
ID_COLUMNS: dict[tuple[str, str], str | None] = {
    ("objects", "parent_id"): "objects",
    ("objects", "job_id"): None,
    ("object_refs", "src_id"): "objects",
    ("object_refs", "dst_id"): "objects",
    ("dh_holes", "set_id"): "objects",
    ("dh_surveys", "set_id"): "objects",
    ("dh_surveys", "hole_id"): "dh_holes",
    ("dh_tables", "set_id"): "objects",
    ("dh_tables", "job_id"): None,
    ("dh_columns", "table_id"): "dh_tables",
    ("dh_rows", "table_id"): "dh_tables",
    ("dh_rows", "hole_id"): "dh_holes",
    ("import_log", "object_id"): "objects",
    ("import_log", "job_id"): None,
    ("export_log", "object_id"): "objects",
    ("export_log", "job_id"): None,
    ("audit_runs", "set_id"): "objects",
    ("audit_runs", "job_id"): None,
    ("audit_runs", "table_id"): "dh_tables",
    ("audit_findings", "run_id"): "audit_runs",
}

#: (table, kind column, id column) → {kind value: table}. Rows of unknown kinds are dropped.
KIND_COLUMNS: dict[tuple[str, str, str], dict[str, str]] = {
    ("blob_owners", "owner_kind", "owner_id"): {"object": "objects", "import": "import_log"},
    ("edit_log", "scope_kind", "scope_id"): {"object": "objects"},
}

#: id maps returned by an import (ТЗ 7.4 «результат содержит соответствие старых и новых id»).
RETURNED_MAPS = ("objects", "dh_tables")
