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
    stats = get_goal_stats(
        [{"amount": 10, "dt": now}], now=now, monthly_roll=0.9
    )

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


def test_the_slow_weekdays_open_on_the_day_and_the_rest_on_the_week():
    from src.donors import goal_scope

    # Tuesday, Wednesday and Saturday open on today's costs.
    assert goal_scope(datetime(2026, 11, 10, 12)) == "daily"
    assert goal_scope(datetime(2026, 11, 11, 12)) == "daily"
    assert goal_scope(datetime(2026, 11, 14, 12)) == "daily"

    # Monday, Thursday, Friday and Sunday open on the week.
    assert goal_scope(datetime(2026, 11, 9, 12)) == "weekly"
    assert goal_scope(datetime(2026, 11, 12, 12)) == "weekly"
    assert goal_scope(datetime(2026, 11, 13, 12)) == "weekly"
    assert goal_scope(datetime(2026, 11, 15, 12)) == "weekly"


def test_the_month_summary_owns_only_the_first_and_last_three_days():
    from src.donors import days_in_month, goal_ladder, goal_scope

    # November 2026 has 30 days, so the closing window is the 28th to the 30th.
    assert days_in_month(datetime(2026, 11, 12, 12)) == 30
    for day in (1, 2, 3, 28, 29, 30):
        when = datetime(2026, 11, day, 12)
        assert goal_scope(when) == "monthly"
        assert goal_ladder(when) == ("monthly",)

    for day in (4, 27):
        assert goal_ladder(datetime(2026, 11, day, 12)) == (
            "daily",
            "weekly",
            "monthly",
        )


def test_a_short_day_asks_for_today_even_when_the_week_opened_on_weekly(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    # Thursday 5 November 2026: the 5th is outside the closing/opening window.
    now = datetime(2026, 11, 5, 12)
    donations = [
        {"amount": 30, "dt": datetime(2026, 11, 2, 9)},
        {"amount": 5, "dt": now},
    ]
    assert get_goal_stats(donations, now=now)["scope"] == "daily"


def test_today_hands_over_at_nine_dollars_ninety_nine(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)

    almost = get_goal_stats([{"amount": 9.98, "dt": now}], now=now)
    assert almost["scope"] == "daily"

    paid = get_goal_stats(
        [{"amount": 9.99, "dt": now}], now=now, monthly_roll=0.9
    )
    assert paid["scope"] == "weekly"


def test_the_week_and_month_are_picked_for_the_day_when_both_are_open(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    donations = [{"amount": 12, "dt": now}]

    monthly = get_goal_stats(donations, now=now, monthly_roll=0.1)
    weekly = get_goal_stats(donations, now=now, monthly_roll=0.9)

    assert monthly["scope"] == "monthly"
    assert weekly["scope"] == "weekly"
    assert format_goal_line(monthly) == (
        "<b>You've helped us raise £6 of our £100 costs for this month</b>"
    )
    assert format_goal_line(weekly) == (
        "<b>You've helped us raise £6 of our £25 costs for this week</b>"
    )


def test_the_choice_is_stable_across_refreshes(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    donations = [{"amount": 12, "dt": now}]

    first = get_goal_stats(donations, now=now)["scope"]
    assert first in ("weekly", "monthly")
    for _ in range(5):
        assert get_goal_stats(donations, now=now)["scope"] == first


def test_a_completed_week_always_shows_the_month(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    stats = get_goal_stats(
        [{"amount": 50, "dt": now}], now=now, monthly_roll=0.9
    )

    assert stats["scope"] == "monthly"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £25 of our £100 costs for this month</b>"
    )


def test_sixty_in_the_last_week_leans_towards_the_month(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    # Monday 16 November 2026, so the current week starts today.
    now = datetime(2026, 11, 16, 12)
    quiet = [{"amount": 12, "dt": now}]
    strong = [
        {"amount": 50, "dt": datetime(2026, 11, 15, 12)},
        {"amount": 12, "dt": now},
    ]

    assert get_goal_stats(quiet, now=now, monthly_roll=0.6)["scope"] == "weekly"
    boosted = get_goal_stats(strong, now=now, monthly_roll=0.6)
    assert boosted["last_week_raised"] == 62.0
    assert boosted["week_raised"] == 12.0
    assert boosted["scope"] == "monthly"


def test_a_completed_month_is_silent(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    stats = get_goal_stats([{"amount": 200, "dt": now}], now=now)

    assert stats["covered"] is True
    assert stats["scope"] is None
    assert format_goal_line(stats) == ""


def test_zero_monthly_and_covered_goals_do_not_render_a_cold_dollar_line(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 3, 12)

    assert format_goal_line(get_goal_stats([], now=now)) == ""
    covered = get_goal_stats([{"amount": 200, "dt": now}], now=now)
    assert covered["covered"] is True
    assert format_goal_line(covered) == ""


def test_a_quiet_day_still_names_today(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    # Thursday 5 November 2026: no donations today, so the daily rung is $0.
    now = datetime(2026, 11, 5, 12)
    donations = [{"amount": 30, "dt": datetime(2026, 11, 2, 9)}]

    stats = get_goal_stats(donations, now=now)

    assert stats["scope"] == "daily"
    assert stats["day_raised"] == 0
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £0 of our £5 costs for today</b>"
    )


def test_status_bar_shows_a_zero_daily_goal(qapp, monkeypatch):
    import src.donation_dialog as dialog
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 5, 12)
    stats = get_goal_stats(
        [{"amount": 30, "dt": datetime(2026, 11, 2, 9)}], now=now
    )
    monkeypatch.setattr(dialog, "get_goal_stats", lambda donations: stats)
    monkeypatch.setattr(dialog, "_resolve_bar_state", lambda widget: (0, 0, 1, 1, True))

    bar = dialog.DonationStatusBar(donations=[])

    assert bar._showing_goal is True
    assert "£0" in bar._goal_label.text()
    assert "for today" in bar._goal_label.text()
    bar.deleteLater()


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
