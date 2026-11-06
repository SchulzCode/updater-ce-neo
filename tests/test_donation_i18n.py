"""Donations copy ships translated for every catalog.

The English source in ``src/i18n.py`` is the fallback, not the deliverable: a
catalog that repeats the English sentence shows the user an untranslated
donations card. This test pins that rule for the donation surfaces, including
the appeal and the supporter ticker, so a new English string cannot ship
without its copy in the other languages.

Only three kinds of cell are allowed to stay English:

* brand and product names (``Buy Me a Coffee``);
* copy that is genuinely the same word in the target language, such as
  ``in {month}`` in Afrikaans, Corsican, Friulian and Hunsrik;
* a proper-noun line (``Ryan Specter & Community Contributors``) and the N'Ko
  cells no translation provider can produce, both listed per cell below.
"""

import json
import re
from pathlib import Path

import pytest

from src import i18n

CATALOG_DIR = Path(__file__).resolve().parent.parent / "assets" / "i18n"

#: Product names stay as they are in every language.
BRAND_KEYS = {"donate_buy_me_a_coffee"}

#: ``(catalog, key)`` cells that are correct in English, or that no provider
#: can translate. Each entry is deliberate; do not grow this set for a new
#: string that simply has no translation yet.
ENGLISH_OK = {
    ("af", "donate_when_in_month"),
    ("af", "donate_when_in_year"),
    ("co", "donate_when_in_month"),
    ("co", "donate_when_in_year"),
    ("fur", "donate_when_in_month"),
    ("fur", "donate_when_in_year"),
    ("hrx", "donate_when_in_month"),
    ("hrx", "donate_when_in_year"),
    ("bm-Nkoo", "donate_when_in_month"),
    ("bm-Nkoo", "donate_when_in_year"),
    ("bm-Nkoo", "donate_donated_amount"),
    ("cnh", "donate_support_author"),
}


def _catalogs():
    for path in sorted(CATALOG_DIR.glob("*.json")):
        if path.stem == "languages":
            continue
        yield path.stem, json.loads(path.read_text(encoding="utf-8"))


def _english_donation_keys():
    """Donation keys with English copy the UI actually shows."""
    for key, table in i18n._STRINGS.items():
        if not key.startswith("donate_") or key in BRAND_KEYS:
            continue
        english = str((table or {}).get("en") or "").strip()
        if not english or "<span" in english:
            # The completion headline is a styled sentence assembled in code,
            # not a plain string a catalog can own.
            continue
        yield key, english


def test_every_donation_string_is_translated_in_every_catalog():
    missing = []
    for lang, catalog in _catalogs():
        for key, english in _english_donation_keys():
            if (lang, key) in ENGLISH_OK:
                continue
            value = str(catalog.get(key) or "").strip()
            if not value or value == english:
                missing.append(f"{lang}:{key}")
    assert not missing, (
        "donation copy still falls back to English in these catalogs: "
        + ", ".join(sorted(missing))
    )


def test_ticker_templates_keep_the_placeholders_the_code_fills():
    """A translated ticker is only usable if its format fields survive."""
    fields = ("anchor", "amount", "method", "when", "count", "top_prefix")
    for lang, catalog in _catalogs():
        for key in (
            "donate_ticker_fmt",
            "donate_ticker_status_fmt",
            "donate_bought_coffee",
            "donate_bought_coffees",
            "donate_bought_coffee_kofi",
            "donate_bought_coffees_kofi",
            "donate_donated_amount",
            "donate_donated_by_method",
        ):
            value = str(catalog.get(key) or "")
            for field in fields:
                source = str((i18n._STRINGS.get(key) or {}).get("en") or "")
                if "{%s}" % field in source:
                    assert "{%s}" % field in value, f"{lang}:{key} lost {{{field}}}"


def test_the_support_appeal_uses_one_complete_sentence_and_keeps_its_bold_lead():
    """The old pair split a sentence mid-clause; translators ended it there."""
    from src.ui.support_appeal import support_appeal_html

    old = i18n.translator().lang
    i18n.translator().set_language("ru")
    try:
        full = i18n.catalog("ru")["donate_support_appeal_full"]
        html = support_appeal_html("donate_support_appeal_lead")
        plain = re.sub(r"<[^>]+>", "", html)
        assert plain == full
        assert html.startswith("<b>")
        assert html.index("</b>") < len(html)
        assert "устройств. которые" not in plain
    finally:
        i18n.translator().set_language(old)
