"""Т8.9-13 and Т8.9-8 for the stack, the faults, the intrusion and the systems of veins (подшаг 19.36; ТЗ Т8.9-13; ADR
0026 «Варианты» → «Обновить», решения 19–21, 23): a model of each of the four kinds is built with 1, 2, 4 and 8 threads
and the outputs, the three files and the layers of strings are the same bytes; the update after an edit of 5 holes of
100 is the full build of the edited data bit for bit, with the patches of the unchanged data taken from the cache (a
wing of a fault is a field of its own: its patches are those of the file), whatever the threads of the two runs.
The database tests: SQLite always, PostgreSQL with BF_TEST_PG (`-m db`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bf_core.db import dialect
from bf_core.model import store as st
from tests.stack_world import (
    fault_group,
    fault_layers,
    fault_manual,
    make_b,
    rows_of,
    small_patches,
    stack_groups,
    vein_system_group,
    veins_lithology,
    veins_world,
)
from tests.test_model_data import read_csv
from tests.test_model_data_load import World
from tests.test_model_job import (
    _roles,  # noqa: F401 - the autouse fixture: the roles of the files until `db/schema.py` has them
    groups_of,
    run,
    version,
)

pytestmark = pytest.mark.db
KINDS = ("stack", "fault", "intrusion", "veins")


def scene_of(kind: str, db_url: str, tmp_path: Path) -> tuple[World, list[dict[str, Any]], list[dict[str, Any]],
                                                              list[dict[str, str]], tuple[float, float]]:  # fmt: skip
    """(the world, the groups, the hand data, the rows of the lithology, where the edit is) of a kind of model."""
    if kind == "veins":
        w = veins_world(db_url, tmp_path)
        return w, [*groups_of("ALV", "SS"), vein_system_group(7)], [], veins_lithology(), (320.0, 200.0)
    w = World(db_url, tmp_path)
    rows = read_csv("lithology.csv")
    if kind == "fault":
        return w, [*stack_groups(), fault_group()], fault_manual(fault_layers(w)), rows, (60.0, 60.0)
    g = groups_of("ALV", "SS", "SH", "GR") if kind == "stack" else groups_of("INT", "ALV", "SS", "SH")
    g[0]["relation"] = "onlap" if kind == "stack" else "intrusion"
    g[1]["relation"] = "erode"
    return w, g, [], rows, (60.0, 60.0)


def edited(rows: list[dict[str, str]], at: tuple[float, float], count: int = 5,
           shift: float = 0.4) -> list[dict[str, str]]:  # fmt: skip
    """The rows of the lithology with the contacts of the `count` holes nearest to `at` moved by `shift` metres (a
    campaign next to each other: 5 holes of 100)."""
    where = {r["HOLE"]: (float(r["X"]), float(r["Y"])) for r in read_csv("collars.csv")}
    pick = set(sorted(where, key=lambda k: (where[k][0] - at[0]) ** 2 + (where[k][1] - at[1]) ** 2)[:count])
    out = [dict(r) for r in rows]
    for a, b in zip(out, out[1:], strict=False):
        if a["HOLE"] in pick and a["HOLE"] == b["HOLE"] and float(a["TO"]) - float(a["FROM"]) > 1.0 \
                and float(b["TO"]) - float(b["FROM"]) > 1.0:  # fmt: skip
            a["TO"] = b["FROM"] = f"{float(a['TO']) + shift:.2f}"
    return out


@pytest.fixture(params=KINDS)
def scene(request, db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    w, groups, manual, rows, at = scene_of(request.param, db_url, tmp_path)
    yield request.param, w, groups, manual, rows, at
    w.engine.dispose()


def make(w: World, name: str, groups: list[dict[str, Any]], manual: list[dict[str, Any]], **over: Any) -> int:
    return int(make_b(w, name, groups, manual, **small_patches(), **over)["id"])


# --- Т8.9-13 ----------------------------------------------------------------------------------------------------------


def test_t8913_the_same_bytes_with_1_2_4_8_threads(scene) -> None:  # type: ignore[no-untyped-def]
    kind, w, groups, manual, _rows, _at = scene
    base_id = make(w, f"{kind} 1", groups, manual)
    run(w, base_id, threads=1)
    base = rows_of(w, base_id)
    assert base["geometry"] and (kind != "veins" or base["lines"])  # the crossing of the veins is a layer of strings
    for threads in (2, 4, 8):
        other = make(w, f"{kind} {threads}", groups, manual)
        out = run(w, other, threads=threads)
        got = rows_of(w, other)
        assert got["files"] == base["files"], (kind, threads)  # points, patches with their hashes, the solution
        assert got["lines"] == base["lines"], (kind, threads)
        assert got["geometry"] == base["geometry"], (kind, threads)
        assert out["stats"]["by_group"] and [(g["group"], g["wing"]) for g in out["stats"]["by_group"]] == [
            (g["group"], g["wing"]) for g in run_stats(w, base_id)]  # fmt: skip


def run_stats(w: World, mid: int) -> list[dict[str, Any]]:
    with dialect.read_tx(w.engine) as conn:
        return list(st.get(conn, mid)["meta"]["stats"]["by_group"])


# --- Т8.9-8 -----------------------------------------------------------------------------------------------------------


def test_t898_the_update_after_an_edit_is_the_full_build_bit_for_bit(scene) -> None:  # type: ignore[no-untyped-def]
    kind, w, groups, manual, rows, at = scene
    mid = make(w, f"{kind} основа", groups, manual)
    first = run(w, mid, threads=4)
    t2 = w._table("Литология", "interval", edited(rows, at), "CODE", "text")  # noqa: SLF001
    data = {"set_id": w.set_id, "table": str(t2), "field": "CODE", "d_max": 30, "step": 5}
    with dialect.write_tx(w.engine) as conn:
        st.update(conn, w.store, w.project, mid, {"data": data}, version(w, mid))
    upd = run(w, mid, update=True, threads=2)  # another number of threads than the build: still the same bits
    ref_id = make(w, f"{kind} полное", groups, manual, table=t2)
    ref = run(w, ref_id, threads=1)
    assert upd["mode"] == "update" and ref["mode"] == "build"
    a, b = rows_of(w, mid), rows_of(w, ref_id)
    assert a["files"] == b["files"] and a["lines"] == b["lines"] and a["geometry"] == b["geometry"], kind
    figures = [[o.get("volume") for o in r["outputs"]] for r in (first, upd)]
    assert figures[0] != figures[1], kind  # the edit moved the contacts: it is not the same model
    total = ref["stats"]["patches"]
    assert upd["stats"]["patches"] == total and 0 < upd["reused"]["patches"] < total, (kind, upd["reused"], total)
    assert [o["wireframe_id"] for o in upd["outputs"] if o["kind"] != "line"] == [
        o["wireframe_id"] for o in first["outputs"] if o["kind"] != "line"]  # replaced in place: the same ids


def test_t898_an_update_with_nothing_changed_takes_every_patch_and_gives_the_same_bytes(scene) -> None:  # type: ignore[no-untyped-def]
    kind, w, groups, manual, _rows, _at = scene
    mid = make(w, f"{kind} без правок", groups, manual)
    first = run(w, mid)
    before = rows_of(w, mid)
    again = run(w, mid, update=True)
    assert again["reused"]["patches"] == first["stats"]["patches"] > 0, kind
    assert rows_of(w, mid) == before

