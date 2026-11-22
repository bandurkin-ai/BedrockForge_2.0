"""Stored variogram models (ТЗ 8.6.4; ADR 0022 §12; подшаг 16.13): `objects(kind="variogram_model", parent_id=NULL)`,
names unique in the project (`name_key`), the experimental points copied into the files of the model — the model
keeps them when the cache is cleared or the data change.

The calculation record — what the jobs `variography.experimental` (16.14) and «Перестроить» leave in the cache of the
project, named by the key of the calculation (SHA-256 of the inputs and the parameters, 64 hex):

    cache/variography/<key>.json            {"v": 1, "result": the form of a calculation (e16-api.md),
                                             "source": {set_id, table, field, domain, weights},
                                             "inputs": {tables [{id, name_key, token, rev}], trajectories},
                                             "calc": {params, sampled}}      — `write_result`, `read_result`
    cache/variography/<key>.nscore.parquet  the table of the normal scores (value, score, weight) — `nscore`

`source` — `spec.Data.canonical()` without `transform` (`source.table`, `source.domain.table`: the id of a table as
text); `inputs` — `points.Points.inputs`. The full sums of the pairs (ADR §12,
Parquet) are the job's own file next to them; the store does not read it.

Files of a model (owner `object`): `vario` — the form of the calculation (JSON, ≤ 256 КБ: what the window draws),
`nscore` — the table of the normal scores (Parquet, only `transform: nscore`). Saving stages the files first, then
one write transaction holds the object and its owners (ADR §12 «Атомарность»): a refusal leaves nothing.

`meta`: {v, source, transform, type, sigma2, c0, structures, common_axes, calc, fit {weights, residual, n, fixed},
back, inputs, exp_key, sha256, algo: "vario-1", versions {numpy, scipy, numba}, created_at, color}.
«Устарел» — `inputs` against the project now, on reading (`freshness`): a table of the model changed (its id, token
or revision; found by id, then by name), the trajectories changed (the key of `drillholes.trajectory`); the set or a
table gone — `source_lost` («Перестроить» unavailable). A model whose ids an archive did not have (`remap`) is lost.

Optimistic locking (ТЗ раздел 17 п. 11): `update` takes the version the caller read; another one now — 409
`version_conflict` {version} (`UPDATE … WHERE version = …`: a write between the reading and the update is caught too).

Errors: 404 `model_not_found`, `result_not_found` {param}; 409 `name_taken` {param: "name"}, `version_conflict`
{version}; 422 `bad_param` {param}.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, dh_tables, objects
from bf_core.storage.blobs import BlobStore, add_owners

KIND = "variogram_model"
OWNER = "object"
VARIO, NSCORE = "vario", "nscore"
ALGO = "vario-1"
CACHE = "variography"
MAX_NAME = 200
JSON_MEDIA = "application/json"
PARQUET_MEDIA = "application/vnd.apache.parquet"
DEFAULT_COLOR = "#1f6bff"  # Cobalt (docs/brand, ADR 0006)
FIT_WEIGHTS = ("n_h", "n", "n_g2")
TRAJECTORY_METHOD = "minimum_curvature"  # the method of `points.load` (`stats.source.midpoints`)
LOST = "источник удалён"
_HEX = set("0123456789abcdef")


def bad(message: str, hint: str, param: str, status: int = 422, code: str = "bad_param") -> ApiError:
    return ApiError(status, code, message, hint, {"param": param})


def not_found() -> ApiError:
    return ApiError(404, "model_not_found", "Модель вариограммы не найдена.", "Обновите список моделей.")


def as_api(e: Exception) -> ApiError:
    """A refusal of a geostatistics kernel (`StatsError`) as an API error: `param` and, of `bad_file`, `line`."""
    if isinstance(e, ApiError):
        return e
    code = str(getattr(e, "code", "bad_param"))
    extra: dict[str, Any] = {}
    if getattr(e, "param", None):
        extra["param"] = e.param  # type: ignore[attr-defined]
    if code == "bad_file" and getattr(e, "line", None) is not None:
        extra["line"] = int(e.line)  # type: ignore[attr-defined]
    return ApiError(422, code, str(getattr(e, "message", e)), str(getattr(e, "hint", "")), extra or None)


# --- the calculation record in the cache ------------------------------------------------------------------------------


def check_key(key: Any, param: str = "exp_key") -> str:
    if not isinstance(key, str) or len(key) != 64 or not set(key) <= _HEX:
        raise bad("Ключ расчёта задан неверно.", "Выполните расчёт вариограммы заново.", param)
    return key


def cache_dir(folder: Path) -> Path:
    return Path(folder) / "cache" / CACHE


def result_path(folder: Path, key: str) -> Path:
    return cache_dir(folder) / f"{check_key(key, 'key')}.json"


def nscore_path(folder: Path, key: str) -> Path:
    return cache_dir(folder) / f"{check_key(key, 'key')}.nscore.parquet"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _plain(x: Any) -> Any:
    """Values JSON holds: a non-finite number is null (a gap is never a 0), numpy values are numbers and lists."""
    if x is None or isinstance(x, bool | str | int):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, Mapping):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_plain(v) for v in x]
    if hasattr(x, "tolist"):
        return _plain(x.tolist())
    raise TypeError(f"a value of {type(x).__name__} cannot be written to JSON")


def _dumps(value: Any) -> bytes:
    return json.dumps(_plain(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def write_result(folder: Path, key: str, result: Mapping[str, Any], *, source: Mapping[str, Any],
                 inputs: Mapping[str, Any], calc: Mapping[str, Any] | None = None,
                 nscore: bytes | None = None) -> Path:  # fmt: skip
    """The record of a calculation under its key (the jobs of 16.14–16.15): written whole or not at all."""
    if calc is None:
        calc = {"params": result.get("params"), "sampled": result.get("sampled")}
    if nscore is not None:
        _write_atomic(nscore_path(folder, key), nscore)
    record = {"v": 1, "result": dict(result), "source": dict(source), "inputs": dict(inputs), "calc": dict(calc)}
    path = result_path(folder, key)
    _write_atomic(path, _dumps(record))
    return path


def read_result(folder: Path, key: Any, param: str = "exp_key") -> dict[str, Any]:
    """The record of the calculation `key`; 404 `result_not_found` {param} — none (the cache was cleared)."""
    path = result_path(folder, check_key(key, param))
    try:
        record = json.loads(path.read_bytes())
    except (OSError, ValueError):
        record = None
    if not isinstance(record, dict) or not isinstance(record.get("result"), dict):
        raise ApiError(404, "result_not_found", "Расчёт вариограммы не найден: кеш проекта очищен.",
                       "Выполните расчёт заново.", {"param": param})  # fmt: skip
    return record


# --- staged files of a model ------------------------------------------------------------------------------------------


@dataclass
class Staged:
    """The files of a model in `blobs/.tmp` before the transaction: (role, (tmp, sha, size), media type)."""

    key: str
    record: dict[str, Any]
    files: list[tuple[str, tuple[Path, str, int], str]] = field(default_factory=list)

    def sha(self, role: str) -> str | None:
        return next((s[1] for r, s, _m in self.files if r == role), None)

    def discard(self) -> None:
        for _role, (tmp, _sha, _size), _media in self.files:
            tmp.unlink(missing_ok=True)


def stage(store: BlobStore, folder: Path, key: Any) -> Staged:
    """The record of `key` and its files staged for a model: the form (`vario`), the normal scores (`nscore`)."""
    record = read_result(folder, key)
    out = Staged(str(key), record)
    out.files.append((VARIO, store.stage_bytes(_dumps(record["result"])), JSON_MEDIA))
    scores = nscore_path(folder, str(key))
    if scores.is_file():
        out.files.append((NSCORE, store.stage_file(scores), PARQUET_MEDIA))
    return out


# --- the fields of a request ------------------------------------------------------------------------------------------


def clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())[:MAX_NAME].rstrip()
    if not text:
        raise ApiError(422, "name_empty", "Название не может быть пустым.", "Введите название модели.",
                       {"param": "name"})  # fmt: skip
    return text


def clean_color(color: Any) -> str:
    if color is None or color == "":
        return DEFAULT_COLOR
    text = color.strip().lower() if isinstance(color, str) else ""
    if len(text) != 7 or text[0] != "#" or not set(text[1:]) <= _HEX:
        raise bad("Цвет модели задан неверно.", "Ожидается цвет вида #1f6bff.", "color")
    return text


def clean_fit(raw: Any) -> dict[str, Any] | None:
    """`fit {weights, residual, n, fixed}` of the window (the answer of `/fit`), or None."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise bad("Сведения о подборе заданы неверно.", "Подберите модель заново.", "fit")
    weights = raw.get("weights", FIT_WEIGHTS[0])
    if weights not in FIT_WEIGHTS:
        raise bad(f"Неизвестное правило весов подбора «{weights}».", "Правила: n_h, n, n_g2.", "fit.weights")
    residual = raw.get("residual")
    if residual is not None:
        if isinstance(residual, bool) or not isinstance(residual, int | float) or not math.isfinite(residual):
            raise bad("Невязка подбора — не число.", "Подберите модель заново.", "fit.residual")
        residual = float(residual)
    n = raw.get("n")
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
        raise bad("Число точек подбора задано неверно.", "Подберите модель заново.", "fit.n")
    fixed = raw.get("fixed") or []
    if not isinstance(fixed, list) or not all(isinstance(p, str) and len(p) <= 64 for p in fixed) or len(fixed) > 64:
        raise bad("Закреплённые параметры заданы неверно.", "Подберите модель заново.", "fit.fixed")
    return {"weights": weights, "residual": residual, "n": n, "fixed": list(fixed)}


def clean_back(raw: Any) -> Any:
    """`back` — the answer of `/back` kept as it is (JSON, ≤ 256 КБ), or None."""
    if raw is None:
        return None
    try:
        size = len(_dumps(raw))
    except (TypeError, ValueError):
        size = -1
    if not isinstance(raw, Mapping) or not 0 <= size <= 1 << 18:
        raise bad("Сведения об обратном преобразовании заданы неверно.", "Выполните обратное преобразование заново.",
                  "back")  # fmt: skip
    return _plain(raw)


# --- objects ----------------------------------------------------------------------------------------------------------


def _taken(conn: sa.Connection, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(objects.c.kind == KIND, objects.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def _refuse_taken(name: str) -> ApiError:
    return ApiError(409, "name_taken", f"Модель «{name}» уже есть.", "Введите другое название.", {"param": "name"})


def free_name(conn: sa.Connection, name: str) -> str:
    candidate, n = name, 1
    while _taken(conn, candidate):
        n += 1
        tail = f" ({n})"
        candidate = name[: MAX_NAME - len(tail)].rstrip() + tail
    return candidate


def default_name(conn: sa.Connection, source: Mapping[str, Any]) -> str:
    """«‹поле› · ‹таблица›»."""
    table = source.get("table")
    tname = None
    if table is not None and str(table).isdigit():
        tname = conn.execute(sa.select(dh_tables.c.name).where(dh_tables.c.id == int(table))).scalar()
    return clean_name(f"{source.get('field') or 'Модель'} · {tname or table or 'таблица'}")


def _versions() -> dict[str, str | None]:
    from importlib import metadata

    out: dict[str, str | None] = {}
    for name in ("numpy", "scipy", "numba"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def _model_fields(kind: str, model_form: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": kind, "c0": model_form["c0"], "structures": model_form["structures"],
            "common_axes": model_form["common_axes"]}  # fmt: skip


def _sha(meta: Mapping[str, Any], vario: str | None, scores: str | None) -> str:
    what = {k: meta.get(k) for k in ("source", "transform", "type", "sigma2", "c0", "structures", "common_axes",
                                     "calc", "fit", "back", "algo")}  # fmt: skip
    return hashlib.sha256(_dumps({**what, "vario": vario, "nscore": scores})).hexdigest()


def _points_meta(staged: Staged) -> dict[str, Any]:
    rec = staged.record
    res = rec["result"]
    return {"source": rec.get("source") or {}, "transform": res.get("transform", "none"), "sigma2": res.get("sigma2"),
            "calc": rec.get("calc") or {}, "inputs": rec.get("inputs") or {}, "exp_key": staged.key}  # fmt: skip


def _own_files(conn: sa.Connection, store: BlobStore, model_id: int, staged: Staged) -> None:
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == model_id))
    rows = []
    for role, files, media in staged.files:
        rows.append((store.place(conn, files, media_type=media), OWNER, model_id, role))
    add_owners(conn, rows)


def create(conn: sa.Connection, store: BlobStore, staged: Staged, model: tuple[str, Mapping[str, Any]], *,
           name: Any = None, color: Any = None, fit: Any = None, back: Any = None,
           author: str | None = None) -> dict[str, Any]:  # fmt: skip
    """A model of the points `staged` (in the caller's write transaction): `{id, name}`. A name given and taken —
    409 `name_taken`; the default name gets « (2)»."""
    if name is None or (isinstance(name, str) and not name.strip()):
        final = free_name(conn, default_name(conn, staged.record.get("source") or {}))
    else:
        final = clean_name(name)
        if _taken(conn, final):
            raise _refuse_taken(final)
    meta: dict[str, Any] = {"v": 1, **_points_meta(staged), **_model_fields(*model), "fit": clean_fit(fit),
                            "back": clean_back(back), "algo": ALGO, "versions": _versions(),
                            "created_at": dialect.utcnow().isoformat(), "color": clean_color(color)}  # fmt: skip
    meta["sha256"] = _sha(meta, staged.sha(VARIO), staged.sha(NSCORE))
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
            version=1, meta=_plain(meta), author=author, created_at=now, updated_at=now,
        )
    )  # fmt: skip
    model_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    _own_files(conn, store, model_id, staged)
    return {"id": model_id, "name": final}


def _row(conn: sa.Connection, model_id: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == model_id, objects.c.kind == KIND)).first()
    if r is None:
        raise not_found()
    return r


def clean_version(raw: Any) -> int:
    """`expected_version` of a `PUT` (required): the version of the model (trend) the window read."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise bad("Не задана версия, которую правит окно.", "Перечитайте объект и повторите сохранение.",
                  "expected_version")  # fmt: skip
    return raw


def conflict(version: int, message: str = "Модель вариограммы изменена в другом окне.",
             hint: str = "Перечитайте модель или сохраните свою как новую.") -> ApiError:  # fmt: skip
    return ApiError(409, "version_conflict", message, hint, {"version": version})


def update(conn: sa.Connection, store: BlobStore, model_id: int, *, expected_version: int, name: Any = None,
           model: tuple[str, Mapping[str, Any]] | None = None, staged: Staged | None = None, color: Any = None,
           fit: Any = None, back: Any = None, has: frozenset[str] = frozenset()) -> dict[str, Any]:  # fmt: skip
    """Rename, replace the model, the points (`staged`), the colour, `fit` / `back` (named in `has`) of the version
    `expected_version`: `{id, name, version}`; another version — 409 `version_conflict`."""
    row = _row(conn, model_id)
    if int(row.version) != expected_version:
        raise conflict(int(row.version))
    meta = dict(row.meta or {})
    final = str(row.name)
    if name is not None:
        final = clean_name(name)
        if _taken(conn, final, but=model_id):
            raise _refuse_taken(final)
    if model is not None:
        meta.update(_model_fields(*model))
    if color is not None:
        meta["color"] = clean_color(color)
    if "fit" in has:
        meta["fit"] = clean_fit(fit)
    if "back" in has:
        meta["back"] = clean_back(back)
    owned = owned_files(conn, model_id)
    if staged is not None:
        meta.update(_points_meta(staged))
        _own_files(conn, store, model_id, staged)
        owned = {role: files[1] for role, files, _media in staged.files}
    meta["sha256"] = _sha(meta, owned.get(VARIO), owned.get(NSCORE))
    n = conn.execute(
        sa.update(objects).where(objects.c.id == model_id, objects.c.version == expected_version).values(
            name=final, name_key=dialect.name_key(final), meta=_plain(meta), version=expected_version + 1,
            updated_at=dialect.utcnow(),
        )
    ).rowcount  # fmt: skip
    if n != 1:
        raise conflict(int(_row(conn, model_id).version))
    return {"id": model_id, "name": final, "version": expected_version + 1}


def delete(conn: sa.Connection, model_id: int) -> None:
    _row(conn, model_id)
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == model_id))
    conn.execute(sa.delete(objects).where(objects.c.id == model_id))


def owned_files(conn: sa.Connection, model_id: int) -> dict[str, str]:
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256)
        .join(blobs, blobs.c.sha256 == blob_owners.c.sha256)
        .where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == model_id)
    ).all()
    return {str(r.role): str(r.sha256) for r in rows}


def nscore_file(conn: sa.Connection, store: BlobStore, model_id: int) -> Path | None:
    """The table of the normal scores of a model (Parquet), or None."""
    sha = owned_files(conn, model_id).get(NSCORE)
    return None if sha is None or not store.exists(sha) else store.path_of(sha)


def _points(conn: sa.Connection, store: BlobStore, model_id: int, meta: Mapping[str, Any]) -> Any:
    sha = owned_files(conn, model_id).get(VARIO)
    if sha is None or not store.exists(sha):
        return None
    form = json.loads(store.read_bytes(sha))
    if isinstance(form, dict):  # the ids of the source as the project has them now (an archive renumbers them)
        src = meta.get("source") or {}
        form = {**form, "set_id": src.get("set_id"), "table": src.get("table")}
    return form


# --- «устарел» --------------------------------------------------------------------------------------------------------


def trajectories_key(conn: sa.Connection, set_id: int) -> list[str] | None:
    """The key of the trajectories of a set as `points.load` records it (`drillholes.trajectory`: the version of the
    set, the sum and the count of the revisions of its tables, the method), without building them; None — no set."""
    version = conn.execute(
        sa.select(objects.c.version).where(objects.c.id == set_id, objects.c.kind == "drillhole_set")
    ).scalar()
    if version is None:
        return None
    revs = conn.execute(
        sa.select(sa.func.coalesce(sa.func.sum(dh_tables.c.rev), 0), sa.func.count()).where(
            dh_tables.c.set_id == set_id
        )
    ).one()
    return [str(int(version)), str(int(revs[0])), str(int(revs[1])), TRAJECTORY_METHOD]


def freshness(conn: sa.Connection, meta: Mapping[str, Any]) -> dict[str, Any]:
    """`{stale, stale_reason, source_lost}` of a model now."""
    from bf_core.stats import source as st

    src = meta.get("source") or {}
    inputs = meta.get("inputs") or {}
    set_id = src.get("set_id")
    lost = {"stale": True, "stale_reason": LOST, "source_lost": True}
    if set_id is None or src.get("table") is None:
        return lost
    try:
        now = st.tables(conn, int(set_id))
    except ApiError:
        return lost
    by_id = {t.id: t for t in now}
    by_key = {t.name_key: t for t in now}
    reason = None
    for t in inputs.get("tables") or []:
        cur = by_id.get(t.get("id")) if t.get("id") is not None else None
        if cur is None or cur.name_key != t.get("name_key"):
            cur = by_key.get(t.get("name_key"))
        if cur is None:
            return lost
        if reason is None and (cur.id != t.get("id") or cur.token != t.get("token") or cur.rev != t.get("rev")):
            reason = f"изменена таблица «{cur.name}»"
    if reason is None and inputs.get("trajectories") and trajectories_key(conn, int(set_id)) != inputs["trajectories"]:
        reason = "изменены траектории"
    return {"stale": reason is not None, "stale_reason": reason, "source_lost": False}


# --- reading ----------------------------------------------------------------------------------------------------------


def _sill(meta: Mapping[str, Any]) -> float | None:
    parts = [meta.get("c0"), *(s.get("c") for s in meta.get("structures") or [])]
    if any(not isinstance(x, int | float) for x in parts):
        return None
    return math.fsum(float(x) for x in parts)  # type: ignore[arg-type]


def _summary(conn: sa.Connection, row: Any) -> dict[str, Any]:
    meta = row.meta or {}
    src = meta.get("source") or {}
    return {"id": int(row.id), "name": row.name, "set_id": src.get("set_id"), "table": src.get("table"),
            "field": src.get("field"), "transform": meta.get("transform"), "type": meta.get("type"),
            "color": meta.get("color", DEFAULT_COLOR), "sill": _sill(meta),
            "structures": len(meta.get("structures") or []),
            **freshness(conn, meta), "created_at": row.created_at, "updated_at": row.updated_at}  # fmt: skip


def list_models(conn: sa.Connection, set_id: int | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id))
    out = [_summary(conn, r) for r in rows]
    return out if set_id is None else [m for m in out if m["set_id"] == set_id]


def get(conn: sa.Connection, store: BlobStore, model_id: int) -> dict[str, Any]:
    row = _row(conn, model_id)
    meta = dict(row.meta or {})
    return {"id": int(row.id), "name": row.name, "color": meta.get("color", DEFAULT_COLOR), "meta": meta,
            "points": _points(conn, store, model_id, meta), **freshness(conn, meta), "version": int(row.version),
            "created_at": row.created_at, "updated_at": row.updated_at}  # fmt: skip


def meta_of(conn: sa.Connection, model_id: int) -> tuple[str, dict[str, Any]]:
    """(name, meta) of a model — the export."""
    row = _row(conn, model_id)
    return str(row.name), dict(row.meta or {})


def versioned(conn: sa.Connection, model_id: int) -> tuple[str, dict[str, Any], int]:
    """(name, meta, version) of a model — «Перестроить» writes over the version it read."""
    row = _row(conn, model_id)
    return str(row.name), dict(row.meta or {}), int(row.version)
