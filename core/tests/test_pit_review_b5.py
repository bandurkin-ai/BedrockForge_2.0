"""The review of the milestone В5, group 4 (Э21): the pit scenarios.

1. The tonnes of the comparison are the tonnes of the run. The run takes a block whose density is 0, negative or empty
   as a block with no density (the default density; docs/progress/e21-api.md §15.13); the comparison of two scenarios
   took only the empty one — a block of density 0 gave 0 t and a negative density took tonnes AWAY from «только в A».
2. «Перестроить» keeps the parameters that wait for a run: it runs the parameters of the result again, and its write
   dropped `meta.draft` — what the user had typed after the last run was gone without a word."""

# ruff: noqa: F811 - the fixture `pw` is imported, and the tests take it by its name
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from bf_core.db import dialect
from bf_core.mine import compare as cmp
from bf_core.mine import store as st
from tests.pitjob import SMALL, direct, info, rebuild
from tests.pitworld import make_model, new, params, pw  # noqa: F401 - the fixture


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


# --- «Перестроить» of a scenario whose parameters were edited and not run yet ---------------------------------------


def test_a_rebuild_keeps_the_parameters_that_wait_for_a_run(pw) -> None:  # type: ignore[no-untyped-def]
    """The parameters edited after a run wait in `meta.draft` («параметры изменены, нужен запуск»). «Перестроить» of
    the graph runs the parameters of the RESULT again — and its write dropped the draft: the price the user had typed
    was gone, the window showed the old one."""
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    first = direct(pw, sid)
    edited = params(mid)
    edited["elements"][0]["price"] = 2345.0
    with dialect.write_tx(pw.project.engine) as conn:
        assert st.update_params(conn, sid, first["version"], params=edited)["params_changed"] is True
    waiting = info(pw, sid)
    assert waiting["params"]["elements"][0]["price"] == 2345.0

    again = rebuild(pw, sid)
    assert again["params_hash"] == first["params_hash"]  # the result is that of its own parameters
    now = info(pw, sid)
    assert now["meta"]["params"]["elements"][0]["price"] == 2000.0
    assert now["params_changed"] is True and now["params"]["elements"][0]["price"] == 2345.0  # still waiting
    assert now["params_hash"] == waiting["params_hash"]

    done = direct(pw, sid)  # «Запустить» takes them and the flag goes
    after = info(pw, sid)
    assert done["params"]["elements"][0]["price"] == 2345.0
    assert after["params_changed"] is False and "draft" not in after["meta"]
