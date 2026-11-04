"""Sleep inhibition: the host is held awake exactly while a device task runs."""

from __future__ import annotations

import ctypes
import time
import types

import pytest

from src import sleep_inhibit


class _RecorderBackend:
    """A backend that records what the inhibitor asked for."""

    name = "recorder"

    def __init__(self, result: bool = True):
        self.engaged: list = []
        self.released = 0
        self.ensured = 0
        self.result = result

    def engage(self, reason: str) -> bool:
        self.engaged.append(reason)
        return self.result

    def disengage(self) -> None:
        self.released += 1

    def ensure(self) -> None:
        self.ensured += 1


class _FakeProc:
    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def inhibitor(monkeypatch):
    monkeypatch.delenv("INNIOASIS_NO_SLEEP_INHIBIT", raising=False)
    backend = _RecorderBackend()
    subject = sleep_inhibit.SleepInhibitor(backend=backend)
    # Keep the watchdog from running through every test: it is exercised by
    # ensure() below directly.
    monkeypatch.setattr(subject, "_start_watchdog", lambda: None)
    return subject, backend


def test_hold_is_idempotent_and_release_lets_go(inhibitor):
    subject, backend = inhibitor
    assert subject.hold("install session") is True
    assert backend.engaged == ["install session"]
    assert subject.is_active
    subject.hold("install session")
    assert backend.engaged == ["install session"], "a duplicate begin is still one hold"
    subject.release("install session")
    assert backend.released == 1
    assert not subject.is_active


def test_each_activity_releases_only_its_own_hold(inhibitor):
    subject, backend = inhibitor
    subject.hold("install session")
    subject.hold("themes:1")
    subject.release("themes:1")
    assert subject.is_active, "a themes stage finishing must not let a flash sleep"
    assert backend.released == 0
    subject.release("install session")
    assert backend.released == 1


def test_releasing_something_unheld_changes_nothing(inhibitor):
    subject, backend = inhibitor
    subject.release("themes:1")
    assert backend.released == 0
    assert not subject.is_active


def test_release_all_drops_every_hold(inhibitor):
    subject, backend = inhibitor
    subject.hold("install session")
    subject.hold("themes:1")
    subject.release(None)
    assert backend.released == 1
    assert not subject.is_active


def test_environment_switch_disables_inhibition(monkeypatch):
    monkeypatch.setenv("INNIOASIS_NO_SLEEP_INHIBIT", "1")
    backend = _RecorderBackend()
    subject = sleep_inhibit.SleepInhibitor(backend=backend)
    assert subject.hold("install session") is False
    assert backend.engaged == []
    assert not subject.is_active


def test_install_session_owns_the_hold(monkeypatch):
    from src import process_guard

    monkeypatch.delenv("INNIOASIS_NO_SLEEP_INHIBIT", raising=False)
    backend = _RecorderBackend()
    monkeypatch.setattr(
        sleep_inhibit, "_GLOBAL", sleep_inhibit.SleepInhibitor(backend=backend)
    )
    process_guard.begin_install_session(reason="test")
    assert sleep_inhibit.is_sleep_inhibited()
    process_guard.end_install_session(reason="test", sweep=False)
    assert not sleep_inhibit.is_sleep_inhibited()
    assert backend.released == 1


def test_themes_stage_holds_until_it_finishes(monkeypatch):
    from src.themes_journey import ThemesJourney

    monkeypatch.delenv("INNIOASIS_NO_SLEEP_INHIBIT", raising=False)
    backend = _RecorderBackend()
    monkeypatch.setattr(
        sleep_inhibit, "_GLOBAL", sleep_inhibit.SleepInhibitor(backend=backend)
    )
    journey = ThemesJourney()
    journey.begin("Y1", automatic=True)
    assert sleep_inhibit.is_sleep_inhibited()
    journey.finish()
    assert not sleep_inhibit.is_sleep_inhibited()
    assert backend.released == 1


def test_linux_prefers_systemd_and_covers_the_lid(monkeypatch):
    backend = sleep_inhibit._LinuxBackend()
    monkeypatch.setattr(
        sleep_inhibit.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "systemd-inhibit" else None,
    )
    spawned = []

    def _record(argv, **kwargs):
        spawned.append((argv, kwargs))
        return _FakeProc()

    monkeypatch.setattr(backend, "_spawn", _record)
    assert backend.engage("install session")
    argv, kwargs = spawned[0]
    assert argv[0] == "systemd-inhibit"
    assert "--what=idle:sleep:handle-lid-switch" in argv
    assert kwargs.get("posix_pdeathsig") is True


def test_linux_falls_back_to_a_session_inhibitor(monkeypatch):
    backend = sleep_inhibit._LinuxBackend()
    monkeypatch.setattr(
        sleep_inhibit.shutil,
        "which",
        lambda name: "/usr/bin/gnome-session-inhibit" if name == "gnome-session-inhibit" else None,
    )
    spawned = []

    def _record(argv, **kwargs):
        spawned.append(argv)
        return _FakeProc()

    monkeypatch.setattr(backend, "_spawn", _record)
    assert backend.engage("install session")
    assert spawned[0][0] == "gnome-session-inhibit"


def test_macos_caffeinate_exits_with_the_app(monkeypatch):
    backend = sleep_inhibit._MacBackend()

    def _no_iokit():
        raise RuntimeError("no iokit in this test")

    monkeypatch.setattr(sleep_inhibit._MacBackend, "_libraries", staticmethod(_no_iokit))
    spawned = []

    def _record(argv, **kwargs):
        spawned.append(argv)
        return _FakeProc()

    monkeypatch.setattr(backend, "_spawn", _record)
    assert backend.engage("install session")
    assert spawned[0][0] == "caffeinate"
    assert spawned[0][-2:] == ["-w", str(sleep_inhibit.os.getpid())]
    backend.disengage()
    assert backend._proc is None


def test_windows_fallback_holds_continuous_system_required(monkeypatch):
    flags = []

    class _Kernel32:
        @staticmethod
        def SetThreadExecutionState(value):
            flags.append(value)

    monkeypatch.setattr(
        ctypes, "windll", types.SimpleNamespace(kernel32=_Kernel32), raising=False
    )
    backend = sleep_inhibit._WindowsBackend()
    assert backend.engage("install session")
    for _ in range(200):
        if flags:
            break
        time.sleep(0.01)
    assert flags, "the fallback must arm the request on its own thread"
    assert flags[0] & sleep_inhibit.ES_CONTINUOUS
    assert flags[0] & sleep_inhibit.ES_SYSTEM_REQUIRED

    backend.disengage()
    for _ in range(200):
        if flags[-1] == sleep_inhibit.ES_CONTINUOUS:
            break
        time.sleep(0.01)
    assert flags[-1] == sleep_inhibit.ES_CONTINUOUS


def test_away_mode_is_not_requested_any_more():
    assert not hasattr(sleep_inhibit, "ES_AWAYMODE_REQUIRED"), (
        "away mode is unsupported on battery-powered laptops; requesting it "
        "made the whole call fail there, leaving the install unprotected"
    )
