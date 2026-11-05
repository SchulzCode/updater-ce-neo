"""SP Flash Tool shortcut switches are an advanced-only option.

The app's own desktop and Start Menu shortcuts are everyday choices and stay on
the card; the tool's two rows appear only once Show Advanced Settings is on.
"""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.i18n import tr
from src.ui.settings_page import SettingsPage


@pytest.fixture()
def settings_page():
    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("innioasis", "updater")
    saved = settings.value("advanced_settings")
    page = SettingsPage()
    page.show()
    app.processEvents()
    try:
        yield page
    finally:
        page.close()
        settings.setValue("advanced_settings", saved)


def test_sp_flash_shortcut_rows_are_advanced_only(settings_page):
    app = QApplication.instance()
    page = settings_page
    sp_rows = [sw for (sid, _kind), sw in page._shortcut_switches.items() if sid == "sp_flash_tool"]
    app_rows = [sw for (sid, _kind), sw in page._shortcut_switches.items() if sid == "updater"]
    sp_row_widgets = [page._switch_rows[sw][0] for sw in sp_rows]
    app_row_widgets = [page._switch_rows[sw][0] for sw in app_rows]
    assert len(sp_rows) == 2
    assert len(app_rows) == 2

    page._cb_advanced_settings.setChecked(False)
    app.processEvents()
    assert not any(row.isVisible() for row in sp_rows)
    assert not any(row.isVisible() for row in sp_row_widgets)
    assert all(row.isVisible() for row in app_rows)
    assert all(row.isVisible() for row in app_row_widgets)

    page._cb_advanced_settings.setChecked(True)
    app.processEvents()
    assert all(row.isVisible() for row in sp_rows)
    assert all(row.isVisible() for row in sp_row_widgets)


def test_shortcut_rows_label_each_shortcut_once(settings_page):
    page = settings_page
    for (sid, kind), switch in page._shortcut_switches.items():
        name_key = "settings_shortcut_updater" if sid == "updater" else "settings_shortcut_sp_flash"
        kind_key = "settings_shortcut_desktop" if kind == "desktop" else "settings_shortcut_menu"
        assert page.switch_caption(switch) == f"{tr(name_key)} — {tr(kind_key)}"
        assert switch.property("text") == ""
