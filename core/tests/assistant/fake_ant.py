#!/usr/bin/env python3
"""A fake `ant` (Anthropic CLI) for the login tests and E2E (stage E23, ADR 0029 decisions 11 and 36).

Only `ant auth login --no-browser [--profile NAME]` is known (and `--version`): it prints a sign-in address, reads the
authorization code from stdin and, when the code is right, writes the profile the real `ant` writes into
`$ANTHROPIC_CONFIG_DIR` (SDK anthropic 2.6.0, `CredentialsFile`, `authentication.type = user_oauth`):
  configs/<profile>.json       {"version", "authentication": {"type": "user_oauth", "client_id"}, "organization_id",
                                "workspace_id", "base_url"?}                      (0644)
  credentials/<profile>.json   {"version", "type": "oauth_token", "access_token", "expires_at", "refresh_token"} (0600)
Never touches the network. Standard library only.

Behaviour through the environment (all optional):
  FAKE_ANT_CODE           the right code (default TEST-CODE-123456); a wrong code: message on stderr, exit 1, no files
  FAKE_ANT_MODE           ok (default) | no_url (no address, waits: the "30 s" case) | hang (address, then never reads
                          the code) | exit_early (error before the address, exit 3)
  FAKE_ANT_URL            the address printed (default https://console.anthropic.com/oauth/authorize?... + a state)
  FAKE_ANT_ORG_ID, FAKE_ANT_WORKSPACE_ID, FAKE_ANT_CLIENT_ID, FAKE_ANT_ACCESS_TOKEN, FAKE_ANT_REFRESH_TOKEN,
  FAKE_ANT_EXPIRES_IN (seconds, default 3600), FAKE_ANT_BASE_URL (written to the profile; for a mock on 127.0.0.1)
`python -m tests.assistant.fake_ant --make-launcher DIR` writes `DIR/ant` (a shell script; `ant.cmd` on Windows) that
runs this file with the current Python and prints its path: set it as `BF_ANT_PATH`.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
import uuid
from pathlib import Path

DEFAULT_CODE = "TEST-CODE-123456"
DEFAULTS = {
    "ORG_ID": "org_test0000000000",
    "WORKSPACE_ID": "wrkspc_test00000000",
    "CLIENT_ID": "00000000-0000-4000-8000-000000000001",
    "ACCESS_TOKEN": "sk-ant-oat01-test-access-000000000000",
    "REFRESH_TOKEN": "sk-ant-ort01-test-refresh-00000000000",
}


def _env(name: str) -> str:
    return os.environ.get(f"FAKE_ANT_{name}") or DEFAULTS[name]


def write_profile(config_dir: Path, profile: str) -> None:
    """The files of a finished login (the values come from FAKE_ANT_*)."""
    auth: dict[str, object] = {"type": "user_oauth", "client_id": _env("CLIENT_ID")}
    config: dict[str, object] = {
        "version": "1.0",
        "authentication": auth,
        "organization_id": _env("ORG_ID"),
        "workspace_id": _env("WORKSPACE_ID"),
    }
    if os.environ.get("FAKE_ANT_BASE_URL"):
        config["base_url"] = os.environ["FAKE_ANT_BASE_URL"]
    creds = {
        "version": "1.0", "type": "oauth_token", "access_token": _env("ACCESS_TOKEN"),
        "expires_at": int(time.time()) + int(os.environ.get("FAKE_ANT_EXPIRES_IN") or 3600),
        "refresh_token": _env("REFRESH_TOKEN"),
    }
    for sub, doc, mode in (("configs", config, 0o644), ("credentials", creds, 0o600)):
        folder = config_dir / sub
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{profile}.json"
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        path.chmod(mode)


def login(profile: str) -> int:
    config_dir = os.environ.get("ANTHROPIC_CONFIG_DIR")
    if not config_dir:
        print("Error: ANTHROPIC_CONFIG_DIR is not set", file=sys.stderr)
        return 1
    mode = os.environ.get("FAKE_ANT_MODE", "ok")
    if mode == "exit_early":
        print("Error: cannot start the login flow", file=sys.stderr)
        return 3
    if mode == "no_url":
        time.sleep(3600)
        return 1
    url = os.environ.get("FAKE_ANT_URL") or (
        f"https://console.anthropic.com/oauth/authorize?response_type=code&client_id={_env('CLIENT_ID')}"
        f"&state={uuid.uuid4().hex}&code_challenge_method=S256"
    )
    print(f"Open this address in a browser and sign in:\n\n{url}\n\nPaste the authorization code here: ", end="")
    sys.stdout.flush()
    if mode == "hang":
        time.sleep(3600)
        return 1
    code = sys.stdin.readline().strip()
    if not code or code != (os.environ.get("FAKE_ANT_CODE") or DEFAULT_CODE):
        print("\nError: invalid authorization code", file=sys.stderr)
        return 1
    write_profile(Path(config_dir), profile)
    print(f"\nLogged in (profile {profile}).", flush=True)
    return 0


def write_launcher(directory: Path) -> Path:
    """`directory/ant` that runs this file with the current Python (no executable bit needed on the source)."""
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        path = directory / "ant.cmd"
        path.write_text(f'@"{sys.executable}" "{__file__}" %*\r\n', encoding="utf-8")
    else:
        path = directory / "ant"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{__file__}" "$@"\n', encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--make-launcher"] and len(args) == 2:
        print(write_launcher(Path(args[1])))
        return 0
    if args == ["--version"]:
        print("ant version 2.9.0 (fake)")
        return 0
    if args[:2] == ["auth", "login"] and "--no-browser" in args:
        i = args.index("--profile") + 1 if "--profile" in args else 0
        return login(args[i] if 0 < i < len(args) else "default")
    print(f"ant (fake): unsupported arguments {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
