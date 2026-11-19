"""Versions of a line layer (ADR 0015 §1; ТЗ 7.22): assembly, save on a base version, return, move, new layer,
measures. Ported from BF `tests/test_line_editing.py` (to the core level; the API is tests/test_lines_edit_api.py)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, blobs, line_layer_versions, line_layers, objects
from bf_core.formats.lineset import from_lines
from bf_core.lines import store as ls_store
from bf_core.lines import versions
from bf_core.lines.assemble import Edit
from bf_core.lines.measure import layer_measures, line_areas, line_lengths, plan_area, polygon_area
from bf_core.storage.blobs import BlobStore
from bf_core.storage.gc import collect
from tests.dbutil import make_engine

# UTM-size coordinates with digits a float32 cannot hold.
BASE = np.array([512345.678901234, 6789012.345678901, 250.123456789])
SQUARE = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], dtype=float) + BASE


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    yield engine, BlobStore(tmp_path)
    engine.dispose()


def edit(lines: list[tuple[int, bool, np.ndarray | int]], new_attrs: dict[int, dict[str, Any]] | None = None) -> Edit:
    """Lines as the editor sends them: (src lid, closed, vertices of a touched / new line or the count of an
    untouched one). A closed touched line gets its first vertex repeated, as the storage form has it."""
    src, touched, closed, count, parts = [], [], [], [], []
    for s, c, v in lines:
        src.append(s)
        closed.append(int(c))
        if isinstance(v, int):
            touched.append(0)
            count.append(v)
            continue
        p = np.vstack([v, v[:1]]) if c else v
        touched.append(1)
        count.append(len(p))
        parts.append(p)
    xyz = np.vstack(parts) if parts else np.zeros((0, 3))
    return Edit(np.array(src), np.array(touched), np.array(closed), np.array(count), xyz, new_attrs or {})


def _new(engine: sa.Engine, bs: BlobStore, name: str = "РУДА", file_id: int | None = None) -> int:
    with dialect.write_tx(engine) as conn:
        return versions.create_layer(conn, bs, name=name, file_id=file_id, color="#ff8800")


def _read(engine: sa.Engine, bs: BlobStore, layer_id: int) -> tuple[dict[str, Any], Any, np.ndarray]:
    with dialect.read_tx(engine) as conn:
        return versions.read_current(conn, bs, layer_id)


def _journal(engine: sa.Engine, layer_id: int) -> list[dict[str, Any]]:
    with dialect.read_tx(engine) as conn:
        return versions.journal(conn, layer_id)


def _counts(engine: sa.Engine) -> tuple[int, int, int]:
    with dialect.read_tx(engine) as conn:
        return tuple(  # type: ignore[return-value]
            conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one()
            for t in (blobs, blob_owners, line_layer_versions)
        )


# ---- measures (analytic, BF)


def test_polygon_area_tilted_and_length() -> None:
    # A 30 x 20 rectangle turned about two axes: the area in its own plane stays 600, the plan area shrinks.
    a, b = np.radians(35.0), np.radians(62.0)
    rot_z = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    rot_x = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
    flat = np.array([[0, 0, 0], [30, 0, 0], [30, 20, 0], [0, 20, 0]], dtype=float)
    tilted = flat @ (rot_z @ rot_x).T + BASE
    assert polygon_area(tilted) == pytest.approx(600.0, rel=1e-9)
    assert plan_area(tilted) == pytest.approx(600.0 * np.cos(b), rel=1e-9)
    stored = np.vstack([tilted, tilted[:1]])
    m = layer_measures(stored, np.array([0, 5]), np.array([1]))
    assert m["area"] == pytest.approx(600.0, rel=1e-9) and m["length"] == pytest.approx(100.0, rel=1e-9)
    assert m["plan_area"] == pytest.approx(600.0 * np.cos(b), rel=1e-9) and m["closed"] == 1
    assert line_lengths(tilted, np.array([0, 4]))[0] == pytest.approx(80.0, rel=1e-9)  # open: no closing edge


def test_square_10_by_10_has_plan_area_100_and_empty_z_is_not_zero() -> None:
    stored = np.vstack([SQUARE, SQUARE[:1]])
    m = layer_measures(stored, np.array([0, 5]), np.array([1]))
    assert m["plan_area"] == 100.0 and m["area"] == 100.0 and m["length"] == 40.0  # ТЗ 7.22 приёмка
    # Several lines at once: open ones have no area, a point has no length.
    xyz = np.vstack([stored, SQUARE[:2], BASE[None]])
    m = layer_measures(xyz, np.array([0, 5, 7, 8]), np.array([1, 0, 0]))
    assert (m["lines"], m["vertices"], m["points"], m["closed"]) == (3, 8, 1, 1)
    assert m["length"] == 50.0 and m["plan_area"] == 100.0
    # A vertex without Z: the length counts that segment in plan, the area in plan stays 100, not a drop to 0.
    holey = stored.copy()
    holey[2, 2] = math.nan
    lifted = stored.copy()
    lifted[2, 2] += 1000.0
    area, plan = line_areas(holey, np.array([0, 5]), np.array([1]))
    assert plan[0] == 100.0 and area[0] == pytest.approx(50.0)  # the triangle of the vertices with a Z
    assert line_lengths(holey, np.array([0, 5]))[0] == 40.0
    assert line_lengths(lifted, np.array([0, 5]))[0] > 2000.0


# ---- saving, reading, the journal


def test_new_layer_save_read_bitwise_and_journal(project) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    layer_id = _new(engine, bs)
    head, ls, lid = _read(engine, bs, layer_id)
    assert (head["line_count"], head["point_count"], head["rev"], ls.points_only) == (0, 0, 1, False)
    assert [(r["version"], r["action"], r["current"]) for r in _journal(engine, layer_id)] == [(1, "create", True)]

    rng = np.random.default_rng(7)
    trace = rng.normal(size=(50, 3)) * 100 + BASE
    trace[3, 2] = math.nan
    trace[4] = [-0.0, 7_000_000.000_000_001, -0.0]
    point = BASE[None, :] + 0.1
    body = edit(
        [(-1, True, SQUARE), (-1, False, trace), (-1, False, point)],
        {0: {"NAME": "контур", "GRADE": 1.5}, 2: {"NAME": "репер"}},
    )
    version = versions.save(engine, bs, layer_id, body, base_version=1, note="первые линии")

    assert version == 2
    head, ls, lid = _read(engine, bs, layer_id)
    assert ls.xyz.tobytes() == np.vstack([SQUARE, SQUARE[:1], trace, point]).tobytes()  # NaN and −0.0 bit for bit
    assert ls.offsets.tolist() == [0, 5, 55, 56] and ls.closed.tolist() == [1, 0, 0]
    assert lid.tolist() == [0, 1, 2] and head["next_lid"] == 3
    assert ls.attrs.column("NAME").to_pylist() == ["контур", None, "репер"]
    assert ls.attrs.column("GRADE").to_numpy().tobytes() == np.array([1.5, np.nan, np.nan]).tobytes()
    assert (head["line_count"], head["point_count"], head["closed_count"], head["version"]) == (3, 56, 1, 2)
    journal = _journal(engine, layer_id)
    rows = [(r["version"], r["action"], r["line_count"], r["prev_line_count"], r["current"]) for r in journal]
    assert rows == [(2, "save", 3, 0, True), (1, "create", 0, 0, False)]
    assert journal[0]["note"] == "первые линии" and journal[0]["stats"]["plan_area"] == pytest.approx(100.0)
    with dialect.read_tx(engine) as conn:
        file_meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == head["file_id"])).scalar_one()
    assert file_meta == {"format": "editor"}


def _imported(engine: sa.Engine, bs: BlobStore) -> tuple[int, np.ndarray]:
    """A layer as a file reader gives it: the closing vertex of the square differs from the first by 4e-7 m."""
    closing = SQUARE[:1] + [4e-7, 0, 0]
    ring = np.vstack([SQUARE, closing])
    open_line = SQUARE[:3] + [0, 0, 5]
    open_line[1, 2] = math.nan
    ls = from_lines(
        "Контуры",
        [ring, open_line, SQUARE[1:2] + 50],
        attrs={"NAME": ["Восточный", "Скважины", "Репер"], "AU": [1.0, math.nan, 2.5]},
        vattrs={"M": np.arange(9, dtype=float)},
        seq=np.arange(9)[::-1],
    )
    with dialect.write_tx(engine) as conn:
        file_id = ls_store.create_file(conn, bs, name="Контуры.STR", meta={"format": "micromine_str"})
        layer_id = ls_store.put_layer(conn, bs, ls, file_id=file_id)
    return layer_id, ls.xyz.copy()


def test_untouched_lines_come_from_the_base_bit_for_bit_and_carry_attributes(project) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    layer_id, xyz = _imported(engine, bs)
    assert [(r["version"], r["action"]) for r in _journal(engine, layer_id)] == [(1, "import")]
    with dialect.read_tx(engine) as conn:
        first_blobs = {r: s for r, s in ls_store.layer_blobs(conn, layer_id).items()}
    # The editor moves the middle vertex of the open line and appends a new one; the ring is untouched.
    moved = xyz[5:8].copy()
    moved[2] += [0.0, 0.0, 12.5]
    moved = np.vstack([moved, BASE + 77])
    new_line = SQUARE + [100, 0, 0]
    body = edit([(1, False, moved), (0, True, 5), (-1, True, new_line)], {2: {"ROLE": "TIE", "AU": "3,5"}})
    assert versions.save(engine, bs, layer_id, body, base_version=1) == 2
    head, ls, lid = _read(engine, bs, layer_id)
    assert ls.line(1).tobytes() == xyz[:5].tobytes()  # the closing vertex «as in the file», not the first one
    assert ls.line(0).tobytes() == moved.tobytes() and lid.tolist() == [1, 0, 3]
    assert ls.attrs.column("NAME").to_pylist() == ["Скважины", "Восточный", None]
    assert ls.attrs.column("ROLE").to_pylist() == [None, None, "TIE"]  # the field is added to the schema
    assert ls.attrs.column("AU").to_numpy().tobytes() == np.array([math.nan, 1.0, 3.5]).tobytes()
    # Vertex rows and seq: kept for vertices equal bit for bit (the NaN-Z one too), new for moved and added ones.
    m = ls.vattrs.column("M").to_numpy()
    assert m[:2].tolist() == [5.0, 6.0] and np.isnan(m[2:4]).all() and m[4:9].tolist() == [0, 1, 2, 3, 4]
    assert ls.seq[:2].tolist() == [3, 2] and ls.seq[2:4].tolist() == [9, 10] and ls.seq[4:9].tolist() == [8, 7, 6, 5, 4]
    assert head["next_lid"] == 4 and head["rev"] == 2 and head["version"] == 2
    with dialect.read_tx(engine) as conn:
        v1 = conn.execute(sa.select(line_layer_versions.c.id).where(line_layer_versions.c.version == 1)).scalar_one()
        assert versions.version_blobs(conn, v1) == first_blobs  # version 1 owns the files of the import
        schema_ = conn.execute(sa.select(line_layers.c.attr_schema)).scalar_one()
    assert {"name": "ROLE", "kind": "text", "unit": None, "scope": "feature"} in schema_


def test_stale_base_is_refused_and_writes_nothing(project) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    layer_id = _new(engine, bs)
    line = np.array([[0, 0, 0], [1, 1, 1]], dtype=float) + BASE
    versions.save(engine, bs, layer_id, edit([(-1, False, line)]), base_version=1)
    before = _counts(engine)
    with pytest.raises(ApiError) as err:
        versions.save(engine, bs, layer_id, edit([(0, False, line + 5)]), base_version=1)
    assert err.value.status == 409 and err.value.code == "layer_changed"
    assert err.value.extra["version"] == 2 and "Ничего не записано" in err.value.message
    assert _counts(engine) == before and not list(bs.tmp_dir.iterdir())  # no file, no row
    _h, ls, _lid = _read(engine, bs, layer_id)
    assert ls.xyz.tobytes() == line.tobytes()


def test_base_changed_between_read_and_commit_leaves_no_files(project, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The check inside the transaction: a save that wins the race after the other one read the layer."""
    engine, bs = project
    layer_id = _new(engine, bs)
    line = np.array([[0, 0, 0], [1, 1, 1]], dtype=float) + BASE
    real = versions.Prepared.__init__

    def racing(self, store_, built):  # type: ignore[no-untyped-def]
        real(self, store_, built)
        monkeypatch.setattr(versions.Prepared, "__init__", real)
        versions.save(engine, bs, layer_id, edit([(-1, False, line + 9)]), base_version=1)

    monkeypatch.setattr(versions.Prepared, "__init__", racing)
    with pytest.raises(ApiError) as err:
        versions.save(engine, bs, layer_id, edit([(-1, False, line)]), base_version=1)
    assert err.value.status == 409
    assert not list(bs.tmp_dir.iterdir())
    with dialect.read_tx(engine) as conn:
        assert conn.execute(sa.select(sa.func.max(line_layer_versions.c.version))).scalar_one() == 2
    collect(engine, bs, now=1e12)  # whatever the loser placed is not referenced
    _h, ls, _lid = _read(engine, bs, layer_id)
    assert ls.xyz.tobytes() == (line + 9).tobytes()


@pytest.mark.parametrize(
    "body",
    [
        lambda: Edit([-1], [1], [0], [3], np.zeros((2, 3))),  # more vertices named than sent
        lambda: Edit([-1, -1], [1, 1], [0, 0], [0, 2], np.zeros((2, 3))),  # an empty line
        lambda: Edit([-1], [1], [1], [2], np.zeros((2, 3))),  # closed with two vertices
        lambda: Edit([-1], [1], [0], [2], np.array([[0, 0, 0], [np.nan, 1, 1]])),  # X empty
        lambda: Edit([5], [0], [0], [2], np.zeros((0, 3))),  # no line 5 in the layer
        lambda: Edit([-1], [1], [0], [1], np.zeros((1, 3)), {3: {"A": 1}}),  # attributes of a missing line
    ],
)
def test_bad_geometry_is_refused(project, body) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    layer_id = _new(engine, bs)
    before = _counts(engine)
    with pytest.raises(ApiError) as err:
        versions.save(engine, bs, layer_id, body(), base_version=1)
    assert err.value.status == 422 and err.value.code == "bad_geometry"
    assert _counts(engine) == before and not list(bs.tmp_dir.iterdir())


def test_untouched_line_must_match_its_base(project) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    layer_id, _xyz = _imported(engine, bs)
    with pytest.raises(ApiError) as err:
        versions.save(engine, bs, layer_id, edit([(0, True, 4)]), base_version=1)  # the ring has 5 vertices
    assert err.value.status == 422


# ---- return to a version


def test_restore_version_writes_n_plus_one_with_the_same_blobs(project) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    layer_id, xyz = _imported(engine, bs)
    with dialect.read_tx(engine) as conn:
        imported = ls_store.layer_blobs(conn, layer_id)
    first = np.array([[0, 0, 0], [5, 0, 0], [5, 5, 1]], dtype=float) + BASE
    assert versions.save(engine, bs, layer_id, edit([(-1, False, first)]), base_version=1) == 2
    assert (
        versions.save(engine, bs, layer_id, edit([(3, False, first + 1), (-1, False, first[:2])]), base_version=2) == 3
    )
    blobs_before = _counts(engine)[0]

    assert versions.restore(engine, bs, layer_id, 1, expected_version=3) == 4

    with dialect.read_tx(engine) as conn:
        assert ls_store.layer_blobs(conn, layer_id) == imported  # the same SHA-256: no file written
    assert _counts(engine)[0] == blobs_before and not list(bs.tmp_dir.iterdir())
    head, ls, lid = _read(engine, bs, layer_id)
    assert ls.xyz.tobytes() == xyz.tobytes() and lid.tolist() == [0, 1, 2]
    assert head["next_lid"] == 5  # numbers given since are not reused
    journal = _journal(engine, layer_id)
    assert [r["version"] for r in journal] == [4, 3, 2, 1] and journal[0]["action"] == "restore"
    assert journal[0]["note"] == "Возврат к версии 1" and (journal[0]["prev_line_count"], journal[0]["line_count"]) == (
        2,
        3,
    )
    with pytest.raises(ApiError) as err:
        versions.restore(engine, bs, layer_id, 4, expected_version=4)
    assert err.value.status == 422
    with pytest.raises(ApiError) as err:
        versions.restore(engine, bs, layer_id, 99, expected_version=4)
    assert err.value.status == 404
    with pytest.raises(ApiError) as err:
        versions.restore(engine, bs, layer_id, 2, expected_version=3)
    assert err.value.status == 409
    # The files of old versions survive the storage cleanup: the journal owns them.
    collect(engine, bs, now=1e12)
    assert versions.restore(engine, bs, layer_id, 3, expected_version=4) == 5
    _h, ls, lid = _read(engine, bs, layer_id)
    assert ls.line(0).tobytes() == (first + 1).tobytes() and lid.tolist() == [3, 4]


def test_rename_does_not_move_the_base(project) -> None:  # type: ignore[no-untyped-def]
    """Finding 1: `objects.version` grows with a new colour; the save base is `rev`."""
    from bf_core.lines import service

    engine, bs = project
    layer_id = _new(engine, bs)
    with dialect.write_tx(engine) as conn:
        service.update_layer(conn, layer_id, 1, {"color": "#00ff00"})
    assert versions.save(engine, bs, layer_id, edit([(-1, False, SQUARE)]), base_version=1) == 2


# ---- move to another layer, new layer


def test_move_lines_to_another_layer(project) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    a = _new(engine, bs, "A")
    _h, _ls, _lid = _read(engine, bs, a)
    b = _new(engine, bs, "B", file_id=_h["file_id"])
    l0 = np.array([[0, 0, 0], [1, 0, 0]], dtype=float) + BASE
    l1 = np.array([[0, 5, 0], [1, 5, 0], [1, 6, 0]], dtype=float) + BASE
    l2 = np.array([[0, 9, 0], [1, 9, 0]], dtype=float) + BASE
    body = edit([(-1, False, l0), (-1, True, l1), (-1, False, l2)], {0: {"N": 0}, 1: {"N": 1}, 2: {"N": 2}})
    versions.save(engine, bs, a, body, base_version=1)
    versions.save(engine, bs, b, edit([(-1, False, l0 + 50)], {0: {"K": "b"}}), base_version=1)

    moved = edit([(1, True, 4)])
    sv, tv = versions.move_lines(
        engine, bs, a, edit([(0, False, 2), (2, False, 2)]), moved, b, source_base=2, target_base=2
    )

    assert (sv, tv) == (3, 3)
    ha, la, lida = _read(engine, bs, a)
    hb, lb, lidb = _read(engine, bs, b)
    assert la.xyz.tobytes() == np.vstack([l0, l2]).tobytes() and la.attrs.column("N").to_pylist() == [0.0, 2.0]
    assert lida.tolist() == [0, 2] and ha["next_lid"] == 3
    assert lb.xyz.tobytes() == np.vstack([l0 + 50, l1, l1[:1]]).tobytes() and lb.closed.tolist() == [0, 1]
    assert lb.attrs.column("N").to_numpy().tobytes() == np.array([np.nan, 1.0]).tobytes()
    assert lb.attrs.column("K").to_pylist() == ["b", None] and lidb.tolist() == [0, 1] and hb["next_lid"] == 2
    assert [r["action"] for r in _journal(engine, b)][:1] == ["move"]
    with pytest.raises(ApiError) as err:
        versions.move_lines(
            engine, bs, a, edit([(0, False, 2)]), edit([(2, False, 2)]), b, source_base=2, target_base=3
        )
    assert err.value.status == 409
    assert _read(engine, bs, a)[0]["rev"] == 3 and _read(engine, bs, b)[0]["rev"] == 3  # neither changed


def test_new_layer_names_and_cloud_is_not_editable(project) -> None:  # type: ignore[no-untyped-def]
    engine, bs = project
    a = _new(engine, bs, "РУДА")
    file_id = _read(engine, bs, a)[0]["file_id"]
    b = _new(engine, bs, "руда", file_id=file_id)
    c = _new(engine, bs, "РУДА")
    names = {i: _read(engine, bs, i)[0]["name"] for i in (a, b, c)}
    assert names == {a: "РУДА", b: "руда (2)", c: "РУДА"} and _read(engine, bs, c)[0]["file_id"] != file_id
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == a).values(meta={"cloud": {"parts": []}}))
    with pytest.raises(ApiError) as err:
        versions.save(engine, bs, a, edit([(-1, False, SQUARE)]), base_version=1)
    assert err.value.status == 422 and err.value.code == "not_editable"


def test_random_edits_against_a_reference_model(project) -> None:  # type: ignore[no-untyped-def]
    """Risk «ошибки переноса lid, src, атрибутов»: random edit sequences against a plain Python model."""
    engine, bs = project
    layer_id = _new(engine, bs)
    rng = np.random.default_rng(11)
    model: list[dict[str, Any]] = []  # {lid, xyz, closed, tag}
    next_lid, rev = 0, 1
    for _step in range(25):
        lines: list[tuple[int, bool, np.ndarray | int]] = []
        out: list[dict[str, Any]] = []
        attrs: dict[int, dict[str, Any]] = {}
        for line in model:
            r = rng.random()
            if r < 0.15:
                continue  # deleted
            if r < 0.35:
                xyz = line["xyz"] + rng.normal(size=line["xyz"].shape) * 0.1
                lines.append((line["lid"], line["closed"], xyz))
                out.append({**line, "xyz": xyz})
            else:
                n = len(line["xyz"]) + (1 if line["closed"] else 0)
                lines.append((line["lid"], line["closed"], n))
                out.append(line)
        for _k in range(int(rng.integers(0, 3))):
            closed = bool(rng.random() < 0.4)
            xyz = BASE + rng.normal(size=(int(rng.integers(3, 7)), 3)) * 50
            tag = f"t{next_lid}"
            attrs[len(lines)] = {"TAG": tag}
            lines.append((-1, closed, xyz))
            out.append({"lid": None, "xyz": xyz, "closed": closed, "tag": tag})
            next_lid += 1
        order = rng.permutation(len(lines))
        lines = [lines[i] for i in order]
        out = [out[i] for i in order]
        attrs = {int(np.flatnonzero(order == k)[0]): v for k, v in attrs.items()}
        first = next_lid - sum(m["lid"] is None for m in out)
        for m in out:  # new lines are numbered in the order of the layer
            if m["lid"] is None:
                m["lid"], first = first, first + 1
        rev = versions.save(engine, bs, layer_id, edit(lines, attrs), base_version=rev)
        model = out
        _h, ls, lid = _read(engine, bs, layer_id)
        assert lid.tolist() == [m["lid"] for m in model]
        for i, m in enumerate(model):
            want = np.vstack([m["xyz"], m["xyz"][:1]]) if m["closed"] else m["xyz"]
            assert ls.line(i).tobytes() == want.tobytes()
        if model:
            assert ls.attrs.column("TAG").to_pylist() == [m["tag"] for m in model]
    assert rev == 26 and len(_journal(engine, layer_id)) == 26


def test_layer_of_points_stays_points_until_a_line_appears(project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.formats.lineset import from_points

    engine, bs = project
    pts = BASE + np.arange(12.0).reshape(4, 3)
    with dialect.write_tx(engine) as conn:
        file_id = ls_store.create_file(conn, bs, name="pts.dat", meta={"format": "dat"})
        layer_id = ls_store.put_layer(
            conn, bs, from_points("Устья", pts, attrs={"N": ["a", "b", "c", "d"]}), file_id=file_id
        )
    moved = pts[2:3] + [0, 0, 1]
    versions.save(engine, bs, layer_id, edit([(0, False, 1), (2, False, moved), (3, False, 1)]), base_version=1)
    head, ls, lid = _read(engine, bs, layer_id)
    assert ls.points_only and head["points_only"] and ls.attrs.column("N").to_pylist() == ["a", "c", "d"]
    assert ls.xyz.tobytes() == np.vstack([pts[:1], moved, pts[3:]]).tobytes() and lid.tolist() == [0, 2, 3]
    versions.save(engine, bs, layer_id, edit([(0, False, 1), (-1, False, SQUARE)]), base_version=2)
    head, ls, lid = _read(engine, bs, layer_id)
    assert not ls.points_only and ls.offsets.tolist() == [0, 1, 5] and lid.tolist() == [0, 4]
    assert versions.restore(engine, bs, layer_id, 1, expected_version=3) == 4
    head, ls, _lid = _read(engine, bs, layer_id)
    assert ls.points_only and head["points_only"] and ls.xyz.tobytes() == pts.tobytes()
