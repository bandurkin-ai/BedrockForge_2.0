"""«Импорт скважин»: `check` with the section the file is added to (`expected`, Д-41 А2)."""

from __future__ import annotations

from pathlib import Path

import pytest

API = "/api/v1"
ASSAY_XYZ = "Hole-ID;Sample;From;To;Length;Cu_%;Ag_G/T;X;Y;Z\n" + "".join(
    f"H{i // 3};S{i};{i % 3};{i % 3 + 1};1;0,{i};{i};10;20;30\n" for i in range(9)
)


def _check(c, path: Path, **extra):  # type: ignore[no-untyped-def]
    r = c.post(f"{API}/drillholes/import/check", json={"path": str(path), **extra})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def assay(tmp_path: Path) -> Path:
    p = tmp_path / "ERR Core assay 03_10_10.csv"
    p.write_text(ASSAY_XYZ, encoding="utf-8")
    return p


def test_assay_with_xyz_is_assay_without_duplicate_holes(pclient, assay) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Тип"})
    r = _check(pclient, assay)
    assert r["kind"] == "assay" and "mismatch" not in r
    assert r["mapping"]["from"] == "From" and r["mapping"]["to"] == "To"
    assert not [i for i in r["issues"] if i["code"] == "duplicate_hole_id"]


def test_expected_match_and_omitted_are_unchanged(pclient, assay) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Тип"})
    base = _check(pclient, assay)
    for expected in ("assay", ["assay", "interval", "event"]):
        r = _check(pclient, assay, expected=expected)
        assert r["kind"] == "assay" and "mismatch" not in r and r["mapping"] == base["mapping"]


def test_mismatch_assigns_nothing_and_offers_the_ways_out(pclient, assay) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Тип"})
    r = _check(pclient, assay, expected="collar")
    assert r["kind"] is None and r["mapping"] == {} and r["issues"] == [] and r["preview"] == []
    m = r["mismatch"]
    assert m["code"] == "kind_mismatch" and m["detected"] == "assay" and m["expected"] == ["collar"]
    assert "«Hole-ID»" in m["reason"] and "«From»" in m["reason"] and "координаты середины" in m["reason"]
    assert m["hint"] and "Устья" in m["message"]
    actions = [(o["action"], o["kind"]) for o in m["options"]]
    assert actions == [("import_as", "collar"), ("switch", "assay"), ("cancel", "")]
    assert m["options"][0]["label"].startswith("Импортировать как «Устья")
    assert m["options"][1]["label"].startswith("Сменить тип на «Пробы")


def test_mismatch_with_several_expected_kinds(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Тип"})
    p = tmp_path / "collar.csv"
    p.write_text("Hole-ID;X;Y;Z;Length\nA;1;2;3;50\nB;4;5;6;60\n", encoding="utf-8")
    assert _check(pclient, p)["kind"] == "collar"
    r = _check(pclient, p, expected=["assay", "interval", "event"])
    assert r["kind"] is None and r["mismatch"]["detected"] == "collar"
    assert [o["kind"] for o in r["mismatch"]["options"] if o["action"] == "import_as"] == ["assay", "interval", "event"]
    assert "нет колонок" in r["mismatch"]["reason"]


def test_explicit_kind_applies_the_schema_of_the_section(pclient, assay) -> None:  # type: ignore[no-untyped-def]
    """«Импортировать как Устья»: the kind is given, so the choice of the person is checked, not overridden."""
    pclient.post(f"{API}/projects", json={"name": "Тип"})
    r = _check(pclient, assay, kind="collar", expected="collar")
    assert r["kind"] == "collar" and "mismatch" not in r
    assert any(i["code"] == "duplicate_hole_id" for i in r["issues"])  # what the person chose, with its consequences
    assert _check(pclient, assay, kind="assay", expected="collar")["kind"] == "assay"


def test_unknown_kind_is_not_a_mismatch(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Тип"})
    p = tmp_path / "x.csv"
    p.write_text("a;b\n1;2\n", encoding="utf-8")
    r = _check(pclient, p, expected="collar")
    assert r["kind"] is None and "mismatch" not in r and r["issues"][0]["code"] == "unknown_kind"
    bad = pclient.post(f"{API}/drillholes/import/check", json={"path": str(p), "expected": "nope"})
    assert bad.status_code == 422
