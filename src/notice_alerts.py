"""Per-category system notifications for notices, app updates and player updates.

A notice is announced on the desktop only when the user left its category on.
The switches live under Settings -> Enable Notifications; the master switch
follows whether this computer can show system notifications at all.

Categories map onto what a notice *is* rather than onto its severity alone:

- general (``info``): announcements. On by default: a new notice is meant to
  reach the user on the desktop even when the window is hidden; the switch in
  Settings turns it off.
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
    CATEGORY_GENERAL: ("notify_general_notices", True),
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


_SUMMARY_FALLBACK_ONE = "New Community Notice"
_SUMMARY_FALLBACK_MANY = "{count} New Community Notices"
_SUMMARY_FALLBACK_BODY = "Open Notices in the app to read them."


def _summary_copy(key: str, fallback: str, *, forbidden=(), **values) -> str:
    """Localized summary copy, never a catalogue key or notice identifier.

    A packaged build can lag behind the catalogue. When a key has no entry, a
    catalogue entry contains the key, or a bad entry is just a notice ID, use
    readable English instead of putting an identifier in the operating system's
    notification title.
    """
    from .i18n import tr

    text = str(tr(key) or "").strip()
    forbidden_ids = {str(value).strip() for value in forbidden if str(value).strip()}
    if (
        not text
        or text == key
        or text.startswith("notices_summary_")
        or text in forbidden_ids
    ):
        text = fallback
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return fallback.format(**values)


def _notice_headline(notice) -> str:
    """A notice's human headline, never its feed identifier."""
    title = str(getattr(notice, "title", "") or "").strip()
    identifier = str(getattr(notice, "id", "") or "").strip()
    return title if title and title != identifier else ""


def notify_new_notices(notices, tray=None, route: str = "notices") -> bool:
    """Announce a batch of new notices as one counted notification.

    A feed that publishes three items must not raise three banners: the user
    gets a single "3 New Community Notices" and opening it shows the list. Only
    notices still unseen are passed in, and only categories the user kept on
    are counted, so a category switched off never inflates the number.
    """
    fresh = [n for n in (notices or []) if category_enabled(notice_category(n))]
    if not fresh:
        return False
    notice_ids = [getattr(notice, "id", "") for notice in fresh]
    if len(fresh) == 1:
        title = _summary_copy(
            "notices_summary_title_one",
            _SUMMARY_FALLBACK_ONE,
            forbidden=notice_ids,
        )
        # The notice's own headline says more than a bare count would here.
        body = _notice_headline(fresh[0]) or _summary_copy(
            "notices_summary_body", _SUMMARY_FALLBACK_BODY
        )
    else:
        title = _summary_copy(
            "notices_summary_title_many",
            _SUMMARY_FALLBACK_MANY,
            forbidden=notice_ids,
            count=len(fresh),
        )
        body = _summary_copy("notices_summary_body", _SUMMARY_FALLBACK_BODY)
    # A click opens the notice itself, not merely the list. For a batch the
    # first (newest) notice is the one the banner's headline describes.
    target_route = route
    if route == "notices" and fresh:
        notice_id = str(getattr(fresh[0], "id", "") or "")
        if notice_id:
            target_route = f"notices:{notice_id}"
    return deliver(title, body, tray=tray, route=target_route)


def deliver(title: str, message: str, tray=None, route: str = "") -> bool:
    """Hand a notification to the platform channel. Never raises."""
    try:
        from .notifications import show_system_notification

        return bool(show_system_notification(title, message, tray=tray, route=route))
    except Exception:
        logger.debug("System notification failed", exc_info=True)
        return False
