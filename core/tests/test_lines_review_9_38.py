"""Review of the core of stage Э9 (9.38): a layer deleted between the read and the write is a 404, not a 500;
the area in its own plane stays vectorised with a few empty Z; the measures of the base are counted only for
the first row of the journal; a move never doubles or copies a line; `remap_wireframes` is gone."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.lines import measure, versions
from bf_core.lines import store as ls_store
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.test_import_job import API
from tests.test_line_versions import BASE, SQUARE, edit
from tests.test_lines_edit_api import body, error_of, geometry, new_layer, save


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    yield engine, BlobStore(tmp_path)
    engine.dispose()


def _new(engine: sa.Engine, bs: BlobStore, name: str) -> int:
    with dialect.write_tx(engine) as conn:
        return versions.create_layer(conn, bs, name=name)


def _head(engine: sa.Engine, layer_id: int) -> dict[str, Any]:
    with dialect.read_tx(engine) as conn:
        return versions.layer_head(conn, layer_id)


def _deleted_before_write(monkeypatch: pytest.MonkeyPatch, victim: int) -> None:
    """The file of the layer `victim` is deleted after the read, just before the write transaction checks it."""
    real = versions._apply

    def racing(conn, bs, head, *args, **kw):  # type: ignore[no-untyped-def]
        if int(head["id"]) == victim:
            ls_store.delete_file(conn, int(head["file_id"]))
        return real(conn, bs, head, *args, **kw)

    monkeypatch.setattr(versions, "_apply", racing)


def _is_not_found(err: pytest.ExceptionInfo[ApiError]) -> None:
    assert err.value.status == 404 and err.value.code == "line_layer_not_found"
    assert "не найден" in err.value.message and err.value.hint


# ---- 1. deleted between the read and the write: 404 for save, restore, move


def test_save_of_a_layer_deleted_meanwhile_is_404(project, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    layer_id = _new(engine, bs, "РУДА")
    _deleted_before_write(monkeypatch, layer_id)
    with pytest.raises(ApiError) as err:
        versions.save(engine, bs, layer_id, edit([(-1, True, SQUARE)]), base_version=1)
    _is_not_found(err)
    assert not list(bs.tmp_dir.iterdir())


def test_restore_of_a_layer_deleted_meanwhile_is_404(project, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    layer_id = _new(engine, bs, "РУДА")
    assert versions.save(engine, bs, layer_id, edit([(-1, True, SQUARE)]), base_version=1) == 2
    _deleted_before_write(monkeypatch, layer_id)
    with pytest.raises(ApiError) as err:
        versions.restore(engine, bs, layer_id, 1, expected_version=2)
    _is_not_found(err)


def test_move_to_a_layer_deleted_meanwhile_is_404_and_the_source_stays(project, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    a, b = _new(engine, bs, "A"), _new(engine, bs, "B")  # two files: deleting B leaves A
    line = SQUARE[:2]
    versions.save(engine, bs, a, edit([(-1, False, line), (-1, False, line + 5)]), base_version=1)
    _deleted_before_write(monkeypatch, b)
    with pytest.raises(ApiError) as err:
        versions.move_lines(
            engine, bs, a, edit([(0, False, 2)]), edit([(1, False, 2)]), b, source_base=2, target_base=1
        )
    _is_not_found(err)
    assert _head(engine, a)["rev"] == 2 and _head(engine, a)["line_count"] == 2  # one transaction: nothing moved


# ---- 2. the area in its own plane with empty Z; the measures of the base only for the first journal row


def _squares(n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Closed tilted quadrangles of 4–7 vertices (storage form), some open lines between them."""
    parts, counts, closed = [], [], []
    for i in range(n):
        k = int(rng.integers(4, 8))
        p = BASE + rng.normal(0, 1e3, 3) + rng.normal(0, 10.0, (k, 3))
        c = i % 5 != 0
        if c:
            p = np.vstack([p, p[:1]])
        parts.append(p)
        counts.append(len(p))
        closed.append(int(c))
    return np.vstack(parts), np.concatenate([[0], np.cumsum(counts)]).astype(np.int64), np.array(closed, np.uint8)


def _reference_areas(xyz: np.ndarray, offsets: np.ndarray, closed: np.ndarray) -> np.ndarray:
    """The area in its own plane as before 9.38 with an empty Z in the layer: `polygon_area` line by line."""
    out = np.zeros(len(closed))
    for i in np.flatnonzero(closed):
        pts = xyz[offsets[i] : offsets[i + 1]]
        out[i] = measure.polygon_area(pts[~np.isnan(pts[:, 2])])
    return out


def test_one_empty_z_does_not_send_every_line_through_python(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(938)
    xyz, offsets, closed = _squares(2000, rng)
    gappy = [1, 2, 777, 1999]  # an empty Z inside, at the first vertex (the closing one too), several
    xyz[offsets[1] + 2, 2] = math.nan
    xyz[[offsets[2], offsets[3] - 1], 2] = math.nan
    xyz[offsets[777] : offsets[777] + 2, 2] = math.nan
    xyz[offsets[1999] + 1, 2] = math.nan
    xyz[offsets[5] + 1, 2] = math.nan  # an open line: no area, no loop
    reference = _reference_areas(xyz, offsets, closed)

    calls: list[int] = []
    real = measure.polygon_area
    monkeypatch.setattr(measure, "polygon_area", lambda pts: calls.append(len(pts)) or real(pts))
    area, plan = measure.line_areas(xyz, offsets, closed)

    assert len(calls) == len(gappy)  # before 9.38: every closed line (1600)
    assert area[gappy].tobytes() == reference[gappy].tobytes()  # the lines with gaps: as before, bit for bit
    np.testing.assert_allclose(area, reference, rtol=1e-12, atol=0)
    assert np.all(area[closed == 0] == 0) and np.isfinite(area).all() and np.isfinite(plan).all()
    full = np.isnan(xyz[:, 2]).any()
    assert full  # the layer really has empty Z
    known = xyz.copy()
    known[np.isnan(known[:, 2]), 2] = 0.0
    np.testing.assert_array_equal(  # a layer without gaps: the vectorised path is the one it always took
        measure.line_areas(known, offsets, closed)[0][[0, 3]], area[[0, 3]]
    )


def test_base_measures_are_counted_only_for_the_first_journal_row(project, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    a, b = _new(engine, bs, "A"), _new(engine, bs, "B")
    calls: list[int] = []
    real = versions.stats_of
    monkeypatch.setattr(versions, "stats_of", lambda ls, nl: calls.append(1) or real(ls, nl))
    line = SQUARE[:2]

    versions.save(engine, bs, a, edit([(-1, False, line), (-1, False, line + 5)]), base_version=1)
    assert len(calls) == 2  # the base (row 1) and the new state
    versions.save(engine, bs, a, edit([(0, False, line + 1), (1, False, 2)]), base_version=2)
    assert len(calls) == 3  # the journal has rows: only the new state
    versions.move_lines(engine, bs, a, edit([(0, False, 2)]), edit([(1, False, 2)]), b, source_base=3, target_base=1)
    assert len(calls) == 6  # B has no rows yet: its base; A: none; both new states
    versions.move_lines(engine, bs, b, edit([]), edit([(0, False, 2)]), a, source_base=2, target_base=4)
    assert len(calls) == 8
    with dialect.read_tx(engine) as conn:
        journal = versions.journal(conn, b)
    assert [(r["version"], r["action"], r["line_count"]) for r in journal] == [
        (3, "move", 0), (2, "move", 1), (1, "create", 0),
    ]  # fmt: skip
    assert journal[-1]["stats"]["lines"] == 0 and journal[-1]["stats"]["length3d"] == 0.0


# ---- 3. a move never doubles a line or copies it


def test_bfa_move_that_keeps_the_line_in_the_source_is_422(project) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    a, b = _new(engine, bs, "A"), _new(engine, bs, "B")
    line = SQUARE[:2]
    versions.save(engine, bs, a, edit([(-1, False, line), (-1, False, line + 5)]), base_version=1)
    with pytest.raises(ApiError) as err:
        versions.move_lines(
            engine, bs, a, edit([(0, False, 2), (1, False, 2)]), edit([(1, False, 2)]), b, source_base=2,
            target_base=1,
        )  # fmt: skip
    assert err.value.status == 422 and err.value.code == "bad_geometry"
    assert "Линия 1" in err.value.message and "копирован" in err.value.message and err.value.hint
    assert _head(engine, a)["rev"] == 2 and _head(engine, b)["rev"] == 1 and not list(bs.tmp_dir.iterdir())


def test_json_move_with_a_line_named_twice_is_422(pclient) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Правка"})
    a = new_layer(pclient, "A")
    b = new_layer(pclient, "B", file_id=a["file_id"])
    lines = [(-1, False, SQUARE[:2] + [0, k, 0]) for k in (0, 20)]
    assert save(pclient, a["id"], 1, body(lines)).status_code == 200
    twice = {"lines": [1, 1], "target_id": b["id"], "base_version": 2, "target_base": 1}
    out = error_of(pclient.post(f"{API}/lines/layers/{a['id']}/move-lines", json=twice), 422, "bad_geometry")
    assert "Линия 1" in out["message"] and "дважды" in out["message"]
    head, arrays = geometry(pclient, b["id"])
    assert len(arrays["xyz"]) == 0 and pclient.get(f"{API}/lines/layers/{a['id']}").json()["line_count"] == 2


# ---- 4. dead code of the archive import


def test_remap_wireframes_is_gone() -> None:
    from bf_core.archive import importer, view_remap

    assert not hasattr(view_remap, "remap_wireframes") and "remap_wireframes" not in (view_remap.__doc__ or "")
    assert importer.remap_built_records.__module__ == "bf_core.ops.provenance"  # what renumbers `meta.built`
