import csv
import logging
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import fx
from .config import DONORS_CSV_URL, MONTHLY_GOAL_USD, site_source_urls
from .network import requests_module

logger = logging.getLogger(__name__)

_DONORS_HOST = "innioasis.app"

_ANONYMOUS_DONOR_NAMES = {
    "", "supporter", "supporters", "a supporter", "top supporter",
    "a top supporter", "someone", "anonymous", "anon", "none", "n/a",
    "unknown", "null",
}


def coffee_method(method) -> str:
    low = " ".join(str(method or "").split()).lower()
    if "bmac" in low or "buymeacoffee" in low or "buy me a coffee" in low:
        return "bmac"
    if "ko-fi" in low or "kofi" in low or "ko fi" in low:
        return "kofi"
    return ""


def method_label(method) -> str:
    kind = coffee_method(method)
    if kind == "bmac":
        return "Buy Me a Coffee"
    if kind == "kofi":
        return "Ko-Fi"
    return ""


def is_anonymous_donor(name) -> bool:
    low = " ".join(str(name or "").split()).lower()
    if low in _ANONYMOUS_DONOR_NAMES:
        return True
    return low.startswith(("supporter", "anonymous", "someone"))


def parse_donors_csv_text(csv_text):
    if not csv_text:
        return []
    donations = []
    try:
        reader = csv.reader(csv_text.splitlines())
        for i, row in enumerate(reader):
            if not row or not any(row):
                continue
            if i == 0 and row[0].strip().lower().startswith("name"):
                continue
            name = row[0].strip() or "Supporter"
            if name.lower().startswith("total"):
                continue
            if name.lower().strip() == "innioasis":
                continue
            try:
                amt = float(row[1].strip().replace("$", ""))
            except (ValueError, IndexError):
                amt = 0.0
            date_str = row[2].strip() if len(row) > 2 else ""
            dt = None
            if date_str:
                try:
                    dt = datetime.strptime(date_str.strip(), "%d/%m/%Y")
                except Exception:
                    dt = None
            url = row[3].strip() if len(row) > 3 else ""
            method = row[4].strip() if len(row) > 4 else "Donation"
            msg = row[5].strip() if len(row) > 5 else ""
            donations.append({
                "name": name,
                "amount": amt,
                "date_str": date_str,
                "dt": dt,
                "url": url,
                "method": method,
                "message": msg,
            })
    except Exception as e:
        logger.warning("Error parsing donors CSV: %s", e)
    return donations


def relative_date(dt, now=None):
    from .i18n import tr

    if not dt:
        return ""
    now = now or datetime.now()
    today = now.date()
    dt_date = dt.date() if hasattr(dt, 'date') else dt
    delta = today - dt_date
    days = delta.days

    if days == 0:
        return tr('donate_when_today')
    elif days == 1:
        return tr('donate_when_yesterday')
    elif days <= 6:
        return tr('donate_when_day').format(day=dt.strftime('%A'))
    elif days < 14:
        return tr('donate_when_week')
    elif days < 30:
        weeks = days // 7
        return tr('donate_when_weeks').format(count=weeks)
    elif days < 60:
        return tr('donate_when_month')
    elif days < 365:
        months = days // 30
        return tr('donate_when_months').format(count=months)
    else:
        return tr('donate_when_year')


def format_donation_when(dt, now=None) -> str:
    from .i18n import tr

    if not dt:
        return ""
    now = now or datetime.now()
    today = now.date()
    dt_date = dt.date() if hasattr(dt, "date") else dt
    delta = today - dt_date
    days = delta.days

    if days == 0:
        return tr("donate_when_today").lower()
    elif days == 1:
        return tr("donate_when_yesterday").lower()
    elif days < 7:
        return tr("donate_when_days").format(count=days)
    elif days < 14:
        return tr("donate_when_week")

    if dt.year == now.year and dt.month == now.month - 1:
        return tr("donate_when_last_month")
    elif dt.year == now.year - 1 and now.month == 1 and dt.month == 12:
        return tr("donate_when_last_month")

    if dt.year == now.year:
        month_name = tr(f"month_{dt.month:02d}")
        return tr("donate_when_in_month").format(month=month_name)

    if dt.year == now.year - 1:
        return tr("donate_when_last_year")

    return tr("donate_when_in_year").format(year=dt.year)


def load_donors_file(paths):
    for p in paths:
        cand = Path(p)
        if not cand.is_file() and cand.name == "donors.csv" and cand.parent.name == "assets":
            alt = cand.parent / "online" / "donors.csv"
            if alt.is_file():
                cand = alt
        if cand.is_file():
            try:
                text = cand.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    return text
            except Exception:
                pass
    return None


def get_monthly_goal_stats(donations, now=None):
    now = now or datetime.now()
    target = MONTHLY_GOAL_USD
    cur = [d for d in donations
           if d.get("dt") and d["dt"].month == now.month
           and d["dt"].year == now.year and d.get("amount", 0) > 0]
    raised = sum(d["amount"] for d in cur)
    percent = min(100.0, max(0.0, (raised / target) * 100.0))
    remaining = max(0.0, target - raised)
    return raised, remaining, percent, target


# The monthly figure is a summary, not the everyday ask: it leads on the first
# and last three days of a month, and otherwise appears once the smaller rungs
# are covered. Tuesday, Wednesday and Saturday open on a single day's costs;
# the rest of the week opens on the week. A day still short of $9.99 names
# today's costs whichever rung it opened on, so a supporter sees the smallest
# outstanding target rather than a whole month's.
GOAL_MONTHLY_OPENING_DAYS = 3
GOAL_MONTHLY_CLOSING_DAYS = 3

# The slow weekdays whose ask opens on a single day's costs.
GOAL_DAILY_WEEKDAYS = (1, 2, 5)  # Tuesday, Wednesday, Saturday

WEEKLY_GOAL_USD = MONTHLY_GOAL_USD / 4.0
DAILY_GOAL_USD = MONTHLY_GOAL_USD / 20.0

# A day is paid at $9.99, not the full $10: a one-cent shortfall is a rounding
# artefact, and the next rung should take over before the line reads complete.
GOAL_DAILY_REACHED_USD = 9.99

# When neither the week nor the month is complete, the ask is randomly one of
# the two. A strong rolling week ($60 in the last seven days) leans the display
# towards the month, whose summary then has the more useful context. The roll
# is kept for the calendar day so a rotating bar does not flip every refresh.
GOAL_WEEK_MONTH_SHARE = 0.5
GOAL_MONTHLY_BOOST_USD = 60.0
GOAL_MONTHLY_BOOST_SHARE = 0.8
_GOAL_MONTH_ROLLS = {}

GOAL_COPY_KEYS = {
    "daily": "donate_goal_day_fmt",
    "weekly": "donate_goal_week_fmt",
    "monthly": "donate_goal_fmt",
}


def get_user_currency() -> tuple[str, str]:
    return fx.pair()


def days_in_month(now=None) -> int:
    now = now or datetime.now()
    if now.month == 12:
        first_next = datetime(now.year + 1, 1, 1)
    else:
        first_next = datetime(now.year, now.month + 1, 1)
    return (first_next - datetime(now.year, now.month, 1)).days


def goal_scope(now=None) -> str:
    """The rung a day opens on.

    The first and last three days of a month summarise the month so far.
    Tuesday, Wednesday and Saturday open on the day's own costs; the rest of
    the week opens on the week. A day that is still short of $9.99 names
    today's costs whichever rung it opened on (see :func:`get_goal_stats`).
    """
    now = now or datetime.now()
    if now.day <= GOAL_MONTHLY_OPENING_DAYS:
        return "monthly"
    if now.day > days_in_month(now) - GOAL_MONTHLY_CLOSING_DAYS:
        return "monthly"
    if now.weekday() in GOAL_DAILY_WEEKDAYS:
        return "daily"
    return "weekly"


def goal_ladder(now=None) -> tuple:
    """Rungs a day's display may name, smallest first.

    Month-edge days name only the month. Every other day can name today's
    costs while they are short, then either the week or the month. The choice
    between those two is made in :func:`get_goal_stats` so it stays stable for
    the day instead of changing on every refresh.
    """
    now = now or datetime.now()
    if goal_scope(now) == "monthly":
        return ("monthly",)
    return ("daily", "weekly", "monthly")


def goal_period_start(now=None, scope=None) -> datetime:
    """First day of ``scope`` (default: this day's opening rung).

    Weeks are calendar weeks, Monday to Sunday, matching the website's funding
    bar. A day is the calendar day and a month starts on the 1st.
    """
    now = now or datetime.now()
    scope = scope or goal_scope(now)
    if scope == "monthly":
        return datetime(now.year, now.month, 1)
    if scope == "daily":
        return datetime(now.year, now.month, now.day)
    return datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())


def weekly_goal_usd(now=None) -> float:
    return WEEKLY_GOAL_USD


def daily_goal_usd(now=None) -> float:
    return DAILY_GOAL_USD


def format_goal_amount(amount) -> str:
    amount = float(amount or 0)
    big = abs(amount) >= 1000
    if amount.is_integer():
        return f"{amount:,.0f}" if big else str(int(amount))
    return f"{amount:,.2f}" if big else f"{amount:.2f}"


def _goal_monthly_roll(now):
    """A stable 0..1 roll for the day's week-or-month choice."""
    key = now.date()
    roll = _GOAL_MONTH_ROLLS.get(key)
    if roll is None:
        _GOAL_MONTH_ROLLS.clear()
        roll = random.random()
        _GOAL_MONTH_ROLLS[key] = roll
    return roll


def _goal_raised_by_window(donations, now):
    """Money actually raised today, this week, this month and in the last week.

    Only received gifts count, and never a row dated later than ``now``: a
    scheduled donation has not arrived yet. The month counts from the 1st; the
    week is the calendar week (Monday to Sunday) and may begin in the previous
    month; the rolling week is the seven days ending today.
    """
    today = now.date()
    week_start = goal_period_start(now, "weekly").date()
    month_start = today.replace(day=1)
    rolling_start = today - timedelta(days=6)
    day_raised = week_raised = month_raised = rolling_raised = 0.0
    for donation in donations or ():
        dt = donation.get("dt")
        if not dt:
            continue
        amount = float(donation.get("amount", 0) or 0)
        if amount <= 0:
            continue
        day = dt.date() if hasattr(dt, "date") else dt
        if day > today:
            continue
        if day >= month_start:
            month_raised += amount
        if day >= week_start:
            week_raised += amount
        if day >= today:
            day_raised += amount
        if day >= rolling_start:
            rolling_raised += amount
    return day_raised, week_raised, month_raised, rolling_raised


def get_goal_stats(donations, now=None, monthly_roll=None):
    """Raised against the target this day's display should name.

    The ladder follows the website's funding bar: a month-edge day always
    names the month; a day short of $9.99 names today; after that a completed
    week names the month, and while both week and month are open the display
    picks one of them for the day. A finished month is silent.
    """
    now = now or datetime.now()
    day_raised, week_raised, month_raised, rolling_raised = _goal_raised_by_window(donations, now)
    sym, code = fx.pair()
    targets = {
        "daily": float(DAILY_GOAL_USD),
        "weekly": float(WEEKLY_GOAL_USD),
        "monthly": float(MONTHLY_GOAL_USD),
    }
    raised_by = {"daily": day_raised, "weekly": week_raised, "monthly": month_raised}
    month_reached = month_raised >= MONTHLY_GOAL_USD

    scope = None
    if not month_reached:
        if goal_scope(now) == "monthly":
            scope = "monthly"
        elif day_raised < GOAL_DAILY_REACHED_USD:
            scope = "daily"
        elif week_raised >= WEEKLY_GOAL_USD:
            scope = "monthly"
        else:
            roll = _goal_monthly_roll(now) if monthly_roll is None else float(monthly_roll)
            monthly_share = (
                GOAL_MONTHLY_BOOST_SHARE
                if rolling_raised >= GOAL_MONTHLY_BOOST_USD
                else GOAL_WEEK_MONTH_SHARE
            )
            scope = "monthly" if roll < monthly_share else "weekly"

    stats = {
        "scope": scope,
        "raised": 0.0,
        "remaining": 0.0,
        "percent": 0.0,
        "target": 0.0,
        "period_start": None,
        "reached": scope is None,
        "covered": scope is None,
        "day_raised": day_raised,
        "week_raised": week_raised,
        "month_raised": month_raised,
        "last_week_raised": rolling_raised,
        "month_reached": month_reached,
        "currency_symbol": sym,
        "currency_code": code,
        "shown_raised": 0.0,
        "shown_target": 0.0,
        "shown_percent": 0.0,
    }
    if scope is None:
        return stats

    target = targets[scope]
    raised = raised_by[scope]
    shown_raised = fx.local(raised, code)
    shown_target = fx.goal(target, code)
    stats.update(
        raised=raised,
        remaining=max(0.0, target - raised),
        percent=min(100.0, max(0.0, (raised / target) * 100.0)),
        target=target,
        period_start=goal_period_start(now, scope),
        reached=raised >= target,
        shown_raised=shown_raised,
        shown_target=shown_target,
        shown_percent=fx.share(shown_raised, shown_target),
    )
    return stats


def _fill_goal_template(template, raised_text, target_text):
    """Fill a goal sentence, including catalogs that still hardcode ``$``.

    Older translations wrap the amount in a dollar sign (``${target:.0f}``)
    because the goal used to be shown in USD. The reader's own currency mark is
    passed in already, so the dollar is removed from the template before the
    amounts (which may themselves be dollars) are substituted.
    """
    text = str(template or "").replace("$", "")
    try:
        text = text.format(raised=raised_text, target=target_text)
    except (KeyError, IndexError, ValueError):
        text = re.sub(
            r"\{\s*(raised|target)(?::[^}]*)?\}",
            lambda match: raised_text if match.group(1) == "raised" else target_text,
            text,
        )
    return text.strip()


def format_goal_line(stats) -> str:
    """Warm, period-aware costs line for the status bar and modal.

    ``stats`` comes from :func:`get_goal_stats`, so the sentence names the
    same daily/weekly/monthly rung the support appeal is using. A quiet day
    still names today's smallest ask ("$0 of $10 costs for today"); a zero
    weekly/monthly summary stays silent rather than reading as a cold target.
    An already-covered rung also returns an empty string.
    """
    from .i18n import tr

    stats = stats or {}
    key = GOAL_COPY_KEYS.get(stats.get("scope"))
    if not key or stats.get("reached") or stats.get("covered"):
        return ""
    raised = float(stats.get("shown_raised") or 0)
    target = float(stats.get("shown_target") or 0)
    if target <= 0 or raised >= target:
        return ""
    if raised <= 0 and stats.get("scope") != "daily":
        return ""
    code = str(stats.get("currency_code") or "USD")
    raised_text = fx.money_local(raised, code)
    target_text = fx.money_local(target, code)
    return _fill_goal_template(tr(key), raised_text, target_text)


def cached_donors_path() -> Path:
    from .catalog import _app_cache_dir
    d = _app_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "donors.csv"


_default_cached_donors_path = cached_donors_path


def _log_connectivity(ok: bool, detail: str) -> None:
    if ok:
        logger.info("Connectivity check: online (%s)", detail)
    else:
        logger.warning("Connectivity check: offline (%s)", detail)


def fetch_remote_donors_async(on_result):
    def _cached():
        text = load_donors_file([cached_donors_path()])
        if not text and cached_donors_path is _default_cached_donors_path:
            try:
                from . import online_data

                text = online_data.read_text("donors.csv")
            except Exception:
                text = ""
        if not text:
            return None
        return parse_donors_csv_text(text) or None

    def _fetch_text(url):
        # Imported here, not at module import: the donation bar is built on
        # every launch, and requests/urllib3 on the first-paint path is ~100 ms
        # and several MB of mapped pages for a fetch that runs seconds later.
        return requests_module().get(
            url,
            headers={
                "User-Agent": "InnioasisUpdater/1.0",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
            timeout=10.0,
        )

    def _worker():
        reason = ""
        for url in site_source_urls("donors.csv"):
            separator = "&" if "?" in url else "?"
            try:
                resp = _fetch_text(f"{url}{separator}_t={int(time.time())}")
                if resp.status_code == 200 and resp.text.strip():
                    text = resp.text
                    parsed = parse_donors_csv_text(text)
                    if parsed:
                        try:
                            cached_donors_path().write_text(text, encoding="utf-8")
                        except Exception as ce:
                            logger.debug("Could not cache donors.csv to disk: %s", ce)
                        _log_connectivity(True, f"{_DONORS_HOST} responded ({len(text)} bytes)")
                        try:
                            on_result(parsed)
                        except RuntimeError:
                            pass
                        return
                    reason = f"{_DONORS_HOST} returned an unusable feed (HTTP {resp.status_code})"
                elif resp.status_code == 200:
                    reason = f"{_DONORS_HOST} returned an empty feed (HTTP {resp.status_code})"
                else:
                    reason = f"{_DONORS_HOST} returned HTTP {resp.status_code}"
            except Exception as e:
                reason = f"{_DONORS_HOST} unreachable ({e})"
                logger.debug("donors.csv source failed: %s", e)

        _log_connectivity(False, reason)
        cached = _cached()
        if cached:
            logger.info("Using the most recent locally cached feed instead")
        try:
            on_result(cached)
        except RuntimeError:
            pass

    threading.Thread(target=_worker, daemon=True).start()
