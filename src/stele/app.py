"""Entry point for the self-contained desktop application."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
import webbrowser
from importlib.resources import files
from pathlib import Path
from typing import TextIO

_SERVER_ARGUMENT = "--stele-server"
_SUPPRESS_BROWSER_ENVIRONMENT = "STELE_SUPPRESS_BROWSER"


def _redirect_output() -> TextIO | None:
    """Keep useful diagnostics when a windowed bundle has no terminal."""
    from stele.doctor import default_home

    try:
        log_dir = default_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = (log_dir / "stele-ui.log").open("a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    sys.stdout = stream
    sys.stderr = stream
    return stream


def _smoke_test() -> int:
    """Exercise packaged resources and native engines without starting a server."""
    import threading
    import urllib.request

    from stele.doctor import run_doctor, write_doctor_json
    from stele.ui.server import create_server

    html = files("stele.ui").joinpath("static", "app.html").read_bytes()
    if b"<!doctype html>" not in html[:100].lower():
        return 1

    try:
        server = create_server()
    except PermissionError:
        # Some build sandboxes prohibit even loopback sockets. Resource and
        # dependency checks still run there; release runners exercise HTTP.
        server = None
    if server is not None:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(server.launch_url, timeout=5) as response:
                served_html = response.read()
                status = response.status
            if status != 200 or b"<!doctype html>" not in served_html[:100].lower():
                return 1
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    result = run_doctor()
    write_doctor_json(result)
    if not result["ok"] and sys.stderr is not None:
        print(json.dumps(result, indent=2), file=sys.stderr)
    return 0 if result["ok"] else 1


def _server_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, _SERVER_ARGUMENT]
    return [sys.executable, "-m", "stele.app", _SERVER_ARGUMENT]


def _open_browser(launch_url: str) -> None:
    if os.environ.get(_SUPPRESS_BROWSER_ENVIRONMENT) != "1":
        webbrowser.open(launch_url)


def _spawn_server() -> subprocess.Popen[bytes]:
    if os.name == "nt":
        return subprocess.Popen(
            _server_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
    return subprocess.Popen(
        _server_command(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _run_server(home: Path) -> int:
    from stele.ui import serve
    from stele.ui.instance import InstanceCoordinator

    with InstanceCoordinator(home) as instance:
        if not instance.try_acquire():
            return 0
        return serve(
            open_browser=False,
            on_ready=lambda server: instance.publish(server.launch_url),
        )


def _launch_or_reopen(home: Path) -> int:
    from stele.ui.instance import InstanceCoordinator

    with InstanceCoordinator(home) as instance:
        if not instance.try_acquire():
            launch_url = instance.wait_for_url(timeout=15)
            if launch_url is not None:
                print(f"Stele is already running; reopening {launch_url}")
                _open_browser(launch_url)
                return 0
            # The worker may have exited during our startup wait. Claim the
            # released lock before deciding that a new worker is needed.
            if not instance.try_acquire():
                raise RuntimeError("Another Stele instance is starting but is not ready")

    worker = _spawn_server()
    instance = InstanceCoordinator(home)
    launch_url = instance.wait_for_url(timeout=15)
    if launch_url is None:
        return_code = worker.poll()
        detail = f" (server exited with status {return_code})" if return_code is not None else ""
        raise RuntimeError(f"Stele's local server did not become ready{detail}")
    _open_browser(launch_url)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    log = _redirect_output()
    try:
        if "--smoke-test" in args:
            return _smoke_test()

        from stele.doctor import default_home

        home = default_home()
        if _SERVER_ARGUMENT in args:
            return _run_server(home)
        return _launch_or_reopen(home)
    except Exception:
        if sys.stderr is not None:
            traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        if log is not None:
            log.flush()


if __name__ == "__main__":
    raise SystemExit(main())
