"""One running copy of the app on every platform — and never a false refusal.

Two copies would fight over the same USB device, SP Flash Tool's own settings
files and the shared cache directory. Worse, every start-up sweeps stray flash
tools, so a second copy starting while a flash run is in progress can kill the
very tool that run is using. A second launch is therefore refused — it asks the
copy already running to show its window and exits — and it never ends that copy,
because ending it runs its shut-down sweep as well.

The guard is deliberately biased towards *starting*. Refusing needs positive
evidence of a live copy; every ambiguous case fails open:

* the lock file lives in the launching user's own temp directory, named per user,
  so another account (or a shared ``/tmp``) can never be mistaken for this one;
* a lock whose holder pid is gone, or whose pid now belongs to a clearly
  unrelated program (a recycled pid), is stale and is taken over;
* a lock file that cannot be read or removed — read-only temp, permissions,
  another sandbox — is ignored, and the app starts;
* any unexpected error inside the guard starts the app.

Positive evidence is either the session-wide Windows mutex (kernel-owned, so it
cannot go stale and cannot be confused with a file) or a lock file whose holder
pid is alive and is our own program. The ``QLockFile`` also closes the race when
two copies are started at the same instant; the mutex and the focus server make
the answer authoritative once a copy is up.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

# Session-wide, not machine-wide: the player is a USB device, so copies in the
# same login session are the ones that can collide. ``Local\`` needs no
# privilege where ``Global\`` can.
WINDOWS_MUTEX_NAME = "Local\\innioasis-updater-ce-single-instance"
_ERROR_ALREADY_EXISTS = 183

_focus_server = None
_windows_mutex = None


def _user_token() -> str:
    """A stable, filename-safe token for the launching user.

    The lock file and the focus channel are per user: two accounts on one
    machine must not be able to block each other, and a shared temp directory
    must not hand one account's lock file to another.
    """
    try:
        raw = str(os.getuid()) if os.name != "nt" else (
            os.environ.get("USERNAME") or os.environ.get("USER") or ""
        )
    except Exception:
        raw = ""
    token = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")
    return token or "user"


def lock_name() -> str:
    return f"updater-ce-{_user_token()}.lock"


def focus_server_name() -> str:
    return f"updater-ce-focus-{_user_token()}"


class FocusServer(QObject):
    """Lets a second launch ask the running copy to show its window."""

    focus_requested = Signal()

    def __init__(self, name: Optional[str] = None):
        super().__init__()
        self._name = name or focus_server_name()
        self._server = QLocalServer(self)
        # A socket left behind by a crash is not a live copy; Qt's cleanup only
        # matters on desktops that use a real socket file.
        QLocalServer.removeServer(self._name)
        self._server.newConnection.connect(self._accept)
        self._server.listen(self._name)

    def is_listening(self) -> bool:
        return bool(self._server.isListening())

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                continue
            conn.readyRead.connect(conn.deleteLater)
            if conn.bytesAvailable() <= 0:
                conn.waitForReadyRead(250)
            conn.readAll()
            self.focus_requested.emit()
            conn.disconnectFromServer()


def start_focus_server() -> Optional[FocusServer]:
    """Start the primary copy's focus channel (kept alive by the app)."""
    global _focus_server
    if _focus_server is not None and _focus_server.is_listening():
        return _focus_server
    server = FocusServer()
    if not server.is_listening():
        logger.debug("Could not start the single-instance focus server")
        return None
    _focus_server = server
    return server


def request_focus(timeout_ms: int = 500) -> bool:
    """Ask an already-running copy to come to the front.

    A successful connection is itself proof that a copy is alive: the OS owns
    the name, so it cannot be left behind by a crash.
    """
    try:
        socket = QLocalSocket()
        socket.connectToServer(focus_server_name())
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True
    except Exception:
        return False


def acquire_windows_mutex() -> bool:
    """Hold the session-wide guard for this process. False means a copy exists.

    Windows only; every other platform returns True and relies on the lock file.
    The handle lives as long as the process, so the kernel releases the guard
    however the app exits. A failure to create the mutex is *not* treated as a
    conflict — a locked-down host may forbid named objects, and blocking the app
    there would be a false refusal.
    """
    global _windows_mutex
    if os.name != "nt":
        return True
    if _windows_mutex is not None:
        return True
    handle = 0
    last_error = 0
    try:
        kernel32 = getattr(ctypes, "WinDLL", ctypes.windll).kernel32
        create = kernel32.CreateMutexW
        create.restype = ctypes.c_void_p
        handle = create(None, False, WINDOWS_MUTEX_NAME)
        last_error = ctypes.get_last_error()
    except Exception as e:
        logger.debug("Could not create the single-instance mutex: %s", e)
        return True
    if not handle:
        logger.debug("CreateMutexW failed: %s", last_error)
        return True
    if last_error == _ERROR_ALREADY_EXISTS:
        try:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass
        return False
    _windows_mutex = handle
    return True


def lock_path() -> Path:
    """The launching user's own lock file."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    return Path(base or tempfile.gettempdir()) / lock_name()


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


def _pid_identity(pid: int) -> str:
    """Best-effort image name / command line of *pid*, or "" when unreadable."""
    if pid <= 0:
        return ""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout or ""
            for line in out.splitlines():
                if line.startswith('"'):
                    return line.split('","')[0].strip('"').lower()
            return ""
        proc = Path(f"/proc/{pid}/cmdline")
        if proc.exists():
            raw = proc.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            return raw.strip().lower()
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout or ""
        return out.strip().lower()
    except Exception:
        return ""


# Anything a copy of this project runs as: the packaged front ends, the
# development launcher, and the frozen app's own bootloader.
_OUR_APP_HINTS = (
    "innioasis",
    "updater",
    "mediatelkinstaller",
    "launcher.py",
    "python",
)


def _is_clearly_foreign(pid: int) -> bool:
    """True only when *pid* is provably some other program (a recycled pid).

    Unreadable identities count as "not foreign" so a permission boundary can
    never be turned into a refusal.
    """
    identity = _pid_identity(pid)
    if not identity:
        return False
    return not any(hint in identity for hint in _OUR_APP_HINTS)


class InstanceLock:
    """The app's single-instance lock file."""

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

    def unopenable(self) -> bool:
        """True when the lock file cannot be used here (read-only temp, rights).

        Nothing about permissions may stop the app from starting, so the caller
        treats this as "no guard available" rather than "another copy".
        """
        try:
            error = self._lock.error()
        except Exception:
            return False
        permission_error = getattr(QLockFile.LockError, "PermissionError", None)
        return permission_error is not None and error == permission_error

    def drop(self) -> bool:
        """Try to clear a lock we have proven stale. False when it cannot go."""
        try:
            return bool(self._lock.removeStaleLockFile())
        except Exception:
            return False


def _dismiss_label() -> str:
    """The app's own acknowledgement label, whichever key this build carries."""
    from .i18n import tr

    for key in ("btn_ok", "btn_close", "dialog_ok"):
        text = tr(key)
        if text and text != key:
            return text
    return "OK"


def _explain_already_running(focus_failed: bool = False) -> None:
    """Say why this launch stops. Never offers to end the running copy.

    There is deliberately no "force quit" here: the copy that is running owns
    the USB device and its own flash tool, and ending it would run its
    shut-down sweep over whatever flash run is in flight.
    """
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

    body = QLabel(
        tr("single_instance_focus_failed" if focus_failed else "single_instance_body")
    )
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(body)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    dismiss = QPushButton(_dismiss_label())
    dismiss.setDefault(True)
    dismiss.clicked.connect(dialog.accept)
    buttons.addWidget(dismiss)
    layout.addLayout(buttons)

    dialog.exec()


def _hold_lock(app, lock: InstanceLock) -> None:
    app._instance_lock = lock
    try:
        app.aboutToQuit.connect(lock.release)
    except Exception:
        pass


def ensure_single_instance(app, lock: Optional["InstanceLock"] = None) -> bool:
    """Take the guard, or send this launch to the copy already running.

    True means "carry on". A live copy is never terminated, so a refusal cannot
    disturb a flash run in progress. Any failure inside the guard itself starts
    the app: the user is never locked out by the guard's own machinery.
    """
    try:
        return _guarded(app, lock)
    except Exception:
        logger.warning("Single-instance guard failed; starting normally", exc_info=True)
        return True


def _guarded(app, lock: Optional["InstanceLock"]) -> bool:
    if not acquire_windows_mutex():
        logger.info("Another copy holds the single-instance mutex; refusing to start")
        focus_failed = not request_focus()
        _explain_already_running(focus_failed=focus_failed)
        return False

    lock = lock or InstanceLock()
    if lock.acquire():
        _hold_lock(app, lock)
        return True

    if lock.unopenable():
        logger.warning(
            "The single-instance lock cannot be used here; starting without it"
        )
        return True

    pid = lock.holder_pid()
    if not pid or not _pid_alive(pid) or _is_clearly_foreign(pid):
        # Dead holder, or the pid now belongs to an unrelated program: the lock
        # is stale. Take it if we can; otherwise start rather than block.
        logger.info("Clearing a stale single-instance lock (pid %s)", pid or "unknown")
        if lock.drop() and lock.acquire():
            _hold_lock(app, lock)
            return True
        logger.warning("Could not clear the stale lock; starting anyway")
        return True

    # A live copy of this app holds the lock. Show its window; if it cannot be
    # raised, say so — but never offer to end it.
    logger.info("Another copy is running (pid %s); refusing to start", pid)
    if request_focus():
        return False
    _explain_already_running(focus_failed=True)
    return False
