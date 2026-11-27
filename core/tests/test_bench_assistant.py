"""The benches of the assistant (подшаг 23.18; ADR 0029 решение 38) on small volumes: each scenario goes from `prepare`
through `load` to the numbers it reports, as `bench.run` runs it, and the data of `info` are what the budgets bound.

`assistant_turn` goes to the mock of the Anthropic API on 127.0.0.1 (no network), `assistant_store` writes a part of
a dialog and lists the dialogs, `assistant_data` calls `data_tree`, `data_holes`, `data_stats` of a project of
`audit_db`. The real sizes (1000 dialogs, 50 000 holes, 5 million samples) are measured by `bench.run`."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCENARIOS = {
    "assistant_turn": {"holes": 12, "tools_count": 40},
    "assistant_store": {"dialogs": 40, "part_mb": 0.3, "usage_rows": 3000},
    "assistant_data": {"holes": 300, "rows": 6000},
}


@pytest.fixture(autouse=True)
def own_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scenario clears ANTHROPIC_* and sets NO_PROXY of the process: the test keeps its own copy."""
    monkeypatch.setattr(os, "environ", os.environ.copy())


def run(name: str, params: dict[str, Any], work: Path) -> dict[str, Any]:
    """A scenario as `bench.run` runs it, in this process."""
    mod = importlib.import_module(f"bench.scenarios.{name}")
    mod.prepare(params, work)
    mod.load()
    info: dict[str, Any] = mod.run(params, work)
    json.dumps(info)  # what `bench.run` prints
    return info


def test_scenarios_have_the_interface_of_bench_run() -> None:
    for name in SCENARIOS:
        mod = importlib.import_module(f"bench.scenarios.{name}")
        assert callable(mod.prepare) and callable(mod.load) and callable(mod.run), name


def test_scenarios_are_registered_in_both_profiles() -> None:
    budgets = json.loads((ROOT / "bench" / "budgets.json").read_text(encoding="utf-8"))
    present = {(p, n) for p in ("ci", "full") for n in SCENARIOS if n in budgets[p]}
    if not present:
        pytest.skip("bench/budgets.json has no assistant scenarios yet (the orchestrator adds them with 23.18)")
    assert present == {(p, n) for p in ("ci", "full") for n in SCENARIOS}, sorted(present)
    for profile in ("ci", "full"):
        for name in SCENARIOS:
            spec = budgets[profile][name]
            assert set(SCENARIOS[name]) <= set(spec["params"]), (profile, name)
            assert {"budget_s", "budget_mb"} <= set(spec), (profile, name)


def test_turn_first_delta_and_two_rounds_on_the_mock(tmp_path: Path) -> None:
    info = run("assistant_turn", SCENARIOS["assistant_turn"], tmp_path)
    assert info["tools"] == 40 and info["api_calls"] == 3  # summary -> audit -> text
    assert 0 < info["first_delta_s"] < 2.0 and 0 < info["two_rounds_s"] < 5.0
    assert info["tools_kb"] > 0 and info["request_kb"] > info["tools_kb"] and info["tools_tokens"] > 0
    assert info["tools_full_kb"] > info["tools_kb"] * 0.8  # the API gets the shortened descriptions


def test_store_part_list_and_usage(tmp_path: Path) -> None:
    info = run("assistant_store", SCENARIOS["assistant_store"], tmp_path)
    assert info["dialogs"] == 40
    for key in ("append_s", "list_s", "page_s", "read_s", "usage_s"):
        assert 0 <= info[key] < 5.0, key


def test_data_tools_on_a_project(tmp_path: Path) -> None:
    info = run("assistant_data", SCENARIOS["assistant_data"], tmp_path)
    assert info["holes"] == 300 and info["rows"] == 6000
    for key in ("tree_s", "holes_s", "search_s", "stats_s", "stats_cold_s"):
        assert 0 <= info[key] < 30.0, key
