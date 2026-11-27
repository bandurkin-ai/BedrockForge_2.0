"""Deterministic split of weighted items into N shards (longest-processing-time first). Standard library only.

Used by shard_tests.py (pytest files) and shard_bench.py (benchmark scenarios) so that CI matrix jobs run
equal parts of the same list. Same input -> same shards on every machine.
"""

from __future__ import annotations


def split(weights: dict[str, float], n: int) -> list[list[str]]:
    """Greedy LPT: the heaviest item goes to the currently lightest shard; ties -> lower shard, then name order."""
    if n < 1:
        raise ValueError("number of shards must be >= 1")
    shards: list[list[str]] = [[] for _ in range(n)]
    loads = [0.0] * n
    for name in sorted(weights, key=lambda k: (-weights[k], k)):
        i = min(range(n), key=lambda j: (loads[j], j))
        shards[i].append(name)
        loads[i] += weights[name]
    return [sorted(s) for s in shards]


def loads(weights: dict[str, float], shards: list[list[str]]) -> list[float]:
    return [sum(weights[k] for k in s) for s in shards]
