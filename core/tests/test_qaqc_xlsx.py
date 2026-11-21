"""The XLSX of a QAQC report (ADR 0020 §8; подшаг 14.14): Т8.4-8 — the book is read by openpyxl, its numbers are
exactly those of the API, the XML of the data sheets of a report by a filter and of one over the records taken by
that filter are equal byte for byte; the names and the order of the sheets; a missing value is an empty cell;
text with «=» gets «'»; a sheet past its limit (1000 by parameter) goes on «Стандарты (2)» with the header; the
pictures are on their sheets; `kind: "pdf"` → 422 `not_available` «Э22»; the file saved by a path with Cyrillic
and spaces (`-m windows`)."""

from __future__ import annotations

import shutil
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.qaqc import report_xlsx as rx
from bf_core.qaqc import service as sv
from bf_core.qaqc.filters import map_types, parse_filter, row_mask
from bf_core.storage.blobs import BlobStore
from tests.test_archive import wait_job
from tests.test_import_job import API
from tests.test_qaqc_import_api import qaqc_core  # noqa: F401 - the fixture
from tests.test_qaqc_report import FILTER, build, data, settings

Q = f"{API}/qaqc"


@pytest.fixture()
def qc(qaqc_core, pclient):  # type: ignore[no-untyped-def]  # noqa: F811
    assert pclient.post(f"{API}/projects", json={"name": "Книги"}).status_code == 200
    return pclient


def png(w: int = 40, h: int = 20) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\0" + b"\xff\x00\x00" * w for _ in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def make_file(c: Any, rid: int, **body: Any) -> dict[str, Any]:
    res = c.post(f"{Q}/reports/{rid}/files", json={"kind": "xlsx", **body})
    assert res.status_code == 202, res.text
    job = wait_job(c, res.json()["source"], res.json()["job_id"], timeout=180)
    assert job["status"] == "done", job
    return job["result"]  # type: ignore[no-any-return]


def book_path(c: Any, rid: int) -> Path:
    p = projects_of(c.core).require_current()
    sha = c.get(f"{Q}/reports/{rid}").json()
    assert sha["files"] and sha["files"][0]["kind"] == "xlsx"
    with dialect.read_tx(p.engine) as conn:
        from bf_core.qaqc import report as rp

        src = BlobStore(p.folder).path_of(rp.file_sha(conn, rid))  # type: ignore[arg-type]
    dst = p.folder.parent / f"отчёт {rid}-{src.name[:8]}.xlsx"  # openpyxl wants the extension
    shutil.copyfile(src, dst)
    return dst


def sheets_xml(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist() if n.startswith("xl/worksheets/sheet")}


def test_t848_book_numbers_and_byte_equal_sheets(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    settings(qc)
    did = data(qc, tmp_path, 900)
    a = build(qc, did, filter=FILTER, charts="none")
    make_file(qc, a["id"])
    p = projects_of(qc.core).require_current()
    recs = sv.open_records(p, did)
    from bf_core.qaqc import records as r

    config = qc.get(f"{Q}/config").json()
    mask = row_mask(recs, parse_filter(FILTER), map_types(recs.text(r.TYPE_CODE), config))
    store = BlobStore(p.folder)
    sub = recs.take(mask)
    staged = sv.stage(store, sub)
    with dialect.write_tx(p.engine) as conn:
        meta = sv.make_meta(sub, {"rows": sub.n}, [], {"kind": "file", "name": "часть", "sha256": "0" * 64}, {})
        part = sv.create(conn, store, staged, name="Часть", meta=meta)
    b = build(qc, part, charts="none")
    make_file(qc, b["id"])
    xa, xb = sheets_xml(book_path(qc, a["id"])), sheets_xml(book_path(qc, b["id"]))
    names = openpyxl.load_workbook(book_path(qc, a["id"]), read_only=True).sheetnames
    assert names == list(rx.SHEETS)
    for k in range(1, 7):  # «Сводка» … «Партии»: equal; «Параметры» (name, hashes, time) is not compared
        name = f"xl/worksheets/sheet{k}.xml"
        assert xa[name] == xb[name], names[k - 1]
    wb = openpyxl.load_workbook(book_path(qc, a["id"]))
    api = qc.post(f"{Q}/calc/standards", json={"report_id": a["id"], "element": "AU"}).json()
    ws = wb["Стандарты"]
    head = [c.value for c in ws[1]]
    rows = [[c.value for c in row] for row in ws.iter_rows(min_row=2, max_row=1 + len(api["groups"]))]
    for g, row in zip(api["groups"], rows, strict=True):
        cell = dict(zip(head, row, strict=False))
        assert (cell["Элемент"], cell["CRM"], cell["Лаборатория"]) == ("AU", g["crm"], g["lab"])
        assert (cell["Среднее"], cell["СКО"], cell["Смещение, %"], cell["Проб"]) == (g["mean"], g["std"], g["bias"],
                                                                                     g["n"])  # fmt: skip
    # the table by samples: the first one after the groups and an empty line
    start = 2 + sum(len(qc.post(f"{Q}/calc/standards", json={"report_id": a["id"], "element": e}).json()["groups"])
                    for e in a["elements"])  # fmt: skip
    assert ws.cell(start, 1).value is None and ws.cell(start + 1, 4).value == "SAMPLE_ID"
    first = [c.value for c in ws[start + 2]]
    assert first[0] == "AU" and isinstance(first[7], float) and first[11] is None  # no moving average yet: empty
    assert first[9] in rx.STATUS.values()
    bl = qc.post(f"{Q}/calc/batches", json={"report_id": a["id"], "element": "AU"}).json()
    wsb = wb["Партии"]
    assert wsb.cell(1, 3).value == "Партия" and wsb.max_row == 1 + sum(
        qc.post(f"{Q}/calc/batches", json={"report_id": a["id"], "element": e}).json()["total"] for e in a["elements"]
    )
    assert wsb.cell(2, 3).value == bl["batches"][0]["batch"] and wsb.cell(2, 6).value == bl["batches"][0]["n"]
    assert wb["Дубликаты"].max_row >= 1 and wb["Параметры"]["B2"].value == "Контроль"


def test_formula_text_long_sheet_and_pictures(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    settings(qc)
    from tools.gen.qaqc import generate, write_csv

    gen = generate(4000, seed=4)
    import pyarrow as pa
    import pyarrow.compute as pc

    batch = gen.table.column("BATCH")
    gen.table = gen.table.set_column(gen.table.schema.get_field_index("BATCH"), "BATCH",
                                     pc.binary_join_element_wise(pa.scalar("="), batch, ""))  # fmt: skip
    path = tmp_path / "формулы.csv"
    write_csv(path, gen)
    m = qc.post(f"{Q}/data/inspect", json={"path": str(path)}).json()["mapping"]
    res = qc.post(f"{Q}/data", json={"name": "Формулы", "source": {"kind": "file", "path": str(path)}, "mapping": m})
    did = wait_job(qc, res.json()["source"], res.json()["job_id"], timeout=120)["result"]["data_id"]
    rep = build(qc, did, charts="all", elements=["AU", "CU"])
    charts = qc.get(f"{Q}/reports/{rep['id']}/charts").json()["charts"]
    std = next(c for c in charts if c["section"] == "standards")
    blank = next(c for c in charts if c["section"] == "blanks")
    for c in (std, blank):
        assert qc.put(f"{Q}/reports/{rep['id']}/images/{c['key']}", content=png()).status_code == 200
    make_file(qc, rep["id"], limit=1000)
    path = book_path(qc, rep["id"])
    wb = openpyxl.load_workbook(path)
    names = wb.sheetnames
    assert names[:3] == ["Сводка", "Стандарты", "Стандарты (2)"] and "Параметры" == names[-1]
    cont = wb["Стандарты (2)"]
    assert [c.value for c in cont[1]][:4] == ["Элемент", "CRM", "Лаборатория", "SAMPLE_ID"]  # the header again
    assert wb["Стандарты"].max_row == 1000
    batches = [c.value for c in wb["Партии"]["C"][1:5]]
    assert all(isinstance(v, str) and v.startswith("'=B") for v in batches)  # text, not a formula
    with zipfile.ZipFile(path) as z:
        media = [n for n in z.namelist() if n.startswith("xl/media/")]
        assert len(media) == 2
        rels = {n: z.read(n).decode() for n in z.namelist() if n.startswith("xl/worksheets/_rels/")}
    blanks_sheet = names.index("Бланки") + 1
    assert set(rels) == {"xl/worksheets/_rels/sheet2.xml.rels", f"xl/worksheets/_rels/sheet{blanks_sheet}.xml.rels"}
    assert len(wb["Стандарты"]._images) == 1 and len(wb["Бланки"]._images) == 1


def test_pdf_not_available_and_save(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    did = data(qc, tmp_path, 200)
    rid = build(qc, did, charts="none")["id"]
    pdf = qc.post(f"{Q}/reports/{rid}/files", json={"kind": "pdf"})
    assert pdf.status_code == 422 and pdf.json()["code"] == "not_available" and "Э22" in pdf.json()["hint"]
    early = qc.post(f"{Q}/reports/{rid}/save", json={"kind": "xlsx", "path": str(tmp_path / "x.xlsx")})
    assert early.status_code == 409 and early.json()["code"] == "file_not_built"
    bad = qc.post(f"{Q}/reports/{rid}/files", json={"kind": "xlsx", "limit": 10})
    assert bad.status_code == 422 and bad.json()["param"] == "limit"
    one = make_file(qc, rid)
    two = make_file(qc, rid)
    assert one["sha256"] == two["sha256"]  # the same report — the same bytes


@pytest.mark.windows
def test_save_to_a_path_with_cyrillic_and_spaces(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    did = data(qc, tmp_path, 200)
    rep = build(qc, did, charts="all")
    charts = qc.get(f"{Q}/reports/{rep['id']}/charts").json()["charts"]
    assert qc.put(f"{Q}/reports/{rep['id']}/images/{charts[0]['key']}", content=png()).status_code == 200
    make_file(qc, rep["id"])
    folder = tmp_path / "Отчёты QAQC" / "партия 1"
    folder.mkdir(parents=True)
    dst = folder / "отчёт контроля 1.xlsx"
    res = qc.post(f"{Q}/reports/{rep['id']}/save", json={"kind": "xlsx", "path": str(dst)})
    assert res.status_code == 200, res.text
    assert res.json() == {"path": str(dst), "size": dst.stat().st_size}
    wb = openpyxl.load_workbook(dst)
    assert wb.sheetnames == list(rx.SHEETS) and len(wb["Стандарты"]._images) == 1
    gone = qc.post(f"{Q}/reports/{rep['id']}/save", json={"kind": "xlsx", "path": str(tmp_path / "нет" / "x.xlsx")})
    assert gone.status_code == 404 and gone.json()["code"] == "folder_not_found"
