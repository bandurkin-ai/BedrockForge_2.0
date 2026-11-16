"""Entry point: bf-core --port 0 --token <t> --data <dir> | --selftest."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="bf-core", description="BedrockForge 2 compute core")
    p.add_argument("--port", type=int, default=0, help="0 = random free port")
    p.add_argument("--token", default=os.environ.get("BF_TOKEN", ""), help="launch token (X-BF-Token)")
    p.add_argument("--data", default=None, help="user data directory")
    p.add_argument("--projects", default=os.environ.get("BF_PROJECTS_DIR"), help="folder of projects")
    p.add_argument("--mode", default=os.environ.get("BF_MODE", "desktop"), choices=["desktop", "server"])
    p.add_argument("--dev-origin", action="append", default=[], help="extra allowed Origin (dev only)")
    p.add_argument("--watch-stdin", action="store_true", help="exit when stdin closes (parent shell died)")
    p.add_argument("--selftest", action="store_true", help="check native dependencies and exit")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    from bf_core.paths import configure_numba_cache

    configure_numba_cache()
    args = parse_args(argv)
    if args.selftest:
        from bf_core.diag.selftest import run_selftest

        report = run_selftest()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if not args.token:
        print("bf-core: --token is required", file=sys.stderr)
        return 2
    return serve(args)


def _watch_parent(request_shutdown) -> None:  # type: ignore[no-untyped-def]
    """The shell keeps our stdin open; EOF (or the shell process gone) means stop, then force-exit.

    Windows: no blocking read of stdin. A synchronous ReadFile pending on the pipe makes every other
    operation on that file object wait — including the start of a `spawn` worker, which gets the std
    handles and hangs in its initialisation. Instead the pipe is peeked and the shell process is checked
    once a second.
    """
    import threading
    import time

    def stop() -> None:
        request_shutdown()
        time.sleep(10)
        os._exit(0)

    def run_posix() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except (OSError, ValueError):
            pass
        stop()

    def run_windows() -> None:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.PeekNamedPipe.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        k32.PeekNamedPipe.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        try:
            pipe = msvcrt.get_osfhandle(sys.stdin.fileno())  # type: ignore[attr-defined]
        except (OSError, ValueError, AttributeError):
            pipe = None
        parent = k32.OpenProcess(0x00100000, False, os.getppid())  # SYNCHRONIZE
        avail = wintypes.DWORD(0)
        while True:
            time.sleep(1.0)
            if pipe is not None:
                if not k32.PeekNamedPipe(pipe, None, 0, None, ctypes.byref(avail), None):
                    break  # ERROR_BROKEN_PIPE: the shell closed our stdin
                if avail.value:
                    try:
                        os.read(sys.stdin.fileno(), avail.value)  # data is there: the read returns at once
                    except OSError:
                        break
            if parent and k32.WaitForSingleObject(parent, 0) == 0:
                break  # the shell process is gone
        stop()

    target = run_windows if sys.platform == "win32" else run_posix
    threading.Thread(target=target, name="parent-watch", daemon=True).start()


def serve(args: argparse.Namespace) -> int:
    import logging

    import uvicorn

    # Messages of the core modules go to stderr: the shell writes them into core.log («core!» lines).
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("bf_core")
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    from bf_core.winjob import kill_children_with_me

    kill_children_with_me()

    import bf_core
    from bf_core.api.app import CoreState, create_app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
    else:
        # Restart on the same port right after a crash (TIME_WAIT); on Windows this flag would allow port theft.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]

    state = CoreState(token=args.token, mode=args.mode, data_dir=args.data, port=port, projects_dir=args.projects)
    app = create_app(state, extra_origins=tuple(args.dev_origin))
    config = uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on", timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)

    def request_shutdown() -> None:
        state.events.close()  # open SSE streams would hold uvicorn's graceful shutdown for 5 s
        server.should_exit = True

    state.request_shutdown = request_shutdown

    if args.watch_stdin:
        _watch_parent(request_shutdown)

    import asyncio

    async def run() -> None:
        sock.listen(128)
        serve_task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started and not serve_task.done():
            await asyncio.sleep(0.01)
        if server.started:
            print(f"BF-READY port={port} version={bf_core.__version__}", flush=True)
        await serve_task

    asyncio.run(run())
    if args.watch_stdin:
        # The stdin watcher thread may still block in read(); leave without interpreter finalization.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
