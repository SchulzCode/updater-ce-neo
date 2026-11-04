"""Donation figures follow the reader's currency, not the dollar.

The goal line converted already; every donor line still printed bare dollars,
so a reader in Europe saw a converted goal beside unconverted tickers.
"""

import os
import sys
import urllib.request
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
def _offline(monkeypatch):
    import src.donation_dialog as dialog

    monkeypatch.setattr(dialog, "fetch_remote_donors_async", lambda callback: None)
    monkeypatch.setattr(dialog.fx, "refresh", lambda callback=None: None)


def _pin(monkeypatch, code, rate):
    import src.fx as fx

    monkeypatch.setenv("UPDATER_CURRENCY", code)
    monkeypatch.setattr(fx, "rate", lambda code="USD": rate)
    return fx


def test_amount_rounds_to_the_currencys_smallest_unit(monkeypatch):
    fx = _pin(monkeypatch, "USD", 1.0)
    assert fx.amount(5.79, "USD") == "5.79"
    assert fx.amount(10.0, "USD") == "10"
    assert fx.amount(1234.5, "USD") == "1,234.50"

    monkeypatch.setattr(fx, "rate", lambda code="USD": 150.0)
    assert fx.amount(10.0, "JPY") == "1,500"


def test_status_bar_ticker_converts_donor_amounts(qapp, monkeypatch):
    from src.donation_dialog import DonationStatusBar
    from src.i18n import translator

    _pin(monkeypatch, "GBP", 0.5)
    translator().set_language("en")
    donations = [
        {"name": "Mason M", "amount": 5.79, "method": "Revolut", "dt": datetime(2026, 7, 5)}
    ]
    bar = DonationStatusBar(donations=donations)
    line = next(text for text in bar._donor_lines if "Mason M" in text)
    assert "\u00a32.90" in line
    assert "$" not in line
    bar.deleteLater()


def test_donation_dialog_ticker_converts_donor_amounts(qapp, monkeypatch):
    from src.donation_dialog import DonationDialog
    from src.i18n import translator

    _pin(monkeypatch, "EUR", 0.8)
    translator().set_language("en")
    dlg = DonationDialog(
        context="general",
        donations=[
            {"name": "Mason M", "amount": 5.79, "method": "Revolut", "dt": datetime(2026, 7, 5)}
        ],
    )
    line = next(text for text in dlg._donor_lines() if "Mason M" in text)
    assert "\u20ac4.63" in line
    assert "$" not in line
    dlg.deleteLater()


def test_a_failed_feed_keeps_the_baked_table(monkeypatch):
    import src.fx as fx

    monkeypatch.setattr(
        fx, "_state", {"rates": {}, "at": 0.0, "warm": True, "busy": False, "failed": 0.0}
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    assert fx._pull() is False
    assert fx.rate("GBP") == fx._BAKED["GBP"]
