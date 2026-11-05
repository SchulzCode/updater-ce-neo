"""The "something is waiting" notifications, shared by the window and the agent.

Both the main window and the headless background agent run the same app and
device update checks. Their notification copy, their per-category switches and
the once-per-version dedupe live here, so a build found while the window is up
is not announced a second time after the window has been shed -- and neither
side needs the other's widgets to post the banner.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from PySide6.QtCore import QSettings

from .config import APP_VERSION
from .i18n import tr

logger = logging.getLogger(__name__)

#: Last app version a banner was posted for. Persisted, so a restart (or a
#: rebuilt window) never announces the same build twice.
NOTIFIED_APP_UPDATE_KEY = "notified_app_update_version"


def _plain(text: str) -> str:
    """Notification bodies are plain text; catalogue copy can carry markup."""
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def notify_app_update(info, tray=None, force: bool = False) -> bool:
    """Announce a waiting app build; clicking opens its update view."""
    from . import notice_alerts

    if not force and not notice_alerts.app_updates_enabled():
        return False
    version = str(getattr(info, "version", "") or "")
    if not version:
        return False
    settings = QSettings("innioasis", "updater")
    if not force and settings.value(NOTIFIED_APP_UPDATE_KEY, "", type=str) == version:
        return False
    body = _plain(
        tr("update_available_fmt").format(new=version, current=APP_VERSION)
    )
    if not force:
        settings.setValue(NOTIFIED_APP_UPDATE_KEY, version)
    return notice_alerts.deliver(
        tr("update_available"), body, tray=tray, route="app_update"
    )


def notify_device_updates(updates: Iterable, tray=None) -> int:
    """Post one notification per device with a newer release. Returns the count.

    A release is announced once per model: the last tag is remembered before
    delivery, so the periodic check that follows does not repeat the banner.
    No window is involved, so the app is never brought to the front.
    """
    from . import notice_alerts, player_update_check

    if not notice_alerts.player_updates_enabled():
        return 0
    sent = 0
    title = tr("reminder_new_release_title")
    for update in updates or []:
        software = str((update or {}).get("software_name") or "")
        model = str((update or {}).get("model") or "")
        latest_tag = str((update or {}).get("latest_tag") or "")
        if not software or not model:
            continue
        if latest_tag and player_update_check.get_last_notified_tag(model) == latest_tag:
            continue
        if latest_tag:
            player_update_check.set_last_notified_tag(model, latest_tag)
        import json

        # The click must land on this release, not just on Select Software.
        route = "player_update:" + json.dumps(
            {"model": model, "software": software, "tag": latest_tag},
            separators=(",", ":"),
        )
        if notice_alerts.deliver(
            title,
            tr("sel_update_available").format(software=software, model=model),
            tray=tray,
            route=route,
        ):
            sent += 1
    return sent
