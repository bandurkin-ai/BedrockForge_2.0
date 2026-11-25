"""The profile of the blocks beside the contact analysis (Д-28, подшаг 18.Д2; e18-api.md §8.5): `POST /stats/contact`
with `blocks {model_id, field, status_field?, domain_field?}` adds `blocks` — the answer of
`estimate.contact.blocks_profile` in the format of the samples (the same edges) — for the contact of the samples:
a closed wireframe («до каркаса») or the category field of the model with the same A, B. The profile is checked
against the kernel and against the analytic distance of the block centres to a cube; the samples part of the answer
is what it is without `blocks`; the refusals name the parameter.

The file `tests/data/stats/contact_blocks_answers.json` is read by vitest (`ui/test/contact-blocks.test.ts`).
Regenerate: `BF_REGEN_CONTACT_BLOCKS=1 uv run pytest -q tests/test_stats_contact_blocks.py`. The floats are compared
to 9 digits."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.geo.intersect import MeshTarget
from bf_core.geostat.estimate import contact as ec
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.meshutil import UTM, cube
from tests.test_estimate_contact import cell_ijk, make
from tests.test_geo_sdf import box_sdf
from tests.test_stats_contact_api import S, body, post, rich, tilted  # noqa: F401  (fixtures)

DATA = Path(__file__).parent / "data" / "stats" / "contact_blocks_answers.json"
REGEN = os.environ.get("BF_REGEN_CONTACT_BLOCKS") == "1"
TOL = 1e-12
FIELDS = [
    {"name": "EST", "dtype": "f8", "unit": "%", "role": "grade"},
    {"name": "STATUS", "dtype": "i1"},
    {"name": "ROCK", "dtype": "cat", "labels": ["гранит", "диорит"], "role": "code"},
]


def cube_model(c: Any) -> tuple[Any, np.ndarray, np.ndarray, np.ndarray]:
    """14³ blocks of 1 m round the cube of 10 m of `tilted`: EST 1 inside, 3 outside, 2 in the layer of blocks
    within 1 m of a face (a softened boundary); STATUS 0 but for two blocks (STATUS 2, EST 99: not estimated)."""
    project = projects_of(c.core).require_current()
    counts = [14, 14, 14]
    i, j, k = cell_ijk(counts)
    idx = i + 14 * (j + 14 * k)
    corner = UTM - 2.0
    grid = {"corner": [float(x) for x in corner], "counts": counts, "size": [1.0, 1.0, 1.0]}
    centres = corner + np.column_stack([i, j, k]) + 0.5
    d = box_sdf(centres, UTM, 10.0)  # inside < 0
    est = np.where(d < 0, 1.0, 3.0)
    est[np.abs(d) < 1.0] = 2.0
    status = np.zeros(len(idx))
    bad = np.flatnonzero((d > 2.0) & (d < 4.0))[:2]
    est[bad], status[bad] = 99.0, 2.0
    values = {"EST": est, "STATUS": status, "ROCK": np.where(d < 0, 1, 0).astype(np.int32)}
    return make(project, BlobStore(project.folder), grid, idx, values, fields=FIELDS), d, est, bad


def want_steps(d: np.ndarray, est: np.ndarray, edges: list[float]) -> tuple[list[Any], list[int]]:
    """The profile by hand: the sign is that of B (inside) above 0; blocks of 1 m³, the weight is the volume."""
    s = -d
    mean: list[Any] = []
    n: list[int] = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (s >= lo) & (s < hi) if hi < edges[-1] else (s >= lo) & (s <= hi)
        n.append(int(m.sum()))
        mean.append(float(est[m].sum() / m.sum()) if m.any() else None)
    return mean, n


def blocks_arg(model: Any, **extra: Any) -> dict[str, Any]:
    return {"model_id": model.id, "field": "EST", "status_field": "STATUS", **extra}


def wireframe_request(set_id: int, cube_id: int, **extra: Any) -> dict[str, Any]:
    return {"v": 1, "set_id": set_id, "table": "Пробы", "field": "PB", "domain": None, "domain_table": None,
            "a": None, "b": None, "distance": 5, "step": 1, "wireframe_id": cube_id, "weight": "length",
            **extra}  # fmt: skip


def test_the_samples_part_does_not_change_with_blocks(tilted) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, cube_id, _inside = tilted
    model, *_ = cube_model(c)
    plain = post(c, wireframe_request(set_id, cube_id))
    assert "blocks" not in plain
    both = post(c, wireframe_request(set_id, cube_id, blocks=blocks_arg(model)))
    assert both.pop("blocks") and both == plain


def test_blocks_at_the_wireframe_are_the_kernel_and_the_analytic_distance(tilted) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, cube_id, _inside = tilted
    model, d, est, bad = cube_model(c)
    got = post(c, wireframe_request(set_id, cube_id, blocks=blocks_arg(model)))
    prof = got["blocks"]
    assert prof["edges"] == got["edges"] == [float(k) for k in range(-5, 6)]  # beside the samples: the same steps
    assert (prof["model_id"], prof["field"], prof["weighted"]) == (model.id, "EST", True)
    assert (prof["a"]["label"], prof["b"]["label"]) == ("снаружи", "внутри")
    ok = np.ones(len(d), dtype=bool)
    ok[bad] = False
    mean, n = want_steps(d[ok], est[ok], prof["edges"])
    assert prof["n"] == n and sum(n) == prof["counts"]["in_steps"] and prof["counts"]["not_estimated"] == 2
    for got_m, want_m in zip(prof["mean"], mean, strict=True):
        assert (got_m is None) == (want_m is None) and (got_m is None or abs(got_m - want_m) < TOL)
    assert prof["mean"][4] == 2.0 and prof["mean"][5] == 2.0  # the layers beside the faces
    assert prof["mean"][3] == 3.0 and prof["mean"][6] == 1.0 and prof["mean"][2] == 3.0 and prof["mean"][9] == 1.0
    assert prof["mean"][0] is None  # the grid reaches 2 m beyond the cube: a corner is 4.3 m away at most
    assert prof["volume"] == [float(x) for x in prof["n"]]
    v, t = cube(2, 10.0, offset=UTM)  # the kernel on the same wireframe
    want = ec.blocks_profile(model, field="EST", status_field="STATUS", distance=5, step=1,
                             wireframe=MeshTarget(v, t, solid="outward"))  # fmt: skip
    assert json.loads(json.dumps(prof)) == json.loads(json.dumps(want))
    # without the status every block counts: the two blocks of EST 99 are in the steps
    plain = post(c, wireframe_request(set_id, cube_id, blocks={"model_id": model.id, "field": "EST"}))["blocks"]
    assert "not_estimated" not in plain["counts"] and plain["n"] != prof["n"]
    assert max(m for m in plain["mean"] if m is not None) > 3.0
    # the refusals of the contact «до каркаса» keep their order: a wireframe that is not there is 404
    r = c.post(f"{S}/contact", json=wireframe_request(set_id, 999_999, blocks=blocks_arg(model)))
    assert r.status_code == 404 and r.json()["code"] == "wireframe_not_found"


def put_domain_model(c: Any) -> Any:
    """A model of 10 × 8 × 12 blocks of 2 × 1 × 0.5 m: ROCK гранит for i < 5 (EST 1) and диорит after (EST 3)."""
    project = projects_of(c.core).require_current()
    counts = [10, 8, 12]
    i, j, k = cell_ijk(counts)
    idx = i + 10 * (j + 8 * k)
    values = {"EST": np.where(i < 5, 1.0, 3.0), "STATUS": np.zeros(len(idx)),
              "ROCK": np.where(i < 5, 0, 1).astype(np.int32)}  # fmt: skip
    grid = {"corner": [0.0, 0.0, 0.0], "counts": counts, "size": [2.0, 1.0, 0.5]}
    return make(project, BlobStore(project.folder), grid, idx, values, fields=FIELDS)


def test_blocks_at_the_domain_field_of_the_model(rich) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, _project = rich
    model = put_domain_model(c)
    got = post(c, body(set_id, distance=10, step=2, blocks=blocks_arg(model, domain_field="ROCK")))
    prof = got["blocks"]
    assert (prof["a"]["label"], prof["b"]["label"]) == ("гранит", "диорит") and prof["edges"] == got["edges"]
    assert prof["a"]["mean"] == 1.0 and prof["b"]["mean"] == 3.0
    # the centres are 1, 3, … 9 m from the contact (a cell 2 m wide): every step has blocks
    assert prof["mean"] == [1.0] * 5 + [3.0] * 5 and all(k > 0 for k in prof["n"])
    want = ec.blocks_profile(model, field="EST", status_field="STATUS", domain_field="ROCK", a="гранит",
                             b="диорит", distance=10, step=2)  # fmt: skip
    assert json.loads(json.dumps(prof)) == json.loads(json.dumps(want))
    # the domain field of the blocks is that of the samples (`domain`: ROCK) when `domain_field` is not given
    assert post(c, body(set_id, distance=10, step=2, blocks=blocks_arg(model)))["blocks"] == prof
    # the same A, B of the samples; B → A is the mirror
    back = post(c, body(set_id, distance=10, step=2, a="диорит", b="гранит",
                        blocks=blocks_arg(model, domain_field="ROCK")))["blocks"]  # fmt: skip
    assert back["n"] == prof["n"][::-1] and back["a"]["label"] == "диорит"


def test_refusals_name_the_parameter(rich) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, _project = rich
    model = put_domain_model(c)

    def refused(blocks: Any, code: str, param: str | None, status: int = 422, **extra: Any) -> None:
        r = c.post(f"{S}/contact", json=body(set_id, blocks=blocks, **extra))
        assert r.status_code == status and r.json()["code"] == code, (blocks, r.text)
        assert r.json().get("param") == param and r.json()["message"] and r.json()["hint"]

    ok = blocks_arg(model, domain_field="ROCK")
    refused("EST", "bad_param", "blocks")
    refused({"field": "EST"}, "bad_param", "blocks")
    refused({**ok, "model_id": "1"}, "bad_param", "blocks")
    refused({**ok, "field": ""}, "bad_param", "blocks")
    refused({**ok, "model_id": 999_999}, "block_model_not_found", None, 404)
    refused({**ok, "field": "нет такого"}, "block_field_not_found", None, 404)
    refused({**ok, "field": "ROCK"}, "field_not_number", "field")
    refused({**ok, "domain_field": "EST"}, "field_not_category", "domain_field")
    refused({**ok, "domain_field": "нет такого"}, "block_field_not_found", None, 404)
    refused({**ok, "status_field": "ROCK"}, "field_not_number", "status_field")
    refused(ok, "bad_param", "step", step=0)


# --- the answers the window reads ----------------------------------------------------------------------------------


def same(x: Any, y: Any) -> bool:
    if isinstance(x, float) or isinstance(y, float):
        return isinstance(x, int | float) and isinstance(y, int | float) and math.isclose(
            x, y, rel_tol=1e-9, abs_tol=1e-9)  # fmt: skip
    if isinstance(x, dict):
        return isinstance(y, dict) and x.keys() == y.keys() and all(same(x[k], y[k]) for k in x)
    if isinstance(x, list):
        return isinstance(y, list) and len(x) == len(y) and all(same(a, b) for a, b in zip(x, y, strict=True))
    return bool(x == y)


def keep(name: str, request: dict[str, Any], answer: dict[str, Any]) -> None:
    """The whole answer of the core under `name`: written with `BF_REGEN_CONTACT_BLOCKS=1`, else compared."""
    part = json.loads(json.dumps({"request": request, "answer": answer}))
    stored = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        stored[name] = part
        DATA.write_text(json.dumps(stored, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return
    assert same(part, stored[name])


def test_answer_for_the_window_at_a_wireframe(tilted) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, cube_id, _inside = tilted
    model, *_ = cube_model(c)
    req = wireframe_request(set_id, cube_id, blocks=blocks_arg(model))
    keep("wireframe", req, post(c, req))


def test_answer_for_the_window_at_a_domain_field(rich) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, set_id, _project = rich
    model = put_domain_model(c)
    req = body(set_id, distance=10, step=2, blocks=blocks_arg(model, domain_field="ROCK"))
    keep("domain", req, post(c, req))
