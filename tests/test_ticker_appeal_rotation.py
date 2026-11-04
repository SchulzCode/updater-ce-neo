"""The status ticker rotates the same broad appeal set as the Support page.

The ticker used to cycle three sentences forever; a window left open on the
donation dialog read the same ask every few minutes. It now draws from the
Support page's pool, minus the legacy lead/followup pair (a headline and a
paragraph, not one scrolling line).
"""

from src import donation_dialog as dd
from src import config
from src.i18n import tr
from src.ui import support_appeal as sa


def test_ticker_cycles_the_support_pool_not_three_sentences():
    messages = dd.get_appeal_messages()
    assert len(messages) >= 10
    assert len(set(messages)) == len(messages), "the ticker must not repeat a line"


def test_ticker_skips_the_legacy_lead_pair():
    assert tr(sa.LEGACY_APPEAL_KEY) not in dd.get_appeal_messages()


def test_ticker_never_scrolls_a_string_id():
    for message in dd.get_appeal_messages():
        assert message and not message.startswith("donate_")


def test_ticker_keeps_the_brand_specific_appeal_only_off_the_mediatek_brand(monkeypatch):
    monkeypatch.setattr(config, "is_mediatek_installer", lambda: False)
    assert tr("donate_appeal_what_innioasis_dont") in dd.get_appeal_messages()

    monkeypatch.setattr(config, "is_mediatek_installer", lambda: True)
    assert tr("donate_appeal_what_innioasis_dont") not in dd.get_appeal_messages()
