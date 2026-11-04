import os
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QPushButton

from src.donation_dialog import DonationStatusBar, DonationDialog, get_appeal_messages
from src.ui.main_window import MainWindow, _PAGE_SELECT, _PAGE_SUPPORT
from src.ui.support_page import SupportPage
from src.ui.flash_page import FlashPage


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication([])
    return app


def test_get_appeal_messages():
    msgs = get_appeal_messages()
    assert len(msgs) >= 3
    # Verify personal human & family appeals are included
    full_text = " ".join(msgs)
    assert "family" in full_text.lower() or "free" in full_text.lower()


def _draw_goal_at(bar, when):
    """Draw the bar's goal line for a pinned date, with the goal always offered.

    The bar otherwise picks a random appetite for the goal slot and the current
    date, and both decide what is shown; pinning them keeps these assertions
    about the goal itself rather than about what day the test ran.
    """
    bar._goal_now = when
    bar._bar_st = (1, 1, 1, 0, len(getattr(bar, "donations", None) or ()) > 2)
    bar._refresh_goal()


def test_donation_status_bar_goal_suppression(qapp):
    from datetime import datetime

    # Days 1-5 name the monthly target: nothing raised in the month leaves the
    # goal line off the bar entirely.
    monthly_day = datetime(2026, 11, 3, 12, 0)
    bar = DonationStatusBar(donations=[])
    _draw_goal_at(bar, monthly_day)
    assert bar._goal_scope == "monthly"
    assert bar._goal_suppressed is True
    assert not bar._goal_bar.isVisible()
    assert not bar._goal_label.isVisible()
    assert not bar._donor_label.isHidden()

    # When raised is 50 (< 200), goal bar is visible
    donations_mid = [{"amount": 50.0, "name": "Alice", "dt": datetime(2026, 11, 2)}]
    bar2 = DonationStatusBar(donations=donations_mid)
    _draw_goal_at(bar2, monthly_day)
    assert bar2._goal_suppressed is False
    assert not bar2._goal_bar.isHidden()
    assert not bar2._goal_label.isHidden()

    # When raised is 250 (>= 200) the month is paid for and the ladder has no
    # smaller rung left to fall back to: no goal bar, no "raised of" and no
    # "needed" text — donors and encouragement only.
    donations_high = [{"amount": 250.0, "name": "Bob", "dt": datetime(2026, 11, 2)}]
    bar3 = DonationStatusBar(donations=donations_high)
    _draw_goal_at(bar3, monthly_day)
    assert bar3._goal_scope is None
    assert bar3._goal_suppressed is True
    assert bar3._showing_goal is False
    assert not bar3._goal_bar.isVisible()
    assert not bar3._goal_label.isVisible()
    assert not bar3._donor_label.isHidden()


def test_goal_scope_names_the_month_the_quiet_days_and_the_week():
    from datetime import datetime

    from src.donors import get_goal_stats, weekly_goal_usd

    donations = [
        {"amount": 30.0, "dt": datetime(2026, 2, 2)},   # opening days
        {"amount": 10.0, "dt": datetime(2026, 2, 9)},   # Monday, first week block
    ]
    # February 2026 has 28 days: monthly on 1-5 and 22-28. Between them the
    # quiet weekdays — Monday, Tuesday, Wednesday, Saturday — name the day's
    # $10, and Thursday, Friday and Sunday name the week's $50.
    assert get_goal_stats(donations, now=datetime(2026, 2, 5, 12, 0))["scope"] == "monthly"
    assert get_goal_stats(donations, now=datetime(2026, 2, 6, 12, 0))["scope"] == "weekly"   # Friday
    assert get_goal_stats(donations, now=datetime(2026, 2, 7, 12, 0))["scope"] == "daily"    # Saturday
    assert get_goal_stats(donations, now=datetime(2026, 2, 8, 12, 0))["scope"] == "weekly"   # Sunday
    assert get_goal_stats(donations, now=datetime(2026, 2, 10, 12, 0))["scope"] == "daily"   # Tuesday
    assert get_goal_stats(donations, now=datetime(2026, 2, 11, 12, 0))["scope"] == "daily"   # Wednesday
    assert get_goal_stats(donations, now=datetime(2026, 2, 12, 12, 0))["scope"] == "weekly"  # Thursday
    assert get_goal_stats(donations, now=datetime(2026, 2, 14, 12, 0))["scope"] == "daily"   # Saturday
    assert get_goal_stats(donations, now=datetime(2026, 2, 22, 12, 0))["scope"] == "monthly"

    opening = get_goal_stats(donations, now=datetime(2026, 2, 3, 12, 0))
    assert opening["raised"] == 30.0 and opening["target"] == 200.0

    # Monday the 9th: that day's own $10 is already in, so the ladder names the
    # week's $50 with the same $10 counted against it.
    monday = get_goal_stats(donations, now=datetime(2026, 2, 9, 12, 0))
    assert monday["scope"] == "weekly"
    assert monday["raised"] == 10.0
    # A week is a quarter of the month's costs, so it is $50 in every month.
    assert monday["target"] == 50.0 and monday["target"] == weekly_goal_usd(monday["period_start"])

    # Tuesday: daily target of $10, only today's donations counted.
    tuesday_stats = get_goal_stats(donations, now=datetime(2026, 2, 10, 12, 0))
    assert tuesday_stats["scope"] == "daily"
    assert tuesday_stats["target"] == 10.0
    assert tuesday_stats["raised"] == 0.0

    closing = get_goal_stats(donations, now=datetime(2026, 2, 24, 12, 0))
    assert closing["scope"] == "monthly" and closing["raised"] == 40.0


def test_a_paid_week_hands_the_ask_down_to_the_day():
    from datetime import datetime

    from src.donors import get_goal_stats

    # Thursday the 12th: the week of 6-12 has raised $60, so its $50 is paid
    # for. The ask drops to the day's $10 rather than naming a finished week.
    donations = [{"amount": 60.0, "dt": datetime(2026, 2, 9)}]
    stats = get_goal_stats(donations, now=datetime(2026, 2, 12, 12, 0))
    assert stats["scope"] == "daily"
    assert stats["target"] == 10.0
    assert stats["raised"] == 0.0        # nothing today, only earlier this week
    assert stats["week_raised"] == 60.0
    assert stats["covered"] is False


def test_a_paid_day_hands_the_ask_up_to_the_week():
    from datetime import datetime

    from src.donors import get_goal_stats

    # Monday the 9th with $12 today: the day's $10 is covered, so the week's
    # own $50 is named rather than a day the reader has already paid for.
    stats = get_goal_stats(
        [{"amount": 12.0, "dt": datetime(2026, 2, 9)}], now=datetime(2026, 2, 9, 12, 0)
    )
    assert stats["scope"] == "weekly"
    assert stats["target"] == 50.0
    assert stats["raised"] == 12.0


def test_a_covered_month_leaves_no_goal_to_name():
    from datetime import datetime

    from src.donors import get_goal_stats

    # $200 in the month: every period on the ladder is paid for, so there is
    # nothing outstanding to show — not a bar, not "raised of", not "needed".
    stats = get_goal_stats(
        [{"amount": 200.0, "dt": datetime(2026, 2, 2)}], now=datetime(2026, 2, 12, 12, 0)
    )
    assert stats["scope"] is None
    assert stats["covered"] is True
    assert stats["month_reached"] is True
    assert stats["target"] == 0.0


def test_currency_transposition(monkeypatch):
    from datetime import datetime
    from src.donors import get_goal_stats

    donations = [{"amount": 25.0, "dt": datetime(2026, 2, 3)}]

    monkeypatch.setenv("UPDATER_CURRENCY", "GBP")
    stats_gbp = get_goal_stats(donations, now=datetime(2026, 2, 3, 12, 0))
    assert stats_gbp["currency_symbol"] == "£"
    assert stats_gbp["currency_code"] == "GBP"

    monkeypatch.setenv("UPDATER_CURRENCY", "EUR")
    stats_eur = get_goal_stats(donations, now=datetime(2026, 2, 3, 12, 0))
    assert stats_eur["currency_symbol"] == "€"
    assert stats_eur["currency_code"] == "EUR"

    monkeypatch.setenv("UPDATER_CURRENCY", "USD")
    stats_usd = get_goal_stats(donations, now=datetime(2026, 2, 3, 12, 0))
    assert stats_usd["currency_symbol"] == "$"
    assert stats_usd["currency_code"] == "USD"


def test_goal_line_is_shown_in_the_readers_currency(qapp, monkeypatch):
    from datetime import datetime

    from src.i18n import translator

    translator().set_language("en")
    try:
        donations = [{"amount": 30.0, "name": "Alice", "dt": datetime(2026, 11, 2)}]
        for currency, symbol in (("USD", "$"), ("GBP", "\u00a3"), ("EUR", "\u20ac")):
            monkeypatch.setenv("UPDATER_CURRENCY", currency)
            bar = DonationStatusBar(donations=donations)
            _draw_goal_at(bar, datetime(2026, 11, 4, 12, 0))
            text = bar._goal_label.text()
            # Transposed 1:1, never converted: the same 30 of 200, in the
            # reader's own currency.
            assert symbol + "30" in text
            assert symbol + "200" in text
            bar.deleteLater()
    finally:
        translator().set_language("en")


def test_status_bar_names_only_the_weeks_costs_mid_month(qapp, monkeypatch):
    from datetime import datetime

    from src.i18n import translator

    monkeypatch.setenv("UPDATER_CURRENCY", "USD")
    translator().set_language("en")
    try:
        donations = [{"amount": 30.0, "name": "Alice", "dt": datetime(2026, 11, 2)}]

        # Day 6: the opening days' $30 was counted against the month, so the
        # bar asks for the week's own costs starting from $0.
        weekly = DonationStatusBar(donations=donations)
        _draw_goal_at(weekly, datetime(2026, 11, 6, 12, 0))
        assert weekly._goal_scope == "weekly"
        assert weekly._showing_goal is True
        weekly_text = weekly._goal_label.text()
        assert "$0" in weekly_text
        assert "week" in weekly_text
        assert "$200" not in weekly_text

        # Day 4 shows the month, and the $30 raised this month with it.
        monthly = DonationStatusBar(donations=donations)
        _draw_goal_at(monthly, datetime(2026, 11, 4, 12, 0))
        assert monthly._goal_scope == "monthly"
        monthly_text = monthly._goal_label.text()
        assert "$30" in monthly_text and "$200" in monthly_text and "month" in monthly_text

        # The last seven days go back to the monthly figure.
        closing = DonationStatusBar(donations=donations)
        _draw_goal_at(closing, datetime(2026, 11, 25, 12, 0))
        assert closing._goal_scope == "monthly"
        assert "$30" in closing._goal_label.text()

        for bar in (weekly, monthly, closing):
            bar.deleteLater()
    finally:
        translator().set_language("en")


def test_goal_is_dropped_once_the_month_is_covered_even_mid_week(qapp):
    from datetime import datetime

    from src.i18n import translator

    translator().set_language("en")
    try:
        # The month's $200 was met on day 2. On day 12 this week has seen no
        # donation at all, but the costs it would cover are already paid for,
        # so no goal may be mentioned: donor lines and encouragement only.
        donations = [
            {"amount": 150.0, "name": "Alice", "dt": datetime(2026, 11, 2)},
            {"amount": 50.0, "name": "Bob", "dt": datetime(2026, 11, 2)},
        ]
        bar = DonationStatusBar(donations=donations)
        _draw_goal_at(bar, datetime(2026, 11, 12, 12, 0))  # Thursday mid-month
        # The month is covered, so no rung is outstanding and the ladder has
        # nothing left to name: the bar shows donors and encouragement only.
        assert bar._goal_scope is None
        assert bar._goal_suppressed is True
        assert bar._showing_goal is False
        assert not bar._goal_label.isVisible()
        assert not bar._goal_bar.isVisible()
        bar.deleteLater()
    finally:
        translator().set_language("en")


def test_support_page_narratives_and_methods(qapp):
    from src.i18n import translator, tr

    page = SupportPage()
    assert hasattr(page, "_appeal_body")
    assert "family" in page._appeal_body.text().lower()
    old_language = translator().lang
    try:
        for language in ("zh-CN", "fr", "es", "de", "ja", "it"):
            translator().set_language(language)
            page.retranslate()
            assert tr("donate_support_appeal_lead") in page._appeal_body.text()
            assert tr("donate_support_appeal_followup") in page._appeal_body.text()
    finally:
        translator().set_language(old_language)
        page.retranslate()

    # Test primary payment methods are created (Ko-fi, PayPal, Revolut, Patreon)
    assert len(page._method_buttons) == 4
    assert hasattr(page, "_honeygain_link")
    assert page._crypto_drawer.isHidden()
    page._toggle_crypto_drawer()
    assert not page._crypto_drawer.isHidden()

    btn = page._crypto_drawer.findChildren(QPushButton)[0]
    page._copy_crypto("test_addr", btn)
    assert QApplication.clipboard().text() == "test_addr"


def test_donation_appeals_and_community_labels_have_all_catalog_translations():
    import json
    from pathlib import Path

    from src.i18n import _STRINGS

    keys = (
        "donate_appeal_costs_for_year",
        "donate_appeal_family_and_time",
        "donate_appeal_human_touch",
        "donate_appeal_keep_going",
        "donate_appeal_always_free",
        "donate_appeal_community_archive",
        "donate_appeal_what_innioasis_dont",
        "donate_support_appeal_lead",
        "donate_support_appeal_followup",
        "community_discord",
        "community_reddit",
    )
    catalog_dir = Path(__file__).resolve().parent.parent / "assets" / "i18n"
    catalogs = [path for path in catalog_dir.glob("*.json") if path.name != "languages.json"]
    assert catalogs
    for path in catalogs:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in keys:
            assert data.get(key, "").strip(), f"{path.stem} missing {key}"
            assert data[key] != key, f"{path.stem} has untranslated {key}"
    for key in keys:
        assert all(_STRINGS[key].get(code) for code in ("en", "zh-CN", "fr", "es", "de", "ja"))


def test_dismissing_donations_does_not_make_settings_window_wider(qapp, monkeypatch):
    from src import device_tracking

    monkeypatch.setenv("UPDATER_REDUCE_MOTION", "1")
    settings = QSettings("innioasis", "updater")
    old_dismissed = device_tracking.is_settings_donations_section_dismissed(settings)
    old_disabled = device_tracking.is_donation_ui_disabled(settings)
    old_prompt_disabled = device_tracking.is_donation_install_prompt_disabled(settings)
    device_tracking.set_settings_donations_section_dismissed(False, settings)
    device_tracking.set_donation_ui_disabled(False, settings)
    device_tracking.set_donation_install_prompt_disabled(False, settings)
    window = MainWindow()
    try:
        window._nav_to_page(4)
        window.show()
        qapp.processEvents()
        page = window._settings_page
        assert page._donations_card.isVisible()
        available_width = window.screen().availableGeometry().width()
        window.resize(min(790, available_width), window.height())
        qapp.processEvents()
        width_before = window.width()
        assert width_before > 640
        page._cb_hide_donations.setChecked(False)
        qapp.processEvents()

        assert not page._dismiss_donations_link.isHidden()
        assert page._dismiss_donations_link.wordWrap()
        assert page._donations_actions.minimumSizeHint().width() < width_before
        assert window.width() == width_before
        assert window._settings_btn.isVisible()

        page._on_dismiss_donations_section()
        qapp.processEvents()
        assert page._donations_card.isHidden()
        assert window.width() == width_before
        assert window._settings_btn.isVisible()
    finally:
        device_tracking.set_settings_donations_section_dismissed(old_dismissed, settings)
        device_tracking.set_donation_ui_disabled(old_disabled, settings)
        device_tracking.set_donation_install_prompt_disabled(old_prompt_disabled, settings)
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_community_buttons_open_their_links_and_follow_visibility(qapp, monkeypatch):
    from src import device_tracking
    from src.config import DONATION_LINKS
    from src.ui.main_window import MainWindow

    old_disabled = device_tracking.is_donation_ui_disabled()
    old_prompt_disabled = device_tracking.is_donation_install_prompt_disabled()
    device_tracking.set_donation_ui_disabled(False)
    device_tracking.set_donation_install_prompt_disabled(False)
    window = MainWindow()
    opened = []
    monkeypatch.setattr("src.browser.open_browser", lambda url: opened.append(url))
    try:
        window._discord_btn.click()
        window._reddit_btn.click()
        assert opened == [
            "https://discord.gg/zHrT2zrcek",
            "https://reddit.com/r/innioasis",
        ]
        assert DONATION_LINKS["discord"] == opened[0]
        assert DONATION_LINKS["reddit"] == opened[1]
        assert not window._discord_btn.icon().isNull()
        assert not window._reddit_btn.icon().isNull()
        # The community marks match the sidebar's own 16px glyphs.
        assert window._discord_btn.iconSize().width() == 16
        assert window._reddit_btn.iconSize().width() == 16
        assert window._github_btn.iconSize().width() == 16

        window._apply_donation_visibility(True)
        assert window._discord_btn.isHidden()
        assert window._reddit_btn.isHidden()
        window._apply_donation_visibility(False)
        assert not window._discord_btn.isHidden()
        assert not window._reddit_btn.isHidden()

        # Mid-install the marks follow the Donations setting, so help stays
        # reachable while a run is in progress.
        window._apply_install_ui_state(True)
        assert not window._discord_btn.isHidden()
        assert not window._reddit_btn.isHidden()
        assert not window._github_btn.isHidden()
        window._apply_donation_visibility(True)
        assert window._discord_btn.isHidden()
        assert window._github_btn.isHidden()
        window._apply_donation_visibility(False)
        window._apply_install_ui_state(False)
        assert not window._discord_btn.isHidden()
        assert not window._reddit_btn.isHidden()
    finally:
        device_tracking.set_donation_ui_disabled(old_disabled)
        device_tracking.set_donation_install_prompt_disabled(old_prompt_disabled)
        window.close()
        qapp.processEvents()


def test_flash_page_paperclip_tip_inside_card_removed(qapp):
    fp = FlashPage()
    fp.set_model("Y1")
    fp.show_power_off_prompt()
    # Inside-card mode hint must be hidden in favour of the beneath-card tip
    assert fp._wait_mode_hint.isHidden()
    assert not fp._paperclip_hint_host.isHidden()


def test_reset_on_r_restores_battery_reminder(qapp):
    s = QSettings("innioasis", "updater")
    s.setValue("battery_hint_dismissed", True)
    assert s.value("battery_hint_dismissed", False, type=bool) is True

    win = MainWindow()
    try:
        win._select_page._check_battery_toast_visibility()
        assert win._select_page._battery_toast.isHidden()

        # Trigger reset
        win.reset_all_configuration_and_caches()

        # After reset, battery_hint_dismissed must be False/cleared and toast visible
        assert s.value("battery_hint_dismissed", False, type=bool) is False
        assert not win._select_page._battery_toast.isHidden()
        assert not win._select_page._toast_container.isHidden()
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_donation_status_bar_rotation_and_centering(qapp, monkeypatch):
    from datetime import datetime
    import random
    from src.ui.main_window import compact_install_height, COMPACT_INSTALL_MIN_HEIGHT, COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS

    monkeypatch.setattr(random, "random", lambda: 0.5)
    donations = [{"amount": 25.0, "name": "Charlie", "dt": datetime.now()}]
    bar = DonationStatusBar(donations=donations)
    bar.show()
    qapp.processEvents()

    # Initial state: showing goal
    assert bar._showing_goal is True
    assert bar._goal_bar.isVisible()
    # The goal line sits flush in the right corner (no mirror spacer on the right).
    assert bar._goal_group.x() + bar._goal_group.width() >= bar.width() - 16

    # Rotate to donor line: goal bar must be hidden so no space is wasted
    bar._rotate()
    assert bar._showing_goal is False
    assert not bar._goal_bar.isVisible()
    assert bar._donor_label.isVisible()

    # Rotate back to goal line
    bar._rotate()
    assert bar._showing_goal is True
    assert bar._goal_bar.isVisible()

    # Verify height expands and contracts
    base = dict(page_h=120, prompt_h=0, status_h=28, inline_header=True)
    h_with = compact_install_height(donations_disabled=False, **base)
    h_without = compact_install_height(donations_disabled=True, **base)
    assert h_with > h_without
    assert h_without == COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS
    bar.close()
    bar.deleteLater()
    qapp.processEvents()


def test_windows_legacy_shortcut_cleanup_never_deletes_user_files(tmp_path, monkeypatch):
    from src import legacy_cleanup

    fake_home = tmp_path / "User"
    desktop = fake_home / "Desktop"
    start_menu = fake_home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop.mkdir(parents=True)
    start_menu.mkdir(parents=True)

    # 1. Shortcuts that MUST be removed (.lnk / .url containing Innioasis or SP Flash)
    old_app_lnk = desktop / "Innioasis Updater.lnk"
    old_sp_lnk = desktop / "SP Flash Tool.lnk"
    old_app_lnk.write_text("shortcut")
    old_sp_lnk.write_text("shortcut")

    menu_folder = start_menu / "Innioasis Updater"
    menu_folder.mkdir()
    (menu_folder / "Innioasis Updater.lnk").write_text("shortcut")
    (menu_folder / "SP Flash Tool.lnk").write_text("shortcut")

    # 2. User files that MUST NEVER be deleted even if named Innioasis / SP Flash
    user_zip = desktop / "Innioasis Y1 Backup.zip"
    user_notes = desktop / "SP Flash notes.txt"
    user_folder = desktop / "My Innioasis Music"
    user_folder.mkdir()
    user_song = user_folder / "song.mp3"
    user_zip.write_text("user zip data")
    user_notes.write_text("user notes")
    user_song.write_text("audio")

    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("APPDATA", str(fake_home / "AppData" / "Roaming"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("PUBLIC", str(tmp_path / "Public"))

    removed = legacy_cleanup._clean_windows_legacy_shortcuts()
    assert len(removed) >= 5
    assert not old_app_lnk.exists()
    assert not old_sp_lnk.exists()
    assert not menu_folder.exists()

    # Non-shortcut user files and folders with user files are completely untouched
    assert user_zip.exists()
    assert user_notes.exists()
    assert user_folder.exists()
    assert user_song.exists()


