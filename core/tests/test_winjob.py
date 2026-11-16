"""Job Object: a child of the core dies when the core dies (Windows only)."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from bf_core.winjob import kill_children_with_me

PARENT = r"""
import subprocess, sys, time
from bf_core.winjob import kill_children_with_me
ok = kill_children_with_me()
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(ok, child.pid, flush=True)
time.sleep(120)
"""


def test_noop_off_windows() -> None:
    if sys.platform != "win32":
        assert kill_children_with_me() is False


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_child_killed_with_core() -> None:
    import ctypes

    parent = subprocess.Popen([sys.executable, "-c", PARENT], stdout=subprocess.PIPE, text=True)
    assert parent.stdout is not None
    ok, pid = parent.stdout.readline().split()
    assert ok == "True"
    parent.kill()  # hard kill: no cleanup code runs in the parent
    parent.wait(timeout=10)
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    deadline = time.monotonic() + 10
    alive = True
    while time.monotonic() < deadline:
        h = k32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not h:
            alive = False
            break
        still = k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT
        k32.CloseHandle(h)
        if not still:
            alive = False
            break
        time.sleep(0.2)
    assert not alive, "child survived its parent"
