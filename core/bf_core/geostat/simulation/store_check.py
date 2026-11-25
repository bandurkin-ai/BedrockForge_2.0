"""What the store of the simulations checks and records against the project (подшаг 18.32; ADR 0024 §2, §9; ADR 0025;
docs/progress/e18-api.md §2.3, §9): the block model (a variable grid is 422 `variable_grid`, вопрос № 83), the domain of
the blocks, the samples, the copy of the variogram model in normal scores (Д-38: `scale: y`), the inputs of the graph
and the state of a stored row (`freshness`). The checks of the estimation (`estimate/store_check.py`) do the common
work — a simulation has the same samples, target, variogram reference and inputs.

    prep = prepare(conn, project, spec, old=None, old_origin=None)      # Prepared(spec, origin, inputs, counts)
    spec, data = check(conn, spec, old=None)                            # the project checks, the samples not read
    inputs_of(conn, spec) -> [{kind, id, fp, name}]
    freshness(conn, meta) -> {stale, stale_reason, source_lost}
    params_hash(spec) -> str      load_spec(meta) -> SimulationSpec

Nothing heavy before BF-READY: numpy and the readers are imported in the functions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import objects
from bf_core.geostat.estimate import store_base as sb
from bf_core.geostat.estimate import store_check as sc
from bf_core.geostat.estimate.spec_parts import VarioRef, parse_model

from .spec import SimulationSpec, parse_simulation

if TYPE_CHECKING:
    from bf_core.geostat.variogram.spec import Data

KIND = "simulation"
LINEAGE_KIND = "object:simulation"
OWNER = "object"
ROLE = "real"


def not_found(param: str | None = None) -> ApiError:
    extra = {"param": param} if param else None
    return ApiError(404, "simulation_not_found", "Моделирование не найдено.", "Обновите список моделирований.", extra)


def conflict(version: int) -> ApiError:
    return ApiError(409, "version_conflict", "Параметры моделирования изменены в другом окне.",
                    "Перечитайте параметры или сохраните как новые.", {"version": version})  # fmt: skip


def stored(conn: sa.Connection, simulation_id: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == simulation_id, objects.c.kind == KIND)).first()
    if r is None:
        raise not_found()
    return r


# --- the parameters of a stored row -------------------------------------------------------------------------------


def load_spec(meta: Mapping[str, Any]) -> SimulationSpec:
    """The checked parameters of a stored `meta` (it parses as it is: `origin`, `inputs`, `last_run` are skipped);
    the ids a `.bfggis` import could not renumber (null) stand for rows that no object has."""
    return parse_simulation(sb.lenient(meta))


def params_hash(spec: SimulationSpec) -> str:
    """SHA-256 of the parameters without the name: the job writes it into `last_run.params`; a row whose parameters
    differ from it is «устарел»."""
    text = json.dumps(spec.hash_params(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def graph_fingerprint(meta: Mapping[str, Any]) -> str:
    """The fingerprint of the node `object:simulation` (ADR 0025): the parameters without the ids of the objects they
    name (a project import renumbers those ids; the objects themselves are in `inputs` with their own fingerprints, so
    naming another object with other contents still changes it) and the inputs' fingerprints."""
    return sb.graph_fingerprint(load_spec(meta).hash_params(), meta.get("inputs"))


# --- the block model, the variogram model ----------------------------------------------------------------------------


def block_model(conn: sa.Connection, model_id: int) -> tuple[str, dict[str, Any]]:
    """(name, meta v2) of a ready block model; 404 `block_model_not_found`, 422 `variable_grid` (№ 83)."""
    try:
        return sc.block_model(conn, model_id)
    except ApiError as e:
        if e.code != "variable_grid":
            raise
        raise ApiError(422, "variable_grid", "Модель с переменным шагом сетки: моделирование не поддерживается.",
                       "Приведите модель к регулярной сетке («Регуляризация») и повторите.",
                       {"param": "model_id"}) from e  # fmt: skip


def scale_error(param: str) -> ApiError:
    """Д-38: the simulation takes a model in normal scores only."""
    return ApiError(422, "variogram_scale", "Для моделирования нужна модель вариограммы в нормальных оценках.",
                    "Постройте вариограмму нормальных оценок и сохраните модель.", {"param": param})  # fmt: skip


def take_variogram(conn: sa.Connection, ref: VarioRef, param: str, keep: VarioRef | None = None) -> VarioRef:
    """The copy of the model `ref.model_id` now (a new version of the model clears «устарел»); a model that is gone
    keeps the copy the row has (`keep`, the same id) — 404 `model_not_found` otherwise; a model in the units of the
    field — 422 `variogram_scale`."""
    row = conn.execute(sa.select(objects.c.meta).where(
        objects.c.id == ref.model_id, objects.c.kind == "variogram_model")).first()  # fmt: skip
    if row is None:
        if keep is not None and keep.model_id == ref.model_id and keep.snapshot is not None:
            return keep
        raise ApiError(404, "model_not_found", "Модель вариограммы не найдена.", "Выберите модель из списка.",
                       {"param": f"{param}.model_id"})  # fmt: skip
    meta = dict(row.meta or {})
    if meta.get("transform") != "nscore":
        raise scale_error(f"{param}.model_id")
    model = {k: meta.get(k) for k in ("type", "c0", "structures", "common_axes")}
    return VarioRef(ref.model_id, parse_model(model, f"{param}.snapshot"), str(meta.get("sha256") or ""), "y", None)


def check(conn: sa.Connection, spec: SimulationSpec, old: SimulationSpec | None = None) -> tuple[SimulationSpec, Data]:
    """The spec against the project, without reading the samples: 404 / 422 as the contract names them (e18-api §4,
    §9). The samples as the project has them (ids as text, stored spelling), the target, the copy of the model."""
    _name, mmeta = block_model(conn, spec.model_id)
    spec = replace(spec, target=sc.check_target(conn, spec, mmeta))  # type: ignore[arg-type]  # `.target` only
    samples, data = sc.canon_samples(conn, spec.samples)
    if spec.kind == "sis":  # the indicator models are in the units of the field (scale z; `back` of an nscore one)
        spec = sc.take_variograms(conn, replace(spec, samples=samples), old)  # type: ignore[arg-type,assignment]
    else:
        assert spec.variogram is not None
        ref = take_variogram(conn, spec.variogram, "variogram", None if old is None else old.variogram)
        spec = replace(spec, samples=samples, variogram=ref)
    if spec.secondary is not None:
        spec = replace(spec, secondary=replace(spec.secondary, field=secondary_field(mmeta, spec.secondary.field)))
    return spec.resolved(), data


def secondary_field(mmeta: Mapping[str, Any], name: str) -> str:
    """The secondary variable of the co-SGS as the model stores it: a numeric field of the block model (a category
    field is no variable to correlate with): 422 `field_not_found`, `field_not_number` {param: secondary.field}."""
    f = sc._field_of(mmeta, name)  # noqa: SLF001
    if f is None:
        raise ApiError(422, "field_not_found", f"В блочной модели нет поля «{name[:80]}».",
                       "Выберите числовое поле модели, например плотность.", {"param": "secondary.field"})
    if f.get("kind") != "number":
        raise ApiError(422, "field_not_number", f"Поле «{f['name']}» — категориальное.",
                       "Выберите числовое поле модели, например плотность.", {"param": "secondary.field"})
    return str(f["name"])


# --- the inputs of the graph and «устарел» --------------------------------------------------------------------------


def inputs_of(conn: sa.Connection, spec: SimulationSpec) -> list[dict[str, Any]]:
    """`meta.inputs` (e18-api §2.3): the tables of the samples and of the domain, the set (trajectories), the variogram
    model, the block model and its fields of the domain and the restrictor."""
    out = sc.inputs_of(conn, spec)  # type: ignore[arg-type]  # the fields it reads are those of a simulation too
    if spec.secondary is not None:  # the secondary field of the model: a new content of it makes the row «устарел»
        ident = f"{spec.model_id}:{spec.secondary.field}"
        got = sc.fp_of(conn, sc.FIELD, ident)
        if got is not None and not any(i["kind"] == sc.FIELD and i["id"] == ident for i in out):
            out.append({"kind": sc.FIELD, "id": ident, "fp": got[1], "name": got[0]})
    return out


def freshness(conn: sa.Connection, meta: Mapping[str, Any]) -> dict[str, Any]:
    """`{stale, stale_reason, source_lost}` of a row now: the inputs it recorded against the project, then the
    parameters against `last_run.params`. A variogram model that is gone — the row keeps its copy: «устарел» only."""
    reason: str | None = None
    for i in meta.get("inputs") or []:
        kind, ident = i.get("kind"), i.get("id")
        got = sc.fp_of(conn, str(kind), ident)
        name = str(i.get("name") or "")
        if got is None:
            if kind == sc.VARIOGRAM:
                reason = reason or f"модель вариограммы «{name}» удалена"
                continue
            return {"stale": True, "stale_reason": sc.LOST, "source_lost": True}
        if reason is None and got[1] != i.get("fp"):
            what = got[0].split(" · ", 1)[-1] if kind == sc.FIELD else got[0]
            reason = sc._CHANGED.get(str(kind), "изменён вход «{}»").format(what)  # noqa: SLF001
    last = meta.get("last_run")
    if reason is None and isinstance(last, Mapping) and last.get("params") is not None:
        try:
            if params_hash(load_spec(meta)) != last["params"]:
                reason = "изменены параметры"
        except ApiError:
            reason = "параметры не читаются"
    return {"stale": reason is not None, "stale_reason": reason, "source_lost": False}


# --- one pass -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    spec: SimulationSpec  # the copy, the ids as text, the search resolved
    origin: list[float]
    inputs: list[dict[str, Any]]
    counts: dict[str, int] | None  # of the scan; None — the samples are those of `old`, not read again


def prepare(conn: sa.Connection, project: Any, spec: SimulationSpec, *, old: SimulationSpec | None = None,
            old_origin: list[float] | None = None) -> Prepared:  # fmt: skip
    """Checks `spec` against the project and completes it. The samples are read (counts, origin) for a new row and
    when `old` has other samples or no origin; 422 `no_samples {counts}` / `domain_unmapped`."""
    spec, data = check(conn, spec, old)
    again = old is None or old.samples != spec.samples or old_origin is None
    found = None
    if again:
        soft = () if spec.samples.soft is None else spec.samples.soft[0]
        found = sc.scan(project, data, soft)
    origin = list(found.origin) if found is not None else list(old_origin or [])
    return Prepared(spec, origin, inputs_of(conn, spec), None if found is None else found.counts)
