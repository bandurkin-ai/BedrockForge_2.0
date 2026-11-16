"""Diagnostic job kinds: the scheduler's own tests and the selftest of a build. Not shown in the ribbon."""

from __future__ import annotations

import os
import time
from typing import Any

from .context import JobContext, JobError
from .registry import handler


@handler("diag.sleep", "Проверка заданий", queue="heavy")
def sleep(ctx: JobContext) -> dict[str, Any]:
    """Ticks `steps` times every `step_s`; `crash_at` kills the process at that step on the first attempts."""
    steps = int(ctx.params.get("steps", 10))
    step_s = float(ctx.params.get("step_s", 0.05))
    crash_at = ctx.params.get("crash_at")
    crash_attempts = int(ctx.params.get("crash_attempts", 99))
    for i in range(steps):
        if crash_at is not None and i == int(crash_at) and int(ctx.job.get("attempts", 1)) <= crash_attempts:
            os._exit(3)  # a dying worker: the job returns to the queue
        ctx.tick((i + 1) / steps, f"Шаг {i + 1} из {steps}")
        time.sleep(step_s)
    ctx.log("готово")
    return {"steps": steps, "attempt": ctx.job.get("attempts")}


@handler("diag.quick", "Быстрая проверка", queue="quick")
def quick(ctx: JobContext) -> dict[str, Any]:
    ctx.tick(1.0)
    return {"ok": True}


@handler("diag.fail", "Проверка ошибки", queue="heavy")
def fail(ctx: JobContext) -> dict[str, Any]:
    raise JobError("diag_failed", "Проверочная ошибка.", "Ничего делать не нужно.")


@handler("diag.hang", "Проверка зависания", queue="heavy")
def hang(ctx: JobContext) -> dict[str, Any]:
    """Never ticks: cancellation must still end it (the scheduler kills the worker)."""
    time.sleep(float(ctx.params.get("seconds", 60)))
    return {}
