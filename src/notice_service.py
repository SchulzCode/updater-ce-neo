"""Remote notice fetching and caching service (innioasis.app/notice.xml).

Fetches announcements and public notices from https://innioasis.app/notice.xml,
caches them locally, and determines which notices should be shown to the user.

Notices support:
- title: headline text
- content: notice description (also supports body)
- persistent: bool - whether the notice persists across app runs even after
  dismissal (persistent=true returns on every app launch; persistent=false
  can be dismissed permanently).
- type: optional "player_update" or "app_update" marker, used only to decide
  which notification category a notice belongs to.
- Dismissals are per notice, not per file: a changed notice.xml shows its new
  notices and keeps the ones the user already dismissed out of the list.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, QSettings, Signal

from .paths import app_cache_dir

logger = logging.getLogger(__name__)

NOTICE_URL = "https://innioasis.app/notice.xml"
_SETTINGS_DISMISSED_KEY = "dismissed_notice_ids"
_SETTINGS_DISMISSED_FINGERPRINTS_KEY = "dismissed_notice_fingerprints"
_SETTINGS_CACHED_HASH_KEY = "notice_xml_content_hash"


@dataclass
class Notice:
    id: str
    title: str
    content: str
    persistent: bool = False
    severity: str = "info"
    # Feed marker for release notices; blank means an ordinary announcement.
    kind: str = ""
    # ``content`` is the banner text; ``full_content`` (falls back to ``content``)
    # is the body shown in the notices pop-out. ``url`` opens the See More page.
    full_content: str = ""
    url: str = ""
    # ``from`` is when the notice was posted (a date or a timestamp); the app
    # shows only this. ``until`` is the date the notice stops being shown.
    from_date: Optional[datetime] = None
    until_date: Optional[date] = None
    models: tuple[str, ...] = ()
    # CSV of platforms this notice targets (``mac,windows``); empty means all.
    platforms: tuple[str, ...] = ()

    def is_active_for(self, model: str = "", platform: str = "") -> bool:
        """Check if notice is within its date window and matches the given model."""
        today = date.today()
        if self.from_date and today < self.from_date.date():
            return False
        if self.until_date and today > self.until_date:
            return False
        if self.models and model:
            target = model.strip().upper()
            if not any(m.strip().upper() == target for m in self.models):
                return False
        if self.platforms:
            wanted = {p.strip().lower() for p in self.platforms if p.strip()}
            if "none" in wanted:
                return False
            if platform and platform.strip().lower() not in wanted:
                return False
        return True


def notice_fingerprint(notice: Notice) -> str:
    """Stable digest of the words a reader sees in one notice.

    Dismissal is remembered against this digest, not just the id, so editing a
    notice's title or body makes it visible again while an untouched notice
    stays dismissed.
    """
    text = "\n".join(
        str(part or "").strip()
        for part in (notice.title, notice.content, notice.full_content)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def current_platform() -> str:
    """Platform name used by ``platform=`` in notice.xml."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "linux"
    return ""


def _parse_bool(val: str | None) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "yes", "persistent")


_TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _parse_timestamp(val: str | None) -> Optional[datetime]:
    """Parse a posting time: ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM[:SS]``."""
    if not val:
        return None
    text = val.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_date(val: str | None) -> Optional[date]:
    parsed = _parse_timestamp(val)
    return parsed.date() if parsed else None


def is_battery_reminder(notice: Notice) -> bool:
    """True for a "charge the battery" notice.

    Select Software already carries the app's own battery reminder toast, so a
    feed notice that repeats it does not need to appear in the Notices list or
    arrive as a system notification. Any notice that both names the battery and
    asks for a charge is treated as that reminder.
    """
    text = f"{notice.title} {notice.content} {notice.full_content}".lower()
    return "battery" in text and "charg" in text


def parse_notice_xml(xml_content: str) -> list[Notice]:
    """Parse notice.xml string into a list of Notice objects."""
    if not xml_content or not xml_content.strip():
        return []
    try:
        root = ET.fromstring(xml_content)
    except Exception as e:
        logger.debug("Failed to parse notice XML: %s", e)
        return []

    notices: list[Notice] = []
    # Find all <notice> elements (under root or nested)
    elements = root.findall(".//notice") if root.tag != "notice" else [root]
    for el in elements:
        notice_id = el.get("id", "").strip()
        if not notice_id:
            continue

        # Title
        title_el = el.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        # Content (supports <content> or <body>)
        content_el = el.find("content")
        if content_el is None:
            content_el = el.find("body")
        content = content_el.text.strip() if content_el is not None and content_el.text else ""

        if not title and not content:
            continue

        # Persistence boolean (attribute on <notice> or child element)
        persistent = _parse_bool(el.get("persistent"))
        persistent_el = el.find("persistent")
        if persistent_el is not None and persistent_el.text:
            persistent = _parse_bool(persistent_el.text)

        severity = el.get("severity", "info").strip().lower()
        if severity not in ("info", "warning", "critical"):
            severity = "info"

        # Release notices can say what they are, so the notification settings
        # can keep player releases and app releases apart from announcements.
        kind_raw = (el.get("type") or "").strip().lower().replace("-", "_")
        kind = kind_raw if kind_raw in ("player_update", "app_update") else ""

        full_el = el.find("full_content")
        full_content = full_el.text.strip() if full_el is not None and full_el.text else ""
        if not full_content:
            full_content = content

        url_el = el.find("url")
        url = url_el.text.strip() if url_el is not None and url_el.text else ""

        from_d = _parse_timestamp(el.get("from"))
        until_d = _parse_date(el.get("until"))

        models_raw = el.get("models", "")
        models = tuple(m.strip() for m in models_raw.split(",") if m.strip())
        platforms_raw = el.get("platform", "")
        platforms = tuple(
            p.strip().lower() for p in platforms_raw.split(",") if p.strip()
        )

        notice = Notice(
            id=notice_id,
            title=title,
            content=content,
            persistent=persistent,
            severity=severity,
            kind=kind,
            full_content=full_content,
            url=url,
            from_date=from_d,
            until_date=until_d,
            models=models,
            platforms=platforms,
        )
        # The app already carries the one-time battery toast on Select
        # Software. A feed notice repeating it is a separate thing and never
        # enters the notice system at all.
        if is_battery_reminder(notice):
            continue
        notices.append(notice)
    return notices


class NoticeService(QObject):
    """Background service to fetch, cache, and manage notice.xml announcements."""

    notice_ready = Signal(object)  # Emits Notice or None
    notices_ready = Signal(object)  # Emits list[Notice] of every active notice (for the list)
    # A refresh is a background fetch; the pages that show notices use these to
    # run a throbber for exactly as long as one is in flight.
    refresh_started = Signal()
    refresh_finished = Signal()

    _instance: NoticeService | None = None

    @classmethod
    def instance(cls) -> NoticeService:
        if cls._instance is None:
            cls._instance = NoticeService()
        return cls._instance

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._cache_dir = app_cache_dir()
        self._cache_file = self._cache_dir / "notice.xml"
        self._session_dismissed: set[str] = set()
        self._active_notices: list[Notice] = []
        self._current_model: str = ""

    @property
    def active_notices(self) -> list[Notice]:
        """Notices currently shown for this model, with dismissals respected."""
        dismissed_past = self.get_dismissed_ids()
        dismissed_fingerprints = self.get_dismissed_fingerprints()
        platform = current_platform()
        visible: list[Notice] = []
        for notice in self._active_notices:
            if is_battery_reminder(notice):
                # The dedicated toast covers this; do not repeat it as a
                # notice or notify about it.
                continue
            if notice.id in self._session_dismissed:
                continue
            if not notice.persistent and notice.id in dismissed_past:
                # A legacy dismissal has no digest: keep it dismissed. Once a
                # notice has a digest, only the same words stay hidden.
                stored = dismissed_fingerprints.get(notice.id, "")
                if not stored or stored == notice_fingerprint(notice):
                    continue
            if not notice.is_active_for(self._current_model, platform):
                continue
            visible.append(notice)
        return visible

    def _snapshot_text(self) -> str:
        """The newest local notice.xml: this install's cache, then the bundled copy."""
        try:
            if self._cache_file.exists():
                text = self._cache_file.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    return text
        except OSError:
            pass
        try:
            from . import online_data

            return online_data.read_text("notice.xml") or ""
        except Exception:
            return ""

    def get_dismissed_ids(self) -> set[str]:
        """IDs of non-persistent notices that were dismissed in past runs."""
        s = QSettings("Innioasis", "Innioasis Updater")
        val = s.value(_SETTINGS_DISMISSED_KEY, [])
        if isinstance(val, str):
            return set(val.split(",")) if val else set()
        if isinstance(val, (list, tuple, set)):
            return set(str(x) for x in val if x)
        return set()

    def set_dismissed_ids(self, ids: set[str]) -> None:
        s = QSettings("Innioasis", "Innioasis Updater")
        if ids:
            s.setValue(_SETTINGS_DISMISSED_KEY, sorted(ids))
        elif hasattr(s, "remove"):
            # Remove rather than store an empty array: macOS keeps the old
            # value when an empty list is written, which made a cleared
            # dismissal come back on the next launch.
            s.remove(_SETTINGS_DISMISSED_KEY)
        else:
            s.setValue(_SETTINGS_DISMISSED_KEY, [])
        sync = getattr(s, "sync", None)
        if callable(sync):
            sync()

    def get_dismissed_fingerprints(self) -> dict[str, str]:
        """Digest of each dismissed notice's wording, keyed by notice id."""
        raw = QSettings("Innioasis", "Innioasis Updater").value(
            _SETTINGS_DISMISSED_FINGERPRINTS_KEY, ""
        )
        if not raw:
            return {}
        try:
            data = json.loads(str(raw))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if k and v}

    def set_dismissed_fingerprints(self, values: dict[str, str]) -> None:
        s = QSettings("Innioasis", "Innioasis Updater")
        if values:
            s.setValue(_SETTINGS_DISMISSED_FINGERPRINTS_KEY, json.dumps(values, sort_keys=True))
        elif hasattr(s, "remove"):
            s.remove(_SETTINGS_DISMISSED_FINGERPRINTS_KEY)
        else:
            s.setValue(_SETTINGS_DISMISSED_FINGERPRINTS_KEY, "")
        sync = getattr(s, "sync", None)
        if callable(sync):
            sync()

    def dismiss_notice(self, notice: Notice) -> None:
        """Dismiss a notice.

        If persistent=False, it is recorded in QSettings so it never returns
        on subsequent app runs (unless remote XML changes).
        If persistent=True, it is dismissed only for the current app session.
        """
        self._session_dismissed.add(notice.id)
        if not notice.persistent:
            dismissed = self.get_dismissed_ids()
            dismissed.add(notice.id)
            self.set_dismissed_ids(dismissed)
            fingerprints = self.get_dismissed_fingerprints()
            fingerprints[notice.id] = notice_fingerprint(notice)
            self.set_dismissed_fingerprints(fingerprints)
        self._emit_next_notice()

    def set_model(self, model: str) -> None:
        """Update target device model and re-evaluate active notice."""
        self._current_model = model or ""
        self._emit_next_notice()

    def fetch_async(self, force: bool = True) -> None:
        """Fetch remote notice.xml in a background thread."""
        self.refresh_started.emit()
        thread = threading.Thread(target=self._fetch_worker, args=(force,), daemon=True)
        thread.start()

    def start_passive_refresh(self, interval_ms: int = 15 * 60 * 1000) -> None:
        """Re-check the feed on a calm interval.

        Notices change rarely, so this is minutes-scale background work rather
        than polling: the readers sees a throbber only when they are looking at
        the notices page at that moment.
        """
        from PySide6.QtCore import QTimer

        if getattr(self, "_passive_timer", None) is None:
            self._passive_timer = QTimer(self)
            self._passive_timer.timeout.connect(lambda: self.fetch_async(force=True))
        self._passive_timer.start(max(60_000, int(interval_ms)))

    def _fetch_worker(self, force: bool = True) -> None:
        """Run a fetch and always report completion, whatever happened."""
        try:
            self._fetch_worker_inner(force=force)
        finally:
            self.refresh_finished.emit()

    def _fetch_worker_inner(self, force: bool = True) -> None:
        from .i18n import current_language, is_english_language

        lang = current_language()
        # 1. Show the newest local copy first -- this install's cache, else the
        #    snapshot built into the app -- so launch waits for neither the
        #    network nor Google Translate (translations come from the disk cache).
        local_content = self._snapshot_text()
        if local_content:
            try:
                self._active_notices = self._localised(
                    parse_notice_xml(local_content), lang, cached_only=True
                )
            except Exception as e:
                logger.debug("Local notice.xml could not be read: %s", e)
            self._emit_next_notice()

        # 2. Replace the local copy only when it is stale. The app-wide cache is
        #    shared with the catalogue, donors and blocklist, so one refresh
        #    keeps every online file current; a failed fetch keeps what we have.
        try:
            from . import online_data

            # A notice is tiny, and the point of the poll is to discover a
            # newly committed notice without waiting out the file's TTL.
            online_data.refresh("notice.xml", force=True)
            remote_content = online_data.cached_text("notice.xml") or ""
        except Exception as e:
            logger.debug("Notice refresh failed: %s", e)
            return
        if not remote_content:
            return

        content_changed = remote_content.strip() != local_content.strip()
        if content_changed:
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._cache_file.write_text(remote_content, encoding="utf-8")
            except Exception as e:
                logger.debug("Failed writing notice.xml to cache: %s", e)

            # Dismissals follow notices, not the file: a new notice has a new
            # id and appears, while every notice already dismissed stays out
            # of the list even when the feed around it changes.

        # Translate new or changed notices (cached per notice and language), then show them.
        if content_changed or not local_content or not is_english_language(lang):
            self._active_notices = self._localised(parse_notice_xml(remote_content), lang)
            self._emit_next_notice()

    def reload_translations(self) -> None:
        """Re-render the cached notices in the language just selected.

        Runs off the UI thread because a first-time translation may take a
        moment; the result arrives through ``notices_ready`` like a refresh.
        """
        thread = threading.Thread(target=self._reload_translations_worker, daemon=True)
        thread.start()

    def _reload_translations_worker(self) -> None:
        from .i18n import current_language

        try:
            text = self._snapshot_text()
            if not text.strip():
                return
            translated = self._localised(parse_notice_xml(text), current_language())
            self._active_notices = translated
            self._emit_next_notice()
        except Exception:
            logger.debug("Could not re-render notices for the new language", exc_info=True)
        finally:
            self.refresh_finished.emit()

    def _localised(self, notices: list[Notice], lang: str, cached_only: bool = False) -> list[Notice]:
        """Return notices with title/content in ``lang`` (Google Translate, disk-cached).

        ``cached_only`` uses only the translation cache so launch never waits on the network.
        """
        from .i18n import is_english_language
        from .translate import fetch_google_translation

        if is_english_language(lang):
            return notices
        out: list[Notice] = []
        for n in notices:
            out.append(self._translation_for(n, lang, cached_only, fetch_google_translation))
        return out

    def _translation_for(self, n: Notice, lang: str, cached_only: bool, translate) -> Notice:
        """Return ``n`` with its text in ``lang``, cached on disk per notice and language."""
        key = hashlib.sha256(
            f"{n.id}\n{n.title}\n{n.content}\n{n.full_content}".encode("utf-8")
        ).hexdigest()[:24]
        path = self._cache_dir / "notice_translations" / f"{lang}_{key}.json"
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return replace(n, title=data["title"], content=data["content"], full_content=data["full_content"])
        except Exception:
            logger.debug("Unreadable notice translation cache", exc_info=True)
        if cached_only:
            return n
        title = translate(n.title, lang) if n.title else n.title
        content = translate(n.content, lang) if n.content else n.content
        full = translate(n.full_content, lang) if n.full_content else n.full_content
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"title": title, "content": content, "full_content": full}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("Could not cache notice translation", exc_info=True)
        return replace(n, title=title, content=content, full_content=full)

    def replay(self) -> None:
        """Emit the current state to a listener that connected late.

        Select Software starts the first fetch while the window is still being
        built, so the initial answer can arrive before the rail connects. A
        replay gives that listener the real list instead of an empty one.
        """
        self._emit_next_notice()

    def _emit_next_notice(self) -> None:
        # The modal lists every active notice; the banner shows the first one.
        visible = self.active_notices
        self.notices_ready.emit(visible)
        self.notice_ready.emit(visible[0] if visible else None)
