"""One-shot language handoff from the Windows installer.

Inno Setup writes its active language to
``%LOCALAPPDATA%\\Updater CE\\install-language`` after a fresh install. The app
reads that marker on the next launch, records it as the normal language
preference, and removes it.

The marker deliberately lives outside ``{app}``: Program Files is read-only to
the installed user, so the app could read a marker there but could not delete
it after use.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import i18n, paths

logger = logging.getLogger(__name__)

MARKER_NAME = "install-language"


def marker_path() -> Path:
    """The one-shot marker the Windows installer writes."""
    return paths.app_cache_dir().parent / MARKER_NAME


def language_for_installer(name: str) -> str:
    """Map an Inno Setup language name to a language the app ships.

    Inno names are the internal names from ``[Languages]`` (``german``,
    ``chinesesimplified``). The shortcut map already owns that correspondence,
    so this reuses it instead of growing a second list. Only languages with an
    actual app catalog are returned; an installer language the app cannot show
    must leave the normal system-language detection in charge.
    """
    key = (name or "").strip().lower()
    if not key:
        return ""
    try:
        from .shortcut_manager import INNO_SHORTCUT_LANGUAGES

        codes = INNO_SHORTCUT_LANGUAGES.get(key, ())
    except Exception:
        logger.debug("Installer language map unavailable", exc_info=True)
        return ""
    available = set(i18n.catalog_codes())
    for code in codes:
        if code in available:
            return code
    return ""


def consume_installer_language(
    settings: Any = None, path: Path | str | None = None
) -> str:
    """Apply and remove the installer's one-shot language marker.

    The marker only exists after a fresh install: updates and existing installs
    skip writing it. It is therefore authoritative and replaces anything a
    stored value or the host's language detection would otherwise choose, so
    the language picked in the installer is the language the app opens in and
    the preference it keeps. The marker is removed even when its language is
    unknown, so a stale handoff never affects a later launch.
    """
    if path is None:
        if not paths.IS_WINDOWS:
            return ""
        marker = marker_path()
    else:
        marker = Path(path)

    try:
        name = marker.read_text(encoding="utf-8-sig", errors="replace").strip()
    except FileNotFoundError:
        return ""
    except OSError:
        logger.debug("Could not read installer language marker", exc_info=True)
        return ""

    try:
        marker.unlink()
    except OSError:
        logger.debug("Could not remove installer language marker", exc_info=True)

    code = language_for_installer(name)
    if not code:
        logger.debug("Installer language %r has no app catalog", name)
        return ""

    if settings is not None:
        try:
            settings.setValue("language", code)
        except Exception:
            logger.debug("Could not store installer language", exc_info=True)
            return ""
        sync = getattr(settings, "sync", None)
        if callable(sync):
            try:
                sync()
            except Exception:
                logger.debug("Could not flush installer language", exc_info=True)
    return code
