"""/api/v1/assistant — the state, the connection, the login and the usage (подшаг 23.11; ТЗ 7.32; ADR 0029;
docs/progress/e23-api.md §2–§4). The dialogs and the turns — `routes_dialogs.py`. The paths are relative to
`/assistant` (`router.py`):

  * `GET /status`; `PUT /prefs {model?, effort?}` → the status; `GET /usage`.
  * `POST /connection/key {key}` → status (the key is checked with the API BEFORE it is kept: 422 `assistant_bad_key`,
    503 `assistant_offline`); `POST /connection/test`; `DELETE /connection`.
  * Only the shell (a request WITHOUT the header `Origin`, else 403 `shell_only`): `PUT /connection/secret`,
    `GET /connection/secret` (204 - nothing to keep), `POST /connection/sealed {revision}`. The bodies and the answers
    of these routes are never logged, and nothing here echoes a value of a body (`parse_body` keeps it out of errors).
  * `POST /login/start`, `POST /login/{id}/finish {code}`, `DELETE /login/{id}` (204): `ant` blocks for seconds, the
    calls run in the thread pool.

The module is light on purpose: `anthropic`, the loop and the database load with the first request, not with the
router (the core answers `BF-READY` first).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any

from anyio import to_thread
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .errors import bad_param, refuse
from .schemas import KeyBody, LoginFinishBody, PrefsBody, SealedBody, SecretPutBody, parse_body

if TYPE_CHECKING:
    from .service import AssistantService

router = APIRouter()


def get_service(request: Request) -> AssistantService:
    from .service import assistant_of

    return assistant_of(request.app.state.core)


if TYPE_CHECKING:
    Svc = Annotated[AssistantService, Depends(get_service)]
else:  # the annotation must resolve at run time without importing the service
    Svc = Annotated[Any, Depends(get_service)]


def shell_only(request: Request) -> None:
    """The window always sends `Origin: app://bedrockforge`; the main process of the app sends none."""
    if request.headers.get("origin") is not None:
        raise refuse("shell_only")


SHELL = [Depends(shell_only)]


async def read_json(request: Request) -> Any:
    """The JSON body (an empty one is `{}`). A broken one is 422 `bad_param {body}`; the text of the error never
    carries the body (it may hold a key)."""
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise bad_param("body") from None


async def blocking(fn: Any, *args: Any) -> Any:
    """A call that waits for the network or for `ant`: in a thread, not on the event loop."""
    return await to_thread.run_sync(fn, *args)


# --- state, prefs, usage -------------------------------------------------------------------------------------------


@router.get("/status")
def get_status(svc: Svc) -> dict[str, Any]:
    return svc.status()  # type: ignore[no-any-return]


@router.put("/prefs")
async def put_prefs(request: Request, svc: Svc) -> dict[str, Any]:
    from . import prefs

    body = parse_body(PrefsBody, await read_json(request))
    await blocking(prefs.set_prefs, svc.state.data_dir, body.model, body.effort)
    svc.publish_connection()
    return svc.status()  # type: ignore[no-any-return]


@router.get("/usage")
def get_usage(svc: Svc) -> dict[str, Any]:
    from . import usage

    return usage.summary(svc.state.data_dir)


# --- the connection -------------------------------------------------------------------------------------------------


@router.post("/connection/key")
async def post_key(request: Request, svc: Svc) -> dict[str, Any]:
    body = parse_body(KeyBody, await read_json(request))
    await blocking(svc.connection.set_key, body.key)
    return svc.status()  # type: ignore[no-any-return]


@router.post("/connection/test")
async def post_test(svc: Svc) -> dict[str, Any]:
    from .schemas import ConnectionTest

    answer = await blocking(svc.connection.test)
    return ConnectionTest.model_validate(answer).model_dump(mode="json")


@router.delete("/connection")
def delete_connection(svc: Svc) -> dict[str, Any]:
    svc.connection.forget()
    return svc.status()  # type: ignore[no-any-return]


@router.put("/connection/secret", dependencies=SHELL)
async def put_secret(request: Request, svc: Svc) -> dict[str, Any]:
    body = parse_body(SecretPutBody, await read_json(request))
    mask = svc.connection.put_secret(body.kind, body.secret, body.secure_storage)
    return {"ok": True, "kind": body.kind, "mask": mask}


@router.get("/connection/secret", dependencies=SHELL)
def get_secret(svc: Svc) -> Response:
    held = svc.connection.get_secret()
    if held is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    kind, secret, revision = held
    return JSONResponse({"kind": kind, "secret": secret, "revision": revision}, headers={"Cache-Control": "no-store"})


@router.post("/connection/sealed", dependencies=SHELL)
async def post_sealed(request: Request, svc: Svc) -> dict[str, Any]:
    body = parse_body(SealedBody, await read_json(request))
    svc.connection.mark_sealed(body.revision)
    return svc.status()  # type: ignore[no-any-return]


# --- the login with an account ---------------------------------------------------------------------------------------


@router.post("/login/start")
async def post_login_start(svc: Svc) -> dict[str, Any]:
    started = await blocking(svc.login.start)
    return started.model_dump(mode="json")  # type: ignore[no-any-return]


@router.post("/login/{login_id}/finish")
async def post_login_finish(login_id: str, request: Request, svc: Svc) -> dict[str, Any]:
    body = parse_body(LoginFinishBody, await read_json(request))
    await blocking(svc.login.finish, login_id, body.code)
    return svc.status()  # type: ignore[no-any-return]


@router.delete("/login/{login_id}")
async def delete_login(login_id: str, svc: Svc) -> Response:
    await blocking(svc.login.cancel, login_id)
    return Response(status_code=204)
