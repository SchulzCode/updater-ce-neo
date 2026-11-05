"""Card titles that are the switch itself, and disclosure rows behind one.

Show Advanced Settings and Allow Keep Settings and Media are not caption rows
inside a card: the card's title line carries the switch, so the wording is
printed once. Release Filters uses the same title switch as a disclosure, so
its four rows stay out of the column until the user asks for them.
"""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import device_tracking, keep_data
from src.i18n import tr
from src.ui.settings_page import SettingsPage


@pytest.fixture()
def settings_page():
    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("innioasis", "updater")
    saved = {
        key: settings.value(key)
        for key in ("advanced_settings", keep_data.ALLOW_ANY_KEY)
    }
    device_tracking.set_advanced_settings_enabled(False)
    keep_data.set_allow_keep_data_any(False)
    page = SettingsPage()
    page.show()
    app.processEvents()
    try:
        yield page
    finally:
        page.close()
        app.processEvents()
        for key, value in saved.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        device_tracking.set_advanced_settings_enabled(False)
        keep_data.set_allow_keep_data_any(False)


def test_the_advanced_title_is_its_own_switch(settings_page):
    page = settings_page
    assert page._advanced_card.title() == tr("settings_advanced_toggle")
    assert page.switch_caption(page._cb_advanced_settings) == (
        page._advanced_card.title()
    )
    assert page._advanced_card.isAncestorOf(page._cb_advanced_settings)
    # No second copy of the words in the card body.
    caption = page._switch_rows[page._cb_advanced_settings][1]
    assert caption.isHidden()


def test_release_filter_rows_wait_for_their_title_switch(settings_page):
    app = QApplication.instance()
    page = settings_page

    page.reveal_advanced_methods()
    app.processEvents()
    assert not page._rockbox_card.isHidden()
    # The card is shown, but its rows stay behind the disclosure.
    assert page._rockbox_options.isHidden()

    page._cb_rockbox_filters.setChecked(True)
    app.processEvents()
    assert not page._rockbox_options.isHidden()
    assert page._cb_nightly.isVisible()
    assert page._cb_240p.isVisible()
    assert page._cb_original_pre307.isVisible()

    page._cb_rockbox_filters.setChecked(False)
    app.processEvents()
    assert page._rockbox_options.isHidden()


def test_hiding_advanced_settings_closes_the_filter_disclosure(settings_page):
    app = QApplication.instance()
    page = settings_page

    page.reveal_advanced_methods()
    page._cb_rockbox_filters.setChecked(True)
    app.processEvents()
    assert not page._rockbox_options.isHidden()

    page.hide_advanced_methods()
    app.processEvents()
    assert not page._cb_rockbox_filters.isChecked()
    assert page._rockbox_options.isHidden()

    page.reveal_advanced_methods()
    app.processEvents()
    assert page._rockbox_options.isHidden()
