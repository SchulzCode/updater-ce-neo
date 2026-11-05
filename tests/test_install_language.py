"""The Windows installer hands its language to the first app launch."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import install_language
from src import i18n
from src.i18n import catalog_codes
from src.shortcut_manager import INNO_SHORTCUT_LANGUAGES


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def test_installer_language_names_map_to_shipped_app_catalogs():
    assert install_language.language_for_installer("german") == "de"
    assert install_language.language_for_installer("DANISH") == "da"
    assert install_language.language_for_installer("chinesesimplified") == "zh-CN"
    assert install_language.language_for_installer("not-a-language") == ""

    available = set(catalog_codes())
    for name in INNO_SHORTCUT_LANGUAGES:
        code = install_language.language_for_installer(name)
        if code:
            assert code in available, (name, code)


def test_marker_is_applied_once_and_removed(tmp_path):
    marker = tmp_path / install_language.MARKER_NAME
    marker.write_text("danish\n", encoding="utf-8")
    settings = _Settings()

    assert install_language.consume_installer_language(settings, marker) == "da"
    assert settings.values == {"language": "da"}
    assert not marker.exists()

    assert install_language.consume_installer_language(settings, marker) == ""
    assert settings.values == {"language": "da"}


def test_installer_language_replaces_a_stored_language(tmp_path):
    marker = tmp_path / install_language.MARKER_NAME
    marker.write_text("german", encoding="utf-8")
    settings = _Settings({"language": "fr"})

    assert install_language.consume_installer_language(settings, marker) == "de"
    assert settings.values == {"language": "de"}
    assert not marker.exists()


def test_installer_language_beats_the_detected_system_language(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "system_language", lambda: "fr")
    assert i18n.initial_language("") == "fr"

    marker = tmp_path / install_language.MARKER_NAME
    marker.write_text("german", encoding="utf-8")
    settings = _Settings()

    consumed = install_language.consume_installer_language(settings, marker)

    assert consumed == "de"
    assert settings.values == {"language": "de"}
    assert i18n.initial_language(settings.values["language"]) == "de"


def test_unknown_marker_is_removed_without_setting_a_language(tmp_path):
    marker = tmp_path / install_language.MARKER_NAME
    marker.write_text("not-a-language", encoding="utf-8")
    settings = _Settings()

    assert install_language.consume_installer_language(settings, marker) == ""
    assert settings.values == {}
    assert not marker.exists()


def _section(text, name):
    """The lines of one section, matched by its own header line."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == name)
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def test_installer_preselects_the_system_language_with_english_fallback():
    text = (ROOT / "scripts" / "build_windows_installer.iss").read_text(
        encoding="utf-8-sig"
    )
    setup = _section(text, "[Setup]")
    assert "LanguageDetectionMethod=uilanguage" in setup
    assert "UsePreviousLanguage=no" in setup
    assert "ShowLanguageDialog=yes" in setup

    languages = _section(text, "[Languages]")
    first_entry = next(
        line for line in languages.splitlines() if line.strip().startswith("Name:")
    )
    assert '"english"' in first_entry


def test_installer_writes_the_active_language_marker_for_a_fresh_install():
    text = (ROOT / "scripts" / "build_windows_installer.iss").read_text(
        encoding="utf-8-sig"
    )
    step = text.split("procedure CurStepChanged", 1)[1]
    assert "SaveInstallLanguage;" in step
    assert "ActiveLanguage" in text
    assert "{localappdata}\\Updater CE\\install-language" in text
