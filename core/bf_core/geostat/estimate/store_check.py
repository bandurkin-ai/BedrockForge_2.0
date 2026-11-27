"""What the store checks and records of a row of the estimation against the project (подшаг 18.11; ADR 0024 §2;
docs/progress/e18-api.md §2.3, §3, §4): the block model, the domain of the blocks, the samples (the fields, the
domain, the counts, the origin), the copies of the variogram models with the scale of Д-38, the trend, the filter,
the inputs of the graph (ADR 0025) and the current state of a stored row (`freshness`).

    prep = prepare(conn, project, spec, old=None, old_origin=None)     # Prepared(spec, origin, inputs, counts)
    inputs_of(conn, spec) -> [{kind, id, fp, name}]                     # the form of `meta.inputs` (ADR 0025 реш. 4)
    freshness(conn, meta) -> {stale, stale_reason, source_lost}

`fp` (ADR 0025 реш. 3): a table — `<token>:<rev>`; a set — `traj:` and the key of its trajectories; a variogram
model — its `meta.sha256`; a trend, a filter — `version`; the block model — `cells:<sha>`; a field of the model —
`cells:<sha>` / `fld:<sha>` of the file that holds it. Nothing heavy before BF-READY: numpy, the readers of the
tables and the models are imported in the functions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import blob_owners, dh_tables, objects

from . import store_base as sb
from .spec_parts import DomainRef, SampleSel, Target, VarioRef, parse_model, variogram_scale

if TYPE_CHECKING:
    from bf_core.geostat.variogram.spec import Data

    from .spec import EstimationSpec

LOST = "источник удалён"
BLOCK_MODEL, VARIOGRAM = "object:block_model", "object:variogram_model"
TREND, FILTER = "object:trend", "object:filter"
SET, TABLE, FIELD = "dh_set", "dh_table", "bm_field"
_KINDS = {BLOCK_MODEL: "block_model", VARIOGRAM: "variogram_model", TREND: "trend", FILTER: "filter",
          SET: "drillhole_set"}  # fmt: skip


def _api(status: int, code: str, message: str, hint: str, param: str | None = None, **extra: Any) -> ApiError:
    return ApiError(status, code, message, hint, {**({"param": param} if param else {}), **extra})


# --- the block model and its domain ---------------------------------------------------------------------------------


def block_model(conn: sa.Connection, model_id: int) -> tuple[str, dict[str, Any]]:
    """(name, meta v2) of a ready block model; 404 `block_model_not_found`, 422 `variable_grid` (a grid with
    tensors is not estimated)."""
    from bf_core.blocks import meta as bmeta

    r = conn.execute(sa.select(objects.c.name, objects.c.meta).where(
        objects.c.id == model_id, objects.c.kind == "block_model", objects.c.state == "ready")).first()  # fmt: skip
    if r is None:
        raise _api(404, "block_model_not_found", "Блочная модель не найдена.", "Выберите модель из списка проекта.",
                   "model_id")  # fmt: skip
    meta = bmeta.upgrade(r.meta or {})
    if "tensors" in meta["grid"]:
        raise _api(422, "variable_grid", f"Модель «{r.name}» с переменным шагом сетки: оценка не поддерживается.",
                   "Приведите модель к регулярной сетке («Регуляризация») и повторите.", "model_id")
    return str(r.name), meta


def _field_of(meta: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    for f in meta.get("fields") or []:
        if str(f["name"]).casefold() == str(name).casefold():
            return dict(f)
    return None


def check_target(conn: sa.Connection, spec: EstimationSpec, meta: Mapping[str, Any]) -> Target:
    """The field of the domain and its label are in the model (422 `field_not_found`, `domain_unmapped`), the filter
    exists (404 `filter_not_found`); the field in its stored spelling."""
    from bf_core.blocks.filters import store as fst
    from bf_core.blocks.schema import CATEGORY

    t = spec.target
    if t.domain_field is not None:
        f = _field_of(meta, t.domain_field)
        if f is None:
            raise _api(422, "field_not_found", f"В блочной модели нет поля «{t.domain_field[:80]}».",
                       "Выберите поле домена из списка полей модели.", "target.domain_field")  # fmt: skip
        label = str(t.label)
        if f["kind"] == CATEGORY:
            if label not in (f.get("labels") or []):
                raise _api(422, "domain_unmapped", f"В поле «{f['name']}» нет значения «{label[:80]}».",
                           "Выберите домен блоков из значений поля.", "target.label")  # fmt: skip
        else:
            try:
                float(label.replace(",", "."))
            except ValueError:
                raise _api(422, "domain_unmapped", f"Поле «{f['name']}» числовое, а домен «{label[:80]}» — не число.",
                           "Выберите числовое значение домена.", "target.label") from None
        t = replace(t, domain_field=str(f["name"]))
    if t.filter_id is not None:
        try:
            fst.rules_of(conn, t.filter_id)
        except ApiError as e:
            raise _api(e.status, e.code, e.message, e.hint, "target.filter_id") from e
    return t


# --- the samples ----------------------------------------------------------------------------------------------------


def _soft_values(raw: tuple[str, ...], kind: str) -> tuple[str, ...]:
    if kind != "number":
        return raw
    out = []
    for i, v in enumerate(raw):
        try:
            out.append(format(float(str(v).replace(",", ".")), ".15g"))
        except ValueError:
            raise _api(422, "bad_param", f"Домен мягкой границы «{str(v)[:40]}» — не число.",
                       "Домен проб числовой: выберите числовые значения.", f"samples.soft.domains.{i}") from None
    return tuple(out)


def canon_samples(conn: sa.Connection, sel: SampleSel) -> tuple[SampleSel, Data]:
    """The samples as the project has them: the ids of the tables as text, the fields in the stored spelling, the
    unit of the field; a refusal of the Э16 resolvers (`table_not_found`, `field_not_found`, …) names `samples.…`."""
    from bf_core.geostat.variogram import spec as vspec

    body: dict[str, Any] = {"set_id": sel.set_id, "table": sel.table, "field": sel.field, "weights": sel.weights,
                            "domain": None if sel.domain is None else {"table": sel.domain.table,
                                                                       "field": sel.domain.field,
                                                                       "values": list(sel.domain.values)}}  # fmt: skip
    try:
        data = vspec.parse_data(conn, body)
        top = sel.topcut
        if top is not None and top[0] == "field":
            top = ("field", vspec._field(conn, data.table_id, str(top[1]), "Поле урезки", "topcut.field", True).name)
    except ApiError as e:
        param = str(e.extra.get("param") or "")
        e.extra = {**e.extra, "param": "samples" + (f".{param}" if param else "")}
        raise
    domain, soft = None, sel.soft
    if data.domain is not None:
        d = data.domain
        domain = DomainRef(str(d.table_id), d.field, d.values)
        if soft is not None:
            soft = (_soft_values(soft[0], d.kind), soft[1])
    weights: str | bool | None = sel.weights if sel.weights in (None, False) else data.weights
    out = replace(sel, table=str(data.table_id), field=data.field, unit=data.unit or sel.unit, domain=domain,
                  soft=soft, topcut=top, weights=weights)  # fmt: skip
    return out, data


@dataclass(frozen=True)
class Scan:
    origin: list[float]  # the centre of the extent of the samples D ∪ S (8.0.1)
    counts: dict[str, int]


def _known_values(project: Any, data: Data) -> set[str]:
    """The values the field of the domain has (text as it is, a number by `format(x, ".15g")`)."""
    import numpy as np

    from bf_core.stats import source

    d = data.domain
    assert d is not None
    cols = source.read(project, data.set_id, d.table_id, [d.field])
    if d.kind == "text":
        return {label.strip() for label in cols.text(d.field)[1]}
    x = np.unique(np.asarray(cols.number(d.field)))
    return {format(float(v), ".15g") for v in x[np.isfinite(x)]}


def scan(project: Any, data: Data, soft: tuple[str, ...] = ()) -> Scan:
    """The samples D (the values of the domain) and S (`soft`) of a table: the counts as Э16 gives them (`rows`
    `missing` `no_position` `outside_domain` `used`; a row is counted by its first reason) and the origin. 422
    `domain_unmapped` — none of the values of the domain is in the table; `no_samples {counts}` — no sample is left."""
    import numpy as np

    from bf_core.geostat.variogram import points
    from bf_core.stats import source

    names = [data.field, *([data.weights] if data.weights else [])]
    d = data.domain
    if d is not None and d.table_id == data.table_id and d.field not in names:
        names.append(d.field)
    cols = source.read(project, data.set_id, data.table_id, names)
    z = np.asarray(cols.number(data.field))
    inside = points._in_domain(project, data, cols)
    near = None
    if soft and d is not None:
        near = points._in_domain(project, replace(data, domain=replace(d, values=tuple(soft))), cols)
    xyz = source.midpoints(project, cols)
    has_value, has_pos = np.isfinite(z), np.isfinite(xyz).all(axis=1)
    ok = has_value & has_pos
    in_d = ok & (inside if inside is not None else True)
    in_s = np.zeros_like(in_d) if near is None else (ok & near & ~in_d)
    counts = {"rows": cols.n + cols.skipped, "missing": int((~has_value).sum()),
              "no_position": cols.skipped + int((has_value & ~has_pos).sum()),
              "outside_domain": int(ok.sum()) - int(in_d.sum()), "used": int(in_d.sum()),
              "soft": int(in_s.sum())}  # fmt: skip
    if counts["used"] == 0:
        if d is not None and not set(d.values) & _known_values(project, data):
            raise _api(422, "domain_unmapped", f"В поле «{d.field}» нет ни одного из выбранных значений домена.",
                       "Выберите значения домена из списка значений поля.", "samples.domain.values")  # fmt: skip
        raise _api(422, "no_samples", "Нет проб для оценки: у проб нет значения, положения или они вне домена.",
                   "Проверьте поле, домен проб и траектории скважин.", "samples", counts=counts)
    pts = xyz[in_d | in_s]
    origin = 0.5 * (pts.min(axis=0) + pts.max(axis=0))
    return Scan([float(v) for v in origin], counts)


# --- the copies of the variogram models -----------------------------------------------------------------------------


def _as_ref(ref: VarioRef, param: str, row: Any) -> VarioRef:
    """The copy of the model (Д-38): `z` — a model of the field itself; `back` — `meta.back.model` of a model in
    normal scores; a model in normal scores without `back` — 422 `variogram_scale`."""
    meta = dict(row.meta or {})
    share: float | None = None
    if meta.get("transform") == "nscore":
        back = meta.get("back")
        back = back if isinstance(back, Mapping) else {}
        if not back.get("model"):
            raise variogram_scale(f"{param}.model_id")
        scale, snapshot = "back", parse_model(back["model"], f"{param}.snapshot")
        got = back.get("share")
        if isinstance(got, int | float) and not isinstance(got, bool):
            share = float(got)
    else:
        scale = "z"
        model = {k: meta.get(k) for k in ("type", "c0", "structures", "common_axes")}
        snapshot = parse_model(model, f"{param}.snapshot")
    return VarioRef(ref.model_id, snapshot, str(meta.get("sha256") or ""), scale, share, ref.t)


def take_variogram(conn: sa.Connection, ref: VarioRef, param: str, keep: VarioRef | None = None) -> VarioRef:
    """The copy of the model `ref.model_id` now (a new version of the model clears «устарел»); a model that is gone
    keeps the copy the row has (`keep`, the same id): the row still counts, «устарел» — 404 `model_not_found`
    otherwise."""
    row = conn.execute(sa.select(objects.c.meta).where(
        objects.c.id == ref.model_id, objects.c.kind == "variogram_model")).first()  # fmt: skip
    if row is None:
        if keep is not None and keep.model_id == ref.model_id and keep.snapshot is not None:
            return replace(keep, t=ref.t)
        raise _api(404, "model_not_found", "Модель вариограммы не найдена.", "Выберите модель из списка.",
                   f"{param}.model_id")  # fmt: skip
    return _as_ref(ref, param, row)


def take_variograms(conn: sa.Connection, spec: EstimationSpec, old: EstimationSpec | None) -> EstimationSpec:
    one = None if spec.variogram is None else take_variogram(
        conn, spec.variogram, "variogram", None if old is None else old.variogram)  # fmt: skip
    many = None
    if spec.variograms is not None:
        prev = None if old is None or old.variograms is None else {v.model_id: v for v in old.variograms}
        many = tuple(take_variogram(conn, v, f"variograms.{i}", None if prev is None else prev.get(v.model_id))
                     for i, v in enumerate(spec.variograms))  # fmt: skip
    return replace(spec, variogram=one, variograms=many)


def trend_form(conn: sa.Connection, trend_id: int) -> dict[str, Any]:
    from bf_core.geostat import trend_store as ts

    try:
        return ts.get(conn, trend_id)
    except ApiError as e:
        raise _api(e.status, e.code, e.message, e.hint, "trend_id") from e


def with_trend(conn: sa.Connection, spec: EstimationSpec) -> EstimationSpec:
    """The search of a row `from: trend` filled in from its trend as it is now, as the job does (`job_plan._row`): a
    stored row reads back with that search not resolved (`spec_parts.parse_search` keeps r1 only). Other rows — as
    they are. The hash of the parameters («устарел») is of the row as stored, not of this one."""
    if spec.search.source == "trend" and spec.trend_id is not None:
        return spec.resolved(trend=trend_form(conn, spec.trend_id))
    return spec


# --- the inputs of the graph and «устарел» --------------------------------------------------------------------------


def _blob(conn: sa.Connection, owner_id: int, role: str) -> str | None:
    q = sa.select(blob_owners.c.sha256).where(
        blob_owners.c.owner_kind == sb.OWNER, blob_owners.c.owner_id == owner_id, blob_owners.c.role == role)
    sha = conn.execute(q).scalar()
    return None if sha is None else str(sha)


def fp_of(conn: sa.Connection, kind: str, ident: Any) -> tuple[str, str] | None:
    """(name, `fp`) of an input as the project has it now; None — it is gone."""
    from bf_core.composites.results import table_token

    if ident is None:
        return None
    if kind == TABLE:
        r = conn.execute(sa.select(dh_tables.c.name, dh_tables.c.created_at, dh_tables.c.rev).where(
            dh_tables.c.id == int(ident), dh_tables.c.state == "ready")).first()  # fmt: skip
        return None if r is None else (str(r.name), f"{table_token(r.created_at)}:{int(r.rev)}")
    if kind == FIELD:
        text = str(ident)
        model, _, name = text.partition(":")
        if not model.isdigit():
            return None
        r = conn.execute(sa.select(objects.c.name, objects.c.meta).where(
            objects.c.id == int(model), objects.c.kind == "block_model")).first()  # fmt: skip
        if r is None:
            return None
        from bf_core.blocks import meta as bmeta

        f = _field_of(bmeta.upgrade(r.meta or {}), name)
        if f is None:
            return None
        file = str(f.get("file") or "cells")
        return f"{r.name} · {f['name']}", f"{'cells' if file == 'cells' else 'fld'}:{_blob(conn, int(model), file)}"
    if kind not in _KINDS or isinstance(ident, bool) or not isinstance(ident, int):
        return None
    r = conn.execute(sa.select(objects.c.name, objects.c.version, objects.c.meta).where(
        objects.c.id == ident, objects.c.kind == _KINDS[kind])).first()  # fmt: skip
    if r is None:
        return None
    if kind == VARIOGRAM:
        return str(r.name), str((r.meta or {}).get("sha256") or "")
    if kind == SET:
        from bf_core.geostat.variogram.store import trajectories_key

        key = trajectories_key(conn, ident)
        return None if key is None else (str(r.name), "traj:" + ":".join(key))
    if kind == BLOCK_MODEL:
        return str(r.name), f"cells:{_blob(conn, ident, 'cells')}"
    return str(r.name), str(int(r.version))


def _rule_fields(conn: sa.Connection, spec: EstimationSpec, meta: Mapping[str, Any]) -> list[str]:
    """The fields of the model the domain and the restrictor read, once each, in their stored spelling."""
    from bf_core.blocks.filters import schema as fs
    from bf_core.blocks.filters import store as fst

    wanted: list[str] = [] if spec.target.domain_field is None else [spec.target.domain_field]
    rules = spec.target.rules
    if spec.target.filter_id is not None:
        try:
            rules = fst.rules_of(conn, spec.target.filter_id)
        except ApiError:
            rules = None
    if rules:
        wanted += fs.fields_of(rules)
    out: list[str] = []
    for w in wanted:
        f = _field_of(meta, w)
        if f is not None and f["name"] not in out:
            out.append(str(f["name"]))
    return out


def inputs_of(conn: sa.Connection, spec: EstimationSpec, samples: Any = None) -> list[dict[str, Any]]:
    """`meta.inputs` of a row (e18-api §2.3): the tables of the samples and of the domain, the set (trajectories),
    the variogram models, the trend, the filter, the block model and its fields of the domain and the restrictor.
    `samples` (`RowSamples` of 18.5) is not needed: the inputs are read from the project."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()

    def add(kind: str, ident: Any) -> None:
        if ident is None or (kind, ident) in seen:
            return
        seen.add((kind, ident))
        got = fp_of(conn, kind, ident)
        if got is not None:
            out.append({"kind": kind, "id": ident, "fp": got[1], "name": got[0]})

    s = spec.samples
    add(TABLE, int(s.table))
    if s.domain is not None and s.domain.table is not None:
        add(TABLE, int(s.domain.table))
    add(SET, s.set_id)
    for v in [*([spec.variogram] if spec.variogram else []), *(spec.variograms or ())]:
        add(VARIOGRAM, v.model_id)
    add(TREND, spec.trend_id)
    add(FILTER, spec.target.filter_id)
    add(BLOCK_MODEL, spec.model_id)
    row = conn.execute(sa.select(objects.c.meta).where(
        objects.c.id == spec.model_id, objects.c.kind == "block_model")).first()  # fmt: skip
    if row is not None:
        from bf_core.blocks import meta as bmeta

        for name in _rule_fields(conn, spec, bmeta.upgrade(row.meta or {})):
            add(FIELD, f"{spec.model_id}:{name}")
    return out


_CHANGED = {TABLE: "изменена таблица «{}»", SET: "изменены траектории", VARIOGRAM: "изменена модель вариограммы «{}»",
            TREND: "изменён тренд «{}»", FILTER: "изменён фильтр «{}»", BLOCK_MODEL: "изменена блочная модель",
            FIELD: "изменено поле «{}» модели"}  # fmt: skip


def freshness(conn: sa.Connection, meta: Mapping[str, Any]) -> dict[str, Any]:
    """`{stale, stale_reason, source_lost}` of a row now: the inputs it recorded against the project, then the
    parameters against `last_run.params`. An input that is gone is «источник удалён» (`source_lost`) — but a variogram
    model: the row keeps its copy, it is only «устарел» (`stale`, the reason names the model)."""
    reason: str | None = None
    for i in meta.get("inputs") or []:
        kind, ident = i.get("kind"), i.get("id")
        got = fp_of(conn, str(kind), ident)
        name = str(i.get("name") or "")
        if got is None:
            if kind == VARIOGRAM:
                reason = reason or f"модель вариограммы «{name}» удалена"
                continue
            return {"stale": True, "stale_reason": LOST, "source_lost": True}
        if reason is None and got[1] != i.get("fp"):
            what = got[0].split(" · ", 1)[-1] if kind == FIELD else got[0]
            reason = _CHANGED.get(str(kind), "изменён вход «{}»").format(what)
    last = meta.get("last_run")
    if reason is None and isinstance(last, Mapping) and last.get("params") is not None:
        try:
            if sb.params_hash(sb.load_spec(meta)) != last["params"]:
                reason = "изменены параметры"
        except ApiError:
            reason = "параметры не читаются"
    return {"stale": reason is not None, "stale_reason": reason, "source_lost": False}


# --- one pass -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    spec: EstimationSpec  # the copies, the ids as text, the search resolved
    origin: list[float]
    inputs: list[dict[str, Any]]
    counts: dict[str, int] | None  # of the scan; None — the samples are those of `old`, not read again


def prepare(conn: sa.Connection, project: Any, spec: EstimationSpec, *, old: EstimationSpec | None = None,
            old_origin: list[float] | None = None) -> Prepared:  # fmt: skip
    """Checks `spec` against the project and completes it: 404 / 422 as the contract names them (e18-api §4). The
    samples are read (counts, origin) for a new row and when `old` has other samples or no origin."""
    _name, mmeta = block_model(conn, spec.model_id)
    spec = replace(spec, target=check_target(conn, spec, mmeta))
    samples, data = canon_samples(conn, spec.samples)
    spec = take_variograms(conn, replace(spec, samples=samples), old)
    form = None if spec.trend_id is None else trend_form(conn, spec.trend_id)
    spec = spec.resolved(trend=form)
    again = old is None or old.samples != spec.samples or old_origin is None
    found = None
    if again:
        soft = () if samples.soft is None else samples.soft[0]
        found = scan(project, data, soft)
    origin = list(found.origin) if found is not None else list(old_origin or [])
    return Prepared(spec, origin, inputs_of(conn, spec), None if found is None else found.counts)
