"""The routes of the checks of an estimate (подшаг 18.13; e18-api.md §8): `POST /estimate/xval` + `GET
/estimations/{id}/xval`, `/estimate/kna`, `/estimate/global`, `/estimate/qq`, `/estimate/swath` — the refusals before
the job (404, 409, 422, 423, `not_ready`), the jobs through the real routes, the page of the table, the Q-Q in a HEAVY
slot (overtaken → 409). The routers are included by `tests/estapi_util.py` until the orchestrator's lines."""

from __future__ import annotations

from typing import Any

from tests import estapi_util as U
from tests.estapi_util import GS, finish, make_row, ok, read_only, refused, row_body
from tests.estapi_world import LABELS

reg, ea = U.reg, U.ea  # the fixtures
E = f"{GS}/estimations"
X = f"{GS}/estimate"


def run_row(c: Any, label: str = "окисл") -> int:
    """A row made and run (its fields are in the model)."""
    ident = make_row(c, label)["id"]
    version = ok(c.get(f"{U.API}/blocks/{c.w.model}"))["version"]
    job = finish(c, ok(c.post(f"{X}/run", json={"estimation_ids": [ident], "expected_version": version,
                                                  "replace": True})))  # fmt: skip
    assert job["status"] == "done", job
    return int(ident)


def test_xval_refusals_the_page_and_the_job(ea) -> None:  # type: ignore[no-untyped-def]
    row = make_row(ea)["id"]
    refused(ea.get(f"{E}/{row}/xval"), 404, "xval_not_found")
    refused(ea.get(f"{E}/{row + 99}/xval"), 404, "estimation_not_found")
    refused(ea.post(f"{X}/xval", json=[1]), 422, "bad_request")
    refused(ea.post(f"{X}/xval", json={}), 422, "bad_param", "estimation_id")
    refused(ea.post(f"{X}/xval", json={"estimation_id": row, "by": "блок"}), 422, "bad_param", "by")
    refused(ea.post(f"{X}/xval", json={"estimation_id": row + 99}), 404, "estimation_not_found")
    back = read_only(ea)
    try:
        refused(ea.post(f"{X}/xval", json={"estimation_id": row}), 423, "project_read_only")
    finally:
        back()
    job = finish(ea, ok(ea.post(f"{X}/xval", json={"estimation_id": row, "by": "sample"})))
    assert job["status"] == "done", job
    assert job["result"]["by"] == "sample" and job["result"]["events"] == [
        ["geostat.changed", {"kind": "estimation", "id": row}]]  # fmt: skip
    page = ok(ea.get(f"{E}/{row}/xval", params={"page": 1, "size": 5}))
    assert (page["estimation_id"], page["by"], page["size"], page["stale"]) == (row, "sample", 5, False)
    assert len(page["rows"]) == 5 and page["total"] == job["result"]["n"]
    assert page["summary"] == job["result"]["summary"]
    assert page["histogram"]["edges"] and {"i", "row_id", "hole", "true", "est", "error", "kv", "z_error"} <= set(
        page["rows"][0])  # fmt: skip
    refused(ea.get(f"{E}/{row}/xval", params={"size": 0}), 422, "bad_param", "size")
    refused(ea.get(f"{E}/{row}/xval", params={"page": 0}), 422, "bad_param", "page")
    ok(ea.put(f"{E}/{row}", json={"expected_version": 1, "disc": [1, 1, 1]}))  # the parameters changed: stale
    assert ok(ea.get(f"{E}/{row}/xval"))["stale"] is True


def test_kna_global_qq_swath(ea) -> None:  # type: ignore[no-untyped-def]
    w = ea.w
    row = make_row(ea)["id"]
    # before the run: the model has no fields of the row
    refused(ea.post(f"{X}/global", json={"estimation_id": row}), 409, "not_estimated", "estimation_id")
    refused(ea.post(f"{X}/qq", json={"estimation_id": row}), 409, "not_estimated", "estimation_id")
    refused(ea.post(f"{X}/global", json={}), 422, "bad_param", "estimation_id")
    refused(ea.post(f"{X}/global", json={"estimation_id": row + 99}), 404, "estimation_not_found")
    refused(ea.post(f"{X}/qq", json={"estimation_id": row, "quantiles": 1}), 422, "bad_param", "quantiles")
    # kna: the body is checked here
    refused(ea.post(f"{X}/kna", json={}), 422, "bad_param", "estimation_id")
    refused(ea.post(f"{X}/kna", json={"estimation_id": row, "estimation": row_body(w)}), 422, "bad_param",
            "estimation_id")  # fmt: skip
    refused(ea.post(f"{X}/kna", json={"estimation_id": row, "vary": ["цвет"]}), 422, "bad_param", "vary.0")
    refused(ea.post(f"{X}/kna", json={"estimation_id": row, "blocks": []}), 422, "bad_param", "blocks")
    refused(ea.post(f"{X}/kna", json={"estimation_id": row, "blocks": [{"ijk": [1, 1]}]}), 422, "bad_param",
            "blocks.0.ijk")  # fmt: skip
    refused(ea.post(f"{X}/kna", json={"estimation_id": row + 99}), 404, "estimation_not_found")
    refused(ea.post(f"{X}/kna", json={"estimation": row_body(w, method={"kind": "nn"})}), 422, "kna_method",
            "method.kind")  # fmt: skip
    refused(ea.post(f"{X}/kna", json={"estimation": row_body(w, disc=[1, 1, 11])}), 422, "bad_param", "disc.2")
    run_row_id = run_row(ea)
    assert run_row_id != row
    # the jobs of the checks through the routes
    kna = finish(ea, ok(ea.post(f"{X}/kna", json={"estimation_id": run_row_id, "vary": ["n_max"],
                                                   "blocks": [{"ijk": [4, 4, 7]}]})))  # fmt: skip
    assert kna["status"] == "done", kna
    assert kna["result"]["estimation_id"] == run_row_id and kna["result"]["blocks"][0]["at"]["ijk"] == [4, 4, 7]
    assert kna["result"]["vary"] == ["n_max"] and kna["result"]["recommend"]["n_max"] in (None, *range(4, 65, 4))
    one_off = finish(ea, ok(ea.post(f"{X}/kna", json={"estimation": row_body(w), "vary": ["radius"],
                                                      "blocks": [{"row": 700}]})))  # fmt: skip
    assert one_off["status"] == "done" and one_off["result"]["estimation_id"] is None, one_off
    glob = finish(ea, ok(ea.post(f"{X}/global", json={"estimation_id": run_row_id})))
    assert glob["status"] == "done", glob
    g = glob["result"]
    assert g["field"] == "AU_OK_EST" and g["estimate"]["n"] > 0 and g["composites"]["declus"]["n"] > 0
    assert isinstance(g["norm"], bool) and g["block_var"]["expected"] > 0
    q = ok(ea.post(f"{X}/qq", json={"estimation_id": run_row_id, "quantiles": 20}))
    assert q["estimation_id"] == run_row_id and q["field"] == "AU_OK_EST" and q["weighted"] is True
    assert len(q["x"]) == len(q["y"]) if isinstance(q["x"], list) else q["x"]["n"] > 0
    unweighted = ok(ea.post(f"{X}/qq", json={"estimation_id": run_row_id, "weighted": False}))
    assert unweighted["weighted"] is False
    # the read-only project may check, not write
    back = read_only(ea)
    try:
        ok(ea.post(f"{X}/qq", json={"estimation_id": run_row_id}))
        assert finish(ea, ok(ea.post(f"{X}/global", json={"estimation_id": run_row_id})))["status"] == "done"
    finally:
        back()
    # the overtaken Q-Q
    from bf_core.blocks import viz_spec as sp

    tok = sp.GATE.enter("qq", 7)
    try:
        refused(ea.post(f"{X}/qq", json={"estimation_id": run_row_id, "channel": "qq", "seq": 1}), 409, "superseded")
    finally:
        sp.GATE.leave(tok)
    # the swath settings of Э13 for the row
    s = ok(ea.post(f"{X}/swath", json={"estimation_id": run_row_id, "direction": "x"}))
    assert s["direction"] == "x" and s["estimation_id"] == run_row_id
    blocks, samples = s["series"]
    assert blocks["kind"] == "blocks" and blocks["model_id"] == w.model and blocks["field"] == "AU_OK_EST"
    assert samples["kind"] == "samples" and samples["field"] == "AU" and samples["within"] == "blocks"
    assert blocks["filter"] == {"field": "ZONE", "labels": [LABELS[0]]}
    refused(ea.post(f"{X}/swath", json={"estimation_id": run_row_id, "direction": "w"}), 422, "bad_param", "direction")
    refused(ea.post(f"{X}/swath", json={"estimation_id": run_row_id + 99}), 404, "estimation_not_found")
    assert ok(ea.post(f"{X}/swath", json={"estimation_id": run_row_id}))["direction"] == "z"
