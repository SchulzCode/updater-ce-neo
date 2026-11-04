"""The costs line names the active period and uses the reader's currency."""

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _english():
    from src.i18n import translator

    translator().set_language("en")
    yield
    translator().set_language("en")


def _pin_gbp(monkeypatch):
    from src import fx

    monkeypatch.setenv("UPDATER_CURRENCY", "GBP")
    monkeypatch.setattr(fx, "rate", lambda code="USD": 0.5)


def test_day_goal_line_is_warm_and_local(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 17, 12)
    stats = get_goal_stats([{"amount": 5.79, "dt": now}], now=now)

    assert stats["scope"] == "daily"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £2.90 of our £5 costs for today</b>"
    )


def test_week_goal_line_uses_the_week_rung(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    stats = get_goal_stats([{"amount": 10, "dt": now}], now=now)

    assert stats["scope"] == "weekly"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £5 of our £25 costs for this week</b>"
    )


def test_month_goal_line_uses_the_month_rung(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 3, 12)
    stats = get_goal_stats([{"amount": 40, "dt": now}], now=now)

    assert stats["scope"] == "monthly"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £20 of our £100 costs for this month</b>"
    )


def test_zero_and_covered_goals_do_not_render_a_cold_dollar_line(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 3, 12)

    assert format_goal_line(get_goal_stats([], now=now)) == ""
    covered = get_goal_stats([{"amount": 200, "dt": now}], now=now)
    assert covered["covered"] is True
    assert format_goal_line(covered) == ""


def test_status_bar_uses_the_period_line_not_the_monthly_dollar_line(qapp, monkeypatch):
    import src.donation_dialog as dialog

    stats = {
        "scope": "daily",
        "reached": False,
        "covered": False,
        "shown_raised": 2.9,
        "shown_target": 5.0,
        "shown_percent": 58.0,
        "currency_code": "GBP",
        "currency_symbol": "£",
    }
    monkeypatch.setattr(dialog, "get_goal_stats", lambda donations: stats)
    monkeypatch.setattr(dialog, "_resolve_bar_state", lambda widget: (0, 0, 1, 1, True))
    monkeypatch.setenv("UPDATER_CURRENCY", "GBP")

    bar = dialog.DonationStatusBar(donations=[])
    assert bar._showing_goal is True
    assert "£2.90" in bar._goal_label.text()
    assert "for today" in bar._goal_label.text()
    assert "$" not in bar._goal_label.text()
    bar.deleteLater()


def test_usd_goal_line_keeps_the_dollar_mark(monkeypatch):
    from src import fx
    from src.donors import format_goal_line, get_goal_stats

    monkeypatch.setenv("UPDATER_CURRENCY", "USD")
    monkeypatch.setattr(fx, "rate", lambda code="USD": 1.0)
    now = datetime(2026, 11, 17, 12)
    stats = get_goal_stats([{"amount": 2.9, "dt": now}], now=now)

    assert format_goal_line(stats) == (
        "<b>You've helped us raise $2.90 of our $10 costs for today</b>"
    )
