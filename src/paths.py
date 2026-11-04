"""Unified path resolution: development mode vs PyInstaller-packaged mode.

Faithful port of InniUpdaterChin's ``app.runtime_paths``. After packaging
there are two important directories:

- ``BUNDLE_DIR`` (``sys._MEIPASS``): internal resources unpacked by
  PyInstaller (``style.qss``, ``mtkclient``, ``donors.csv``).
- ``INSTALL_DIR`` (the executable's folder): files installed beside the app
  (``SP_Flash_Tool``, ``tools/UnRAR.exe``).
"""

import os
import platform
import struct
import sys
from pathlib import Path


def _get_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _get_install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable))
    return Path(__file__).resolve().parent.parent


def get_appimage_path() -> Path | None:
    """Return the Path to the running AppImage file if running under an AppImage on Linux."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        try:
            return Path(appimage).resolve()
        except (OSError, ValueError):
            pass
    if sys.argv and sys.argv[0]:
        try:
            cand = Path(sys.argv[0]).resolve()
            if cand.suffix.lower() == ".appimage":
                return cand
        except (OSError, ValueError):
            pass
    return None


def get_appimage_dir() -> Path | None:
    """Return the host directory containing the AppImage if running under AppImage."""
    img = get_appimage_path()
    if img is not None:
        try:
            parent = img.parent
            if parent.is_dir():
                return parent
        except (OSError, ValueError):
            pass
    return None


BUNDLE_DIR = _get_bundle_dir()
INSTALL_DIR = _get_install_dir()

# Where the project ships its own files in a dev checkout:
REPO_ROOT = Path(__file__).resolve().parent.parent

BASE_DIR = BUNDLE_DIR
MTKCLIENT_DIR = BUNDLE_DIR / "mtkclient"
RESOURCES_DIR = BUNDLE_DIR / "assets"
SP_FLASH_TOOL_DIR = INSTALL_DIR / "SP_Flash_Tool"
TOOLS_DIR = INSTALL_DIR / "tools"

# In a dev checkout, vendor/ lives next to the repo root (BUNDLE_DIR is the
# repo root in dev mode), so BUNDLE_DIR / "mtkclient" does not resolve.
if not (BUNDLE_DIR / "mtkclient").exists() and (REPO_ROOT / "vendor" / "mtkclient").exists():
    MTKCLIENT_DIR = REPO_ROOT / "vendor" / "mtkclient"
if not (BUNDLE_DIR / "assets").exists() and (REPO_ROOT / "assets").exists():
    RESOURCES_DIR = REPO_ROOT / "assets"
COMPAT_DIR = RESOURCES_DIR / "compat"


def _is_package_dir(path: Path) -> bool:
    """True when ``path`` is an importable package directory.

    Both source and bytecode count: the bundled builds ship mtkclient
    precompiled (``__init__.pyc``, no ``.py`` — see
    ``scripts/precompile_tree.py``), and a bundle that only recognised
    ``__init__.py`` would stop finding its own mtkclient.
    """
    if (path / "__init__.py").is_file() or (path / "__init__.pyc").is_file():
        return True
    cache = path / "__pycache__"
    if cache.is_dir():
        try:
            return any(
                p.name.startswith("__init__.") and p.suffix == ".pyc" for p in cache.iterdir()
            )
        except OSError:
            return False
    return False


def _mtkclient_path_entry(cand: Path) -> str | None:
    """The ``sys.path`` entry that makes ``mtkclient`` importable from ``cand``.

    Two layouts exist in the wild: ``cand/mtkclient/`` (the vendored tree) and
    ``cand`` itself being the package (a PyInstaller bundle).
    """
    if _is_package_dir(cand / "mtkclient"):
        return str(cand)
    if _is_package_dir(cand):
        return str(cand.parent)
    return None


def ensure_mtkclient_importable():
    """Make the bundled mtkclient importable (idempotent).

    Called by every module that imports mtkclient so the import works both in
    a dev checkout (vendor/) and in a PyInstaller bundle (BUNDLE_DIR/mtkclient,
    Contents/Resources/mtkclient, etc.).
    """
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        if stream is not None and hasattr(stream, "detach"):
            try:
                import io
                def _no_detach():
                    raise io.UnsupportedOperation("detach disabled to protect stream buffer")
                stream.detach = _no_detach
            except Exception:
                pass

    candidates = [
        MTKCLIENT_DIR,
        BUNDLE_DIR / "mtkclient",
        BUNDLE_DIR.parent / "Resources" / "mtkclient" if getattr(sys, "frozen", False) else None,
        REPO_ROOT / "vendor" / "mtkclient",
    ]
    for cand in candidates:
        if not cand or not cand.exists():
            continue
        entry = _mtkclient_path_entry(Path(cand))
        if entry:
            if entry not in sys.path:
                sys.path.insert(0, entry)
            return

    if str(MTKCLIENT_DIR) not in sys.path:
        sys.path.insert(0, str(MTKCLIENT_DIR))


def find_libusb_dylib() -> str | None:
    """Find the path to the libusb-1.0 dynamic library on macOS.

    Checks:
    1. Vendored universal dylib in mtkclient/Darwin/libusb-1.0.dylib
    2. PyInstaller bundle locations (sys._MEIPASS, Frameworks, Resources)
    3. python package `libusb_package`
    4. Standard Homebrew / MacPorts locations
    5. Returns None if not found on disk (falling back to standard dlopen name)
    """
    candidates = [
        MTKCLIENT_DIR / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
        MTKCLIENT_DIR / "Darwin" / "libusb-1.0.dylib",
        BUNDLE_DIR / "libusb-1.0.dylib",
        BUNDLE_DIR / "Frameworks" / "libusb-1.0.dylib",
        BUNDLE_DIR / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
        BUNDLE_DIR / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
    ]
    app_root = os.environ.get("INNIOASIS_APP_ROOT")
    if app_root:
        root_p = Path(app_root)
        candidates.extend([
            root_p / "Frameworks" / "libusb-1.0.dylib",
            root_p / "MacOS" / "libusb-1.0.dylib",
            root_p / "Resources" / "app" / "vendor" / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
        ])
    if getattr(sys, "frozen", False):
        res_dir = BUNDLE_DIR.parent / "Resources"
        frameworks_dir = BUNDLE_DIR.parent / "Frameworks"
        candidates.extend([
            res_dir / "libusb-1.0.dylib",
            res_dir / "Frameworks" / "libusb-1.0.dylib",
            res_dir / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
            res_dir / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
            frameworks_dir / "libusb-1.0.dylib",
            BUNDLE_DIR.parent / "MacOS" / "libusb-1.0.dylib",
        ])

    for c in candidates:
        if c and c.is_file():
            return str(c)

    try:
        import libusb_package
        lp = libusb_package.get_library_path()
        if lp and os.path.isfile(lp):
            return lp
    except Exception:
        pass

    for sys_path in [
        "/opt/homebrew/lib/libusb-1.0.dylib",
        "/usr/local/lib/libusb-1.0.dylib",
        "/opt/local/lib/libusb-1.0.dylib",
    ]:
        if os.path.isfile(sys_path):
            return sys_path

    return None

# --- Backend discovery (ported from flash_service) --------------------------

# Simulated macOS mode: run the macOS code path (MTKClient-only flash
# backend, no SP Flash Tool GUI) on Linux/Windows for parity testing.
# Set via the ``--simulate-macos`` launch flag (see launcher.py) or the
# INNIOASIS_SIMULATE_MACOS=1 environment variable.
SIMULATE_MACOS = os.environ.get("INNIOASIS_SIMULATE_MACOS", "") in ("1", "true", "yes", "on")

IS_WINDOWS = os.name == "nt" or sys.platform.startswith("win")
# Every macOS conditional in the app must read IS_MAC (not raw sys.platform)
# so the simulated mode is indistinguishable from the real thing.
IS_MAC = sys.platform == "darwin" or SIMULATE_MACOS
IS_LINUX = sys.platform.startswith("linux") and not IS_MAC


def find_sp_flash_tool():
    """Locate the SP Flash Tool directory (Windows & Linux).

    Search order:
    1. ``$SP_FLASH_TOOL_DIR`` env var
    2. Staged or self-healed user tools directory
    3. Inno-installed / AppImage bundled ``SP_Flash_Tool`` folder
    4. Local development / repository fallback.
    """
    if IS_MAC:
        return None
    candidates = [
        Path(os.environ["SP_FLASH_TOOL_DIR"]) if os.environ.get("SP_FLASH_TOOL_DIR") else None,
        SP_FLASH_TOOL_DIR,
        INSTALL_DIR / "SP_Flash_Tool",
        INSTALL_DIR / "sp_flash_tool",
        INSTALL_DIR,
    ]
    appimage_dir = get_appimage_dir()
    if appimage_dir:
        candidates.extend([
            appimage_dir / "SP_Flash_Tool",
            appimage_dir / "sp_flash_tool",
            appimage_dir,
        ])
    candidates.extend([
        REPO_ROOT / "SP_Flash_Tool",
        REPO_ROOT / "tools" / "SP_Flash_Tool",
        REPO_ROOT / "tools" / "windows" / "SP_Flash_Tool_v5.1904_Win",
        REPO_ROOT / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux",
        Path.cwd() / "SP_Flash_Tool",
        Path.cwd() / "sp_flash_tool",
        Path.cwd(),
    ])
    if IS_WINDOWS:
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Updater CE" / "tools" / "sp_flash_tool_win")
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Updater CE" / "SP_Flash_Tool")
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Innioasis Updater" / "tools" / "sp_flash_tool_win")
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Innioasis Updater" / "SP_Flash_Tool")
        candidates.append(Path.home() / "AppData" / "Local" / "Updater CE" / "tools" / "sp_flash_tool_win")
        candidates.append(Path.home() / "AppData" / "Local" / "Updater CE" / "SP_Flash_Tool")
        candidates.append(Path.home() / "AppData" / "Local" / "Innioasis Updater" / "tools" / "sp_flash_tool_win")
        candidates.append(Path.home() / "AppData" / "Local" / "Innioasis Updater" / "SP_Flash_Tool")
        candidates.append(Path(r"D:\work\data\tools\SP_Flash_Tool_v5.2016_Windows"))
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        candidates.append(Path(xdg_data) / "innioasis-updater" / "tools" / "sp_flash_tool_linux")
        xdg_cache = os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        candidates.append(Path(xdg_cache) / "innioasis-updater" / "linux_flash_tool")

    bin_names = ["flash_tool.exe"] if IS_WINDOWS else ["flash_tool", "flash_tool.sh"]
    for p in candidates:
        if not p or not p.exists():
            continue
        for b in bin_names:
            if (p / b).is_file():
                return p
    return None


_MACHO_MAGICS = (
    b"\xca\xfe\xba\xbe",  # Universal binary (fat) BE
    b"\xbe\xba\xfe\xca",  # Universal binary (fat) LE
    b"\xfe\xed\xfa\xce",  # Mach-O 32-bit BE
    b"\xce\xfa\xed\xfe",  # Mach-O 32-bit LE
    b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit BE
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit LE
)


def _is_compatible_rar_binary(
    path: Path,
    *,
    plat: str | None = None,
    machine: str | None = None,
) -> bool:
    """Return True if *path* can execute on *plat* / *machine*.

    Validates PE (Windows x64/ARM64), Mach-O (macOS), and ELF (Linux
    x86_64/aarch64/armhf) headers so cross-platform or cross-architecture
    binaries in ``tools/`` are never selected for the wrong host. Lightweight
    script/stub files used in unit tests are permitted as long as they don't
    use a ``.exe`` suffix on POSIX.
    """
    plat = (plat or sys.platform).lower()
    machine = (machine or platform.machine()).lower()

    if plat != "win32" and path.suffix.lower() == ".exe":
        return False

    try:
        with open(path, "rb") as f:
            hdr = f.read(512)
    except OSError:
        return False

    if len(hdr) >= 2 and hdr[:2] == b"MZ":
        if plat != "win32":
            return False
        if len(hdr) >= 0x40:
            pe_off = struct.unpack_from("<I", hdr, 0x3C)[0]
            if pe_off + 6 <= len(hdr) and hdr[pe_off : pe_off + 4] == b"PE\x00\x00":
                pe_machine = struct.unpack_from("<H", hdr, pe_off + 4)[0]
                # 0xAA64 = ARM64; Windows x64 hosts cannot execute ARM64 PE binaries.
                if pe_machine == 0xAA64 and machine in ("x86_64", "amd64", "x86", "i386", "i686"):
                    return False
        return True

    if len(hdr) >= 4 and hdr[:4] in _MACHO_MAGICS:
        return plat == "darwin"

    if len(hdr) >= 20 and hdr[:4] == b"\x7fELF":
        if not plat.startswith("linux"):
            return False
        endian = "<" if hdr[5] == 1 else ">"
        e_machine = struct.unpack_from(f"{endian}H", hdr, 18)[0]
        if e_machine == 0x3E:  # EM_X86_64
            return machine in ("x86_64", "amd64")
        if e_machine == 0xB7:  # EM_AARCH64
            return machine in ("aarch64", "arm64")
        if e_machine == 0x28:  # EM_ARM (32-bit armhf)
            return machine.startswith("arm") or machine in ("aarch64", "arm64")
        if e_machine == 0x03:  # EM_386
            return machine in ("i386", "i686", "x86", "x86_64", "amd64")
        return False

    return True


def find_bundled_rar_for_target(platform_name: str, arch: str = "") -> Path | None:
    """Return the repo-bundled portable RAR extractor for *(platform_name, arch)*.

    Covers every target platform and architecture in ``BUILD_TARGETS.md``:
    - macOS: ``arm64``, ``x86_64``, ``universal2`` -> ``tools/macos/unar``
    - Windows: ``x64`` / ``x86_64`` / ``amd64`` -> ``tools/windows/x64/UnRAR.exe``
               ``arm64`` / ``aarch64`` -> ``tools/windows/arm64/UnRAR.exe``
    - Linux: ``x86_64`` / ``amd64`` -> ``tools/linux/x86_64/unrar``
             ``aarch64`` / ``arm64`` -> ``tools/linux/aarch64/unrar``
             ``armhf`` / ``armv7l`` -> ``tools/linux/armhf/unrar``
    """
    plat = (platform_name or "").strip().lower()
    arch_norm = (arch or "").strip().lower()

    if plat in ("darwin", "macos", "mac", "osx"):
        cand = REPO_ROOT / "tools" / "macos" / "unar"
        return cand if cand.is_file() else None

    if plat in ("win32", "windows", "win"):
        if arch_norm in ("arm64", "aarch64"):
            cands = [
                REPO_ROOT / "tools" / "windows" / "arm64" / "UnRAR.exe",
            ]
            check_arch = "arm64"
        else:
            cands = [
                REPO_ROOT / "tools" / "windows" / "x64" / "UnRAR.exe",
                REPO_ROOT / "tools" / "windows" / "UnRAR.exe",
            ]
            check_arch = "x86_64"
        for c in cands:
            if c.is_file() and _is_compatible_rar_binary(c, plat="win32", machine=check_arch):
                return c
        return None

    if plat.startswith("linux"):
        if arch_norm in ("aarch64", "arm64"):
            cands = [
                REPO_ROOT / "tools" / "linux" / "aarch64" / "unrar",
                REPO_ROOT / "tools" / "linux" / "arm64" / "unrar",
            ]
            check_arch = "aarch64"
        elif arch_norm in ("armhf", "armv7l", "armv7", "arm"):
            cands = [
                REPO_ROOT / "tools" / "linux" / "armhf" / "unrar",
            ]
            check_arch = "armv7l"
        else:
            cands = [
                REPO_ROOT / "tools" / "linux" / "x86_64" / "unrar",
                REPO_ROOT / "tools" / "linux" / "unrar",
            ]
            check_arch = "x86_64"
        for c in cands:
            if c.is_file() and _is_compatible_rar_binary(c, plat="linux", machine=check_arch):
                return c
        return None

    return None


def find_unrar() -> str:
    """Locate a host-compatible UnRAR / unar executable across platforms and architectures."""
    plat = sys.platform.lower()
    machine = platform.machine().lower()

    tool_roots: list[Path] = []
    app_root = os.environ.get("INNIOASIS_APP_ROOT")
    if app_root:
        ar = Path(app_root)
        tool_roots.extend([
            ar / "Frameworks" / "tools",
            ar / "Resources" / "tools",
            ar / "Resources" / "app" / "tools",
        ])
    tool_roots.extend([TOOLS_DIR, BUNDLE_DIR / "tools"])

    candidates: list[Path] = []
    if plat == "win32":
        win_archs = ["arm64", "x64"] if machine in ("arm64", "aarch64") else ["x64"]
        for d in tool_roots:
            for a in win_archs:
                candidates.append(d / a / "UnRAR.exe")
            candidates.extend([d / "UnRAR.exe", d / "unar.exe"])
        for a in win_archs:
            candidates.append(REPO_ROOT / "tools" / "windows" / a / "UnRAR.exe")
        candidates.extend([
            REPO_ROOT / "tools" / "windows" / "UnRAR.exe",
            REPO_ROOT / "tools" / "UnRAR.exe",
            Path.home() / "AppData" / "Local" / "Updater CE" / "tools" / "unrar_win" / "UnRAR.exe",
            Path.home() / "AppData" / "Local" / "Innioasis Updater" / "tools" / "unrar_win" / "UnRAR.exe",
            Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
            Path(r"C:\Program Files (x86)\WinRAR\UnRAR.exe"),
        ])
    elif plat == "darwin":
        for d in tool_roots:
            candidates.extend([d / "unar", d / "unrar"])
        candidates.extend([
            REPO_ROOT / "tools" / "macos" / "unar",
            REPO_ROOT / "tools" / "macos" / "unrar",
            REPO_ROOT / "tools" / "unar",
            REPO_ROOT / "tools" / "unrar",
        ])
    else:
        if machine in ("aarch64", "arm64"):
            lin_archs = ["aarch64", "arm64", "armhf"]
        elif machine.startswith("arm"):
            lin_archs = ["armhf"]
        else:
            lin_archs = ["x86_64"]
        for d in tool_roots:
            for a in lin_archs:
                candidates.extend([d / a / "unrar", d / a / "unar"])
            candidates.extend([d / "unrar", d / "unar"])
        for a in lin_archs:
            candidates.extend([
                REPO_ROOT / "tools" / "linux" / a / "unrar",
                REPO_ROOT / "tools" / "linux" / a / "unar",
            ])
        candidates.extend([
            REPO_ROOT / "tools" / "linux" / "unrar",
            REPO_ROOT / "tools" / "linux" / "unar",
            REPO_ROOT / "tools" / "unrar",
            REPO_ROOT / "tools" / "unar",
        ])

    for p in candidates:
        if p and p.is_file() and _is_compatible_rar_binary(p, plat=plat, machine=machine):
            return str(p)
    on_path = _which("unar") or _which("unrar")
    if on_path:
        return on_path
    return ""


def _which(name: str):
    import shutil

    return shutil.which(name)


def app_cache_dir() -> Path:
    """Per-user cache directory for downloaded manifests, catalogs and PATs."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Updater CE" / ".cache"
    elif sys.platform == "darwin":
        # The app's own cache lives in the shared Application Support folder —
        # the pre-3.0 app's folder, which first launch clears of the old app's
        # files (see legacy_cleanup.clear_legacy_support_now).
        return Path.home() / "Library" / "Application Support" / "Innioasis Updater" / ".cache"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        target = base / "updater-ce"
        legacy = base / "innioasis-updater" / ".cache"
        if not target.exists() and legacy.exists():
            return legacy
        return target

