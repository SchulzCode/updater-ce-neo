"""The Support Us appeal rotates, and the family wording knows its limits.

Every opening of the page or its pop-out modal picks a different appeal, so a
fresh set of wordings is only half the job: the child-expecting variant must
reach the stored 30% cohort, weigh more on a day with no donation logged and
while a daily or weekly goal is on screen, and disappear after 17 April 2027.
"""

import datetime as dt
import os
import random

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from src.ui import support_appeal as sa

#: A daily-scope day: mid-month, one of the quiet weekdays.
SHORT_GOAL_DAY = dt.datetime(2026, 11, 17, 12, 0)
#: A monthly-scope day: the closing three days, when the month summarises.
MONTHLY_DAY = dt.datetime(2026, 11, 29, 12, 0)


def _store(tmp_path, cohort):
    store = QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat)
    store.setValue(sa.FAMILY_COHORT_KEY, "1" if cohort else "0")
    return store


def _donations(when, *, today=True):
    day = when if today else when - dt.timedelta(days=20)
    return [{"name": "A supporter", "amount": 5.0, "method": "Ko-fi", "dt": day}]


def _family_share(store, when, donations, wraps=3000, seed=5):
    rng = random.Random(seed)
    keys = [
        sa.pick_support_appeal_key(donations, now=when, settings=store, rng=rng)
        for _ in range(wraps)
    ]
    return sum(key in sa.FAMILY_APPEAL_KEYS for key in keys) / wraps


def test_the_general_pool_is_wide_and_every_variant_is_reachable(tmp_path):
    store = _store(tmp_path, cohort=False)
    rng = random.Random(11)
    picked = {
        sa.pick_support_appeal_key(_donations(MONTHLY_DAY), now=MONTHLY_DAY, settings=store, rng=rng)
        for _ in range(4000)
    }
    assert len(sa.SUPPORT_APPEAL_KEYS) >= 20
    assert picked == set(sa.SUPPORT_APPEAL_KEYS)


def test_family_wording_only_reaches_the_stored_thirty_percent(tmp_path):
    inside = _family_share(_store(tmp_path, cohort=True), SHORT_GOAL_DAY, _donations(SHORT_GOAL_DAY))
    outside = _family_share(_store(tmp_path, cohort=False), SHORT_GOAL_DAY, _donations(SHORT_GOAL_DAY))
    assert inside > 0.05
    assert outside == 0.0


def test_family_wording_comes_round_more_with_no_donation_logged_today(tmp_path):
    store = _store(tmp_path, cohort=True)
    quiet = _family_share(store, SHORT_GOAL_DAY, _donations(SHORT_GOAL_DAY, today=False))
    donated = _family_share(store, SHORT_GOAL_DAY, _donations(SHORT_GOAL_DAY))
    assert quiet > donated


def test_family_wording_comes_round_more_while_daily_or_weekly_goals_show(tmp_path):
    store = _store(tmp_path, cohort=True)
    daily = _family_share(store, SHORT_GOAL_DAY, _donations(SHORT_GOAL_DAY, today=False))
    monthly = _family_share(store, MONTHLY_DAY, _donations(MONTHLY_DAY, today=False))
    assert daily > monthly


def test_family_wording_stops_after_17_april_2027(tmp_path):
    store = _store(tmp_path, cohort=True)
    last_day = dt.datetime(2027, 4, 17, 23, 0)
    after = dt.datetime(2027, 4, 18, 0, 30)
    assert _family_share(store, last_day, _donations(last_day, today=False)) > 0.0
    assert _family_share(store, after, _donations(after, today=False)) == 0.0


def test_the_cohort_decision_is_made_once_and_kept(tmp_path):
    store = QSettings(str(tmp_path / "fresh.ini"), QSettings.IniFormat)
    first = sa.family_appeal_cohort(store)
    assert sa.family_appeal_cohort(store) == first
    assert store.value(sa.FAMILY_COHORT_KEY) in ("0", "1", 0, 1, True, False)


def test_a_two_sentence_appeal_keeps_its_first_sentence_bold():
    html = sa.support_appeal_html("donate_support_appeal_servers")
    assert html.startswith("<b>")
    assert html.count("<b>") == 1
