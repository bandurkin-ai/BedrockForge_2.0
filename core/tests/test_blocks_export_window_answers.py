"""The answers of the core the window «Экспорт блочной модели» is read against (подшаг 17.38; ТЗ 8.7.7, 7.35 Д-17;
ADR 0023 §12, decision 20; e17-api.md §11): `POST /blocks/{id}/export` → `{source, id}` and the job `blocks.export`
followed to its end (`{path, format, rows, total, bytes, warnings}`) for DAT, CSV (the delimiters, the decimal mark,
the header of Micromine), Parquet and OMF, with the fields chosen, the filter of the library and the quick
restrictor, the code of «пусто» and the encoding; the refusals of the request in Russian (409 `files_exist`, 422
`bad_param {param}`, `bad_path`, `omf_subblocks`, 404) and the failures of the job (`export_encoding`,
`empty_code_taken`, `export_empty`); the model as the properties show it (`GET /blocks/{id}`).

The file `tests/data/blocks/export_window_answers.json` is read by vitest (`ui/test/blocks-export-*.test.ts`).
Regenerate: `BF_REGEN_BLOCKS_EXPORT=1 uv run pytest -q tests/test_blocks_export_window_answers.py`. What changes
from run to run (the numbers of the jobs, the times, the folder of the files, the bytes of an OMF) is not stored;
the floats are kept to 9 digits."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import APIRouter

from bf_core.blocks import routes_export
from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_archive import wait_job
from tests.test_blocks_fields_store import make_model
from tests.test_datamine_blocks import octree_dm

API = "/api/v1"
BL = API + "/blocks"
DATA = Path(__file__).parent / "data" / "blocks" / "export_window_answers.json"
REGEN = os.environ.get("BF_REGEN_BLOCKS_EXPORT") == "1"
SKIPPED = ("created_at", "updated_at", "at", "seconds", "stages")


@pytest.fixture()
def ec(pclient, tmp_path: Path):  # type: ignore[no-untyped-def]
    """A core with a project; the route of the export is mounted until the line of the orchestrator names it
    (`blocks/router.py`)."""
    if not any(getattr(r, "path", "") == f"{BL}/{{bm_id}}/export" for r in pclient.app.routes):
        mount = APIRouter(prefix="/blocks")
        mount.include_router(routes_export.router)
        pclient.app.include_router(mount, prefix=API)
    assert pclient.post(f"{API}/projects", json={"name": "Экспорт блоков"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    pclient.p = SimpleNamespace(engine=project.engine, folder=project.folder, store=BlobStore(project.folder))
    pclient.out = tmp_path / "out"
    pclient.out.mkdir()
    pclient.tmp = tmp_path
    return pclient


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def path_of(c: Any, name: str) -> str:
    return str(c.out / name)


def ask(c: Any, mid: int, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /blocks/{id}/export`; the job followed to its end when the core took the request."""
    r = c.post(f"{BL}/{mid}/export", json=body)
    out: dict[str, Any] = {"request": body, "answer": answer(r)}
    if r.status_code == 200:
        started = r.json()
        assert sorted(started) == ["id", "source"]
        job = wait_job(c, started["source"], started["id"], timeout=120)
        keys = ("kind", "title", "queue", "status", "progress", "message", "error", "result")
        out["job"] = {k: job.get(k) for k in keys}
        out["log"] = c.get(f"{API}/jobs/{started['source']}/{started['id']}/log").json()
        out["answer"] = {"status": 200, "body": {"keys": sorted(started), "source": type(started["source"]).__name__}}
    return out


def sub_model(c: Any) -> int:
    path = c.tmp / "sub.dm"
    octree_dm(path)
    r = c.post(f"{API}/import/objects", json={"files": [str(path)]})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=120)
    assert job["status"] == "done", job
    return int(job["result"]["files"][0]["object_id"])


def kanji_model(c: Any) -> int:
    """Two blocks whose label has characters the code page of DAT has not (`export_encoding`)."""
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 1, 1], "size": [1.0, 1.0, 1.0]}
    fields = [{"name": "DOM", "dtype": "cat", "labels": ["鉱石", "waste"]}]
    with bm.CellsWriterV2(c.p.store, grid, fields, row_group=100) as writer:
        writer.write(np.arange(2, dtype=np.uint64), {"DOM": np.array([0, 1], dtype=np.int32)})
        staged = writer.finish()
    try:
        with dialect.write_tx(c.p.engine) as conn:
            return bm.commit_block_model(
                conn, c.p.store, staged, name="Иероглифы", source={"format": "dat", "file": "k.dat"}
            )
    finally:
        staged.discard()


def collect(c: Any) -> dict[str, Any]:
    mid = make_model(c.p, c.p.store, name="Повёрнутая")
    sid = sub_model(c)
    fid = c.post(f"{API}/filters", json={"name": "Руда", "rules": {"cond": {"field": "AU", "op": ">", "value": 1}}})
    assert fid.status_code == 200, fid.text
    flt = fid.json()["id"]

    def p(name: str) -> str:
        return path_of(c, name)

    runs = {
        "dat": ask(c, mid, {"format": "dat", "path": p("model.dat")}),
        "dat_fields": ask(
            c,
            mid,
            {"format": "dat", "path": p("fields.dat"), "fields": ["DOM", "AU"], "empty": -99, "encoding": "bocu1"},
        ),  # fmt: skip
        "dat_filtered": ask(
            c,
            mid,
            {
                "format": "dat",
                "path": p("filtered.dat"),
                "fields": ["AU", "N"],
                "filter_id": flt,
                "restrict": {"cond": {"field": "N", "op": ">", "value": 0}},
            },
        ),
        "csv": ask(c, mid, {"format": "csv", "path": p("model.csv")}),
        "csv_micromine": ask(
            c,
            mid,
            {
                "format": "csv",
                "path": p("micromine.csv"),
                "header": "micromine",
                "delimiter": ",",
                "decimal": ".",
                "encoding": "utf-8",
            },
        ),  # fmt: skip
        "csv_tab": ask(
            c,
            mid,
            {
                "format": "csv",
                "path": p("tab.csv"),
                "delimiter": "\t",
                "decimal": ",",
                "encoding": "cp1251",
                "empty": -999,
            },
        ),  # fmt: skip
        "parquet": ask(c, mid, {"format": "parquet", "path": p("model.parquet"), "fields": ["AU", "CU"]}),
        "omf": ask(c, mid, {"format": "omf", "path": p("model.omf"), "fields": ["AU", "DOM"]}),
        "sub_dat": ask(c, sid, {"format": "dat", "path": p("sub.dat")}),
        "sub_parquet": ask(c, sid, {"format": "parquet", "path": p("sub.parquet")}),
        "extension_added": ask(c, mid, {"format": "csv", "path": p("noext")}),
    }
    refused = {
        "files_exist": ask(c, mid, {"format": "dat", "path": p("model.dat")}),
        "overwrite": ask(c, mid, {"format": "dat", "path": p("model.dat"), "overwrite": True}),
        "omf_subblocks": ask(c, sid, {"format": "omf", "path": p("sub.omf")}),
        "omf_no_fields": ask(c, mid, {"format": "omf", "path": p("none.omf"), "fields": []}),
        "inside_project": ask(c, mid, {"format": "dat", "path": str(c.p.folder / "m.dat")}),
        "relative": ask(c, mid, {"format": "dat", "path": "m.dat"}),
        "no_folder": ask(c, mid, {"format": "dat", "path": p("нет/m.dat")}),
        "no_path": ask(c, mid, {"format": "dat", "path": ""}),
        "format": ask(c, mid, {"format": "gpx", "path": p("x.gpx")}),
        "encoding": ask(c, mid, {"format": "dat", "path": p("e.dat"), "encoding": "ebcdic"}),
        "delimiter": ask(c, mid, {"format": "csv", "path": p("d.csv"), "delimiter": "x"}),
        "decimal_is_delimiter": ask(c, mid, {"format": "csv", "path": p("dd.csv"), "delimiter": ",", "decimal": ","}),
        "empty_text": ask(c, mid, {"format": "dat", "path": p("t.dat"), "empty": "abc"}),
        "field_missing": ask(c, mid, {"format": "dat", "path": p("f.dat"), "fields": ["NOPE"]}),
        "filter_missing": ask(c, mid, {"format": "dat", "path": p("g.dat"), "filter_id": 99999}),
        "restrict_field": ask(
            c, mid, {"format": "dat", "path": p("h.dat"), "restrict": {"cond": {"field": "ZZZ", "op": ">", "value": 1}}}
        ),
        "model_missing": ask(c, 99999, {"format": "dat", "path": p("i.dat")}),
        "not_object": {"request": [], "answer": answer(c.post(f"{BL}/{mid}/export", json=[]))},
    }
    failed = {
        "encoding_char": ask(c, kanji_model(c), {"format": "dat", "path": p("k.dat"), "encoding": "cp1251"}),
        "empty_code_taken": ask(c, mid, {"format": "dat", "path": p("taken.dat"), "empty": 3}),
        "nothing_passes": ask(
            c,
            mid,
            {"format": "dat", "path": p("none.dat"), "restrict": {"cond": {"field": "AU", "op": ">", "value": 1e9}}},
        ),  # fmt: skip
    }
    return {
        "rules": "ответы настоящего ядра через маршрут /blocks/{id}/export и задание blocks.export "
        "(test_blocks_export_window_answers.py); номера заданий, времена и папка файлов не хранятся",
        "ids": {"model": mid, "sub": sid, "filter": flt},
        "blocks": answer(c.get(BL)),
        "model": answer(c.get(f"{BL}/{mid}")),
        "sub_model": answer(c.get(f"{BL}/{sid}")),
        "filters": answer(c.get(f"{API}/filters")),
        "runs": runs,
        "refused": refused,
        "failed": failed,
    }


def masked(x: Any, root: str, project: str) -> Any:
    """The floats to 9 digits, the folder of the files as «/выгрузка», no times; the bytes of an OMF (its uid and time
    change with every run) are not stored."""
    if isinstance(x, dict):
        out = {masked(k, root, project): masked(v, root, project) for k, v in x.items() if k not in SKIPPED}
        if out.get("format") == "omf" and "bytes" in out:
            del out["bytes"]
        return out
    if isinstance(x, list):
        return [masked(v, root, project) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}") if math.isfinite(x) else x
    if isinstance(x, str):
        return x.replace(root, "/выгрузка").replace(project, "/проект")
    return x


def test_the_answers_the_window_of_the_export_is_read_against(ec) -> None:  # type: ignore[no-untyped-def]
    got = json.loads(json.dumps(masked(collect(ec), str(ec.out), str(ec.p.folder))))
    for key, run in got["runs"].items():
        assert run["job"]["status"] == "done", (key, run)
    assert got["refused"]["files_exist"]["answer"]["status"] == 409
    assert got["refused"]["omf_subblocks"]["answer"]["body"]["code"] == "omf_subblocks"
    assert {k: v["job"]["error"]["code"] for k, v in got["failed"].items()} == {
        "encoding_char": "export_encoding", "empty_code_taken": "empty_code_taken", "nothing_passes": "export_empty",
    }  # fmt: skip
    if REGEN:
        DATA.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    saved = json.loads(DATA.read_text(encoding="utf-8"))
    assert got == saved, "the answers of the core changed: regenerate with BF_REGEN_BLOCKS_EXPORT=1"
