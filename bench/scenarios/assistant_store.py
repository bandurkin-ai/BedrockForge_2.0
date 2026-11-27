"""Dialogs of the assistant in the project and the usage summary (ADR 0029 решение 38; ТЗ 7.32): a part of 2 MB is
written, 1000 dialogs are listed, the summary of 10^5 lines of `usage.jsonl` is counted.

`prepare` writes `dialogs` dialogs of one small part each (the fsync of every write stays outside the timing) and
the file of the usage; `run` times the stages (median of 3; keys of `info`, the budgets of `limits`):
  append_s   `Dialogs.append` of a part of `part_mb` MB of JSON (encode, zstd, the file, the owner, the meta);
  list_s     `Dialogs.list` of all the dialogs (the whole list at once);
  page_s     the first page of 50 (the panel opens with it; report only);
  read_s     `Dialogs.read` of the dialog with the big part (report only);
  usage_s    `usage.summary` of the first call over `usage_rows` lines (the cache does not count).
"""

from __future__ import annotations

import json
import shutil
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bench.scenarios import assistant_common as common

MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2")
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
REPEAT = 3


def _user(text: str) -> list[Any]:
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def _big_part(n: int, megabytes: float) -> list[Any]:
    """Messages of about `megabytes` MB of JSON: the result of a read tool with numbers (zstd compresses it badly)."""
    import numpy as np

    rng = np.random.default_rng(n)
    size = int(megabytes * 1_000_000)
    rows: list[str] = []
    done = 0
    while done < size:
        text = json.dumps([round(float(v), 6) for v in rng.random(8) * 1000.0])
        rows.append(text)
        done += len(text) + 1
    result = "[" + ",".join(rows) + "]"
    return [
        *_user(f"Покажи таблицу {n}"),
        {"role": "assistant", "content": [{"type": "tool_use", "id": f"toolu_{n}", "name": "data_table", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": result}]},
    ]


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.assistant import prices, usage
    from bf_core.assistant.store import Dialogs, make_title

    project = common.make_project(work)
    try:
        dialogs = Dialogs(project)
        for i in range(int(params["dialogs"])):
            text = f"Вопрос {i}: что с аудитом набора {i % 7}?"
            day, minute = 1 + i % 26, i // 26 % 60
            head = dialogs.create(make_title(text), at=f"2026-11-{day:02d}T10:{minute:02d}:00Z")
            dialogs.append(
                head["id"],
                [*_user(text), {"role": "assistant", "content": [{"type": "text", "text": f"Ответ {i}."}]}],
                [{"kind": "user", "id": "i1", "at": "2026-11-26T10:00:00Z", "text": text}],
                questions=1,
                usage={"input": 1200, "output": 300, "cache_read": 5000, "cache_write": 800, "cost_usd": 0.0421},
                models=[MODELS[i % 4]],
                at=f"2026-11-{day:02d}T10:{minute:02d}:30Z",
            )
    finally:
        project.engine.dispose()
    rows = []
    for i in range(int(params["usage_rows"])):
        model = MODELS[i % 4]
        rows.append(
            json.dumps(
                {
                    "at": usage._fmt(NOW - timedelta(seconds=i * 20)),
                    "model": model,
                    "input": 1200 + i % 50,
                    "output": 300,
                    "cache_read": 9000,
                    "cache_write": 400,
                    "cost_usd": prices.cost(model, 1200 + i % 50, 300, 9000, 400),
                    "long": False,
                    "dialog_uid": f"d{i % 40}",
                    "project_uid": "p1",
                    "prices_version": prices.PRICES_VERSION,
                }
            )
        )
    path = usage.usage_path(work / "data")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def load() -> None:
    import duckdb  # noqa: F401

    import bf_core.assistant.store  # noqa: F401
    import bf_core.assistant.usage  # noqa: F401


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.assistant import usage
    from bf_core.assistant.store import Dialogs

    total = int(params["dialogs"])
    # a repeated run (`--repeat`) must find the project as `prepare` left it: the parts go into a copy
    copy = work / "run"
    shutil.rmtree(copy, ignore_errors=True)
    copy.mkdir()
    shutil.copytree(Path((work / "folder.txt").read_text(encoding="utf-8")), copy / "p")
    (copy / "folder.txt").write_text(str(copy / "p"), encoding="utf-8")
    project = common.open_project(copy)
    out: dict[str, Any] = {"dialogs": total, "part_mb": params["part_mb"]}
    try:
        dialogs = Dialogs(project)
        t0 = time.perf_counter()
        summary = usage.summary(work / "data", NOW, UTC)
        out["usage_s"] = round(time.perf_counter() - t0, 4)
        assert summary["all"]["requests"] == int(params["usage_rows"])

        times: list[float] = []
        for _ in range(REPEAT):
            t0 = time.perf_counter()
            listed = dialogs.list(0, total)
            times.append(time.perf_counter() - t0)
            assert listed["total"] == total and len(listed["items"]) == total
        out["list_s"] = round(statistics.median(times), 4)
        t0 = time.perf_counter()
        first = dialogs.list(0, 50)
        out["page_s"] = round(time.perf_counter() - t0, 4)
        assert len(first["items"]) == min(50, total)

        times = []
        for k in range(REPEAT):
            messages = _big_part(k, float(params["part_mb"]))  # made outside the timing
            t0 = time.perf_counter()
            head = dialogs.append(first["items"][k]["id"], messages, [], questions=1, state="idle")
            times.append(time.perf_counter() - t0)
        out["append_s"] = round(statistics.median(times), 4)
        t0 = time.perf_counter()
        loaded = dialogs.read(head["id"])
        out["read_s"] = round(time.perf_counter() - t0, 4)
        assert len(loaded.messages) == 2 + 3
    finally:
        project.engine.dispose()
        shutil.rmtree(copy, ignore_errors=True)
    return out
