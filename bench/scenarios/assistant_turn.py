"""A turn of the assistant against the mock of the Anthropic API, in this process (ADR 0029 решение 38; ТЗ 7.32):
what the CORE spends before Claude's first word, and for a turn of two circles of read tools. The time of the real API
is not measured (the network); the mock answers at once, so what is timed is the core: the check of the tools, the
request body, the read of the dialog, the SDK client, the parse of the stream, the write of the part.

The window sends `tools_count` tools of `ui_*` (270 in the program, ~340 KB of JSON before the shortening for Claude of
23.9б); `prepare` makes them (a fixed generator of descriptions: some over the 300 signs the API gets, 3
parameters each) and a small project with a set of `holes` holes for `data_summary` / `data_audit`. `run` times
(median of 5 after one warm turn; keys of `info`, the budgets of `limits`):
  first_delta_s   a question -> the first `text.delta` of the segment (a new dialog, the project keeps it);
  two_rounds_s    a question -> `data_summary` -> `data_audit` -> the text (three calls of the API), to `turn.end`;
and reports (no budget): `request_kb` — the body of the request to the API, `tools_kb` — its `tools`, `tools_tokens` —
the estimate of the window (signs / 3), `tools_full_kb` — the same tools before the shortening.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from bench.scenarios import assistant_common as common

REPEAT = 5
WORDS = (
    "проект скважина набор разрез карта слой окно панель таблица график каркас сетка блок модель "
    "пробы интервал композит статистика вариограмма оценка отчёт экспорт импорт фильтр выбор цвет "
    "масштаб вид сцена подпись легенда шкала сечение траектория устье инклинометрия литология"
).split()


def _sentence(n: int, words: int) -> str:
    return " ".join(WORDS[(n * 7 + k * 3) % len(WORDS)] for k in range(words)).capitalize() + "."


def make_tools(count: int) -> list[dict[str, Any]]:
    """`count` tools as `commandTools()` of the window makes them: every property a string, all of them required."""
    out = []
    for n in range(count):
        props = {f"arg{k}": {"type": "string", "description": _sentence(n + k, 7 + (n + k) % 6)} for k in range(3)}
        out.append(
            {
                "name": f"ui_command_{n:03d}",
                "description": " ".join(_sentence(n + k, 10 + (n + k) % 7) for k in range(2 + n % 4)),
                "input_schema": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            }
        )
    return out


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.db import dialect
    from bf_core.drillholes.store import CollarRow, create_set, replace_collars

    project = common.make_project(work)
    try:
        holes = int(params["holes"])
        with dialect.write_tx(project.engine) as conn:
            set_id = create_set(conn, "Набор")
            replace_collars(
                conn,
                set_id,
                [CollarRow(f"DH-{i:03d}", 1000.0 + i * 25.0, 2000.0, 300.0, 120.0, 90.0, -60.0) for i in range(holes)],
            )
    finally:
        project.engine.dispose()
    (work / "set.txt").write_text(str(set_id), encoding="utf-8")
    (work / "tools.json").write_text(json.dumps(make_tools(int(params["tools_count"]))), encoding="utf-8")


def load() -> None:
    import anthropic  # noqa: F401

    import bf_core.assistant.data_tools as data_tools
    import bf_core.assistant.loop  # noqa: F401

    data_tools.load_all()


def _kb(value: Any) -> float:
    return round(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) / 1000.0, 1)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    common.quiet_environment()
    from bf_core.assistant import loop, store_parts, tools
    from bf_core.assistant.connection import Connection
    from bf_core.assistant.loop_events import LoopEnv
    from bf_core.assistant.schemas import TurnBody, parse_body
    from bf_core.assistant.segments import Segment, Segments
    from bf_core.assistant.store import Dialogs
    from tests.assistant.mock_anthropic import TEST_API_KEY, MockAnthropic

    ui_tools = json.loads((work / "tools.json").read_text(encoding="utf-8"))
    set_id = int((work / "set.txt").read_text(encoding="utf-8"))
    context = {
        "project": {"uid": "bench-assistant", "name": "Замер"},
        "set": {"id": set_id, "name": "Набор", "holes": int(params["holes"])},
    }

    def body(text: str) -> TurnBody:
        return parse_body(TurnBody, {"dialog_id": None, "text": text, "context": context, "tools": ui_tools})

    def ask(env: LoopEnv, text: str) -> tuple[float, Segment, float | None]:
        """(seconds to `turn.end`, the segment, seconds to the first `text.delta`)."""
        t0 = time.perf_counter()
        seg = loop.start_turn(env, body(text))
        first = None
        for event in seg.iter(0):
            if event is not None and event.event == "text.delta":
                first = time.perf_counter() - t0
                break
        assert seg.wait(30.0), "the segment did not finish"
        total = time.perf_counter() - t0
        last = seg.events_after(0)[-1]
        assert last.event == "turn.end" and last.data["state"] == "idle", last.data
        return total, seg, first

    project = common.open_project(work)
    out: dict[str, Any] = {"tools": len(ui_tools)}
    try:
        with MockAnthropic() as mock:
            env_vars = {"BF_ASSISTANT_BASE_URL": mock.base_url, "ANTHROPIC_API_KEY": TEST_API_KEY}
            connection = Connection(work / "data", env=env_vars)
            env = LoopEnv(connection, Dialogs(project, store_parts.TempDialogs()), Segments())
            mock.use_scenario(["summary", "data_tools"])
            ask(env, "Сводка по проекту")  # warm: the SDK client, the parsers, the first write of a project
            ask(env, "Что не так с данными")

            firsts: list[float] = []
            for _ in range(REPEAT):
                _total, _seg, first = ask(env, "Сводка по проекту")
                assert first is not None
                firsts.append(first)
            out["first_delta_s"] = round(statistics.median(firsts), 4)
            sent = [r["body"] for r in mock.requests_to("/v1/messages")]
            last = sent[-1]
            out["request_kb"] = _kb(last)
            out["tools_kb"] = _kb(last["tools"])
            out["tools_tokens"] = tools.tools_size(last["tools"])[1]
            out["tools_full_kb"] = _kb(ui_tools)

            calls_before = len(mock.requests_to("/v1/messages"))
            rounds: list[float] = []
            for _ in range(REPEAT):
                total, seg, _first = ask(env, "Что не так с данными")
                rounds.append(total)
                results = [e for e in seg.events_after(0) if e.event == "tool.end"]
                assert len(results) == 2, [e.event for e in seg.events_after(0)]
            out["two_rounds_s"] = round(statistics.median(rounds), 4)
            out["api_calls"] = (len(mock.requests_to("/v1/messages")) - calls_before) // REPEAT
        assert TEST_API_KEY not in json.dumps(last, ensure_ascii=False)
    finally:
        project.engine.dispose()
        os.environ.pop("NO_PROXY", None)
    return out
