"""The export of a block model to a file: CSV, DAT, Parquet (ТЗ 8.7.1; Т8.7-8; ADR 0023 «Замеры»: ci — 1 млн строк × 20
полей ≤ 20 с, full — 20 млн × 20 **≤ 5 мин, ≤ 4 ГБ**).

The model of `tools/gen/blockmodel.py` with sub-blocks (`rows` rows; 25 % of the parents split into eight; the 20
number fields AU, DENSITY, F2 … F19 are exported, the first columns are the centres and the sizes of the rows) in the
project of the scenario; the files go to a folder of their own outside the project (the job refuses a path inside it).
The job `blocks.export` (`export.run_export`, the body of the handler: the columns by portions of 2¹⁸ rows, the rows
written by the writer of the format, the file replaced at the end) for each of the `formats` of the params:
  csv_s, dat_s, parquet_s   the job;  <format>_mb — the size of the file
What is checked (a failure stops the run): the answer of the job — the rows, the path; of the CSV — the lines are the
rows plus the header; the first row and the last one are those of the model (AU and DENSITY to 1e-9 of the print);
of the Parquet — the rows and the first AU bit by bit; of the DAT — the rows of the file read back by the importer.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc

FIELDS = 20
EXT = {"csv": ".csv", "dat": ".dat", "parquet": ".parquet"}


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.sub_model(ctx, int(params["rows"]), fields=FIELDS))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def close(a: float, b: float, rel: float = 1e-9) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-300)


def last_line(path: Path) -> str:
    with path.open("rb") as fh:
        fh.seek(max(0, path.stat().st_size - 4096))
        return fh.read().decode("utf-8", "replace").rstrip("\r\n").splitlines()[-1]


def check_csv(path: Path, model: Any, rows: int) -> None:
    with path.open("rb") as fh:
        lines = sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 24), b""))
        fh.seek(0)
        head = fh.readline().decode("utf-8-sig").strip().split(";")
        first = fh.readline().decode("utf-8").strip().split(";")
    assert lines == rows + 1, (lines, rows)
    ai, di = head.index("AU"), head.index("DENSITY")
    last = last_line(path).split(";")
    for line, at in ((first, 0), (last, rows - 1)):
        got = model.columns(["AU", "DENSITY"], at, at + 1)
        assert close(float(line[ai].replace(",", ".")), float(got["AU"][0])), (line[ai], got["AU"][0])
        assert close(float(line[di].replace(",", ".")), float(got["DENSITY"][0])), (line[di], got["DENSITY"][0])


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import export

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    names = [f["name"] for f in bc.wide_fields(FIELDS)]
    info: dict[str, Any] = {"rows": meta["rows"], "fields": len(names)}
    out = Path(tempfile.mkdtemp(prefix="bf-bench-export-"))
    try:
        model = ctx.model(mid)
        for fmt in params.get("formats", ["csv"]):
            target = out / f"model{EXT[fmt]}"
            body = {"model_id": mid, "format": fmt, "path": str(target), "fields": names, "overwrite": True,
                    "delimiter": ";", "decimal": ",", "header": "plain"}  # fmt: skip
            t0 = time.perf_counter()
            done = export.run_export(ctx.engine, ctx.folder, body)
            info[f"{fmt}_s"] = round(time.perf_counter() - t0, 3)
            info[f"{fmt}_mb"] = round(Path(done["path"]).stat().st_size / 2**20, 1)
            assert done["rows"] == meta["rows"] and Path(done["path"]).is_file(), done
            if fmt == "csv":
                check_csv(Path(done["path"]), model, meta["rows"])
            elif fmt == "parquet":
                import pyarrow.parquet as pq

                pf = pq.ParquetFile(done["path"])
                first = pf.read_row_group(0, columns=["AU"]).column(0)[0].as_py()
                assert pf.metadata.num_rows == meta["rows"], pf.metadata.num_rows
                assert first == float(model.columns(["AU"], 0, 1)["AU"][0]), first
            else:
                from bf_core.importing.object_blockfile import BlockOptions, open_source

                assert open_source(Path(done["path"]), BlockOptions.of({})).rows == meta["rows"]
            Path(done["path"]).unlink()
    finally:
        shutil.rmtree(out, ignore_errors=True)
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info


