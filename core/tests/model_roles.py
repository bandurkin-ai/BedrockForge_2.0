"""Test-only: the roles of the files of an implicit model for a worker (`BF_EXTRA_JOB_MODULES=tests.model_roles`) until
`db/schema.py` has them (the line of the orchestrator): imported by the worker, it registers them; a no-op after."""

from bf_core.db import schema
from bf_core.model.store_base import BLOB_ROLES

if not set(BLOB_ROLES) <= schema.BLOB_ROLES:
    schema.BLOB_ROLES = schema.BLOB_ROLES | set(BLOB_ROLES)
