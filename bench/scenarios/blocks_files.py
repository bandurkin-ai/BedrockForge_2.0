"""The files of block models for the benches of the import (ТЗ 5.3; Д-13): a Micromine DAT and a Datamine DM of the
model of `tools/gen/blockmodel.py` with sub-blocks, written record by record without keeping the model in memory.

    meta = write_dat(path, rows, fields)     «Для BF» / Micromine .DAT: EAST, NORTH, RL (centres), _EAST, _NORTH, _RL
                                             (sizes) and `fields` number fields (AU, DENSITY, F2 …), IEEE doubles
    meta = write_dm(path, rows, fields)      Datamine .dm: IJK, XC, YC, ZC, XINC, YINC, ZINC and the same fields, the
                                             origin and the counts as the implicit fields XMORIG … NZ
→ {rows, au_sum (math.fsum), au_head (the first 1000 values of AU), size (bytes), seconds}

Records go in the order of the file of `sub_chunks` (P, sz, sy, sx): the import reads them in that order, so what
it keeps equals what was written, row by row. The DM header is written first and patched at the end (the number of
pages is known only then); the pages are 512 words of 8 bytes (`tests/formats/dm_builder.py`).
"""

from __future__ import annotations

import json
import math
import struct
import time
from pathlib import Path
from typing import Any

import numpy as np

DIV = (4, 4, 2)
HEAD = 1000
# the stages of the progress of the job
STAGE_KEYS = {"Чтение": "read", "Прочитано": "read", "Сетка": "grid", "Запись": "write", "Сортировка": "sort"}


def _first(fields: int) -> list[str]:
    return ["AU", "DENSITY", *[f"F{i}" for i in range(2, fields)]]


def _model(rows: int, div: tuple[int, int, int], size: tuple[float, float, float] | None) -> dict[str, Any]:
    from tools.gen.blockmodel import sub_model

    return sub_model(rows, div) if size is None else sub_model(rows, div, size=size)


def write_dat(path: Path, rows: int, fields: int = 20, div: tuple[int, int, int] = DIV,
              size: tuple[float, float, float] | None = None) -> dict[str, Any]:
    from tools.gen.blockmodel import sub_chunks

    from bf_core.blocks import geometry as geo
    from bf_core.blocks.schema import GridV2
    from bf_core.formats.micromine_header import Field, build_header, default_xml

    t0 = time.perf_counter()
    grid = GridV2.of(_model(rows, div, size))
    first = _first(fields)
    names = ["EAST", "NORTH", "RL", "_EAST", "_NORTH", "_RL", *first]
    head = [Field(n, "R", 8, 2) for n in names]
    dtype = np.dtype({"names": [f"f{i}" for i in range(len(names))] + ["eol"],
                      "formats": ["<f8"] * len(names) + ["S2"]})  # fmt: skip
    au: list[float] = []
    au_head = np.empty(0)
    n = 0
    with path.open("wb") as fh:
        fh.write(build_header(head, "cp1251", default_xml(names, "ibm-5347")))
        for p, s, w, vals in sub_chunks(rows, div, 0.25, 1 << 18, 1, fields):
            c = geo.centres(grid, p, s, w)
            d = geo.row_widths(grid, p, s, w)
            cols = [c[:, 0], c[:, 1], c[:, 2], d[:, 0], d[:, 1], d[:, 2], *[vals[f] for f in first]]
            rec = np.empty(len(p), dtype=dtype)
            rec["eol"] = b"\r\n"
            for i, col in enumerate(cols):
                rec[f"f{i}"] = col
            fh.write(rec.tobytes())
            au.append(math.fsum(vals["AU"].tolist()))
            if len(au_head) < HEAD:
                au_head = np.concatenate([au_head, vals["AU"]])[:HEAD]
            n += len(p)
    return {"rows": n, "au_sum": math.fsum(au), "au_head": au_head.tolist(), "size": path.stat().st_size,
            "seconds": round(time.perf_counter() - t0, 1)}  # fmt: skip


def write_dm(path: Path, rows: int, fields: int = 20, div: tuple[int, int, int] = DIV,
             size: tuple[float, float, float] | None = None) -> dict[str, Any]:
    from tools.gen.blockmodel import sub_chunks

    from bf_core.blocks import geometry as geo
    from bf_core.blocks.schema import GridV2
    from tests.formats.dm_builder import WORDS, F, build

    t0 = time.perf_counter()
    model = _model(rows, div, size)
    grid = GridV2.of(model)
    first = _first(fields)
    spec = [F(n) for n in ("IJK", "XC", "YC", "ZC", "XINC", "YINC", "ZINC", *first)]
    origin = zip(("XMORIG", "YMORIG", "ZMORIG"), model["corner"], strict=True)
    spec += [F(n, default=v, implicit=True) for n, v in origin]
    spec += [F(n, default=float(v), implicit=True) for n, v in zip(("NX", "NY", "NZ"), model["counts"], strict=True)]
    width = 7 + fields  # the stored fields of a record
    per = WORDS // width  # whole records of a page
    head = bytearray(build(spec, []))
    header_pages = len(head) // (WORDS * 8)
    written = 0
    au: list[float] = []
    au_head = np.empty(0)
    left = np.empty((0, width))

    def flush(fh: Any, mat: np.ndarray, final: bool = False) -> np.ndarray:
        nonlocal written
        pages = -(-len(mat) // per) if final else len(mat) // per
        if not pages:
            return mat
        take = len(mat) if final else pages * per
        page = np.zeros((pages, WORDS))
        body = np.zeros((pages * per, width))
        body[:take] = mat[:take]
        page[:, : per * width] = body.reshape(pages, per * width)
        fh.write(page.astype("<f8").tobytes())
        written += take
        return mat[take:]

    with path.open("wb") as fh:
        fh.write(head)  # the counts of the pages are patched at the end
        for p, s, w, vals in sub_chunks(rows, div, 0.25, 1 << 18, 1, fields):
            c = geo.centres(grid, p, s, w)
            d = geo.row_widths(grid, p, s, w)
            mat = np.column_stack([p.astype(np.float64), c, d, *[vals[f] for f in first]])
            left = flush(fh, np.concatenate([left, mat]) if len(left) else mat)
            au.append(math.fsum(vals["AU"].tolist()))
            if len(au_head) < HEAD:
                au_head = np.concatenate([au_head, vals["AU"]])[:HEAD]
        flush(fh, left, final=True)
        pages = -(-written // per)
        fh.seek(26 * 8)
        fh.write(struct.pack("<dd", float(header_pages + pages), float(written - (pages - 1) * per)))
    return {"rows": written, "au_sum": math.fsum(au), "au_head": au_head.tolist(), "size": path.stat().st_size,
            "seconds": round(time.perf_counter() - t0, 1)}  # fmt: skip


def write_list_dat(path: Path, rows: int, fields: int = 20, seed: int = 1) -> dict[str, Any]:
    """A DAT of a model that lies in no grid (ADR 0030, a list of blocks): columns of 10 × 10 m, each cut along Z into
    2–8 blocks of random height (`rows` blocks, the last column may be short), EAST, NORTH, RL, _EAST, _NORTH, _RL and
    `fields` number fields (no gaps). The rows are in the order of the columns. → {rows, au_sum, volume, size, seconds};
    `volume` — Σ of the volumes of the blocks (math.fsum)."""
    from bf_core.formats.micromine_header import Field, build_header, default_xml

    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    cols = max(1, rows // 5)
    pieces = rng.integers(2, 9, cols)
    col = np.repeat(np.arange(cols), pieces)
    starts = np.concatenate([[0], np.cumsum(pieces)[:-1]])
    w = rng.uniform(0.3, 1.0, int(pieces.sum()))
    dz = 10.0 * w / np.repeat(np.add.reduceat(w, starts), pieces)
    base = np.repeat(np.concatenate([[0.0], np.cumsum(np.add.reduceat(dz, starts)[:-1])]), pieces)
    z1 = np.cumsum(dz) - base
    col, dz, z1 = col[:rows], dz[:rows], z1[:rows]  # the last column may be short
    n = len(col)
    nx = int(np.ceil(np.sqrt(cols)))
    centre = np.column_stack([1000.0 + (col % nx) * 10.0 + 5.0, 2000.0 + (col // nx) * 10.0 + 5.0,
                              100.0 + z1 - dz / 2])  # fmt: skip
    size = np.column_stack([np.full(n, 10.0), np.full(n, 10.0), dz])
    first = _first(fields)
    values = {f: (rng.lognormal(0.0, 1.0, n) if f == "AU" else rng.normal(2.7, 0.05, n) if f == "DENSITY"
                  else rng.random(n)) for f in first}  # fmt: skip
    names = ["EAST", "NORTH", "RL", "_EAST", "_NORTH", "_RL", *first]
    dtype = np.dtype({"names": [f"f{i}" for i in range(len(names))] + ["eol"],
                      "formats": ["<f8"] * len(names) + ["S2"]})  # fmt: skip
    rec = np.empty(n, dtype=dtype)
    rec["eol"] = b"\r\n"
    for i, arr in enumerate([*centre.T, *size.T, *[values[f] for f in first]]):
        rec[f"f{i}"] = arr
    with path.open("wb") as fh:
        fh.write(build_header([Field(x, "R", 8, 2) for x in names], "cp1251", default_xml(names, "ibm-5347")))
        fh.write(rec.tobytes())
    vol = (size[:, 0] * size[:, 1]) * size[:, 2]
    return {"rows": n, "au_sum": math.fsum(values["AU"].tolist()), "volume": math.fsum(vol.tolist()),
            "tonnes": math.fsum((vol * values["DENSITY"]).tolist()), "size": path.stat().st_size,
            "seconds": round(time.perf_counter() - t0, 1)}  # fmt: skip


def list_model(ctx: Any, work: Path, rows: int, fields: int = 20) -> dict[str, Any]:
    """`prepare` of the benches of a list of blocks: `write_list_dat` imported as a list (outside the timing) →
    {model_id, rows, parents, counts, volume, tonnes, au_sum}."""
    from bf_core.importing.object_blockfile import BlockOptions, import_model, open_source

    made = write_list_dat(work / "list.dat", rows, fields)
    opts = BlockOptions.of({"layout": "list"})
    res = import_model(open_source(work / "list.dat", opts), opts, engine=ctx.engine, store=ctx.store,
                       workdir=work / "cache" / "w0", name="Список блоков", label="list.dat",
                       tick=lambda _f, _m: None, log=lambda _t, _l: None)  # fmt: skip
    (work / "list.dat").unlink()
    return {"model_id": res.model_id, "rows": res.rows, "parents": res.parents, "counts": res.grid["counts"],
            "volume": made["volume"], "tonnes": made["tonnes"], "au_sum": made["au_sum"]}  # fmt: skip


def make(params: dict[str, Any], work: Path, kind: str) -> None:
    """`prepare` of the import scenarios: the project and the file `kind` ("dat" | "dm") of the params in `work`."""
    from . import blocks_common as bc

    path = work / f"model.{kind}"
    div = tuple(int(v) for v in params.get("div", DIV))
    size = tuple(float(v) for v in params["size"]) if params.get("size") else None
    if params.get("layout") == "list":  # a model with no grid: the file is a list of boxes (ADR 0030)
        meta = {**write_list_dat(path, int(params["rows"]), int(params.get("fields", 20))), "ordered": False}
    else:
        meta = (write_dat if kind == "dat" else write_dm)(path, int(params["rows"]), int(params.get("fields", 20)),
                                                            div, size)  # fmt: skip
    bc.Ctx.create(work).close()
    (work / "file.json").write_text(json.dumps({"path": path.name, **meta}), encoding="utf-8")


def imported(params: dict[str, Any], work: Path) -> dict[str, Any]:
    """`run` of the import scenarios: the file of `make` imported as a block model (`import_model`: the file read in
    portions, the roles of the columns found, the grid inferred, the rows sorted and checked, the model written and
    committed). The info: import_s, the seconds of every stage (`stage_<name>_s`), the rows, the checks; peak_mb.
    What is checked (a failure stops the run): the rows and the sub-blocks, no duplicate, no overlap, Σ AU of the
    model equals that of the generator (1e-9) and its first 1000 values equal bit by bit."""
    from bf_core.blocks import store as bm
    from bf_core.db import dialect
    from bf_core.importing.object_blockfile import BlockOptions, import_model, open_source

    from . import blocks_common as bc

    bc.limit_threads(params)
    made = json.loads((work / "file.json").read_text(encoding="utf-8"))
    ctx = bc.Ctx.open(work)
    stages: dict[str, float] = {}
    last: list[Any] = [None, time.perf_counter()]

    def tick(_f: float | None = None, message: str | None = None) -> None:
        key = (message or "").split(":")[0].split(" ")[0]
        now = time.perf_counter()
        if key and key != last[0]:
            if last[0] is not None:
                stages[last[0]] = stages.get(last[0], 0.0) + now - last[1]
            last[0], last[1] = key, now

    info: dict[str, Any] = {"rows": made["rows"], "file_mb": round(made["size"] / 2**20, 1)}
    try:
        opts = BlockOptions.of({"empty": [-99], **({"layout": params["layout"]} if params.get("layout") else {})})
        t0 = time.perf_counter()
        source = open_source(work / made["path"], opts)
        info["open_s"] = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter()
        res = import_model(source, opts, engine=ctx.engine, store=ctx.store, workdir=work / "cache" / "w1",
                           name="Импорт", label=made["path"], tick=tick, log=lambda _text, _level: None)  # fmt: skip
        info["import_s"] = round(time.perf_counter() - t0, 3)
        if last[0] is not None:
            stages[last[0]] = stages.get(last[0], 0.0) + time.perf_counter() - last[1]
        named: dict[str, float] = {}
        for key, seconds in stages.items():
            name = STAGE_KEYS.get(key, key)
            named[name] = named.get(name, 0.0) + seconds
        info.update({f"stage_{k}_s": round(v, 2) for k, v in named.items()})
        info.update(format=source.format, parents=res.parents, sub=res.sub, fields=len(res.fields),
                    div=list(res.grid["div"]))
        if params.get("div"):  # the division found is the one the file was written with
            assert info["div"] == [int(v) for v in params["div"]], (info["div"], params["div"])
        model = ctx.model(res.model_id)
        assert res.rows == model.rows == made["rows"], (res.rows, model.rows, made["rows"])
        bad = [c["n"] if isinstance(c, dict) else c for c in (res.checks["duplicates"], res.checks["overlaps"])]
        assert bad == [0, 0], bad
        head = np.empty(0)
        parts: list[float] = []
        for ch in model.iter(["AU"], 1 << 20):
            parts.append(math.fsum(ch.cols["AU"].tolist()))
            if len(head) < HEAD:
                head = np.concatenate([head, ch.cols["AU"]])[:HEAD]
        total = math.fsum(parts)
        assert abs(total - made["au_sum"]) <= 1e-9 * abs(made["au_sum"]), (total, made["au_sum"])
        if made.get("ordered", True):  # a list is written in the order of its cells, not of the file
            assert np.array_equal(head, np.asarray(made["au_head"])), "the first rows of AU differ from those written"
        if "volume" in made:
            assert abs(float(model.volume().sum()) - made["volume"]) <= 1e-9 * made["volume"], "the volume differs"
        with dialect.write_tx(ctx.engine) as conn:  # the model goes: a run once more starts from the file only
            bm.delete_block_model(conn, res.model_id)
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
