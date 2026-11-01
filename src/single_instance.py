"""One running copy of the app, and a way out when a stale copy blocks launch.

Two copies would fight over the same USB device, SP Flash Tool's own settings
files and the shared cache directory, so a second launch is refused rather than
allowed to disturb the first. The refusal explains itself and offers to end the
copy that holds the lock, which is what keeps a crashed or hidden window from
making the app permanently unlaunchable.

``QLockFile`` is used instead of a bare pid file: it records the holder's pid
and host, and with ``staleLockTime(0)`` it clears a lock whose process is gone
straight away, so a force-quit of the app never blocks the next launch.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QLockFile, QStandardPaths

logger = logging.getLogger(__name__)

LOCK_NAME = "updater-ce.lock"


def lock_path() -> Path:
    """Per-user, per-session location for the lock file."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    return Path(base or tempfile.gettempdir()) / LOCK_NAME


def _pid_alive(pid: int) -> bool:
    """True when *pid* is still a live process on this machine."""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except Exception:
            return False
        return str(pid) in (out or "")
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, but owned by someone else: still a holder.
        return True
    except OSError:
        return False


class InstanceLock:
    """The app's single-instance lock."""

    def __init__(self, path: Optional[Path] = None):
        self._lock = QLockFile(str(path or lock_path()))
        # No time-based guesswork: a dead holder is detected by its pid.
        self._lock.setStaleLockTime(0)
        self._held = False

    def acquire(self, timeout_ms: int = 200) -> bool:
        self._held = bool(self._lock.tryLock(timeout_ms))
        if not self._held and not self.holder_pid():
            # The lock file survived without a readable owner: remove it once
            # and try again rather than failing a launch for nothing.
            self._lock.removeStaleLockFile()
            self._held = bool(self._lock.tryLock(timeout_ms))
        return self._held

    def release(self) -> None:
        if self._held:
            self._lock.unlock()
            self._held = False

    def holder_pid(self) -> int:
        try:
            info = self._lock.getLockInfo()
        except Exception:
            return 0
        if not info:
            return 0
        try:
            return int(info[0])
        except (TypeError, ValueError, IndexError):
            return 0


def terminate_pid(pid: int, wait_seconds: float = 4.0) -> bool:
    """End *pid* (and its children) and report whether it actually went away."""
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=wait_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        logger.debug("Could not terminate pid %s: %s", pid, e)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    if not sys.platform.startswith("win"):
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            return not _pid_alive(pid)
        time.sleep(0.2)
    return not _pid_alive(pid)


def _ask_force_quit(pid: int) -> bool:
    """Ask the user whether to end the copy that is already running."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

    from .i18n import tr

    dialog = QDialog()
    dialog.setWindowTitle(tr("single_instance_title"))
    dialog.setModal(True)
    dialog.setMinimumWidth(420)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(10)

    title = QLabel(tr("single_instance_title"))
    font = title.font()
    font.setPixelSize(14)
    font.setBold(True)
    title.setFont(font)
    layout.addWidget(title)

    body = QLabel(tr("single_instance_body"))
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(body)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton(tr("btn_cancel"))
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(cancel)
    force = QPushButton(tr("single_instance_force_quit"))
    force.setDefault(True)
    force.clicked.connect(dialog.accept)
    buttons.addWidget(force)
    layout.addLayout(buttons)

    return dialog.exec() == QDialog.DialogCode.Accepted


def ensure_single_instance(app, lock: Optional[InstanceLock] = None) -> bool:
    """Take the lock, or explain why the launch stops. True means "carry on"."""
    lock = lock or InstanceLock()
    if lock.acquire():
        app._instance_lock = lock
        try:
            app.aboutToQuit.connect(lock.release)
        except Exception:
            pass
        return True

    pid = lock.holder_pid()
    logger.info("Another copy is running (pid %s); refusing to start", pid or "unknown")
    if not pid or not _pid_alive(pid):
        # Nothing is really holding it: clear and retry once.
        lock._lock.removeStaleLockFile()
        if lock.acquire():
            app._instance_lock = lock
            app.aboutToQuit.connect(lock.release)
            return True

    if pid and _pid_alive(pid) and _ask_force_quit(pid):
        terminate_pid(pid)
        if lock.acquire():
            app._instance_lock = lock
            app.aboutToQuit.connect(lock.release)
            return True
        logger.warning("Force quit did not release the instance lock")
    return False
