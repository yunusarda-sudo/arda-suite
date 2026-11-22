from __future__ import annotations

import signal
import threading

from arda import bridge_worker


class FakeControlPlane:
    def __init__(self, db_path):
        self.db_path = db_path

    def dispatch_next_task(self, project_id):
        return None


def test_watch_drains_current_run_then_stops_without_another_cycle(monkeypatch):
    stop_event = threading.Event()
    calls = []

    class FakeBridge:
        db_path = "/unused/core.sqlite3"

        def run_once(self):
            calls.append("run")
            stop_event.set()

    monkeypatch.setattr(bridge_worker, "ControlPlane", FakeControlPlane)
    bridge_worker.watch(
        FakeBridge(), poll_seconds=60, autopilot_projects=("project",), stop_event=stop_event
    )

    assert calls == ["run"]


def test_watch_does_not_claim_work_if_stop_arrives_during_dispatch(monkeypatch):
    stop_event = threading.Event()
    calls = []
    dispatched_projects = []

    class StoppingControlPlane(FakeControlPlane):
        def dispatch_next_task(self, project_id):
            dispatched_projects.append(project_id)
            stop_event.set()

    class FakeBridge:
        db_path = "/unused/core.sqlite3"

        def run_once(self):
            calls.append("run")

    monkeypatch.setattr(bridge_worker, "ControlPlane", StoppingControlPlane)
    bridge_worker.watch(
        FakeBridge(), poll_seconds=60, autopilot_projects=("first", "second"), stop_event=stop_event
    )

    assert calls == []
    assert dispatched_projects == ["first"]


def test_main_maps_sigterm_and_sigint_to_stop_event(monkeypatch):
    handlers = {}

    def fake_signal(signum, handler):
        previous = handlers.get(signum, signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    monkeypatch.setattr(bridge_worker.signal, "signal", fake_signal)
    monkeypatch.setattr(bridge_worker, "load_factory", lambda _spec: object())

    observed = {}

    def fake_watch(_bridge, *, poll_seconds, autopilot_projects, stop_event):
        observed["poll_seconds"] = poll_seconds
        observed["projects"] = autopilot_projects
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        observed["stopped"] = stop_event.is_set()

    monkeypatch.setattr(bridge_worker, "watch", fake_watch)
    result = bridge_worker.main(["--executor-factory", "test:factory", "--poll-seconds", "0.25"])

    assert result == 0
    assert observed == {"poll_seconds": 0.25, "projects": (), "stopped": True}
    assert handlers[signal.SIGTERM] is signal.SIG_DFL
    assert handlers[signal.SIGINT] is signal.SIG_DFL
