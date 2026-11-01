"""Desktop and Start Menu / Apps Menu shortcuts for Innioasis Updater and SP Flash Tool.

Windows and Linux only; macOS has no shortcut management. State is read from
the real shortcut files on disk, so checkboxes reflect what the installer
created and what the user has since deleted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from . import paths

APP_ID = "updater"
TOOL_ID = "sp_flash_tool"
SHORTCUT_IDS = (APP_ID, TOOL_ID)

APP_NAME = "Innioasis Updater"
TOOL_NAME = "SP Flash Tool"
LINUX_APP_FILE = "innioasis-updater.desktop"
LINUX_TOOL_FILE = "sp-flash-tool.desktop"
LEGACY_COMMUNITY_FOLDER = "Innioasis Community"


def supported() -> bool:
    return paths.IS_WINDOWS or paths.IS_LINUX


def _win_menu_root() -> Path | None:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" if appdata else None


def _win_desktop() -> Path | None:
    userprofile = os.environ.get("USERPROFILE")
    return Path(userprofile) / "Desktop" if userprofile else None


def _linux_menu_root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"


def _linux_desktop_root() -> Path | None:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return Path.home() / "Desktop"


def _names(kind: str) -> Dict[str, str]:
    """File names for each shortcut id (the same on the menu and desktop)."""
    if paths.IS_WINDOWS:
        return {APP_ID: f"{APP_NAME}.lnk", TOOL_ID: f"{TOOL_NAME}.lnk"}
    return {APP_ID: LINUX_APP_FILE, TOOL_ID: LINUX_TOOL_FILE}


def _location(kind: str, shortcut_id: str) -> Path | None:
    """Path of a shortcut file, or None where the platform has no such place."""
    if kind == "menu":
        root = _win_menu_root() if paths.IS_WINDOWS else _linux_menu_root()
    else:
        root = _win_desktop() if paths.IS_WINDOWS else _linux_desktop_root()
    if root is None:
        return None
    return root / _names(kind)[shortcut_id]


def _legacy_community_file(shortcut_id: str) -> Path | None:
    root = _win_menu_root() if paths.IS_WINDOWS else None
    if root is None:
        return None
    return root / LEGACY_COMMUNITY_FOLDER / _names("menu")[shortcut_id]


def _migrate_community_folder() -> None:
    """Move shortcuts out of the old Innioasis Community folder, then delete it.

    The shortcuts are recreated in the main Start Menu so the Settings
    checkboxes keep matching what the user can see.
    """
    if not paths.IS_WINDOWS:
        return
    root = _win_menu_root()
    if root is None:
        return
    folder = root / LEGACY_COMMUNITY_FOLDER
    if not folder.is_dir():
        return
    for sid in SHORTCUT_IDS:
        old = _legacy_community_file(sid)
        if old is not None and old.is_file():
            try:
                shutil.move(str(old), str(_location("menu", sid)))
            except OSError:
                pass
    shutil.rmtree(folder, ignore_errors=True)


def get_state() -> Dict[str, Dict[str, bool]]:
    """Return {shortcut_id: {"desktop": bool, "menu": bool}} from real files."""
    state: Dict[str, Dict[str, bool]] = {}
    for sid in SHORTCUT_IDS:
        entry = {}
        for kind in ("desktop", "menu"):
            loc = _location(kind, sid)
            entry[kind] = bool(loc and loc.is_file())
        state[sid] = entry
    return state


def set_shortcut(shortcut_id: str, kind: str, enabled: bool) -> bool:
    """Add or remove one shortcut. Returns True when the file state now matches."""
    if not supported() or shortcut_id not in SHORTCUT_IDS or kind not in ("desktop", "menu"):
        return False
    _migrate_community_folder()
    target = _location(kind, shortcut_id)
    if target is None:
        return False
    if not enabled:
        try:
            if target.is_file():
                target.unlink()
        except OSError:
            return False
        return not target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if paths.IS_WINDOWS:
            _write_windows_lnk(target, shortcut_id)
        else:
            _write_linux_desktop(target, shortcut_id)
    except Exception:
        return False
    return target.is_file()


def _launch_target(shortcut_id: str) -> tuple[str, str]:
    """(target executable, working directory) for a shortcut."""
    if shortcut_id == APP_ID:
        if paths.IS_WINDOWS:
            return str(paths.INSTALL_DIR / "InnioasisUpdater.exe"), str(paths.INSTALL_DIR)
        return str(Path.home() / ".local" / "bin" / "innioasis-updater"), str(paths.INSTALL_DIR)
    tool = paths.find_sp_flash_tool()
    tool_dir = Path(tool) if tool else paths.SP_FLASH_TOOL_DIR
    if paths.IS_WINDOWS:
        return str(tool_dir / "flash_tool.exe"), str(tool_dir)
    return str(tool_dir / "flash_tool"), str(tool_dir)


def _write_windows_lnk(target: Path, shortcut_id: str) -> None:
    exe, workdir = _launch_target(shortcut_id)
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:INNIO_LNK);"
        "$s.TargetPath=$env:INNIO_EXE;$s.WorkingDirectory=$env:INNIO_WD;"
        "$s.IconLocation=$env:INNIO_EXE;$s.Save()"
    )
    env = dict(os.environ, INNIO_LNK=str(target), INNIO_EXE=exe, INNIO_WD=workdir)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        env=env,
        capture_output=True,
        creationflags=0x08000000,  # CREATE_NO_WINDOW
        timeout=30,
    )


def _write_linux_desktop(target: Path, shortcut_id: str) -> None:
    exe, workdir = _launch_target(shortcut_id)
    name = APP_NAME if shortcut_id == APP_ID else TOOL_NAME
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={name}\n"
        f"Exec=\"{exe}\"\n"
        f"Path={workdir}\n"
        f"Icon={exe}\n"
        "Terminal=false\n"
        "Categories=Utility;System;\n"
    )
    target.write_text(body, encoding="utf-8")
    target.chmod(0o755 if target.parent == _linux_desktop_root() else 0o644)
