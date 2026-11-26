"""Э23 23.3: prices, usage accounting and prefs of the assistant (ADR 0029 решения 13, 15, 16; вопросы № 128, 129)."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from bf_core.api.errors import ApiError
from bf_core.assistant import prefs, prices, usage

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
M = 1_000_000


def _line(at: datetime, model: str | None = "claude-opus-5-5", inp: int = 1000, out: int = 200, cr: int = 0,
          cw: int = 0, cost: float | None | str = "auto") -> str:
    c = prices.cost(model, inp, out, cr, cw) if cost == "auto" else cost
    row = {"at": usage._fmt(at), "model": model, "input": inp, "output": out, "cache_read": cr, "cache_write": cw,
           "cost_usd": c, "long": False, "dialog_uid": None, "project_uid": None, "prices_version": "2026-11"}
    return json.dumps(row)


def _write(tmp: Path, lines: list[str]) -> None:
    p = usage.usage_path(tmp)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------------------------------- prices

@pytest.mark.parametrize(("model", "inp", "out", "cr"), [
    ("claude-opus-5-5", 4.0, 20.0, 0.20),
    ("claude-sonnet-5-5", 2.0, 10.0, 0.10),
    ("claude-haiku-5-5", 0.10, 0.50, 0.01),
    ("claude-fable-5-2", 8.0, 40.0, 0.20),
])
def test_prices_of_four_models(model: str, inp: float, out: float, cr: float) -> None:
    n = 50_000  # below the long tier of Haiku
    k = n / M
    assert prices.cost(model, n, 0) == pytest.approx(inp * k)
    assert prices.cost(model, 0, n) == pytest.approx(out * k)
    assert prices.cost(model, 0, 0, cache_read=n) == pytest.approx(cr * k)
    # cache write = 1.25 x input
    assert prices.cost(model, 0, 0, cache_write=n) == pytest.approx(1.25 * inp * k)
    assert prices.cost(model, 10, 10, 10, 10) == pytest.approx((inp + out + cr + 1.25 * inp) * 10 / M, abs=1e-9)


def test_list_has_four_models_and_defaults() -> None:
    assert [m.id for m in prices.MODELS] == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
                                              "claude-fable-5-2"]
    assert prices.DEFAULT_MODEL == "claude-opus-5-5" and prices.DEFAULT_EFFORT == "medium"
    assert [m["id"] for m in prices.models_payload() if m["default"]] == ["claude-opus-5-5"]
    haiku = prices.models_payload()[2]
    assert haiku["long"] == {"threshold": 100000, "input": 0.5, "output": 2.5, "cache_read": 0.05,
                             "cache_write": 0.625}
    assert haiku["price"]["cache_write"] == 0.125
    assert [e["id"] for e in prices.efforts_payload()] == ["low", "medium", "high", "xhigh", "max"]


def test_haiku_long_request_over_100k() -> None:
    # threshold: input + cache read + cache write of ONE request; the whole request is priced by the long tier (№ 128)
    assert not prices.is_long("claude-haiku-5-5", 100_000)
    assert prices.is_long("claude-haiku-5-5", 100_001)
    assert prices.is_long("claude-haiku-5-5", 10, cache_read=60_000, cache_write=40_000)
    assert not prices.is_long("claude-haiku-5-5", 10, cache_read=60_000, cache_write=39_990)
    assert prices.cost("claude-haiku-5-5", 100_000, M) == pytest.approx(0.01 + 0.5)
    assert prices.cost("claude-haiku-5-5", 100_001, M) == pytest.approx(100_001 * 0.5 / M + 2.5)
    assert prices.cost("claude-haiku-5-5", 1, 0, cache_read=M) == pytest.approx(0.05, abs=1e-6)
    assert prices.cost("claude-haiku-5-5", 0, 0, cache_write=M) == pytest.approx(0.625)
    # other models have no long tier
    assert not prices.is_long("claude-opus-5-5", 900_000)
    assert prices.cost("claude-opus-5-5", 500_000, 0) == pytest.approx(2.0)


def test_model_prefixes_and_unknown_model_is_null() -> None:
    assert prices.cost("claude-sonnet-5-5-20261101", M, 0) == pytest.approx(2.0)
    assert prices.cost("claude-haiku-5-5@20261101", 50_000, 0) == pytest.approx(0.005)
    assert prices.find_model("claude-opus-5-55") is None  # prefix only at a boundary
    for unknown in ("claude-opus-9", "gpt-x", "", None, "claude-opus-5"):
        assert prices.cost(unknown, 1000, 1000) is None  # not 0
    assert prices.title_of("claude-sonnet-5-5-2026") == "Sonnet 5.5"
    assert prices.title_of("mystery") == "mystery"


@pytest.mark.parametrize("bad", [-1, 1.5, True, None, "7"])
def test_bad_token_counts_are_rejected(bad: object) -> None:
    with pytest.raises(ValueError, match="токенов"):
        prices.cost("claude-opus-5-5", bad, 0)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------------- record

def test_record_appends_a_line_with_cost(tmp_path: Path) -> None:
    row = usage.record(tmp_path, model="claude-opus-5-5", input=1000, output=500, cache_read=2000, cache_write=800,
                       dialog_uid="d1", project_uid=None, now=NOW)
    assert row["cost_usd"] == pytest.approx((1000 * 4 + 500 * 20 + 2000 * 0.2 + 800 * 5) / M)
    assert row["long"] is False and row["prices_version"] == "2026-11"
    lines = usage.usage_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == row
    assert row["at"] == "2026-11-26T12:00:00.000Z"
    usage.record(tmp_path, model="claude-haiku-5-5", input=150_000, output=10, now=NOW)
    assert json.loads(usage.usage_path(tmp_path).read_text().splitlines()[1])["long"] is True


def test_record_unknown_model_writes_null_cost(tmp_path: Path) -> None:
    row = usage.record(tmp_path, model="claude-future-9", input=10, output=10, now=NOW)
    assert row["cost_usd"] is None
    assert json.loads(usage.usage_path(tmp_path).read_text())["cost_usd"] is None


def test_record_is_thread_safe(tmp_path: Path) -> None:
    def work() -> None:
        for _ in range(50):
            usage.record(tmp_path, model="claude-opus-5-5", input=1, output=1, now=NOW)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = usage.usage_path(tmp_path).read_text().splitlines()
    assert len(lines) == 400 and all(json.loads(x)["input"] == 1 for x in lines)
    assert usage.summary(tmp_path, NOW, UTC)["all"]["requests"] == 400


def test_default_profile_uses_paths_py(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BF_LOCAL_DIR", str(tmp_path / "local"))
    assert usage.assistant_dir(None) == tmp_path / "local" / "assistant"
    assert usage.assistant_dir(tmp_path / "data") == tmp_path / "data" / "assistant"


# ------------------------------------------------------------------------------------------------------- summary

def test_summary_empty_profile(tmp_path: Path) -> None:
    s = usage.summary(tmp_path, NOW, UTC)
    assert s["since"] is None and s["prices_version"] == "2026-11"
    for p in ("today", "days30", "all"):
        assert s[p]["requests"] == 0 and s[p]["cost_usd"] == 0.0 and s[p]["by_model"] == []


def test_periods_and_models(tmp_path: Path) -> None:
    _write(tmp_path, [
        _line(NOW - timedelta(hours=1), "claude-opus-5-5", 1000, 100),
        _line(NOW - timedelta(hours=2), "claude-sonnet-5-5-20261101", 2000, 200, cr=500),
        _line(NOW - timedelta(days=3), "claude-sonnet-5-5", 4000, 400),
        _line(NOW - timedelta(days=40), "claude-haiku-5-5", 150_000, 100),
    ])
    s = usage.summary(tmp_path, NOW, UTC)
    assert (s["today"]["requests"], s["days30"]["requests"], s["all"]["requests"]) == (2, 3, 4)
    assert [m["model"] for m in s["today"]["by_model"]] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    sonnet = s["days30"]["by_model"][1]
    assert sonnet["title"] == "Sonnet 5.5" and sonnet["requests"] == 2
    assert sonnet["tokens"] == {"input": 6000, "output": 600, "cache_read": 500, "cache_write": 0}
    assert s["all"]["tokens"]["input"] == 1000 + 2000 + 4000 + 150_000
    assert s["all"]["cost_usd"] == pytest.approx(
        (1000 * 4 + 100 * 20 + 2000 * 2 + 200 * 10 + 500 * 0.1 + 4000 * 2 + 400 * 10 + 150_000 * 0.5 + 100 * 2.5) / M)
    assert s["since"] == usage._fmt(NOW - timedelta(days=40))


def test_local_day_boundaries_by_timezone(tmp_path: Path) -> None:
    tz = timezone(timedelta(hours=5))  # UTC+5
    now = datetime(2026, 11, 26, 20, 0, tzinfo=UTC)  # 27 Nov 01:00 local
    midnight = datetime(2026, 11, 27, 0, 0, tzinfo=tz)  # = 26 Nov 19:00 UTC
    _write(tmp_path, [
        _line(midnight - timedelta(milliseconds=1)),  # 26 Nov 23:59:59.999 local: yesterday
        _line(midnight),  # 27 Nov 00:00:00.000 local: today
        _line(midnight + timedelta(hours=3)),
    ])
    s = usage.summary(tmp_path, now, tz)
    assert s["today"]["requests"] == 2 and s["days30"]["requests"] == 3
    # the same instants for a machine in UTC-8: all three are still 26 Nov local (now = 26 Nov 12:00 local)
    s8 = usage.summary(tmp_path, now, timezone(timedelta(hours=-8)))
    assert s8["today"]["requests"] == 3


def test_local_midnight_with_a_zone_change_of_day(tmp_path: Path) -> None:
    tz = timezone(timedelta(hours=-8))
    now = datetime(2026, 11, 27, 3, 0, tzinfo=UTC)  # 26 Nov 19:00 local
    _write(tmp_path, [_line(datetime(2026, 11, 26, 7, 59, 59, tzinfo=UTC)),  # 25 Nov 23:59:59 local
                      _line(datetime(2026, 11, 26, 8, 0, 0, tzinfo=UTC))])  # 26 Nov 00:00:00 local
    assert usage.summary(tmp_path, now, tz)["today"]["requests"] == 1
    # the same file for UTC: both are on the 26th UTC... and the 27th has started 3 hours ago
    assert usage.summary(tmp_path, now, UTC)["today"]["requests"] == 0


def test_days30_window_is_30_local_days_with_today(tmp_path: Path) -> None:
    tz = timezone(timedelta(hours=3))
    now = datetime(2026, 11, 26, 12, 0, tzinfo=tz)
    first = datetime(2026, 10, 28, 0, 0, tzinfo=tz)  # today - 29 days
    _write(tmp_path, [_line(first - timedelta(milliseconds=1)), _line(first), _line(now)])
    s = usage.summary(tmp_path, now, tz)
    assert s["days30"]["requests"] == 2 and s["all"]["requests"] == 3 and s["today"]["requests"] == 1


def test_system_timezone_is_the_default(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _write(tmp_path, [_line(now), _line(now - timedelta(days=2))])
    s = usage.summary(tmp_path)
    assert s["all"]["requests"] == 2 and s["days30"]["requests"] == 2 and s["today"]["requests"] >= 1


def test_unknown_model_is_null_not_zero(tmp_path: Path) -> None:
    _write(tmp_path, [_line(NOW, "claude-mystery-1", 500, 50, cost=None)])
    s = usage.summary(tmp_path, NOW, UTC)
    assert s["today"]["cost_usd"] is None and s["all"]["unpriced_requests"] == 1
    assert s["all"]["by_model"][0]["cost_usd"] is None and s["all"]["by_model"][0]["title"] == "claude-mystery-1"
    assert s["all"]["tokens"]["input"] == 500  # tokens are counted, the price is not guessed
    # a mixed period: the known part is summed, the unpriced calls are counted apart
    _write(tmp_path, [_line(NOW, "claude-mystery-1", cost=None), _line(NOW, "claude-opus-5-5", M, 0)])
    s = usage.summary(tmp_path, NOW, UTC)
    assert s["all"]["cost_usd"] == pytest.approx(4.0) and s["all"]["unpriced_requests"] == 1
    by = {m["model"]: m for m in s["all"]["by_model"]}
    assert by["claude-mystery-1"]["cost_usd"] is None and by["claude-opus-5-5"]["cost_usd"] == pytest.approx(4.0)


def test_unreadable_lines_are_skipped(tmp_path: Path) -> None:
    p = usage.usage_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(_line(NOW) + "\n{broken\n\n" + _line(NOW) + '\n{"at": "2026-11-26T1', encoding="utf-8")
    assert usage.summary(tmp_path, NOW, UTC)["all"]["requests"] == 2


def test_summary_cache_follows_the_file(tmp_path: Path) -> None:
    usage.record(tmp_path, model="claude-opus-5-5", input=10, output=1, now=NOW)
    first = usage.summary(tmp_path, NOW, UTC)
    first["all"]["requests"] = 99  # the caller may not damage the cache
    assert usage.summary(tmp_path, NOW, UTC)["all"]["requests"] == 1
    usage.record(tmp_path, model="claude-opus-5-5", input=10, output=1, now=NOW)
    assert usage.summary(tmp_path, NOW, UTC)["all"]["requests"] == 2
    # the day changed: today's period is recomputed
    assert usage.summary(tmp_path, NOW + timedelta(days=1), UTC)["today"]["requests"] == 0


# ------------------------------------------------------------------------------------------------------- fold

def _old_file(tmp: Path) -> list[str]:
    lines = []
    for d in range(1, 120):
        at = NOW - timedelta(days=d, minutes=d)
        for model in ("claude-opus-5-5", "claude-haiku-5-5", "claude-mystery-1"):
            lines.append(_line(at, model, 1000 + d, 100 + d, cr=d, cw=2 * d,
                               cost=None if "mystery" in model else "auto"))
    lines += [_line(NOW - timedelta(hours=h)) for h in (1, 2, 3)]
    _write(tmp, lines)
    return lines


def test_fold_keeps_all_time_and_windows(tmp_path: Path) -> None:
    _old_file(tmp_path)
    before = usage.summary(tmp_path, NOW, UTC)
    size = usage.usage_path(tmp_path).stat().st_size
    folded = usage.fold(tmp_path, NOW, UTC)
    after = usage.summary(tmp_path, NOW, UTC)
    assert folded > 0 and usage.usage_path(tmp_path).stat().st_size < size
    assert after == before  # all three periods, models, costs and `since`
    data = json.loads((usage.assistant_dir(tmp_path) / usage.SUMMARY_FILE).read_text())
    assert data["folded_before"] == "2026-10-01T00:00:00.000Z"  # window starts on 28 Oct: months before October
    assert sorted(data["months"]) == ["2026-07", "2026-08", "2026-09"]
    assert usage.fold(tmp_path, NOW, UTC) == 0  # nothing more to fold
    assert usage.summary(tmp_path, NOW, UTC) == before


def test_fold_twice_with_new_months_adds_up(tmp_path: Path) -> None:
    _old_file(tmp_path)
    usage.fold(tmp_path, NOW, UTC)
    later = NOW + timedelta(days=62)
    before = usage.summary(tmp_path, later, UTC)
    assert usage.fold(tmp_path, later, UTC) > 0
    assert usage.summary(tmp_path, later, UTC) == before
    # a file whose summary is damaged does not break the table
    (usage.assistant_dir(tmp_path) / usage.SUMMARY_FILE).write_text("{not json")
    assert usage.summary(tmp_path, later, UTC)["all"]["requests"] < before["all"]["requests"]


def test_interrupted_fold_does_not_double_count(tmp_path: Path) -> None:
    lines = _old_file(tmp_path)
    before = usage.summary(tmp_path, NOW, UTC)
    usage.fold(tmp_path, NOW, UTC)
    # a crash after the summary write but before the file replace: the old lines are still in the file
    usage.usage_path(tmp_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert usage.summary(tmp_path, NOW, UTC) == before
    assert usage.fold(tmp_path, NOW, UTC) == 0
    assert usage.summary(tmp_path, NOW, UTC) == before


def test_record_folds_a_big_file(tmp_path: Path) -> None:
    _old_file(tmp_path)
    before = usage.summary(tmp_path, NOW, UTC)["all"]["requests"]
    size = usage.usage_path(tmp_path).stat().st_size
    usage.record(tmp_path, model="claude-opus-5-5", input=1, output=1, now=NOW, tz=UTC, fold_bytes=size // 2)
    assert (usage.assistant_dir(tmp_path) / usage.SUMMARY_FILE).exists()
    assert usage.usage_path(tmp_path).stat().st_size < size // 2
    assert usage.summary(tmp_path, NOW, UTC)["all"]["requests"] == before + 1


def test_threshold_is_20_mb() -> None:
    assert usage.FOLD_BYTES == 20 * 1024 * 1024


def test_torn_last_line_is_not_swallowed_by_the_next_call(tmp_path: Path) -> None:
    _write(tmp_path, [_line(NOW)])
    with open(usage.usage_path(tmp_path), "ab") as fh:
        fh.write(b'{"at":"2026-11-26T11:0')  # a crash in the middle of a write
    assert usage.summary(tmp_path, NOW, UTC)["all"]["requests"] == 1  # a torn line is not a call, not a zero
    usage.record(tmp_path, model="claude-opus-5-5", input=1, output=1, now=NOW)
    assert usage.summary(tmp_path, NOW, UTC)["all"]["requests"] == 2


def test_sql_engine_equals_python_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = _old_file(tmp_path)
    lines.append(_line(NOW, "claude-sonnet-5-5-20261101", 7, 7, cost=None))
    _write(tmp_path, lines)
    fast = usage.summary(tmp_path, NOW, UTC)
    assert fast["all"]["unpriced_requests"] > 0
    monkeypatch.setattr(usage, "_USE_DUCKDB", False)
    usage._cache.clear()
    assert usage.summary(tmp_path, NOW, UTC) == fast
    # the same after a fold
    usage.fold(tmp_path, NOW, UTC)
    usage._cache.clear()
    assert usage.summary(tmp_path, NOW, UTC) == fast
    monkeypatch.setattr(usage, "_USE_DUCKDB", True)
    usage._cache.clear()
    assert usage.summary(tmp_path, NOW, UTC) == fast


def test_sql_engine_survives_a_quote_and_cyrillic_in_the_path(tmp_path: Path) -> None:
    data = tmp_path / "Данные 'проба'"
    _write(data, [_line(NOW), _line(NOW, "claude-sonnet-5-5")])
    assert usage.summary(data, NOW, UTC)["all"]["requests"] == 2


# ------------------------------------------------------------------------------------------------------- speed

def test_summary_of_100k_lines_is_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_python_engine(*_a: object) -> None:
        raise AssertionError("the SQL engine must count a regular file itself")

    monkeypatch.setattr(usage, "_scan_python", no_python_engine)
    models = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"]
    rows = []
    for i in range(100_000):
        at = NOW - timedelta(seconds=i * 20)
        rows.append(json.dumps({"at": usage._fmt(at), "model": models[i % 4], "input": 1200 + i % 50, "output": 300,
                                "cache_read": 9000, "cache_write": 400, "cost_usd": 0.0421, "long": False,
                                "dialog_uid": "d" + str(i % 40), "project_uid": "p1", "prices_version": "2026-11"}))
    _write(tmp_path, rows)
    t0 = time.perf_counter()
    s = usage.summary(tmp_path, NOW, UTC)
    dt = time.perf_counter() - t0
    assert s["all"]["requests"] == 100_000 and len(s["all"]["by_model"]) == 4
    assert dt <= 0.3, f"summary of 10^5 lines took {dt:.3f} s"
    t0 = time.perf_counter()
    usage.summary(tmp_path, NOW, UTC)
    assert time.perf_counter() - t0 < 0.05  # cached


# ------------------------------------------------------------------------------------------------------- prefs

def test_prefs_defaults(tmp_path: Path) -> None:
    r = prefs.resolve(tmp_path, {})
    assert (r.model, r.model_source, r.effort, r.effort_source) == ("claude-opus-5-5", "default", "medium", "default")


def test_prefs_window_wins_over_env_over_default(tmp_path: Path) -> None:
    env = {"ASSISTANT_MODEL": "claude-sonnet-5-5", "ASSISTANT_EFFORT": "high"}
    r = prefs.resolve(tmp_path, env)
    assert (r.model, r.model_source, r.effort, r.effort_source) == ("claude-sonnet-5-5", "env", "high", "env")
    r = prefs.set_prefs(tmp_path, model="claude-haiku-5-5")
    assert (r.model, r.model_source) == ("claude-haiku-5-5", "prefs")
    r = prefs.resolve(tmp_path, env)
    assert (r.model, r.model_source, r.effort, r.effort_source) == ("claude-haiku-5-5", "prefs", "high", "env")
    r = prefs.set_prefs(tmp_path, effort="max")
    assert r.model == "claude-haiku-5-5" and r.effort == "max"  # the other value stays
    assert json.loads(prefs.prefs_path(tmp_path).read_text()) == {"model": "claude-haiku-5-5", "effort": "max"}


def test_prefs_bad_environment_falls_back_with_a_log(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    prefs._warned.clear()
    with caplog.at_level("WARNING", logger="bf_core.assistant"):
        r = prefs.resolve(tmp_path, {"ASSISTANT_MODEL": "gpt-9", "ASSISTANT_EFFORT": "ultra"})
        prefs.resolve(tmp_path, {"ASSISTANT_MODEL": "gpt-9"})
    assert (r.model, r.model_source, r.effort, r.effort_source) == ("claude-opus-5-5", "default", "medium", "default")
    assert len([x for x in caplog.records if "gpt-9" in x.getMessage()]) == 1  # logged once
    assert any("ultra" in x.getMessage() for x in caplog.records)
    # an empty variable is simply not set; a bad one does not hide a good source below it
    assert prefs.resolve(tmp_path, {"ASSISTANT_MODEL": "  "}).model_source == "default"
    prefs.set_prefs(tmp_path, model="claude-fable-5-2")
    assert prefs.resolve(tmp_path, {"ASSISTANT_MODEL": "gpt-9"}).model == "claude-fable-5-2"


def test_prefs_reads_real_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSISTANT_MODEL", "claude-fable-5-2")
    monkeypatch.setenv("ASSISTANT_EFFORT", "xhigh")
    r = prefs.resolve(tmp_path)
    assert (r.model, r.effort, r.model_source) == ("claude-fable-5-2", "xhigh", "env")


@pytest.mark.parametrize(("kwargs", "param"), [
    ({"model": "claude-opus-4"}, "model"), ({"model": ""}, "model"), ({"model": "claude-haiku-5-5-2026"}, "model"),
    ({"effort": "ultra"}, "effort"), ({"effort": "MEDIUM"}, "effort"),
    ({"model": "claude-haiku-5-5", "effort": "ultra"}, "effort"),
])
def test_prefs_wrong_values_are_refused_in_russian(tmp_path: Path, kwargs: dict[str, str], param: str) -> None:
    with pytest.raises(ApiError) as err:
        prefs.set_prefs(tmp_path, **kwargs)
    e = err.value
    assert (e.status, e.code, e.extra) == (422, "bad_param", {"param": param})
    assert e.message and e.hint and any("а" <= ch.lower() <= "я" for ch in e.message + e.hint)
    assert not prefs.prefs_path(tmp_path).exists()  # nothing is written, even the valid half


def test_prefs_damaged_file_is_ignored(tmp_path: Path) -> None:
    p = prefs.prefs_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("[1, 2", encoding="utf-8")
    assert prefs.resolve(tmp_path, {}).model_source == "default"
    p.write_text(json.dumps({"model": "claude-old-1", "effort": 5}), encoding="utf-8")
    assert prefs.resolve(tmp_path, {"ASSISTANT_MODEL": "claude-sonnet-5-5"}).model == "claude-sonnet-5-5"
    assert prefs.set_prefs(tmp_path, effort="low").effort == "low"
