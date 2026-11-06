"""A second copy must focus the running copy and stop, never run beside it.

The running copy owns the USB device and, through its shut-down sweep, the
flash tool a flash run is using. A copy that answers the focus request is
left alone and this launch exits. Only a copy that does not answer may be
ended, behind an explicit force-quit answer. And because a false refusal
would lock a user out of their own app, every ambiguous case has to start
the app anyway.
"""

import ctypes
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


@pytest.fixture(autouse=True)
def isolated_show_request(tmp_path, monkeypatch):
    """Keep the marker out of the real profile; tests never wait on a live app."""
    request = tmp_path / "show.request"
    monkeypatch.setattr(single_instance, "show_request_path", lambda: request)
    monkeypatch.setattr(single_instance, "SHOW_REQUEST_TIMEOUT_S", 0.0)
    single_instance.clear_show_request()
    yield
    single_instance.clear_show_request()


@pytest.fixture(autouse=True)
def no_running_copy(monkeypatch):
    """Keep the guard's process scan away from copies running on this host.

    ``find_running_copy_pids`` is exercised on its own; every test here decides
    the lock's answer explicitly and must not be influenced by a real app the
    developer has open.
    """
    monkeypatch.setattr(single_instance, "find_running_copy_pids", lambda: [])


def test_a_folder_name_in_another_programs_argv_is_not_a_copy():
    """Linux saw a second copy in every editor opened on the project folder."""
    assert not single_instance._copy_pid_from_command(
        4242, "/usr/bin/code /home/user/updater-ce"
    )
    assert not single_instance._copy_pid_from_command(
        4242, "/usr/bin/python3 -m http.server --directory /home/user/updater-ce"
    )
    assert single_instance._copy_pid_from_command(
        4242, "/usr/bin/python3 /home/user/updater-ce/src/app.py"
    )
    assert single_instance._copy_pid_from_command(
        4242, "/opt/InnioasisUpdater/InnioasisUpdater"
    )


def test_a_responsive_copy_is_focused_and_never_ended(
    monkeypatch, free_mutex, own_process, app
):
    """The running window came forward, so this launch just exits."""
    monkeypatch.setattr(
        single_instance,
        "end_running_copy",
        lambda pid: pytest.fail("a responsive copy must never be ended"),
    )
    monkeypatch.setattr(single_instance, "request_focus", lambda *a, **k: True)

    assert single_instance.ensure_single_instance(app, _FakeLock()) is False
    assert not hasattr(app, "_instance_lock")


def test_the_unanswered_copy_is_only_ended_when_the_user_asks(
    monkeypatch, free_mutex, own_process, app
):
    """Force quit takes the lock back, and only after the dialog says yes."""
    ended = []

    class _AfterKill(_FakeLock):
        def acquire(self, timeout_ms=200):
            self.acquire_calls += 1
            return self.acquire_calls > 1

    monkeypatch.setattr(
        single_instance, "_focus_running_copy", lambda *a, **k: False
    )
    monkeypatch.setattr(
        single_instance, "_explain_already_running", lambda **k: "force"
    )
    monkeypatch.setattr(
        single_instance, "end_running_copy", lambda pid: ended.append(pid) or True
    )

    lock = _AfterKill()
    assert single_instance.ensure_single_instance(app, lock) is True
    assert ended == [4242]
    assert app._instance_lock is lock


def test_open_anyway_starts_beside_an_unanswered_copy(
    monkeypatch, free_mutex, own_process, app
):
    """The dialog offers a way in even when the other copy cannot be ended."""
    monkeypatch.setattr(
        single_instance, "_focus_running_copy", lambda *a, **k: False
    )
    monkeypatch.setattr(
        single_instance, "_explain_already_running", lambda **k: "anyway"
    )
    monkeypatch.setattr(
        single_instance,
        "end_running_copy",
        lambda pid: pytest.fail("Open App Anyway must not end the other copy"),
    )

    assert single_instance.ensure_single_instance(app, _FakeLock()) is True
    assert not hasattr(app, "_instance_lock")


def test_a_failed_force_quit_can_still_open_the_app(
    monkeypatch, free_mutex, own_process, app
):
    """When the end really fails, the user is not locked out by the guard."""
    monkeypatch.setattr(
        single_instance, "_focus_running_copy", lambda *a, **k: False
    )
    monkeypatch.setattr(
        single_instance, "_explain_already_running", lambda **k: "force"
    )
    monkeypatch.setattr(single_instance, "end_running_copy", lambda pid: False)
    monkeypatch.setattr(single_instance, "_explain_force_failed", lambda pid=0: True)

    assert single_instance.ensure_single_instance(app, _FakeLock()) is True
    assert not hasattr(app, "_instance_lock")


def test_a_failed_force_quit_can_still_be_declined(
    monkeypatch, free_mutex, own_process, app
):
    monkeypatch.setattr(
        single_instance, "_focus_running_copy", lambda *a, **k: False
    )
    monkeypatch.setattr(
        single_instance, "_explain_already_running", lambda **k: "force"
    )
    monkeypatch.setattr(single_instance, "end_running_copy", lambda pid: False)
    monkeypatch.setattr(single_instance, "_explain_force_failed", lambda pid=0: False)

    assert single_instance.ensure_single_instance(app, _FakeLock()) is False


def test_a_declined_force_quit_leaves_the_running_copy_alone(
    monkeypatch, free_mutex, own_process, app
):
    monkeypatch.setattr(
        single_instance, "_focus_running_copy", lambda *a, **k: False
    )
    monkeypatch.setattr(
        single_instance, "_explain_already_running", lambda **k: ""
    )
    monkeypatch.setattr(
        single_instance,
        "end_running_copy",
        lambda pid: pytest.fail("a declined takeover must not end anything"),
    )

    assert single_instance.ensure_single_instance(app, _FakeLock()) is False


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
        lambda focus_failed=False, pid=0: seen.append(focus_failed),
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
        lambda focus_failed=False, pid=0: explained.append(focus_failed),
    )

    lock = _FakeLock(acquired=True)  # must not even be consulted
    assert single_instance.ensure_single_instance(app, lock) is False
    assert lock.acquire_calls == 0
    # The window came forward: the second launch leaves without a dialog.
    assert explained == []


def test_mutex_is_session_scoped_and_idempotent():
    assert single_instance._ERROR_ALREADY_EXISTS == 183
    assert single_instance.WINDOWS_MUTEX_NAME.startswith("Local\\")
    if os.name != "nt":
        assert single_instance.acquire_windows_mutex() is True
        return
    first = single_instance.acquire_windows_mutex()
    assert single_instance.acquire_windows_mutex() is first


def test_a_take_over_launch_waits_for_the_old_copy(
    monkeypatch, free_mutex, app
):
    """The elevated restart starts before the old copy quits, so it waits."""
    waits = []
    monkeypatch.setattr(single_instance, "_take_over_requested", lambda: True)
    monkeypatch.setattr(
        single_instance, "_wait_for_previous_copy", lambda *a, **k: waits.append(True)
    )

    assert single_instance.ensure_single_instance(app, _FakeLock(acquired=True)) is True
    assert waits == [True]


def test_a_plain_launch_does_not_wait(monkeypatch, free_mutex, app):
    waits = []
    monkeypatch.setattr(single_instance, "_take_over_requested", lambda: False)
    monkeypatch.setattr(
        single_instance, "_wait_for_previous_copy", lambda *a, **k: waits.append(True)
    )

    assert single_instance.ensure_single_instance(app, _FakeLock(acquired=True)) is True
    assert waits == []


def test_the_take_over_flag_comes_from_the_command_line(monkeypatch):
    monkeypatch.setattr(
        single_instance.sys, "argv", ["app.exe", single_instance.TAKE_OVER_ARG]
    )
    assert single_instance._take_over_requested() is True
    monkeypatch.setattr(single_instance.sys, "argv", ["app.exe", "--tray"])
    assert single_instance._take_over_requested() is False


def test_named_mutex_reports_an_existing_copy():
    """The mutex tier must notice ERROR_ALREADY_EXISTS on a real second create.

    ``ctypes.get_last_error()`` returns 0 unless kernel32 was loaded with
    ``use_last_error=True``; loaded the plain way the guard silently passed, so
    this pins the error shadow as well as the handle.
    """
    if os.name != "nt":
        pytest.skip("named mutexes are Windows-only")
    name = "Local\\updater-ce-test-only-mutex"
    handle, existed = single_instance._create_named_mutex(name)
    assert handle and not existed
    second, existed_again = single_instance._create_named_mutex(name)
    assert second and existed_again
    kernel32 = single_instance._win_kernel32()
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    kernel32.CloseHandle(ctypes.c_void_p(second))


def test_lock_and_channel_are_per_user():
    """A shared temp directory must not hand one account's lock to another."""
    token = single_instance._user_token()
    assert token
    assert token in single_instance.lock_name()
    assert token in single_instance.focus_server_name()
    assert single_instance.lock_path().name == single_instance.lock_name()


def test_focus_connection_is_liveness_proof_when_unavailable(monkeypatch):
    """No server listening here: request_focus reports "nothing to raise".

    The name is unique to this test: the real app answers its own channel
    whenever it is running, and that is not this test's subject.
    """
    monkeypatch.setattr(
        single_instance, "focus_server_name", lambda: "updater-ce-test-nobody"
    )
    assert single_instance.request_focus(timeout_ms=50) is False


def test_a_marker_acknowledgement_counts_as_the_window_coming_up(
    monkeypatch, free_mutex, own_process, app
):
    monkeypatch.setattr(single_instance, "_focus_running_copy", lambda *a, **k: False)
    monkeypatch.setattr(single_instance, "_focus_running_window", lambda *a, **k: False)
    monkeypatch.setattr(single_instance, "wait_for_show_request", lambda *a, **k: True)
    monkeypatch.setattr(
        single_instance,
        "_explain_already_running",
        lambda *a, **k: pytest.fail("an acknowledged show must not offer a takeover"),
    )

    assert single_instance.ensure_single_instance(app, _FakeLock()) is False
