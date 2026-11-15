"""Block models: DAT (BOCU-1 and cp1251) and CSV readers, grid, checks, import job, report."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from app.blocks.report import TROY_OUNCE_G
from app.formats.blockmodel import (
    BlockModelError, check_model, describe_grid, header_fields, looks_like_block_model, read_block_model,
)
from app.formats.micromine import read_columns
from app.formats.registry import detect
from tests.dat_builder import encode_bocu, header
from tests.test_bocu1 import ICU_VECTORS
from tests.test_storage_jobs import _project, upload

ORIGIN = np.array([466167.5, 4584527.5, 202.5])  # corner of parent (0, 0, 0); first centre 466170, 4584530, 205
PARENT = 5.0
# BOCU-1 bytes ICU writes for a Cyrillic label (prev starts afresh in every field).
RUDA = dict(ICU_VECTORS)["Руда окисл. ёЁ"]


def make_blocks(seed: int = 3) -> dict[str, np.ndarray]:
    """A sub-blocked model on a 4 × 3 × 2 parent grid of 5 m: some parents whole, some split
    along X into 2 + 3 m, some into 1 m × 5 × 5 slices, some into 5 × 5 × (2 + 2 + 1)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(4):
        for j in range(3):
            for k in range(2):
                if (i + j + k) % 5 == 4:
                    continue  # an empty parent (air)
                lo = ORIGIN + PARENT * np.array([i, j, k])
                kind = (i * 7 + j * 3 + k) % 4
                if kind == 0:
                    parts = [(lo, (5, 5, 5))]
                elif kind == 1:
                    parts = [(lo, (2, 5, 5)), (lo + [2, 0, 0], (3, 5, 5))]
                elif kind == 2:
                    parts = [(lo + [x, 0, 0], (1, 5, 5)) for x in range(5)]
                else:
                    parts = [(lo, (5, 5, 2)), (lo + [0, 0, 2], (5, 5, 2)), (lo + [0, 0, 4], (5, 5, 1))]
                for corner, size in parts:
                    rows.append((*(corner + np.array(size) / 2), *size))
    a = np.array(rows)
    n = len(a)
    return {
        "X": a[:, 0], "Y": a[:, 1], "Z": a[:, 2], "_X": a[:, 3], "_Y": a[:, 4], "_Z": a[:, 5],
        "SG": np.round(rng.uniform(2.4, 3.2, n), 3),
        "DENSITY": np.full(n, 2.6),
        "AU_CUT": np.round(rng.lognormal(0, 1, n), 4),
        "AU": np.round(rng.lognormal(0.1, 1, n), 4),
        "CU_CUT": np.round(rng.uniform(0, 1, n), 4),
        "ALT": np.array(["G" if x % 3 else "Q" for x in range(n)]),
        "OXIDE": (np.arange(n) % 4 == 0).astype(int),
    }


def write_dat(path: Path, m: dict[str, np.ndarray], codepage: str = "BOCU-1", cyrillic: bool = False,
              tight: bool = False, rename: dict[str, str] | None = None, described: bool = False) -> None:
    """R fields for geometry and AU, N (text) for SG and OXIDE, C for ALT. `tight`: the real layout of a
    variable line, NAME padded to 10 and the type right after it ("RUN_AU_CUTN 12 8"); `rename`: field
    names in the header (the data are taken by the original name)."""
    bocu = codepage.upper() == "BOCU-1"
    enc = (lambda s: encode_bocu(s)) if bocu else (lambda s: s.encode("cp1251"))
    fields = [("X", "R", 8), ("Y", "R", 8), ("Z", "R", 8), ("_X", "R", 8), ("_Y", "R", 8), ("_Z", "R", 8),
              ("SG", "N", 10), ("DENSITY", "R", 8), ("AU_CUT", "R", 8), ("AU", "R", 8), ("CU_CUT", "R", 8),
              ("ALT", "C", 20), ("OXIDE", "N", 3)]
    out = bytearray(header(codepage))
    out += f"{len(fields)}   VARIABLES\r\n".encode()
    for name, kind, width in fields:
        shown = (rename or {}).get(name, name)
        if tight:
            out += enc(shown).ljust(10, b" ") + f"{kind} {width} 3".encode()
            # a field description after "|", as newer Micromine writes it
            out += (b"|" + enc(("Grade of " if bocu else "Описание ") + f"{shown} X 1 2 ") if described else b"") + b"\r\n"
        else:
            out += enc(shown).ljust(10, b" ") + f" {kind} {width:>3}  3".encode() + b"\r\n"
    for r in range(len(m["X"])):
        for name, kind, width in fields:
            v = m[name][r]
            if kind == "R":
                out += struct.pack("<d", float(v))
            elif kind == "N":
                text = f"{v:.3f}" if name == "SG" else f"{int(v)}"
                if name == "SG" and r == 1:
                    text = ""  # an empty value
                out += enc(text.rjust(width))[:width].ljust(width, b" ")
            else:
                label = "Руда окисл. ёЁ" if cyrillic and str(v) == "Q" else str(v)
                raw = RUDA if (bocu and label.startswith("Руда")) else enc(label)
                out += raw.ljust(width, b" ")
        out += b"\r\n"
    path.write_bytes(bytes(out))


def write_micromine_csv(path: Path, m: dict[str, np.ndarray]) -> None:
    names = list(m)
    types = ["CHARACTER" if m[n].dtype.kind in "US" else "DOUBLE" for n in names]
    lines = [",".join(names), ",".join(types), "466170,5,4584530,5,205,5,CENTRE"]
    for r in range(len(m["X"])):
        lines.append(",".join(str(m[n][r]) for n in names))
    path.write_text("\n".join(lines) + "\n", encoding="cp1251")


# ------------------------------------------------------------------------------------ readers
def test_dat_columns_bocu_numbers_text_and_cyrillic(tmp_path):
    m = make_blocks()
    p = tmp_path / "Model.DAT"
    write_dat(p, m, cyrillic=True)
    cols = {c.name: c for c in read_columns(p).columns}
    assert list(cols)[:6] == ["X", "Y", "Z", "_X", "_Y", "_Z"]
    assert np.array_equal(cols["X"].values, m["X"]) and np.array_equal(cols["AU_CUT"].values, m["AU_CUT"])
    sg = cols["SG"].values
    assert np.isnan(sg[1]) and np.allclose(np.delete(sg, 1), np.delete(m["SG"], 1))
    assert np.array_equal(cols["OXIDE"].values, m["OXIDE"])
    alt = cols["ALT"]
    assert alt.kind == "category" and set(alt.labels) == {"G", "Руда окисл. ёЁ"}
    assert [alt.labels[c] for c in alt.values[:3]] == ["Руда окисл. ёЁ", "G", "G"]


def test_cp1251_dat_and_header_padded_to_8192(tmp_path):
    m = make_blocks()
    p = tmp_path / "Model.DAT"
    write_dat(p, m, codepage="ibm-5347_P100-1998")
    data = p.read_bytes()
    # The same file with the XML header padded to 8192 bytes, as some exports have it.
    padded = data[:4096] + b"\x00" * 4096 + data[4096:]
    p2 = tmp_path / "Padded.DAT"
    p2.write_bytes(padded)
    for path in (p, p2):
        t = read_block_model(path, path.name)
        assert len(t.centres) == len(m["X"]) and t.format.startswith("Micromine DAT")
        assert {a.name for a in t.attributes} >= {"SG", "AU_CUT", "ALT"}


def test_micromine_csv_grid_line_and_plain_csv(tmp_path):
    m = make_blocks()
    p = tmp_path / "Model.csv"
    write_micromine_csv(p, m)
    t = read_block_model(p, p.name)
    assert t.format == "Micromine CSV" and t.grid_hint == {"origin": [466170.0, 4584530.0, 205.0], "size": [5.0, 5.0, 5.0], "at": "centre"}
    assert np.allclose(t.centres[:, 0], m["X"]) and next(a for a in t.attributes if a.name == "ALT").kind == "category"
    # A regular model without sizes: the size is the spacing of the centres.
    q = tmp_path / "regular.csv"
    q.write_text("EAST;NORTH;RL;AU\n0;0;0;1\n10;0;0;2\n0;10;0;3\n0;0;4;4\n", encoding="utf-8")
    r = read_block_model(q, q.name)
    assert r.sizes[0].tolist() == [10.0, 10.0, 4.0] and "регулярная" in r.notes[0]
    with pytest.raises(BlockModelError, match="центра"):
        bad = tmp_path / "bad.csv"
        bad.write_text("A,B\n1,2\n", encoding="utf-8")
        read_block_model(bad, bad.name)


def test_detection_tells_block_models_from_drillhole_tables(tmp_path):
    m = make_blocks()
    p = tmp_path / "Model.DAT"
    write_dat(p, m)
    assert detect("Model.DAT", p).kind == "blocks"
    assert header_fields(p, "Model.DAT")[:3] == ["X", "Y", "Z"]
    assert not looks_like_block_model(["BHID", "FROM", "TO", "X", "Y", "Z", "AU"])
    assay = tmp_path / "assay.csv"
    assay.write_text("BHID,FROM,TO,AU\nH1,0,1,0.5\n", encoding="utf-8")
    assert detect("assay.csv", assay).kind == "drillholes"


# ------------------------------------------------------------------------------- grid, checks
def test_grid_of_a_subblocked_model_and_clean_checks(tmp_path):
    m = make_blocks()
    p = tmp_path / "Model.DAT"
    write_dat(p, m)
    t = read_block_model(p, p.name)
    grid, cells, inside = describe_grid(t)
    assert grid.parent == [5.0, 5.0, 5.0] and np.allclose(grid.origin, ORIGIN) and grid.counts == [4, 3, 2]
    assert grid.subblocked and inside.all() and grid.parents_used == 19
    checks = check_model(t, grid, cells, inside)
    assert not [c for c in checks if c["level"] == "error"], checks
    assert any(c["code"] == "constant" and "DENSITY" in c["message"] for c in checks)
    volume = next(c for c in checks if c["code"] == "volume")
    assert "2 375" in volume["message"]  # 19 parents × 125 m³


def test_checks_find_overlaps_crossings_and_duplicates(tmp_path):
    m = make_blocks()
    extra = {k: np.append(v, v[0]) for k, v in m.items()}  # a duplicate of block 0
    # A block crossing from parent (0,0,0) into (1,0,0) and overlapping the blocks there.
    extra = {k: np.append(v, v[0]) for k, v in extra.items()}
    extra["X"][-1] = ORIGIN[0] + 4.5
    extra["_X"][-1] = 2.0
    extra["_Y"][-1] = 5.0
    extra["_Z"][-1] = 1.0
    extra["Y"][-1] = ORIGIN[1] + 2.5
    extra["Z"][-1] = ORIGIN[2] + 0.5
    p = tmp_path / "Bad.DAT"
    write_dat(p, extra)
    t = read_block_model(p, p.name)
    grid, cells, inside = describe_grid(t)
    codes = {c["code"]: c["count"] for c in check_model(t, grid, cells, inside)}
    assert codes["duplicates"] == 1 and codes["crosses_parent"] == 1
    assert codes["overlaps"] == 2  # block 0 and its duplicate (the crossing block is not on the grid)


# ------------------------------------------------------------------------- import job, report
def _import(db_client, run_jobs, tmp_path, m) -> tuple[int, dict]:
    pid = _project(db_client)
    p = tmp_path / "Model_OK.DAT"
    write_dat(p, m, cyrillic=True)
    uid = upload(db_client, pid, "Model_OK.DAT", p.read_bytes(), chunk=1 << 16)
    det = db_client.post(f"/api/projects/{pid}/detect", json={"uploads": [uid]}).json()[0]
    assert det["kind"] == "blocks" and "Блочная модель" in det["preview"]["summary"]
    job = db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [uid]})
    assert job.status_code == 202, job.text
    run_jobs()
    done = db_client.get(f"/api/jobs/{job.json()['id']}").json()
    assert done["status"] == "done", done
    (d,) = db_client.get(f"/api/projects/{pid}/datasets").json()
    return pid, d


def test_import_job_and_grade_tonnage_report(db_client, run_jobs, tmp_path):
    m = make_blocks()
    _, d = _import(db_client, run_jobs, tmp_path, m)
    assert d["kind"] == "blocks"
    meta = d["meta"]
    n = len(m["X"])
    assert meta["block_count"] == n and meta["volume"] == pytest.approx(2375.0)
    assert meta["grid"]["counts"] == [4, 3, 2] and meta["grid"]["subblocked"]
    attrs = {a["name"]: a for a in meta["attributes"]}
    assert attrs["AU_CUT"]["unit"] == "g/t" and attrs["CU_CUT"]["unit"] == "%" and attrs["ALT"]["kind"] == "category"
    # Defaults: SG (it varies), not the constant DENSITY; AU_CUT rather than AU.
    assert meta["report"]["density"] == attrs["SG"]["key"]
    assert attrs["AU"]["key"] not in meta["report"]["grades"] and attrs["AU_CUT"]["key"] in meta["report"]["grades"]

    au, cu, sg = attrs["AU_CUT"]["key"], attrs["CU_CUT"]["key"], attrs["SG"]["key"]
    url = f"/api/blockmodels/{d['id']}/report"
    r = db_client.post(url, json={"density": sg, "grades": [au, cu], "cutoff_field": au, "cutoffs": [0, 0.5, 1]})
    assert r.status_code == 200, r.text
    body = r.json()
    vol = m["_X"] * m["_Y"] * m["_Z"]
    sgv = m["SG"].copy()
    sgv[1] = np.nan  # the empty value
    t = vol * sgv
    assert body["excluded_no_density"] == 1
    for row in body["rows"]:
        sel = (m["AU_CUT"] >= row["cutoff"]) & np.isfinite(t)
        assert row["tonnes"] == pytest.approx(t[sel].sum())
        g = row["grades"][au]
        assert g["grade"] == pytest.approx((m["AU_CUT"][sel] * t[sel]).sum() / t[sel].sum())
        assert g["metal"]["value"] == pytest.approx((m["AU_CUT"][sel] * t[sel]).sum() / TROY_OUNCE_G / 1000)
        assert row["grades"][cu]["metal"]["unit"] == "т"
    # Constant density, filter by a category, grouping.
    alt = attrs["ALT"]["key"]
    g = db_client.post(url, json={"density_value": 2.7, "grades": [au], "filters": [{"key": alt, "labels": ["G"]}],
                                  "group_by": attrs["OXIDE"]["key"]}).json()
    total = g["rows"][0]
    sel = m["ALT"] == "G"
    assert total["group"] is None and total["tonnes"] == pytest.approx(vol[sel].sum() * 2.7)
    assert sum(r["tonnes"] for r in g["rows"][1:]) == pytest.approx(total["tonnes"])
    assert db_client.post(url, json={"grades": [au]}).status_code == 422  # no density

    # A block by its number, attribute values for the viewer.
    b = db_client.get(f"/api/blockmodels/{d['id']}/blocks/0").json()
    assert b["centre"][0] == m["X"][0] and b["values"]["ALT"] == "Руда окисл. ёЁ" and b["values"]["AU_CUT"] == m["AU_CUT"][0]
    raw = db_client.get(f"/api/blockmodels/{d['id']}/values/{au}").content
    assert np.allclose(np.frombuffer(raw, "<f4"), m["AU_CUT"].astype("<f4"))
    assert db_client.patch(f"/api/blockmodels/{d['id']}/attributes/{au}", json={"unit": "ppm"}).json()["unit"] == "ppm"


def test_edit_values_of_a_block(db_client, run_jobs, tmp_path):
    m = make_blocks()
    pid, d = _import(db_client, run_jobs, tmp_path, m)
    url = f"/api/blockmodels/{d['id']}/blocks/2"
    r = db_client.patch(url, json={"values": {"AU_CUT": "99,5", "ALT": "Новая руда", "SG": None}})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["values"]["AU_CUT"] == 99.5 and b["values"]["ALT"] == "Новая руда" and b["values"]["SG"] is None
    assert db_client.get(url).json()["values"] == b["values"]
    (d2,) = db_client.get(f"/api/projects/{pid}/datasets").json()
    attrs = {a["name"]: a for a in d2["meta"]["attributes"]}
    assert attrs["AU_CUT"]["stats"]["max"] == 99.5
    assert "Новая руда" in attrs["ALT"]["labels"] and attrs["SG"]["stats"]["empty"] >= 1
    raw = db_client.get(f"/api/blockmodels/{d['id']}/values/{attrs['AU_CUT']['key']}").content
    assert np.frombuffer(raw, "<f4")[2] == 99.5
    assert db_client.patch(url, json={"values": {"AU_CUT": "abc"}}).status_code == 422
    assert db_client.patch(url, json={"values": {"NOPE": 1}}).status_code == 422
    assert db_client.patch(f"/api/blockmodels/{d['id']}/blocks/100000", json={"values": {"AU": 1}}).status_code == 404


def test_xml_header_longer_than_4096_and_no_codepage(tmp_path):
    """The model export pads a long XML header to 8192 bytes: the codepage is past byte 4096.
    Without any codepage, BOCU-1 names (ASCII + 0x50) are recognised by their bytes."""
    m = make_blocks()
    p = tmp_path / "Model.DAT"
    write_dat(p, m)
    data = p.read_bytes()
    head, rest = data[:4096], data[4096:]
    magic_end = head.index(b"\r\n") + 2
    xml = head[magic_end:].rstrip(b"\x00")
    long_xml = xml.replace(b"<metadata version=\"1.0\">", b"<metadata version=\"1.0\"><blockmodel>" + b" " * 5000 + b"</blockmodel>")
    big = (head[:magic_end] + long_xml).ljust(8192, b"\x00") + rest
    q = tmp_path / "Long.DAT"
    q.write_bytes(big)
    assert detect("Long.DAT", q).kind == "blocks"
    t = read_block_model(q, q.name)
    assert len(t.centres) == len(m["X"]) and "BOCU-1" in t.format
    # No codepage at all.
    bare = (head[:magic_end] + xml.replace(b'codepage="BOCU-1"', b"")).ljust(4096, b"\x00") + rest
    r = tmp_path / "Bare.DAT"
    r.write_bytes(bare)
    assert detect("Bare.DAT", r).kind == "blocks"
    assert next(a for a in read_block_model(r, r.name).attributes if a.name == "ALT").labels[0] in ("G", "Руда окисл. ёЁ")


def test_detection_explains_a_table_that_is_not_a_model(tmp_path):
    p = tmp_path / "assay.csv"
    p.write_text("BHID,FROM,TO,AU\nH1,0,1,0.5\n", encoding="utf-8")
    d = detect("assay.csv", p)
    assert d.kind == "drillholes" and "_X" in d.note
    broken = tmp_path / "broken.DAT"
    broken.write_bytes(b"THIS IS MICROMINE EXTENDED DATA FILE!" + b"\x00" * 5000)
    d = detect("broken.DAT", broken)
    assert d.kind == "drillholes" and "не прочитан" in d.note


def test_variable_line_with_a_ten_character_name_and_cp1251_cyrillic(tmp_path):
    """The real export has fields named RUN_AU_CUT (10 characters): the type follows the name with
    no space. A cp1251 file names its codepage "ibm-5347_P100-1998" and its Cyrillic must decode."""
    m = make_blocks()
    every = {"AU_CUT": "RUN_AU_CUT", "CU_CUT": "RUN_CU_CUT", "DENSITY": "ПЛОТНОСТЬ"}
    for codepage in ("BOCU-1", "ibm-5347_P100-1998"):
        # (Cyrillic names in BOCU-1 are multi-byte: not part of this layout test)
        names = {k: v for k, v in every.items() if codepage != "BOCU-1" or v.isascii()}
        p = tmp_path / f"Tight_{codepage[:4]}.DAT"
        write_dat(p, m, codepage=codepage, tight=True, rename=names)
        assert detect(p.name, p).kind == "blocks"
        t = read_block_model(p, p.name)
        got = {a.name for a in t.attributes}
        assert {"RUN_AU_CUT", "RUN_CU_CUT"} <= got
        if codepage != "BOCU-1":
            assert "ПЛОТНОСТЬ" in got
        assert np.allclose(next(a for a in t.attributes if a.name == "RUN_AU_CUT").values, m["AU_CUT"])
        assert len(t.centres) == len(m["X"])


def test_variable_line_with_a_field_description(tmp_path):
    """Newer Micromine writes "NAME C 1 0|description" in the VARIABLES block (a real BM_LU.DAT)."""
    m = make_blocks()
    for codepage in ("BOCU-1", "ibm-5347_P100-1998"):
        p = tmp_path / f"Described_{codepage[:4]}.DAT"
        write_dat(p, m, codepage=codepage, tight=True, described=True)
        assert detect(p.name, p).kind == "blocks"
        t = read_block_model(p, p.name)
        assert {"AU_CUT", "ALT"} <= {a.name for a in t.attributes}
        assert np.allclose(next(a for a in t.attributes if a.name == "AU_CUT").values, m["AU_CUT"])
        assert len(t.centres) == len(m["X"])


def test_size_fields_named_after_the_centre():
    """A real export: EAST, NORTH, RL and the sizes _EAST, _NORTH, _RL."""
    from app.formats.blockmodel import find_roles

    roles = find_roles(["EAST", "NORTH", "RL", "_EAST", "_NORTH", "_RL", "CU"])
    assert (roles["x"], roles["dx"], roles["dy"], roles["dz"]) == ("EAST", "_EAST", "_NORTH", "_RL")


def test_outer_shell_of_a_model():
    from app.blocks.shell import exposed_mask

    # 3 × 3 × 3 whole parents of 10 m: only the middle one is closed on every side.
    idx = np.array([(i, j, k) for i in range(3) for j in range(3) for k in range(3)], float)
    centres = idx * 10 + 5
    sizes = np.full_like(centres, 10.0)
    m = exposed_mask(centres, sizes, [0, 0, 0], [10, 10, 10], [3, 3, 3])
    assert m.sum() == 26 and m[13] == 0
    # The middle parent split into 2 × 1 × 1 halves: both hidden; one more parent removed: its neighbours show.
    mid = np.array([[2.5 + 10, 15, 15], [7.5 + 10, 15, 15]])
    c2 = np.vstack([np.delete(centres, [13, 14], axis=0), mid])  # 14 = (1, 1, 2): the middle one's top neighbour
    s2 = np.vstack([np.delete(sizes, [13, 14], axis=0), [[5, 10, 10], [5, 10, 10]]])
    m2 = exposed_mask(c2, s2, [0, 0, 0], [10, 10, 10], [3, 3, 3])
    assert m2[-2:].tolist() == [1, 1]  # their top face is open now
    # A parent not filled by its sub-blocks: its blocks are shown, its neighbours too.
    m3 = exposed_mask(c2[:-1], s2[:-1], [0, 0, 0], [10, 10, 10], [3, 3, 3])
    assert m3[-1] == 1


def test_shell_of_an_imported_model(db_client, run_jobs, tmp_path):
    m = make_blocks()
    _, d = _import(db_client, run_jobs, tmp_path, m)
    raw = db_client.get(f"/api/blockmodels/{d['id']}/exposed")
    assert raw.status_code == 200
    mask = np.frombuffer(raw.content, np.uint8)
    assert len(mask) == len(m["X"]) and 0 < mask.sum() <= len(mask)


def test_grid_of_a_trimmed_model_starts_where_whole_blocks_stand():
    """A model trimmed to the ore: its lowest block is a sub-block in the middle of its parent.
    The grid must still be at the whole blocks, and no sub-block crosses its parent."""
    from app.formats.blockmodel import BlockTable, aligned_origin, describe_grid

    whole = np.array([[i * 10 + 5, j * 10 + 5, k * 5 + 2.5] for i in range(3) for j in range(3) for k in range(2)], float)
    subs = np.array([[33.5, 5, 2.5], [37.5, 5, 2.5]])  # parent (3, 0, 0) cut into 3 m (from x=32) and 5 m; 30–32 is air
    low = np.array([[-6.0, 5, 2.5], [-2.5, 5, 2.5]])  # parent (−1, 0, 0): 4 m from x=−8 … only its right part
    centres = np.vstack([whole, subs, low]) + [1000, 2000, 300]
    sizes = np.vstack([np.tile([10, 10, 5.0], (len(whole), 1)), [[3, 10, 5], [5, 10, 5]], [[4, 10, 5], [5, 10, 5]]])
    o = aligned_origin(centres - sizes / 2, sizes, np.array([10, 10, 5.0]))
    assert np.allclose(o, [990, 2000, 300])
    t = BlockTable("CSV", centres, sizes, [], {}, None)
    info, _, inside = describe_grid(t)
    assert inside.all() and info.origin == [990, 2000, 300]


def test_old_model_is_realigned_on_first_show(db_client, db_engine, run_jobs, tmp_path):
    from sqlalchemy.orm import Session

    from app.models import Dataset

    m = make_blocks()
    pid, d = _import(db_client, run_jobs, tmp_path, m)
    with Session(db_engine) as s:  # as stored before: no shell, the grid one block off
        ds = s.get(Dataset, d["id"])
        g = {k: v for k, v in ds.meta["grid"].items() if k != "aligned"}
        g["origin"] = [v - 1.5 for v in g["origin"]]
        ds.meta = {**ds.meta, "grid": g}
        ds.blobs = {k: v for k, v in ds.blobs.items() if k != "exposed"}
        s.commit()
    assert db_client.get(f"/api/blockmodels/{d['id']}/exposed").status_code == 200
    (d2,) = db_client.get(f"/api/projects/{pid}/datasets").json()
    assert d2["meta"]["grid"]["aligned"] and np.allclose(d2["meta"]["grid"]["origin"], d["meta"]["grid"]["origin"])
