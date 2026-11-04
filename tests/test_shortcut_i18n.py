"""Shortcut names follow the languages the Windows installer offers."""

import re
from pathlib import Path

import pytest

from src import i18n
from src import shortcut_manager as sm


def _sandbox(tmp_path, monkeypatch):
    roots = {"menu": [], "desktop": []}
    for kind in ("menu", "desktop"):
        for scope in ("user", "common"):
            folder = tmp_path / f"{scope}_{kind}"
            folder.mkdir()
            roots[kind].append(folder)
    monkeypatch.setattr(sm, "_roots", lambda kind: list(roots[kind]))
    monkeypatch.setattr(sm, "supported", lambda: True)
    monkeypatch.setattr(sm, "_lnk_targets", lambda files: {})
    return roots


def test_installer_language_coverage_is_explicit():
    assert len(sm.INNO_SHORTCUT_LANGUAGES) == 32
    assert sm.INNO_SHORTCUT_LANGUAGES["russian"] == ("ru",)
    assert sm.INNO_SHORTCUT_LANGUAGES["brazilianportuguese"] == ("pt-BR", "pt")
    assert sm.INNO_SHORTCUT_LANGUAGES["chinesetraditional"] == ("zh-TW",)


def test_creation_name_follows_the_active_installer_language(monkeypatch):
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    expected = i18n.text_for_language("app_name", "ru")
    assert expected
    assert sm._creation_name(sm.APP_ID, "menu") == expected
    assert sm._creation_name(sm.APP_ID, "desktop") == expected


def test_localized_shortcut_in_an_owned_folder_is_detected(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    localized = i18n.text_for_language("app_name", "ru")
    assert localized
    folder = roots["menu"][0] / sm.DESKTOP_APP_NAME
    folder.mkdir()
    (folder / f"{localized}.lnk").write_bytes(b"x")

    assert sm.get_state()[sm.APP_ID]["menu"] is True


def test_top_level_localized_shortcut_needs_a_matching_target(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    localized = i18n.text_for_language("app_name", "ru")
    shortcut = roots["desktop"][0] / f"{localized}.lnk"
    shortcut.write_bytes(b"x")

    assert sm.get_state()[sm.APP_ID]["desktop"] is False

    monkeypatch.setattr(
        sm,
        "_lnk_targets",
        lambda files: {
            sm._path_key(shortcut): r"C:\Program Files\UpdaterCE\InnioasisUpdater.exe"
        },
    )
    assert sm.get_state()[sm.APP_ID]["desktop"] is True


def test_top_level_active_language_name_is_detected_without_a_target(
    tmp_path, monkeypatch
):
    roots = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    localized = i18n.text_for_language("app_name", "ru")
    (roots["desktop"][0] / f"{localized}.lnk").write_bytes(b"x")

    assert sm.get_state()[sm.APP_ID]["desktop"] is True


ROOT = Path(__file__).resolve().parent.parent


def _installer_shortcut_names():
    """The [CustomMessages] shortcut names the Windows installer compiles."""
    text = (ROOT / "scripts" / "build_windows_installer.iss").read_text(
        encoding="utf-8-sig"
    )
    section = text.split("[CustomMessages]", 1)[1].split("[Files]", 1)[0]
    values = {}
    keys = []
    for line in section.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        key, separator, value = line.partition("=")
        if separator and key.endswith(("AppShortcutName", "DesktopAppShortcutName")):
            keys.append(key)
            values[key] = value.strip()
    assert len(keys) == len(set(keys)), "duplicate shortcut custom message"
    return values


def test_installer_offers_exactly_the_shortcut_languages_we_map():
    """The Inno [Languages] list and the app's mapping must not drift apart."""
    text = (ROOT / "scripts" / "build_windows_installer.iss").read_text(
        encoding="utf-8-sig"
    )
    section = text.split("[Languages]", 1)[1].split("[", 1)[0]
    offered = set(re.findall(r'^\s*Name:\s*"([^"]+)"', section, re.MULTILINE))

    assert offered == set(sm.INNO_SHORTCUT_LANGUAGES)


def test_installer_shortcut_names_match_the_app(monkeypatch):
    """Every Inno language must name the shortcut exactly as the app does."""
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    messages = _installer_shortcut_names()
    assert messages["AppShortcutName"] == sm.APP_NAME
    assert messages["DesktopAppShortcutName"] == sm.DESKTOP_APP_NAME

    for inno_name, codes in sm.INNO_SHORTCUT_LANGUAGES.items():
        localized = sm._localized_creation_name(codes[0])
        expected_menu = localized or sm.APP_NAME
        expected_desktop = localized or sm.DESKTOP_APP_NAME
        menu = messages.get(f"{inno_name}.AppShortcutName", messages["AppShortcutName"])
        desktop = messages.get(
            f"{inno_name}.DesktopAppShortcutName",
            messages["DesktopAppShortcutName"],
        )
        assert menu == expected_menu, f"menu shortcut name for {inno_name}"
        assert desktop == expected_desktop, f"desktop shortcut name for {inno_name}"


@pytest.mark.skipif(not sm.paths.IS_WINDOWS, reason="reads a real Windows .lnk")
def test_localized_shortcut_target_is_read_without_powershell(tmp_path):
    """A Cyrillic .lnk must resolve its target through IShellLinkW."""
    work = tmp_path / "Программа обновления CE"
    work.mkdir()
    executable = work / "InnioasisUpdater.exe"
    executable.write_bytes(b"MZ")
    desktop = tmp_path / "Рабочий стол"
    desktop.mkdir()
    shortcut = desktop / "Программа обновления CE.lnk"

    sm._write_windows_lnk_com(shortcut, str(executable), str(work))
    sm._LNK_TARGET_CACHE.clear()
    resolved = sm._lnk_targets([shortcut])

    assert resolved[sm._path_key(shortcut)] == str(executable)


def test_english_creation_name_is_unchanged(monkeypatch):
    monkeypatch.setattr(sm, "current_language", lambda: "en")
    assert sm._creation_name(sm.APP_ID, "menu") == sm.APP_NAME
    assert sm._creation_name(sm.APP_ID, "desktop") == sm.DESKTOP_APP_NAME


@pytest.mark.skipif(not sm.paths.IS_WINDOWS, reason="writes a real Windows .lnk")
def test_removed_localized_shortcut_can_be_added_again(tmp_path, monkeypatch):
    """The switch must recreate the localized .lnk it just deleted.

    The old PowerShell writer turned a non-ASCII name into "????" and Save()
    failed, so once the installer's shortcut was removed the switch could not
    be turned back on in Russian, Japanese, Chinese or Arabic.
    """
    roots = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    executable = tmp_path / "InnioasisUpdater.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr(
        sm, "_launch_target", lambda shortcut_id: (str(executable), str(tmp_path))
    )
    name = i18n.text_for_language("app_name", "ru")
    shortcut = roots["desktop"][0] / f"{name}.lnk"

    assert sm.set_shortcut(sm.APP_ID, "desktop", True) is True
    assert shortcut.is_file()
    assert sm.get_state()[sm.APP_ID]["desktop"] is True

    assert sm.set_shortcut(sm.APP_ID, "desktop", False) is True
    assert not shortcut.exists()
    assert sm.get_state()[sm.APP_ID]["desktop"] is False

    assert sm.set_shortcut(sm.APP_ID, "desktop", True) is True
    assert shortcut.is_file()
    assert sm.get_state()[sm.APP_ID]["desktop"] is True
