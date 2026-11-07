"""Every language in the picker ships every English UI string.

The bundles have to work offline: a label must not fall back to English just
because a catalog was generated before that label existed. Adding a string to
``src/i18n.py`` therefore means adding it to every catalog in one change.
"""

import json
from pathlib import Path

from src import i18n

CATALOG_DIR = Path(__file__).resolve().parents[1] / "assets" / "i18n"


def _english_strings() -> dict:
    return {
        key: table["en"]
        for key, table in i18n._STRINGS.items()
        if isinstance(table, dict) and str(table.get("en") or "").strip()
    }


def _picker_languages() -> list[str]:
    """The codes the picker offers, i.e. the languages whose catalog ships.

    ``languages.json`` lists every language Google Translate knows, but the
    picker (``main_window._populate_languages`` → ``language_choices``) only
    offers a language whose catalog is in the bundle. Guarding the whole
    Google list instead would demand ~120 catalogs the app never shows.
    """
    return [
        code
        for code in i18n.shipped_catalog_codes()
        if not i18n.is_english_language(code)
    ]


def test_every_curated_language_has_a_catalog():
    """A hand-translated language sits in the picker even with no catalog file."""
    missing = [
        code
        for code in i18n.curated_codes()
        if not i18n.is_english_language(code)
        and not (CATALOG_DIR / f"{code}.json").is_file()
    ]
    assert not missing, (
        "these picker languages have no catalog and would fall back to English: "
        + ", ".join(missing)
    )


def test_every_catalog_contains_every_english_string():
    english = _english_strings()
    missing = {}
    for code in _picker_languages():
        path = CATALOG_DIR / f"{code}.json"
        if not path.is_file():
            continue  # the previous test names the missing catalog itself
        catalog = json.loads(path.read_text(encoding="utf-8"))
        absent = [
            key
            for key in english
            if not str(catalog.get(key) or "").strip()
        ]
        if absent:
            missing[code] = absent
    assert not missing, (
        "catalogs missing English UI strings (add them with "
        "`scripts/i18n_fill.py --lang <code>`): "
        + json.dumps(
            {code: keys[:8] for code, keys in list(missing.items())[:10]},
            ensure_ascii=False,
        )
    )


def test_every_english_string_has_a_source_id():
    """A GUI label with no ``_STRINGS`` entry cannot reach a catalog at all."""
    english = _english_strings()
    assert len(english) >= 900
    assert all(isinstance(text, str) and text.strip() for text in english.values())
