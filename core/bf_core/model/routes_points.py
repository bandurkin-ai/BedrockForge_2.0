"""`POST /implicit/models/{id}/points/preview` (also `…/points`) — the data points of one group without the solution
(показ в 3D; ТЗ 8.9.2; ADR 0026 «API»; e19-api.md §5; подшаг 19.16).

Body `{group_id | group, limit?, channel?, seq?}`: a stored group by its number, or a group of the form not yet saved
(taken into the model as `with_group` does: the same number replaces, no number — added last). The answer: `{group, n,
truncated, counts {contact, inside, outside, manual, value, merged, conflicts, no_trajectory, no_depth}, notes, points
{x, y, z, f, kind}}` — columns, the first `limit` (200 000 by default, 1–2 000 000) in the canonical order of the
points; `kind` is an index of `spec.POINT_TYPES`. Nothing is written, so it works in a project «только просмотр»; a
synchronous calculation in one of the HEAVY slots, a newer `seq` of its `channel` overtakes it (409 `superseded`).

The points come from `data.load` of the one group (the same code as the build, so a preview is what the build will
interpolate). The last loads are kept in the process (`CACHE`, the key is the group, the data selection, the manual
layers of the group and the fingerprints of the inputs), so the second request for the same data — turning the layer
on and off, a new `limit` — is the selection of the columns only. 404 `model_not_found`, `group_not_found`, 422
`bad_param {param}`, `no_contacts`, `too_many_points`, 409 `no_project`.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Response

from bf_core.db import dialect

from .routes_base import Payload, Svc, body_of, common_of

if TYPE_CHECKING:
    from .data import Points

router = APIRouter()
KEEP, KEEP_POINTS = 4, 1_000_000  # the loads kept, and the points they may hold together
KEEP_ANSWERS, KEEP_BYTES = 8, 48_000_000  # the texts of the answers kept (the same group, the same `limit`), their size
_LOCK = threading.Lock()
CACHE: OrderedDict[str, Points] = OrderedDict()
ANSWERS: OrderedDict[tuple[str, int], bytes] = OrderedDict()


def forget() -> None:
    """Drops the kept loads and answers (the tests; a project that was closed)."""
    with _LOCK:
        CACHE.clear()
        ANSWERS.clear()


def _remember(key: tuple[str, int], text: bytes) -> None:
    with _LOCK:
        ANSWERS[key] = text
        ANSWERS.move_to_end(key)
        while len(ANSWERS) > KEEP_ANSWERS or (len(ANSWERS) > 1 and sum(map(len, ANSWERS.values())) > KEEP_BYTES):
            ANSWERS.popitem(last=False)


def _keep(key: str, pts: Points) -> None:
    with _LOCK:
        CACHE[key] = pts
        CACHE.move_to_end(key)
        while len(CACHE) > KEEP or (len(CACHE) > 1 and sum(p.n for p in CACHE.values()) > KEEP_POINTS):
            CACHE.popitem(last=False)


def _key(project: Any, spec: Any, gid: int, inputs: list[dict[str, Any]]) -> str:
    g = spec.group(gid)
    body = {"uid": getattr(project, "uid", ""), "group": g.canonical(), "gid": gid,
            "data": None if spec.data is None else spec.data.canonical(),
            "manual": [m.canonical() for m in spec.manual if m.group == gid],
            "inputs": [[i["kind"], i["id"], i["fp"]] for i in inputs]}  # fmt: skip
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _resolve(project: Any, model_id: int, req: Any) -> tuple[Any, int, list[dict[str, Any]]]:
    """(the spec the preview reads the data with, the number of the group, the inputs of the model) — 404, 422."""
    from . import store_base as sb
    from . import store_check as sc

    with dialect.read_tx(project.engine) as conn:
        spec = sb.load_spec(dict(sb.stored(conn, model_id).meta or {}))
        if req.group is not None:
            spec = spec.with_group(req.group)
            gid = req.group.id if req.group.id is not None else spec.groups[-1].id
        else:
            spec.group(req.group_id)  # 404 `group_not_found`
            gid = req.group_id
        prep = sc.prepare(conn, spec)
    assert gid is not None
    return prep.spec, int(gid), prep.inputs


def _counts(pts: Points, gid: int) -> dict[str, int]:
    """The points of the group by kind and what the load dropped (e19-api §5, as the code counts them)."""
    from .spec_parts import POINT_TYPES

    row = (pts.counts.get("groups") or {}).get(str(gid), {})
    out = {k: int(row.get(k, 0)) for k in POINT_TYPES}
    out["merged"] = int(row.get("merged", pts.counts.get("merged", 0)))
    out["conflicts"] = int(row.get("conflicts", pts.counts.get("conflicts", 0)))
    out["no_trajectory"] = int(pts.counts.get("no_trajectory", 0))
    out["no_depth"] = int(pts.counts.get("no_depth", 0))
    return out


def _answer(pts: Points, gid: int, limit: int) -> bytes:
    own = pts  # `data.load(groups=[gid])`: every row is the group's
    k = min(limit, own.n)
    cols: dict[str, Any] = {"x": own.xyz[:k, 0], "y": own.xyz[:k, 1], "z": own.xyz[:k, 2], "f": own.f[:k],
                            "kind": own.kind[:k]}  # fmt: skip
    body = {"group": gid, "n": own.n, "truncated": own.n > k, "counts": _counts(pts, gid), "notes": list(pts.notes),
            "points": {name: arr.tolist() for name, arr in cols.items()}}  # fmt: skip
    return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def preview(project: Any, model_id: int, raw: dict[str, Any]) -> bytes:
    """The body of the route: the JSON text of the answer."""
    from . import data as md
    from .spec import parse_preview

    req = parse_preview(raw)
    spec, gid, inputs = _resolve(project, model_id, req)
    group = spec.group(gid)
    if group.type not in ("domain", "shell", "fault"):  # a vein or an expansion has no data points of this kind
        body = {"group": gid, "n": 0, "truncated": False, "counts": _counts(md.Points.empty(), gid),
                "notes": ["У группы этого вида нет точек данных для показа."],
                "points": {k: [] for k in ("x", "y", "z", "f", "kind")}}  # fmt: skip
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    key = _key(project, spec, gid, inputs)
    with _LOCK:
        text = ANSWERS.get((key, req.limit))
        pts = CACHE.get(key)
        if text is not None:
            ANSWERS.move_to_end((key, req.limit))
        if pts is not None:
            CACHE.move_to_end(key)
    if text is not None:
        return text
    if pts is None:
        pts = md.load(project, spec, groups=[gid])
        _keep(key, pts)
    text = _answer(pts, gid, req.limit)
    _remember((key, req.limit), text)
    return text


@router.post("/models/{model_id}/points/preview")
@router.post("/models/{model_id}/points")
def points_preview(svc: Svc, model_id: int, body: Payload) -> Response:
    from bf_core.blocks import viz_spec as sp

    raw = body_of(body)
    project = svc.require_current()
    common = common_of(raw)

    def work(tok: Any) -> bytes:
        with sp.heavy(tok):
            return preview(project, model_id, raw)

    return Response(sp.overtaken(common, work), media_type="application/json")
