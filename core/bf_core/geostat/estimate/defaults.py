"""`GET /estimate/defaults` — what the wizard of the estimation fills its steps with (ТЗ 8.8.2; Д-38; docs/progress/
e18-api.md §5): the block model (its grid and the fields that can hold a domain), the samples (the counts as Э16 gives
them, the naive and declustered means of the chosen domain and of every value of the field of the domain), the match
of the labels of the blocks with the values of the domain of the samples, the variogram model with its scale and the
warnings, the search it gives, the defaults and the ranges of the form.

    answer = build(project, q)   # q: model_id, set_id, table, field, block_domain?, domain_table?, domain_field?,
                                 # domain_values? (list), weights? (None — DECL_W, "" — none), variogram_id?, method?
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore

from . import samples as sm
from . import spec as es
from . import store_check as sc
from .spec_parts import (
    METHODS,
    DomainRef,
    SampleSel,
    VarioRef,
    search_from_model,
)

DISTINCT_MAX = 64  # the values a numeric field of the model offers as a domain
SHARE_MIN = 0.95  # below it the back transform of a model of normal scores is approximate (Д-38)


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def _num(x: float | None) -> float | None:
    return None if x is None else float(x)


def _means(z: np.ndarray, w: np.ndarray | None) -> dict[str, float | None]:
    """Naive and declustered means; without weights the declustered one is the naive one (8.8.2)."""
    naive = float(np.mean(z)) if z.size else None
    return {"naive": naive, "declus": naive if w is None else sm.declus_mean(z, w)}


def _comma(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def model_part(conn: Any, store: BlobStore, model_id: int) -> tuple[dict[str, Any], Model]:
    name, meta = sc.block_model(conn, model_id)  # 404 block_model_not_found, 422 variable_grid
    model = Model.open(conn, store, model_id)
    g = model.grid
    fields: list[dict[str, Any]] = []
    for f in model.fields:
        if f.get("kind") == "category":
            fields.append({"name": f["name"], "kind": "category", "labels": [str(x) for x in f.get("labels") or []]})
        elif f.get("dtype") in ("i1", "i2", "i4"):
            col = np.asarray(model.columns([str(f["name"])])[str(f["name"])], dtype=np.float64)
            values = np.unique(col[np.isfinite(col)])
            if values.size <= DISTINCT_MAX:
                fields.append({"name": f["name"], "kind": "number", "values": [float(v) for v in values]})
    out = {"id": model_id, "name": name, "version": model.version, "rows": model.rows, "parents": model.parents,
           "sub": bool(model.sub), "regular": True, "rotation": float(g.rotation),
           "size": None if g.size is None else [float(v) for v in g.size], "domain_fields": fields}  # fmt: skip
    del meta
    return out, model


def _variogram(conn: Any, vid: int, sel: SampleSel, method: str) -> tuple[dict[str, Any], VarioRef]:
    from bf_core.geostat.variogram import store as ms

    try:
        ref = sc.take_variogram(conn, VarioRef(vid, None, None, None), "variogram")
    except ApiError as e:
        if str(e.extra.get("param", "")).startswith("variogram"):
            e.extra = {**e.extra, "param": "variogram_id"}
        raise
    if ref.scale == "y" and method != "sgs":  # pragma: no cover - `take_variogram` refuses it first
        raise sc._api(422, "variogram_scale", "Модель построена в нормальных оценках", "", "variogram_id")
    name, meta = ms.meta_of(conn, vid)
    warnings: list[str] = []
    if ref.scale == "back" and ref.share is not None and ref.share < SHARE_MIN:
        warnings.append(
            f"Доля Эрмита {_comma(ref.share)} < {_comma(SHARE_MIN)} — обратное преобразование приближённое.")
    src = meta.get("source") or {}
    if src.get("field") and str(src["field"]).casefold() != sel.field.casefold():
        warnings.append(f"Модель построена по другому полю ({src['field']}).")
    mine = {str(v).strip().casefold() for v in (sel.domain.values if sel.domain else ())}
    theirs = {str(v).strip().casefold() for v in ((src.get("domain") or {}).get("values") or [])}
    if mine and theirs and mine != theirs:
        warnings.append("Модель построена по другому домену.")
    info = {"id": vid, "name": name, "scale": ref.scale, "share": ref.share, "warnings": warnings,
            "snapshot": ref.snapshot, "sha": ref.sha}  # fmt: skip
    return info, ref


def _samples_part(project: Any, sel: SampleSel, chosen: tuple[str, ...]) -> dict[str, Any]:
    """The samples with every value of the field of the domain apart (`load(all_domains=True)` with no D)."""
    probe = replace(sel, domain=None if sel.domain is None else DomainRef(sel.domain.table, sel.domain.field, ()),
                    soft=None)  # fmt: skip
    s = sm.load(project, probe, all_domains=True)
    c = s.counts
    base = {"rows": int(c["rows"]), "missing": int(c["missing"]), "no_position": int(c["no_position"])}
    if sel.domain is None:
        d = np.ones(s.n, dtype=bool)
        counts = {**base, "outside_domain": 0, "used": int(c["selected"])}
        domain = None
    else:
        want = {v.strip() for v in chosen}
        others = {str(k): int(v) for k, v in (c.get("others") or {}).items()}
        d = np.zeros(s.n, dtype=bool)
        values = []
        for k, label in enumerate(s.dom_labels):
            m = s.dom == 2 + k
            d |= m & (label in want)
            values.append({"value": label, "n": int(m.sum()),
                           **_means(s.z[m], None if s.w is None else s.w[m])})  # fmt: skip
        used = sum(n for label, n in others.items() if label in want)
        counts = {**base, "outside_domain": int(c["outside_domain"]) + sum(others.values()) - used, "used": used}
        domain = {"table": sel.domain.table, "field": sel.domain.field, "values": values}
    mean = _means(s.z[d], None if s.w is None else s.w[d])
    return {"counts": counts, "mean": mean, "domain": domain, "weights": s.w is not None}


def build(project: Any, q: dict[str, Any]) -> dict[str, Any]:
    for key, what in (("model_id", "блочная модель"), ("set_id", "набор скважин"), ("table", "таблица проб"),
                      ("field", "поле содержания")):  # fmt: skip
        if q.get(key) in (None, ""):
            raise _bad(key, f"Не выбрана {what}.", "Заполните шаги мастера по порядку.")
    method = q.get("method") or "ok"
    if method not in METHODS:
        raise _bad("method", f"Неизвестный метод «{str(method)[:20]}».", "Методы: nn, idw, sk, ok, ik.")
    store = BlobStore(project.folder)
    values = tuple(str(v).strip() for v in (q.get("domain_values") or []) if str(v).strip())
    weights: str | bool | None = None
    if q.get("weights") is not None:
        weights = False if q["weights"] == "" else str(q["weights"])
    domain = None
    if q.get("domain_field"):
        domain = DomainRef(q.get("domain_table") or None, str(q["domain_field"]),
                           values or ("*",))  # "*": no values yet
    with dialect.read_tx(project.engine) as conn:
        model_info, model = model_part(conn, store, int(q["model_id"]))
        sel, data = sc.canon_samples(conn, SampleSel(int(q["set_id"]), str(q["table"]), str(q["field"]), None,
                                                     domain, None, None, weights))  # fmt: skip
        vario = ref = None
        if q.get("variogram_id") is not None:
            vario, ref = _variogram(conn, int(q["variogram_id"]), sel, method)
    part = _samples_part(project, sel, values)
    mapping = None
    block = q.get("block_domain")
    if block and part["domain"] is not None:
        field = next((f for f in model.fields if str(f["name"]).casefold() == str(block).casefold()), None)
        if field is None:
            raise _bad("block_domain", f"В блочной модели нет поля «{str(block)[:80]}».",
                       "Выберите поле домена из списка полей модели.")  # fmt: skip
        labels = [str(x) for x in field.get("labels") or []] or [format(v, ".15g") for v in
                  next((f.get("values", []) for f in model_info["domain_fields"] if f["name"] == field["name"]), [])]
        found = [v["value"] for v in part["domain"]["values"]]
        mapping = {lab: [v for v in found if v.casefold() == lab.strip().casefold()] for lab in labels}
    search = None
    if ref is not None and ref.snapshot is not None:
        try:
            ang, rad = search_from_model(ref.snapshot, "variogram_id")
            search = {"from": "model", "ang1": ang[0], "ang2": ang[1], "ang3": ang[2], "r1": rad[0], "r2": rad[1],
                      "r3": rad[2]}  # fmt: skip
        except ApiError:
            search = None  # a model without structures: the radii are typed in
    out_domain = part["domain"]
    if out_domain is not None:
        out_domain = {**out_domain, "table": sel.domain.table if sel.domain else None}
    return {"model": model_info,
            "samples": {"set_id": sel.set_id, "table": sel.table, "table_name": data.table_name, "field": sel.field,
                        "unit": sel.unit, "weights": data.weights,
                        "counts": part["counts"], "mean": part["mean"], "domain": out_domain},
            "mapping": mapping, "variogram": vario, "search": search, "params": es.defaults_json(),
            "ranges": es.ranges_json()}  # fmt: skip
