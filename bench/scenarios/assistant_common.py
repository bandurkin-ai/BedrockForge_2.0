"""Common helpers of the benches of the assistant (ADR 0029 решение 38; подшаг 23.18): a project folder with its
engine, and the quiet environment the mock of the Anthropic API needs. No bench goes to the internet."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def make_project(work: Path, name: str = "Ассистент") -> Any:
    """A new project folder; returns `SimpleNamespace(engine, folder, uid)` (what `Dialogs` and `data_tools` read).
    The folder is also written into `work/folder.txt` for `run`."""
    from bf_core.projects.project import create_folder

    folder = create_folder(work, name, {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    return open_project(work)


def open_project(work: Path) -> Any:
    from bf_core.db import dialect

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    return SimpleNamespace(engine=engine, folder=folder, uid="bench-assistant")


def quiet_environment() -> None:
    """Nothing of the machine reaches the SDK: no ANTHROPIC_* variables, no proxy for 127.0.0.1."""
    for key in list(os.environ):
        if key.startswith(("ANTHROPIC_", "BF_ANT", "BF_ASSISTANT")):
            del os.environ[key]
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
