from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import stele.ui.instance as instance_module
from stele.ui.instance import InstanceCoordinator, _valid_launch_url


def test_second_instance_reads_primary_url_and_lock_recovers(tmp_path):
    first = InstanceCoordinator(tmp_path)
    second = InstanceCoordinator(tmp_path)
    third = InstanceCoordinator(tmp_path)
    launch_url = "http://127.0.0.1:49152/?token=private-token"
    try:
        assert first.try_acquire()
        first.publish(launch_url)
        if os.name != "nt":
            assert stat.S_IMODE(first.session_path.stat().st_mode) == 0o600

        assert not second.try_acquire()
        assert second.wait_for_url(timeout=0.1, poll_interval=0.01) == launch_url

        first.close()
        assert not first.session_path.exists()
        assert second.try_acquire()
        second.publish("http://127.0.0.1:49153/?token=next-token")

        assert not third.try_acquire()
        assert third.wait_for_url(timeout=0.1, poll_interval=0.01) == (
            "http://127.0.0.1:49153/?token=next-token"
        )
    finally:
        first.close()
        second.close()
        third.close()


def test_invalid_or_incomplete_session_is_not_reopened(tmp_path):
    primary = InstanceCoordinator(tmp_path)
    secondary = InstanceCoordinator(tmp_path)
    try:
        assert primary.try_acquire()
        primary.session_path.write_text(
            json.dumps({"url": "https://attacker.example/?token=nope"}),
            encoding="utf-8",
        )
        assert not secondary.try_acquire()
        assert secondary.wait_for_url(timeout=0.02, poll_interval=0.005) is None
    finally:
        primary.close()
        secondary.close()


def test_launch_url_validation_is_strictly_loopback_and_tokenized():
    assert _valid_launch_url("http://127.0.0.1:1234/?token=secret")
    assert _valid_launch_url("http://localhost:1234/?token=secret") is None
    assert _valid_launch_url("https://127.0.0.1:1234/?token=secret") is None
    assert _valid_launch_url("http://127.0.0.1:1234/") is None


def test_windows_lock_backend_locks_and_unlocks_first_byte(tmp_path, monkeypatch):
    calls: list[tuple[int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_NBLCK=1,
        LK_UNLCK=2,
        locking=lambda _descriptor, mode, size: calls.append((mode, size)),
    )
    monkeypatch.setattr(instance_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(instance_module, "msvcrt", fake_msvcrt, raising=False)

    lock_path = tmp_path / "windows.lock"
    with lock_path.open("w+b", buffering=0) as stream:
        assert instance_module._try_lock(stream)
        instance_module._unlock(stream)

    assert lock_path.read_bytes() == b"\0"
    assert calls == [(fake_msvcrt.LK_NBLCK, 1), (fake_msvcrt.LK_UNLCK, 1)]


def test_windows_lock_backend_reports_a_busy_lock(tmp_path, monkeypatch):
    def busy(*_):
        raise OSError(13, "Permission denied")

    fake_msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=busy)
    monkeypatch.setattr(instance_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(instance_module, "msvcrt", fake_msvcrt, raising=False)

    with (tmp_path / "windows.lock").open("w+b", buffering=0) as stream:
        assert not instance_module._try_lock(stream)
