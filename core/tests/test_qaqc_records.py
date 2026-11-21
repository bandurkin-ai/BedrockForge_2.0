"""QAQC records and normalization (ADR 0020 §2; подшаг 14.8): a missing value stays NaN; «<0,01» → 0,005 with its
flag; the parent sample found, not found (`no_parent`), a repeated SAMPLE_ID (`dup_ids`, the first is taken); the
previous sample of a batch by (SEQ, row); three ways to write a date and a date of Excel, an unparsed date is
empty and counted; the Parquet cycle is bit for bit; past 5 million rows and 60 elements — 422 with a hint; the
generator gives one file for one seed."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from bf_core.api.errors import ApiError
from bf_core.formats.registry import read_table
from bf_core.formats.table import Column, ReadOptions
from bf_core.qaqc import normalize as nz
from bf_core.qaqc import records as r

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen.qaqc import generate, write_csv  # noqa: E402


def table(**cols: Any) -> dict[str, Column]:
    """Columns as a reader gives them: lists of str / None → text, numpy arrays → number."""
    out = {}
    for name, values in cols.items():
        if isinstance(values, np.ndarray):
            out[name] = Column(name, "number", values.astype(np.float64))
        else:
            out[name] = Column(name, "text", pa.chunked_array([pa.array(values, type=pa.string())]))
    return out


MAP = {"sample_id": "ID", "type": "T", "parent_id": "PAR", "lab": "LAB", "batch": "B", "seq": "SEQ",
       "date": "D", "elements": [{"column": "AU_ppm"}]}  # fmt: skip


def run(mapping: dict[str, Any] | None = None, **cols: Any) -> nz.Normalized:
    return nz.normalize(table(**cols), {**MAP, **(mapping or {})})


def small(**over: Any) -> dict[str, Any]:
    cols: dict[str, Any] = {
        "ID": ["S1", "S2", " s1 ", "S3", "S4", "S5"],
        "T": ["ORIG", "ORIG", "DUP_F", "CRM", "DUP_P", "BLANK"],
        "PAR": [None, None, "S2", None, "nope", None],
        "LAB": ["L1"] * 6,
        "B": ["B1", "B1", "B1", "B2", "B2", None],
        "SEQ": np.array([3, 1, 2, 1, np.nan, 1]),
        "D": ["2024-01-02", "02.01.2024 10:30", "03/01/2024", "45293", "32.01.2024", None],
        "AU_ppm": ["1,5", "<0,01", None, ">100", "н/д", "abc"],
    }
    cols.update(over)
    return cols


def test_values_flags_and_missing() -> None:
    made = run(**small())
    au = made.records.number(r.v("AU"))
    assert au[0] == 1.5 and au[1] == 0.005 and au[3] == 100.0
    assert np.isnan(au[2]) and np.isnan(au[4]) and np.isnan(au[5])  # missing, «н/д», not a number: NaN, not 0
    assert made.records.flags(r.q("AU")).tolist() == [0, r.Q_BELOW, 0, r.Q_ABOVE, 0, 0]
    e = made.counts["elements"]["AU"]
    assert (e["numbers"], e["missing"], e["below"], e["above"], e["bad"]) == (3, 2, 1, 1, 1)
    assert made.counts["below_dl"] == 1 and made.counts["above_dl"] == 1 and made.counts["bad_numbers"] == 1


def test_parent_found_not_found_and_repeated_ids() -> None:
    made = run(**small())
    recs = made.records
    # «S2» → row 1; «nope» — not found; « s1 » repeats S1 (trimmed, case-insensitive): the first row is the parent
    assert recs.index(r.PARENT_ROW).tolist() == [-1, -1, 1, -1, -1, -1]
    assert made.counts["no_parent"] == 1 and made.counts["dup_ids"] == 1
    p = recs.number(r.p("AU"))
    assert p[2] == 0.005 and np.isnan(p[[0, 1, 3, 4, 5]]).all()
    assert recs.text(r.PARENT_LAB).values() == [None, None, "L1", None, None, None]
    dup = run(PAR=[None, None, None, None, "S1", None], **{k: v for k, v in small().items() if k != "PAR"})
    assert dup.records.index(r.PARENT_ROW)[4] == 0  # the first of the two «S1»
    assert dup.records.number(r.p("AU"))[4] == 1.5


def test_row_is_not_its_own_parent() -> None:
    made = run(**small(PAR=["S1", None, None, None, None, None]))
    assert made.records.index(r.PARENT_ROW)[0] == -1 and made.counts["no_parent"] == 1


def test_previous_sample_of_the_batch() -> None:
    made = run(**small())
    # B1: SEQ 1 (row 1), 2 (row 2), 3 (row 0); B2: SEQ 1 (row 3), NaN (row 4) — after; row 5 has no batch
    assert made.records.index(r.PREV_ROW).tolist() == [2, -1, 1, -1, 3, -1]
    b = made.records.number(r.b("AU"))
    assert b[2] == 0.005 and np.isnan(b[0]) and b[4] == 100.0 and np.isnan(b[1])  # row 0 follows row 2 (NaN)
    same = run(**small(SEQ=np.array([np.nan] * 6)))  # no SEQ: the order of the file
    assert same.records.index(r.PREV_ROW).tolist() == [-1, 0, 1, -1, 3, -1]


def test_batch_is_per_laboratory() -> None:
    made = run(**small(LAB=["L1", "L2", "L1", "L1", "L1", "L1"]))
    assert made.records.index(r.PREV_ROW).tolist() == [2, -1, -1, -1, 3, -1]


def test_dates() -> None:
    made = run(**small())
    ms = made.records.date()
    day = 86_400_000
    jan2 = 1_704_153_600_000  # 2024-01-02
    assert ms[0] == jan2 and ms[1] == jan2 + 10.5 * 3_600_000 and ms[2] == jan2 + day
    assert ms[3] == jan2  # Excel 45293 = 2024-01-02
    assert np.isnan(ms[4]) and np.isnan(ms[5])  # 32.01 — not a date; empty
    assert made.counts["bad_dates"] == 1 and made.counts["empty_dates"] == 1
    assert "Неразобранных дат: 1" in " ".join(made.notes)
    assert nz.date_ms("2024-01-02T03:04:05.5") == jan2 + 3 * 3_600_000 + 4 * 60_000 + 5_500
    assert math.isnan(nz.excel_ms(60)) and nz.excel_ms(1) == nz.date_ms("1900-01-01")
    assert nz.excel_ms(61) == nz.date_ms("1900-03-01")


def test_without_optional_roles() -> None:
    made = nz.normalize(table(ID=["A", "B"], T=["CRM", None], AU=np.array([1.0, np.nan])),
                        {"sample_id": "ID", "type": "T", "elements": [{"column": "AU", "unit": "g/t"}]})  # fmt: skip
    recs = made.records
    assert recs.schema.elements == (r.Element("AU", "г/т", False),)
    assert recs.text(r.TYPE_CODE).values() == ["CRM", None]
    assert recs.text(r.LAB).labels == () and recs.index(r.PREV_ROW).tolist() == [-1, -1]
    assert np.isnan(recs.date()).all() and made.counts["empty_dates"] == 2
    assert np.isnan(recs.number(r.v("AU"))[1])


def test_mapping_errors() -> None:
    cols = table(ID=["A"], T=["CRM"], AU=np.array([1.0]))
    for mapping, code, param in (
        ({"type": "T", "elements": [{"column": "AU"}]}, "no_sample_id", "sample_id"),
        ({"sample_id": "ID", "elements": [{"column": "AU"}]}, "no_type", "type"),
        ({"sample_id": "ID", "type": "T", "elements": []}, "no_elements", "elements"),
        ({"sample_id": "ID", "type": "Х", "elements": [{"column": "AU"}]}, "field_not_found", "type"),
        ({"sample_id": "ID", "type": "T", "elements": [{"column": "AU"}, {"column": "AU"}]}, "bad_param",
         "elements[1].name"),
    ):  # fmt: skip
        with pytest.raises(ApiError) as e:
            nz.normalize(cols, mapping)
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, code, param)
        assert e.value.message and e.value.hint


def test_limits_of_size() -> None:
    with pytest.raises(ApiError) as e:
        r.check_size(r.MAX_ROWS + 1, 1)
    assert e.value.code == "too_many_rows" and "исходные" in e.value.hint
    r.check_size(r.MAX_ROWS, r.MAX_ELEMENTS, r.MAX_EXTRA)
    big = r.MAX_ROWS + 1  # refused before anything is read
    cols = {"ID": Column("ID", "number", np.zeros(big)), "T": Column("T", "number", np.zeros(big))}
    with pytest.raises(ApiError) as e:
        nz.normalize(cols, {"sample_id": "ID", "type": "T", "elements": [{"column": "T", "name": "X"}]})
    assert e.value.code == "too_many_rows"
    els = [{"column": "AU", "name": f"E{i}"} for i in range(r.MAX_ELEMENTS + 1)]
    with pytest.raises(ApiError) as e:
        nz.normalize(table(ID=["A"], T=["CRM"], AU=np.array([1.0])), {"sample_id": "ID", "type": "T", "elements": els})
    assert e.value.code == "too_many_elements" and e.value.status == 422 and e.value.hint


def test_units_and_auto_mapping() -> None:
    assert nz.split_unit("AU_ppm") == ("AU", "ppm")
    assert nz.split_unit("Cu, %") == ("Cu", "%")
    assert nz.split_unit("Au (г/т)") == ("Au", "г/т")
    assert nz.split_unit("Ag ppb") == ("Ag", "ppb")
    assert nz.split_unit("ZN") == ("ZN", None)
    cols = [("Sample ID", "text"), ("Hole", "text"), ("QC_TYPE", "text"), ("Parent", "text"), ("Standard", "text"),
            ("Lab", "text"), ("Batch", "text"), ("Дата", "text"), ("Seq", "number"), ("AU_ppm", "number"),
            ("AU_DL", "number"), ("Cu, %", "number"), ("Note", "text")]  # fmt: skip
    m = nz.auto_mapping(cols)
    assert (m["sample_id"], m["hole"], m["type"], m["parent_id"], m["crm_id"]) == (
        "Sample ID", "Hole", "QC_TYPE", "Parent", "Standard")  # fmt: skip
    assert (m["lab"], m["batch"], m["date"], m["seq"]) == ("Lab", "Batch", "Дата", "Seq")
    assert m["elements"] == [
        {"column": "AU_ppm", "name": "AU", "unit": "ppm", "dl_column": "AU_DL"},
        {"column": "Cu, %", "name": "Cu", "unit": "%", "dl_column": None},
    ]


def test_dl_column_and_extra_fields() -> None:
    made = run({"elements": [{"column": "AU_ppm", "dl_column": "DL"}], "extra": ["ZONE", "DEPTH"]},
               **small(), DL=["0,01"] * 6, ZONE=["A", "B", None, "A", " ", "C"],
               DEPTH=np.arange(6.0))  # fmt: skip
    recs = made.records
    assert recs.schema.element("au").has_dl  # type: ignore[union-attr]
    assert recs.number(r.dl("AU")).tolist() == [0.01] * 6
    assert recs.text(r.x("ZONE")).values() == ["A", "B", None, "A", None, "C"]
    assert recs.number(r.x("DEPTH")).tolist() == list(range(6))
    assert r.x("ZONE") in recs.schema.columns() and r.x("Нет") not in recs.schema.columns()


def same(a: Any, b: Any) -> bool:
    if isinstance(a, r.Text):
        return isinstance(b, r.Text) and a.labels == b.labels and a.codes.tobytes() == b.codes.tobytes()
    if isinstance(a, np.ndarray):
        return a.dtype == b.dtype and a.tobytes() == b.tobytes()
    return bool(a.equals(b))


def test_parquet_cycle_is_bit_for_bit(tmp_path: Path) -> None:
    made = run({"elements": [{"column": "AU_ppm", "dl_column": "DL"}], "extra": ["ZONE"]}, **small(),
               DL=["0,01"] * 6, ZONE=["A", "B", None, "A", None, "C"])  # fmt: skip
    path = tmp_path / "записи.parquet"
    r.write(made.records, path)
    back = r.open_file(path)
    assert back.n == made.records.n and back.schema == made.records.schema
    for name in made.records.schema.columns():
        assert same(made.records.column(name), back.column(name)), name
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    assert pf.metadata.row_group(0).column(0).compression == "ZSTD"
    assert pf.schema_arrow.field(r.DATE).type == pa.timestamp("ms")


def test_take_keeps_rows_and_labels() -> None:
    recs = run(**small()).records
    mask = np.array([False, True, True, False, True, False])
    sub = recs.take(mask)
    assert sub.n == 3 and sub.row.tolist() == [1, 2, 4]
    assert same(sub.number(r.v("AU")), recs.number(r.v("AU"))[mask])
    t = sub.text(r.TYPE_CODE)
    assert t.labels == recs.text(r.TYPE_CODE).labels and t.values() == ["ORIG", "DUP_F", "DUP_P"]
    assert sub.ids().to_pylist() == ["S2", "s1", "S4"]
    assert sub.index(r.PARENT_ROW).tolist() == [-1, 1, -1]  # rows of the whole file
    assert sub.take(np.array([2, 0])).row.tolist() == [4, 1]
    with pytest.raises(KeyError):
        recs.number(r.v("CU"))


def test_columns_go_through_the_cache(tmp_path: Path) -> None:
    from bf_core.stats.source import ColumnCache

    path = tmp_path / "r.parquet"
    r.write(run(**small()).records, path)
    cache = ColumnCache(1 << 20)
    a = r.open_file(path, sha="a" * 64, scope="p1", cache=cache)
    a.load([r.v("AU"), r.LAB, r.DATE])
    assert cache.misses == 3 and len(cache.keys()) == 3  # one read of the file for three columns
    b = r.open_file(path, sha="a" * 64, scope="p1", cache=cache)
    assert same(b.number(r.v("AU")), a.number(r.v("AU"))) and cache.hits == 1
    r.open_file(path, sha="a" * 64, scope="p2", cache=cache).number(r.SEQ)
    assert [k[0] for k in cache.keys()] == ["p2"]  # another project drops the columns of the previous one


def test_generator_one_seed_one_file(tmp_path: Path) -> None:
    a, b, c = tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "c.csv"
    write_csv(a, generate(2000, seed=7))
    write_csv(b, generate(2000, seed=7))
    write_csv(c, generate(2000, seed=8))
    assert a.read_bytes() == b.read_bytes() and a.read_bytes() != c.read_bytes()
    gen = generate(2000, seed=7)
    assert gen.counts == {"rows": 3100, "control": 2000, "parents": 1100, "batches": 62}
    assert len(gen.crm) == 60 and {d["element"] for d in gen.dl} == {"AU", "CU", "AG"}


def test_generated_file_normalizes(tmp_path: Path) -> None:
    gen = generate(3000, seed=3)
    path = tmp_path / "контроль.csv"
    write_csv(path, gen)
    names = [f.name for f in gen.table.schema]
    kinds = [(n, "number" if n in {c for *_, c in gen.elements} | {"SEQ"} else "text") for n in names]
    m = nz.parse_mapping(nz.auto_mapping(kinds), names)
    tab = read_table(path, ReadOptions(text_columns=nz.text_columns(m)))
    made = nz.normalize({c.name: c for c in tab.columns}, m)
    c = made.counts
    assert c["rows"] == 4650 and c["no_parent"] == 0 and c["dup_ids"] == 0 and c["bad_dates"] == 0
    recs = made.records
    types = recs.text(r.TYPE_CODE)
    dup = np.isin(types.codes, [types.code(t) for t in ("DUP_F", "DUP_C", "DUP_P", "UMP")])
    assert (recs.index(r.PARENT_ROW)[dup] >= 0).all() and (recs.index(r.PARENT_ROW)[~dup] == -1).all()
    assert c["below_dl"] > 0 and (recs.flags(r.q("AU")) == r.Q_BELOW).sum() == c["elements"]["AU"]["below"]
    assert recs.text(r.LAB).labels == ("EXT", "LAB1", "LAB2", "LAB3")


def test_bad_seq_is_a_bad_number_not_a_limit() -> None:
    """14.25: the bad cells of SEQ are counted in `bad_numbers` only, never as «<DL» or «>x»."""
    made = run(**small(SEQ=["1", "x", "y", "2", "3", "4"], AU_ppm=["1"] * 6))
    c = made.counts
    assert (c["below_dl"], c["above_dl"], c["bad_numbers"]) == (0, 0, 2)


def test_read_rows_takes_only_their_row_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """14.25: the card of records reads the row groups of its rows, every column, past the cache."""
    import pyarrow.parquet as pq

    monkeypatch.setattr(r, "ROW_GROUP", 2)
    recs = run(**small()).records
    path = tmp_path / "r.parquet"
    r.write(recs, path)
    assert pq.ParquetFile(path).num_row_groups == 3
    read: list[list[int]] = []
    real = pq.ParquetFile.read_row_groups

    def spy(self: Any, groups: Any, *a: Any, **kw: Any) -> Any:
        read.append(list(groups))
        return real(self, groups, *a, **kw)

    monkeypatch.setattr(pq.ParquetFile, "read_row_groups", spy)
    sub = r.read_rows(path, np.array([1, 4, 5]))
    assert read == [[0, 2]] and sub.row.tolist() == [1, 4, 5]
    whole = r.open_file(path)
    for name in recs.schema.columns():
        part = whole.column(name)
        want = part.take(np.array([1, 4, 5])) if isinstance(part, r.Text) else (
            part.take([1, 4, 5]) if hasattr(part, "to_pylist") else part[[1, 4, 5]])  # fmt: skip
        assert same(want, sub.column(name)), name
    assert r.read_rows(path, np.array([], dtype=np.int64)).n == 0
