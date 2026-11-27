"""The CI sharding scripts (tools/ci/shard_tests.py, shard_bench.py): a partition, deterministic, no test lost."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

CI = Path(__file__).resolve().parents[2] / "tools" / "ci"


def _load(name: str):  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(CI))
    try:
        spec = importlib.util.spec_from_file_location(f"_ci_{name}", CI / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(CI))


shard_tests = _load("shard_tests")
shard_bench = _load("shard_bench")
shardlib = _load("shardlib")


def test_split_is_a_balanced_deterministic_partition() -> None:
    w = {f"f{i}": float(i % 7 + 1) for i in range(40)}
    a = shardlib.split(w, 4)
    assert a == shardlib.split(dict(reversed(list(w.items()))), 4)
    assert sorted(x for s in a for x in s) == sorted(w)
    loads = shardlib.loads(w, a)
    assert max(loads) - min(loads) <= max(w.values())


@pytest.mark.parametrize("n", [1, 3, 4, 7])
def test_test_files_are_dealt_without_loss_or_repeat(n: int) -> None:
    files = shard_tests.discover()
    assert "tests/test_ci_shards.py" in files
    shards, _ = shard_tests.plan(n)
    flat = [f for s in shards for f in s]
    assert sorted(flat) == files
    assert all(shards)


def test_new_test_file_gets_a_weight_and_lands_in_a_shard() -> None:
    files = [*shard_tests.discover(), "tests/test_not_in_the_table_yet.py"]
    table, per_test = shard_tests.load_table()
    assert "tests/test_not_in_the_table_yet.py" not in table
    # the file does not exist: counted as one test, the default weight of a file of one test
    w = {f: (table[f] if f in table else per_test) for f in files}
    shards = shardlib.split(w, 4)
    assert sum("tests/test_not_in_the_table_yet.py" in s for s in shards) == 1


def test_stale_weights_table_does_not_lose_files() -> None:
    # rows of renamed or deleted files stay harmless: only the files found on disk are dealt
    table, per_test = shard_tests.load_table()
    assert per_test > 0 and table
    w = shard_tests.weights_of(shard_tests.discover(), {**table, "tests/test_gone.py": 99.0}, per_test)
    assert "tests/test_gone.py" not in w and set(w) == set(shard_tests.discover())


def test_bench_scenarios_are_dealt_without_loss() -> None:
    names = set(shard_bench.scenarios())
    assert names
    for n in (1, 5, 8):
        shards, _ = shard_bench.plan(n)
        flat = [s for sh in shards for s in sh]
        assert sorted(flat) == sorted(names) and len(flat) == len(names)


def test_bench_merge_unites_and_refuses_a_repeated_scenario(tmp_path: Path) -> None:
    bm = _load("bench_merge")
    for i, name in enumerate(("a", "b")):
        (tmp_path / f"{i}.json").write_text(
            json.dumps({"profile": "ci", "python": "3.12", "scenarios": {name: {"seconds": 1}}}), encoding="utf-8"
        )
    merged = bm.merge([tmp_path / "0.json", tmp_path / "1.json"])
    assert set(merged["scenarios"]) == {"a", "b"}
    with pytest.raises(SystemExit):
        bm.merge([tmp_path / "0.json", tmp_path / "0.json"])
