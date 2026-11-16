"""Trial period of 30 days (ТЗ 5.7; ADR 0005 §6, full protection — Э24).

Marks {first, last} are kept in three places (HKCU on Windows, %APPDATA%, %LOCALAPPDATA%) with an
HMAC bound to the PC code. The earliest «first» and the latest «last» win; a clock set back before
«last» pauses the trial with a message. A tampered mark is ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

TRIAL_DAYS = 30
CLOCK_TOLERANCE = timedelta(hours=1)
_SECRET = b"bf2-trial-v1:3c1f6f0e9a"  # obfuscated and compiled with Cython on Э24
_REG_KEY = r"Software\BedrockForge\Trial"


def _sign(hwid: str, first: str, last: str) -> str:
    return hmac.new(_SECRET + hwid.encode(), f"{first}|{last}".encode(), hashlib.sha256).hexdigest()


def _parse(text: str, hwid: str) -> tuple[datetime, datetime] | None:
    try:
        d = json.loads(text)
        first, last, sig = str(d["first"]), str(d["last"]), str(d["sig"])
        if not hmac.compare_digest(sig, _sign(hwid, first, last)):
            return None
        return datetime.fromisoformat(first), datetime.fromisoformat(last)
    except (ValueError, KeyError, TypeError):
        return None


def _dump(hwid: str, first: datetime, last: datetime) -> str:
    f, la = first.isoformat(), last.isoformat()
    return json.dumps({"first": f, "last": la, "sig": _sign(hwid, f, la)})


@dataclass
class TrialStatus:
    first_run: datetime
    expires: datetime
    days_left: int
    clock_back: bool

    @property
    def active(self) -> bool:
        return self.days_left > 0 and not self.clock_back

    def as_dict(self) -> dict[str, object]:
        return {
            "first_run": self.first_run.isoformat(),
            "expires": self.expires.isoformat(),
            "days_left": self.days_left,
            "clock_back": self.clock_back,
            "active": self.active,
        }


class TrialStore:
    """Reads and writes the marks; `files` are %APPDATA% and %LOCALAPPDATA% locations."""

    def __init__(self, files: list[Path], hwid: str, use_registry: bool = sys.platform == "win32") -> None:
        self.files = files
        self.hwid = hwid
        self.use_registry = use_registry

    def _read_all(self) -> list[tuple[datetime, datetime]]:
        texts: list[str] = []
        for f in self.files:
            try:
                texts.append(f.read_text(encoding="utf-8"))
            except OSError:
                pass
        if sys.platform == "win32" and self.use_registry:
            try:
                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
                    texts.append(str(winreg.QueryValueEx(k, "Mark")[0]))
            except OSError:
                pass
        return [m for t in texts if (m := _parse(t, self.hwid))]

    def _write_all(self, first: datetime, last: datetime) -> None:
        text = _dump(self.hwid, first, last)
        for f in self.files:
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(f)
            except OSError:
                pass
        if sys.platform == "win32" and self.use_registry:
            try:
                import winreg

                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
                    winreg.SetValueEx(k, "Mark", 0, winreg.REG_SZ, text)
            except OSError:
                pass

    def check(self, now: datetime | None = None) -> TrialStatus:
        now = now or datetime.now(UTC)
        marks = self._read_all()
        first = min((m[0] for m in marks), default=now)
        last = max((m[1] for m in marks), default=now)
        clock_back = now + CLOCK_TOLERANCE < last
        if not clock_back:
            last = max(last, now)
        self._write_all(first, last)
        expires = first + timedelta(days=TRIAL_DAYS)
        days_left = max(0, (expires - now).days + (1 if (expires - now) % timedelta(days=1) else 0))
        return TrialStatus(
            first_run=first, expires=expires, days_left=min(days_left, TRIAL_DAYS), clock_back=clock_back
        )
