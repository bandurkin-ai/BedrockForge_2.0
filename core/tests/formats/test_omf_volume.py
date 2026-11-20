"""A volume of OMF v1 as a regular block model of BF2, streamed (ТЗ 7.24, 5.3; ADR 0017 §2–3).

Reference: a cell is checked through its position, not through the numbering of the reader — the centre of a BF2
cell `idx` (`blocks.schema.centers`) projected on the axes of the OMF grid gives (iu, iv, iw) by the steps of the
file, and the value «N» = iu + nu·(iv + nv·iw) written into the file at that OMF cell must be the value stored for
`idx`. Grids 2 × 3 × 4 with variable steps, swapped and negative axes; empty values (NaN, −1); a cancel inside a
block; the temporary files; the memory of 5 million cells × 3 fields (a separate process, peak RSS).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest

from bf_core.blocks import schema as bs
from bf_core.blocks.store import CellsWriter
from bf_core.formats import omf_read as orr
from bf_core.formats import omf_volume as ov
from bf_core.formats.errors import FormatError
from bf_core.storage.blobs import BlobStore
from tests.formats.omf_builder import V1, RawOmf

X, Y, Z = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]


def neg(v: list[float]) -> list[float]:
    return [-c for c in v]


TU, TV, TW = [1.0, 2.0], [1.0, 1.0, 3.0], [0.5] * 4
AXES = {
    "identity": (X, Y, Z),
    "u−Y v+X w−Z": (neg(Y), X, neg(Z)),
    "u+Z v−X w+Y": (Z, neg(X), Y),
    "all negative": (neg(X), neg(Y), neg(Z)),
}


class Sink:
    def __init__(self, grid: dict[str, Any], fields: list[dict[str, Any]]) -> None:
        self.grid, self.fields = grid, fields
        self.idx: list[np.ndarray] = []
        self.values: dict[str, list[np.ndarray]] = {f["name"]: [] for f in fields}
        self.finished = self.discarded = False

    def write(self, idx: Any, values: Any) -> None:
        assert set(values) == set(self.values)
        self.idx.append(np.asarray(idx))
        for k, v in values.items():
            self.values[k].append(np.asarray(v))

    def finish(self) -> Sink:
        self.finished = True
        return self

    def discard(self) -> None:
        self.discarded = True

    def all(self) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        idx = np.concatenate(self.idx) if self.idx else np.zeros(0, np.int64)
        return idx, {k: np.concatenate(v) if v else np.zeros(0) for k, v in self.values.items()}


def volume_file(tmp_path: Path, axes: Any, data: Any = None, tensors: Any = (TU, TV, TW), name: str = "v") -> Path:
    raw = RawOmf()
    nu, nv, nw = (len(t) for t in tensors)
    n = nu * nv * nw
    items = data(raw, n) if data else [raw.scalar("N", np.arange(n, dtype=float), "cells", description="Единицы: g/t")]
    raw.volume(name, tensors, origin=(100.0, 200.0, 300.0), axes=axes, data=items)
    return raw.write(tmp_path / f"{name}.omf", origin=(1000.0, 0.0, 0.0))


def read(path: Path, tmp: Path, make: Any = Sink, **kw: Any) -> tuple[ov.VolumeRead, Any]:
    with orr.open_reader(path) as r:
        (vol,) = r.volumes()
        out = ov.read_volume(r.file, vol, make, tmp_dir=tmp, **kw)
    return out, out.staged


def omf_cell(centres: np.ndarray, origin: np.ndarray, axes: Any, tensors: Any) -> np.ndarray:
    """OMF number of the cell holding each point, from the geometry of the file."""
    ia = []
    for axis, t in zip(axes, tensors, strict=True):
        d = (centres - origin) @ np.asarray(axis, dtype=np.float64)
        ia.append(np.searchsorted(np.cumsum(t), d))
    nu, nv = len(tensors[0]), len(tensors[1])
    return ia[0] + nu * (ia[1] + nv * ia[2])  # type: ignore[no-any-return]


@pytest.mark.parametrize("case", list(AXES))
def test_cells_of_swapped_and_negative_axes_are_exact(tmp_path: Path, case: str) -> None:
    axes = AXES[case]
    out, sink = read(volume_file(tmp_path, axes), tmp_path, portion=5)
    idx, values = sink.all()
    assert sink.finished and not sink.discarded and out.cells == 24 and len(idx) == 24
    assert (np.diff(idx) > 0).all() and idx[0] == 0 and idx[-1] == 23
    grid = bs.check_grid(sink.grid)
    centres = bs.centers(grid, idx.astype(np.uint64))
    assert (values["N"] == omf_cell(centres, np.array([1100.0, 200.0, 300.0]), axes, (TU, TV, TW))).all()
    # the grid: counts and steps along +X, +Y, +Z, the minimum corner
    lo = np.array(bs.extent(grid)[:3])
    for axis, t in zip(axes, (TU, TV, TW), strict=True):
        k = int(np.argmax(np.abs(axis)))
        assert grid["counts"][k] == len(t)
        assert grid["tensors"]["xyz"[k]] == (t if axis[k] > 0 else t[::-1])
        expected = [1100.0, 200.0, 300.0][k] - (0 if axis[k] > 0 else sum(t))
        assert lo[k] == expected
    assert out.fields == [{"name": "N", "kind": "number", "unit": "g/t"}] and out.source["element"] == "v"
    assert not list(tmp_path.glob(".omf-*"))  # temporary files of swapped axes are gone


def test_regular_steps_give_a_size_and_identity_needs_no_temporary_files(tmp_path: Path, monkeypatch: Any) -> None:
    def no_files(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("identity axes must not spill to disk")

    monkeypatch.setattr(ov, "_Mapped", no_files)
    out, sink = read(volume_file(tmp_path, AXES["identity"], tensors=([2.0] * 3, [5.0] * 2, [1.5] * 4)), tmp_path)
    assert sink.grid == {"corner": [1100.0, 200.0, 300.0], "counts": [3, 2, 4], "size": [2.0, 5.0, 1.5], "order": "F"}
    idx, values = sink.all()
    assert (idx == np.arange(24)).all() and (values["N"] == np.arange(24)).all()


def test_empty_values_are_not_loaded(tmp_path: Path) -> None:
    def data(raw: RawOmf, n: int) -> list[str]:
        au = np.arange(n, dtype=float)
        au[[0, 5, 7, 9]] = np.nan
        au[11] = np.inf
        zone = np.zeros(n, dtype=np.int64)
        zone[[0, 1, 7, 11]] = -1
        zone[2] = 99  # outside the legend: empty
        return [raw.scalar("AU", au, "cells"), raw.mapped("ZONE", zone, ["окисл."], "cells")]

    for case in ("identity", "u−Y v+X w−Z"):
        out, sink = read(volume_file(tmp_path, AXES[case], data), tmp_path)
        idx, v = sink.all()
        assert out.cells == 24 - 3 and len(idx) == 21  # cells 0, 7, 11 have no value at all
        assert any("ячеек без значений 3 из 24" in w for w in out.warnings)
        zone = v["ZONE"]
        assert zone.dtype == np.int32 and set(zone.tolist()) == {-1, 0}
        both_empty = ~np.isfinite(v["AU"]) & (zone < 0)
        assert not both_empty.any() and int((zone < 0).sum()) == 2 and int(np.isnan(v["AU"]).sum()) == 2
        assert out.fields[1]["labels"] == ["окисл."]


def test_without_fields_every_cell_is_loaded(tmp_path: Path) -> None:
    out, sink = read(volume_file(tmp_path, AXES["u+Z v−X w+Y"], lambda raw, n: []), tmp_path, portion=7)
    idx, values = sink.all()
    assert (idx == np.arange(24)).all() and values == {} and out.fields == []
    assert any("в ячейках нет данных — загружены все 24 ячеек" in w for w in out.warnings)


def test_texts_dates_and_names_of_fields(tmp_path: Path) -> None:
    def data(raw: RawOmf, n: int) -> list[str]:
        rock = raw.data("StringData", "Порода", raw.strings((["руда", "порода", ""] * 8)[:n]), "cells")
        when = raw.data("DateTimeData", "idx", raw.strings(["2026-01-01T00:00:00Z"] * n, "DateTimeArray"), "cells")
        dup = raw.scalar("порода", np.ones(n), "cells")
        return [rock, when, dup]

    out, sink = read(volume_file(tmp_path, AXES["all negative"], data), tmp_path)
    assert [f["name"] for f in out.fields] == ["Порода", "idx (2)", "порода (2)"]
    assert out.fields[0]["labels"] == ["руда", "порода"] and out.fields[1]["labels"] == ["2026-01-01T00:00:00Z"]
    bs.check_fields(out.fields)  # the names are what the store takes
    _idx, v = sink.all()
    assert v["Порода"].dtype == np.int32 and sorted(set(v["Порода"].tolist())) == [-1, 0, 1]


def test_too_many_labels_skip_the_field(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(bs, "MAX_LABELS", 3)

    def data(raw: RawOmf, n: int) -> list[str]:
        return [raw.data("StringData", "ID", raw.strings([f"s{i}" for i in range(n)]), "cells"),
                raw.scalar("AU", np.ones(n), "cells")]  # fmt: skip

    out, _sink = read(volume_file(tmp_path, AXES["identity"], data), tmp_path)
    assert [f["name"] for f in out.fields] == ["AU"]
    assert any("«ID» — не загружены: больше 3 разных значений" in w for w in out.warnings)


def test_sample_volume_into_cells_writer(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "project")
    out, staged = read(V1, tmp_path, lambda g, f: CellsWriter(store, g, f))
    try:
        assert out.grid == {"corner": [10.0, 10.0, -10.0], "counts": [10, 15, 20], "size": [1.0, 1.0, 1.0],
                            "order": "F"}  # fmt: skip
        assert staged.cells == out.cells == 3000 and out.fields[0]["stats"]["count"] == 3000
        table = pq.read_table(staged.file[0])
        assert table.column_names == ["idx", "Random Data"] and table.num_rows == 3000
        with orr.open_reader(V1) as r:
            (vol,) = r.volumes()
            raw = r.file.array(vol.data[0][1]["array"], "данные", rows=3000)
        assert table.column("Random Data").to_numpy().tobytes() == np.asarray(raw, np.float64).tobytes()
    finally:
        staged.discard()


class Stop(Exception):
    pass


def _big(tmp_path: Path, case: str) -> Path:
    def data(raw: RawOmf, n: int) -> list[str]:
        rng = np.random.default_rng(2)
        return [raw.scalar("A", rng.uniform(size=n), "cells"), raw.scalar("B", rng.uniform(size=n), "cells")]

    return volume_file(tmp_path, AXES[case], data, tensors=([1.0] * 50, [1.0] * 40, [1.0] * 100), name=case[:3])


@pytest.mark.parametrize("case", ["identity", "u−Y v+X w−Z"])
def test_cancel_inside_a_block_leaves_nothing(tmp_path: Path, case: str) -> None:
    path = _big(tmp_path, case)
    store = BlobStore(tmp_path / "project")
    sinks: list[Any] = []

    def make(g: Any, f: Any) -> Any:
        sinks.append(CellsWriter(store, g, f, row_group=4096))
        return sinks[-1]

    seen: list[float] = []

    def stop(fraction: float, _m: str | None) -> None:
        seen.append(fraction)
        if fraction > 0.3:
            raise Stop

    with orr.open_reader(path) as r, pytest.MonkeyPatch.context() as mp:
        from bf_core.formats import omf_file

        mp.setattr(omf_file, "CHUNK", 1 << 16)
        (vol,) = r.volumes()
        with pytest.raises(Stop):
            ov.read_volume(r.file, vol, make, tmp_dir=tmp_path / "cache", progress=stop, portion=8192)
        assert r.file.tick is None  # given back
    assert seen and 0.3 < seen[-1] < 0.6  # stopped inside the first blocks, not between fields
    assert not list((tmp_path / "cache").glob("*")) and not list(store.tmp_dir.glob("*"))
    assert sinks and sinks[0]._done


def test_disk_full_and_out_of_memory(tmp_path: Path, monkeypatch: Any) -> None:
    path = volume_file(tmp_path, AXES["u−Y v+X w−Z"])

    class Usage:
        free = 10

    monkeypatch.setattr(ov.shutil, "disk_usage", lambda _p: Usage())
    sinks: list[Sink] = []
    with pytest.raises(FormatError) as info:
        read(path, tmp_path, lambda g, f: sinks.append(Sink(g, f)) or sinks[-1])
    assert info.value.code == "disk_full" and "ГБ" in info.value.message and info.value.hint
    assert sinks[0].discarded and not list(tmp_path.glob(".omf-*"))

    class Hungry(Sink):
        def write(self, idx: Any, values: Any) -> None:
            raise MemoryError

    with pytest.raises(FormatError) as info:
        read(volume_file(tmp_path, AXES["identity"]), tmp_path, Hungry)
    assert info.value.code == "out_of_memory" and "24 ячеек" in info.value.message


def test_estimate(tmp_path: Path) -> None:
    for case, disk in (("identity", 0), ("u−Y v+X w−Z", 24 * 8)):
        with orr.open_reader(volume_file(tmp_path, AXES[case])) as r:
            (vol,) = r.volumes()
            e = ov.estimate(r.file, vol)
        assert e["cells"] == 24 and e["fields"] == 1 and e["disk"] == disk and e["memory"] > 0


@pytest.mark.windows
def test_maps_are_closed_before_their_files_are_removed(tmp_path: Path) -> None:
    """On Windows a mapped file cannot be removed: after reading and after a failure the folder is empty."""
    path = _big(tmp_path, "u−Y v+X w−Z")
    store = BlobStore(tmp_path / "project")
    cache = tmp_path / "cache"
    out, staged = read(path, cache, lambda g, f: CellsWriter(store, g, f))
    try:
        assert out.cells == 200_000 and not list(cache.glob("*"))
    finally:
        staged.discard()

    class Broken(Sink):
        def write(self, idx: Any, values: Any) -> None:
            raise RuntimeError("sink failed")

    with pytest.raises(RuntimeError):
        read(path, cache, Broken)
    assert not list(cache.glob("*"))


_RSS = """
import json, sys, time
from pathlib import Path
from bf_core.blocks.store import CellsWriter
from bf_core.formats import omf_read as orr, omf_volume as ov
from bf_core.storage.blobs import BlobStore
path, root = Path(sys.argv[1]), Path(sys.argv[2])
store = BlobStore(root)
def hwm():  # VmHWM: the peak of this process (ru_maxrss would keep the peak of the parent across exec)
    line = next(x for x in Path("/proc/self/status").read_text().splitlines() if x.startswith("VmHWM:"))
    return int(line.split()[1]) / 1024
base = hwm()
t = time.perf_counter()
with orr.open_reader(path) as r:
    (vol,) = r.volumes()
    out = ov.read_volume(r.file, vol, lambda g, f: CellsWriter(store, g, f), tmp_dir=root / "cache")
out.staged.discard()
print(json.dumps({"cells": out.cells, "s": time.perf_counter() - t, "base": base, "peak": hwm()}))
"""


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="VmHWM of /proc (Linux)")
def test_five_million_cells_three_fields_peak_rss(tmp_path: Path) -> None:
    nu, nv, nw = 200, 250, 100
    n = nu * nv * nw

    def data(raw: RawOmf, n_: int) -> list[str]:
        k = np.arange(n_)
        au = (k % 997) * 0.01
        au[k % 5 == 0] = np.nan
        return [raw.scalar("AU", au, "cells"), raw.scalar("CU", (k % 101) * 0.5, "cells"),
                raw.mapped("ZONE", k % 3, ["a", "b", "c"], "cells")]  # fmt: skip

    path = volume_file(tmp_path, (Y, neg(X), Z), data, tensors=([5.0] * nu, [5.0] * nv, [2.0] * nw), name="big")
    r = subprocess.run([sys.executable, "-c", _RSS, str(path), str(tmp_path / "p")], capture_output=True, text=True,
                       timeout=300, cwd=Path(__file__).resolve().parents[2])  # fmt: skip
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["cells"] == n
    assert got["peak"] <= 400, got
