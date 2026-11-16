"""Alembic environment of the project database. Run through bf_core.db.migrate (the connection is passed in)."""

from alembic import context

from bf_core.db.migrate import run_migrations

run_migrations(context, "project")
