"""What the routes of `/api/v1/stats` share in reading a body (13.36): the refusal of a wrong setting and the keys
every chart has. The bodies are untrusted: a wrong key is 422 `bad_param` {param} with a Russian text.

    bad(param, message, hint) -> ApiError          422 `bad_param` {param}
    stats_error(e) -> ApiError                     a refusal of a kernel (`weighted.StatsError`): 422 with its code
    set_id_of(settings, key="set_id") -> int       the set of drillholes (a positive id), else `bad_param`
    name_of(settings, key) -> str | None           a name (of a table, a field); "" and null — none
    holes_of(settings, key="holes") -> list[str]   the names of holes (`charts.kinds.check` made it a list)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bf_core.api.errors import ApiError


def bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, extra={"param": param})


def stats_error(e: Any) -> ApiError:
    return ApiError(422, e.code, e.message, e.hint, extra={"param": e.param} if e.param else None)


def set_id_of(settings: Mapping[str, Any], key: str = "set_id") -> int:
    from bf_core.charts.kinds import is_id

    v: Any = settings.get(key)
    if not is_id(v):
        raise bad(key, "Не выбран набор скважин.", "Выберите набор скважин графика.")
    return int(v)


def name_of(settings: Mapping[str, Any], key: str) -> str | None:
    v = settings.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise bad(key, f"«{key}» задаётся названием.", "Выберите значение из списка.")
    return v


def holes_of(settings: Mapping[str, Any], key: str = "holes") -> list[str]:
    v = settings.get(key)
    return [] if v is None else list(v)
