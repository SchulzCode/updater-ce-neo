"""A second copy must never run, must never end the first, and must never
block a legitimate launch.

The running copy owns the USB device and, through its shut-down sweep, the
flash tool a flash run is using. So the guard may only refuse — it must not
offer or perform a takeover. And because a false refusal would lock a user out
of their own app, every ambiguous case has to start the app anyway.
"""

import os
import types

import pytest

from src import single_instance


class _FakeLock:
    """Stands in for InstanceLock; the test decides every answer."""

    def __init__(self, acquired=False, pid=4242, unopenable=False, droppable=True):
        self._acquired = acquired
        self._pid = pid
        self._unopenable = unopenable
        self._droppable = droppable
        self.released = False
        self.dropped = 0
        self.acquire_calls = 0
        self._lock = types.SimpleNamespace(removeStaleLockFile=lambda: True)

    def acquire(self, timeout_ms=200):
        self.acquire_calls += 1
        return self._acquired

    def holder_pid(self):
        return self._pid

    def unopenable(self):
        return self._unopenable

    def drop(self):
        self.dropped += 1
        return self._droppable

    def release(self):
        self.released = True


class _App(types.SimpleNamespace):
    def __init__(self):
        super().__init__(aboutToQuit=types.SimpleNamespace(connect=lambda fn: None))


@pytest.fixture
def app():
    return _App()


@pytest.fixture
def free_mutex(monkeypatch):
    monkeypatch.setattr(single_instance, "acquire_windows_mutex", lambda: True)


@pytest.fixture
def own_process(monkeypatch):
    """The holder pid is alive and is our own program."""
    monkeypatch.setattr(single_instance, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(single_instance, "_pid_identity", lambda pid: "innioasisupdater.exe")


def test_there_is_no_takeover_path():
    """The old force-quit route is gone: nothing may end the running copy."""
    assert not hasattr(single_instance, "_ask_force_quit")
    assert not hasattr(single_instance, "terminate_pid")


def test_second_launch_is_refused_and_only_asks_for_the_window(
    monkeypatch, free_mutex, own_process, app
):
    seen = {"focus": 0, "dialog": 0}
    monkeypatch.setattr(
        single_instance,
        "request_focus",
        lambda *a, **k: seen.__setitem__("focus", seen["focus"] + 1) or True,
    )
    monkeypatch.setattr(
        single_instance,
        "_explain_already_running",
        lambda *a, **k: seen.__setitem__("dialog", seen["dialog"] + 1),
    )

    assert single_instance.ensure_single_instance(app, _FakeLock()) is False
    assert seen == {"focus": 1, "dialog": 0}
    assert not hasattr(app, "_instance_lock")


def test_refusal_explains_itself_when_the_window_cannot_be_raised(
    monkeypatch, free_mutex, own_process, app
):
    seen = []
    monkeypatch.setattr(single_instance, "request_focus", lambda *a, **k: False)
    monkeypatch.setattr(
        single_instance,
        "_explain_already_running",
        lambda focus_failed=False: seen.append(focus_failed),
    )

    assert single_instance.ensure_single_instance(app, _FakeLock()) is False
    assert seen == [True]


def test_the_only_copy_takes_the_lock(free_mutex, app):
    lock = _FakeLock(acquired=True)
    assert single_instance.ensure_single_instance(app, lock) is True
    assert app._instance_lock is lock


def test_a_dead_holder_is_cleared_and_collected(monkeypatch, free_mutex, app):
    """A holder pid that is gone must not make the app unlaunchable."""

    class _OnceStale(_FakeLock):
        def acquire(self, timeout_ms=200):
            self.acquire_calls += 1
            return self.acquire_calls > 1

    monkeypatch.setattr(single_instance, "_pid_alive", lambda pid: False)
    lock = _OnceStale()

    assert single_instance.ensure_single_instance(app, lock) is True
    assert app._instance_lock is lock


def test_a_recycled_pid_does_not_block_launch(monkeypatch, free_mutex, app):
    """The lock names a live pid, but that pid is now some other program."""
    monkeypatch.setattr(single_instance, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(single_instance, "_pid_identity", lambda pid: "explorer.exe")

    assert single_instance.ensure_single_instance(app, _FakeLock()) is True


def test_an_unreadable_holder_identity_never_blocks(monkeypatch, free_mutex, app):
    """A permission boundary must not turn into a refusal."""
    monkeypatch.setattr(single_instance, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(single_instance, "_pid_identity", lambda pid: "")
    monkeypatch.setattr(single_instance, "request_focus", lambda *a, **k: True)

    assert single_instance.ensure_single_instance(app, _FakeLock()) is False


def test_an_unusable_lock_file_starts_the_app(free_mutex, app):
    """Read-only temp, no rights: the guard stands aside."""
    lock = _FakeLock(acquired=False, unopenable=True)
    assert single_instance.ensure_single_instance(app, lock) is True


def test_a_lock_that_cannot_be_cleared_still_starts_the_app(
    monkeypatch, free_mutex, app
):
    monkeypatch.setattr(single_instance, "_pid_alive", lambda pid: False)
    lock = _FakeLock(acquired=False, droppable=False)

    assert single_instance.ensure_single_instance(app, lock) is True


def test_a_broken_guard_starts_the_app(monkeypatch, app):
    def _boom(*a, **k):
        raise RuntimeError("guard exploded")

    monkeypatch.setattr(single_instance, "acquire_windows_mutex", _boom)
    assert single_instance.ensure_single_instance(app) is True


def test_a_held_mutex_stops_the_launch_before_any_lock_file_work(
    monkeypatch, app
):
    monkeypatch.setattr(single_instance, "acquire_windows_mutex", lambda: False)
    monkeypatch.setattr(single_instance, "request_focus", lambda *a, **k: True)
    explained = []
    monkeypatch.setattr(
        single_instance,
        "_explain_already_running",
        lambda focus_failed=False: explained.append(focus_failed),
    )

    lock = _FakeLock(acquired=True)  # must not even be consulted
    assert single_instance.ensure_single_instance(app, lock) is False
    assert lock.acquire_calls == 0
    assert explained == [False]


def test_mutex_is_session_scoped_and_idempotent():
    assert single_instance._ERROR_ALREADY_EXISTS == 183
    assert single_instance.WINDOWS_MUTEX_NAME.startswith("Local\\")
    if os.name != "nt":
        assert single_instance.acquire_windows_mutex() is True
        return
    first = single_instance.acquire_windows_mutex()
    assert single_instance.acquire_windows_mutex() is first


def test_lock_and_channel_are_per_user():
    """A shared temp directory must not hand one account's lock to another."""
    token = single_instance._user_token()
    assert token
    assert token in single_instance.lock_name()
    assert token in single_instance.focus_server_name()
    assert single_instance.lock_path().name == single_instance.lock_name()


def test_focus_connection_is_liveness_proof_when_unavailable():
    """No server listening here: request_focus reports "nothing to raise"."""
    assert single_instance.request_focus(timeout_ms=50) is False
