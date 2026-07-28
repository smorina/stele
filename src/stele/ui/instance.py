"""Per-user single-instance coordination for the desktop launcher."""

from __future__ import annotations

import errno
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import IO

from stele import __version__

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    import msvcrt
else:
    import fcntl


def _try_lock(stream: IO[bytes]) -> bool:
    if _IS_WINDOWS:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(stream: IO[bytes]) -> None:
    if _IS_WINDOWS:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _valid_launch_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.path != "/"
        or not urllib.parse.parse_qs(parsed.query).get("token")
    ):
        return None
    return value


class InstanceCoordinator:
    """Use an OS-held lock as authority for the private active-session file."""

    def __init__(self, home: str | Path):
        self.home = Path(home)
        self.lock_path = self.home / "ui.lock"
        self.session_path = self.home / "ui-session.json"
        self._lock_file: IO[bytes] | None = None
        self._owns_lock = False

    def try_acquire(self) -> bool:
        if self._owns_lock:
            return True
        self.home.mkdir(parents=True, exist_ok=True)
        if self._lock_file is None:
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.lock_path, flags, 0o600)
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            self._lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        if not _try_lock(self._lock_file):
            return False
        self._owns_lock = True
        self.session_path.unlink(missing_ok=True)
        return True

    def publish(self, launch_url: str) -> None:
        if not self._owns_lock:
            raise RuntimeError("Only the primary Stele instance can publish its session")
        if _valid_launch_url(launch_url) is None:
            raise ValueError("Stele launch URL must be a tokenized 127.0.0.1 address")
        payload = {
            "pid": os.getpid(),
            "url": launch_url,
            "stele_version": __version__,
        }
        temporary = self.session_path.with_name(
            f".{self.session_path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.session_path)
        finally:
            temporary.unlink(missing_ok=True)

    def wait_for_url(self, timeout: float = 5.0, poll_interval: float = 0.05) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                payload = json.loads(self.session_path.read_text(encoding="utf-8"))
                launch_url = _valid_launch_url(payload.get("url"))
                if launch_url is not None:
                    return launch_url
            except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
                pass
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_interval)

    def close(self) -> None:
        if self._owns_lock:
            self.session_path.unlink(missing_ok=True)
            if self._lock_file is not None:
                _unlock(self._lock_file)
            self._owns_lock = False
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def __enter__(self) -> InstanceCoordinator:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
