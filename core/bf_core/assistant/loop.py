"""The loop of a turn of the assistant: a question or the results of the window -> the events of a segment
(подшаг 23.10; ТЗ 7.32; ADR 0029 решения 5-7, 14, 21-25, 29, № 131, № 133; docs/progress/e23-api.md §8-§10, §12).

The routes (23.11) start a segment and read its events:

    seg = start_turn(env, body)                     `POST /turns`: body = `schemas.TurnBody`; synchronous refusals:
                                                    409 not_connected / busy, 404, 422 (tools, dialog_full)
    seg = start_continue(env, dialog_id, body)      `POST /dialogs/{id}/tool_results`: `schemas.ToolResultsBody`; also
                                                    409 no_pending / pending_mismatch
    for ev in seg.iter(after=0): ...                the answer of the POST (SSE); `None` = `: keepalive`
    cancel_turn(env, dialog_id) -> bool             `POST /dialogs/{id}/cancel` (no segment is not an error)
    env.segments.get(dialog_id)                     `GET /dialogs/{id}/stream?after=<n>`: `seg.iter(after)` or 204
    read_dialog(env, dialog_id) -> dict             `GET /dialogs/{id}`: `Dialog`, `state: running` while a segment runs

The loop runs in a thread of its own and finishes the segment even when nobody reads it (решение 24). One segment:
`turn.start`, then the calls of the API in a cycle: the answer streams (`text.delta`); `data_*` of the answer
run at once and their results go into the next call; `ui_*` stop the segment (`pending`: the window runs them and posts
the results);
the part of the dialog is written at the end (`Dialogs.append`), then `pending` and `turn.end`. At most 25 calls of the
API per question (№ 133, `pending` carries the count over to the continuation).

Failures. A cut stream after its first event: the partial text is kept (`interrupted`), `assistant_interrupted`, no
retry. An answer that was stopped by the user keeps its text (`stopped`); unfinished `tool_use` and
thoughts are dropped.
401, offline, 429, 5xx ... - `Connection.api_error` (§12). A call that fails before any output stores no messages and
leaves an open `pending` as it was, so «Повторить» / the same results can be sent again. The messages of the API are
never changed by the window of the history (`window.trim_messages` makes a copy for the request).
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import anthropic

from bf_core.api.errors import ApiError

from . import data_tools, errors, prefs, prices, prompt, tools, usage, window
from . import store_parts as sp
from .connection import Connection
from .loop_call import CallResult, classify, request_params, stream_message
from .loop_events import (
    EFFORT_CAP,
    LoopEnv,
    Plan,
    Tally,
    assistant_item,
    capped,
    clip,
    error_item,
    last_uses,
    lower_effort,
    merge_items,
    new_id,
    notice_item,
    status_of,
    text_only,
    tool_item,
    tool_result,
    tool_uses,
    user_item,
)
from .schemas import ToolResult, ToolResultsBody, TurnBody
from .segments import Segment
from .store import Loaded, make_title

log = logging.getLogger("bf_core.assistant")

MAX_CALLS = 25  # № 133: calls of the API per question
NO_COMMAND = "Нет такой команды: «{name}» не входит в список команд этого запроса."
EMPTY_OK = "Выполнено"
EMPTY_ERROR = "Ошибка"


# --- the entries ------------------------------------------------------------------------------------------------------


def _resolved(conn: Connection) -> prefs.Resolved:
    environ = getattr(conn, "_environ", None)
    return prefs.resolve(conn.data_dir, environ() if callable(environ) else None)


def _tools_of(ui_tools: list[Any]) -> tuple[list[dict[str, Any]], frozenset[str]]:
    api = tools.build_api_tools(ui_tools, data_tools.definitions())
    return tools.with_cache_control(api), tools.known_ui_names(api)


def _set_of(context: dict[str, Any]) -> int | None:
    value = (context.get("set") or {}).get("id")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def start_turn(env: LoopEnv, body: TurnBody) -> Segment:
    """A question (`dialog_id: null` - a new dialog). The refusals come before any stream; the answer is the
    events of the returned segment."""
    if not env.connection.is_connected():
        raise errors.refuse("assistant_not_connected")
    api_tools, known = _tools_of(list(body.tools))
    context = body.context.model_dump(mode="json")
    r = _resolved(env.connection)
    loaded: Loaded | None = None
    if body.dialog_id is None:
        head = env.dialogs.create(make_title(body.text))
        seg = env.segments.start(head["id"])
    else:
        seg = env.segments.start(body.dialog_id)
        try:
            loaded = env.dialogs.read(body.dialog_id)
            sp.check_room(loaded.meta, 0)
        except BaseException:
            env.segments.release(seg)
            raise
        head = loaded.head
    plan = Plan(
        "question", body.text, [], context, api_tools, known, r.model, r.effort, head, loaded, loaded is None,
        pending=(loaded.meta.get("pending") if loaded else None), set_id=_set_of(context),
    )  # fmt: skip
    return _launch(env, seg, plan)


def start_continue(env: LoopEnv, dialog_id: str, body: ToolResultsBody) -> Segment:
    """The results of the commands of the window for the `pending` of a dialog (§9)."""
    if not env.connection.is_connected():
        raise errors.refuse("assistant_not_connected")
    api_tools, known = _tools_of(list(body.tools))
    seg = env.segments.start(dialog_id)
    try:
        loaded = env.dialogs.read(dialog_id)
        pending = loaded.meta.get("pending")
        if not pending:
            raise errors.refuse("assistant_no_pending")
        expected = [str(t["tool_use_id"]) for t in pending["tools"]]
        if body.pending_id != pending["id"] or sorted(expected) != sorted(r.tool_use_id for r in body.results):
            raise errors.pending_mismatch(expected)
        if not last_uses(loaded.messages):
            raise sp.broken()
        sp.check_room(loaded.meta, 0)
    except BaseException:
        env.segments.release(seg)
        raise
    context = body.context.model_dump(mode="json")
    r = _resolved(env.connection)
    plan = Plan(
        "continue", None, list(body.results), context, api_tools, known, r.model, r.effort, loaded.head, loaded, False,
        pending=pending, set_id=_set_of(context), rounds=int(pending.get("rounds") or 0),
    )  # fmt: skip
    return _launch(env, seg, plan)


def cancel_turn(env: LoopEnv, dialog_id: str) -> bool:
    """`POST …/cancel`: the running segment of the dialog is asked to stop; False - there is none (still 204)."""
    return env.segments.cancel(dialog_id)


def read_dialog(env: LoopEnv, dialog_id: str) -> dict[str, Any]:
    """`GET /dialogs/{id}`: the dialog as the window shows it - a card written twice (`waiting`, then with its result)
    is one item; `state` is `running` while a segment of the dialog runs."""
    dialog = env.dialogs.read(dialog_id, env.segments.busy()).as_dialog()
    dialog["items"] = merge_items(dialog["items"])
    return dialog


def _launch(env: LoopEnv, seg: Segment, plan: Plan) -> Segment:
    turn = _Turn(env, seg, plan)
    try:
        threading.Thread(target=turn.run, name=f"assistant-{seg.id}", daemon=True).start()
    except BaseException:
        env.segments.release(seg)
        raise
    return seg


# --- the worker -------------------------------------------------------------------------------------------------------


@dataclass
class _State:
    state: str = "idle"  # idle | pending | error | cancelled
    stop_reason: str | None = None
    pending: dict[str, Any] | None = None
    consumed: bool = (
        False  # the API answered (or the user stopped it): the messages of the segment belong to the history
    )
    trimmed: bool = False  # the notice `context_trimmed` was sent
    force_trim: bool = False
    effort_noted: bool = False
    models: list[str] = field(default_factory=list)


class _Turn:
    def __init__(self, env: LoopEnv, seg: Segment, plan: Plan) -> None:
        self.env, self.seg, self.plan = env, seg, plan
        self.conn = env.connection
        self.dialog_id: str = plan.head["id"]
        self.history: list[dict[str, Any]] = list(plan.loaded.messages) if plan.loaded else []
        self.base_len = len(self.history)
        self.items: list[dict[str, Any]] = []
        self.held_items: list[dict[str, Any]] = []  # the cards of a closed `pending`: written once the API answered
        self.tally = Tally(plan.loaded.meta if plan.loaded else None, 1 if plan.kind == "question" else 0)
        self.calls = plan.rounds
        self.s = _State()
        self.effort = capped(plan.model, plan.effort)
        self.project = env.dialogs.project
        self.system = prompt.system_blocks()
        self.extra = window.estimate_tokens(prompt.SYSTEM_PROMPT) + tools.tools_size(plan.api_tools)[1]
        self.head = plan.head

    # --- events ---------------------------------------------------------------------------------------------------

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        self.seg.emit(event, data)

    def _notice(self, code: str) -> None:
        item = notice_item(code)
        self.items.append(item)
        self._emit("notice", {"item": item})

    def _fail(self, code: str, message: str | None = None, hint: str | None = None) -> None:
        item = error_item(code, message, hint)
        self.items.append(item)
        self._emit("error", {"item": item})
        self.s.state = "error"

    # --- run ------------------------------------------------------------------------------------------------------

    def run(self) -> None:
        try:
            self._begin()
            self._cycle()
        except ApiError as exc:
            self._fail(exc.code if exc.code in errors.REFUSALS else "internal", exc.message, exc.hint)
        except Exception as exc:  # noqa: BLE001 - a bug must end the segment, not hang the window
            log.error("assistant: the loop failed: %s: %s", type(exc).__name__, self.conn.scrub(str(exc))[:300])
            log.debug("assistant: the loop failed", exc_info=True)
            self._fail("internal")
        finally:
            try:
                self._finish()
            except Exception as exc:  # noqa: BLE001
                log.error("assistant: the end of the segment failed: %s", type(exc).__name__)
                self._emit("turn.end", {"state": "error", "stop_reason": None, "dialog": self.head})
            finally:
                self.env.segments.release(self.seg)

    def _begin(self) -> None:
        p = self.plan
        data: dict[str, Any] = {
            "dialog": {**p.head, "state": "running"},
            "segment_id": self.seg.id,
            "model": p.model,
            "effort": p.effort,
        }
        if p.kind == "question":
            assert p.text is not None
            blocks: list[dict[str, Any]] = []
            if p.pending:  # решение 23, № 131: a question closes a `pending` nobody answered
                blocks += self._result_blocks({}, errors.TEXT_SKIPPED, self.held_items)
            blocks += prompt.user_content(p.text, p.context)
            item = user_item(p.text)
            self.items.append(item)
            data["user_item"] = item
            self.history.append({"role": "user", "content": blocks})
        else:
            by_id = {r.tool_use_id: r for r in p.results}
            blocks = self._result_blocks(by_id, errors.TEXT_SKIPPED, self.held_items)
            self.history.append({"role": "user", "content": blocks})
        self._emit("turn.start", data)
        if p.new_dialog and self.head.get("temporary"):
            self._notice("temporary")

    def _result_blocks(
        self, by_id: dict[str, ToolResult], skipped: str, cards: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """The `tool_result` blocks for the last answer of Claude, in the order of its `tool_use`: the data tools from
        the stored pending, the commands of the window from `by_id` (not there: `skipped`). The cards go to `cards`."""
        pending = self.plan.pending or {}
        stored = {b["tool_use_id"]: b for b in pending.get("data_results") or []}
        ui = {t["tool_use_id"]: t for t in pending.get("tools") or []}
        out: list[dict[str, Any]] = []
        for use in last_uses(self.history):
            tid = use["id"]
            if tid in stored:
                out.append(stored[tid])
                continue
            res = by_id.get(tid)
            ok, text = (res.ok, res.text) if res else (False, skipped)
            out.append(tool_result(tid, text or (EMPTY_OK if ok else EMPTY_ERROR), not ok))
            entry = ui.get(tid)
            if entry is not None:
                cards.append(self._card_of(entry, status_of(ok, text), text, res.title if res else None))
        return out

    def _card_of(self, entry: dict[str, Any], status: str, text: str, title: str | None) -> dict[str, Any]:
        old: dict[str, Any] = next(
            (
                i
                for i in reversed(self.plan.loaded.items if self.plan.loaded else [])
                if i.get("id") == entry["item_id"]
            ),
            {},
        )
        return tool_item(
            item_id=entry["item_id"],
            tool_use_id=entry["tool_use_id"],
            name=entry["name"],
            side="ui",
            title=title or old.get("title") or entry["name"],
            args=entry.get("args") or {},
            status=status,
            summary=text,
            at=old.get("at"),
        )

    # --- the cycle ------------------------------------------------------------------------------------------------

    def _cycle(self) -> None:
        while True:
            if self.seg.cancelled:
                self._stopped(None)
                return
            if self.calls >= MAX_CALLS:
                self._notice("max_rounds")
                self.s.stop_reason, self.s.state = "max_rounds", "idle"
                self._consume()
                return
            res = self._call()
            if res is None:
                return
            if not self._handle(res):
                return

    def _consume(self) -> None:
        if not self.s.consumed:
            self.s.consumed = True
            self.items.extend(self.held_items)

    def _call(self) -> CallResult | None:
        """One call of the API, with the retries of the depth and of the window; None - the segment ends in an error."""
        ctx_retried = False
        while True:
            self.calls += 1
            try:
                client = self.conn.client()
            except ApiError as exc:
                self._fail(exc.code, exc.message, exc.hint)
                return None
            windowed = window.trim_messages(self.history, extra_tokens=self.extra, force=self.s.force_trim)
            if windowed.changed and not self.s.trimmed:
                self.s.trimmed = True
                self._notice("context_trimmed")
            if self.effort != self.plan.effort and not self.s.effort_noted:
                self.s.effort_noted = True
                self._notice("effort_lowered")
            params = request_params(
                model=self.plan.model, effort=self.effort, system=self.system, tools=self.plan.api_tools,
                messages=windowed.messages,
            )  # fmt: skip
            item_id = new_id("a")
            res = stream_message(client, params, self.seg, item_id, self.plan.model)
            self.item_id = item_id
            if res.error is None and not res.cancelled:
                self.conn.note_call(None)
                if window.is_context_exceeded(stop_reason=res.stop_reason):
                    self._record(res)  # the call was billed, whatever comes next
                    self._partial_item(res, interrupted=True)
                    if not ctx_retried and self._shrink():
                        ctx_retried = True
                        continue
                    self._fail("assistant_api_error")
                    return None
                return res
            kind = classify(res)
            if kind == "cancelled" or kind == "interrupted":
                return res
            failure = res.error
            assert failure is not None
            self.conn.note_call(failure)
            lower = lower_effort(self.effort) if self._is_effort_refusal(failure) else None
            if lower is not None:
                self.effort = EFFORT_CAP[self.plan.model] = lower
                continue
            if window.is_context_exceeded(error=failure) and not ctx_retried and self._shrink():
                ctx_retried = True
                continue
            self._api_failure(failure)
            return None

    @staticmethod
    def _is_effort_refusal(exc: BaseException) -> bool:
        if not isinstance(exc, anthropic.BadRequestError):
            return False
        return "effort" in str(getattr(exc, "message", exc)).lower()

    def _shrink(self) -> bool:
        """After «the context does not fit»: shorten the old results, thoughts and screens at once (once per call)."""
        shrunk = window.shrink_for_retry(self.history, extra_tokens=self.extra)
        if shrunk is None:
            return False
        self.s.force_trim = True
        if not self.s.trimmed:
            self.s.trimmed = True
            self._notice("context_trimmed")
        return True

    def _api_failure(self, exc: BaseException) -> None:
        err = self.conn.api_error(exc, self.plan.model)
        if err.extra:
            log.warning("assistant: the API refused the request: %s", err.extra)
        self._fail(err.code, err.message, err.hint)

    # --- an answer ------------------------------------------------------------------------------------------------

    def _partial_item(self, res: CallResult, *, stopped: bool = False, interrupted: bool = False) -> None:
        if res.text:
            self.items.append(
                assistant_item(self.item_id, res.model, res.text, stopped=stopped, interrupted=interrupted)
            )

    def _record(self, res: CallResult) -> None:
        """The cost of the call: the usage file, the totals, the event `usage`."""
        u = res.usage
        if not any(u.values()):
            return
        cost = prices.cost(res.model, u["input"], u["output"], u["cache_read"], u["cache_write"])
        long = prices.is_long(res.model, u["input"], u["cache_read"], u["cache_write"])
        try:
            usage.record(
                self.conn.data_dir,
                model=res.model,
                input=u["input"],
                output=u["output"],
                cache_read=u["cache_read"],
                cache_write=u["cache_write"],
                dialog_uid=self.head.get("uid"),
                project_uid=getattr(self.project, "uid", None),
            )
        except (OSError, ValueError):  # a lost line of the account must not break the turn
            log.warning("assistant: the line of usage was not written", exc_info=True)
        self.tally.add(u, cost)
        if res.model not in self.s.models:
            self.s.models.append(res.model)
        call = {"model": res.model, **u, "cost_usd": cost, "long": long}
        self._emit("usage", {"call": call, "dialog": self.tally.dialog()})

    def _stopped(self, res: CallResult | None) -> None:
        """The user stopped the segment: the partial text stays, the rest of the answer goes (решение 7)."""
        self.s.state = "cancelled"
        if res is not None:
            self._record(res)
            self._partial_item(res, stopped=True)
            if res.text:
                self.history.append({"role": "assistant", "content": [{"type": "text", "text": res.text}]})
        self._consume()

    def _handle(self, res: CallResult) -> bool:
        """True - call the API again (the results of the data tools are in the history)."""
        kind = classify(res) if (res.cancelled or res.error is not None) else "ok"
        if kind == "cancelled":
            self._stopped(res)
            return False
        if kind == "interrupted":
            self._record(res)
            if res.text:
                self._partial_item(res, interrupted=True)
                self.history.append({"role": "assistant", "content": [{"type": "text", "text": res.text}]})
                self._consume()
            self._fail("assistant_interrupted")
            return False
        self._record(res)
        self._consume()
        stop = res.stop_reason
        self.s.stop_reason = stop
        if res.text:
            self.items.append(assistant_item(self.item_id, res.model, res.text))
        if stop == "tool_use" and tool_uses(res.content):
            return self._tool_round(res)
        if stop == "pause_turn":
            self.history.append({"role": "assistant", "content": res.content})
            return True
        keep = res.content if stop in ("end_turn", "stop_sequence", None) else text_only(res.content)
        if keep:
            self.history.append({"role": "assistant", "content": keep})
        if stop == "max_tokens":
            self._notice("max_tokens")
        elif stop == "refusal":
            self._notice("refusal")
        self.s.state = "idle"
        return False

    def _tool_round(self, res: CallResult) -> bool:
        uses = tool_uses(res.content)
        done: dict[str, dict[str, Any]] = {}  # tool_use_id -> tool_result block, for the calls answered here
        ui_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (the call, its card)
        for use in uses:
            if self.seg.cancelled:  # the answer is dropped but for its text; no card of a command of the window stays
                self.s.state = "cancelled"
                if res.text:
                    self.history.append({"role": "assistant", "content": text_only(res.content)})
                return False
            name, tid, args = str(use["name"]), str(use["id"]), use.get("input") or {}
            if name.startswith("ui_") and name in self.plan.known_ui:
                card = tool_item(
                    item_id=new_id("c"), tool_use_id=tid, name=name, side="ui", title=name, args=args, status="waiting"
                )
                ui_calls.append((use, card))
            elif name.startswith("ui_"):
                text = NO_COMMAND.format(name=name[:80])
                card = tool_item(
                    item_id=new_id("c"), tool_use_id=tid, name=name, side="ui", title=name, args=args, status="error",
                    summary=text,
                )  # fmt: skip
                self.items.append(card)
                self._emit("tool.start", {"item": card})
                done[tid] = tool_result(tid, text, True)
            else:
                done[tid] = self._run_data(name, tid, args)
        self.items.extend(card for _, card in ui_calls)
        self.history.append({"role": "assistant", "content": res.content})
        if ui_calls:
            self.s.pending = {
                "id": "p-" + uuid.uuid4().hex[:16],
                "created_at": sp.stamp(),
                "tools": [
                    {
                        "tool_use_id": u["id"],
                        "name": u["name"],
                        "command": None,
                        "args": u.get("input") or {},
                        "item_id": c["id"],
                    }
                    for u, c in ui_calls
                ],
                "data_results": list(done.values()),
                "rounds": self.calls,
            }
            self.s.state = "pending"
            return False
        self.history.append({"role": "user", "content": [done[str(u["id"])] for u in uses]})
        return True

    def _run_data(self, name: str, tid: str, args: dict[str, Any]) -> dict[str, Any]:
        card = tool_item(
            item_id=new_id("c"), tool_use_id=tid, name=name, side="data", title=data_tools.title_of(name), args=args,
            status="waiting",
        )  # fmt: skip
        self.items.append(card)
        self._emit("tool.start", {"item": dict(card)})
        out = data_tools.run(name, args, project=self.project, set_id=self.plan.set_id)
        status = "error" if out.is_error else "ok"
        card.update(status=status, summary=clip(out.summary))
        self._emit("tool.end", {"item_id": card["id"], "status": status, "summary": card["summary"]})
        return tool_result(tid, out.text, out.is_error)

    # --- the end --------------------------------------------------------------------------------------------------

    def _finish(self) -> None:
        s, p = self.s, self.plan
        pending = s.pending
        messages = self.history[self.base_len :]
        meta_state = "error" if s.state == "error" else "idle"
        if not s.consumed and s.state == "error":  # nothing was answered: the history stays as it was, a retry is clean
            messages = []
            pending = p.pending
            meta_state = "error"
            self.items = [i for i in self.items if i["kind"] != "tool"]
        try:
            head = self.env.dialogs.append(
                self.dialog_id, messages, self.items,
                questions=1 if p.kind == "question" else 0,
                usage=self.tally.segment_usage(), models=s.models, prices_version=prices.PRICES_VERSION,
                state=meta_state, pending=pending,
            )  # fmt: skip
        except ApiError as exc:
            log.warning("assistant: the part of the dialog was not written: %s", exc.code)
            self._fail(exc.code if exc.code in errors.STREAM_ERRORS else "internal", exc.message, exc.hint)
            s.state, s.pending = "error", None
            head = self.head
        else:
            head = self._migrate(head)
            self._saved(head)
        self.head = head
        if s.state == "pending" and s.pending is not None:
            self._emit("pending", sp.public_pending({"pending": s.pending}) or {})
        self._emit("turn.end", {"state": s.state, "stop_reason": s.stop_reason, "dialog": head})

    def _migrate(self, head: dict[str, Any]) -> dict[str, Any]:
        """A temporary dialog whose project was opened meanwhile goes into it (решение 31, № 130)."""
        dialogs = self.env.dialogs
        if not (head.get("temporary") and dialogs.persistent):
            return head
        try:
            moved = dialogs.copy_to(self.dialog_id, dialogs)
        except ApiError:
            log.warning("assistant: a temporary dialog was not moved into the project", exc_info=True)
            return head
        self.env.segments.rekey(self.seg, moved["id"])
        self.dialog_id = moved["id"]
        return moved

    def _saved(self, head: dict[str, Any]) -> None:
        if self.env.on_saved is not None:
            try:
                self.env.on_saved(head)
            except Exception:  # noqa: BLE001 - the bus must not break the end of a segment
                log.warning("assistant: on_saved failed", exc_info=True)
