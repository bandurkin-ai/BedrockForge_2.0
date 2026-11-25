"""Kinds of jobs the scheduler announces by their kind (not by `result.events`, which every job may carry)."""

from __future__ import annotations

#: Kinds of jobs that make lines, rasters or wireframes of a project, or what «Свойства» tell about them: their
#: end is an `objects.changed` event.
OBJECT_JOBS = frozenset(
    {
        "import.objects",
        "model.dtm",
        "wireframe.check",
        "ground.classify",
        "cloud.dz",
        "geometry.operation",
        "geometry.operation.heavy",
    }
)

#: Jobs whose end makes the levels of detail of wireframes ready: a `wireframes.lod` event.
LOD_JOBS = frozenset({"wireframe.lod", "wireframe.lod.heavy", "wireframe.check"})

__all__ = ["LOD_JOBS", "OBJECT_JOBS"]
