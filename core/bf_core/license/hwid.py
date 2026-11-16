"""PC code (HWID) for the licence (ТЗ 5.7, ADR 0005 §6): three components hashed separately.

Components: MachineGuid, serial number of the system volume, processor identifier. A licence matches
when at least 2 of 3 agree. No WMI and no child processes (winreg, ctypes). Linux fallbacks exist only
for development and CI.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass

PART_LEN = 8


def _h(value: str) -> str:
    return hashlib.sha256(("bf2:" + value).encode("utf-8")).hexdigest()[:PART_LEN].upper()


def _machine_guid() -> str:
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as k:
            return str(winreg.QueryValueEx(k, "MachineGuid")[0])
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p, encoding="ascii") as f:
                return f.read().strip()
        except OSError:
            continue
    return ""


def _volume_serial() -> str:
    if sys.platform == "win32":
        import ctypes

        root = (os.environ.get("SystemDrive") or "C:") + "\\"
        serial = ctypes.c_uint32(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root), None, 0, ctypes.byref(serial), None, None, None, 0
        )
        return f"{serial.value:08X}" if ok else ""
    try:
        return str(os.stat("/").st_dev)
    except OSError:
        return ""


def _cpu_id() -> str:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
                ident = str(winreg.QueryValueEx(k, "Identifier")[0]).strip()
                return f"{name}|{ident}"
        except OSError:
            return ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            fields = dict(line.split(":", 1) for line in f if ":" in line)
        return "|".join(fields.get(k, "").strip() for k in ("model name", "vendor_id", "cpu family", "model"))
    except OSError:
        return ""


@dataclass(frozen=True)
class Hwid:
    parts: tuple[str, str, str]

    @property
    def code(self) -> str:
        """Shown to the user and sent to get a licence: «A1B2C3D4-…-…»."""
        return "-".join(self.parts)

    def matches(self, code: str) -> bool:
        other = code.strip().upper().split("-")
        if len(other) != 3:
            return False
        return sum(a == b for a, b in zip(self.parts, other, strict=True)) >= 2


def current_hwid() -> Hwid:
    raw = []
    for fn in (_machine_guid, _volume_serial, _cpu_id):
        try:
            raw.append(fn())
        except OSError:
            raw.append("")
    return Hwid(tuple(_h(v) if v else "0" * PART_LEN for v in raw))  # type: ignore[arg-type]
