"""Anonymous ledger rows are different people and must never be merged."""

import datetime
import re

import pytest

from src.donation_dialog import DonationStatusBar
from src.donors import is_anonymous_donor, parse_donors_csv_text
from src.i18n import translator


def clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


@pytest.fixture(autouse=True)
def reset_translator():
    t = translator()
    t.set_language("en")
    yield
    t.set_language("en")


def _make_status_bar(donations):
    sb = DonationStatusBar.__new__(DonationStatusBar)
    sb.donations = donations
    return sb


ANONYMOUS_CSV = """Name,Amount,Date,URL,Method,Message
Supporter,50.25,11/08/2026,,Bitcoin,
Supporter,13.2,12/06/2026,,Bitcoin,
Someone,6.77,15/05/2026,,Ko-Fi,Thank you for your work.
Supporter,4.85,21/05/2026,,Bitcoin,
"""


@pytest.mark.parametrize(
    "name",
    ["", "Supporter", "supporter", "Someone", "Anon", "anonymous", "A supporter", "A top supporter"],
)
def test_anonymous_label_forms_are_recognised(name):
    assert is_anonymous_donor(name)


@pytest.mark.parametrize("name", ["Alexis P", "Mick T", "Zangu", "Luis C"])
def test_named_donors_are_not_anonymous(name):
    assert not is_anonymous_donor(name)


def test_parsing_keeps_each_anonymous_row_separate():
    donations = parse_donors_csv_text(ANONYMOUS_CSV)

    assert len(donations) == 4
    assert [d["amount"] for d in donations] == [50.25, 13.2, 6.77, 4.85]
    assert all(is_anonymous_donor(d["name"]) for d in donations)


def test_anonymous_gifts_are_not_merged_into_one_supporter():
    donations = parse_donors_csv_text(ANONYMOUS_CSV)
    lines = [clean_html(line) for line in _make_status_bar(donations)._build_donor_lines()]

    # Four different people: four lines, each with only its own amount
    # (Ko-fi gifts are phrased as coffees, so 6.77 reads as 7 coffees).
    assert len(lines) == 4
    for amount in ("$50.25", "$13.20", "7 coffees", "$4.85"):
        assert sum(amount in line for line in lines) == 1
    # The old bug summed these four people into one $75.07 supporter.
    for merged_total in ("75.07", "63.45", "70.22"):
        assert all(merged_total not in line for line in lines)
    assert all("supporter" in line.lower() for line in lines)
    # Never presented as repeat contributions from a single person.
    assert all("contributions" not in line.lower() for line in lines)


def test_two_anonymous_gifts_are_never_totalled_together():
    recent = datetime.datetime.now() - datetime.timedelta(days=1)
    older = recent - datetime.timedelta(days=3)
    donations = [
        {"name": "Supporter", "amount": 4.85, "method": "Bitcoin", "dt": older, "url": ""},
        {"name": "Someone", "amount": 6.77, "method": "Ko-Fi", "dt": recent, "url": ""},
    ]
    lines = [clean_html(line) for line in _make_status_bar(donations)._build_donor_lines()]

    assert len(lines) == 2
    # A second anonymous gift must not turn the first giver into a repeat donor.
    assert all("top supporter" not in line.lower() for line in lines)
    assert all("11.62" not in line for line in lines)
