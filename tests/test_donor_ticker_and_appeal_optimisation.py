import datetime
import re
import pytest
from PySide6.QtWidgets import QApplication

from src.donation_dialog import DonationStatusBar
from src.i18n import translator, tr
from src.ui.flash_page import FlashPage
from src import release_blocklist, pat_pool


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


def test_donor_bought_us_a_coffee_single():
    """Where one Ko-fi coffee was donated, the line names Ko-fi."""
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    donations = [
        {
            "name": "Alice",
            "amount": 1.0,
            "method": "Ko-fi",
            "dt": yesterday,
            "url": "",
        }
    ]
    sb = _make_status_bar(donations)
    lines = sb._build_donor_lines()
    assert len(lines) == 1
    clean = clean_html(lines[0])
    assert "bought us a coffee on Ko-Fi" in clean
    assert "bought us 1 coffee" not in clean
    assert "Alice bought us a coffee on Ko-Fi" in clean


def test_donor_bought_us_multiple_coffees():
    """Where multiple Ko-fi coffees were donated, the line names Ko-fi."""
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    donations = [
        {
            "name": "Derek M",
            "amount": 5.0,
            "method": "Ko-fi",
            "dt": yesterday,
            "url": "",
        }
    ]
    sb = _make_status_bar(donations)
    lines = sb._build_donor_lines()
    assert len(lines) == 1
    clean = clean_html(lines[0])
    assert "Derek M bought us 5 coffees on Ko-Fi" in clean


def test_buy_me_a_coffee_line_stays_plain():
    """A Buy Me a Coffee gift uses the plain coffees wording."""
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    donations = [
        {
            "name": "Derek M",
            "amount": 5.0,
            "method": "Buy Me a Coffee",
            "dt": yesterday,
            "url": "",
        }
    ]
    sb = _make_status_bar(donations)
    lines = sb._build_donor_lines()
    assert len(lines) == 1
    clean = clean_html(lines[0])
    assert "Derek M bought us 5 coffees" in clean
    assert "on Ko-Fi" not in clean


@pytest.mark.parametrize("generic_name", ["", "   ", "Unknown", "unknown", "Supporter", "supporter", "Anonymous", "anonymous", "null", "NULL"])
def test_generic_supporter_naming(generic_name):
    """Where supporter name is empty or generic, display as 'A supporter' or 'A top supporter'."""
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    
    # 1. Standard donation (< $20): "A supporter"
    donations_std = [
        {
            "name": generic_name,
            "amount": 1.0,
            "method": "Ko-fi",
            "dt": yesterday,
            "url": "",
        }
    ]
    sb_std = _make_status_bar(donations_std)
    lines_std = sb_std._build_donor_lines()
    assert len(lines_std) == 1
    clean_std = clean_html(lines_std[0])
    assert "A supporter bought us a coffee on Ko-Fi" in clean_std
    assert "Top supporter" not in clean_std

    # 2. Standard donation multiple coffees: "A supporter bought us 7 coffees"
    donations_7 = [
        {
            "name": generic_name,
            "amount": 7.0,
            "method": "Ko-fi",
            "dt": yesterday,
            "url": "",
        }
    ]
    sb_7 = _make_status_bar(donations_7)
    lines_7 = sb_7._build_donor_lines()
    clean_7 = clean_html(lines_7[0])
    assert "A supporter bought us 7 coffees on Ko-Fi" in clean_7

    # 3. Standard donation dollar amount: "A supporter donated $10"
    donations_dollar = [
        {
            "name": generic_name,
            "amount": 10.0,
            "method": "PayPal",
            "dt": yesterday,
            "url": "",
        }
    ]
    sb_dollar = _make_status_bar(donations_dollar)
    lines_dollar = sb_dollar._build_donor_lines()
    clean_dollar = clean_html(lines_dollar[0])
    assert "A supporter donated $10 by PayPal" in clean_dollar

    # 4. Top supporter (>= $20): "A top supporter"
    donations_top = [
        {
            "name": generic_name,
            "amount": 25.0,
            "method": "PayPal",
            "dt": yesterday,
            "url": "",
        }
    ]
    sb_top = _make_status_bar(donations_top)
    lines_top = sb_top._build_donor_lines()
    assert len(lines_top) == 1
    clean_top = clean_html(lines_top[0])
    assert "A top supporter donated $25 by PayPal" in clean_top
    # Ensure it does not say "Top supporter A top supporter"
    assert "Top supporter A top supporter" not in clean_top


def test_psychological_donation_appeal_variants(qapp):
    """Verify presence of psychological donation appeal variants and dynamic rotation."""
    fp = FlashPage()
    fp.show_flashing()
    fp.set_device_done(donations_shown=True)

    from src.ui.support_appeal import COMPLETION_BODY_KEYS

    expected_keys = list(COMPLETION_BODY_KEYS)

    for key in expected_keys:
        text = tr(key)
        assert len(text) > 20
        assert "coffee" in text.lower() or "café" in text.lower()

    # Verify that show_completion_appeal selects from the variants
    seen_keys = set()
    for _ in range(50):
        fp.show_completion_appeal(True)
        key = getattr(fp, "_active_appeal_key", None)
        assert key in expected_keys
        seen_keys.add(key)
    # With 50 iterations over 6 items, we should see multiple distinct variants
    assert len(seen_keys) > 1


def test_release_blocklist_does_not_block_startup(monkeypatch):
    """Ensure load_blocklist reads cached/bundled data without blocking network calls."""
    network_called = False

    def fake_get(*args, **kwargs):
        nonlocal network_called
        network_called = True
        raise RuntimeError("Network should not be called synchronously in load_blocklist")

    monkeypatch.setattr("src.network.requests_module", lambda: type("Req", (), {"get": staticmethod(fake_get)}))
    
    rules = release_blocklist.load_blocklist()
    assert not network_called
    assert isinstance(rules, list)


def test_pat_pool_does_not_block_startup(monkeypatch):
    """Ensure get_candidate_pats returns instantly without synchronous network requests."""
    network_called = False

    def fake_urlopen(*args, **kwargs):
        nonlocal network_called
        network_called = True
        raise RuntimeError("Network should not be called synchronously in get_candidate_pats")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    pats = pat_pool.get_random_pats(count=3)
    assert not network_called
    assert len(pats) > 0
