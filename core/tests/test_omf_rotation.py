"""OMF with a turn about Z (подшаг 17.15; ТЗ 8.7.1, 7.35 Д-17; ADR 0023 решения 6, 20).

A block model of BF2 with `grid.rotation` φ goes to OMF as `axis_u` = (cos φ, sin φ, 0), `axis_v` = (−sin φ, cos φ, 0),
`axis_w` = +Z with `origin` = the corner, and comes back as the same grid: the centres of the blocks agree to 1e−9 m,
the values bit for bit. A turn about X or Y is refused with the text of 8.7.1 (422 `rotation_unsupported`); a model
with sub-blocks is not written (422 `omf_subblocks`, «только после регуляризации»); the sub-less model v2 and the
fields of the files `fld:` are written as the others."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry as geo
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.exporting import omf_job
from bf_core.formats import omf_compose as oc
from bf_core.formats import omf_read as orr
from bf_core.formats import omf_volume as ov
from bf_core.formats.errors import FormatError
from bf_core.formats.omf_file import open_omf
from bf_core.formats.omf_write import OmfWriter
from bf_core.storage.blobs import BlobStore
from tests.formats.omf_builder import RawOmf
from tests.test_blocks_fields_store import GRID, GRID_SUB, add_fields, bc, blocks_router, project  # noqa: F401

API = "/api/v1"
TEXT_8_7_1 = "Поворот вокруг X/Y в v1 не поддерживается."
COUNTS = (5, 4, 3)
FIELDS = [{"name": "Au", "kind": "number", "unit": "g/t"}, {"name": "Порода", "kind": "category",
                                                           "labels": ["руда", "порода", "дайка"]}]  # fmt: skip


class Sink:
    """What `read_volume` gives a sink, as `block_sink` would store it."""

    def __init__(self, grid: dict[str, Any], fields: list[dict[str, Any]]) -> None:
        self.grid, self.fields = grid, fields
        self.idx: list[np.ndarray] = []
        self.values: dict[str, list[np.ndarray]] = {f["name"]: [] for f in fields}
        self.finished = self.discarded = False

    def write(self, idx: Any, values: Any) -> None:
        self.idx.append(np.asarray(idx))
        for k, v in values.items():
            self.values[k].append(np.asarray(v))

    def finish(self) -> Sink:
        self.finished = True
        return self

    def discard(self) -> None:
        self.discarded = True

    def all(self) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        return np.concatenate(self.idx), {k: np.concatenate(v) for k, v in self.values.items()}


def grid_in(phi: float, tensors: bool = False) -> dict[str, Any]:
    g: dict[str, Any] = {"corner": [512_000.125, 5_912_000.5, 100.0], "counts": list(COUNTS), "rotation": phi}
    if tensors:
        g["tensors"] = {"x": [2.0, 3.0, 2.5, 4.0, 2.0], "y": [1.5, 2.0, 2.0, 3.5], "z": [1.0, 2.0, 1.5]}
    else:
        g["size"] = [2.5, 3.0, 1.25]
    return g


def cells_in(n: int = 40, seed: int = 11) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(COUNTS[0] * COUNTS[1] * COUNTS[2], size=n, replace=False)).astype(np.uint64)
    au = rng.lognormal(0.0, 1.0, n)
    au[::5] = np.nan
    rock = rng.integers(-1, 3, n).astype(np.int32)
    rock[::5] = 0  # a cell with no Au keeps its rock: it stays occupied
    return idx, {"Au": au, "Порода": rock}


def write_model(path: Path, grid: dict[str, Any], idx: np.ndarray, values: dict[str, np.ndarray],
                fields: Any = FIELDS) -> None:  # fmt: skip
    with OmfWriter(path, "Проект") as w:
        w.add_volume("Модель", grid, fields, lambda name: [(idx, values[name])])


def read_back(path: Path, tmp: Path, make: Any = Sink) -> tuple[ov.VolumeRead, Any]:
    with orr.open_reader(path) as r:
        (vol,) = r.volumes()
        out = ov.read_volume(r.file, vol, make, tmp_dir=tmp)
    return out, out.staged


def axes_of_file(path: Path) -> tuple[list[float], list[float], list[float]]:
    with open_omf(path) as f:
        (g,) = [o for o in f.objects.values() if o.get("__class__") == "VolumeGridGeometry"]
    return g["axis_u"], g["axis_v"], g["axis_w"]


def centres(grid: Any, idx: np.ndarray) -> np.ndarray:
    return geo.centres(bs.GridV2.of(grid), np.asarray(idx, dtype=np.uint64))


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b, equal_nan=True))


# --- round trip ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("variable", [False, True], ids=["size", "tensors"])
@pytest.mark.parametrize("phi", [30.0, 33.3, -135.5, 45.0, 90.0, -90.0, 180.0])
def test_a_turn_about_z_goes_to_the_axes_and_back_without_loss(tmp_path: Path, phi: float, variable: bool) -> None:
    g = grid_in(phi, variable)
    idx, values = cells_in()
    path = tmp_path / "m.omf"
    write_model(path, g, idx, values)
    s, c = bs.GridV2.of(g).sin_cos()
    u, v, w = axes_of_file(path)
    assert u == [c, s, 0.0] and v == [0.0 - s, c, 0.0] and w == [0.0, 0.0, 1.0]  # axis_u/v from the angle
    if phi % 90 == 0:
        assert all(x in (0.0, 1.0, -1.0) for x in (*u, *v))  # exact on the multiples of 90°
    out, sink = read_back(path, tmp_path)
    got_idx, got = sink.all()
    assert sink.finished and out.cells == len(idx) and out.fields[1]["labels"] == ["руда", "порода", "дайка"]
    if phi % 90 == 0:  # axes along ±X, ±Y: a permutation of the axes — the blocks stay, the cells are renumbered
        assert "rotation" not in sink.grid
        order, want = np.lexsort(np.round(centres(sink.grid, got_idx), 3).T), np.lexsort(np.round(centres(g, idx), 3).T)
        assert np.abs(centres(sink.grid, got_idx)[order] - centres(g, idx)[want]).max() < 1e-9
        assert same(got["Au"][order], values["Au"][want]) and np.array_equal(got["Порода"][order],
                                                                            values["Порода"][want])  # fmt: skip
        return
    assert np.array_equal(got_idx, idx)
    assert sink.grid["corner"] == g["corner"] and sink.grid["counts"] == g["counts"]  # bit for bit
    assert sink.grid.get("size") == g.get("size") and sink.grid.get("tensors") == g.get("tensors")
    assert sink.grid["rotation"] == pytest.approx(phi, abs=1e-12)
    assert same(got["Au"], values["Au"]) and np.array_equal(got["Порода"], values["Порода"])
    assert np.abs(centres(sink.grid, got_idx) - centres(g, idx)).max() < 1e-9  # the centres of the blocks, metres


def test_a_grid_without_a_turn_is_written_and_read_as_before(tmp_path: Path) -> None:
    g = grid_in(0.0)
    idx, values = cells_in()
    path = tmp_path / "m.omf"
    write_model(path, {k: v for k, v in g.items() if k != "rotation"}, idx, values)
    assert axes_of_file(path) == ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])
    _out, sink = read_back(path, tmp_path)
    assert "rotation" not in sink.grid and sink.grid["corner"] == g["corner"] and sink.grid["size"] == g["size"]


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_axes_of_another_program_are_normalised_and_float32_axes_are_accepted(tmp_path: Path, scale: float) -> None:
    c32, s32 = (float(np.float32(math.cos(math.radians(30)))), float(np.float32(math.sin(math.radians(30)))))
    raw = RawOmf()
    raw.volume("v", ([1.0, 1.0], [2.0], [1.0]), origin=(10.0, 20.0, 30.0),
               axes=([scale * c32, scale * s32, 0.0], [-scale * s32, scale * c32, 0.0], [0.0, 0.0, scale]))  # fmt: skip
    path = raw.write(tmp_path / "e.omf")
    assert not oc.describe(path).elements[0].skip
    out, sink = read_back(path, tmp_path)
    assert sink.grid["rotation"] == pytest.approx(30.0, abs=1e-5) and sink.grid["corner"] == [10.0, 20.0, 30.0]
    assert out.cells == 2  # every cell of a grid without data


# --- the other turns --------------------------------------------------------------------------------------------

R = math.sqrt(0.5)
BAD_AXES = {
    "about X": ([1.0, 0.0, 0.0], [0.0, R, R], [0.0, -R, R]),
    "about Y": ([R, 0.0, -R], [0.0, 1.0, 0.0], [R, 0.0, R]),
    "about X and Z": ([R, R, 0.0], [-0.5, 0.5, R], [0.5, -0.5, R]),
    "a mirror": ([R, R, 0.0], [R, -R, 0.0], [0.0, 0.0, 1.0]),
    "w down": ([R, R, 0.0], [R, -R, 0.0], [0.0, 0.0, -1.0]),
    "a skew": ([1.0, 0.0, 0.0], [R, R, 0.0], [0.0, 0.0, 1.0]),
}


@pytest.mark.parametrize("case", list(BAD_AXES))
def test_a_turn_about_x_or_y_is_refused_with_the_text_of_the_tz(tmp_path: Path, case: str) -> None:
    raw = RawOmf()
    raw.volume("v", ([1.0], [1.0], [1.0]), axes=BAD_AXES[case])
    path = raw.write(tmp_path / "e.omf")
    entry = oc.describe(path).elements[0]
    assert not entry.loads and entry.skip == "поворот вокруг X/Y в v1 не поддерживается"
    geometry = {"axis_u": BAD_AXES[case][0], "axis_v": BAD_AXES[case][1], "axis_w": BAD_AXES[case][2],
                "tensor_u": [1.0], "tensor_v": [1.0], "tensor_w": [1.0]}  # fmt: skip
    fake = SimpleNamespace(geometry=geometry, entry=SimpleNamespace(label="Объём «v»"), origin=(0.0, 0.0, 0.0))
    with pytest.raises(ApiError) as e:
        ov.volume_grid(fake)  # type: ignore[arg-type]
    assert e.value.status == 422 and e.value.code == "rotation_unsupported" and e.value.message == TEXT_8_7_1
    assert e.value.hint and e.value.extra["param"] == "grid.rotation"


def test_a_model_turned_about_x_or_y_is_not_written(tmp_path: Path) -> None:
    for turn in ({"x": 10.0}, {"y": -5.0}):
        with pytest.raises(ApiError) as e:
            write_model(tmp_path / "m.omf", {**grid_in(0.0), "rotation": turn}, *cells_in())
        assert e.value.code == "rotation_unsupported" and e.value.message == TEXT_8_7_1
        assert not list(tmp_path.glob("m.omf*"))  # nothing left


def test_a_sink_that_drops_the_turn_is_refused_not_trusted(tmp_path: Path) -> None:
    path = tmp_path / "m.omf"
    write_model(path, grid_in(30.0), *cells_in())
    store = BlobStore(tmp_path / "project")
    with pytest.raises(FormatError) as e:
        read_back(path, tmp_path, lambda g, f: bm.CellsWriter(store, g, f))  # meta v1: no `rotation`
    assert e.value.code == "rotation_unsupported"
    assert not list(store.tmp_dir.glob("*")) if store.tmp_dir.exists() else True


# --- the project: v2 models, files of fields, sub-blocks --------------------------------------------------------


def load_back(p: Any, store: BlobStore, path: Path, tmp: Path) -> tuple[ov.VolumeRead, int]:
    """The import as `object_blocks.write_volumes` does it: `block_sink` → `commit_block_model`."""
    with orr.open_reader(path) as r:
        (vol,) = r.volumes()
        out = ov.read_volume(r.file, vol, ov.block_sink(store), tmp_dir=tmp)
    with dialect.write_tx(p.engine) as conn:
        return out, bm.commit_block_model(conn, store, out.staged, name=out.name, source=out.source)


def test_a_rotated_model_with_a_file_of_a_field_goes_through_the_project(bc, tmp_path: Path) -> None:  # noqa: F811
    p = project(bc)
    store = BlobStore(p.folder)
    from tests.test_blocks_fields_store import make_model, sample

    bm_id = make_model(p, store, name="Повёрнутая")
    idx, values = sample()
    extra = {"Z2": values["AU"] * 2.0}
    add_fields(p, store, bm_id, [{"name": "Z2", "dtype": "f8"}], extra)  # a field in its own file `fld:`
    path = tmp_path / "out" / "m.omf"
    path.parent.mkdir()
    with dialect.read_tx(p.engine) as conn:
        info = bm.block_model_info(conn, bm_id)
        offered = omf_job.exportable(conn)["block_models"]
        omf_job.request(conn, path=str(path), block_model_ids=[bm_id])  # no error
    assert info["grid"]["rotation"] == 30.0 and {f["file"] for f in info["fields"]} != {"cells"}
    assert [m["available"] for m in offered] == [True]
    counts, warnings = omf_job.write_objects(path, block_model_ids=[bm_id], engine=p.engine, store=store)
    assert counts["block_models"] == 1 and counts["cells"] == 350 and not warnings
    u, v, _w = axes_of_file(path)
    s, c = bs.GridV2.of(GRID).sin_cos()
    assert u == [c, s, 0.0] and v == [0.0 - s, c, 0.0]
    out, new_id = load_back(p, store, path, tmp_path)
    with dialect.read_tx(p.engine) as conn:
        info2 = bm.block_model_info(conn, new_id)
        got_idx, got = bm.read_cells(conn, store, new_id)
    assert info2["meta_v"] == 2 and info2["grid"]["rotation"] == pytest.approx(30.0, abs=1e-12)
    assert info2["grid"]["corner"] == GRID["corner"] and info2["grid"]["counts"] == GRID["counts"]
    assert info2["grid"]["size"] == GRID["size"] and out.cells == 350
    assert np.array_equal(got_idx, idx) and same(got["Z2"], extra["Z2"]) and same(got["AU"], values["AU"])
    assert np.array_equal(got["DOM"], values["DOM"])
    assert np.abs(centres(info2["grid"], got_idx) - centres(GRID, idx)).max() < 1e-9
    assert info2["extent"] == pytest.approx(bs.GridV2.of(GRID).extent(), abs=1e-9)


def test_a_model_with_divisions_but_no_sub_blocks_is_written(bc, tmp_path: Path) -> None:  # noqa: F811
    p = project(bc)
    store = BlobStore(p.folder)
    from tests.test_blocks_fields_store import FIELDS as V2_FIELDS
    from tests.test_blocks_fields_store import sample

    idx, values = sample(60)
    with bm.CellsWriterV2(store, GRID_SUB, V2_FIELDS, sub=False) as w:
        w.write(idx, values)
        staged = w.finish()
    with dialect.write_tx(p.engine) as conn:
        bm_id = bm.commit_block_model(conn, store, staged, name="Деление без субблоков")
    path = tmp_path / "m.omf"
    counts, _ = omf_job.write_objects(path, block_model_ids=[bm_id], engine=p.engine, store=store)
    assert counts["cells"] == 60 and path.is_file()


def sub_model(p: Any, store: BlobStore) -> int:
    par = np.array([3, 3, 7, 9, 9], np.uint64)
    s = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 0], [0, 1, 0], [1, 1, 0]])
    w = np.array([[1, 2, 2], [1, 2, 2], [2, 2, 2], [2, 1, 2], [1, 1, 2]])
    with bm.CellsWriterV2(store, GRID_SUB, [{"name": "AU", "dtype": "f8"}]) as wr:
        wr.write(par, {"AU": np.array([1.0, 2.0, np.nan, 4.0, 5.0])}, s, w)
        staged = wr.finish()
    with dialect.write_tx(p.engine) as conn:
        return bm.commit_block_model(conn, store, staged, name="Субблочная")


def test_a_model_with_sub_blocks_is_refused_after_the_regularization_text(bc, tmp_path: Path) -> None:  # noqa: F811
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = sub_model(p, store)
    path = tmp_path / "s.omf"
    with dialect.read_tx(p.engine) as conn:
        (offered,) = omf_job.exportable(conn)["block_models"]
        assert not offered["available"] and "после регуляризации" in offered["reason"]["message"]
        assert offered["reason"]["hint"]
        with pytest.raises(ApiError) as e:
            omf_job.request(conn, path=str(path), block_model_ids=[bm_id])
    assert e.value.status == 422 and e.value.code == "omf_subblocks" and "Субблочная" in e.value.message
    assert "только после регуляризации" in e.value.message and e.value.hint
    with pytest.raises(ApiError) as e2:  # the writer of the job says the same when it is reached anyway
        omf_job.write_objects(path, block_model_ids=[bm_id], engine=p.engine, store=store)
    assert e2.value.code == "omf_subblocks"
    assert not list(tmp_path.glob("s.omf*"))  # nothing left, not even «.part»
    with pytest.raises(FormatError) as e3:
        with OmfWriter(path, "P") as w:
            w.add_volume("М", grid_in(0.0), [], lambda _n: [], sub=True)
    assert e3.value.code == "omf_subblocks" and "после регуляризации" in e3.value.message
    assert not list(tmp_path.glob("s.omf*"))
