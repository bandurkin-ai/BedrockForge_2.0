"""Test-only: a floor under the run time of the job `sections.cache` (`BF_EXTRA_JOB_MODULES=tests.slow_section_cut`).

Imported by the worker (spawn), it wraps `wf_sections.cut` so that each plane takes at least `BF_TEST_CUT_DELAY_S`
seconds; with the variable unset it passes straight through. The job cannot finish sooner than planes x delay however
fast the machine is, so a test that wants to meet it running (the progress reaches the database at most once a second,
`PROGRESS_DB_EVERY_S`) and to cancel it in the middle does not race its end."""

from __future__ import annotations

import os
import time
from typing import Any

from bf_core.sections import wf_sections

_cut = wf_sections.cut


def _slow_cut(*args: Any, **kwargs: Any) -> Any:
    delay = float(os.environ.get("BF_TEST_CUT_DELAY_S") or 0.0)
    if delay > 0.0:
        time.sleep(delay)
    return _cut(*args, **kwargs)


if wf_sections.cut is _cut:
    wf_sections.cut = _slow_cut
