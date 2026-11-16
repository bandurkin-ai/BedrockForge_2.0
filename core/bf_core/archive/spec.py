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
}

#: (table, kind column, id column) → {kind value: table}. Rows of unknown kinds are dropped.
KIND_COLUMNS: dict[tuple[str, str, str], dict[str, str]] = {
    ("blob_owners", "owner_kind", "owner_id"): {"object": "objects"},
    ("edit_log", "scope_kind", "scope_id"): {"object": "objects"},
}

#: id maps returned by an import (ТЗ 7.4 «результат содержит соответствие старых и новых id»).
RETURNED_MAPS = ("objects",)
