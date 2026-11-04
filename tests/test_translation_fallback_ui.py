"""A dead translation service must dim the control, not break the screen.

Updater CE translates release notes through free, no-auth services. Those
services go down, rate-limit, or get blocked on a corporate network. The app
has to keep working when they do: the Translate control dims, its hint explains
why on hover and pins on click (the same metaphor as the Type A/B ``(i)`` mark),
and one local notice lands in Notices so the situation is explained once rather
than on every release. Nothing in this path may raise.

These tests drive the methods on stand-ins. Building a real Select Software page
starts catalogue workers and icon preloads that have nothing to do with a
disabled link.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import notice_service, translate  # noqa: E402
from src.i18n import tr  # noqa: E402
from src.ui import dialogs, select_page  # noqa: E402


class _FakeNoticeService:
    def __init__(self):
        self.posted = []
        self.removed = []

    def post_local_notice(self, notice_id, title, content, **kwargs):
        self.posted.append((notice_id, title, content, kwargs))

    def remove_local_notice(self, notice_id):
        self.removed.append(notice_id)


@pytest.fixture
def notices(monkeypatch):
    fake = _FakeNoticeService()
    monkeypatch.setattr(
        notice_service.NoticeService, "instance", staticmethod(lambda: fake)
    )
    translate.reset_translation_cooldowns()
    return fake


class _PageHost:
    """The slice of SelectPackagePage the failure path touches."""

    _note_translation_unavailable = (
        select_page.SelectPackagePage._note_translation_unavailable
    )

    def __init__(self):
        self._current_selected_rel = {
            "tag_name": "v1",
            "name": "Release",
            "body": "Notes",
        }
        self._is_translating = True
        self._is_translated = False
        self._translated_notes_cache = {}
        self._translate_worker = None
        self._translation_notice_posted = False
        self.label_updates = 0

    def _update_translate_link(self):
        self.label_updates += 1


class _DialogHost:
    """The slice of the release-notes dialog the failure path touches."""

    _note_translation_unavailable = (
        dialogs.UpdateAvailableDialog._note_translation_unavailable
    )

    def __init__(self):
        self._is_translating = True
        self._translation_notice_posted = False
        self._translated = None
        self._info = type("I", (), {"tag": "v1"})()
        self.label_updates = 0

    def _render_notes(self):
        pass

    def _update_translate_label(self):
        self.label_updates += 1


def test_a_failed_translation_posts_one_local_notice(notices):
    host = _PageHost()

    select_page.SelectPackagePage._note_translation_unavailable(host)
    select_page.SelectPackagePage._note_translation_unavailable(host)

    assert len(notices.posted) == 1
    notice_id, title, body, kwargs = notices.posted[0]
    assert notice_id == "translation-unavailable"
    assert title == tr("translate_unavailable_notice_title")
    assert body == tr("translate_unavailable_notice_body")
    assert kwargs.get("severity") == "warning"
    assert host._is_translating is False


def test_no_worker_is_started_while_every_provider_is_cooling(notices, monkeypatch):
    monkeypatch.setattr(select_page, "is_english_language", lambda *a, **k: False)
    monkeypatch.setattr(
        select_page, "translator", lambda: type("T", (), {"lang": "fr"})()
    )
    monkeypatch.setattr(translate, "is_translation_available", lambda *a, **k: False)

    started = []
    monkeypatch.setattr(
        select_page, "ReleaseTranslateWorker", lambda *a, **k: started.append(a)
    )

    host = _PageHost()
    select_page.SelectPackagePage._translate_current_release_in_app(host)

    assert started == []
    assert [n[0] for n in notices.posted] == ["translation-unavailable"]


def test_a_recovered_provider_clears_the_notice(notices):
    host = _DialogHost()
    dialogs.UpdateAvailableDialog._on_translation_ready(
        host, {"tag_name": "v1"}, "fr", "Nom", "Corps"
    )

    assert notices.removed == ["translation-unavailable"]
    assert host._translated == ("Nom", "Corps")


def test_the_dialog_refuses_to_translate_when_unavailable(notices, monkeypatch):
    monkeypatch.setattr(
        dialogs, "is_english_language", lambda *a, **k: False, raising=False
    )
    monkeypatch.setattr(
        dialogs, "translator", lambda: type("T", (), {"lang": "fr"})(), raising=False
    )
    monkeypatch.setattr(translate, "is_translation_available", lambda *a, **k: False)
    monkeypatch.setattr(
        translate, "ReleaseTranslateWorker", lambda *a, **k: pytest.fail("worker started")
    )

    host = _DialogHost()
    dialogs.UpdateAvailableDialog._start_translation(host)

    assert [n[0] for n in notices.posted] == ["translation-unavailable"]


def test_several_no_auth_providers_are_configured():
    """Google alone is not a plan: the chain has independent free services."""
    assert len(translate.TRANSLATION_PROVIDERS) >= 4
    names = [name for name, _fn in translate.TRANSLATION_PROVIDERS]
    assert len(set(names)) == len(names)


def test_all_providers_failing_is_reported_not_raised_at_the_caller(monkeypatch):
    translate.reset_translation_cooldowns()

    def _boom(text, lang, timeout):
        raise OSError("service unreachable")

    monkeypatch.setattr(
        translate, "TRANSLATION_PROVIDERS", (("one", _boom), ("two", _boom))
    )

    # The default contract returns the original text: callers render it as-is.
    assert translate.fetch_google_translation("hello", "fr") == "hello"

    # And the app can now say, truthfully, that translation is unavailable.
    assert translate.is_translation_available() is False
    with pytest.raises(translate.TranslationUnavailableError):
        translate.fetch_google_translation("hello", "fr", raise_on_error=True)
    translate.reset_translation_cooldowns()


def test_a_provider_on_cooldown_is_skipped_without_a_second_request(monkeypatch):
    translate.reset_translation_cooldowns()
    calls = []

    def _works(text, lang, timeout):
        calls.append("works")
        return "bonjour"

    def _never(text, lang, timeout):
        calls.append("never")
        raise OSError("timeout")

    monkeypatch.setattr(
        translate, "TRANSLATION_PROVIDERS", (("broken", _never), ("ok", _works))
    )

    assert translate.fetch_google_translation("hello", "fr") == "bonjour"
    assert calls == ["never", "works"]
    assert translate.is_translation_available() is True
    translate.reset_translation_cooldowns()
