"""Two resource reports against each other: `resources.compare {report_a, report_b}` (подшаг 20.10; ТЗ 07-35 Д-37; ADR
0027 решение 26; `docs/progress/e20-api.md` §9.1). Synchronous, over the stored `data` of both reports.

    out = compare_reports(conn, store, report_a, report_b)     # 404 `resource_report_not_found`, 409 `report_building`

The rows are matched by (the labels of the keys, `total`, `kind`, the cut-off and its upper edge). A row that only one
of the reports has — `only_in: "a" | "b"`, `delta: null`: a gap, never 0. For a pair: Δ blocks, volume, tonnes and, per
element, Δ of the grade (ḡ_A − ḡ_B) and of the metal (ΔM); an empty grade of either report — an empty Δ. A = B (even the
same report) gives 0 in every row (ТД37-1). The two curves come as they are, for one chart.

The head: for each report {id, name, version, model {id, name, cells_sha}, inputs (name, fingerprint), stale,
created_at}. `grids_differ` — the models differ (by number, or the fingerprints of their cells): the rows may still
match, but the blocks are not the same, so only the reports are compared, not the blocks. Warnings (`{code, message}`):
`grids_differ`, `keys_differ` (other keys of groups), `cutoff_field_differs` (other g_б), `grade_units_differ` (a
grade in other units), `density_differs` (another density), `stale` (a report «устарел»).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import store as st
from . import store_data as sd
from .spec_parts import metal_family, metal_k

if TYPE_CHECKING:
    import sqlalchemy as sa

    from bf_core.storage.blobs import BlobStore

ALGO = "resources-compare-1"


def _head(info: dict[str, Any]) -> dict[str, Any]:
    graph = info.get("graph") or {}
    return {"id": info["id"], "name": info["name"], "version": info["version"], "model": dict(info.get("model") or {}),
            "inputs": [dict(i) for i in info.get("inputs") or []], "stale": bool(graph.get("stale")),
            "stale_reason": graph.get("stale_reason"), "created_at": info.get("created_at")}  # fmt: skip


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (bool(row["total"]), tuple(row["g"] or ()), row["kind"], row["cutoff"], row["cutoff_to"])


def _sub(a: Any, b: Any) -> float | None:
    return None if a is None or b is None else float(a) - float(b)


def _side(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"blocks": row["blocks"], "volume": row["volume"], "tonnes": row["tonnes"],
            "elements": {e: dict(v) for e, v in (row["elements"] or {}).items()}}  # fmt: skip


def _units(info: dict[str, Any]) -> dict[str, Any]:
    """{element (without case): its unit} of the parameters of a report."""
    return {str(e.get("field")).casefold(): e.get("unit") for e in (info.get("params") or {}).get("elements") or []}


def _one_unit(ua: Any, ub: Any) -> tuple[bool, bool]:
    """(the grades, the metals) of an element of the two reports are numbers of one unit. g/t and ppm are one unit of
    a grade; the metal is kept in the base unit of the family (grams — g/t, ppm, ppb; tonnes — %). A grade in g/t
    minus a grade in % is not a difference: such a Δ is empty, never a number."""
    if ua == ub:
        return True, True
    family = metal_family(ua)
    same = family is not None and family == metal_family(ub)
    return same and metal_k(ua) == metal_k(ub), same


def _delta(a: dict[str, Any], b: dict[str, Any], elements: list[str],
           units: tuple[dict[str, Any], dict[str, Any]] = ({}, {})) -> dict[str, Any]:  # fmt: skip
    out: dict[str, Any] = {"blocks": None if a["blocks"] is None or b["blocks"] is None else a["blocks"] - b["blocks"],
                           "volume": _sub(a["volume"], b["volume"]), "tonnes": _sub(a["tonnes"], b["tonnes"]),
                           "elements": {}}  # fmt: skip
    for e in elements:
        ea, eb = (a["elements"] or {}).get(e), (b["elements"] or {}).get(e)
        if ea is None or eb is None:
            out["elements"][e] = None
            continue
        grade, metal = _one_unit(units[0].get(e.casefold()), units[1].get(e.casefold()))
        out["elements"][e] = {"grade": _sub(ea["grade"], eb["grade"]) if grade else None,
                              "metal": _sub(ea["metal"], eb["metal"]) if metal else None}  # fmt: skip
    return out


def _warn(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _warnings(ia: dict[str, Any], ib: dict[str, Any], differ: bool) -> list[dict[str, str]]:
    pa, pb = ia.get("params") or {}, ib.get("params") or {}
    out: list[dict[str, str]] = []
    if differ:
        out.append(_warn("grids_differ", "Сетки различаются: сравниваются только отчёты, а не блоки."))
    if ia["graph"].get("stale") or ib["graph"].get("stale"):
        out.append(_warn("stale", "Один из отчётов устарел: он не отражает текущих данных."))
    if [g.get("field") or g.get("kind") for g in pa.get("groups") or []] != [
        g.get("field") or g.get("kind") for g in pb.get("groups") or []
    ]:
        out.append(_warn("keys_differ", "Ключи группировки отчётов разные: сопоставляются только итоговые строки."))
    if pa.get("cutoff_field") != pb.get("cutoff_field"):
        out.append(_warn("cutoff_field_differs", "Поля борта в отчётах разные: борта сравниваются как числа."))
    units_a = {e.get("field"): e.get("unit") for e in pa.get("elements") or []}
    units_b = {e.get("field"): e.get("unit") for e in pb.get("elements") or []}
    if any(units_a[k] != units_b[k] for k in units_a.keys() & units_b.keys()):
        out.append(_warn("grade_units_differ", "Единицы содержания в отчётах разные: разность содержаний (и металла, "
                         "если он в разных единицах) для таких элементов не показана."))  # fmt: skip
    if pa.get("density") != pb.get("density"):
        out.append(_warn("density_differs", "Плотность в отчётах разная: разность тонн включает и её."))
    return out


def compare_reports(conn: sa.Connection, store: BlobStore, report_a: int, report_b: int) -> dict[str, Any]:
    """The comparison of two stored reports (module docstring)."""
    infos, tables, curves = [], [], []
    for rid in (report_a, report_b):
        info = st.info(conn, rid, store)
        if info["state"] == "building":
            raise st.building()
        infos.append(info)
        table = st.read_table(conn, store, rid)
        tables.append(table)
        curves.append(st.read_curve(conn, store, rid))
    (ia, ib), (ta, tb) = infos, tables
    ea, eb = sd.elements_of(ta), sd.elements_of(tb)
    elements = ea + [e for e in eb if e not in ea]
    rows_a, rows_b = sd.table_rows(ta, ea), sd.table_rows(tb, eb)
    by_b = {_key(r): r for r in rows_b}
    units = (_units(ia), _units(ib))
    seen: set[tuple[Any, ...]] = set()
    rows: list[dict[str, Any]] = []

    def entry(row: dict[str, Any], a: dict[str, Any] | None, b: dict[str, Any] | None, only: str | None,
              delta: dict[str, Any] | None) -> dict[str, Any]:  # fmt: skip
        return {"g": row["g"], "total": row["total"], "kind": row["kind"], "cutoff": row["cutoff"],
                "cutoff_to": row["cutoff_to"], "only_in": only, "a": a, "b": b, "delta": delta}  # fmt: skip

    for ra in rows_a:
        key = _key(ra)
        rb = by_b.get(key)
        if rb is None:
            rows.append(entry(ra, _side(ra), None, "a", None))
            continue
        seen.add(key)
        a, b = _side(ra), _side(rb)
        assert a is not None and b is not None
        rows.append(entry(ra, a, b, None, _delta(a, b, elements, units)))
    rows += [entry(rb, None, _side(rb), "b", None) for rb in rows_b if _key(rb) not in seen]
    ma, mb = ia.get("model") or {}, ib.get("model") or {}
    differ = ma.get("id") != mb.get("id") or (
        ma.get("cells_sha") is not None and mb.get("cells_sha") is not None and ma["cells_sha"] != mb["cells_sha"]
    )
    return {"a": _head(ia), "b": _head(ib), "grids_differ": differ, "warnings": _warnings(ia, ib, differ),
            "keys": list((ia.get("params") or {}).get("groups") or []), "elements": elements, "rows": rows,
            "curves": {"a": curves[0], "b": curves[1]}}  # fmt: skip
