"""The review of the milestone В5, group 3 (Э19): «Проверка модели» of a model whose parameters were edited after the
build. The files of the model keep the points, the patches and the solutions, not the kernel they were solved with: it
is read from the parameters of the model, and those may be newer than the build."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.model import check as ck
from bf_core.model import store as st
from bf_core.model import store_base as sb
from tests.stack_world import fault_group, fault_layers, fault_manual, make_b, small_patches, stack_groups
from tests.test_model_check import check_spec, layered, loaded, refused, stored_points
from tests.test_model_data_load import World
from tests.test_model_job import (
    _roles,  # noqa: F401 - the autouse fixture of the roles of the files
    groups_of,
    make,
    meta_of,
    run,
    shell,
)

pytestmark = pytest.mark.db


@pytest.fixture()
def world(db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    w = World(db_url, tmp_path)
    yield w
    w.engine.dispose()


@pytest.fixture(autouse=True)
def _forget() -> None:
    ck.forget()


def edit(w: World, mid: int, **raw: object) -> None:
    with dialect.write_tx(w.engine) as conn:
        st.update(conn, w.store, w.project, mid, raw, int(sb.stored(conn, mid).version))


def test_the_check_of_a_model_whose_kernel_was_edited_is_not_a_table_of_another_field(world: World) -> None:
    w = world
    mid = layered(w)
    fmax = float(np.abs(stored_points(w, mid)["f"]).max())
    before = ck.page(loaded(w, mid), check_spec(size=5000))
    assert before["max_abs_residual"] <= 1e-8 * fmax  # η = 0: the field of the build goes through its data
    edit(w, mid, kernel="cubic")  # not built again: the solutions in the files are those of the linear kernel
    ck.forget()
    err = refused(lambda: loaded(w, mid), 409, "not_built")
    assert "ядро" in err.message.lower() or "параметр" in err.message.lower()
    edit(w, mid, kernel="linear")  # the kernel of the build again: the table of the last build is back
    after = ck.page(loaded(w, mid), check_spec(size=5000))
    assert after["max_abs_residual"] <= 1e-8 * fmax


def test_an_edit_that_does_not_touch_the_field_leaves_the_check_of_the_last_build(world: World) -> None:
    w = world
    mid = layered(w)
    edit(w, mid, h=10.0, post={"v_min": 5.0, "simplify": False, "clip": "none", "clip_id": None})
    out = ck.page(loaded(w, mid), check_spec(size=5000))
    assert out["model"]["stale"] is True and out["warnings"][0]["code"] == "model_stale"
    assert out["max_abs_residual"] <= 1e-8 * float(np.abs(stored_points(w, mid)["f"]).max())


def _shells(w: World) -> int:
    groups = [*groups_of("SS"), shell(5, w, mode="indicator", thresholds=[0.3, 1.0]), shell(6, w, thresholds=[0.3])]
    return int(make(w, "Оболочки", groups, h=8.0)["id"])


def _turned(w: World) -> int:
    hand = {"ang1": 20.0, "ang2": 10.0, "ang3": 0.0, "ratios": [1.0, 0.5, 0.25]}
    return int(make(w, "Вручную", groups_of("SS", "SH"), anisotropy=hand, kernel="cubic",
                    smoothing={"mode": "manual", "s": 0.1, "cond_target": None}, **small_patches())["id"])  # fmt: skip


def _structural(w: World) -> int:
    from tests.test_model_job_structural import measurements, structural

    trend = structural(w, measurements(w, 35.0, 120.0))
    return int(make(w, "Структурная", groups_of("SS", "SH"), trend_id=trend, **small_patches())["id"])


def _auto(w: World) -> int:
    over = {"smoothing": {"mode": "auto", "s": 0.01, "cond_target": 1e6}, **small_patches()}
    return int(make(w, "Авто", groups_of("SS", "SH"), **over)["id"])


def _finite_radius(w: World) -> int:
    return int(make(w, "Конечный радиус", groups_of("SS", "SH"), kernel="wendland", **small_patches())["id"])


def _faulted(w: World) -> int:
    manual = fault_manual(fault_layers(w))
    groups = [*stack_groups(), fault_group(radius=120.0)]  # the stack, the wings and the partial fits of a finite fault
    return int(make_b(w, "Разлом", groups, manual, **small_patches())["id"])


@pytest.mark.parametrize("model", [_shells, _turned, _structural, _auto, _finite_radius, _faulted])
def test_every_kind_of_field_is_known_again_after_an_edit_that_does_not_touch_it(world: World, model: Any) -> None:
    """The hash of the first patch of a field, made again from the files and the parameters, is the stored one for
    every way a field is solved: a stale model is refused only when its field is another one."""
    w = world
    mid = model(w)
    run(w, mid)
    before = loaded(w, mid).table
    edit(w, mid, h=11.0)  # «изменены параметры», but not those of a field
    ck.forget()
    got = loaded(w, mid)
    assert got.info["stale"] is True
    assert np.array_equal(got.table.f_model, before.f_model, equal_nan=True) and got.table.n == before.n > 0


def test_a_fault_of_a_model_of_a_structural_trend_is_checked_without_axes_of_its_own(world: World) -> None:
    """The field of a fault is solved without A_j (`faults._Build.fit`), so in a model of a structural trend its rows
    of `implicit_patches` have NaN in the columns of A_j: «no matrix», not a matrix (the check ended with «SVD did
    not converge», a 500)."""
    from tests.test_model_job_structural import measurements, structural

    w = world
    trend = structural(w, measurements(w, 35.0, 120.0))
    groups = [*stack_groups(), fault_group()]
    mid = make_b(w, "Тренд и разлом", groups, fault_manual(fault_layers(w)), trend_id=trend, **small_patches())["id"]
    out = run(w, mid)
    built = {(g["group"], g["wing"]): g["max_residual"] for g in out["stats"]["by_group"]}
    got = {(g["group"], g["wing"]): g["max_abs_residual"] for g in loaded(w, mid).table.groups}
    assert set(got) == set(built) and len(got) > 5
    for key, residual in got.items():  # the field of the build, read back: the residual the build measured
        assert residual is not None and residual == pytest.approx(built[key], abs=1e-9)
    edit(w, mid, h=11.0)
    ck.forget()
    assert loaded(w, mid).table.n > 0  # and it is known again when the model is «устарела»


def test_the_thresholds_of_an_indicator_shell_edited_after_the_build_are_another_field(world: World) -> None:
    w = world
    mid = _shells(w)
    run(w, mid)
    assert loaded(w, mid).table.n > 0
    meta = meta_of(w, mid)
    groups = [dict(g) for g in meta["groups"]]
    groups[1] = {**groups[1], "params": {**groups[1]["params"], "thresholds": [0.5, 2.0]}}
    edit(w, mid, groups=groups)
    ck.forget()
    refused(lambda: loaded(w, mid), 409, "not_built")  # the residuals would be those of the indicators of 0,5 and 2
