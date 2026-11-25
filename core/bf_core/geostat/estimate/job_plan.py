"""The plan of a run of `estimate.run` (подшаг 18.12; ТЗ 8.8; ADR 0024 §7, решение 10, № 77; docs/progress/e18-api.md
§2.2, §6): the rows of the plan, their groups of one prefix, the siblings that a partial repeat keeps, the checks
against the project — everything the job decides before the first block is estimated.

    plan = build(conn, store, run, model_view=None)      # Plan; ApiError 404 / 409 / 422 as the contract names them
    plan.rows[i]   RowRun — a row of the plan: the stored parameters (`stored`, for `record_run`), the resolved ones
                   (`spec`), the restrictor, the inputs of the graph, the tallies; `samples` and `rk` come later
    plan.groups    Group — the rows that write one prefix: one set of fields, each row its own blocks (§2.2)
    plan.names     the output fields in the order they are written (STATUS first in each group)

Siblings (№ 77): the rows of the same prefix that share a plan with a row of the run but are not in it. A repeat of
one row rewrites the blocks of its domain only; the blocks of the domain of a sibling keep the values the current
fields have (they are carried into the new file), STATUS 1 is written for the blocks that belong to no known domain.
Nothing to carry (the fields are new) — those blocks get STATUS 1 and no values, as before the first run of the
sibling.

Nothing heavy at import: numpy only. The errors name the row («Строка «…»: …») when the plan has more than one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import calc_job, restrict
from bf_core.blocks import schema as bs
from bf_core.db.schema import objects

from . import store_base as sb
from . import store_check as sc
from .store_plan import check_plan, plans_with

if TYPE_CHECKING:
    from bf_core.blocks.model import Model
    from bf_core.storage.blobs import BlobStore

    from .kernel import RowKernel
    from .samples import RowSamples
    from .spec import EstimationSpec, RunSpec


PORTION = 1 << 16  # blocks of a piece (the kernel's portion)
CHUNK_ROWS = 1 << 18  # rows of the model read at a time


@dataclass
class Tally:
    """What the pieces of one row add up to."""

    by_status: np.ndarray = field(default_factory=lambda: np.zeros(4, np.int64))
    by_pass: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    singular: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    neg_kv: int = 0


@dataclass
class RowRun:
    index: int  # the position in the run (`items.N`)
    id: int
    name: str
    stored: EstimationSpec  # as the store parses its `meta` (the hash of `last_run.params` is of it)
    spec: EstimationSpec  # the search resolved (a trend), the field of the domain in its stored spelling
    origin: list[float] | None  # the stored one; the job takes the centre of the extent of the samples now
    inputs: list[dict[str, Any]]  # `meta.inputs` read before the samples (`store.inputs_of`)
    restrictor: restrict.Restrictor
    tree: dict[str, Any] | None
    prefix: str
    columns: list[dict[str, Any]]
    tally: Tally = field(default_factory=Tally)
    samples: RowSamples | None = None
    rk: RowKernel | None = None


@dataclass(frozen=True)
class Sibling:
    id: int
    field: str | None  # the stored name of the field of its domain; None — every block
    label: str | None


@dataclass
class Group:
    prefix: str
    rows: list[RowRun]
    columns: list[dict[str, Any]]  # `spec.columns()` of the first row: STATUS first
    siblings: list[Sibling] = field(default_factory=list)
    carry: dict[str, str] = field(default_factory=dict)  # new name → the stored name of the field to carry


@dataclass
class Plan:
    model: Model
    plan_id: int | None
    rows: list[RowRun]
    groups: list[Group]
    names: list[str]  # every output field, in the order of writing
    files: list[str]  # SHA-256 of what the domains and the restrictors read (`source.inputs`)

    @property
    def read(self) -> list[str]:
        """The fields of the model a chunk must hold: the domains (rows and siblings), the restrictors, the carried."""
        out: dict[str, None] = {}
        for r in self.rows:
            if r.spec.target.domain_field is not None:
                out[r.spec.target.domain_field] = None
            out.update(dict.fromkeys(r.restrictor.fields))
        for g in self.groups:
            out.update(dict.fromkeys(s.field for s in g.siblings if s.field is not None))
            out.update(dict.fromkeys(g.carry.values()))
        return list(out)


def _err(row: RowRun | str | None, e: ApiError, many: bool) -> ApiError:
    """The error of a row with the name of the row when there are several of them."""
    if not many or row is None:
        return e
    name = row if isinstance(row, str) else row.name
    return ApiError(e.status, e.code, f"Строка «{name}»: {e.message}", e.hint, e.extra)


def stored_field(model: Model, name: str | None) -> str | None:
    """The field of the model by the name, in its stored spelling (None — there is none)."""
    if name is None:
        return None
    key = str(name).casefold()
    return next((str(f["name"]) for f in model.fields if str(f["name"]).casefold() == key), None)


def domain_mask(model: Model, name: str | None, label: Any, cols: Mapping[str, np.ndarray], n: int) -> np.ndarray:
    """The blocks of a domain: bool [n]. No field — every block; a category — the code of the label; a number —
    the value (a block without it, NaN or −1, is in no domain)."""
    if name is None:
        return np.ones(n, dtype=np.bool_)
    spec = model.field(name)
    values = cols[str(spec["name"])]
    if spec["kind"] == bs.CATEGORY:
        labels = [str(x) for x in spec.get("labels") or []]
        if str(label) not in labels:
            return np.zeros(n, dtype=np.bool_)
        return np.asarray(values == labels.index(str(label)))
    try:
        want = float(str(label).replace(",", "."))
    except ValueError:
        return np.zeros(n, dtype=np.bool_)
    return np.asarray(values == want)


def _resolve(conn: sa.Connection, run: RunSpec) -> tuple[int | None, list[int], int | None, str]:
    """(plan id, the ids of the rows, the model of the plan, the name of the list in `param`)."""
    if run.plan_id is None:
        return None, list(run.estimation_ids), None, "estimation_ids"
    row = sb.stored(conn, sb.PLAN, run.plan_id)
    meta = row.meta or {}
    ids = [int(i) for i in meta.get("items") or []]
    if not ids:
        raise sb.bad("plan_id", f"В плане «{row.name}» нет строк.", "Добавьте в план параметры оценки.")
    return run.plan_id, ids, meta.get("model_id"), "items"


def _row(conn: sa.Connection, model: Model, mmeta: Mapping[str, Any], store: BlobStore, i: int, ident: int,
         spec: EstimationSpec, param: str, many: bool) -> RowRun:  # fmt: skip
    row = sb.stored(conn, sb.KIND, ident)
    meta = dict(row.meta or {})
    name = str(row.name)
    if sc.freshness(conn, meta)["source_lost"]:
        raise ApiError(409, "source_lost", f"У строки «{name}» удалён источник: таблица, набор или модель.",
                       "Выберите существующие данные в параметрах оценки или уберите строку из плана.",
                       {"param": f"{param}.{i}"})  # fmt: skip
    try:
        work = spec
        if work.search.source == "trend" and work.trend_id is not None:
            work = work.resolved(trend=sc.trend_form(conn, work.trend_id))
        work = replace(work, target=sc.check_target(conn, work, dict(mmeta)))
        tree = calc_job.rules_tree(conn, work.target.filter_id, work.target.rules)
        restrictor = restrict.prepare(model, tree, conn=conn, store=store)
    except ApiError as e:
        raise _err(name, e, many) from e
    return RowRun(i, ident, name, spec, work, meta.get("origin"), sc.inputs_of(conn, spec), restrictor, tree,
                  work.outputs.prefix, work.columns())  # fmt: skip


def _siblings(conn: sa.Connection, model: Model, rows: list[RowRun]) -> dict[str, list[Sibling]]:
    """The rows of the plans of the run that are not in it, by the prefix (module docstring)."""
    plans = plans_with(conn)
    run_ids = {r.id for r in rows}
    out: dict[str, list[Sibling]] = {}
    for prefix in dict.fromkeys(r.prefix for r in rows):
        pids = sorted({p for r in rows if r.prefix == prefix for p in plans.get(r.id, [])})
        seen: set[int] = set()
        for pid in pids:
            for item in (sb.stored(conn, sb.PLAN, pid).meta or {}).get("items") or []:
                ident = int(item)
                if ident in run_ids or ident in seen:
                    continue
                seen.add(ident)
                got = conn.execute(sa.select(objects.c.meta).where(
                    objects.c.id == ident, objects.c.kind == sb.KIND)).first()  # fmt: skip
                if got is None:
                    continue
                try:
                    sp = sb.load_spec(got.meta or {})
                except ApiError:
                    continue
                if sp.outputs.prefix != prefix or sp.model_id != model.id:
                    continue
                t = sp.target
                name = stored_field(model, t.domain_field)
                if t.domain_field is not None and name is None:
                    continue  # the field of its domain is gone: it covers no block
                out.setdefault(prefix, []).append(Sibling(ident, name, None if t.label is None else str(t.label)))
    return out


def _groups(model: Model, rows: list[RowRun], sibs: Mapping[str, list[Sibling]]) -> list[Group]:
    have = {str(f["name"]).casefold(): str(f["name"]) for f in model.fields}
    groups: dict[str, Group] = {}
    for r in rows:
        g = groups.get(r.prefix)
        if g is None:
            g = groups[r.prefix] = Group(r.prefix, [], r.columns, list(sibs.get(r.prefix, [])))
        g.rows.append(r)
    for g in groups.values():
        if g.siblings:  # the blocks of a sibling keep what the fields have now
            g.carry = {c["name"]: have[c["name"].casefold()] for c in g.columns if c["name"].casefold() in have}
    return list(groups.values())


def _check_names(model: Model, groups: list[Group], replace_fields: bool) -> list[str]:
    have = {str(f["name"]).casefold(): str(f["name"]) for f in model.fields}
    seen: dict[str, str] = {}
    names: list[str] = []
    for g in groups:
        for c in g.columns:
            key = c["name"].casefold()
            if key in seen:
                raise sb.bad("estimation_ids", f"Строки с префиксами «{seen[key]}» и «{g.prefix}» пишут одно поле "
                             f"«{c['name']}».", "Задайте строкам разные префиксы.")  # fmt: skip
            seen[key] = g.prefix
            names.append(c["name"])
            if key in have and not replace_fields:
                raise ApiError(409, "field_exists", f"Поле «{have[key]}» в модели уже есть.",
                               "Задайте другой префикс или замените поля.", {"field": have[key]})  # fmt: skip
    return names


def build(conn: sa.Connection, store: BlobStore, run: RunSpec, model_view: Model | None = None) -> Plan:
    """The plan of a run (module docstring), in the caller's read transaction. 404 `estimation_not_found`,
    `plan_not_found`, `block_model_not_found`; 409 `version_conflict {version}` (the model is not that of the
    request), `source_lost`, `field_exists {field}` (without `replace`); 422 `bad_param`, `variable_grid`,
    `field_not_found`, `domain_unmapped`, `filter_*`, `no_structures`."""
    from bf_core.blocks.fields_store import version_conflict
    from bf_core.blocks.model import Model

    plan_id, ids, model_id, param = _resolve(conn, run)
    specs = check_plan(conn, ids, model_id, param)
    mid = specs[0].model_id
    _name, mmeta = sc.block_model(conn, mid)
    model = model_view or Model.open(conn, store, mid)
    if model.version != run.expected_version:
        raise version_conflict(model.version)
    many = len(ids) > 1
    rows = [_row(conn, model, mmeta, store, i, ident, spec, param, many)
            for i, (ident, spec) in enumerate(zip(ids, specs, strict=True))]  # fmt: skip
    groups = _groups(model, rows, _siblings(conn, model, rows))
    names = _check_names(model, groups, run.replace)
    files: set[str] = set()
    for r in rows:
        used = [*r.restrictor.fields, *([] if r.spec.target.domain_field is None else [r.spec.target.domain_field])]
        files.update(calc_job.inputs_of(conn, model, used, r.tree, None))
    return Plan(model, plan_id, rows, groups, names, sorted(files))
