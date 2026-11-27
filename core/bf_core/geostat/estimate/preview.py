"""`POST /estimate/preview` — one block of the estimation in detail, for the wizard (ТЗ 8.8.4; e18-api.md §7): the
values the job would write, the passes, the ellipsoid of the search, the samples of the neighbourhood with the weight
and the reason of every one (`kernel.estimate_block` — the same code as the job; ≤ 100 мс).

    answer = preview(project, body)     # body: {estimation: {…} | estimation_id, at: {row} | {ijk} | {xyz}}

A one-off body is checked as the store checks a row (`store_check`: the fields, the domain, the copy of the variogram
model, the search) but nothing is written. The samples, the tree, the block model and the compiled row are kept for
the next block of the same parameters (the wizard asks again at every click): a small cache whose key holds the
parameters and the fingerprints of every input, so any change of the data is a new entry.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import replace
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore

from . import kernel as kn
from . import memory as mem
from . import samples as sm
from . import store_base as sb
from . import store_check as sc
from .kna_blocks import Blocks
from .spec import EstimationSpec, parse_at, parse_estimation
from .spec_parts import DTYPES

CACHE_MAX = 3
_cache = mem.ByteLru("preview", CACHE_MAX)  # also within the budget of all the caches of the estimation (`memory`)
_BUILD = threading.Lock()  # the builds: one at a time (a click that comes twice builds once; a hit does not wait)


def forget() -> None:
    _cache.clear()


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def light(conn: Any, spec: EstimationSpec) -> EstimationSpec:
    """`store_check.prepare` without the scan of the samples and the inputs: the checked, completed parameters of a
    one-off body (the canonical fields, the copy of the variogram model, the search resolved)."""
    _name, mmeta = sc.block_model(conn, spec.model_id)
    spec = replace(spec, target=sc.check_target(conn, spec, mmeta))
    samples, _data = sc.canon_samples(conn, spec.samples)
    spec = sc.take_variograms(conn, replace(spec, samples=samples), None)
    form = None if spec.trend_id is None else sc.trend_form(conn, spec.trend_id)
    return spec.resolved(trend=form)


def row_of(conn: Any, estimation_id: int) -> tuple[EstimationSpec, np.ndarray | None]:
    """The stored row and its `origin`; 404, 409 `source_lost`."""
    row = sb.stored(conn, sb.KIND, estimation_id)
    meta = dict(row.meta or {})
    if sc.freshness(conn, meta)["source_lost"]:
        raise ApiError(409, "source_lost", "Источник параметров оценки удалён.",
                       "Восстановите таблицу, модель вариограммы или блочную модель либо удалите строку.",
                       {"param": "estimation_id"})  # fmt: skip
    origin = meta.get("origin")
    spec = sc.with_trend(conn, sb.load_spec(meta))  # `from: trend`: the ellipsoid of the trend, as the job takes it
    return spec, (np.asarray(origin, dtype=np.float64) if isinstance(origin, list) and len(origin) == 3 else None)


def _key(project: Any, spec: EstimationSpec, origin: np.ndarray | None, fps: list[str]) -> str:
    text = json.dumps([str(project.uid), spec.hash_params(), None if origin is None else origin.tolist(), fps],
                      sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compiled(project: Any, spec: EstimationSpec, origin: np.ndarray | None,
              fps: list[str]) -> tuple[kn.RowKernel, Blocks]:  # fmt: skip
    key = _key(project, spec, origin, fps)
    hit = _cache.get(key)
    if hit is not None:
        return hit  # type: ignore[no-any-return]
    if not spec.resolved_search:
        raise ApiError(422, "search_unresolved", "Эллипсоид поиска не определён.",
                       "Выберите модель вариограммы или задайте радиусы поиска.", {"param": "search"})  # fmt: skip
    with _BUILD:
        hit = _cache.get(key)
        if hit is not None:
            return hit  # type: ignore[no-any-return]
        s = sm.load(project, spec, origin=origin)
        with dialect.read_tx(project.engine) as conn:
            bl = Blocks.open(conn, BlobStore(project.folder), spec)
        rk = kn.compile_row(spec, s, rotation_deg=bl.rotation, blockcov=None)
        _cache.put(key, (rk, bl), mem.row_bytes(rk, bl))
    return rk, bl


def _f(x: Any) -> float | None:
    v = float(x)
    return v if math.isfinite(v) else None


def _values(spec: EstimationSpec, detail: Any) -> dict[str, Any]:
    """What the job would write for the block, by the keys of the row's outputs (STATUS is `status`)."""
    keys = {c["key"] for c in spec.columns()}
    out: dict[str, Any] = {}
    for key, v in detail.values.items():
        if key == "STATUS" or key not in keys:
            continue
        base = "P" if key[0] == "P" and key[1:].isdigit() else key
        fv = _f(v)
        out[key] = int(fv) if fv is not None and DTYPES.get(base, "f8").startswith("i") else fv
    return out


def _samples(rk: kn.RowKernel, detail: Any) -> list[dict[str, Any]]:
    s, c = rk.samples, detail.candidates
    names = c.names()
    out = []
    for j in range(int(c.idx.shape[0])):
        i = int(c.idx[j])
        out.append({"i": i, "row_id": int(s.row_id[i]), "hole": s.holes[int(s.hole[i])],
                    "from": _f(s.span[i, 0]), "to": _f(s.span[i, 1]), "xyz": [float(v) for v in s.xyz[i]],
                    "z_raw": _f(s.raw[i]), "z": _f(c.z[j]), "h": _f(c.h[j]), "dist": _f(c.dist[j]),
                    "octant": int(c.octant[j]), "lambda": _f(detail.cand_lam[j]), "reason": names[j]})  # fmt: skip
    return out


def answer(spec: EstimationSpec, rk: kn.RowKernel, b: Any, d: Any) -> dict[str, Any]:
    """The form of §7 for the block `b` (`kna_blocks.BlockAt`) and its `BlockDetail` `d`."""
    e = dict(d.ellipsoid)
    e["centre"] = e.pop("centre_xyz")  # the project axes, as the centre of the answer
    return {"at": b.at(), "centre": [float(v) for v in b.centre], "size": [float(v) for v in b.size],
            "label": spec.target.label, "status": int(d.status), "pass": int(d.pass_), "values": _values(spec, d),
            "mu": _f(d.mu), "samples": _samples(rk, d), "ellipsoid": e, "passes": d.passes,
            "origin": [float(v) for v in rk.samples.origin], "counts": dict(d.counts)}  # fmt: skip


def preview(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    have = [k for k in ("estimation", "estimation_id") if body.get(k) is not None]
    if len(have) != 1:
        raise _bad("estimation", "Задайте параметры оценки: тело (estimation) или сохранённые (estimation_id).",
                   "Заполните шаги мастера или выберите строку.")  # fmt: skip
    at = parse_at(body.get("at"))
    with dialect.read_tx(project.engine) as conn:
        if have[0] == "estimation_id":
            ident = body["estimation_id"]
            if isinstance(ident, bool) or not isinstance(ident, int) or ident < 1:
                raise _bad("estimation_id", "Номер параметров оценки — целое число.", "Выберите строку оценки.")
            spec, origin = row_of(conn, ident)
        else:
            spec, origin = light(conn, parse_estimation(body["estimation"])), None
        fps = [str(i["fp"]) for i in sc.inputs_of(conn, spec)]
    rk, bl = _compiled(project, spec, origin, fps)
    b = bl.place(at, "at")
    d = kn.estimate_block(rk, b.centre, b.size, active=b.active)
    return answer(spec, rk, b, d)
