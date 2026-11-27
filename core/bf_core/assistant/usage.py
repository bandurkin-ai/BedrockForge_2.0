"""Usage accounting of the assistant (ТЗ 7.32, ADR 0029 решение 16, вопрос № 129).

`<data>/assistant/usage.jsonl` in the user profile (not in the project, not in `.bfggis`, ТЗ 7.4): one line per API
call, appended under a lock. `summary()` gives "Сегодня / 30 дней / Всё время" by models; days are LOCAL days of
the machine.
A file over 20 MB folds the old months into `usage-summary.json`; the "all time" totals do not change.

Line: {"at", "model", "input", "output", "cache_read", "cache_write", "cost_usd", "long", "dialog_uid",
"project_uid", "prices_version"}. `at` is UTC with a fixed width (`2026-11-26T10:00:00.123Z`), so the periods are
compared as strings — no date parsing per line. `cost_usd: null` is a call of an unknown model ("нет цены", not 0).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from bf_core.assistant import prices
from bf_core.paths import local_app_dir

log = logging.getLogger("bf_core.assistant")

USAGE_FILE = "usage.jsonl"
SUMMARY_FILE = "usage-summary.json"
FOLD_BYTES = 20 * 1024 * 1024
LOCK_TIMEOUT_S = 5.0
_NANO = 1_000_000_000
_FIELDS = ("requests", "input", "output", "cache_read", "cache_write", "cost_nano", "unpriced")

_thread_lock = threading.RLock()
_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}


def assistant_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    """`<data>/assistant` (`--data` of the core = %APPDATA%\\BedrockForge); without `--data` — paths.py profile."""
    base = Path(data_dir) if data_dir else local_app_dir()
    return base / "assistant"


def usage_path(data_dir: str | os.PathLike[str] | None = None) -> Path:
    return assistant_dir(data_dir) / USAGE_FILE


# ---------------------------------------------------------------------------------------------------- locking


def _acquire(fh: Any) -> bool:
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)


def _release(fh: Any) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Thread lock + lock file next to the data (several cores of one user). On a lock timeout the call goes on
    without the file lock: a line is appended in one write, losing the accounting is worse than a rare race."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        with open(path.with_name(path.name + ".lock"), "a+b") as fh:
            got = _acquire(fh)
            if not got:
                log.warning("assistant usage: lock timeout, going on without the file lock")
            try:
                yield
            finally:
                if got:
                    _release(fh)


# ---------------------------------------------------------------------------------------------------- time


def _fmt(dt: datetime) -> str:
    u = dt.astimezone(UTC)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def _normalize_at(at: Any) -> str | None:
    """Fixed-width UTC text of `at`; `None` for an unreadable value."""
    if not isinstance(at, str):
        return None
    if len(at) == 24 and at[10] == "T" and at[-1] == "Z":
        return at
    try:
        dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _fmt(dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def _local_midnight(day: date, tz: tzinfo | None) -> datetime:
    naive = datetime(day.year, day.month, day.day)
    return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()


def _local_today(now: datetime, tz: tzinfo | None) -> date:
    return now.astimezone(tz).date()


def _bounds(now: datetime | None, tz: tzinfo | None) -> tuple[str, str, str]:
    """UTC text of (start of local today, start of the 30-day window, start of the month of that window)."""
    now = now or datetime.now(UTC)
    today = _local_today(now, tz)
    start30 = today - timedelta(days=29)
    return (
        _fmt(_local_midnight(today, tz)),
        _fmt(_local_midnight(start30, tz)),
        _fmt(_local_midnight(date(start30.year, start30.month, 1), tz)),
    )


# ---------------------------------------------------------------------------------------------------- summary file


def _read_folded(directory: Path) -> dict[str, Any]:
    try:
        data = json.loads((directory / SUMMARY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "folded_before": None, "first_at": None, "months": {}}
    if not isinstance(data, dict) or not isinstance(data.get("months"), dict):
        log.warning("assistant usage: %s is damaged, ignored", SUMMARY_FILE)
        return {"version": 1, "folded_before": None, "first_at": None, "months": {}}
    return data


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ---------------------------------------------------------------------------------------------------- record


def record(
    data_dir: str | os.PathLike[str] | None,
    *,
    model: str | None,
    input: int,
    output: int,
    cache_read: int = 0,
    cache_write: int = 0,
    dialog_uid: str | None = None,
    project_uid: str | None = None,
    now: datetime | None = None,
    tz: tzinfo | None = None,
    fold_bytes: int | None = None,
) -> dict[str, Any]:
    """Appends one API call and returns the written row (`cost_usd` is `None` for an unknown model).

    Raises `ValueError` for a bad token count and `OSError` when the profile is not writable: the caller decides
    whether a lost line may break the turn."""
    cost = prices.cost(model, input, output, cache_read, cache_write)
    row = {
        "at": _fmt(now or datetime.now(UTC)),
        "model": model or "",
        "input": input,
        "output": output,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "cost_usd": cost,
        "long": prices.is_long(model, input, cache_read, cache_write),
        "dialog_uid": dialog_uid,
        "project_uid": project_uid,
        "prices_version": prices.PRICES_VERSION,
    }
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    path = usage_path(data_dir)
    with _locked(path):
        with open(path, "ab") as fh:
            if fh.tell() and _last_byte(path) != b"\n":
                line = b"\n" + line  # a torn last line (crash mid-write) must not swallow this call
            fh.write(line)
        limit = FOLD_BYTES if fold_bytes is None else fold_bytes
        if path.stat().st_size > limit:
            _fold_locked(path, now, tz)
    return row


def _last_byte(path: Path) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(-1, 2)
        return fh.read(1)


# ---------------------------------------------------------------------------------------------------- fold


def fold(data_dir: str | os.PathLike[str] | None, now: datetime | None = None, tz: tzinfo | None = None) -> int:
    """Folds the months older than the 30-day window into `usage-summary.json`; returns the number of folded lines.
    The window and "all time" do not change (the file loses only what no period of the table shows by day)."""
    path = usage_path(data_dir)
    if not path.exists():
        return 0
    with _locked(path):
        return _fold_locked(path, now, tz)


def _fold_locked(path: Path, now: datetime | None, tz: tzinfo | None) -> int:
    directory = path.parent
    cut = _bounds(now, tz)[2]
    folded = _read_folded(directory)
    old_cut: str | None = folded.get("folded_before")
    months: dict[str, dict[str, dict[str, int]]] = folded["months"]
    first_at: str | None = folded.get("first_at")
    keep: list[bytes] = []
    n = 0
    canon: dict[str, str] = {}
    with open(path, "rb") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
                at = _normalize_at(rec["at"])
            except (ValueError, KeyError, TypeError):
                log.warning("assistant usage: unreadable line dropped on fold")
                continue
            if at is None:
                continue
            if old_cut is not None and at < old_cut:
                continue  # left over from an interrupted fold: already in the summary
            if at >= cut:
                keep.append(raw if raw.endswith(b"\n") else raw + b"\n")
                continue
            dt = datetime.fromisoformat(at[:-1] + "+00:00").astimezone(tz)
            month = months.setdefault(dt.strftime("%Y-%m"), {})
            model = _model_key(rec.get("model"), canon)
            agg = month.setdefault(model, dict.fromkeys(_FIELDS, 0))
            _add(agg, rec)
            if first_at is None or at < first_at:
                first_at = at
            n += 1
    if n == 0 and old_cut is not None and old_cut >= cut:
        return 0
    folded.update(
        version=1, folded_before=cut if old_cut is None else max(old_cut, cut), first_at=first_at, months=months
    )
    # summary first: lines of the file older than `folded_before` are ignored, so a crash between the two writes
    # neither loses nor doubles anything
    _write_atomic(directory / SUMMARY_FILE, json.dumps(folded, ensure_ascii=False, indent=1).encode("utf-8"))
    _write_atomic(path, b"".join(keep))
    _cache.pop(str(path), None)
    return n


# ---------------------------------------------------------------------------------------------------- summary


def _model_key(model: Any, memo: dict[str, str]) -> str:
    key = model if isinstance(model, str) else ""
    got = memo.get(key)
    if got is None:
        price = prices.find_model(key)
        got = memo[key] = price.id if price else key
    return got


def _add(agg: dict[str, int], rec: dict[str, Any]) -> None:
    agg["requests"] += 1
    agg["input"] += rec.get("input") or 0
    agg["output"] += rec.get("output") or 0
    agg["cache_read"] += rec.get("cache_read") or 0
    agg["cache_write"] += rec.get("cache_write") or 0
    cost = rec.get("cost_usd")
    if cost is None:
        agg["unpriced"] += 1
    else:
        agg["cost_nano"] += round(cost * _NANO)


def _period(acc: dict[str, dict[str, int]]) -> dict[str, Any]:
    total = dict.fromkeys(_FIELDS, 0)
    for agg in acc.values():
        for k in _FIELDS:
            total[k] += agg[k]
    order = {m.id: i for i, m in enumerate(prices.MODELS)}
    by_model = []
    for model in sorted(acc, key=lambda m: (order.get(m, len(order)), m)):
        a = acc[model]
        by_model.append({"model": model, "title": prices.title_of(model), **_view(a)})
    return _view(total) | {"by_model": by_model}


def _view(a: dict[str, int]) -> dict[str, Any]:
    # a period of unpriced calls only has no cost at all (null), not a zero
    cost = None if a["requests"] and a["unpriced"] == a["requests"] else a["cost_nano"] / _NANO
    return {
        "requests": a["requests"],
        "tokens": {k: a[k] for k in ("input", "output", "cache_read", "cache_write")},
        "cost_usd": cost,
        "unpriced_requests": a["unpriced"],
    }


def summary(
    data_dir: str | os.PathLike[str] | None, now: datetime | None = None, tz: tzinfo | None = None
) -> dict[str, Any]:
    """`GET /usage`: {today, days30, all, prices_version, since}. `tz` — the local zone (default: of the machine)."""
    path = usage_path(data_dir)
    start_today, start30, _ = _bounds(now, tz)
    with _locked(path) if path.parent.exists() else _nullcontext():
        try:
            st = path.stat()
            stamp: tuple[Any, ...] = (st.st_size, st.st_mtime_ns)
        except OSError:
            stamp = (0, 0)
        try:
            stamp += (((path.parent / SUMMARY_FILE).stat().st_mtime_ns),)
        except OSError:
            stamp += (0,)
        key = stamp + (start_today, start30)
        hit = _cache.get(str(path))
        if hit is not None and hit[0] == key:
            return copy.deepcopy(hit[1])
        result = _compute(path, start_today, start30)
        _cache[str(path)] = (key, result)
    return copy.deepcopy(result)


@contextmanager
def _nullcontext() -> Iterator[None]:
    yield


# (bucket, canonical model) -> counters in the order of _FIELDS
_Acc = dict[tuple[int, str], list[int]]
_USE_DUCKDB = True  # the SQL engine of the summary; the Python one is the fallback and the reference of the tests
_LINE_AT = "____-__-__T__:__:__.___Z"


def _compute(path: Path, start_today: str, start30: str) -> dict[str, Any]:
    folded = _read_folded(path.parent)
    fold_cut: str = folded.get("folded_before") or ""
    # every line goes to ONE bucket (0 older than the window, 1 window without today, 2 today); the periods are
    # sums of buckets: "all" = 0+1+2, "30 days" = 1+2, "today" = 2. Lines older than the fold are leftovers.
    acc: _Acc = {}
    for month in folded["months"].values():
        for model, agg in month.items():
            a = acc.setdefault((0, model), [0] * len(_FIELDS))
            for i, k in enumerate(_FIELDS):
                a[i] += int(agg.get(k, 0))
    since: str | None = folded.get("first_at")
    got = _scan_duckdb(path, fold_cut, start_today, start30, acc) if _USE_DUCKDB else None
    if got is None:
        got = _scan_python(path, fold_cut, start_today, start30, acc)
    if got is not None and (since is None or got < since):
        since = got

    def merge(buckets: tuple[int, ...]) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for (b, model), a in acc.items():
            if b in buckets:
                dst = out.setdefault(model, dict.fromkeys(_FIELDS, 0))
                for i, k in enumerate(_FIELDS):
                    dst[k] += a[i]
        return out

    return {
        "today": _period(merge((2,))),
        "days30": _period(merge((1, 2))),
        "all": _period(merge((0, 1, 2))),
        "prices_version": prices.PRICES_VERSION,
        "since": since,
    }


def _scan_duckdb(path: Path, fold_cut: str, start_today: str, start30: str, acc: _Acc) -> str | None:
    """Fast engine: DuckDB reads the file (10^5 lines in ~0.1 s against ~0.3 s of `json`). `None` — the engine is
    not usable for this file (missing, unreadable lines, odd `at`): the Python engine counts it, `acc` untouched."""
    if not path.exists():
        return None  # no journal yet: nothing to read, nothing to log
    try:
        import duckdb

        data = path.read_bytes()
        n_lines = data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)
        if n_lines == 0:
            return None
        quoted = path.as_posix().replace("'", "''")
        sql = f"""select model,
            case when "at" < '{fold_cut}' then -1 when "at" >= '{start_today}' then 2
                 when "at" >= '{start30}' then 1 else 0 end as b,
            count(*), sum(input), sum(output), sum(cache_read), sum(cache_write),
            sum(round(cost_usd * 1e9)::bigint), count(*) filter (where cost_usd is null), min("at"),
            sum(case when "at" is null or length("at") <> 24 or "at" not like '{_LINE_AT}' then 1 else 0 end)
            from read_ndjson('{quoted}', columns={{"at": 'VARCHAR', model: 'VARCHAR', input: 'BIGINT',
                 output: 'BIGINT', cache_read: 'BIGINT', cache_write: 'BIGINT', cost_usd: 'DOUBLE'}},
                 ignore_errors = true)
            group by 1, 2"""
        con = duckdb.connect()
        try:
            rows = con.execute(sql).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — any engine problem means "count it the slow, sure way"
        log.warning("assistant usage: SQL engine failed, the Python one is used", exc_info=True)
        return None
    if sum(r[2] for r in rows) != n_lines or any(r[10] for r in rows):
        return None  # a torn, blank or hand-edited line: only the Python engine tells what to skip
    canon: dict[str, str] = {}
    first: str | None = None
    add: _Acc = {}
    for model, bucket, n, inp, out, cr, cw, nano, unpriced, at_min, _bad in rows:
        if bucket < 0:
            continue
        a = add.setdefault((bucket, _model_key(model, canon)), [0] * len(_FIELDS))
        for i, v in enumerate((n, inp, out, cr, cw, nano, unpriced)):
            a[i] += int(v or 0)
        if first is None or at_min < first:
            first = at_min
    for k, a in add.items():
        dst = acc.setdefault(k, [0] * len(_FIELDS))
        for i, v in enumerate(a):
            dst[i] += v
    return first


def _scan_python(path: Path, fold_cut: str, start_today: str, start30: str, acc: _Acc) -> str | None:
    """Reference engine: one `json` pass; an unreadable line (a torn tail) is not a call and not a zero."""
    first: str | None = None
    canon: dict[str, str] = {}
    skipped = 0
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None
    raw_decode = json.JSONDecoder().raw_decode
    for line in text.splitlines():
        try:
            rec, _ = raw_decode(line)
            at = rec["at"]
            if len(at) != 24 or at[-1] != "Z":
                at = _normalize_at(at)
                if at is None:
                    raise ValueError
            model = rec.get("model")
            key = canon.get(model)  # type: ignore[arg-type]
            if key is None:
                key = canon[model] = _model_key(model, canon)
        except (ValueError, KeyError, TypeError):
            if line.strip():
                skipped += 1
            continue
        if at < fold_cut:
            continue
        if first is None or at < first:
            first = at
        bucket = 2 if at >= start_today else 1 if at >= start30 else 0
        a = acc.get((bucket, key))
        if a is None:
            a = acc[(bucket, key)] = [0] * len(_FIELDS)
        a[0] += 1
        a[1] += rec.get("input") or 0
        a[2] += rec.get("output") or 0
        a[3] += rec.get("cache_read") or 0
        a[4] += rec.get("cache_write") or 0
        cost = rec.get("cost_usd")
        if cost is None:
            a[6] += 1
        else:
            a[5] += round(cost * _NANO)
    if skipped:
        log.warning("assistant usage: %d unreadable lines skipped", skipped)
    return first
