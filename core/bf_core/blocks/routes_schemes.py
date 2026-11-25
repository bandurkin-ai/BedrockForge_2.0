"""/api/v1/blocks/schemes — the schemes of categories of the project (ТЗ 8.7.5; ADR 0023 §9, № 68; e17-api.md §9).

The router has no prefix of its own: `blocks/router.py` includes it under `/blocks`, BEFORE its `/{bm_id}` routes (the
path `/blocks/schemes` must not be taken for a model number).

  * `GET /blocks/schemes` → {schemes: [{id, name, version, rules: [{label, expr | null}]}]}: `jorc` (the example of
    8.7.5) and `gkz` (the template A, B, C1, C2 with no thresholds) are always there, then the user's own schemes;
  * `PUT /blocks/schemes/{id}` {expected_version?, name, rules} → the scheme: a new one has no version, an existing one
    (a default too) needs its current `expected_version`; 409 `version_conflict {version}`; 422 `bad_param {param}`,
    `expr_syntax {param: "rules.N.expr", pos, …}`; 423 `project_read_only`.
Plain `def` routes run in the thread pool.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import schemes as sc

router = APIRouter(prefix="/schemes")
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]


@router.get("")
def list_schemes(svc: Svc) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"schemes": sc.schemes(conn)}


@router.put("/{scheme_id}")
def put_scheme(svc: Svc, scheme_id: str, body: Payload) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        return sc.put(conn, scheme_id, body)
