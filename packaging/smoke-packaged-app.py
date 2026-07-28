#!/usr/bin/env python3
"""Smoke-test a frozen Stele app and its single-instance lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _read_session(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _quit(session: dict[str, Any]) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(session["url"])
    origin = f"{parsed.scheme}://{parsed.netloc}"
    token = urllib.parse.parse_qs(parsed.query)["token"][0]
    request = urllib.request.Request(
        f"{origin}/api/quit",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Origin": origin,
            "X-Stele-Token": token,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _wait_until_removed(path: Path, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return not path.exists()


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: smoke-packaged-app.py EXECUTABLE STELE_HOME", file=sys.stderr)
        return 2

    executable = Path(sys.argv[1]).resolve()
    home = Path(sys.argv[2]).resolve()
    session_path = home / "ui-session.json"
    doctor_path = home / "doctor.json"
    environment = os.environ.copy()
    environment.update(
        {
            "STELE_HOME": str(home),
            "STELE_SUPPRESS_BROWSER": "1",
        }
    )

    subprocess.run(
        [str(executable), "--smoke-test"],
        env=environment,
        check=True,
        timeout=120,
    )
    result = json.loads(doctor_path.read_text(encoding="utf-8"))
    failed = [check for check in result["checks"] if not check["ok"]]
    if failed:
        for check in failed:
            print(f"FAIL {check['name']}: {check['detail']}", file=sys.stderr)
        return 1
    print(f"Packaged diagnostics passed: Stele {result['stele_version']} on {result['platform']}")

    first: dict[str, Any] | None = None
    try:
        subprocess.run(
            [str(executable)],
            env=environment,
            check=True,
            timeout=30,
        )
        first = _read_session(session_path)

        subprocess.run(
            [str(executable)],
            env=environment,
            check=True,
            timeout=30,
        )
        second = _read_session(session_path)
        if second != first:
            raise RuntimeError("second packaged launch replaced the active server session")

        response = _quit(first)
        if response != {"status": "stopping"}:
            raise RuntimeError(f"packaged Quit returned an unexpected response: {response}")
        if not _wait_until_removed(session_path):
            raise RuntimeError("packaged Quit did not remove the active server session")
        print(f"Packaged lifecycle passed: one server at PID {first['pid']}, then clean Quit")
    finally:
        if session_path.exists():
            try:
                _quit(_read_session(session_path))
                _wait_until_removed(session_path)
            except Exception as exc:
                print(f"warning: could not stop packaged smoke server: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
