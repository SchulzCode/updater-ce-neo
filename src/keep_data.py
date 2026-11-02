"""Keep Data eligibility from the catalogue's derivative relationships.

``slidia_manifest.xml`` may give a package a ``derivative`` attribute naming
the repository it was built from. Firmware with the same root (the original
software) shares a data partition, so a Keep Data install is safe when the
software already tracked for the model is the same firmware or a relative of
the incoming one. A firmware with no derivative is only compatible with
itself.

The helpers here are deliberately pure over ``FirmwarePackage`` objects so the
same rules can be tested without a window or a device.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QSettings

from . import catalog
from .player_update_check import get_device_install

ALLOW_ANY_KEY = "allow_keep_data_any_firmware"


def _norm(repo: str) -> str:
    return str(repo or "").strip().replace("\\", "/").strip("/").casefold()


def allow_keep_data_any(settings: Optional[QSettings] = None) -> bool:
    """The advanced override, off by default."""
    s = settings or QSettings("innioasis", "updater")
    return bool(s.value(ALLOW_ANY_KEY, False, type=bool))


def set_allow_keep_data_any(enabled: bool, settings: Optional[QSettings] = None) -> None:
    s = settings or QSettings("innioasis", "updater")
    s.setValue(ALLOW_ANY_KEY, bool(enabled))


def _packages(model: str = "") -> list:
    packages = catalog.packages_for_model(model) if model else list(catalog.LIVE_CATALOG or catalog.CATALOG)
    return list(packages or [])


def _package_for_repo(repo: str, packages: Iterable) -> object | None:
    wanted = _norm(repo)
    if not wanted:
        return None
    for pkg in packages or ():
        if _norm(getattr(pkg, "repo", "")) == wanted:
            return pkg
    return None


def root_repo(pkg, packages: Optional[Iterable] = None) -> str:
    """Follow ``derivative`` upward and return the family's root repository."""
    packages = list(packages if packages is not None else _packages(getattr(pkg, "model", "")))
    repo = _norm(getattr(pkg, "repo", ""))
    seen: set[str] = set()
    while repo and repo not in seen:
        seen.add(repo)
        parent = _package_for_repo(repo, packages)
        derivative = _norm(getattr(parent, "derivative", "")) if parent is not None else ""
        if not derivative or derivative == repo:
            break
        repo = derivative
    return repo


def family_repos(pkg, packages: Optional[Iterable] = None) -> set[str]:
    """Every repository in the same derivative family as ``pkg``."""
    packages = list(packages if packages is not None else _packages(getattr(pkg, "model", "")))
    root = root_repo(pkg, packages)
    if not root:
        return set()
    family: set[str] = set()
    for candidate in packages:
        repo = _norm(getattr(candidate, "repo", ""))
        if repo and root_repo(candidate, packages) == root:
            family.add(repo)
    return family


def compatible_packages(pkg, model: str = "") -> list:
    """Catalogue packages whose data partition matches ``pkg``."""
    packages = _packages(model or getattr(pkg, "model", ""))
    family = family_repos(pkg, packages)
    return [p for p in packages if _norm(getattr(p, "repo", "")) in family]


def compatible_software_names(pkg, model: str = "") -> list:
    """Software names compatible with ``pkg``, original software first."""
    packages = compatible_packages(pkg, model)
    names: list[str] = []
    for candidate in packages:
        name = str(getattr(candidate, "name", "") or "").strip()
        if name and name not in names:
            names.append(name)
    names.sort(key=lambda n: (n.casefold() != "original software", n.casefold()))
    return names


def tracked_package(model: str, pkg, settings: Optional[QSettings] = None):
    """The tracked install's catalogue package, or ``None`` when unknown."""
    record = get_device_install(model, settings=settings)
    if not record:
        return None
    name = str(record.get("software_name") or "").strip()
    packages = _packages(model)
    for candidate in packages:
        if name and str(candidate.name or "").casefold() == name.casefold():
            return candidate
    slug = str(record.get("package_slug") or "").strip()
    for candidate in packages:
        if slug and str(candidate.slug or "") == slug:
            return candidate
    return None


def tracked_is_compatible(model: str, pkg, settings: Optional[QSettings] = None) -> Optional[bool]:
    """True/False for a tracked install, ``None`` when nothing is tracked."""
    tracked = tracked_package(model, pkg, settings=settings)
    if tracked is None:
        return None
    if _norm(getattr(tracked, "repo", "")) == _norm(getattr(pkg, "repo", "")):
        return True
    return bool(family_repos(pkg).intersection(family_repos(tracked)))


def keep_data_available(model: str, pkg, settings: Optional[QSettings] = None) -> bool:
    """Whether the Keep Data choice may be offered for this install."""
    if pkg is None:
        return True
    if allow_keep_data_any(settings):
        return True
    tracked = tracked_is_compatible(model, pkg, settings=settings)
    return tracked is not False


def needs_confirmation(model: str, pkg, settings: Optional[QSettings] = None) -> bool:
    """True when the user must confirm the currently installed firmware."""
    if pkg is None or allow_keep_data_any(settings):
        return False
    return tracked_is_compatible(model, pkg, settings=settings) is None


def keep_data_label(model: str) -> str:
    """One novice-facing label for every model; the warning carries the detail."""
    from .i18n import tr

    return tr("flash_keep_data_option")
