"""User profile locations (ТЗ 6.5). No third-party imports: used before numba loads."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "BedrockForge"


def _portable_root() -> Path | None:
    """Portable mode: flag file BedrockForge.portable next to the executable (Э24)."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (exe_dir, exe_dir.parent, exe_dir.parent.parent):
            if (candidate / f"{APP_DIR_NAME}.portable").exists():
                return candidate / "data"
    return None


def local_app_dir() -> Path:
    """%LOCALAPPDATA%\\BedrockForge (Windows) or $XDG_CACHE_HOME/BedrockForge."""
    override = os.environ.get("BF_LOCAL_DIR")
    if override:
        return Path(override)
    portable = _portable_root()
    if portable is not None:
        return portable / "local"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_DIR_NAME


def cache_dir() -> Path:
    return local_app_dir() / "cache"


def logs_dir() -> Path:
    return local_app_dir() / "logs"


def configure_numba_cache() -> Path:
    """Point numba's on-disk cache to the profile unless NUMBA_CACHE_DIR is set."""
    if not os.environ.get("NUMBA_CACHE_DIR"):
        os.environ["NUMBA_CACHE_DIR"] = str(cache_dir() / "numba")
    path = Path(os.environ["NUMBA_CACHE_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def instance_db() -> Path:
    """Jobs without a project and the cache of the project list (ADR 0002 §6); deleted without loss."""
    return local_app_dir() / "state" / "instance.sqlite"


def default_projects_dir() -> Path:
    """Документы\\BedrockForge\\Проекты (ТЗ 6.5); the shell passes the real Documents folder with --projects."""
    portable = _portable_root()
    if portable is not None:
        return portable / "Проекты"
    return Path.home() / "Documents" / APP_DIR_NAME / "Проекты"
