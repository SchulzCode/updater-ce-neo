"""Community notices arrive as one counted notification, never one banner each."""

from types import SimpleNamespace

import pytest

from src import notice_alerts
from src.i18n import tr


def _notice(ident, title="A notice", kind="info", severity="info"):
    return SimpleNamespace(id=ident, title=title, content="body text", kind=kind, severity=severity)


@pytest.fixture
def delivered(monkeypatch):
    """Capture what would reach the desktop instead of posting it."""
    sent = []

    def _deliver(title, message, tray=None, route=""):
        sent.append((title, message, route))
        return True

    monkeypatch.setattr(notice_alerts, "deliver", _deliver)
    return sent


def test_three_new_notices_arrive_as_one_counted_notification(monkeypatch, delivered):
    monkeypatch.setattr(notice_alerts, "category_enabled", lambda category: True)
    notices = [_notice("a"), _notice("b"), _notice("c")]

    assert notice_alerts.notify_new_notices(notices) is True
    assert len(delivered) == 1
    title, _message, route = delivered[0]
    assert title == tr("notices_summary_title_many").format(count=3)
    assert route == "notices:a"


def test_a_single_notice_is_announced_by_its_own_headline(monkeypatch, delivered):
    monkeypatch.setattr(notice_alerts, "category_enabled", lambda category: True)

    notice_alerts.notify_new_notices([_notice("a", title="Y1 firmware 2.0 is out")])
    title, message, _route = delivered[0]
    assert title == tr("notices_summary_title_one")
    assert message == "Y1 firmware 2.0 is out"


def test_only_notices_in_enabled_categories_are_counted(monkeypatch, delivered):
    """General notices default off; turning them off must not inflate a count."""
    monkeypatch.setattr(
        notice_alerts, "category_enabled", lambda category: category == notice_alerts.CATEGORY_WARNING
    )

    notice_alerts.notify_new_notices(
        [_notice("a"), _notice("b", severity="warning"), _notice("c")]
    )
    assert len(delivered) == 1
    assert delivered[0][0] == tr("notices_summary_title_one")


def test_nothing_is_posted_when_every_category_is_off(monkeypatch, delivered):
    monkeypatch.setattr(notice_alerts, "category_enabled", lambda category: False)

    assert notice_alerts.notify_new_notices([_notice("a"), _notice("b")]) is False
    assert delivered == []


def test_only_unseen_notices_are_counted(monkeypatch, delivered):
    monkeypatch.setattr(notice_alerts, "category_enabled", lambda category: True)
    monkeypatch.setattr(notice_alerts, "seen_notice_ids", lambda: {"a", "c"})

    fresh = notice_alerts.unseen_notices([_notice("a"), _notice("b"), _notice("c")])
    assert [n.id for n in fresh] == ["b"]
    notice_alerts.notify_new_notices(fresh)
    assert delivered[0][0] == tr("notices_summary_title_one")


def test_the_window_posts_one_notification_for_the_whole_batch(monkeypatch):
    from src.ui.main_window import MainWindow

    posted = []
    monkeypatch.setattr(notice_alerts, "unseen_notices", lambda notices: list(notices))
    monkeypatch.setattr(notice_alerts, "mark_notices_seen", lambda ids: None)
    monkeypatch.setattr(
        notice_alerts,
        "notify_new_notices",
        lambda notices, tray=None, route="": posted.append(len(list(notices))) or True,
    )

    class _Window:
        def _notification_tray(self):
            return None

    MainWindow._notify_about_notices(
        _Window(), [_notice("a"), _notice("b"), _notice("c")]
    )
    assert posted == [3]


def test_notification_titles_never_fall_back_to_keys_or_notice_ids(
    monkeypatch, delivered
):
    import src.i18n as i18n

    monkeypatch.setattr(notice_alerts, "category_enabled", lambda category: True)
    monkeypatch.setattr(i18n, "tr", lambda key: key)

    notice_alerts.notify_new_notices(
        [_notice("2026-11-03-rockbox-y2-warn", title="2026-11-03-rockbox-y2-warn")]
    )
    title, body, _route = delivered[0]
    assert "notices_summary_" not in title
    assert "2026-11-03-rockbox-y2-warn" not in title
    assert "2026-11-03-rockbox-y2-warn" not in body

    delivered.clear()
    notice_alerts.notify_new_notices([_notice("a"), _notice("b")])
    title, body, _route = delivered[0]
    assert title == "2 New Community Notices"
    assert body == "Open Notices in the app to read them."


def test_a_catalogue_value_equal_to_the_notice_id_is_not_used(monkeypatch, delivered):
    import src.i18n as i18n

    monkeypatch.setattr(notice_alerts, "category_enabled", lambda category: True)
    monkeypatch.setattr(i18n, "tr", lambda key: "2026-11-03-rockbox-y2-warn")
    notice_alerts.notify_new_notices(
        [_notice("2026-11-03-rockbox-y2-warn", title="A real headline")]
    )
    title, _body, _route = delivered[0]
    assert title == "New Community Notice"


def test_notice_rows_use_words_instead_of_an_id(monkeypatch):
    from src.ui.dialogs import _notice_heading

    assert _notice_heading(_notice("a", title="")) == tr("notices_dialog_title")
    assert _notice_heading(_notice("a", title="a")) == tr("notices_dialog_title")
    assert _notice_heading(_notice("a", title="Real headline")) == "Real headline"


def test_the_summary_copy_is_words_not_catalogue_ids(monkeypatch, delivered):
    """A missing catalogue entry falls back to its own key, which reads as an ID.

    The title of a notice notification once reached the desktop as
    ``notices_summary_title_one``: the string had never been added to the
    catalogue, and every earlier assertion compared it with ``tr(key)``, so a
    missing entry matched a missing entry. Compare with the words instead.
    """
    for key in (
        "notices_summary_title_one",
        "notices_summary_title_many",
        "notices_summary_body",
    ):
        assert tr(key) != key, f"{key} has no catalogue entry"
        assert "_" not in tr(key), f"{key} still reads as an identifier"

    monkeypatch.setattr(notice_alerts, "category_enabled", lambda category: True)
    notice_alerts.notify_new_notices([_notice("2026-11-03-rockbox-y2-warn")])
    title, _body, _route = delivered[0]
    assert title == tr("notices_summary_title_one")
    assert "_" not in title

    delivered.clear()
    notice_alerts.notify_new_notices([_notice("a"), _notice("b")])
    title, _body, _route = delivered[0]
    assert title == tr("notices_summary_title_many").format(count=2)
    assert "2" in title
