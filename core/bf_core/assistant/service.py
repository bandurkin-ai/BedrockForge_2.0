"""The assistant of a running core: the connection, the login, the segments and the dialogs of the open project
(подшаг 23.11; ADR 0029; e23-api.md §1).

    svc = assistant_of(state)       created on the first request (`anthropic` loads then, not at the start of the core)
    svc.connection / svc.login      one per core (a test of the API gets its own with its `CoreState`)
    svc.env()                       the `LoopEnv` of the loop: dialogs of the project open NOW, `on_saved` -> bus
    svc.publish_connection()        `assistant.connection` of the bus (no secret)
    svc.publish_dialog(head)        `assistant.dialogs` of the bus
    svc.shutdown()                  the core goes down: the running segments are asked to stop, a login is closed

The dialogs follow the project: `LiveDialogs` asks the service of the projects for the current one at every use, so a
project opened (or closed) while a dialog goes on takes the next part of it (решение 31, № 130).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .connection import Connection
from .login import LoginManager
from .loop_events import LoopEnv
from .segments import Segments
from .store import Dialogs
from .store_parts import TempDialogs

if TYPE_CHECKING:
    from bf_core.api.app import CoreState

log = logging.getLogger("bf_core.assistant")

KEY = "assistant"
_create_lock = threading.Lock()


class LiveDialogs(Dialogs):
    """`Dialogs` of the project that is open at the moment of the call (none: the temporary dialogs of the core)."""

    def __init__(self, current: Callable[[], Any]) -> None:
        self._current = current
        super().__init__(None, TempDialogs())  # the temporary dialogs live as long as this core

    @property  # type: ignore[override]
    def project(self) -> Any:
        return self._current()

    @project.setter
    def project(self, value: Any) -> None:  # `Dialogs.__init__` assigns it; the live value wins
        return None


class AssistantService:
    """Everything the routes share; closed by the lifespan of the app (`shutdown`)."""

    def __init__(self, state: CoreState) -> None:
        self.state = state
        self.connection = Connection(state.data_dir)
        self.login = LoginManager(self.connection)
        self.segments = Segments()
        self.dialogs = LiveDialogs(self._project)
        self.connection.add_listener(self.publish_connection)

    def _project(self) -> Any:
        """The open project or None; the service of the projects is not started just to ask."""
        projects = self.state.services.get("projects")
        return None if projects is None else projects.current

    def project_uid(self) -> str | None:
        project = self._project()
        return None if project is None else str(project.uid)

    def env(self) -> LoopEnv:
        return LoopEnv(self.connection, self.dialogs, self.segments, on_saved=self.publish_dialog)

    def status(self) -> dict[str, Any]:
        """`Status` of the contract as JSON."""
        return self.connection.status(ant_available=self.login.ant_available()).model_dump(mode="json")

    # --- the bus ---------------------------------------------------------------------------------------------------

    def publish_connection(self) -> None:
        self.state.events.publish("assistant.connection", self.connection.bus_event())

    def publish_dialog(self, head: dict[str, Any], deleted: bool = False) -> None:
        data: dict[str, Any] = {"project_uid": head.get("project_uid"), "id": head["id"]}
        if deleted:
            data["deleted"] = True
        self.state.events.publish("assistant.dialogs", data)

    def shutdown(self) -> None:
        stopped = self.segments.cancel_all()
        self.login.close()
        if stopped:
            log.info("assistant: %d running turn(s) asked to stop", stopped)


def assistant_of(state: CoreState) -> AssistantService:
    service = state.services.get(KEY)
    if service is not None:
        return service  # type: ignore[no-any-return]
    with _create_lock:
        service = state.services.get(KEY)
        if service is None:
            service = state.services[KEY] = AssistantService(state)
    return service  # type: ignore[no-any-return]
