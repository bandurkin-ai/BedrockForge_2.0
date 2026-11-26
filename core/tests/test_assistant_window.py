"""The window of the history of a dialog (подшаг 23.9; ADR 0029 decision 29): the estimate is characters / 3; over the
limit the old `tool_result`s become «[результат сокращён]», then the old thinking goes, then the old `<workspace>`s; the
current cycle (after the last question of the user) is never touched, the input is never modified, the pairing of
`tool_use` and `tool_result` stays; `model_context_window_exceeded` is recognised and shrinks once more."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from bf_core.assistant import window as w

BIG = "я" * 3000  # 1000 tokens


def question(text: str = "Вопрос", ws: str = "<workspace>{}</workspace>") -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": ws}, {"type": "text", "text": text}]}


def call(n: int, name: str = "data_tree") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "думаю " * 50, "signature": "sig"},
            {"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": {}},
        ],
    }


def result(n: int, text: str = BIG, is_error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": text}
    if is_error:
        block["is_error"] = True
    return {"role": "user", "content": [block]}


def answer(text: str = "Ответ") -> dict[str, Any]:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def history(cycles: int, *, current: bool = True) -> list[dict[str, Any]]:
    """`cycles` finished questions (question, call, result, answer) and, if `current`, a running cycle of a question, a
    call with thinking and its result."""
    msgs: list[dict[str, Any]] = []
    for i in range(cycles):
        msgs += [question(f"Старый {i}"), call(i), result(i), answer()]
    if current:
        n = cycles
        msgs += [question("Текущий"), call(n), result(n, BIG + "!")]
    return msgs


def results_of(msgs: list[dict[str, Any]]) -> list[str]:
    return [
        b["content"] for m in msgs if isinstance(m["content"], list) for b in m["content"] if b["type"] == "tool_result"
    ]


# --- the estimate ---


def test_estimate_is_characters_over_three() -> None:
    assert w.estimate_tokens("") == 0 and w.estimate_tokens("abc") == 1 and w.estimate_tokens("abcd") == 2
    assert w.estimate_tokens("я" * 3000) == 1000  # characters, not bytes
    assert w.estimate_tokens({"a": "я" * 30}) == -(-len('{"a":"' + "я" * 30 + '"}') // 3)  # compact JSON, no \u escapes
    assert w.estimate_messages([answer("я" * 300)], extra_tokens=500) > 600


# --- the cycle ---


def test_cycle_starts_at_the_last_question() -> None:
    msgs = history(2)
    assert w.cycle_start(msgs) == 8 and msgs[8]["content"][1]["text"] == "Текущий"
    assert w.cycle_start(history(2, current=False)) == 4  # the last question, its call, result and answer
    only_results = [result(1), result(2)]
    assert w.cycle_start(only_results) == 0  # no question: everything is one cycle
    assert w.cycle_start([]) == 0
    assert w.cycle_start([{"role": "user", "content": "строкой"}, answer()]) == 0
    # results appended to the same message as a question ("skipped" results + the new question): it still opens a cycle
    mixed = {"role": "user", "content": [result(9)["content"][0], {"type": "text", "text": "Новый"}]}
    assert w.cycle_start([question(), call(9), mixed]) == 2


# --- below the limit nothing changes ---


def test_within_the_limit_nothing_changes() -> None:
    msgs = history(3)
    out = w.trim_messages(msgs, limit=10**9)
    assert out.messages == msgs and not out.changed and out.fits
    assert out.tokens == w.estimate_messages(msgs)
    assert all(a is b for a, b in zip(out.messages, msgs, strict=True))  # the very same objects


# --- the trim ---


def test_old_results_are_shortened_the_current_cycle_is_not() -> None:
    msgs = history(4)
    limit = w.estimate_messages(msgs) - 2500  # needs about three old results out
    out = w.trim_messages(msgs, limit=limit)
    assert out.changed and out.fits and out.tokens <= limit
    res = results_of(out.messages)
    assert res[0] == w.TRIMMED_RESULT  # the oldest first
    assert res[-1] == BIG + "!"  # the running cycle is intact
    start = w.cycle_start(msgs)
    assert out.messages[start:] == msgs[start:]  # all of it, thoughts included
    assert any(r == BIG for r in res)  # stops as soon as it fits: not everything is cut


def test_everything_old_goes_when_needed_and_the_cycle_stays() -> None:
    msgs = history(5)
    out = w.trim_messages(msgs, limit=1)
    start = w.cycle_start(msgs)
    assert not out.fits  # the cycle alone does not fit: the trim does not cut it, the loop will see the API's answer
    assert results_of(out.messages[:start]) == [w.TRIMMED_RESULT] * 5
    assert out.messages[start:] == msgs[start:]
    assert not any(
        b["type"] == "thinking" for m in out.messages[:start] for b in m["content"] if isinstance(m["content"], list)
    )
    assert any(
        b["type"] == "thinking" for b in out.messages[start + 1]["content"]
    )  # thinking of the running cycle stays


def test_thinking_is_dropped_only_after_the_results() -> None:
    msgs = history(3)
    results_only = w.estimate_messages(msgs) - 2500  # enough to cut the results of two cycles, thinking can stay
    out = w.trim_messages(msgs, limit=results_only)
    assert any(b["type"] == "thinking" for b in out.messages[1]["content"])  # not yet dropped
    out2 = w.trim_messages(msgs, limit=w.estimate_messages(msgs) - 3000 - 3 * 100)  # more than all results hold
    assert out2.changed
    assert not any(b["type"] == "thinking" for b in out2.messages[1]["content"])


def test_old_workspace_blocks_are_the_last_step() -> None:
    ws = "<workspace>" + "я" * 3000 + "</workspace>"
    msgs = [question("Старый", ws), answer(), question("Текущий", ws)]
    out = w.trim_messages(msgs, limit=w.estimate_messages(msgs) - 500)
    assert out.messages[0]["content"][0]["text"] == w.TRIMMED_WORKSPACE
    assert out.messages[0]["content"][1]["text"] == "Старый"  # the words of the user are never cut
    assert out.messages[2] == msgs[2]  # the workspace of the current question stays


def test_pairing_and_flags_survive() -> None:
    msgs = [
        question(),
        call(1),
        result(1, is_error=True),
        call(2),
        result(2),
        answer(),
        question("Текущий"),
        answer("Нет"),
    ]
    out = w.trim_messages(msgs, limit=1)
    ids = [
        b["id"] for m in out.messages if isinstance(m["content"], list) for b in m["content"] if b["type"] == "tool_use"
    ]
    used = [
        b["tool_use_id"]
        for m in out.messages
        if isinstance(m["content"], list)
        for b in m["content"]
        if b["type"] == "tool_result"
    ]
    assert ids == used == ["toolu_1", "toolu_2"]
    assert out.messages[2]["content"][0]["is_error"] is True  # an error stays an error
    assert [m["role"] for m in out.messages] == [m["role"] for m in msgs]


def test_a_short_result_is_not_replaced_by_a_longer_placeholder() -> None:
    msgs = [question(), call(1), result(1, "ok"), answer(), question("Текущий")]
    out = w.trim_messages(msgs, limit=1)
    assert results_of(out.messages) == ["ok"]


def test_list_content_of_a_result_is_replaced_by_text() -> None:
    blocks = [{"type": "text", "text": BIG}]
    msgs = [
        question(),
        call(1),
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": blocks}]},
        answer(),
        question("Текущий"),
    ]
    assert results_of(w.trim_messages(msgs, limit=1).messages) == [w.TRIMMED_RESULT]


def test_an_assistant_message_of_only_thinking_is_kept() -> None:
    only = {"role": "assistant", "content": [{"type": "thinking", "thinking": BIG, "signature": "s"}]}
    out = w.trim_messages([question(), only, question("Текущий")], limit=1)
    assert out.messages[1] == only  # never an empty message


def test_input_is_not_modified() -> None:
    msgs = history(4)
    before = copy.deepcopy(msgs)
    w.trim_messages(msgs, limit=1)
    w.trim_messages(msgs, limit=1, force=True)
    w.shrink_for_retry(msgs)
    assert msgs == before


def test_extra_tokens_count_towards_the_limit() -> None:
    msgs = history(3)
    base = w.estimate_messages(msgs)
    assert not w.trim_messages(msgs, limit=base + 100).changed
    assert w.trim_messages(msgs, limit=base + 100, extra_tokens=5000).changed  # the tools and the prompt take room too


def test_default_limit_is_150k_tokens() -> None:
    assert w.CONTEXT_LIMIT_TOKENS == 150_000
    msgs = [question(), call(1), result(1, "я" * 600_000), answer(), question("Текущий")]  # ~200k tokens
    out = w.trim_messages(msgs)
    assert out.changed and out.fits and results_of(out.messages) == [w.TRIMMED_RESULT]


# --- model_context_window_exceeded ---


def test_force_trims_everything_old_whatever_the_estimate() -> None:
    msgs = history(2)
    out = w.trim_messages(msgs, limit=10**9, force=True)
    start = w.cycle_start(msgs)
    assert (
        out.changed
        and results_of(out.messages[:start]) == [w.TRIMMED_RESULT] * 2
        and out.messages[start:] == msgs[start:]
    )


def test_shrink_for_retry() -> None:
    out = w.shrink_for_retry(history(2))
    assert out is not None and out.changed
    assert w.shrink_for_retry(out.messages) is None  # a second time there is nothing left: the loop gives up
    assert w.shrink_for_retry([question("Только вопрос")]) is None  # nothing old at all
    assert w.MAX_RETRIES == 1


@pytest.mark.parametrize(
    ("stop", "error", "expected"),
    [
        ("model_context_window_exceeded", None, True),
        ("end_turn", None, False),
        ("max_tokens", None, False),
        (None, None, False),
        (None, "Error code: 400 - prompt is too long: 210000 tokens > 200000 maximum", True),
        (None, ValueError("The model_context_window_exceeded stop"), True),
        (None, "exceeds the context window of this model", True),
        (None, "invalid x-api-key", False),
        (None, ConnectionError("offline"), False),
    ],
)
def test_is_context_exceeded(stop: str | None, error: Any, expected: bool) -> None:
    assert w.is_context_exceeded(stop_reason=stop, error=error) is expected
