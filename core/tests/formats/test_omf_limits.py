"""Limits of OMF v1 found by the review of Э11 (11.21): a block without a known size has an absolute limit (vertices,
triangles, legends — `omf_too_large`, nothing inflated beyond it), the JSON ≤ 64 MiB, the memory of a surface
from the sizes of its blocks and the composition of the wizard not read again; the last report of a volume comes
before its cells are staged; a corner out of the range of floats is a damaged volume; broken cells of a block
model are `block_model_broken` when written to OMF."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import schema as bs
from bf_core.formats import omf_compose as oc
from bf_core.formats import omf_file as of
from bf_core.formats import omf_read as orr
from bf_core.formats import omf_volume as ov
from bf_core.formats.errors import FormatError
from bf_core.formats.omf_write import OmfWriter
from bf_core.importing import object_blocks as ob
from tests.formats.omf_builder import RawOmf
from tests.formats.test_omf_volume import AXES, Sink, read, volume_file


def _surface(tmp_path: Path, n: int, name: str = "s.omf", rng: int = 0) -> Path:
    raw = RawOmf()
    v = np.random.default_rng(rng).uniform(0, 1000, (n, 3))
    raw.surface("Тело", v, np.arange(3 * (n // 3)).reshape(-1, 3))
    return raw.write(tmp_path / name)


def test_vertices_and_triangles_over_the_mesh_limits_are_too_large(tmp_path: Path, monkeypatch: Any) -> None:
    path = _surface(tmp_path, 30)
    monkeypatch.setattr(orr, "MAX_MESH_VERTICES", 29)
    with orr.open_reader(path) as r, pytest.raises(FormatError) as info:
        list(r.surfaces())
    assert info.value.code == "omf_too_large" and "29" in info.value.message and info.value.hint
    monkeypatch.setattr(orr, "MAX_MESH_VERTICES", 30)
    monkeypatch.setattr(orr, "MAX_MESH_TRIANGLES", 9)
    with orr.open_reader(path) as r, pytest.raises(FormatError) as info:
        list(r.surfaces())
    assert info.value.code == "omf_too_large"
    monkeypatch.setattr(orr, "MAX_MESH_TRIANGLES", 10)
    with orr.open_reader(path) as r:
        (mesh,) = r.surfaces()
    assert len(mesh.xyz) == 30 and len(mesh.tri) == 10


def test_any_block_without_a_size_has_an_absolute_limit(tmp_path: Path, monkeypatch: Any) -> None:
    raw = RawOmf()
    uid = raw.array("Vector3Array", np.zeros((100, 3)), "<f8")  # 2 400 bytes that pack into a few dozen
    path = raw.write(tmp_path / "zeros.omf")
    monkeypatch.setattr(of, "MAX_BLOCK", 2400)
    with of.open_omf(path) as f:
        assert f.array(uid, "x").shape == (100, 3)
    monkeypatch.setattr(of, "MAX_BLOCK", 2399)
    seen: list[int] = []
    with of.open_omf(path) as f, pytest.raises(FormatError) as info:
        real = f.inflate
        f.inflate = lambda *a, **k: (seen.append(len(p)) or p for p in real(*a, **k))  # type: ignore[method-assign]
        f.array(uid, "x")
    assert info.value.code == "omf_too_large" and sum(seen) <= 2399
    with of.open_omf(path) as f, pytest.raises(FormatError) as info:
        f.array(uid, "x", max_rows=99)
    assert info.value.code == "omf_too_large" and "99" in info.value.message


def test_a_legend_over_the_labels_is_not_inflated_nor_listed(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(bs, "MAX_LABELS", 3)

    def data(raw: RawOmf, n: int) -> list[str]:
        legend = raw.add("Legend", name="L", description="", values=raw.array("ScalarArray", np.arange(1000.0), "<f8"))
        codes = raw.array("ScalarArray", np.zeros(n), "<i8")
        mapped = raw.data("MappedData", "Порода", codes, "cells", legends=[legend])
        return [mapped, raw.scalar("AU", np.ones(n), "cells")]

    inflated: list[int] = []
    real = of.OmfFile.inflate

    def spy(self: of.OmfFile, block: of.Block, what: str, *a: Any, **k: Any) -> Any:
        for part in real(self, block, what, *a, **k):
            if "легенды" in what:
                inflated.append(len(part))
            yield part

    monkeypatch.setattr(of.OmfFile, "inflate", spy)
    out, _sink = read(volume_file(tmp_path, AXES["identity"], data), tmp_path)
    assert [f["name"] for f in out.fields] == ["AU"]
    assert any("«Порода» — не загружены: больше 3 подписей" in w for w in out.warnings)
    assert sum(inflated) <= 3 * 8  # not the 1 000 values of the legend


def test_the_json_limit_counts_python_objects() -> None:
    assert of.MAX_JSON == 64 << 20


def test_memory_of_a_surface_comes_from_its_blocks(tmp_path: Path) -> None:
    n = 60_000
    path = _surface(tmp_path, n)
    size = path.stat().st_size
    need = ob.memory(path, size)
    assert need >= 12 * (24 * n + 12 * (n // 3)) > 4 * size  # the file alone would say ≈ 1.5 MB
    assert need <= 12 * (24 * 3 * n + 24 * 8 * n)


def test_the_composition_of_the_wizard_is_not_read_again(tmp_path: Path, monkeypatch: Any) -> None:
    path = _surface(tmp_path, 30, "кеш.omf")
    calls: list[int] = []
    real = oc.compose
    monkeypatch.setattr(oc, "compose", lambda f: calls.append(1) or real(f))
    oc.describe(path)  # the inspection of the wizard
    for _ in range(3):  # every submission of the job estimates its memory
        ob.memory(path, path.stat().st_size)
    assert len(calls) == 1
    _surface(tmp_path, 60, "кеш.omf", rng=1)  # the file changed: read again
    ob.memory(path, path.stat().st_size)
    assert len(calls) == 2


class Stop(Exception):
    pass


def _stop_at_the_end(_fraction: float, message: str | None = None) -> None:
    if (message or "").startswith("Прочитано ячеек"):  # the last report of `read_volume`
        raise Stop


def test_a_cancel_at_the_last_report_stages_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    sinks: list[Sink] = []
    for case in ("identity", "u−Y v+X w−Z"):
        with pytest.raises(Stop):
            read(volume_file(tmp_path, AXES[case]), tmp_path, lambda g, f: sinks.append(Sink(g, f)) or sinks[-1],
                 progress=_stop_at_the_end)  # fmt: skip
        assert sinks[-1].discarded and not sinks[-1].finished
    from bf_core.blocks import store as bm
    from bf_core.storage.blobs import BlobStore

    synced: list[int] = []
    monkeypatch.setattr(bm.os, "fsync", lambda fd: synced.append(fd))
    store = BlobStore(tmp_path / "project")
    with pytest.raises(Stop):
        read(volume_file(tmp_path, AXES["identity"]), tmp_path, lambda g, f: bm.CellsWriter(store, g, f),
             progress=_stop_at_the_end)  # fmt: skip
    assert synced == [] and not list(store.tmp_dir.glob("*"))  # no fsync, no SHA-256 of the whole file


@pytest.mark.parametrize(
    "project, origin, tensors, axes",
    [
        (1e308, 1e308, ([1.0, 2.0], [1.0], [1.0]), "identity"),  # the origin of the project + of the grid: inf
        (0.0, 0.0, ([1e308, 1e308], [1.0], [1.0]), "all negative"),  # the sum of the steps overflows
        (0.0, 1.7e308, ([1e308], [1.0], [1.0]), "identity"),  # the far side of the grid: inf
    ],
)
def test_a_grid_out_of_the_range_of_floats_is_damaged(
    tmp_path: Path, project: float, origin: float, tensors: Any, axes: str
) -> None:
    raw = RawOmf()
    raw.volume("v", tensors, origin=(origin, 0.0, 0.0), axes=AXES[axes])
    path = raw.write(tmp_path / "huge.omf", origin=(project, 0.0, 0.0))
    with orr.open_reader(path) as r, np.errstate(over="ignore"):
        (vol,) = r.volumes()
        with pytest.raises(FormatError) as info:
            ov.volume_grid(vol)
    assert info.value.code == "omf_damaged" and "диапазона" in info.value.message


@pytest.mark.parametrize(
    "cells",
    [
        lambda _f: iter([(np.array([5, 3]), np.array([1.0, 2.0]))]),  # out of order
        lambda _f: iter([(np.array([1, 2, 3]), np.array([1.0]))]),  # of other sizes
        lambda _f: iter([(np.array([0, 10**9]), np.array([1.0, 2.0]))]),  # beyond the grid
    ],
)
def test_broken_cells_are_block_model_broken(tmp_path: Path, cells: Any) -> None:
    path = tmp_path / "out.omf"
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 2], "size": [1.0, 1.0, 1.0]}
    with pytest.raises(FormatError) as info, OmfWriter(path, "p") as w:
        w.add_volume("Модель", grid, [{"name": "Au", "kind": "number"}], cells)
    assert info.value.code == "block_model_broken" and "«Модель»" in info.value.message and info.value.hint
    assert not path.exists() and not path.with_name(path.name + ".part").exists()
