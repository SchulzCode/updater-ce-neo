"""The subprocess shield: children start hidden, requested windows are kept."""

import os

import pytest

from src import quiet_subprocess

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="console-window hiding is a Windows concern"
)

SW_HIDE = 0
STARTF_USESHOWWINDOW = 0x00000001


class _Recorder:
    def __init__(self):
        self.kwargs = None

    def __call__(self, _self, *args, **kwargs):
        self.kwargs = kwargs


def test_hidden_window_kwargs_ask_for_no_window():
    kwargs = quiet_subprocess.hidden_window_kwargs()
    assert kwargs["creationflags"] == quiet_subprocess.CREATE_NO_WINDOW
    info = kwargs["startupinfo"]
    assert info.dwFlags & STARTF_USESHOWWINDOW
    assert info.wShowWindow == SW_HIDE


def test_wrapped_popen_hides_the_child_by_default():
    """A plain spawn — how every helper in the app is launched — is hidden."""
    recorder = _Recorder()
    wrapped = quiet_subprocess._wrap_init(recorder)
    wrapped(object(), ["adb", "devices"])

    assert recorder.kwargs["creationflags"] & quiet_subprocess.CREATE_NO_WINDOW
    # CREATE_NO_WINDOW alone still flashes for a few console hosts, so the
    # wrapper must also ask for SW_HIDE.
    assert recorder.kwargs["startupinfo"].wShowWindow == SW_HIDE


def test_wrapped_popen_keeps_a_deliberate_window():
    recorder = _Recorder()
    wrapped = quiet_subprocess._wrap_init(recorder)
    wrapped(object(), ["cmd"], creationflags=quiet_subprocess._CREATE_NEW_CONSOLE)

    assert recorder.kwargs["creationflags"] == quiet_subprocess._CREATE_NEW_CONSOLE
    assert "startupinfo" not in recorder.kwargs


def test_wrapped_popen_keeps_a_callers_startupinfo():
    recorder = _Recorder()
    wrapped = quiet_subprocess._wrap_init(recorder)
    own = quiet_subprocess._hidden_startupinfo()
    wrapped(object(), ["cmd"], startupinfo=own)

    assert recorder.kwargs["startupinfo"] is own


def test_wrapping_twice_is_a_no_op():
    original = quiet_subprocess._wrap_init(_Recorder())
    assert quiet_subprocess._wrap_init(original)._innioasis_hidden is True

    fake_popen_init = quiet_subprocess._wrap_init(_Recorder())
    # install() must not double-wrap an already shielded Popen.
    assert getattr(fake_popen_init, "_innioasis_hidden", False)
