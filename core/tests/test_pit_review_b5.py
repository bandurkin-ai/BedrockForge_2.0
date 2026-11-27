"""The review of the milestone В5, group 4 (Э21): the pit scenarios.

1. The tonnes of the comparison are the tonnes of the run. The run takes a block whose density is 0, negative or empty
   as a block with no density (the default density; docs/progress/e21-api.md §15.13); the comparison of two scenarios
   took only the empty one — a block of density 0 gave 0 t and a negative density took tonnes AWAY from «только в A»."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from bf_core.mine import compare as cmp


def side(**params_: Any) -> Any:
    return SimpleNamespace(params=params_)


def test_a_density_of_zero_or_below_takes_the_default_as_in_the_run() -> None:
    cols = {"SG": np.array([2.7, 0.0, np.nan, -1.0])}
    vol = np.full(4, 100.0)
    t = cmp._tonnes(side(density="SG", default_density=2.5), cols, vol)
    assert t.tolist() == [270.0, 250.0, 250.0, 250.0]


def test_without_a_default_such_a_block_has_no_tonnes_and_is_not_a_zero() -> None:
    cols = {"SG": np.array([2.7, 0.0, np.nan, -1.0])}
    t = cmp._tonnes(side(density="SG", default_density=None), cols, np.full(4, 100.0))
    assert t[0] == 270.0 and np.isnan(t[1:]).all()  # counted in `no_tonnes`, never in the sum


def test_a_constant_density_is_as_before() -> None:
    t = cmp._tonnes(side(density=3.0, default_density=None), {}, np.array([10.0, 20.0]))
    assert t.tolist() == [30.0, 60.0]
