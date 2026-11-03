"""Ephemeral working state for a portable (bundled firmware) run.

A portable package auto-starts its firmware install: the macOS build finds a
``Firmware`` folder beside the ``.app``, Windows and Linux find a scatter file
in the packaged SP Flash Tool directory. Staging, extraction, icons and the
catalogue cache all live in the user's application-support/cache folders for
the length of that run.

Such a run must leave nothing behind. A snapshot taken before staging records
what was already in those folders; everything that appears afterwards belongs
to this run and is removed when the user finishes (the completion card offers
to install the app properly first). Pre-existing files — an installed copy's
cache, a firmware the user downloaded earlier — are never touched.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ``progress(percent, label_key)`` — the label is a translation key so this
# module stays free of UI imports.
Progress = Callable[[int, str], None]


def _downloads_dir() -> Path:
    """The app's working folder (staged firmware, archives, extractions)."""
    from .downloads import downloads_dir

    return downloads_dir()


def _cache_dir() -> Path:
    """The app's cache (manifest, icons, docs)."""
    from .paths import app_cache_dir

    return app_cache_dir()


def _temp_dir() -> Path:
    """The system temporary folder the flashing and themes stages work in."""
    return Path(tempfile.gettempdir())


#: Working directories the flashing and Rockbox themes stages create in the
#: system temporary folder. They are app-specific names, never another tool's.
_TEMP_PREFIXES = (
    "preloader_wrapped_",
    "innioasis_readback_",
    "rockbox_360p_",
    "rockbox_fallback_",
    "rockbox_payload_",
)


def _names(folder: Path) -> set[str]:
    try:
        return {entry.name for entry in folder.iterdir()}
    except OSError:
        return set()


def _tree_entries(folder: Path) -> set[str]:
    """Every file and directory below ``folder``, relative to it."""
    entries: set[str] = set()
    if not folder.is_dir():
        return entries
    try:
        for root, dirs, files in os.walk(folder):
            root_path = Path(root)
            for name in dirs:
                entries.add(str((root_path / name).relative_to(folder)))
            for name in files:
                entries.add(str((root_path / name).relative_to(folder)))
    except OSError:
        pass
    return entries


def _remove(path: Path) -> bool:
    """Remove one file or directory tree; a failure never aborts the pass."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            return False
    except OSError as exc:
        logger.warning("Portable cleanup could not remove %s: %s", path, exc)
        return False
    return True


def _clean_tree(folder: Path, before: set[str]) -> list[str]:
    """Remove entries below ``folder`` that were absent from the snapshot."""
    removed: list[str] = []
    if not folder.is_dir():
        return removed
    try:
        for root, dirs, files in os.walk(folder, topdown=False):
            root_path = Path(root)
            for name in files:
                path = root_path / name
                if str(path.relative_to(folder)) in before:
                    continue
                if _remove(path):
                    removed.append(str(path))
            for name in dirs:
                path = root_path / name
                if str(path.relative_to(folder)) in before:
                    continue
                try:
                    if path.is_dir() and not any(path.iterdir()):
                        path.rmdir()
                        removed.append(str(path))
                except OSError:
                    logger.debug("Could not remove portable directory %s", path)
    except OSError:
        logger.debug("Could not walk %s for portable cleanup", folder)
    return removed


class PortableSession:
    """The working files a portable run may create, and their cleanup."""

    def __init__(self, started_at: Optional[float] = None):
        self.started_at = float(started_at if started_at is not None else time.time())
        self._downloads = _downloads_dir()
        self._cache = _cache_dir()
        self._temp = _temp_dir()
        self._downloads_existed = self._downloads.is_dir()
        self._cache_existed = self._cache.is_dir()
        self._downloads_before = _tree_entries(self._downloads)
        self._cache_before = _tree_entries(self._cache)
        self._temp_before = _names(self._temp)

    def targets(self) -> list[Path]:
        """Everything cleanup would remove, without removing it."""
        targets = {
            self._downloads / str(relative)
            for relative in _tree_entries(self._downloads) - self._downloads_before
        }
        targets.update(
            self._cache / str(relative)
            for relative in _tree_entries(self._cache) - self._cache_before
        )
        return sorted(targets)

    def cleanup(self, progress: Optional[Progress] = None) -> list[str]:
        """Remove this run's working files, reporting three progress steps."""
        steps = (
            ("portable_cleanup_downloads", self._clean_downloads),
            ("portable_cleanup_cache", self._clean_cache),
            ("portable_cleanup_temp", self._clean_temp),
        )
        removed: list[str] = []
        for index, (key, step) in enumerate(steps, start=1):
            try:
                removed.extend(step())
            except Exception:  # never let one stubborn file stop the quit
                logger.warning("Portable cleanup step %s failed", key, exc_info=True)
            if progress is not None:
                progress(int(round(index * 100 / len(steps))), key)
        if progress is not None:
            progress(100, "portable_cleanup_done")
        return removed

    def _clean_downloads(self) -> list[str]:
        return _clean_tree(self._downloads, self._downloads_before)

    def _clean_cache(self) -> list[str]:
        return _clean_tree(self._cache, self._cache_before)

    def _clean_temp(self) -> list[str]:
        """Remove this run's temporary work directories, then empty shells."""
        removed: list[str] = []
        for name in sorted(_names(self._temp)):
            if name in self._temp_before:
                continue
            if not name.startswith(_TEMP_PREFIXES):
                continue
            if _remove(self._temp / name):
                removed.append(str(self._temp / name))
        removed.extend(self._clean_shells())
        return removed

    def _clean_shells(self) -> list[str]:
        """Drop the two folders themselves when this run created them empty."""
        removed: list[str] = []
        for folder, existed in (
            (self._downloads, self._downloads_existed),
            (self._cache, self._cache_existed),
        ):
            if existed:
                continue
            try:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
                    removed.append(str(folder))
            except OSError:
                logger.debug("Could not remove empty portable folder %s", folder)
        return removed
