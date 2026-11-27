"""The segment of a turn (подшаг 23.10; ADR 0029 решение 24; e23-api.md §9-§10): the numbers of the events, the
buffer of 2000, the catch-up from `after`, the keepalive, one segment per dialog, the stop."""

from __future__ import annotations

import threading
import time

import pytest

from bf_core.api.errors import ApiError
from bf_core.assistant.segments import BUFFER_EVENTS, SEGMENTS, Segment, Segments


def fill(seg: Segment, n: int) -> None:
    for i in range(n):
        seg.emit("text.delta", {"item_id": "a", "text": str(i)})


def test_events_are_numbered_from_one() -> None:
    seg = Segment("1")
    assert seg.emit("turn.start", {}).id == 1 and seg.emit("text.delta", {"item_id": "a", "text": "x"}).id == 2
    assert [e.id for e in seg.events_after(0)] == [1, 2] and [e.id for e in seg.events_after(1)] == [2]
    assert seg.last_id == 2 and seg.first_id == 1 and seg.id.startswith("seg-") and not seg.done


def test_the_buffer_keeps_the_last_2000_events() -> None:
    assert BUFFER_EVENTS == 2000
    seg = Segment("1")
    fill(seg, 2500)
    kept = seg.events_after(0)
    assert len(kept) == 2000 and kept[0].id == 501 and kept[-1].id == 2500 and seg.first_id == 501
    seg.finish()
    assert [e.id for e in seg.iter(after=0) if e is not None] == list(range(501, 2501))  # from the first one kept
    assert [e.id for e in seg.iter(after=2490) if e is not None] == list(range(2491, 2501))


def test_iteration_ends_after_a_finished_segment_and_never_raises() -> None:
    seg = Segment("1")
    fill(seg, 3)
    seg.finish()
    assert [e.id for e in seg.iter() if e is not None] == [1, 2, 3]
    assert list(seg.iter(after=3)) == [] and list(seg.iter(after=99)) == []
    assert seg.emit("late", {}).event == "late" and len(seg.events_after(0)) == 3  # nothing is added after the end


def test_a_reader_gets_the_events_as_they_come() -> None:
    seg = Segment("1")
    got: list[int] = []
    reader = threading.Thread(target=lambda: got.extend(e.id for e in seg.iter() if e is not None), daemon=True)
    reader.start()
    for i in range(5):
        seg.emit("text.delta", {"item_id": "a", "text": str(i)})
        time.sleep(0.01)
    seg.finish()
    reader.join(5)
    assert got == [1, 2, 3, 4, 5]


def test_two_readers_and_a_late_one_see_the_same_events() -> None:
    seg = Segment("1")
    fill(seg, 4)
    first = [e.id for e in seg.events_after(0)]
    seg.emit("turn.end", {})
    seg.finish()
    late = [e.id for e in seg.iter(after=2) if e is not None]
    assert first == [1, 2, 3, 4] and late == [3, 4, 5]


def test_silence_gives_a_keepalive() -> None:
    seg = Segment("1")
    stream = seg.iter(keepalive=0.05)
    t0 = time.monotonic()
    assert next(stream) is None and time.monotonic() - t0 < 2.0
    seg.emit("turn.start", {})
    event = next(stream)
    assert event is not None and event.event == "turn.start"
    seg.finish()
    assert list(stream) == []


def test_one_running_segment_per_dialog() -> None:
    segments = Segments()
    seg = segments.start("41")
    assert segments.get("41") is seg and segments.busy() == frozenset({"41"})
    with pytest.raises(ApiError) as e:
        segments.start("41")
    assert e.value.status == 409 and e.value.code == "assistant_busy"
    other = segments.start("42")  # another dialog is free
    assert segments.busy() == frozenset({"41", "42"})
    segments.release(seg)
    assert seg.done and segments.get("41") is None and segments.busy() == frozenset({"42"})
    assert segments.start("41") is not seg
    segments.release(other)
    segments.release(seg)  # releasing twice, or an old segment, does not free a newer one
    assert segments.get("41") is not None


def test_cancel_raises_the_flag_and_closes_the_stream() -> None:
    segments = Segments()
    seg = segments.start("1")
    closed: list[bool] = []
    seg.set_closer(lambda: closed.append(True))
    assert not seg.cancelled and segments.cancel("1") is True
    assert seg.cancelled and closed == [True]
    assert segments.cancel("nobody") is False
    seg.set_closer(None)
    seg.cancel()
    assert closed == [True]  # no closer, nothing closed twice


def test_a_closer_given_after_a_cancel_is_called_at_once() -> None:
    seg = Segment("1")
    seg.cancel()
    closed: list[bool] = []
    seg.set_closer(lambda: closed.append(True))
    assert closed == [True]


def test_a_failing_closer_does_not_break_the_cancel() -> None:
    seg = Segment("1")

    def boom() -> None:
        raise RuntimeError("already closed")

    seg.set_closer(boom)
    seg.cancel()
    assert seg.cancelled


def test_a_temporary_dialog_that_became_saved_is_rekeyed() -> None:
    segments = Segments()
    seg = segments.start("t-1")
    segments.rekey(seg, "41")
    assert segments.get("t-1") is None and segments.get("41") is seg and seg.dialog_id == "41"
    with pytest.raises(ApiError):
        segments.start("41")


def test_cancel_all_and_wait() -> None:
    segments = Segments()
    a, b = segments.start("1"), segments.start("2")
    assert segments.cancel_all() == 2 and a.cancelled and b.cancelled
    assert a.wait(0.01) is False
    a.finish()
    assert a.wait(0.01) is True


def test_the_process_wide_registry_exists() -> None:
    assert isinstance(SEGMENTS, Segments)
