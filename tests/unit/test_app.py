from __future__ import annotations

from types import SimpleNamespace

from stele import app
from stele.ui.instance import InstanceCoordinator


def test_launcher_reopens_existing_session_without_spawning(tmp_path, monkeypatch):
    primary = InstanceCoordinator(tmp_path)
    launch_url = "http://127.0.0.1:49152/?token=private-token"
    opened: list[str] = []
    try:
        assert primary.try_acquire()
        primary.publish(launch_url)
        monkeypatch.setattr(app.webbrowser, "open", opened.append)
        monkeypatch.setattr(
            app,
            "_spawn_server",
            lambda: (_ for _ in ()).throw(AssertionError("spawned a second server")),
        )

        assert app._launch_or_reopen(tmp_path) == 0
        assert opened == [launch_url]
    finally:
        primary.close()


def test_launcher_starts_worker_then_opens_published_session(tmp_path, monkeypatch):
    worker_instance: list[InstanceCoordinator] = []
    launch_url = "http://127.0.0.1:49153/?token=worker-token"
    opened: list[str] = []

    def spawn():
        instance = InstanceCoordinator(tmp_path)
        assert instance.try_acquire()
        instance.publish(launch_url)
        worker_instance.append(instance)
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(app, "_spawn_server", spawn)
    monkeypatch.setattr(app.webbrowser, "open", opened.append)
    try:
        assert app._launch_or_reopen(tmp_path) == 0
        assert opened == [launch_url]
    finally:
        for instance in worker_instance:
            instance.close()


def test_browser_can_be_suppressed_for_packaged_lifecycle_tests(monkeypatch):
    monkeypatch.setenv("STELE_SUPPRESS_BROWSER", "1")
    monkeypatch.setattr(
        app.webbrowser,
        "open",
        lambda _: (_ for _ in ()).throw(AssertionError("opened a browser")),
    )

    app._open_browser("http://127.0.0.1:49152/?token=private-token")
