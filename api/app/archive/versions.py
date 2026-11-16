"""Schema versions of archives.

An archive records the Alembic revision of the database it was written from. On import the rows
first land in staging tables shaped like the current tables (columns an older archive lacks get
their defaults; columns it has and the current schema dropped are added as text), then the
statements of UPGRADES run for every revision after the archive's, in order. Each statement may
refer to the staging copy of a table as `stg_<table>`.

A migration that changes a table of the archive (archive/spec.py) must add its revision here,
with the statements that bring older archives up to it (an empty list when none are needed);
tests/test_project_archive.py fails until it does.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# The first revision archives can be written from.
FIRST_REVISION = "0016"

UPGRADES: dict[str, list[str]] = {
    "0016": [],
}

_API_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def revisions() -> tuple[str, ...]:
    """Every revision from the first to the head, oldest first."""
    from alembic.config import Config  # imported here: loading alembic is slow and talkative
    from alembic.script import ScriptDirectory

    config = Config(str(_API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_API_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    return tuple(reversed([r.revision for r in script.walk_revisions()]))


def head() -> str:
    return revisions()[-1]


def upgrade_path(archive_revision: str) -> list[str]:
    """Revisions to apply after `archive_revision`; raises ValueError for an unknown (newer) one."""
    revs = revisions()
    if archive_revision not in revs:
        raise ValueError(archive_revision)
    return list(revs[revs.index(archive_revision) + 1:])
