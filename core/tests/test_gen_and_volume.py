"""Synthetic generators and the signed-volume kernel against analytic references (ТЗ 10.2)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen.blockmodel import generate as gen_bm  # noqa: E402
from tools.gen.drillholes import generate as gen_dh  # noqa: E402
from tools.gen.mesh import uv_sphere  # noqa: E402

from bf_core.diag.kernels import signed_volume  # noqa: E402


def test_sphere_volume_within_2e3() -> None:
    v, t = uv_sphere(200_000, radius=100.0)
    vol = signed_volume(v, t.astype(np.int64))
    exact = 4.0 / 3.0 * np.pi * 100.0**3
    assert abs(vol - exact) / exact <= 2e-3


def test_cube_volume_exact_far_from_origin() -> None:
    from bf_core.diag.selftest import _cube

    v, t = _cube(10.0, (500_000.0, 7_000_000.0, 300.0))
    assert abs(signed_volume(v, t.astype(np.int64)) - 1000.0) <= 1e-9 * 1000.0


def test_drillholes_have_missing_values(tmp_path: Path) -> None:
    info = gen_dh(tmp_path, holes=20, samples_per_hole=50, seed=3)
    assert info == {"holes": 20, "samples": 1000}
    text = (tmp_path / "assays.csv").read_text(encoding="utf-8")
    assert "СКВ-00001" in text
    assert len(text.splitlines()) == 1001


def test_blockmodel_rows(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    n = gen_bm(tmp_path / "bm.parquet", 8, 4, 2)
    assert n == 64 and pq.read_metadata(tmp_path / "bm.parquet").num_rows == 64
