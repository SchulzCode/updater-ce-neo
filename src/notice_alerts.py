"""Per-category system notifications for notices, app updates and player updates.

A notice is announced on the desktop only when the user left its category on.
The switches live under Settings -> Enable Notifications; the master switch
follows whether this computer can show system notifications at all.

Categories map onto what a notice *is* rather than onto its severity alone:

- general (``info``): announcements. Off by default, since most are readable
  in the app whenever the user wants them.
- warning (``warning`` and ``critical``): safety advice. On by default.
- player_update: a newer player firmware release. On by default.
- app_update: a newer Updater CE build. On by default.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)

SETTINGS_ORG = "innioasis"
SETTINGS_APP = "updater"

NOTIFICATIONS_ENABLED_KEY = "notifications_enabled"
SEEN_NOTICE_IDS_KEY = "notified_notice_ids"

CATEGORY_GENERAL = "general"
CATEGORY_WARNING = "warning"
CATEGORY_PLAYER = "player_update"
CATEGORY_APP = "app_update"

_CATEGORY_SETTINGS = {
    CATEGORY_GENERAL: ("notify_general_notices", False),
    CATEGORY_WARNING: ("notify_warning_notices", True),
    CATEGORY_PLAYER: ("notify_player_updates", True),
    CATEGORY_APP: ("notify_app_updates", True),
}


def _settings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def _read_bool(key: str, default: bool) -> bool:
    value = _settings().value(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def system_notifications_available() -> bool:
    """Whether this computer has any channel that can deliver a notification."""
    from .notifications import system_notifications_available as available

    return available()


def notifications_enabled() -> bool:
    """The master switch. Without a delivery channel it reads as off."""
    if not system_notifications_available():
        return False
    return _read_bool(NOTIFICATIONS_ENABLED_KEY, True)


def set_notifications_enabled(checked: bool) -> None:
    _settings().setValue(NOTIFICATIONS_ENABLED_KEY, bool(checked))


def category_preference(category: str) -> bool:
    """The stored switch for a category, whether or not the master is on."""
    key, default = _CATEGORY_SETTINGS.get(category, ("", False))
    if not key:
        return False
    return _read_bool(key, default)


def category_enabled(category: str) -> bool:
    """True only when notifications are on *and* this category is on."""
    if not notifications_enabled():
        return False
    return category_preference(category)


def set_category_enabled(category: str, checked: bool) -> None:
    key, _default = _CATEGORY_SETTINGS.get(category, ("", False))
    if not key:
        return
    _settings().setValue(key, bool(checked))


def app_updates_enabled() -> bool:
    return category_enabled(CATEGORY_APP)


def player_updates_enabled() -> bool:
    return category_enabled(CATEGORY_PLAYER)


def notice_category(notice) -> str:
    """The settings category a notice belongs to."""
    kind = str(getattr(notice, "kind", "") or "").strip().lower()
    if kind in (CATEGORY_PLAYER, "player-update"):
        return CATEGORY_PLAYER
    if kind in (CATEGORY_APP, "app-update"):
        return CATEGORY_APP
    severity = str(getattr(notice, "severity", "info") or "info").strip().lower()
    if severity in ("warning", "critical"):
        return CATEGORY_WARNING
    return CATEGORY_GENERAL


def seen_notice_ids() -> set[str]:
    s = _settings()
    value = s.value(SEEN_NOTICE_IDS_KEY, [])
    if isinstance(value, str):
        return {part for part in value.split(",") if part}
    if isinstance(value, (list, tuple, set)):
        return {str(part) for part in value if part}
    return set()


def mark_notices_seen(ids: Iterable[str]) -> None:
    """Remember notices handled once, so a refresh never repeats a notification."""
    fresh = {str(i) for i in ids if i}
    if not fresh:
        return
    current = seen_notice_ids()
    if fresh <= current:
        return
    s = _settings()
    s.setValue(SEEN_NOTICE_IDS_KEY, sorted(current | fresh))
    # Written now, not at shutdown: a crash must not replay old notices.
    s.sync()


def unseen_notices(notices) -> list:
    """Notices this install has never processed before."""
    seen = seen_notice_ids()
    return [n for n in (notices or []) if getattr(n, "id", "") not in seen]


def notify_notice(notice, tray=None, route: str = "notices") -> bool:
    """Send one notice as a system notification, if its category is on."""
    if notice is None or not category_enabled(notice_category(notice)):
        return False
    title = str(getattr(notice, "title", "") or getattr(notice, "id", "") or "")
    body = str(getattr(notice, "content", "") or "")
    return deliver(title, body, tray=tray, route=route)


def deliver(title: str, message: str, tray=None, route: str = "") -> bool:
    """Hand a notification to the platform channel. Never raises."""
    try:
        from .notifications import show_system_notification

        return bool(show_system_notification(title, message, tray=tray, route=route))
    except Exception:
        logger.debug("System notification failed", exc_info=True)
        return False
