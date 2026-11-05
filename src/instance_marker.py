"""A shared marker saying which running copy owns the player's USB work.

The app refuses a second copy, but two copies can still meet: a ``--take-over``
relaunch, the force-quit path for a hung window, or a build that predates the
single-instance guard. Every copy sweeps stray flash tools on launch, and that
sweep used to be blind to work the other copy was doing — so it could kill the
SP Flash Tool, mtkclient or adb process that copy was flashing with.

This module writes a small JSON file into the per-user cache (the same place the
show-request marker lives) naming the owning pid, the tasks it is running and
the tool pids it started. Another copy reads it before sweeping tools or before
ending the running copy, and backs off while the owner is alive.

The marker is advisory, never a lock: a dead owner, a recycled pid or an
unreadable file is treated as "nobody is working", so a crash can never leave
the app unable to clean up after itself.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_VERSION = 1
_LOCK = threading.RLock()
_TASKS: dict[str, int] = {}
_OWNED_PIDS: set[int] = set()
_STARTED = time.time()


def _user_token() -> str:
    """A stable, filename-safe token for the launching user."""
    try:
        raw = str(os.getuid()) if os.name != "nt" else (
            os.environ.get("USERNAME") or os.environ.get("USER") or ""
        )
    except Exception:
        raw = ""
    token = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")
    return token or "user"


def marker_path() -> Path:
    """The per-user marker shared by installed and portable copies."""
    try:
        from .paths import app_cache_dir

        base = Path(app_cache_dir())
    except Exception:
        base = Path(tempfile.gettempdir())
    return base / f"updater-ce-{_user_token()}.busy.json"


def _pid_alive(pid: int) -> bool:
    """True while *pid* is a live process on this machine."""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout or ""
        except Exception:
            return False
        return str(int(pid)) in out
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _process_identity(pid: int) -> str:
    """Image name or command line of *pid*, or "" when it cannot be read."""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout or ""
            for line in out.splitlines():
                if line.startswith('"'):
                    return line.split('","')[0].strip('"').lower()
            return ""
        proc = Path(f"/proc/{int(pid)}/cmdline")
        if proc.exists():
            return proc.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace").strip().lower()
        out = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout or ""
        return out.strip().lower()
    except Exception:
        return ""


_APP_HINTS = ("innioasis", "updater", "python", "mediatek")


def _looks_like_our_app(pid: int) -> bool:
    """False only when *pid* is provably some other program.

    An unreadable identity counts as ours: the marker's whole job is to stop a
    sweep, so the safe answer is to let the other copy keep its tools.
    """
    identity = _process_identity(pid)
    if not identity:
        return True
    return any(hint in identity for hint in _APP_HINTS)


def _read() -> Optional[dict]:
    """The marker's contents, or None when it is absent or unreadable."""
    try:
        raw = marker_path().read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        state = json.loads(raw)
    except Exception:
        return None
    return state if isinstance(state, dict) else None


def _state() -> dict:
    with _LOCK:
        return {
            "version": STATE_VERSION,
            "pid": os.getpid(),
            "started": _STARTED,
            "tasks": sorted(name for name, count in _TASKS.items() if count > 0),
            "owned_pids": sorted(int(pid) for pid in _OWNED_PIDS if pid > 1),
        }


def _write() -> None:
    """Publish this copy's state atomically. Never raises."""
    try:
        path = marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_state(), separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(prefix=".busy-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        logger.debug("Could not publish the running-copy marker", exc_info=True)


def start() -> None:
    """Publish this copy as the live owner, with whatever it is already doing."""
    _write()


def refresh() -> None:
    """Rewrite the marker with the current task and owned-pid sets."""
    _write()


def clear() -> None:
    """Remove the marker at exit, but only when this copy owns it."""
    state = _read()
    if state and int(state.get("pid") or 0) not in (0, os.getpid()):
        return
    try:
        marker_path().unlink()
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("Could not remove the running-copy marker", exc_info=True)


def begin_task(name: str) -> None:
    """Mark a task in flight (an install session, a recovery, a transfer)."""
    key = str(name or "").strip()
    if not key:
        return
    with _LOCK:
        _TASKS[key] = _TASKS.get(key, 0) + 1
        _write()


def end_task(name: str) -> None:
    """Mark one instance of a task finished."""
    key = str(name or "").strip()
    with _LOCK:
        count = _TASKS.get(key, 0)
        if count <= 1:
            _TASKS.pop(key, None)
        else:
            _TASKS[key] = count - 1
        _write()


def note_owned_pid(pid: int) -> None:
    """Remember a tool process this copy started or handed off."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid <= 1:
        return
    with _LOCK:
        _OWNED_PIDS.add(pid)
        _write()


def forget_owned_pid(pid: int) -> None:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    with _LOCK:
        _OWNED_PIDS.discard(pid)
        _write()


def active_tasks() -> list[str]:
    """Task names this copy has in flight, for logs and diagnostics."""
    return _state()["tasks"]


def live_owner() -> Optional[int]:
    """Pid of another live copy that published a marker, or None."""
    state = _read()
    if not state:
        return None
    try:
        pid = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or pid == os.getpid():
        return None
    if not _pid_alive(pid) or not _looks_like_our_app(pid):
        return None
    return pid


def foreign_busy() -> bool:
    """True when another live copy is doing work this launch must not kill.

    A copy that is merely open owns nothing: only a published task or a tool
    pid it handed off is reason enough to refuse a sweep or a takeover. That
    keeps the recovery path for a hung, idle copy usable.
    """
    if live_owner() is None:
        return False
    state = _read() or {}
    return bool(state.get("tasks")) or bool(state.get("owned_pids"))


def busy_for_pid(pid: int) -> bool:
    """True when the marker's owner is *pid* and that copy is still alive."""
    state = _read()
    if not state:
        return False
    try:
        owner = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    return owner == int(pid or 0) and _pid_alive(owner)


def owner_tasks() -> list[str]:
    """The task names another live copy published."""
    state = _read()
    if not state or live_owner() is None:
        return []
    tasks = state.get("tasks")
    return [str(name) for name in tasks] if isinstance(tasks, list) else []


def owned_pids() -> frozenset[int]:
    """Tool pids a live copy asked other copies to leave alone."""
    state = _read()
    if not state or live_owner() is None:
        return frozenset()
    pids = state.get("owned_pids")
    if not isinstance(pids, list):
        return frozenset()
    found = set()
    for value in pids:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        if pid > 1:
            found.add(pid)
    return frozenset(found)


atexit.register(clear)
