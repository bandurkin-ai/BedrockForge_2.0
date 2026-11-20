"""/api/v1/composites — sample processing and ore intervals (ADR 0018 §11): the routes of the parts."""

from __future__ import annotations

from fastapi import APIRouter

from .routes_results import router as _results

router = APIRouter(prefix="/composites")
router.include_router(_results)
