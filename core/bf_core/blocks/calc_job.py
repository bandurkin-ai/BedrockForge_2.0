"""The calculator of a block model: the job `blocks.calc` (подшаг 17.9; ТЗ 8.7.4; ADR 0023 §7, decision 10, № 69;
the forms — `docs/progress/e17-api.md` §8, §15).

    spec = parse(model_id, body)                       # the body of `POST /blocks/{id}/calc` (422 `bad_param {param}`)
    prog, dtype = compile_for(model, expr, dtype)      # `POST /blocks/calc/check` and the job: 422 with `pos`
    out = run_calc(engine, folder, params, tick=tick)  # the job: {model_id, field, version, rows, passed, missing, …}
    is_stale(conn, store, model, field)                # «устарел» of a field of the calculator (17.29, 17.36)

The job reads the model by portions of 2²⁰ rows (`Model.iter`), keeps the rows the restrictor lets pass (the filter
`filter_id` of the library and the quick restrictor `restrict`, together as AND — `restrict.combine`; a block passes
where its share is > 0, № 65), evaluates the expression with `calc.compile` on the portion and streams the result
into one file of fields (`FieldsWriter`); the file is committed whole in the last stage («Запись»), or nothing is
left: a cancel, a failure and a stale version leave neither the file nor a row.

Outside the restrictor (`outside`): `"keep"` — the value the field had (a new field — empty; a replaced one — its
old values; a replaced number in a number, a replaced category in a category — other pairs leave it empty, with a
warning), `"nan"` — empty. «Empty» is NaN for f8/f4 and null for i4/i2/i1/cat — never 0 (ТЗ 17). Values by type:
  * f8, f4 — as evaluated (f4 refuses a value it cannot hold: 422 `field_values_invalid`);
  * i4, i2, i1 — rounded to the nearest whole number, half away from zero, NaN → null; out of range → 422;
  * cat — the codes of the labels of the expression (`IF(AU > 1, 'руда', 'порода')`); a condition (`bool`) is stored
    as a number 1 / 0 (default i1).
The default `dtype`: f8 for a number, i1 for a condition, cat for a text. A category from a numeric expression and a
number from a text one — 422 `expr_type` at the root of the expression.

An existing name without `replace` — 409 `field_exists {field}` (the window asks «Заменить?», № 69); with `replace`
the old field leaves the model at the commit, its previous version is not kept; the unit, the description and the
role of the old field are NOT inherited (the role only where the new field is of the same kind: a replaced `grade`
stays `grade`). An expression may read the field it replaces (`AU = AU * 2`).

`source` of the new field: `{op: calc, params {expr, text, dtype, filter_id, restrict, outside}, inputs, algo,
versions, at}`; `inputs` — the SHA-256 of the files of the model that hold the fields read (the expression, the
restrictor, the old values kept outside it; not the field's own file), of the files of the wireframes and surfaces
of the restrictor and of its tree (a changed filter changes it). `is_stale` compares them with the current ones: a
changed input file, a changed filter or an `algo` of another version of the calculator — «устарел» (the interface
offers «Перестроить»: the job again with `source.params`, `replace: true`). `versions` {numpy, numexpr, numba} is
recorded; the result is the same bit for bit with any of them (17.6), so they do not make a field stale.

Stages (the message of the job): «Чтение», «Расчёт», «Запись». The result carries the events
`objects.changed {kind: "block_model", id}` for the scheduler to publish.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.calc import ExprError, ExprTypeError, compile
from bf_core.calc.runtime import round_half_away
from bf_core.db import dialect
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import restrict
from . import schema as bs
from .fields_store import FieldsWriter, commit_fields, version_conflict
from .filters import schema as fs
from .model import ANY, CHUNK, Model

if TYPE_CHECKING:
    from bf_core.calc import Program
    from bf_core.jobs.context import JobContext

KIND = "blocks.calc"
TITLE = "Калькулятор блочной модели"
ALGO = "calc-1"
DTYPES = ("f8", "f4", "i4", "i2", "i1", "cat")
OUTSIDE = ("keep", "nan")
DEFAULT_DTYPE = {"num": "f8", "bool": "i1", "cat": "cat"}
STAGE_READ, STAGE_CALC, STAGE_WRITE = "Чтение", "Расчёт", "Запись"
Tick = Callable[[float | None, str | None], None]
_NAME = re.compile(r"\[([^\]]*)\]|\w+")


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def bad_param(param: str, message: str, hint: str = "Проверьте параметры калькулятора.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- the body -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    model_id: int
    expected_version: int
    expr: str
    field: str
    dtype: str | None
    unit: str | None
    description: str | None
    filter_id: int | None
    restrict: dict[str, Any] | None
    outside: str
    replace: bool


def _text(body: Mapping[str, Any], key: str, limit: int, *, required: bool = False) -> str | None:
    raw = body.get(key)
    if raw is None:
        if required:
            raise bad_param(key, "Не задано обязательное значение.")
        return None
    if not isinstance(raw, str):
        raise bad_param(key, "Ожидается текст.")
    out = " ".join(raw.split()) if key != "expr" else raw
    if required and not out.strip():
        raise bad_param(key, "Значение не может быть пустым.")
    if len(out) > limit:
        raise bad_param(key, f"Не больше {limit} символов.")
    return out or None


def parse(model_id: int, body: Mapping[str, Any]) -> Spec:
    """The body of `POST /blocks/{id}/calc` (e17-api §8) checked: the shapes only, the model and the expression are
    checked against the model by `compile_for` and the job."""
    version = body.get("expected_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise bad_param("expected_version", "Нужна версия модели (целое число от 1).",
                        "Обновите свойства модели и повторите.")  # fmt: skip
    dtype = body.get("dtype")
    if dtype is not None and dtype not in DTYPES:
        raise bad_param("dtype", "Неизвестный тип поля.", "Допустимо: " + ", ".join(DTYPES) + ".")
    outside = body.get("outside", "keep")
    if outside not in OUTSIDE:
        raise bad_param("outside", "Вне ограничителя: «оставить» (keep) или пусто (nan).")
    replace = body.get("replace", False)
    if not isinstance(replace, bool):
        raise bad_param("replace", "Ожидается true или false.")
    fid = body.get("filter_id")
    if fid is not None and (isinstance(fid, bool) or not isinstance(fid, int) or fid < 1):
        raise bad_param("filter_id", "Номер фильтра — целое число от 1.")
    quick = body.get("restrict")
    if quick is not None:
        quick = fs.normalize_rules(quick, "restrict")
    name = _text(body, "field", bs.MAX_FIELD_NAME, required=True)
    expr = _text(body, "expr", 4000, required=True)
    assert name is not None and expr is not None
    return Spec(int(model_id), version, expr, name, dtype, _text(body, "unit", bs.MAX_UNIT),
                _text(body, "description", bs.MAX_DESCRIPTION), fid, quick, outside, replace)  # fmt: skip


# --- the expression against the model -------------------------------------------------------------


def schema_of(model: Model) -> dict[str, Any]:
    return {
        str(f["name"]): (list(f.get("labels") or []) if f.get("kind") == bs.CATEGORY else "num") for f in model.fields
    }


def expr_api_error(e: ExprError, text: str, param: str = "expr") -> ApiError:
    """422 `expr_syntax` / `expr_field` / `expr_type` with the place in the text (`pos`, `line`, `column`); for an
    unknown field also its name (`field`)."""
    extra: dict[str, Any] = {k: v for k, v in e.to_body().items() if k not in ("code", "message", "hint")}
    extra["param"] = param
    if e.code == "expr_field":
        found = _NAME.match(text, e.pos)
        if found:
            extra["field"] = found.group(1) if found.group(1) is not None else found.group(0)
    return ApiError(422, e.code, e.message, e.hint, extra)


def compile_for(model: Model, expr: str, dtype: str | None) -> tuple[Program, str]:
    """(program, stored type) of `expr` on `model`; `dtype` None — the default of the kind of the result."""
    try:
        prog = compile(expr, schema_of(model))
    except ExprError as e:
        raise expr_api_error(e, expr) from e
    kind = prog.out_kind
    if dtype is None:
        return prog, DEFAULT_DTYPE[kind]
    if (dtype == "cat") != (kind == "cat"):
        what = "Текстовое выражение (категория)" if kind == "cat" else "Числовое выражение или условие"
        use = "категория" if dtype == "cat" else f"число {dtype}"
        hint = "Для категории пишите IF(AU > 1, 'руда', 'порода'); для числа — тип f8, f4, i4, i2 или i1."
        err = ExprTypeError(expr, prog.expr.pos, f"{what} нельзя записать в поле типа «{use}»", hint)
        raise expr_api_error(err, expr)
    return prog, dtype


# --- the inputs and «устарел» ---------------------------------------------------------------------


def _versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in ("numpy", "numexpr", "numba"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def rules_tree(conn: sa.Connection, filter_id: int | None, quick: Mapping[str, Any] | None) -> dict[str, Any] | None:
    from .filters import store as filters

    return restrict.combine(None if filter_id is None else filters.rules_of(conn, filter_id), quick)


def _digest(tree: Mapping[str, Any]) -> str:
    text = json.dumps(tree, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inputs_of(
    conn: sa.Connection, model: Model, fields: list[str], tree: Mapping[str, Any] | None, own: str | None
) -> list[str]:
    """SHA-256 (sorted, once each) of what a field of the calculator depends on: the files of the fields `fields`
    (not of `own`, the field written), the files of the wireframes of `tree` and the tree itself."""
    from bf_core.wireframes import files as wf_files

    have = {str(f["name"]).casefold(): f for f in model.fields}
    shas = model.shas()
    out: set[str] = set()
    for name in fields:
        spec = have.get(name.casefold())
        if spec is None or (own is not None and name.casefold() == own.casefold()):
            continue
        out.add(shas[str(spec.get("file") or bs.ROLE)])
    if tree is not None:
        out.add(_digest(tree))
        for oid in fs.object_ids(tree):
            try:
                out.update(wf_files.blob_shas(conn, oid).values())
            except ApiError:  # a lost wireframe: the leaf is «недоступен» — it does not change the inputs
                continue
    return sorted(out)


def is_stale(conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any]) -> bool:
    """A field of the calculator is «устарел»: its inputs or the algorithm of the calculator changed since it was
    written, or it cannot be built again (an input field or the filter is gone). Reads the meta only."""
    src = field.get("source")
    if not isinstance(src, Mapping) or src.get("op") != "calc":
        return False
    params = src.get("params") or {}
    try:
        prog, _ = compile_for(model, str(params["expr"]), params.get("dtype"))
        tree = rules_tree(conn, params.get("filter_id"), params.get("restrict"))
        names = list(prog.fields_used)
        if tree is not None:
            names += fs.fields_of(tree)
        names += [str(field["name"])] if params.get("outside") == "keep" else []
        now = inputs_of(conn, model, names, tree, str(field["name"]))
    except (ApiError, KeyError):
        return True
    return src.get("algo") != ALGO or sorted(src.get("inputs") or []) != now


# --- the calculation ------------------------------------------------------------------------------


def _old_values(got: np.ndarray, kind: str, lut: np.ndarray | None) -> np.ndarray:
    """The values the replaced field had in this portion, in the form of the new field (categories — in the codes of
    the labels of the new field, `lut`)."""
    if kind == bs.NUMBER:
        return got
    assert lut is not None
    return np.where(got >= 0, lut[np.clip(got, 0, None)], -1).astype(np.int32)


def run_calc(
    engine: sa.Engine, folder: Any, params: Mapping[str, Any], *, tick: Tick = _noop, chunk: int = CHUNK
) -> dict[str, Any]:
    """The body of `blocks.calc` (the handler and the tests). `params` — the body of the request and `model_id`."""
    spec = parse(int(params["model_id"]), params)
    store = BlobStore(folder)
    clock = _Clock(tick)
    clock(0.02, STAGE_READ)
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, spec.model_id, ANY)
        if model.version != spec.expected_version:
            raise version_conflict(model.version)
        prog, dtype = compile_for(model, spec.expr, spec.dtype)
        old = existing_field(model, spec)
        tree = rules_tree(conn, spec.filter_id, spec.restrict)
        r = restrict.prepare(model, tree, conn=conn, store=store)
        wanted = [*prog.fields_used, *r.fields]
        inputs = inputs_of(conn, model, wanted, tree, spec.field)
    kind = bs.CATEGORY if dtype == "cat" else bs.NUMBER
    labels: list[str] = list(prog.labels) if kind == bs.CATEGORY else []
    lut: np.ndarray | None = None
    notes: list[str] = []
    kept: dict[str, Any] | None = old if spec.outside == "keep" else None  # the field whose values stay outside
    if kept is not None and kept["kind"] != kind:
        notes.append(f"Прежние значения поля «{kept['name']}» другого типа не сохранены: вне ограничителя оно пусто.")
        kept = None
    if kept is not None and kind == bs.CATEGORY:
        known = {s: i for i, s in enumerate(labels)}
        for s in kept.get("labels") or []:
            known.setdefault(str(s), len(known))
        labels = list(known)
        lut = np.array([known[str(s)] for s in kept.get("labels") or []] or [-1], dtype=np.int32)
    roles = bs.CATEGORY_ROLES if kind == bs.CATEGORY else bs.NUMBER_ROLES
    role = old["role"] if old is not None and old.get("role") in roles else None
    passed = 0
    # ModelView of fields_store is invariant in `meta` and settable: a `Model` fits it by duck typing
    with FieldsWriter(store, model, spec.expected_version) as w:  # type: ignore[arg-type]
        w.add(spec.field, dtype, unit=spec.unit, role=role, description=spec.description,
              labels=labels if kind == bs.CATEGORY else None, replace=spec.replace)  # fmt: skip
        clock(0.05, STAGE_CALC)
        read = [*prog.fields_used, *([] if kept is None else [str(kept["name"])])]
        for ch, f in r.iter(model, extra=read, chunk=chunk):
            clock(0.05 + 0.85 * (ch.start + ch.n) / max(model.rows, 1), None)
            raw = prog.eval_chunk(ch.cols, ch.n)
            vals = raw.astype(np.float64) if kind == bs.NUMBER else raw.astype(np.int32)
            inside = f > 0.0
            passed += int(np.count_nonzero(inside))
            if not inside.all():
                if kept is None:
                    vals = np.where(inside, vals, np.float64(np.nan) if kind == bs.NUMBER else np.int32(-1))
                else:
                    vals = np.where(inside, vals, _old_values(ch.cols[str(kept["name"])], kind, lut))
            if dtype in ("i4", "i2", "i1"):
                vals = round_half_away(vals, 0)
            w.write({spec.field: vals})
        clock(0.92, STAGE_WRITE)
        staged = w.finish()
    new = staged.fields[0]
    source = {"op": "calc", "algo": ALGO, "versions": _versions(), "inputs": inputs,
              "params": {"expr": spec.expr, "text": prog.expr.to_text(), "dtype": dtype, "filter_id": spec.filter_id,
                         "restrict": spec.restrict, "outside": spec.outside}}  # fmt: skip
    try:
        with dialect.write_tx(engine) as conn:
            version = commit_fields(conn, store, staged, source=source)
    finally:
        staged.discard()  # a write transaction that did not open leaves no file without an owner
    clock.close()
    stats = new["stats"]
    return {"model_id": spec.model_id, "field": new["name"], "version": version, "rows": model.rows,
            "passed": passed, "missing": int(stats["missing"]), "stats": stats, "dtype": dtype,
            "warnings": list(dict.fromkeys([*notes, *r.warnings])), "stages": clock.stages(),
            "events": [["objects.changed", {"kind": "block_model", "id": spec.model_id}]]}  # fmt: skip


def existing_field(model: Model, spec: Spec) -> dict[str, Any] | None:
    """The field of the model under the name of the request (case aside); 409 `field_exists` without `replace`."""
    old = next((f for f in model.fields if str(f["name"]).casefold() == spec.field.casefold()), None)
    if old is not None and not spec.replace:
        raise ApiError(409, "field_exists", f"Поле «{old['name']}» в модели уже есть.",
                       "Задайте другое имя или замените поле.", {"field": old["name"]})  # fmt: skip
    return None if old is None else dict(old)


class _Clock:
    """Seconds of the stages from the messages of the ticks."""

    KEYS = {STAGE_READ: "read", STAGE_CALC: "calc", STAGE_WRITE: "write"}

    def __init__(self, tick: Tick) -> None:
        self.tick, self.name, self.t0 = tick, "read", time.perf_counter()
        self.sec: dict[str, float] = {}

    def __call__(self, progress: float | None = None, message: str | None = None) -> None:
        self.tick(progress, message)
        if message in self.KEYS and self.KEYS[message] != self.name:
            self._shift(self.KEYS[message])

    def _shift(self, name: str) -> None:
        now = time.perf_counter()
        self.sec[self.name] = self.sec.get(self.name, 0.0) + now - self.t0
        self.name, self.t0 = name, now

    def close(self) -> None:
        self._shift(self.name)

    def stages(self) -> dict[str, float]:
        return {k: round(v, 3) for k, v in self.sec.items()}


def memory_mb(params: dict[str, Any]) -> int:
    """A portion of 2²⁰ rows: the fields read, the evaluation temporaries and the row groups of the writer."""
    return 640


# --- the warm-up ----------------------------------------------------------------------------------


def warm() -> dict[str, float]:
    """numexpr and the evaluation of a small expression (`ops/warm.py` calls it as a step)."""
    t0 = time.perf_counter()
    prog = compile("IF(A > 1, A * 2 + SQRT(ABS(B)), COALESCE(B, 0))", {"A": "num", "B": "num"})
    prog.eval_chunk({"A": np.linspace(0.0, 3.0, 4096), "B": np.linspace(-1.0, 1.0, 4096)}, 4096)
    return {"calc": time.perf_counter() - t0}


# --- the handler ----------------------------------------------------------------------------------


@handler(KIND, TITLE, queue="quick", estimate_memory=memory_mb)
def calc_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        return run_calc(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
