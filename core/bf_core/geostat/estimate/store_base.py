"""The common part of the stores of the estimation rows and plans (подшаг 18.11; ADR 0024 §2; docs/progress/e18-api.md
§2, §4): the kinds, the roles of `object_refs`, the names (unique among the rows, among the plans), the stored
object, the parameters of a stored `meta`. Light: sqlalchemy and the pure parts of the geostatistics only (nothing
heavy before BF-READY).

    row = stored(conn, KIND, id)                  # 404 `estimation_not_found` | `plan_not_found`
    spec = load_spec(meta)                        # EstimationSpec of a stored `meta` (lost ids tolerated)
    name = final_name(conn, KIND, given, default) # given and taken — 409 `name_taken`; default gets « (2)»
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects

if TYPE_CHECKING:
    from .spec import EstimationSpec

KIND = "estimation"  # objects(kind="estimation", parent_id=NULL): «Параметры оценки»
PLAN = "estimation_plan"
ROLE_TREND = "trend"  # object_refs(src=estimation, dst=trend) — the role the trend store lists in «Где используется»
ROLE_FILTER = "filter"  # object_refs(src=estimation, dst=filter) — the role of the filters store
XVAL = "xval"  # the role of the blob of the table of the cross-validation (18.14)
OWNER = "object"
ALGO = "est-1"
MAX_NAME = 200
#: an id that no object has: stands for a reference the archive did not have (`remap`); the row is read, the
#: reference is «источник удалён»
DEAD_ID = 2_000_000_000
LABELS = {KIND: "Параметры оценки", PLAN: "План оценки"}


def not_found(kind: str = KIND, param: str | None = None) -> ApiError:
    extra = {"param": param} if param else None
    if kind == PLAN:
        return ApiError(404, "plan_not_found", "План оценки не найден.", "Обновите список планов.", extra)
    return ApiError(404, "estimation_not_found", "Параметры оценки не найдены.", "Обновите список параметров оценки.",
                    extra)  # fmt: skip


def conflict(version: int, kind: str = KIND) -> ApiError:
    what = "Параметры оценки изменены" if kind == KIND else "План оценки изменён"
    return ApiError(409, "version_conflict", f"{what} в другом окне.",
                    "Перечитайте параметры или сохраните как новые." if kind == KIND
                    else "Перечитайте план или сохраните как новый.", {"version": version})  # fmt: skip


def bad(param: str, message: str, hint: str, code: str = "bad_param", status: int = 422) -> ApiError:
    return ApiError(status, code, message, hint, {"param": param})


def stored(conn: sa.Connection, kind: str, object_id: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == object_id, objects.c.kind == kind)).first()
    if r is None:
        raise not_found(kind)
    return r


# --- names ------------------------------------------------------------------------------------------------------------


def clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())[:MAX_NAME].rstrip()
    if not text:
        raise bad("name", "Название не может быть пустым.", "Введите название.", "name_empty")
    return text


def taken(conn: sa.Connection, kind: str, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(objects.c.kind == kind, objects.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def free_name(conn: sa.Connection, kind: str, name: str) -> str:
    candidate, n = name, 1
    while taken(conn, kind, candidate):
        n += 1
        tail = f" ({n})"
        candidate = name[: MAX_NAME - len(tail)].rstrip() + tail
    return candidate


def final_name(conn: sa.Connection, kind: str, given: Any, default: str, but: int | None = None) -> str:
    """A name given and taken — 409 `name_taken`; none given — the default, made free."""
    if given is None or (isinstance(given, str) and not given.strip()):
        return free_name(conn, kind, clean_name(default))
    name = clean_name(given)
    if taken(conn, kind, name, but):
        raise ApiError(409, "name_taken", f"«{name}» уже есть.", "Введите другое название.", {"param": "name"})
    return name


# --- the parameters of a stored row -----------------------------------------------------------------------------------


def lenient(meta: Mapping[str, Any]) -> dict[str, Any]:
    """The stored `meta` with the ids a `.bfggis` import could not renumber (null) replaced by `DEAD_ID`: the
    parameters parse, the lost reference shows in `freshness` as «источник удалён»."""
    m = dict(meta)
    if m.get("model_id") is None:
        m["model_id"] = DEAD_ID
    samples = dict(m.get("samples") or {})
    if samples.get("set_id") is None:
        samples["set_id"] = DEAD_ID
    if samples.get("table") is None:
        samples["table"] = "0"
    m["samples"] = samples

    def vario(v: Any) -> Any:
        return {**v, "model_id": DEAD_ID} if isinstance(v, Mapping) and v.get("model_id") is None else v

    if m.get("variogram") is not None:
        m["variogram"] = vario(m["variogram"])
    if isinstance(m.get("variograms"), list):
        m["variograms"] = [vario(v) for v in m["variograms"]]
    search = m.get("search")
    if m.get("trend_id") is None and isinstance(search, Mapping) and search.get("from") == "trend":
        m["trend_id"] = DEAD_ID
    return m


def load_spec(meta: Mapping[str, Any]) -> EstimationSpec:
    """The checked parameters of a stored `meta` (it parses as it is: `origin`, `inputs`, `last_run` are skipped)."""
    from .spec import parse_estimation

    return parse_estimation(lenient(meta))


def params_hash(spec: EstimationSpec) -> str:
    """SHA-256 of the parameters without the name (`spec.hash_params`): the job writes it into `last_run.params`, a
    row whose parameters differ from it is «устарел»."""
    text = json.dumps(spec.hash_params(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def without_ids(params: Mapping[str, Any]) -> dict[str, Any]:
    """`spec.hash_params()` without the numbers of the project's objects it names - the block model, the set, the
    tables, the filter and the wireframes of its rules, the trend, the variogram models. A project import renumbers
    them, the objects themselves are among the `inputs` with their own fingerprints (naming another object with other
    contents still changes the state). The fingerprint of the graph (`lineage/adapt_*`) is made of this."""
    from bf_core.blocks.filters.schema import remap_ids

    out = dict(params)
    out.pop("model_id", None)
    out.pop("trend_id", None)
    if isinstance(out.get("samples"), Mapping):
        s = {k: v for k, v in out["samples"].items() if k not in ("set_id", "table")}
        if isinstance(s.get("domain"), Mapping):
            s["domain"] = {k: v for k, v in s["domain"].items() if k != "table"}
        out["samples"] = s
    if isinstance(out.get("target"), Mapping):
        t = {k: v for k, v in out["target"].items() if k != "filter_id"}
        if isinstance(t.get("rules"), Mapping):
            t["rules"] = remap_ids(t["rules"], lambda _i: 0)
        out["target"] = t

    def vario(v: Any) -> Any:
        return {k: x for k, x in v.items() if k != "model_id"} if isinstance(v, Mapping) else v

    if "variogram" in out:
        out["variogram"] = vario(out["variogram"])
    if isinstance(out.get("variograms"), list):
        out["variograms"] = [vario(v) for v in out["variograms"]]
    return out


def graph_fingerprint(params: Mapping[str, Any], inputs: Any) -> str:
    """SHA-256 of the parameters without the ids (`without_ids`) and of the fingerprints of the inputs (kind and `fp`,
    not their ids; sorted): what a row of the graph (`object:estimation`, `object:simulation`) depends on."""
    ins = sorted(f"{i.get('kind')}:{i.get('fp')}" for i in inputs or [] if isinstance(i, Mapping))
    text = json.dumps([without_ids(params), ins], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)  # fmt: skip
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

