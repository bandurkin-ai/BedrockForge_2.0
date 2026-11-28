"""The jobs `blocks.density` and `blocks.classify` — the fields of a block model by a rule (подшаг 17.29; ТЗ 8.7.5; ADR
0023 §9, решения 13, 22; the forms — `docs/progress/e17-api.md` §9, §15). The kernels are `density.py` and
`categories.py` (17.26); the model is the same of calc: `calc_job.py`.

    out = run_density(engine, folder, params, tick=tick)      # DENS (f8, role density) and DENS_SRC (i1)
    out = run_classify(engine, folder, params, tick=tick)     # CLASS (cat, role class)
    inputs_now(conn, store, model, field) → sha256 list | None   # what «устарел» compares (`source.inputs`)

`params` of a job is the body of the request, checked by the route, and `model_id`: `expected_version`, `filter_id`,
`restrict` (the filter of the library and the quick restrictor, AND — `restrict.combine`), `replace`, `unit` and
`description` (optional: the unit and the description of the field; without them the defaults — «т/м³», the texts
below; «Перестроить» passes the ones the field has), and the body of the kernel: `density.Spec.params()` or
`categories.Spec.params()` (`rules` there are those of the scheme as they were; `scheme` is only the name).

Both write whole files of fields or nothing (`FieldsWriter` → `commit_fields`; a cancel, a failure and a stale
version leave neither the file nor a row). Outside the restrictor the fields are empty (NaN / null — never 0). The
name of a field in use without `replace` — 409 `field_exists {field}` before the work; with `replace` the old field
leaves the model at the commit (its role is not inherited: the roles are those of the result). `source` of every
field: {op: density | classify, params: {…body, filter_id, restrict}, inputs, algo, versions, at}; `inputs` — the
SHA-256 of the files of the fields the rule reads (the formula, the table, the rules, the estimate, the restrictor), of
the wireframes of the restrictor and of the categories, and of the tree of the restrictor (a changed filter makes the
field «устарел»); the fields written are not inputs.

Stages (the message): «Чтение», «Расчёт», «Мажоритарный фильтр» (only when it is asked for), «Запись». Results:
  density  {model_id, field, fields [out, src], version, rows, passed, defaulted, empty, stats, dtype, warnings,
            stages {read, calc, write, total}, events}
  classify {model_id, field, fields [out], version, rows, passed, counts {label: blocks}, unclassified, no_estimate,
            stats, warnings, stages, events}
`events` — `objects.changed {kind: "block_model", id}`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import calc_job, restrict
from bf_core.blocks import categories as cat
from bf_core.blocks import density as dens
from bf_core.blocks import schema as bs
from bf_core.blocks.fields_store import FieldsWriter, commit_fields, version_conflict
from bf_core.blocks.filters import schema as fs
from bf_core.blocks.jobs import Clock
from bf_core.blocks.model import ANY, CHUNK, Model
from bf_core.db import dialect
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

KIND_DENSITY = "blocks.density"
KIND_CLASSIFY = "blocks.classify"
TITLE_DENSITY = "Плотность блочной модели"
TITLE_CLASSIFY = "Категории блочной модели"
S_READ, S_CALC, S_MAJORITY, S_WRITE = "Чтение", "Расчёт", "Мажоритарный фильтр", "Запись"
KEYS = {S_READ: "read", S_CALC: "calc", S_MAJORITY: "majority", S_WRITE: "write"}
DENSITY_TEXT = "Плотность блока"
SOURCE_TEXT = "Источник плотности: 0 — значение блока, 1 — подставлено «по умолчанию»"
CLASS_TEXT = "Категория блока по правилам схемы"
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def bad(param: str, message: str, hint: str = "Проверьте параметры операции.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- the common part of the body --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Common:
    expected_version: int
    filter_id: int | None
    restrict: dict[str, Any] | None
    replace: bool
    unit: str | None
    description: str | None


def _text(body: Mapping[str, Any], key: str, limit: int) -> str | None:
    raw = body.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise bad(key, "Ожидается текст.")
    out = " ".join(raw.split())
    if len(out) > limit:
        raise bad(key, f"Не больше {limit} знаков.")
    return out or None


def parse_common(body: Mapping[str, Any]) -> Common:
    """The keys every operation on a field has: `expected_version` (422 `bad_param`), `filter_id`, `restrict`,
    `replace`, `unit`, `description`."""
    version = body.get("expected_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise bad("expected_version", "Нужна версия модели (целое число от 1).",
                  "Обновите свойства модели и повторите.")  # fmt: skip
    fid = body.get("filter_id")
    if fid is not None and (isinstance(fid, bool) or not isinstance(fid, int) or fid < 1):
        raise bad("filter_id", "Номер фильтра — целое число от 1.")
    quick = body.get("restrict")
    if quick is not None:
        quick = fs.normalize_rules(quick, "restrict")
    replace = body.get("replace", False)
    if not isinstance(replace, bool):
        raise bad("replace", "Ожидается true или false.")
    return Common(version, fid, quick, replace, _text(body, "unit", bs.MAX_UNIT),
                  _text(body, "description", bs.MAX_DESCRIPTION))  # fmt: skip


def check_names(model: Model, names: list[str], replace: bool) -> None:
    """409 `field_exists {field}` for a name the model has already (without case), unless `replace`."""
    have = {str(f["name"]).casefold(): str(f["name"]) for f in model.fields}
    if len({n.casefold() for n in names}) != len(names):
        raise bad("src_field", "Имена полей результата не могут совпадать.")
    for n in names:
        if n.casefold() in have and not replace:
            raise ApiError(409, "field_exists", f"Поле «{have[n.casefold()]}» в модели уже есть.",
                           "Задайте другое имя или замените поле.", {"field": have[n.casefold()]})  # fmt: skip


def check_restrictor(model: Model, tree: Mapping[str, Any] | None) -> None:
    """422 `filter_field_missing {field, model}` for a field of the restrictor the model lacks."""
    if tree is None:
        return
    have = {str(f["name"]).casefold() for f in model.fields}
    for name in fs.fields_of(fs.normalize_rules(tree)):
        if name.casefold() not in have:
            raise restrict.field_missing(name, model.name)


def source_of(op: str, algo: str, params: dict[str, Any], inputs: list[str]) -> dict[str, Any]:
    return {"op": op, "algo": algo, "versions": calc_job._versions(), "inputs": inputs, "params": params}  # noqa: SLF001


def _merged(*warnings: list[str]) -> list[str]:
    return list(dict.fromkeys(w for part in warnings for w in part))


# --- the density ------------------------------------------------------------------------------------------------------


def run_density(
    engine: sa.Engine, folder: Any, params: Mapping[str, Any], *, tick: Tick = _noop, chunk: int = CHUNK
) -> dict[str, Any]:  # fmt: skip
    """The body of `blocks.density` (the handler and the tests)."""
    com = parse_common(params)
    spec = dens.parse(params)
    store = BlobStore(Path(folder))
    clock = Clock(tick, KEYS)
    clock(0.02, S_READ)
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, int(params["model_id"]), ANY)
        if model.version != com.expected_version:
            raise version_conflict(model.version)
        check_names(model, [spec.out, spec.src_field], com.replace)
        plan = dens.prepare(model, spec)
        tree = calc_job.rules_tree(conn, com.filter_id, com.restrict)
        r = restrict.prepare(model, tree, conn=conn, store=store)
        names = [n for n in dict.fromkeys([*plan.fields, *r.fields]) if n.casefold() not in _own(spec)]
        inputs = calc_job.inputs_of(conn, model, names, tree, None)
    clock(0.05, S_CALC)
    with FieldsWriter(store, model, com.expected_version) as w:  # type: ignore[arg-type]  # ModelView: settable attrs
        w.add(spec.out, "f8", unit=com.unit or plan.unit, role="density", description=com.description or DENSITY_TEXT,
              replace=com.replace)  # fmt: skip
        w.add(spec.src_field, "i1", description=SOURCE_TEXT, replace=com.replace)
        for piece in dens.iter_density(model, plan, r, chunk=chunk):
            clock(0.05 + 0.85 * (piece.chunk.start + piece.chunk.n) / max(model.rows, 1), None)
            w.write({spec.out: piece.density, spec.src_field: piece.source})
        clock(0.92, S_WRITE)
        staged = w.finish()
    body = {**spec.params(), "filter_id": com.filter_id, "restrict": com.restrict}
    try:
        with dialect.write_tx(engine) as conn:
            version = commit_fields(conn, store, staged, source=source_of("density", dens.ALGO, body, inputs))
    finally:
        staged.discard()  # a write transaction that did not open leaves no file without an owner
    t = plan.tally
    return {"model_id": model.id, "field": spec.out, "fields": [spec.out, spec.src_field], "version": version,
            "rows": model.rows, "passed": t.passed, "defaulted": t.defaulted, "empty": t.empty,
            "stats": staged.fields[0]["stats"], "dtype": "f8", "warnings": _merged(plan.warnings, r.warnings),
            "stages": clock.stages(), "events": [["objects.changed", {"kind": bs.KIND, "id": model.id}]]}  # fmt: skip


def _own(spec: Any) -> set[str]:
    """The names a density writes (casefold): not inputs of itself."""
    return {spec.out.casefold(), spec.src_field.casefold()}


# --- the categories ---------------------------------------------------------------------------------------------------


def spec_of_params(params: Mapping[str, Any]) -> cat.Spec:
    """The `categories.Spec` of `source.params` (or of a job's params): the rules as they were, the scheme — its name
    only (the reference may have changed since)."""
    spec = cat.parse({**params, "scheme": None})
    return dataclasses.replace(spec, scheme=params.get("scheme") or None)


def _wire_tree(wire_id: int) -> dict[str, Any]:
    return {"wireframe": {"id": wire_id, "side": "inside", "by": "centre"}}


def wire_shas(conn: sa.Connection, spec: cat.Spec) -> list[str]:
    """The SHA-256 of the geometry of the wireframes of the categories (a lost one has none)."""
    from bf_core.wireframes import files as wf_files

    out: set[str] = set()
    for wid, _ in spec.overrides:
        try:
            out.update(wf_files.blob_shas(conn, wid).values())
        except ApiError:
            continue
    return sorted(out)


def run_classify(
    engine: sa.Engine, folder: Any, params: Mapping[str, Any], *, tick: Tick = _noop, chunk: int = CHUNK
) -> dict[str, Any]:  # fmt: skip
    """The body of `blocks.classify` (the handler and the tests)."""
    com = parse_common(params)
    spec = spec_of_params(params)
    store = BlobStore(Path(folder))
    clock = Clock(tick, KEYS)
    clock(0.02, S_READ)
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, int(params["model_id"]), ANY)
        if model.version != com.expected_version:
            raise version_conflict(model.version)
        check_names(model, [spec.out], com.replace)
        over = [restrict.prepare(model, _wire_tree(wid), conn=conn, store=store) for wid, _ in spec.overrides]
        plan = cat.prepare(model, spec, [o.weights for o in over])
        tree = calc_job.rules_tree(conn, com.filter_id, com.restrict)
        r = restrict.prepare(model, tree, conn=conn, store=store)
        names = [n for n in dict.fromkeys([*plan.fields, *r.fields]) if n.casefold() != spec.out.casefold()]
        inputs = sorted({*calc_job.inputs_of(conn, model, names, tree, None), *wire_shas(conn, spec)})
    clock(0.05, S_CALC)
    chunks = max(1, -(-model.rows // chunk))
    seen = [0]

    def progress() -> None:  # called once before every portion
        seen[0] += 1
        clock(0.05 + 0.6 * min(seen[0], chunks) / chunks, None)

    first = dataclasses.replace(plan, spec=dataclasses.replace(spec, majority=0))  # the filter — in its own stage
    res = cat.classify(model, first, r, chunk=chunk, cancel=progress)
    if spec.majority:
        clock(0.7, S_MAJORITY)
        codes = cat.majority_model(model, res.codes, len(spec.rules), spec.majority,
                                   lambda: clock(None, None))  # fmt: skip
        counts = np.bincount(codes[codes >= 0].astype(np.int64), minlength=len(spec.rules)).tolist()
        res = dataclasses.replace(res, codes=codes, counts=[int(c) for c in counts])
    clock(0.9, S_WRITE)
    with FieldsWriter(store, model, com.expected_version) as w:  # type: ignore[arg-type]
        w.add(spec.out, "cat", role="class", description=com.description or CLASS_TEXT, labels=res.labels,
              replace=com.replace)  # fmt: skip
        for part in res.iter_codes(chunk):
            w.write({spec.out: part})
        staged = w.finish()
    body = {**spec.params(), "scheme": spec.scheme, "filter_id": com.filter_id, "restrict": com.restrict}
    try:
        with dialect.write_tx(engine) as conn:
            version = commit_fields(conn, store, staged, source=source_of("classify", cat.ALGO, body, inputs))
    finally:
        staged.discard()  # a write transaction that did not open leaves no file without an owner
    return {"model_id": model.id, "field": spec.out, "fields": [spec.out], "version": version, "rows": model.rows,
            "passed": res.passed, "counts": dict(zip(res.labels, res.counts, strict=True)),
            "unclassified": res.unclassified, "no_estimate": res.no_estimate, "stats": staged.fields[0]["stats"],
            "warnings": _merged(r.warnings, *(o.warnings for o in over)), "stages": clock.stages(),
            "events": [["objects.changed", {"kind": bs.KIND, "id": model.id}]]}  # fmt: skip


# --- what a field read («устарел», «Где используется», «Перестроить») -----------------------------------------------


@dataclass
class Reads:
    """What the rule of a field of density / classify reads, resolved on the model."""

    names: list[str]  # stored names of the fields (without the fields written)
    tree: dict[str, Any] | None
    extra: list[str]  # SHA-256 of the wireframes of the categories
    out: list[str]  # the names the operation writes


def reads_of(conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any]) -> Reads:
    """The inputs of a field made by density or classify, from its `source.params` (ApiError — an input is gone:
    a field, the filter, an expression that does not compile)."""
    from bf_core.wireframes import store as wf

    src = field["source"]
    params = dict(src["params"])
    tree = calc_job.rules_tree(conn, params.get("filter_id"), params.get("restrict"))
    r = restrict.prepare(model, tree, conn=conn, store=store)
    if src["op"] == "density":
        spec = dens.parse(params)
        plan = dens.prepare(model, spec)
        own = _own(spec)
        out = [spec.out, spec.src_field]
        extra: list[str] = []
        fields = plan.fields
    else:
        cspec = spec_of_params(params)
        for wid, _ in cspec.overrides:  # a lost wireframe of a category is an input that is gone: 404
            wf.wireframe_info(conn, wid)
        cplan = cat.prepare(model, cspec, [lambda ch: np.zeros(ch.n)] * len(cspec.overrides))
        own = {cspec.out.casefold()}
        out = [cspec.out]
        extra = wire_shas(conn, cspec)
        fields = cplan.fields
    names = [n for n in dict.fromkeys([*fields, *r.fields]) if n.casefold() not in own]
    return Reads(names, tree, extra, out)


def inputs_now(conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any]) -> list[str]:
    """The SHA-256 list a field of density / classify would have if it were written now."""
    rd = reads_of(conn, store, model, field)
    return sorted({*calc_job.inputs_of(conn, model, rd.names, rd.tree, None), *rd.extra})


def is_stale(conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any]) -> bool:
    """«Устарел»: the files the rule reads, the filter or the algorithm changed, or an input is gone."""
    src = field.get("source")
    if not isinstance(src, Mapping) or src.get("op") not in ("density", "classify"):
        return False
    algo = dens.ALGO if src["op"] == "density" else cat.ALGO
    try:
        now = inputs_now(conn, store, model, field)
    except (ApiError, KeyError, TypeError, ValueError):
        return True
    return bool(src.get("algo") != algo or sorted(src.get("inputs") or []) != now)


def check_inputs_alive(conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any]) -> None:
    """For «Перестроить»: an ApiError of the rule (a field, a filter, a wireframe is gone) is returned as is."""
    reads_of(conn, store, model, field)


# --- the handlers -----------------------------------------------------------------------------------------------------


def _user(exc: ApiError) -> Any:
    from bf_core.jobs.context import JobError

    return JobError(exc.code, exc.message, exc.hint, retry=False)


def memory_density(params: dict[str, Any]) -> int:
    """A portion of 2²⁰ rows: the fields read and two columns written."""
    return 640


def memory_classify(params: dict[str, Any]) -> int:
    """The codes of the whole model (2 bytes a row), the rows P and the volumes of the majority filter."""
    return max(640, 512 + int(params.get("rows") or 0) * 40 // 2**20)


@handler(KIND_DENSITY, TITLE_DENSITY, queue="quick", estimate_memory=memory_density)
def density_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_density(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise _user(exc) from None


@handler(KIND_CLASSIFY, TITLE_CLASSIFY, queue="quick", estimate_memory=memory_classify)
def classify_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_classify(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise _user(exc) from None
