"""Parameters of an implicit model (подшаг 19.1; ТЗ 8.9.4; 7.35 Д-24, Д-25, Д-31, Д-39; ADR 0026 решения 4–22,
30): the defaults of 8.9.4, every range → 422 `bad_param` {param}, the checks of the groups within the model, the
round trip of `tests/data/implicit/params.json` body → spec → canonical dict, the parts of
`contract_answers.json` that `spec.py` writes (`defaults`, `bad_param`, `refusals`), no numpy at import.
`BF_REGEN_IMPLICIT=1` writes the canonical forms and those parts anew (the rest of the file is a draft until 19.16)."""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.model import spec
from bf_core.model import spec_parts as sp

DATA = Path(__file__).parent / "data" / "implicit"
REGEN = os.environ.get("BF_REGEN_IMPLICIT") == "1"
BOX = [0, 0, -300, 1000, 800, 100]
BASE: dict[str, Any] = {
    "boundary": {"box": BOX},
    "data": {"set_id": 3, "table": "12", "field": "LITH"},
    "groups": [{"id": 1, "name": "Дайка", "type": "domain", "codes": ["DY"]},
               {"id": 2, "name": "Песчаник", "type": "domain", "codes": ["SS"]}],
}  # fmt: skip
SHELL = {"name": "Au", "type": "shell", "params": {"result_id": 31, "field": "AU", "thresholds": [0.5, 1.0]}}
BAD_PARAM_BODY = {**BASE, "pou": {"n_min": 10}}


def body(**over: Any) -> dict[str, Any]:
    out = copy.deepcopy(BASE)
    out.update(over)
    return out


def refused(b: Any, code: str = "bad_param", status: int = 422, fn: Any = spec.parse_model) -> str:
    with pytest.raises(ApiError) as e:
        fn(b)
    assert e.value.status == status and e.value.code == code, (e.value.code, e.value.message)
    assert e.value.message and e.value.hint  # Russian text with a hint
    return str(e.value.extra.get("param"))


def test_defaults_8_9_4() -> None:
    s = spec.parse_model({"boundary": {"box": BOX}})
    assert s.kernel == "linear" and s.kernel_radius is None and s.h is None and s.levels == 2
    assert (s.pou.n_patch, s.pou.n_min, s.pou.delta) == (1000, 100, 0.25)
    assert s.smoothing == sp.Smoothing("manual", None, None)
    assert s.trend_id is None and s.anisotropy is None and s.data is None and s.groups == () and s.manual == ()
    assert s.post == sp.Post(0.0, False, "boundary", None)
    d = spec.parse_model(BASE).data
    assert d is not None and (d.d_max, d.step) == (30.0, 5.0)
    auto = spec.parse_model(body(smoothing={"mode": "auto"})).smoothing
    assert auto.cond_target == 1e8 and auto.s is None
    assert sp.RANGES["trend_radius"].lo == 10.0 and sp.TREND_RADIUS == 200.0


def test_h_is_the_diagonal_over_200_within_its_range() -> None:
    s = spec.parse_model(BASE).resolved((0.0, 0.0, -300.0, 1000.0, 800.0, 100.0))
    assert s.h == math.sqrt(1000**2 + 800**2 + 400**2) / 200.0
    assert spec.parse_model(BASE).resolved((0, 0, 0, 1, 1, 1)).h == 0.1  # clamped from below
    assert spec.parse_model(BASE).resolved((0, 0, 0, 1e5, 1e5, 1e3)).h == 100.0  # clamped from above
    assert spec.parse_model(body(h="2,5")).resolved((0, 0, 0, 10, 10, 10)).h == 2.5  # given — kept
    with pytest.raises(ApiError) as e:
        spec.parse_model(body(h=0.1)).resolved((0, 0, 0, 1e6, 1, 1))
    assert e.value.code == "bad_param" and e.value.extra["param"] == "h"


def test_s_by_group_type_and_eta_formulas() -> None:
    s = spec.parse_model(body(groups=[*BASE["groups"], SHELL]))
    dom, shell = s.groups[0], s.groups[2]
    assert s.s_of(dom) == 0.0 and s.s_of(shell) == 0.05
    given = spec.parse_model(body(smoothing={"s": "0,2"}))
    assert given.s_of(given.groups[0]) == 0.2
    assert spec.eta("linear", 0.5, 2.0) == 1.0 and spec.eta("cubic", 0.5, 2.0) == 4.0
    assert spec.eta("wendland", 0.5, 2.0) == 0.5


@pytest.mark.parametrize(
    ("over", "param"),
    [
        ({"h": 0.05}, "h"), ({"h": 101}, "h"), ({"levels": 5}, "levels"), ({"levels": 1.5}, "levels"),
        ({"pou": {"n_patch": 299}}, "pou.n_patch"), ({"pou": {"n_patch": 4001}}, "pou.n_patch"),
        ({"pou": {"n_min": 19}}, "pou.n_min"), ({"pou": {"n_min": 501}}, "pou.n_min"),
        ({"pou": {"n_patch": 300, "n_min": 400}}, "pou.n_min"),
        ({"pou": {"delta": 0.09}}, "pou.delta"), ({"pou": {"delta": 1.01}}, "pou.delta"),
        ({"data": {"set_id": 3, "table": "12", "field": "L", "d_max": 0.5}}, "data.d_max"),
        ({"data": {"set_id": 3, "table": "12", "field": "L", "d_max": 501}}, "data.d_max"),
        ({"data": {"set_id": 3, "table": "12", "field": "L", "step": 0.4}}, "data.step"),
        ({"data": {"set_id": 3, "table": "12", "field": "L", "step": 51}}, "data.step"),
        ({"data": {"table": "12", "field": "L"}}, "data.set_id"),
        ({"smoothing": {"s": -0.1}}, "smoothing.s"), ({"smoothing": {"s": 10.5}}, "smoothing.s"),
        ({"smoothing": {"mode": "auto", "cond_target": 1e3}}, "smoothing.cond_target"),
        ({"smoothing": {"mode": "auto", "cond_target": 1e13}}, "smoothing.cond_target"),
        ({"smoothing": {"mode": "fast"}}, "smoothing.mode"),
        ({"kernel": "gauss"}, "kernel"), ({"kernel": "wendland", "kernel_radius": 0}, "kernel_radius"),
        ({"anisotropy": {"ang2": 91}}, "anisotropy.ang2"), ({"anisotropy": {"ang1": 361}}, "anisotropy.ang1"),
        ({"anisotropy": {"ratios": [1, 0, 1]}}, "anisotropy.ratios.1"),
        ({"anisotropy": {"ratios": [0.5, 1, 1]}}, "anisotropy.ratios.0"),
        ({"anisotropy": {"ratios": [1, 1]}}, "anisotropy.ratios"),
        ({"trend_id": 9, "anisotropy": {"ang1": 10}}, "anisotropy"),
        ({"boundary": {}}, "boundary"), ({"boundary": {"box": BOX, "wireframe_id": 5}}, "boundary"),
        ({"boundary": {"box": [0, 0, 0, 1, 1]}}, "boundary.box"), ({"boundary": {"box": [0, 0, 0, "x", 1, 1]}},
                                                                    "boundary.box.3"),
        ({"post": {"v_min": -1}}, "post.v_min"), ({"post": {"clip": "wireframe"}}, "post.clip_id"),
        ({"post": {"simplify": "да"}}, "post.simplify"),
        ({"manual": [{"layer_id": 7, "group": 9}]}, "manual.0.group"),
        ({"manual": [{"layer_id": 7, "group": 1, "role": "above"}]}, "manual.0.role"),
    ],
)  # fmt: skip
def test_ranges_refused_with_param(over: dict[str, Any], param: str) -> None:
    assert refused(body(**over)) == param


def test_boundary_min_not_below_max_is_boundary_empty() -> None:
    assert refused(body(boundary={"box": [0, 0, 0, 10, 0, 10]}), "boundary_empty") == "boundary.box.4"
    s = spec.parse_model(body(boundary={"wireframe_id": 5, "dtm_id": 6}))
    assert s.boundary == sp.Boundary(None, 5, 6)


def test_text_numbers_and_angles() -> None:
    s = spec.parse_model(body(h="2,5", anisotropy={"ang1": 360, "ang3": -180, "ratios": [1, "0,5", "0.25"]}))
    assert s.h == 2.5 and s.anisotropy == sp.Anisotropy(0.0, 0.0, 180.0, (1.0, 0.5, 0.25))


@pytest.mark.parametrize(
    ("group", "param"),
    [
        ({"name": "X", "type": "lens", "codes": ["A"]}, "groups.2.type"),
        ({"type": "domain", "codes": ["A"]}, "groups.2.name"),
        ({"name": "X", "codes": [""]}, "groups.2.codes.0"),
        ({"name": "X", "codes": ["A"], "relation": "under"}, "groups.2.relation"),
        ({"name": "X", "type": "shell", "params": {"field": "AU", "thresholds": [1]}}, "groups.2.params.result_id"),
        ({"name": "X", "type": "shell", "params": {"result_id": 3, "field": "AU"}}, "groups.2.params.thresholds"),
        ({"name": "X", "type": "shell", "params": {"result_id": 3, "field": "AU", "thresholds": [1, 1]}},
         "groups.2.params.thresholds.1"),
        ({"name": "X", "type": "shell", "params": {"result_id": 3, "field": "AU", "thresholds": [1],
                                                    "mode": "indicator", "p": 0.2}}, "groups.2.params.p"),
        ({"name": "X", "type": "shell", "params": {"result_id": 3, "field": "AU", "thresholds": [1],
                                                    "mode": "indicator", "p": 0.71}}, "groups.2.params.p"),
        ({"name": "X", "type": "shell", "codes": ["A"], "params": {"result_id": 3, "field": "AU", "thresholds": [1]}},
         "groups.2.codes"),
        ({"name": "X", "type": "shell", "params": {"result_id": 3, "field": "AU", "thresholds": [1],
                                                    "transform": "sqrt"}}, "groups.2.params.transform"),
        ({"name": "X", "type": "vein", "codes": ["Q"], "params": {"t_min": 101}}, "groups.2.params.t_min"),
        ({"name": "X", "type": "vein", "codes": ["Q"], "params": {"bound": "polygon", "polygon": [[0, 0], [1, 1]]}},
         "groups.2.params.polygon"),
        ({"name": "X", "type": "vein", "codes": ["Q"], "params": {"offset": -1}}, "groups.2.params.offset"),
        ({"name": "X", "type": "expand", "params": {"wireframe_id": 5, "delta": 0}}, "groups.2.params.delta"),
        ({"name": "X", "type": "expand", "params": {"wireframe_id": 5, "delta": 1001}}, "groups.2.params.delta"),
        ({"name": "X", "type": "expand", "params": {"delta": 5}}, "groups.2.params.wireframe_id"),
        ({"name": "X", "type": "fault", "codes": ["F"], "params": {"radius": 0}}, "groups.2.params.radius"),
        ({"name": "X", "type": "fault", "codes": ["F"], "relation": "erode"}, "groups.2.faults"),
        ({"name": "X", "type": "vein_system", "params": {"veins": [{"code": "V1"}]}}, "groups.2.params.veins"),
        ({"name": "X", "type": "vein_system", "params": {"veins": [{"code": "V1"}, {"code": "V1"}]}},
         "groups.2.params.veins.1.code"),
        # within the model
        ({"id": 1, "name": "X", "codes": ["A"]}, "groups.2.id"),
        ({"name": "дайка", "codes": ["A"]}, "groups.2.name"),
        ({"name": "X", "codes": ["A", "DY"]}, "groups.2.codes.1"),
        ({"name": "X", "codes": []}, "groups.2.codes"),
        ({"name": "X", "codes": ["A"], "faults": [1]}, "groups.2.faults.0"),
    ],
)  # fmt: skip
def test_groups_refused_with_param(group: dict[str, Any], param: str) -> None:
    assert refused(body(groups=[*BASE["groups"], group])) == param


def test_codes_need_data_and_manual_data_is_enough() -> None:
    assert refused(body(data=None)) == "data"
    no_codes = [{"id": 1, "name": "Контакт", "codes": []}]
    s = spec.parse_model(body(data=None, groups=no_codes, manual=[{"layer_id": 7, "group": 1}]))
    assert s.manual == (sp.Manual(7, "contact", 1),) and s.groups[0].codes == ()


def test_groups_numbered_and_canonical_codes() -> None:
    groups = [{"name": "A", "codes": [12, 1.5, "  SS ", 12]}, {"id": 5, "name": "B", "codes": ["X"]},
              {"name": "C", "codes": ["Y"]}]  # fmt: skip
    s = spec.parse_model(body(groups=groups))
    assert [g.id for g in s.groups] == [6, 5, 7]
    assert s.groups[0].codes == ("12", "1.5", "SS") and s.groups[0].relation == "conformable"
    assert s.group(5).name == "B"
    with pytest.raises(ApiError) as e:
        s.group(99)
    assert e.value.status == 404 and e.value.code == "group_not_found"


def test_with_group_replaces_or_adds_last() -> None:
    s = spec.parse_model(BASE)
    add = s.with_group(spec.parse_group({"name": "Гранит", "codes": ["GR"]}))
    assert [g.id for g in add.groups] == [1, 2, 3] and add.groups[2].name == "Гранит"
    put = s.with_group(spec.parse_group({"id": 1, "name": "Дайка-2", "codes": ["DY", "DY2"]}))
    assert [g.name for g in put.groups] == ["Дайка-2", "Песчаник"]
    with pytest.raises(ApiError) as e:
        s.with_group(spec.parse_group({"name": "песчаник", "codes": ["Z"]}))
    assert e.value.extra["param"] == "groups.2.name"


def test_stage_b_relations_and_faults() -> None:
    groups = [*BASE["groups"], {"id": 3, "name": "Разлом", "type": "fault", "codes": ["F"]}]
    s = spec.parse_model(body(groups=groups))
    r = spec.parse_relations({"order": [2, 1], "relations": {"1": "erode", "2": "intrusion"}}, s)
    assert [(g.id, g.relation) for g in r.groups] == [(2, "intrusion"), (1, "erode"), (3, "conformable")]
    assert spec.parse_relations({}, s).groups == s.groups
    assert refused({"order": [1]}, fn=lambda b: spec.parse_relations(b, s)) == "order"
    assert refused({"order": [1, 2, 3]}, fn=lambda b: spec.parse_relations(b, s)) == "order"
    assert refused({"relations": {"9": "erode"}}, fn=lambda b: spec.parse_relations(b, s)) == "relations.9"
    assert refused({"relations": {"1": "slide"}}, fn=lambda b: spec.parse_relations(b, s)) == "relations.1"
    cut = spec.parse_model(body(groups=[{**BASE["groups"][0], "faults": [3]}, *groups[1:]]))
    assert cut.groups[0].faults == (3,)


def test_requests_of_a_model() -> None:
    assert spec.parse_build({"expected_version": 3}) == spec.BuildSpec(3, None)
    assert spec.parse_build({"expected_version": 3, "threads": "4"}).threads == 4
    assert refused({}, fn=spec.parse_build) == "expected_version"
    assert refused({"expected_version": 1, "threads": 65}, fn=spec.parse_build) == "threads"
    p = spec.parse_preview({"group_id": 2})
    assert (p.group_id, p.group, p.limit) == (2, None, 200_000)
    assert spec.parse_preview({"group": {"name": "X", "codes": ["A"]}}).group is not None
    assert refused({}, fn=spec.parse_preview) == "group_id"
    assert refused({"group_id": 1, "limit": 0}, fn=spec.parse_preview) == "limit"
    assert spec.parse_check({}) == spec.CheckSpec(None, 1, 500, "residual", "json")
    assert refused({"size": 5001}, fn=spec.parse_check) == "size"
    assert refused({"sort": "x"}, fn=spec.parse_check) == "sort"
    assert spec.parse_support({}, 30.0) == (None, 60.0)
    assert refused({"threshold": 0}, fn=spec.parse_support) == "threshold"
    c = spec.parse_compare({"set_id": 3, "table": "12", "field": "LITH"})
    assert (c.set_id, c.table, c.field, c.page, c.size) == (3, "12", "LITH", 1, 500)
    assert refused({"set_id": 3, "table": "12"}, fn=spec.parse_compare) == "field"


def test_refusals_russian_with_hints() -> None:
    for code, (status, message, hint) in sp.REFUSALS.items():
        e = spec.refuse(code, group=1)
        assert (e.status, e.code, e.message, e.hint, e.extra) == (status, code, message, hint, {"group": 1})
        assert any("а" <= ch <= "я" for ch in message.lower() + hint.lower())
    assert {"bad_param", "rbf_singular", "too_many_points", "wireframe_open", "trend_not_found", "trend_structural",
            "no_contacts", "boundary_empty"} <= {*sp.REFUSALS, "bad_param"}  # решение 30
    assert spec.refuse("no_contacts", "Свой текст.").message == "Свой текст."


def test_meta_parses_as_it_is_and_hash_has_no_name() -> None:
    s = spec.parse_model(BASE).resolved(tuple(float(x) for x in BOX))
    meta = {**s.canonical(), "v": 1, "algo": spec.ALGO, "extent": BOX, "outputs": [], "stats": {}, "inputs": [],
            "state": "ready", "error": None, "versions": {}, "last_run": None, "created_at": "x"}  # fmt: skip
    assert spec.parse_model(meta).canonical() == s.canonical()
    h = s.hash_params()
    assert "name" not in h and h["algo"] == "rbf-1"


def _bad_param_answer() -> dict[str, Any]:
    with pytest.raises(ApiError) as e:
        spec.parse_model(BAD_PARAM_BODY)
    x = e.value
    return {"status": x.status, "body": {**x.extra, "code": x.code, "message": x.message, "hint": x.hint}}


def _spec_answers() -> dict[str, Any]:
    refusals = {code: {"status": st, "body": {"code": code, "message": m, "hint": h}}
                for code, (st, m, h) in sp.REFUSALS.items()}  # fmt: skip
    return {"defaults": {"status": 200, "body": {"params": spec.defaults_json(), "ranges": spec.ranges_json()}},
            "bad_param": _bad_param_answer(), "refusals": refusals}  # fmt: skip


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def test_params_json_round_trip() -> None:
    path = DATA / "params.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for name, case in data["cases"].items():
        s = spec.parse_model(case["body"])
        got = json.loads(json.dumps(s.canonical()))
        assert spec.parse_model(got).canonical() == s.canonical(), name  # canonical parses to itself
        if REGEN:
            case["canonical"] = got
        else:
            assert got == case["canonical"], name
    if REGEN:
        data["_note"] = ("Примеры параметров неявной модели (19.1): body — тело POST /implicit/models; canonical — "
                         "spec.parse_model(body).canonical() (часть meta модели, docs/progress/e19-api.md §3–4). "
                         "Перегенерация: BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_spec.py")
        _write(path, data)


def test_contract_answers_spec_parts() -> None:
    path = DATA / "contract_answers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    want = json.loads(json.dumps(_spec_answers()))
    if REGEN:
        data.update(want)
        data["_note"] = ("ЧЕРНОВИК форм ответов (19.1, docs/progress/e19-api.md §5–§10): настоящие ответы ядра "
                         "запишет 19.16 (BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_api_answers.py); "
                         "интерфейс принимается только на них. Уже настоящие (пишет spec.py, "
                         "BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_spec.py): defaults, bad_param, "
                         "refusals. Ключи *_b — Э19б.")  # fmt: skip
        _write(path, data)
    for key, value in want.items():
        assert data[key] == value, key
    assert "ЧЕРНОВИК" in data["_note"]
    for key, answer in data.items():
        if key != "_note" and isinstance(answer, dict) and "status" in answer and answer["status"] >= 400:
            b = answer["body"]
            assert b["code"] and b["message"] and b["hint"], key


def test_no_numpy_at_import() -> None:
    code = "import sys, bf_core.model.spec; raise SystemExit(3 if 'numpy' in sys.modules else 0)"
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-400:]
