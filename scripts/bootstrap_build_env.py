#!/usr/bin/env python3
"""bootstrap_build_env.py — make a fresh clone buildable.

A clone of this repository contains no virtualenv and no third-party packages,
and macOS still ships a Python 3.9 at ``/usr/bin/python3`` that cannot run the
build stack (PySide6, PyInstaller, pyobjc). Running a build script there used to
die on the first missing import, before anything explained what to install.

This helper closes that gap. It:

1. finds a suitable CPython (3.10+, preferring 3.12) on PATH, in Homebrew, the
   python.org framework, pyenv, an existing project virtualenv, or uv;
   if none exists it can obtain one (uv, Homebrew, or winget — never sudo);
2. creates ``.venv-build`` (the virtualenv this repository's build scripts and
   docs already use);
3. pip-installs ``requirements.txt`` + ``requirements-build.txt`` and records a
   hash marker so later builds skip the network entirely;
4. verifies the modules the PyInstaller specs and build scripts import, and
   reports exactly what is missing instead of leaving a traceback behind.

The last line of stdout is ``PYTHON=<absolute interpreter path>`` so a build
script can consume it with ``PYTHON="$(python3 scripts/bootstrap_build_env.py)"``;
all human-readable progress goes to stderr.

Usage:
    python3 scripts/bootstrap_build_env.py              # set up or verify
    python3 scripts/bootstrap_build_env.py --check      # verify only, install nothing
    python3 scripts/bootstrap_build_env.py --force      # reinstall the requirement sets
    python3 scripts/bootstrap_build_env.py --no-install # never download a Python
    python3 scripts/bootstrap_build_env.py --quiet      # errors only

Exit codes: 0 = ready to build, 1 = not ready (the messages say why),
2 = the helper itself could not run (wrong interpreter, no pip).

``PYTHON=/path/to/python`` pins the interpreter: it is then verified but never
modified. ``INNIOASIS_NO_AUTO_INSTALL=1`` is the environment form of
``--no-install``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv-build"
# Virtualenvs this repository already documents; a healthy existing one is
# reused (and repaired) rather than replaced by a second copy.
REUSABLE_VENVS = (".venv-build", ".venv")
REQUIREMENTS = ("requirements.txt", "requirements-build.txt")
MARKER_NAME = ".innioasis-build-env.json"

MIN_VERSION = (3, 10)
MAX_VERSION_EXCLUSIVE = (3, 15)
# Order matters: 3.12 is the interpreter this repository is tested on, and the
# PySide6 6.12 wheels that fix the none_dealloc crash need 3.12+.
PREFERRED = ((3, 12), (3, 13), (3, 11), (3, 10), (3, 14))

BASE_MODULES = (
    "PyInstaller",
    "PySide6",
    "requests",
    "usb",
    "serial",
    "Cryptodome",
    "colorama",
    "capstone",
)
DARWIN_MODULES = ("objc", "AppKit", "Foundation", "Quartz", "pyqt_liquidglass", "libusb_package")

# One subprocess verifies every module, so a slow import (PySide6, AppKit) is
# paid once. Any exception counts as missing: a half-installed wheel is not
# buildable either.
_VERIFY_SNIPPET = """
import importlib, json, sys
missing = []
for name in sys.argv[1:]:
    try:
        importlib.import_module(name)
    except BaseException as exc:  # SystemExit from a broken wheel included
        missing.append([name, f"{type(exc).__name__}: {exc}"])
print(json.dumps(missing))
"""


class Bootstrapper:
    def __init__(self, *, quiet: bool, allow_install: bool, force: bool) -> None:
        self.quiet = quiet
        self.allow_install = allow_install
        self.force = force
        self._version_cache: dict[str, tuple[int, int, int] | None] = {}
        self._arch_cache: dict[str, str | None] = {}

    # -- reporting ---------------------------------------------------------
    def say(self, message: str = "") -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def step(self, message: str) -> None:
        if not self.quiet:
            print(f"  -> {message}", file=sys.stderr)

    def warn(self, message: str) -> None:
        print(f"  ! {message}", file=sys.stderr)

    def fail(self, message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return 1

    # -- interpreter probing ----------------------------------------------
    def version(self, exe: str, *, refresh: bool = False) -> tuple[int, int, int] | None:
        key = os.path.realpath(exe)
        if not refresh and key in self._version_cache:
            return self._version_cache[key]
        found = None
        try:
            out = subprocess.run(
                [exe, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if out.returncode == 0:
                parts = out.stdout.strip().split(".")
                if len(parts) >= 2 and all(p.isdigit() for p in parts[:3]):
                    found = (int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
        except (OSError, subprocess.SubprocessError):
            found = None
        self._version_cache[key] = found
        return found

    def arch(self, exe: str) -> str | None:
        key = os.path.realpath(exe)
        if key in self._arch_cache:
            return self._arch_cache[key]
        found = None
        try:
            out = subprocess.run(
                [exe, "-c", "import platform; print(platform.machine())"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if out.returncode == 0:
                found = out.stdout.strip().lower()
        except (OSError, subprocess.SubprocessError):
            found = None
        self._arch_cache[key] = found
        return found

    def _acceptable(self, exe: str) -> bool:
        ver = self.version(exe)
        return ver is not None and MIN_VERSION <= ver < MAX_VERSION_EXCLUSIVE

    def _describe(self, exe: str) -> str:
        ver = self.version(exe)
        text = ".".join(str(p) for p in ver) if ver else "unknown version"
        arch = self.arch(exe) or "unknown arch"
        return f"Python {text} ({arch}) at {exe}"

    # -- candidate discovery ----------------------------------------------
    def candidates(self) -> list[str]:
        """Every plausible interpreter, best first, deduplicated by real path."""
        seen: set[str] = set()
        ordered: list[tuple[tuple[int, int], str]] = []

        def add(path: str | os.PathLike[str] | None, *, bonus: int = 0) -> None:
            if not path:
                return
            text = str(path)
            exe = text if os.path.isabs(text) else shutil.which(text) or ""
            if not exe or not os.path.isfile(exe) or not os.access(exe, os.X_OK):
                return
            real = os.path.realpath(exe)
            if real in seen:
                return
            seen.add(real)
            ver = self.version(exe)
            if ver is None:
                return
            rank = PREFERRED.index((ver[0], ver[1])) if (ver[0], ver[1]) in PREFERRED else len(PREFERRED)
            ordered.append(((rank - bonus, -(ver[0] * 100 + ver[1])), exe))

        # The interpreter running this helper is a good hint (someone typing
        # `python3.12 scripts/bootstrap_build_env.py` already chose one).
        add(sys.executable, bonus=1)
        for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"):
            add(name)

        here = Path.home()
        search_dirs: list[Path] = []
        if sys.platform == "darwin":
            for prefix in ("/opt/homebrew", "/usr/local"):
                base = Path(prefix)
                search_dirs += [base / "bin", base / "opt" / "python@3.13" / "bin", base / "opt" / "python@3.12" / "bin"]
                search_dirs += [base / "opt" / "python@3.11" / "bin", base / "opt" / "python@3.10" / "bin"]
            framework = Path("/Library/Frameworks/Python.framework/Versions")
            if framework.is_dir():
                search_dirs += [p / "bin" for p in sorted(framework.iterdir(), reverse=True)]
        elif os.name != "nt":
            search_dirs += [Path("/usr/local/bin"), Path("/usr/bin"), here / ".local" / "bin"]
        search_dirs += [here / ".local" / "share" / "uv" / "python"]
        for directory in search_dirs:
            try:
                entries = sorted(directory.iterdir(), reverse=True)
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
                        add(entry / "bin" / name)
                elif entry.name.startswith("python3"):
                    add(entry)

        for base in (here / ".pyenv" / "versions", Path("/opt/python")):
            try:
                versions = sorted(base.iterdir(), reverse=True)
            except OSError:
                continue
            for version_dir in versions:
                add(version_dir / "bin" / "python3")

        if os.name == "nt":
            local = os.environ.get("LOCALAPPDATA", "")
            for minor in ("13", "12", "11", "10"):
                add(Path(local) / "Programs" / "Python" / f"Python3{minor}" / "python.exe")
                add(Path(f"C:/Python3{minor}") / "python.exe")
                add(Path(local) / "Microsoft" / "WindowsApps" / f"python3.{minor}.exe")
            launcher = shutil.which("py")
            if launcher:
                try:
                    listing = subprocess.run(
                        [launcher, "-0p"], capture_output=True, text=True, timeout=30
                    ).stdout
                except (OSError, subprocess.SubprocessError):
                    listing = ""
                for line in listing.splitlines():
                    match = re.search(r"([A-Za-z]:\\.*?python\.exe)", line)
                    if match:
                        add(match.group(1))

        ordered.sort(key=lambda item: item[0])
        return [exe for _, exe in ordered]

    def project_venv_python(self) -> str | None:
        for name in REUSABLE_VENVS:
            venv = ROOT / name
            for rel in ("bin/python3", "bin/python", "Scripts/python.exe"):
                candidate = venv / rel
                if candidate.is_file():
                    return str(candidate)
        return None

    # -- obtaining an interpreter -----------------------------------------
    def uv(self) -> str | None:
        for candidate in (
            shutil.which("uv"),
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
            "/opt/homebrew/bin/uv",
        ):
            if candidate and Path(candidate).is_file():
                return str(candidate)
        return None

    def obtain_interpreter(self) -> str | None:
        """Last resort: fetch a CPython through uv, Homebrew or winget."""
        if not self.allow_install:
            return None
        uv = self.uv()
        if uv:
            self.step(f"No suitable Python found; asking uv for one: {uv} python install 3.12")
            subprocess.run([uv, "python", "install", "3.12"], check=False)
            for found in self.candidates():
                if self._acceptable(found):
                    return found

        if sys.platform == "darwin":
            brew = shutil.which("brew") or next(
                (p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if Path(p).is_file()), None
            )
            if brew:
                self.step("Installing Homebrew's Python 3.12 (brew install python@3.12)…")
                subprocess.run([brew, "install", "python@3.12"], check=False)
                for found in self.candidates():
                    if self._acceptable(found):
                        return found
            else:
                self.step("Installing uv (Astral's official installer, https://astral.sh/uv) into ~/.local/bin…")
                subprocess.run(
                    ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"], check=False
                )
                uv = self.uv()
                if uv:
                    self.step(f"Asking uv for Python 3.12: {uv} python install 3.12")
                    subprocess.run([uv, "python", "install", "3.12"], check=False)
                    for found in self.candidates():
                        if self._acceptable(found):
                            return found

        if os.name == "nt" and shutil.which("winget"):
            self.step("Installing Python 3.12 with winget…")
            subprocess.run(
                ["winget", "install", "-e", "--id", "Python.Python.3.12", "--accept-package-agreements"],
                check=False,
            )
            for found in self.candidates():
                if self._acceptable(found):
                    return found
        return None

    # -- virtualenv --------------------------------------------------------
    def venv_python_path(self) -> Path:
        return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")

    def create_venv(self, interpreter: str) -> Path | None:
        if VENV_DIR.exists() and not self.venv_python_path().is_file():
            # A half-created directory from an interrupted run: it holds nothing
            # worth keeping, and `python -m venv` would refuse/reuse it oddly.
            self.step(f"Replacing incomplete virtualenv {VENV_DIR.name}")
            shutil.rmtree(VENV_DIR, ignore_errors=True)
        self.step(f"Creating {VENV_DIR.name} with {self._describe(interpreter)}")
        result = subprocess.run(
            [interpreter, "-m", "venv", str(VENV_DIR)], capture_output=True, text=True
        )
        venv_py = self.venv_python_path()
        if result.returncode != 0 or not venv_py.is_file():
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            if detail:
                print("\n".join(detail[-4:]), file=sys.stderr)
            self._explain_venv_failure()
            return None
        if not self._has_pip(str(venv_py)):
            subprocess.run(
                [str(venv_py), "-m", "ensurepip", "--upgrade", "--default-pip"],
                capture_output=True,
                text=True,
            )
        if not self._has_pip(str(venv_py)):
            self._explain_venv_failure()
            return None
        return venv_py

    def _has_pip(self, exe: str) -> bool:
        return (
            subprocess.run(
                [exe, "-m", "pip", "--version"], capture_output=True, text=True
            ).returncode
            == 0
        )

    def _explain_venv_failure(self) -> None:
        print(
            "ERROR: could not create a virtualenv. Install the venv/ensurepip support first:\n"
            "  macOS:          xcode-select --install   (or reinstall Python from python.org)\n"
            "                  brew install python@3.12\n"
            "  Debian/Ubuntu:  sudo apt install python3-venv python3-pip\n"
            "  Fedora:         sudo dnf install python3-pip\n"
            "  Arch:           sudo pacman -S python-pip\n"
            "Then run this script again.",
            file=sys.stderr,
        )

    # -- dependencies ------------------------------------------------------
    def modules(self) -> tuple[str, ...]:
        if sys.platform == "darwin":
            return BASE_MODULES + DARWIN_MODULES
        return BASE_MODULES

    def requirements_hash(self, python_exe: str) -> str:
        digest = hashlib.sha256()
        for name in REQUIREMENTS:
            path = ROOT / name
            digest.update(name.encode())
            digest.update(path.read_bytes() if path.exists() else b"<missing>")
        ver = self.version(python_exe) or (0, 0, 0)
        digest.update(f"{ver}|{sys.platform}|{self.arch(python_exe)}".encode())
        return digest.hexdigest()

    def read_marker(self) -> dict:
        try:
            return json.loads((VENV_DIR / MARKER_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def write_marker(self, python_exe: str, req_hash: str) -> None:
        try:
            (VENV_DIR / MARKER_NAME).write_text(
                json.dumps(
                    {
                        "requirements_hash": req_hash,
                        "python": python_exe,
                        "version": ".".join(str(p) for p in self.version(python_exe) or ()),
                        "platform": sys.platform,
                        "arch": self.arch(python_exe),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def verify(self, python_exe: str) -> list[list[str]]:
        result = subprocess.run(
            [python_exe, "-c", _VERIFY_SNIPPET, *self.modules()],
            capture_output=True,
            text=True,
        )
        for line in reversed(result.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("["):
                try:
                    return json.loads(line)
                except ValueError:
                    break
        return [[m, "could not be imported (interpreter produced no report)"] for m in self.modules()]

    def install(self, python_exe: str, *, stream: bool) -> bool:
        if not (ROOT / "requirements.txt").is_file():
            print("ERROR: requirements.txt is missing from this checkout.", file=sys.stderr)
            return False
        args = [python_exe, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
        if self.force:
            args.append("--upgrade")
        for name in REQUIREMENTS:
            if (ROOT / name).is_file():
                args += ["-r", str(ROOT / name)]
        if stream:
            self.step("Installing build dependencies (first run downloads ~200 MB)…")
            self.say("     " + " ".join(args))
            result = subprocess.run(args, text=True)
        else:
            # Topping up an existing environment: pip's "already satisfied"
            # report is noise, so keep it until something actually fails.
            self.step("Repairing build dependencies…")
            result = subprocess.run(args, capture_output=True, text=True)
            if result.returncode != 0:
                print(result.stdout[-4000:], file=sys.stderr)
                print(result.stderr[-4000:], file=sys.stderr)
        if result.returncode != 0:
            print(
                "ERROR: dependency installation failed.\n"
                "       Re-run it by hand to see the full log:\n"
                f"         {python_exe} -m pip install -r requirements.txt -r requirements-build.txt",
                file=sys.stderr,
            )
            return False
        return True

    # -- the actual workflow ----------------------------------------------
    def run(self, *, check_only: bool) -> int:
        explicit = os.environ.get("PYTHON", "").strip()
        req_hash = ""
        python_exe: str | None = None

        if explicit:
            resolved = explicit if os.path.isabs(explicit) else shutil.which(explicit)
            if not resolved or not Path(resolved).is_file():
                return self.fail(f"PYTHON={explicit} does not point at an interpreter.")
            if not self._acceptable(resolved):
                return self.fail(
                    f"PYTHON={explicit} is {self._describe(resolved)}; need Python "
                    f"{MIN_VERSION[0]}.{MIN_VERSION[1]}–{MAX_VERSION_EXCLUSIVE[0]}.{MAX_VERSION_EXCLUSIVE[1] - 1}.\n"
                    "       Unset PYTHON to let the build scripts set up .venv-build for you."
                )
            python_exe = resolved
            self.step(f"Using PYTHON: {self._describe(python_exe)}")
            missing = self.verify(python_exe)
            if missing:
                return self.fail(
                    "that interpreter is missing build dependencies:\n"
                    + "\n".join(f"         - {name}: {err}" for name, err in missing)
                    + f"\n       Install them: {python_exe} -m pip install -r requirements.txt -r requirements-build.txt"
                )
            return self._done(python_exe)

        reusable = self.project_venv_python()
        if reusable and not self._acceptable(reusable):
            self.warn(
                f"{reusable} is {self._describe(reusable)}; need Python "
                f"{MIN_VERSION[0]}.{MIN_VERSION[1]}+. It will be replaced."
            )
            reusable = None

        if reusable and check_only:
            missing = self.verify(reusable)
            if missing:
                return self.fail(
                    "build dependencies are missing from the existing virtualenv:\n"
                    + "\n".join(f"         - {name}: {err}" for name, err in missing)
                    + f"\n       Install them: {reusable} -m pip install -r requirements.txt -r requirements-build.txt"
                )
            return self._done(reusable)

        if reusable:
            req_hash = self.requirements_hash(reusable)
            marker = self.read_marker()
            if not self.force and marker.get("requirements_hash") == req_hash:
                missing = self.verify(reusable)
                if not missing:
                    self.step(f"Reusing {Path(reusable).parent.parent.name} — dependencies up to date")
                    return self._done(reusable)
            if not self.install(reusable, stream=False):
                return 1
            missing = self.verify(reusable)
            if missing:
                return self.fail(
                    "dependencies installed but still not importable:\n"
                    + "\n".join(f"         - {name}: {err}" for name, err in missing)
                    + "\n       Delete .venv-build and run the build again to start clean."
                )
            self.write_marker(reusable, req_hash)
            return self._done(reusable)

        if check_only:
            return self.fail(
                "no build environment yet (this checkout has no .venv-build).\n"
                "       Run the build without --check-deps to create it, or set PYTHON=/path/to/python3.12."
            )

        interpreter = next((exe for exe in self.candidates() if self._acceptable(exe)), None)
        if not interpreter:
            too_new = [
                exe
                for exe in self.candidates()
                if (self.version(exe) or (0, 0, 0)) >= MAX_VERSION_EXCLUSIVE
            ]
            if too_new:
                self.warn(
                    f"Found only newer Pythons than {MAX_VERSION_EXCLUSIVE[0]}.{MAX_VERSION_EXCLUSIVE[1] - 1} "
                    f"({too_new[0]}); their wheels are not verified for this build."
                )
            interpreter = self.obtain_interpreter()
        if not interpreter:
            return self.fail(
                "no Python 3.10–3.14 was found and none could be installed automatically.\n"
                "       Install one, then run the build again:\n"
                "  macOS:          brew install python@3.12   (or https://www.python.org/downloads/macos/)\n"
                "  Debian/Ubuntu:  sudo apt install python3.12 python3.12-venv\n"
                "  Fedora:         sudo dnf install python3.12\n"
                "  Windows:        winget install -e --id Python.Python.3.12\n"
                "       Prefer an existing interpreter? PYTHON=/path/to/python3 ./build_…"
            )

        venv_py = self.create_venv(interpreter)
        if venv_py is None:
            return 1
        python_exe = str(venv_py)
        if not self.install(python_exe, stream=True):
            return 1
        missing = self.verify(python_exe)
        if missing:
            return self.fail(
                "dependencies installed but still not importable:\n"
                + "\n".join(f"         - {name}: {err}" for name, err in missing)
                + "\n       Delete .venv-build and run the build again to start clean."
            )
        self.write_marker(python_exe, self.requirements_hash(python_exe))
        return self._done(python_exe)

    def _done(self, python_exe: str) -> int:
        self.step(f"Build environment ready: {self._describe(python_exe)}")
        # The machine-readable contract: build scripts read this line.
        print(f"PYTHON={python_exe}")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create/verify the Python build environment for this checkout.",
    )
    parser.add_argument("--check", action="store_true", help="verify only; never create or install anything")
    parser.add_argument("--force", action="store_true", help="reinstall the requirement sets even if the marker matches")
    parser.add_argument(
        "--no-install",
        action="store_true",
        default=os.environ.get("INNIOASIS_NO_AUTO_INSTALL") == "1",
        help="never download a Python interpreter (uv/Homebrew/winget)",
    )
    parser.add_argument("--quiet", action="store_true", help="print errors only")
    args = parser.parse_args(argv)

    helper = Bootstrapper(quiet=args.quiet, allow_install=not args.no_install, force=args.force)
    helper.say("Preparing the build environment for this checkout")
    return helper.run(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
