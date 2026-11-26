"""McLaughlin (MineLib) as a block model of BF — FOR THE TESTS AND THE MEASURES ONLY (подшаг 21.13; ТЗ 7.35 Д-21,
8.11; ADR 0028 finding 7, decision 30; questions № 114 and № 116).

The source is `tests/data/ext/omf/minelib/McLaughlin_tutorial.omf` (OMF 2 of Incline Design: `index.json.gz` and
Parquet files in a ZIP — the product refuses this dialect with «сохраните проект в OMF v1», so it is read here
directly with the standard `zipfile`/`gzip`/`json` and `pyarrow`). The model: 140 × 296 × 68 = 2 817 920 blocks of
7,62 × 7,62 × 6,096 m (the corners of a block are fractions of the grid size; the order of the file is checked, not
assumed), the fields `au`, `density`, `rocktype` (rock / air), `hardness`; 2 140 342 blocks are rock — the size of the
MineLib set «mclaughlin_limit».

    m = read_omf2()                      the model: dims (nx, ny, nz), size (dx, dy, dz), flat arrays in the order of
                                         P = i + nx·(j + ny·k) (k = 0 — the bottom), `au` g/t, `density`, `rock`
    sc = scenario()                      `core/tests/data/pit/mclaughlin.json`: the costs and the angle of the scenario
                                         of Incline, the price and the unit of `au` (the ASSUMPTIONS, question № 116)
    v = cell_values(m, sc, rf)           float64 $ per block at the revenue factor `rf` (the air — 0)
    g, ids = graph(m, sc, rf)            the `PitGraph` of the integers of `econ_table.to_int` over the cones of the
                                         ore (45°, K = 8, one table); `ids` — its nodes in the full graph
The economics is the one of `pit_common.cell_values`, with the data of the scenario: a block is the better of «ore»
(`rf · g · recovery · price − process − haulage of ore` per tonne) and «waste» (`− haulage of waste`).
"""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[3]
OMF = ROOT / "tests" / "data" / "ext" / "omf" / "minelib" / "McLaughlin_tutorial.omf"
PARAMS = Path(__file__).resolve().parents[1] / "data" / "pit" / "mclaughlin.json"
BLOCK_MODEL = "mclaughlin_bm"
GRAMS_PER_TROY_OZ = 31.1034768


class OmfUnreadable(Exception):
    """The OMF 2 file is not the one this reader knows (the layout of Incline 0.3.3)."""


@dataclass(frozen=True)
class McLaughlin:
    dims: tuple[int, int, int]
    size: tuple[float, float, float]
    au: NDArray[np.float64]
    density: NDArray[np.float64]
    rock: NDArray[np.int8]  # codes of `rock_names`
    rock_names: tuple[str, ...]
    hardness: NDArray[np.int8]
    hardness_names: tuple[str, ...]

    @property
    def air(self) -> NDArray[np.bool_]:
        return np.asarray(self.rock == self.rock_names.index("air"))

    @property
    def blocks(self) -> int:
        return int(self.au.shape[0])


def _table(zf: zipfile.ZipFile, name: str, column: str) -> NDArray[Any]:
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(zf.read(name)), columns=[column]).column(column).to_numpy()


def _attribute(elem: dict[str, Any], name: str) -> dict[str, Any]:
    for a in elem["attributes"]:
        if a["name"] == name:
            return dict(a["data"])
    raise OmfUnreadable(f"no attribute «{name}» in the block model")


def read_omf2(path: Path = OMF) -> McLaughlin:
    """The block model of an OMF 2 file of Incline Design (see the module docstring)."""
    import pyarrow.parquet as pq

    with zipfile.ZipFile(path) as zf:
        index = json.loads(gzip.decompress(zf.read("index.json.gz")))
        elem = next((e for e in index["elements"] if e["name"] == BLOCK_MODEL), None)
        if elem is None or elem["geometry"]["type"] != "BlockModel":
            raise OmfUnreadable(f"no block model «{BLOCK_MODEL}»")
        grid = elem["geometry"]["grid"]
        gsize = np.asarray(grid["size"], dtype=np.float64) / np.asarray(grid["count"], dtype=np.float64)
        corners = elem["geometry"]["subblocks"]["subblocks"]["filename"]
        cols = [f"corner_{w}_{a}" for w in ("min", "max") for a in "uvw"]
        t = pq.read_table(io.BytesIO(zf.read(corners)), columns=cols)
        lo = np.stack([t.column(f"corner_min_{a}").to_numpy() for a in "uvw"], axis=1) * gsize
        hi = np.stack([t.column(f"corner_max_{a}").to_numpy() for a in "uvw"], axis=1) * gsize
        size = np.median(hi - lo, axis=0)
        if not np.allclose(hi - lo, size, atol=1e-6):
            raise OmfUnreadable("the blocks are not of one size (sub-blocks)")
        ijk = np.rint(lo / size).astype(np.int64)
        dims = tuple(int(x) for x in ijk.max(axis=0) + 1)
        n = dims[0] * dims[1] * dims[2]
        if n != len(ijk) or not np.allclose(lo, ijk * size, atol=1e-6):
            raise OmfUnreadable("the blocks do not fill a regular grid")
        pos = ijk[:, 0] + dims[0] * (ijk[:, 1] + dims[1] * ijk[:, 2])
        order = None if np.array_equal(pos, np.arange(n)) else np.argsort(pos)  # the order of P, k = 0 — the bottom

        def values(name: str) -> NDArray[Any]:
            data = _attribute(elem, name)
            col = _table(zf, data["values"]["filename"], "index" if data["type"] == "Category" else "number")
            return col if order is None else col[order]

        def names(name: str) -> tuple[str, ...]:
            f = _attribute(elem, name)["names"]["filename"]
            return tuple(str(x) for x in _table(zf, f, "name"))

        return McLaughlin(
            dims=(dims[0], dims[1], dims[2]),
            size=(float(size[0]), float(size[1]), float(size[2])),
            au=values("au").astype(np.float64),
            density=values("density").astype(np.float64),
            rock=values("rocktype").astype(np.int8),
            rock_names=names("rocktype"),
            hardness=values("hardness").astype(np.int8),
            hardness_names=names("hardness"),
        )


def scenario(path: Path = PARAMS) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def rfs(sc: dict[str, Any]) -> list[float]:
    """The revenue factors of the scenario (0,2 … 1,7 step 0,025: 61 shells), rounded as the parameters are."""
    r = sc["revenue_factor"]
    count = round((r["to"] - r["from"]) / r["step"]) + 1
    return [round(r["from"] + i * r["step"], 6) for i in range(count)]


def cell_values(m: McLaughlin, sc: dict[str, Any], rf: float) -> NDArray[np.float64]:
    """$ of every block at the factor `rf`; the air — 0. Costs are per tonne of the block (volume · density)."""
    c, a = sc["costs"], sc["assumptions"]
    price_g = float(a["price_usd_per_troy_oz"]) / GRAMS_PER_TROY_OZ  # $ per gram of gold
    tonnes = m.size[0] * m.size[1] * m.size[2] * m.density
    ore = rf * m.au * float(sc["recovery"]) * price_g - float(c["process"]) - float(c["ore_haulage"])
    v = tonnes * np.maximum(ore, -float(c["waste_haulage"]))
    v[m.air] = 0.0
    return v


def offsets(m: McLaughlin, sc: dict[str, Any]) -> NDArray[np.int32]:
    """The offsets of the template of 21.3 for the cell of the model: the angle and K of the scenario."""
    from bf_core.mine import template as T

    t = T.build_template(m.size[0], m.size[1], m.size[2], T.angle_table([(0.0, float(sc["slope_angle"]))]),
                         k=int(sc["template_k"]))  # fmt: skip
    return np.ascontiguousarray(t.tables[0].offsets)


def graph(m: McLaughlin, sc: dict[str, Any], rf: float, *, cones: bool = True) -> tuple[Any, Any]:
    """(`PitGraph`, `ids`): ids is None for the full graph; with `cones` — the union of the cones of the blocks with
    v′ > 0 and `ids` are its nodes in the full graph (the way the shells of 21.7 solve it)."""
    from bf_core.mine import graph as G
    from bf_core.mine.econ_table import to_int

    air = m.air
    v = cell_values(m, sc, rf)
    full = np.zeros(m.blocks, dtype=np.int64)
    full[~air] = to_int(v[~air]).v
    g = G.build_graph(m.dims, full, air, None, [offsets(m, sc)])
    if not cones:
        return g, None
    return G.restrict_graph(g, G.cone_mask(g))


def describe(m: McLaughlin) -> dict[str, Any]:
    """What `core/tests/data/pit/mclaughlin.json` says of the model (the tests compare)."""
    air = m.air
    return {
        "dims": list(m.dims),
        "size": [round(x, 6) for x in m.size],
        "blocks": m.blocks,
        "rock_blocks": int(m.blocks - air.sum()),
        "air_blocks": int(air.sum()),
        "au_positive": int((m.au > 0).sum()),
        "au_max": float(m.au.max()),
    }


def sanity(m: McLaughlin) -> None:
    """The things the scenario relies on (raises OmfUnreadable): no NaN, the air carries no grade, the rock has a
    density."""
    if not bool(np.all(np.isfinite(m.au))) or not bool(np.all(np.isfinite(m.density))):
        raise OmfUnreadable("gaps in au or density")
    air = m.air
    if bool((m.au[air] != 0).any()) or bool((m.density[~air] <= 0).any()):
        raise OmfUnreadable("grade in the air or no density in the rock")
