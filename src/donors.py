"""Donors data — CSV parsing, funding-goal stats, live remote fetch.

Ported from CE's ``load_donors_data`` / ``parse_donors_csv_text`` /
``get_monthly_goal_stats`` / ``fetch_remote_donors_async``, plus the funding
phases: the monthly figure on the month's opening five and closing seven days,
the week's own share of the same costs on every day between them.
"""

import csv
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import DONORS_CSV_URL, MONTHLY_GOAL_USD, site_source_urls
from .network import requests_module

requests = requests_module()
logger = logging.getLogger(__name__)

# Host of the supporters feed, named in connectivity logs instead of the URL.
_DONORS_HOST = "innioasis.app"

# Ledger labels used when a supporter gave without a name. Every one of these
# rows is a different person, so they must never be merged into one supporter.
_ANONYMOUS_DONOR_NAMES = {
    "", "supporter", "supporters", "a supporter", "top supporter",
    "a top supporter", "someone", "anonymous", "anon", "none", "n/a",
    "unknown", "null",
}


def is_anonymous_donor(name) -> bool:
    """True when a ledger name identifies no specific person.

    Anonymous rows arrive as an empty field, ``Supporter`` or ``Someone``, and
    older cached feeds also carry the display label ``A supporter``. Matching
    all of them keeps anonymous gifts from being grouped as one donor.
    """
    low = " ".join(str(name or "").split()).lower()
    if low in _ANONYMOUS_DONOR_NAMES:
        return True
    return low.startswith(("supporter", "anonymous", "someone"))


def parse_donors_csv_text(csv_text):
    """Parse raw CSV text into structured donation records (see donors.csv)."""
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
    """Return a short relative-time string for a donation date.

    Returns a translated string such as 'Today', 'Yesterday', 'On Monday',
    '3 days ago', '2 weeks ago', 'a month ago', etc.
    """
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
    """Return a natural phrasing for the donation date: 'last year', 'in August', 'last month', etc."""
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

    # Check last month
    if dt.year == now.year and dt.month == now.month - 1:
        return tr("donate_when_last_month")
    elif dt.year == now.year - 1 and now.month == 1 and dt.month == 12:
        return tr("donate_when_last_month")

    # Check in same year
    if dt.year == now.year:
        month_name = tr(f"month_{dt.month:02d}")
        return tr("donate_when_in_month").format(month=month_name)

    # Check last year
    if dt.year == now.year - 1:
        return tr("donate_when_last_year")

    # Earlier year
    return tr("donate_when_in_year").format(year=dt.year)


def load_donors_file(paths):
    """Read the first existing non-empty donors.csv among ``paths``."""
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
    """Amount raised in the current calendar month towards the $200 target."""
    now = now or datetime.now()
    target = MONTHLY_GOAL_USD
    cur = [d for d in donations
           if d.get("dt") and d["dt"].month == now.month
           and d["dt"].year == now.year and d.get("amount", 0) > 0]
    raised = sum(d["amount"] for d in cur)
    percent = min(100.0, max(0.0, (raised / target) * 100.0))
    remaining = max(0.0, target - raised)
    return raised, remaining, percent, target


#: Days of the month whose goal display names the monthly target: the opening
#: five and the closing seven. Every day between them names a smaller period —
#: a week's $50, or a day's $10 on the quiet weekdays — so the ask keeps its
#: urgency instead of going quiet once the month's total looks healthy.
GOAL_MONTHLY_OPENING_DAYS = 5
GOAL_MONTHLY_CLOSING_DAYS = 7

#: The quiet weekdays whose display names a single day's costs instead of a
#: week's (``datetime`` weekday numbers: Monday, Tuesday, Wednesday, Saturday).
#: They are the slowest days for gifts, so a $10 target measured against that
#: day's own donations says more than a week that has barely begun.
GOAL_DAILY_WEEKDAYS = (0, 1, 2, 5)

#: A week of the month's running costs: one quarter of the $200 figure, so
#: the weekly ask is always the same $50 rather than drifting with the length
#: of the month. A day is a twentieth of the same figure: $10.
WEEKLY_GOAL_USD = MONTHLY_GOAL_USD / 4.0
DAILY_GOAL_USD = MONTHLY_GOAL_USD / 20.0

#: A period with less than this left is treated as paid for: naming a 40-cent
#: shortfall would read as a rounding error rather than a need.
GOAL_SETTLED_USD = 1.0


def get_user_currency() -> tuple[str, str]:
    """Detect whether user should see GBP (£), EUR (€), or USD ($).

    Transposed 1:1 without conversion for users in the UK or Europe.
    Returns (symbol, currency_code), e.g. ('£', 'GBP'), ('€', 'EUR'), or ('$', 'USD').
    """
    import os

    # Check environment override first (convenient for testing or explicit config)
    env_curr = os.environ.get("UPDATER_CURRENCY", "").upper()
    if env_curr == "GBP":
        return ("£", "GBP")
    if env_curr == "EUR":
        return ("€", "EUR")
    if env_curr == "USD":
        return ("$", "USD")

    locale_name = ""
    territory_name = ""
    try:
        from PySide6.QtCore import QLocale

        loc = QLocale.system()
        locale_name = loc.name().lower()
        try:
            territory_name = loc.territory().name.lower()
        except Exception:
            try:
                territory_name = loc.country().name.lower()
            except Exception:
                pass
    except Exception:
        pass

    env_locale = (
        os.environ.get("LC_ALL", "")
        or os.environ.get("LC_MONETARY", "")
        or os.environ.get("LANG", "")
    ).lower()

    tz_name = ""
    try:
        import time

        tz_name = (time.tzname[0] if time.tzname else "").lower()
    except Exception:
        pass
    try:
        tzinfo = datetime.now().astimezone().tzinfo
        if tzinfo:
            tz_name = f"{tz_name} {str(tzinfo)}".lower()
    except Exception:
        pass

    combined_locale = f"{locale_name} {territory_name} {env_locale}".lower()

    # United Kingdom check
    if (
        "gb" in combined_locale
        or "uk" in combined_locale
        or "unitedkingdom" in territory_name
        or "london" in tz_name
        or "bst" in tz_name
        or ("gmt" in tz_name and ("en_gb" in combined_locale or "gb" in combined_locale))
    ):
        return ("£", "GBP")

    # Europe check
    european_countries = (
        "france", "germany", "spain", "italy", "netherlands", "belgium",
        "ireland", "austria", "portugal", "finland", "greece", "poland",
        "sweden", "denmark", "norway", "switzerland", "czech", "hungary",
        "romania", "bulgaria", "slovakia", "croatia", "slovenia", "estonia",
        "latvia", "lithuania", "cyprus", "malta", "luxembourg", "iceland",
    )
    european_locales = (
        "fr_", "de_", "es_", "it_", "nl_", "pt_", "pl_", "sv_", "da_", "nb_",
        "nn_", "fi_", "el_", "cs_", "hu_", "ro_", "bg_", "sk_", "hr_", "sl_",
        "et_", "lv_", "lt_", "ga_", "is_",
    )
    if (
        any(c in territory_name for c in european_countries)
        or any(
            combined_locale.startswith(loc_prefix)
            or f"_{loc_prefix}" in combined_locale
            or f" {loc_prefix}" in combined_locale
            for loc_prefix in european_locales
        )
        or ("europe/" in tz_name and "london" not in tz_name)
        or "cest" in tz_name
        or "cet" in tz_name
    ):
        return ("€", "EUR")

    return ("$", "USD")


def days_in_month(now=None) -> int:
    """Number of days in ``now``'s calendar month."""
    now = now or datetime.now()
    if now.month == 12:
        first_next = datetime(now.year + 1, 1, 1)
    else:
        first_next = datetime(now.year, now.month + 1, 1)
    return (first_next - datetime(now.year, now.month, 1)).days


def goal_scope(now=None) -> str:
    """The period this day's ask starts on.

    ``"monthly"`` on the month's edge days, ``"daily"`` on the quiet weekdays
    between them (Monday, Tuesday, Wednesday, Saturday), and ``"weekly"`` on
    the rest. This is where the ladder starts, not where it stops:
    ``get_goal_stats`` walks on from here when the period is already paid for.
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
    """The periods this day's ask falls through when one is already paid for.

    The month is the largest rung and never ladders: a covered month has no
    outstanding costs to name at all, so the display goes silent instead of
    inventing a fourth ask. A covered week hands down to the day, and a
    covered day — or a week that filled early — climbs to the week, then the
    month, so exactly one outstanding target is named, or none.
    """
    now = now or datetime.now()
    base = goal_scope(now)
    if base == "daily":
        return ("daily", "weekly", "monthly")
    if base == "weekly":
        return ("weekly", "daily", "monthly")
    return ("monthly",)


def goal_period_start(now=None, scope=None) -> datetime:
    """First day of the period ``scope`` names (default: this day's scope).

    The month starts on day 1. A day's period is that calendar day. A week
    opens on day 6, so weeks are seven-day blocks from there (6-12, 13-19,
    ...). Each block counts from zero: donations from earlier days were already
    shown against their own periods, and counting them again would hide the
    time that is actually short.
    """
    now = now or datetime.now()
    scope = scope or goal_scope(now)
    if scope == "monthly":
        return datetime(now.year, now.month, 1)
    if scope == "daily":
        return datetime(now.year, now.month, now.day)
    block = max(0, (now.day - GOAL_MONTHLY_OPENING_DAYS - 1) // 7)
    start_day = max(1, GOAL_MONTHLY_OPENING_DAYS + 1 + block * 7)
    return datetime(now.year, now.month, start_day)


def weekly_goal_usd(now=None) -> float:
    """One quarter of the monthly running costs ($200 / 4 = $50)."""
    return WEEKLY_GOAL_USD


def daily_goal_usd(now=None) -> float:
    """Daily costs target ($10) for the quiet weekdays mid-month."""
    return DAILY_GOAL_USD


def format_goal_amount(amount) -> str:
    """Whole dollars without cents, cents kept when the amount has them."""
    amount = float(amount or 0)
    return str(int(amount)) if amount.is_integer() else f"{amount:.2f}"


def _goal_raised_by_window(donations, now):
    """Money actually raised so far today, in this week block, and this month.

    Only received gifts count, only from the month being displayed, and never a
    row dated later than ``now``: a scheduled donation has not arrived yet.
    """
    today = now.date()
    week_start = goal_period_start(now, "weekly").date()
    month_start = datetime(now.year, now.month, 1).date()
    day_raised = week_raised = month_raised = 0.0
    for donation in donations or ():
        dt = donation.get("dt")
        if not dt:
            continue
        amount = float(donation.get("amount", 0) or 0)
        if amount <= 0:
            continue
        day = dt.date() if hasattr(dt, "date") else dt
        if day.year != now.year or day.month != now.month:
            continue
        if day > today:
            continue
        month_raised += amount
        if day >= week_start:
            week_raised += amount
        if day >= today:
            day_raised += amount
    return day_raised, week_raised, month_raised


def get_goal_stats(donations, now=None):
    """Raised against the target this day's display should name.

    The periods nest, so the display walks ``goal_ladder``: the first period
    that is still short is the one named. A paid period hands the ask to the
    next rung, and when no rung is left the display is silent — ``scope`` is
    ``None`` and ``covered`` is true, so no bar, no "raised of" and no
    "needed" sentence is drawn for costs that are already paid for.

    Returns ``{"scope", "raised", "remaining", "percent", "target",
    "period_start", "reached", "covered", "day_raised", "week_raised",
    "month_raised", "month_reached", "currency_symbol", "currency_code"}``.
    """
    now = now or datetime.now()
    day_raised, week_raised, month_raised = _goal_raised_by_window(donations, now)
    sym, code = get_user_currency()
    targets = {
        "daily": float(DAILY_GOAL_USD),
        "weekly": float(WEEKLY_GOAL_USD),
        "monthly": float(MONTHLY_GOAL_USD),
    }
    raised_by = {"daily": day_raised, "weekly": week_raised, "monthly": month_raised}
    month_reached = month_raised >= MONTHLY_GOAL_USD

    scope = None
    if not month_reached:
        for candidate in goal_ladder(now):
            if targets[candidate] - raised_by[candidate] >= GOAL_SETTLED_USD:
                scope = candidate
                break

    stats = {
        "scope": scope,
        "raised": 0.0,
        "remaining": 0.0,
        "percent": 0.0,
        "target": 0.0,
        "period_start": None,
        "reached": False,
        "covered": scope is None,
        # The three windows travel together: which rung is named depends on
        # all of them, so the display can fall back without recounting.
        "day_raised": day_raised,
        "week_raised": week_raised,
        "month_raised": month_raised,
        "month_reached": month_reached,
        "currency_symbol": sym,
        "currency_code": code,
    }
    if scope is None:
        return stats

    target = targets[scope]
    raised = raised_by[scope]
    stats.update(
        raised=raised,
        remaining=max(0.0, target - raised),
        percent=min(100.0, max(0.0, (raised / target) * 100.0)),
        target=target,
        period_start=goal_period_start(now, scope),
        reached=raised >= target,
    )
    return stats


def cached_donors_path() -> Path:
    """Path to the locally cached donors.csv file."""
    from .catalog import _app_cache_dir
    d = _app_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "donors.csv"


_default_cached_donors_path = cached_donors_path


def _log_connectivity(ok: bool, detail: str) -> None:
    """Record the reachability of the feed as a connectivity check.

    Supporter names and amounts never reach the diagnostics log: the feed is
    only ever read to show recognition inside the app. What the log keeps is
    whether the app could talk to its server, which is what a failure report
    needs and what the offline/online decision is based on.
    """
    if ok:
        logger.info("Connectivity check: online (%s)", detail)
    else:
        logger.warning("Connectivity check: offline (%s)", detail)


def fetch_remote_donors_async(on_result):
    """Fetch the latest donors.csv from innioasis.app in a background thread.

    ``on_result(donations_or_None)`` is invoked on success (from the worker
    thread; the caller is expected to marshal to the UI thread). When the feed
    cannot be reached the most recent local download is used instead, so the
    app keeps working if innioasis.app is unavailable.
    """

    def _cached():
        text = load_donors_file([cached_donors_path()])
        if not text and cached_donors_path is _default_cached_donors_path:
            # First run (or a fresh install): use the copy baked into the app.
            try:
                from . import online_data

                text = online_data.read_text("donors.csv")
            except Exception:
                text = ""
        if not text:
            return None
        return parse_donors_csv_text(text) or None

    def _fetch_text(url):
        return requests.get(
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
        # Raw GitHub first (fresh commit), then innioasis.app, then the
        # maintainer's fork if the organisation repository is unavailable.
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
        # Falling back to the newest local copy keeps the app usable offline.
        cached = _cached()
        if cached:
            logger.info("Using the most recent locally cached feed instead")
        try:
            on_result(cached)
        except RuntimeError:
            pass

    threading.Thread(target=_worker, daemon=True).start()
