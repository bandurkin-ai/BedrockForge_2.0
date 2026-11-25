"""What the routers of the estimation share (подшаг 18.13; docs/progress/e18-api.md §1): the dependencies, the shape of
a body, the events `geostat.changed`, the synchronous calculation in a HEAVY slot that a newer request of its channel
overtakes (409 `superseded`), and the JSON form of a calculation (NaN → null, numpy → plain). Light: nothing heavy is
imported before it is needed.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Body, Depends, Request

from bf_core.api.errors import ApiError
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[Any, Body()]
EVENT = "geostat.changed"


def body_of(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна оценки.")
    return raw


def publish(request: Request, kind: str, ident: int, deleted: bool = False) -> None:
    data: dict[str, Any] = {"kind": kind, "id": ident}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


def author() -> str | None:
    from bf_core.drillholes.edit import author as who

    return who()


def not_ready(what: str = "Расчёт") -> ApiError:
    return ApiError(422, "not_ready", f"{what} ещё не подключён в этой сборке.", "Обновите программу.")


def registered(kind: str, what: str = "Расчёт") -> None:
    from bf_core.jobs.registry import KINDS

    if kind not in KINDS:
        raise not_ready(what)


def plain(x: Any) -> Any:
    """JSON form of an answer: NaN and ±inf → null (a gap is never 0, 8.0.1), numpy scalars and arrays → Python."""
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [plain(v) for v in x]
    if hasattr(x, "tolist") and not isinstance(x, str | bytes):
        return plain(x.tolist())
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    return x


def common_of(body: Any) -> Any:
    """The `channel` and `seq` of a request (`blocks.viz_spec.parse_common`; 422 `bad_param`)."""
    from bf_core.blocks import viz_spec as sp

    b = body if isinstance(body, dict) else {}
    return sp.parse_common({"channel": b.get("channel"), "seq": b.get("seq")})


def sync(common: Any, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """`fn` in one of the HEAVY slots of the synchronous calculations; a newer request of the channel overtakes this
    one (409 `superseded`)."""
    from bf_core.blocks import viz_spec as sp

    def work(tok: Any) -> dict[str, Any]:
        with sp.heavy(tok):
            return plain(fn())  # type: ignore[no-any-return]

    return sp.overtaken(common, work)  # type: ignore[no-any-return]
