"""Keep helper processes from opening console windows on Windows.

The app is a GUI program, so every console tool it launches (``adb``, ``taskkill``,
``powershell``, ``wmic`` ...) would otherwise get a fresh black console window
for as long as it runs - or, for a tool that only lives for a moment, a small
window that flashes open and shut. ``install()`` makes ``subprocess.Popen`` (and
so ``run``, ``check_call``, ``check_output`` and friends) start every child
hidden by default:

* ``CREATE_NO_WINDOW`` is added to ``creationflags``, and
* a ``STARTUPINFO`` with ``SW_HIDE`` is supplied unless the caller passed its
  own. Both are needed: some hosts (PowerShell 5.1 among them) can show a frame
  before they honour ``CREATE_NO_WINDOW`` alone.

Callers that deliberately ask for a visible or detached console (``DETACHED_PROCESS``,
``CREATE_NEW_CONSOLE``) are left untouched, so the terminal-install hand-off
keeps working. This is a no-op on macOS and Linux.
"""

from __future__ import annotations

import os
import subprocess

_installed = False

CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_CONSOLE = 0x00000010
# A caller that sets either of these wants a window of its own.
_INTENTIONALLY_VISIBLE = _DETACHED_PROCESS | _CREATE_NEW_CONSOLE


def _hidden_startupinfo():
    """A ``STARTUPINFO`` that asks Windows not to show the child's window."""
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    return info


def hidden_window_kwargs() -> dict:
    """``creationflags``/``startupinfo`` kwargs that hide a console child (Windows only)."""
    if os.name != "nt":
        return {}
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": _hidden_startupinfo()}


def _wrap_init(original):
    """A ``Popen.__init__`` that hides the child unless a window was asked for."""

    def _init(self, *args, **kwargs):
        flags = int(kwargs.get("creationflags") or 0)
        if not flags & _INTENTIONALLY_VISIBLE:
            kwargs["creationflags"] = flags | CREATE_NO_WINDOW
            if kwargs.get("startupinfo") is None:
                kwargs["startupinfo"] = _hidden_startupinfo()
        original(self, *args, **kwargs)

    _init._innioasis_hidden = True
    return _init


def install() -> bool:
    """Patch ``subprocess.Popen`` once so child windows stay hidden. True if patched."""
    global _installed
    if os.name != "nt":
        return False
    if _installed or getattr(subprocess.Popen.__init__, "_innioasis_hidden", False):
        _installed = True
        return True
    subprocess.Popen.__init__ = _wrap_init(subprocess.Popen.__init__)
    _installed = True
    return True
