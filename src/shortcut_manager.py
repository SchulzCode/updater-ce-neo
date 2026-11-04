"""Desktop and Start Menu / Apps Menu shortcuts for Innioasis Updater and SP Flash Tool.

Windows and Linux only; macOS has no shortcut management.

The Settings switches describe the machine, not a preference we remember: a
shortcut can be written by the installer, moved into a menu folder, renamed by
the person using it, or deleted from Explorer between two launches. So state is
read by looking for our shortcuts everywhere they can legally be - the per-user
*and* all-users Start Menu and desktop, in our own subfolders, under any of the
names this project has shipped - and a change acts on those same files instead
of leaving a second copy beside them.

On Windows the installer writes the menu entries to ``{userprograms}`` (the
current user's Start Menu) but the desktop icon to ``{autodesktop}`` (the
all-users desktop) under the brand's display name, which is why a per-user-only
lookup reported shortcuts as missing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from . import paths
from .i18n import current_language, text_for_language

logger = logging.getLogger(__name__)

APP_ID = "updater"
TOOL_ID = "sp_flash_tool"
SHORTCUT_IDS = (APP_ID, TOOL_ID)

# Names this project has given the shortcuts. [Icons] in the installer writes
# "{userprograms}\Innioasis Updater" for both brands and "{autodesktop}\{MyAppName}"
# for the desktop icon, so the desktop name is the brand's display name.
APP_NAME = "Innioasis Updater"
TOOL_NAME = "SP Flash Tool"
DESKTOP_APP_NAME = "Innioasis Updater CE"
DESKTOP_APP_NAME_MEDIATEK = "MediaTek Installer"

LINUX_APP_FILE = "innioasis-updater.desktop"
LINUX_TOOL_FILE = "sp-flash-tool.desktop"
LEGACY_COMMUNITY_FOLDER = "Innioasis Community"

# The languages Inno Setup offers in scripts/build_windows_installer.iss. The
# app is translated more widely, but shortcut generation follows the installer:
# outside this set the installer's English name remains the correct name.
INNO_SHORTCUT_LANGUAGES: dict[str, tuple[str, ...]] = {
    "english": ("en",),
    "arabic": ("ar",),
    "armenian": ("hy",),
    "brazilianportuguese": ("pt-BR", "pt"),
    "bulgarian": ("bg",),
    "catalan": ("ca",),
    "chinesesimplified": ("zh-CN",),
    "chinesetraditional": ("zh-TW",),
    "corsican": ("co",),
    "czech": ("cs",),
    "danish": ("da",),
    "dutch": ("nl",),
    "finnish": ("fi",),
    "french": ("fr",),
    "german": ("de",),
    "hebrew": ("he",),
    "hungarian": ("hu",),
    "icelandic": ("is",),
    "italian": ("it",),
    "japanese": ("ja",),
    "korean": ("ko",),
    "norwegian": ("no",),
    "polish": ("pl",),
    "portuguese": ("pt",),
    "russian": ("ru",),
    "slovak": ("sk",),
    "slovenian": ("sl",),
    "spanish": ("es",),
    "swedish": ("sv",),
    "tamil": ("ta",),
    "turkish": ("tr",),
    "ukrainian": ("uk",),
}

SHORTCUT_SUFFIXES = (".lnk", ".url")
# Our menu folders are a couple of levels deep at most; the bound keeps a
# runaway profile tree from hanging a state read.
MAX_SCAN_DEPTH = 3
# Folder names that are ours, whatever version wrote them: the installer has
# used "Innioasis Updater", "Innioasis Community" and "SP Flash Tool" over time.
OWNED_FOLDER_TOKENS = ("innioasis", "updater ce", "mediatek installer", "sp flash")

_USER_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"


def supported() -> bool:
    return paths.IS_WINDOWS or paths.IS_LINUX


def _is_mediatek_brand() -> bool:
    """True when this build is the generic MediaTek Installer."""
    try:
        from .config import is_mediatek_installer

        return bool(is_mediatek_installer())
    except Exception:
        return False


def shortcut_names(shortcut_id: str) -> Tuple[str, ...]:
    """Every file name this shortcut has carried, in any shipped version."""
    if shortcut_id == TOOL_ID:
        return (TOOL_NAME,)
    if _is_mediatek_brand():
        # The installer names this brand's menu entry after the updater too, so
        # both spellings are ours.
        return (DESKTOP_APP_NAME_MEDIATEK, APP_NAME, DESKTOP_APP_NAME, "Updater CE")
    return (APP_NAME, DESKTOP_APP_NAME, "Updater CE")


def _foreign_app_names() -> Tuple[str, ...]:
    """The other brand's shortcut names, which are not ours on this build."""
    return (APP_NAME,) if _is_mediatek_brand() else (DESKTOP_APP_NAME_MEDIATEK,)


def _inno_language_for(lang: str) -> str:
    """The Inno Setup language whose shortcut name matches *lang*, or ""."""
    value = (lang or "").strip()
    if not value:
        return ""
    for inno_name, app_codes in INNO_SHORTCUT_LANGUAGES.items():
        if value in app_codes:
            return inno_name
    base = value.split("-")[0]
    for inno_name, app_codes in INNO_SHORTCUT_LANGUAGES.items():
        if base in app_codes:
            return inno_name
    return ""


def _localized_creation_name(lang: str = "") -> str:
    """The localized app shortcut name for an installer language, or ""."""
    if _is_mediatek_brand():
        return ""
    inno_name = _inno_language_for(lang or current_language())
    if not inno_name or inno_name == "english":
        return ""
    for code in INNO_SHORTCUT_LANGUAGES[inno_name]:
        name = text_for_language("app_name", code).strip()
        if name:
            return name
    return ""


def _localized_shortcut_names(shortcut_id: str) -> Tuple[str, ...]:
    """Localized app names an Inno Setup shortcut may carry."""
    if shortcut_id != APP_ID or _is_mediatek_brand():
        return ()
    names: List[str] = []
    for inno_name, codes in INNO_SHORTCUT_LANGUAGES.items():
        if inno_name == "english":
            continue
        for code in codes:
            name = text_for_language("app_name", code).strip()
            if name and name not in names:
                names.append(name)
    return tuple(names)


def _creation_name(shortcut_id: str, kind: str) -> str:
    """The name a shortcut created by this app should carry (installer parity)."""
    if shortcut_id == TOOL_ID:
        return TOOL_NAME
    localized = _localized_creation_name()
    if localized:
        return localized
    if kind == "menu":
        return APP_NAME
    return DESKTOP_APP_NAME_MEDIATEK if _is_mediatek_brand() else DESKTOP_APP_NAME


# ---------------------------------------------------------------------------
# Where shortcuts live
# ---------------------------------------------------------------------------
def _env_path(name: str) -> Optional[Path]:
    value = os.environ.get(name)
    return Path(value) if value else None


def _registry_shell_folder(sub_key: str, value_name: str) -> Optional[Path]:
    """A shell folder as Explorer resolves it (OneDrive moves Desktop)."""
    if not paths.IS_WINDOWS:
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
    except Exception:
        return None
    if not value:
        return None
    return Path(os.path.expandvars(str(value)))


def _user_desktop() -> Optional[Path]:
    if paths.IS_WINDOWS:
        resolved = _registry_shell_folder(_USER_SHELL_FOLDERS, "Desktop")
        if resolved:
            return resolved
    profile = _env_path("USERPROFILE")
    return profile / "Desktop" if profile else None


def _common_desktop() -> Optional[Path]:
    """The all-users desktop, where the installer puts a desktop icon."""
    if not paths.IS_WINDOWS:
        return None
    public = _env_path("PUBLIC")
    if public:
        return public / "Desktop"
    return Path(os.environ.get("SystemDrive", "C:") + "\\") / "Users" / "Public" / "Desktop"


def _windows_menu_roots() -> List[Path]:
    roots: List[Path] = []
    appdata = _env_path("APPDATA")
    if appdata:
        roots.append(appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    programdata = _env_path("PROGRAMDATA")
    if programdata:
        roots.append(programdata / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return roots


def _linux_menu_root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"


def _linux_desktop_root() -> Optional[Path]:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return Path.home() / "Desktop"


def _roots(kind: str) -> List[Path]:
    """Every folder a shortcut of this kind lives in, writable location first."""
    if kind == "menu":
        roots: List[Optional[Path]] = _windows_menu_roots() if paths.IS_WINDOWS else [_linux_menu_root()]
    elif paths.IS_WINDOWS:
        roots = [_user_desktop(), _common_desktop()]
    else:
        roots = [_linux_desktop_root()]
    unique: List[Path] = []
    seen = set()
    for root in roots:
        if root is None:
            continue
        key = os.path.normcase(os.path.abspath(str(root)))
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


# ---------------------------------------------------------------------------
# Reading the real files
# ---------------------------------------------------------------------------
def _is_shortcut_file(path: Path) -> bool:
    return path.suffix.lower() in SHORTCUT_SUFFIXES


def _is_owned_folder(name: str) -> bool:
    low = (name or "").strip().lower()
    return any(token in low for token in OWNED_FOLDER_TOKENS)


def _walk_shortcuts(folder: Path, depth: int, owned_name: str) -> Iterator[Tuple[Path, str]]:
    """Shortcut files below one of our own folders, junctions skipped.

    *owned_name* is the name of the folder that made this subtree ours, so the
    caller can tell an app folder from a tool folder.
    """
    if depth > MAX_SCAN_DEPTH:
        return
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                yield from _walk_shortcuts(entry, depth + 1, owned_name)
            elif _is_shortcut_file(entry):
                yield entry, owned_name
        except OSError:
            continue


def _shortcuts_in(root: Path) -> Iterator[Tuple[Path, str]]:
    """Shortcuts at the top of *root*, plus those in our own subfolders.

    A shortcut found inside a folder that carries our name is still ours even
    when it was renamed; the folder is the evidence, not the file name. Files at
    the top of a shared folder carry no such evidence, so they are matched by
    name only.
    """
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if _is_owned_folder(entry.name):
                    yield from _walk_shortcuts(entry, 1, entry.name)
            elif _is_shortcut_file(entry):
                yield entry, ""
        except OSError:
            continue


def _name_matches(stem: str, names: Iterable[str]) -> bool:
    value = (stem or "").strip().lower()
    if not value:
        return False
    for name in names:
        low = name.lower()
        # "Innioasis Updater CE 2.5.2" and "SP Flash Tool - shortcut" are ours.
        if value == low or value.startswith(low + " ") or value.startswith(low + " -"):
            return True
    return False


def _active_localized_names(shortcut_id: str) -> Tuple[str, ...]:
    """The installer-language name for the language the app is using now."""
    if shortcut_id != APP_ID or _is_mediatek_brand():
        return ()
    inno_name = _inno_language_for(current_language())
    if not inno_name or inno_name == "english":
        return ()
    names: List[str] = []
    for code in INNO_SHORTCUT_LANGUAGES[inno_name]:
        name = text_for_language("app_name", code).strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _belongs(path: Path, owned_folder: str, shortcut_id: str, target: str = "") -> bool:
    """True when *path* is this app's shortcut for *shortcut_id*.

    English names stand on their own. A localized name is only trusted inside
    one of our own folders, when the .lnk target proves the app, or when it is
    the installer's name for the language the app is using now; otherwise a
    generic translated word for "updater" could claim an unrelated icon.
    """
    if _name_matches(path.stem, _foreign_app_names()):
        return False
    if _name_matches(path.stem, shortcut_names(shortcut_id)):
        return True
    localized = _name_matches(path.stem, _localized_shortcut_names(shortcut_id))
    if localized and (
        owned_folder
        or shortcut_id in _target_ids(target)
        or _name_matches(path.stem, _active_localized_names(shortcut_id))
    ):
        return True
    if not owned_folder:
        return False
    # A renamed shortcut inside one of our *app* folders is still the app's. A
    # folder that only carries the tool's name says nothing about the app.
    if shortcut_id != APP_ID or "sp flash" in owned_folder.lower():
        return False
    return not _name_matches(path.stem, shortcut_names(TOOL_ID))


_LNK_TARGET_CACHE: Dict[str, Tuple[int, str]] = {}


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _lnk_targets(files: Iterable[Path]) -> Dict[str, str]:
    """Resolve the executable each .lnk starts, through the shell API.

    A shortcut the person renamed still belongs to the app its target starts,
    which the file name alone cannot say. Targets are cached per modification
    time so reopening Settings does not walk the menus again.
    """
    resolved: Dict[str, str] = {}
    pending: List[Path] = []
    for path in files:
        if path.suffix.lower() != ".lnk":
            continue
        key = _path_key(path)
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            continue
        cached = _LNK_TARGET_CACHE.get(key)
        if cached and cached[0] == stamp:
            resolved[key] = cached[1]
        else:
            pending.append(path)
    if not pending or not paths.IS_WINDOWS:
        return resolved
    try:
        read = _read_windows_lnk_targets(pending)
    except Exception:
        logger.debug("Could not read shortcut targets", exc_info=True)
        read = {}
    for path in pending:
        key = _path_key(path)
        target = read.get(key, "")
        resolved[key] = target
        try:
            _LNK_TARGET_CACHE[key] = (path.stat().st_mtime_ns, target)
        except OSError:
            pass
    return resolved


def _read_windows_lnk_targets(files: Iterable[Path]) -> Dict[str, str]:
    """Read .lnk targets through the wide-string shell API.

    The previous PowerShell reader passed the paths in an environment variable,
    which the shell's ANSI layer turned into "????" for a localized name. A
    shortcut the installer created in Russian or Japanese then could not prove
    which program it started after the app's UI language changed. IShellLinkW
    loads the same file with Unicode paths and returns a Unicode target.
    """
    import ctypes
    import ctypes.wintypes as wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid(value: str) -> GUID:
        parsed = uuid.UUID(value)
        return GUID(
            parsed.time_low,
            parsed.time_mid,
            parsed.time_hi_version,
            (ctypes.c_ubyte * 8)(*parsed.bytes[8:]),
        )

    def method(interface, index: int, *argument_types):
        table = ctypes.cast(
            interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)
        return prototype(table[index])

    def check(result: int, what: str) -> None:
        if result < 0:
            raise OSError(f"{what} failed (0x{result & 0xFFFFFFFF:08X})")

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long

    initialized = False
    targets: Dict[str, str] = {}
    try:
        result = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        if result in (0, 1):  # S_OK / S_FALSE
            initialized = True
        elif (result & 0xFFFFFFFF) != 0x80010106:  # RPC_E_CHANGED_MODE is usable
            check(result, "CoInitializeEx")
        for path in files:
            link = ctypes.c_void_p()
            persist = ctypes.c_void_p()
            try:
                check(
                    ole32.CoCreateInstance(
                        ctypes.byref(guid("00021401-0000-0000-C000-000000000046")),
                        None,
                        0x1,  # CLSCTX_INPROC_SERVER
                        ctypes.byref(guid("000214F9-0000-0000-C000-000000000046")),
                        ctypes.byref(link),
                    ),
                    "IShellLinkW",
                )
                check(
                    method(
                        link,
                        0,  # QueryInterface
                        ctypes.POINTER(GUID),
                        ctypes.POINTER(ctypes.c_void_p),
                    )(
                        link,
                        ctypes.byref(guid("0000010B-0000-0000-C000-000000000046")),
                        ctypes.byref(persist),
                    ),
                    "IPersistFile",
                )
                check(
                    method(persist, 5, ctypes.c_wchar_p, wintypes.DWORD)(
                        persist, str(path), 0  # STGM_READ
                    ),
                    "Load",
                )
                buffer = ctypes.create_unicode_buffer(4096)
                result = method(
                    link,
                    3,  # GetPath
                    ctypes.c_wchar_p,
                    ctypes.c_int,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                )(link, buffer, len(buffer), None, 0)
                if result >= 0 and buffer.value:
                    targets[_path_key(path)] = buffer.value
            except Exception:
                logger.debug("Could not read shortcut target for %s", path, exc_info=True)
            finally:
                for interface in (persist, link):
                    if interface.value:
                        method(interface, 2)(interface)  # Release
    finally:
        if initialized:
            ole32.CoUninitialize()
    return targets


def _foreign_target_names() -> Tuple[str, ...]:
    """The other brand's executable; a shortcut starting it is not ours."""
    return ("innioasisupdater.exe",) if _is_mediatek_brand() else ("mediatekinstaller.exe",)


def _target_ids(target: str) -> set:
    """The shortcut ids an executable path starts (empty when unknown)."""
    if not target:
        return set()
    path = Path(target)
    name = path.name.lower()
    ids = set()
    if name in ("innioasisupdater.exe", "mediatekinstaller.exe", "2. install firmware.exe"):
        ids.add(APP_ID)
    elif name == "flash_tool.exe":
        ids.add(TOOL_ID)
    try:
        if path.parent.resolve() == Path(paths.INSTALL_DIR).resolve():
            ids.add(APP_ID)
    except OSError:
        pass
    return ids


def _shortcut_files(
    shortcut_id: str, kind: str, resolve_targets: bool = True
) -> List[Path]:
    """Every shortcut of *shortcut_id* of this kind that exists right now."""
    candidates = [item for root in _roots(kind) for item in _shortcuts_in(root)]
    targets = (
        _lnk_targets([path for path, _owned in candidates]) if resolve_targets else {}
    )
    found: List[Path] = []
    seen = set()
    for path, owned_folder in candidates:
        key = _path_key(path)
        if key in seen:
            continue
        if _name_matches(path.stem, _foreign_app_names()):
            continue
        target = targets.get(key, "")
        if Path(target).name.lower() in _foreign_target_names():
            continue
        if not (
            _belongs(path, owned_folder, shortcut_id, target)
            or shortcut_id in _target_ids(target)
        ):
            continue
        seen.add(key)
        found.append(path)
    return found


def _has_shortcut(shortcut_id: str, kind: str) -> bool:
    """Whether one exists, without paying for target reads when a name matches.

    Resolving targets starts a shell, so it only runs when nothing of this kind
    is named after the app - the renamed-shortcut case.
    """
    if _shortcut_files(shortcut_id, kind, resolve_targets=False):
        return True
    return bool(_shortcut_files(shortcut_id, kind))


def get_state(skip: Iterable[str] = ()) -> Dict[str, Dict[str, bool]]:
    """Return {shortcut_id: {"desktop": bool, "menu": bool}} from real files.

    ``skip`` leaves an id out: its switches are not on screen, so nothing has
    to be read for them.
    """
    skipped = set(skip)
    return {
        shortcut_id: {
            kind: _has_shortcut(shortcut_id, kind)
            for kind in ("desktop", "menu")
        }
        for shortcut_id in SHORTCUT_IDS
        if shortcut_id not in skipped
    }


# ---------------------------------------------------------------------------
# Changing it
# ---------------------------------------------------------------------------
def _migrate_community_folder() -> None:
    """Move shortcuts out of the retired shared menu folder, then drop it.

    Detection already covers the folder, so this is housekeeping: it keeps the
    entries where the installer now writes them and does nothing when the
    folder is absent.
    """
    roots = _roots("menu")
    if not roots:
        return
    folder = roots[0] / LEGACY_COMMUNITY_FOLDER
    if not folder.is_dir():
        return
    for path, _owned_name in _walk_shortcuts(folder, 1, folder.name):
        destination = roots[0] / path.name
        if destination.exists():
            continue
        try:
            shutil.move(str(path), str(destination))
        except OSError:
            logger.debug("Could not move legacy shortcut %s", path, exc_info=True)
    try:
        shutil.rmtree(folder)
    except OSError:
        logger.debug("Could not remove legacy shortcut folder %s", folder, exc_info=True)


def _creation_path(shortcut_id: str, kind: str) -> Optional[Path]:
    """Where this app writes a shortcut it has to create (a writable folder)."""
    roots = _roots(kind)
    if not roots:
        return None
    if paths.IS_WINDOWS:
        return roots[0] / f"{_creation_name(shortcut_id, kind)}.lnk"
    return roots[0] / (LINUX_APP_FILE if shortcut_id == APP_ID else LINUX_TOOL_FILE)


def set_shortcut(shortcut_id: str, kind: str, enabled: bool) -> bool:
    """Add or remove one shortcut. Returns True when the files now match."""
    if not supported() or shortcut_id not in SHORTCUT_IDS or kind not in ("desktop", "menu"):
        return False
    _migrate_community_folder()
    existing = _shortcut_files(shortcut_id, kind)
    if not enabled:
        for path in existing:
            try:
                path.unlink()
            except OSError:
                # The all-users desktop and Start Menu belong to whoever
                # installed the app, so a standard account cannot always remove
                # what Setup put there. The switch then keeps showing the truth
                # rather than a preference that had no effect.
                logger.info("Could not remove shortcut %s", path, exc_info=True)
        return not _shortcut_files(shortcut_id, kind)
    if existing:
        # One is already there - the installer's own, or one the person kept -
        # so adopt it instead of adding a second entry beside it.
        return True
    target = _creation_path(shortcut_id, kind)
    if target is None:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if paths.IS_WINDOWS:
            _write_windows_lnk(target, shortcut_id)
        else:
            _write_linux_desktop(target, shortcut_id)
    except Exception:
        logger.warning("Could not create shortcut %s", target, exc_info=True)
        return False
    return target.is_file()


# ---------------------------------------------------------------------------
# Writing a new shortcut (only when none exists anywhere)
# ---------------------------------------------------------------------------
def _app_exe_name() -> str:
    return "MediaTekInstaller.exe" if _is_mediatek_brand() else "InnioasisUpdater.exe"


def _launch_target(shortcut_id: str) -> Tuple[str, str]:
    """(target executable, working directory) for a shortcut."""
    if shortcut_id == APP_ID:
        if paths.IS_WINDOWS:
            return str(paths.INSTALL_DIR / _app_exe_name()), str(paths.INSTALL_DIR)
        return str(Path.home() / ".local" / "bin" / "innioasis-updater"), str(paths.INSTALL_DIR)
    tool = paths.find_sp_flash_tool()
    tool_dir = Path(tool) if tool else paths.SP_FLASH_TOOL_DIR
    if paths.IS_WINDOWS:
        return str(tool_dir / "flash_tool.exe"), str(tool_dir)
    return str(tool_dir / "flash_tool"), str(tool_dir)


def _write_windows_lnk(target: Path, shortcut_id: str) -> None:
    """Write a .lnk through the Unicode shell API.

    Shortcut names are localized, and the old PowerShell/WScript.Shell route
    passed the destination through the ANSI environment block: a Russian,
    Japanese, Chinese or Arabic name arrived as "????" and ``Save()`` failed.
    Removing the installer's shortcut then made the switch impossible to turn
    back on. ``IShellLinkW`` takes wide strings, so the name and the install
    path survive whatever the system code page is.
    """
    exe, workdir = _launch_target(shortcut_id)
    _write_windows_lnk_com(Path(target), exe, workdir)


def _write_windows_lnk_com(target: Path, exe: str, workdir: str) -> None:
    import ctypes
    import ctypes.wintypes as wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid(value: str) -> GUID:
        parsed = uuid.UUID(value)
        return GUID(
            parsed.time_low,
            parsed.time_mid,
            parsed.time_hi_version,
            (ctypes.c_ubyte * 8)(*parsed.bytes[8:]),
        )

    def method(interface, index: int, *argument_types):
        table = ctypes.cast(
            interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)
        return prototype(table[index])

    def check(result: int, what: str) -> None:
        if result < 0:
            raise OSError(f"{what} failed (0x{result & 0xFFFFFFFF:08X})")

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long

    initialized = False
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        result = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        if result in (0, 1):  # S_OK / S_FALSE
            initialized = True
        elif (result & 0xFFFFFFFF) != 0x80010106:  # RPC_E_CHANGED_MODE is usable
            check(result, "CoInitializeEx")
        check(
            ole32.CoCreateInstance(
                ctypes.byref(guid("00021401-0000-0000-C000-000000000046")),  # ShellLink
                None,
                0x1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(guid("000214F9-0000-0000-C000-000000000046")),  # IShellLinkW
                ctypes.byref(link),
            ),
            "IShellLinkW",
        )
        check(method(link, 20, ctypes.c_wchar_p)(link, exe), "SetPath")
        check(
            method(link, 9, ctypes.c_wchar_p)(link, workdir),
            "SetWorkingDirectory",
        )
        check(
            method(link, 17, ctypes.c_wchar_p, ctypes.c_int)(link, exe, 0),
            "SetIconLocation",
        )
        check(
            method(
                link,
                0,  # QueryInterface
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )(
                link,
                ctypes.byref(guid("0000010B-0000-0000-C000-000000000046")),  # IPersistFile
                ctypes.byref(persist),
            ),
            "IPersistFile",
        )
        check(
            method(persist, 6, ctypes.c_wchar_p, wintypes.BOOL)(
                persist, str(target), 1
            ),
            "Save",
        )
    finally:
        for interface in (persist, link):
            if interface.value:
                method(interface, 2)(interface)  # Release
        if initialized:
            ole32.CoUninitialize()


def _write_linux_desktop(target: Path, shortcut_id: str) -> None:
    exe, workdir = _launch_target(shortcut_id)
    name = _creation_name(shortcut_id, "menu")
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
