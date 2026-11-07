"""Shortcut names follow the app's languages, not only the installer's list."""

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
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    expected = i18n.text_for_language("app_name", "ru")
    assert expected
    assert sm._creation_name(sm.APP_ID, "menu") == expected
    assert sm._creation_name(sm.APP_ID, "desktop") == expected


def test_creation_name_follows_languages_the_installer_cannot_offer(monkeypatch):
    """The app catalog, not Inno's 32 languages, decides the shortcut name."""
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    monkeypatch.setattr(sm, "current_language", lambda: "cy")
    expected = i18n.text_for_language("app_name", "cy")
    assert expected
    assert not any("cy" in codes for codes in sm.INNO_SHORTCUT_LANGUAGES.values())
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


def _generated_shortcut_names():
    """The names baked into Setup for localized shortcut cleanup."""
    path = ROOT / "scripts" / "build_windows_shortcut_names.iss"
    assert path.is_file(), "run scripts/generate_windows_shortcut_names.py"
    names = []
    prefix = "  Names.Add('"
    suffix = "');"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith(prefix):
            continue
        assert line.endswith(suffix), line
        names.append(line[len(prefix) : -len(suffix)].replace("''", "'"))
    return names


def test_installer_cleanup_list_covers_every_app_language():
    """Setup must know the names it cannot display itself."""
    generated = _generated_shortcut_names()
    expected = list(
        sm.app_shortcut_names_for_cleanup(i18n.shipped_catalog_codes())
    )
    assert generated == expected
    assert len(generated) == len({name.casefold() for name in generated})
    assert sm.APP_NAME in generated
    assert sm.DESKTOP_APP_NAME in generated
    welsh = i18n.text_for_language("app_name", "cy")
    assert welsh and welsh in generated
    assert "SP Flash Tool" not in generated
    assert sm.DESKTOP_APP_NAME_MEDIATEK not in generated


@pytest.mark.parametrize("lang", ["cy", "id", "az"])
def test_non_installer_language_shortcut_is_detected_in_an_owned_folder(
    tmp_path, monkeypatch, lang
):
    assert not any(lang in codes for codes in sm.INNO_SHORTCUT_LANGUAGES.values())
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    roots = _sandbox(tmp_path, monkeypatch)
    localized = i18n.text_for_language("app_name", lang)
    assert localized
    folder = roots["menu"][0] / sm.DESKTOP_APP_NAME
    folder.mkdir()
    (folder / f"{localized}.lnk").write_bytes(b"x")

    assert sm.get_state()[sm.APP_ID]["menu"] is True


def test_creating_a_shortcut_removes_an_old_language_variant(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    roots = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    old_name = i18n.text_for_language("app_name", "de")
    old = roots["desktop"][0] / f"{old_name}.lnk"
    old.write_bytes(b"x")
    monkeypatch.setattr(
        sm,
        "_lnk_targets",
        lambda files: {
            sm._path_key(old): r"C:\Program Files\UpdaterCE\InnioasisUpdater.exe"
        },
    )

    def write(target, shortcut_id):
        Path(target).write_bytes(b"lnk")

    monkeypatch.setattr(sm, "_write_windows_lnk", write)
    monkeypatch.setattr(sm, "_write_linux_desktop", write)

    assert sm.set_shortcut(sm.APP_ID, "desktop", True) is True
    assert not old.exists()
    assert sm.get_state()[sm.APP_ID]["desktop"] is True


def test_language_change_renames_an_existing_shortcut_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    roots = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    old_name = i18n.text_for_language("app_name", "de")
    new_name = i18n.text_for_language("app_name", "ru")
    old = roots["desktop"][0] / f"{old_name}.lnk"
    old.write_bytes(b"original shortcut")
    monkeypatch.setattr(
        sm,
        "_lnk_targets",
        lambda files: {
            sm._path_key(old): r"C:\Program Files\UpdaterCE\InnioasisUpdater.exe"
        },
    )

    sm.reconcile_shortcut_names()

    renamed = old.with_name(f"{new_name}.lnk")
    assert not old.exists()
    assert renamed.read_bytes() == b"original shortcut"
    assert sm.get_state()[sm.APP_ID]["desktop"] is True


def test_language_change_does_not_create_missing_shortcuts(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    roots = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")

    sm.reconcile_shortcut_names()

    for kind in ("desktop", "menu"):
        assert all(not list(root.iterdir()) for root in roots[kind])


def test_language_change_leaves_an_unrelated_shortcut_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    roots = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    other = roots["desktop"][0] / "Notepad.lnk"
    other.write_bytes(b"not ours")
    monkeypatch.setattr(
        sm,
        "_lnk_targets",
        lambda files: {sm._path_key(other): r"C:\Windows\notepad.exe"},
    )

    sm.reconcile_shortcut_names()

    assert other.exists()
    assert sm.get_state()[sm.APP_ID]["desktop"] is False


def test_language_change_updates_only_the_kind_that_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    roots = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    old_name = i18n.text_for_language("app_name", "de")
    new_name = i18n.text_for_language("app_name", "ru")
    folder = roots["menu"][0] / sm.DESKTOP_APP_NAME
    folder.mkdir()
    old = folder / f"{old_name}.lnk"
    old.write_bytes(b"menu shortcut")

    sm.reconcile_shortcut_names()

    assert not old.exists()
    assert (folder / f"{new_name}.lnk").read_bytes() == b"menu shortcut"
    assert all(not list(root.iterdir()) for root in roots["desktop"])


def test_disabling_removes_shortcuts_in_multiple_languages(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    roots = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "current_language", lambda: "ru")
    old_names = [
        i18n.text_for_language("app_name", "de"),
        i18n.text_for_language("app_name", "ru"),
    ]
    old = [roots["menu"][0] / f"{name}.lnk" for name in old_names]
    for path in old:
        path.write_bytes(b"x")
    monkeypatch.setattr(
        sm,
        "_lnk_targets",
        lambda files: {
            sm._path_key(path): r"C:\Program Files\UpdaterCE\InnioasisUpdater.exe"
            for path in old
        },
    )

    assert sm.set_shortcut(sm.APP_ID, "menu", False) is True
    assert all(not path.exists() for path in old)
    assert sm.get_state()[sm.APP_ID]["menu"] is False


def _installer_shortcut_names():
    """The [CustomMessages] shortcut names the Windows installer compiles.

    The per-language entries live in an include generated from the app's
    catalogs; Inno merges it into the section, so the test does too.
    """

    def _entries(text):
        values = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith((";", "#", "{")):
                continue
            key, separator, value = line.partition("=")
            if separator and key.endswith(
                ("AppShortcutName", "DesktopAppShortcutName")
            ):
                yield key, value.strip()

    text = (ROOT / "scripts" / "build_windows_installer.iss").read_text(
        encoding="utf-8-sig"
    )
    section = text.split("[CustomMessages]", 1)[1].split("[Files]", 1)[0]
    values = {}
    keys = []
    for key, value in _entries(section):
        keys.append(key)
        values[key] = value
    include = ROOT / "scripts" / "build_windows_installer_names.iss"
    assert include.is_file(), "run scripts/generate_windows_shortcut_names.py"
    for key, value in _entries(include.read_text(encoding="utf-8-sig")):
        keys.append(key)
        values[key] = value
    assert len(keys) == len(set(keys)), "duplicate shortcut custom message"
    assert "AppShortcutName" in values and "DesktopAppShortcutName" in values
    return values


def test_installer_custom_messages_include_has_only_valid_entries():
    text = (ROOT / "scripts" / "build_windows_installer_names.iss").read_text(
        encoding="utf-8-sig"
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    assert lines
    assert all(
        line.startswith(";") or line.startswith("#") or "=" in line
        for line in lines
    ), "[CustomMessages] allows ; comments, preprocessor guards and key=value entries only"


def _iss_section(text, header):
    """The body of a real Inno section, not a [Languages] mention in a comment."""
    match = re.search(rf"^\[{re.escape(header)}\]\s*$", text, re.MULTILINE)
    assert match, f"missing [{header}] section"
    rest = text[match.end() :]
    following = re.search(r"^\[[^\]]+\]\s*$", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def test_installer_writes_only_the_app_in_one_start_menu_folder():
    text = (ROOT / "scripts" / "build_windows_installer.iss").read_text(
        encoding="utf-8-sig"
    )
    icons = _iss_section(text, "Icons")
    entries = [
        line.strip()
        for line in icons.splitlines()
        if line.strip().startswith("Name:")
    ]
    menu = [
        line
        for line in entries
        if '"{group}' in line
        or '"{userprograms}' in line
        or '"{commonprograms}' in line
    ]

    assert menu
    assert all(line.startswith('Name: "{group}\\') for line in menu)
    assert all("{#MyAppExeName}" in line for line in menu)
    assert '"{userprograms}' not in icons
    assert '"{commonprograms}' not in icons
    assert not any(
        "SP Flash" in line or "SP_Flash_Tool" in line for line in entries
    )
    assert not any(
        "unins" in line.lower() or "uninstall" in line.lower() for line in entries
    )

    assert "DefaultGroupName={#MyAppGroup}" in text
    assert re.search(r'#define MyAppGroup "Innioasis Updater"', text)
    assert "PurgeLegacyMenuEntries;" in text


def test_installer_offers_exactly_the_shortcut_languages_we_map():
    """The Inno [Languages] list and the app's mapping must not drift apart."""
    text = (ROOT / "scripts" / "build_windows_installer.iss").read_text(
        encoding="utf-8-sig"
    )
    section = _iss_section(text, "Languages")
    offered = set(re.findall(r'^\s*Name:\s*"([^"]+)"', section, re.MULTILINE))

    assert offered == set(sm.INNO_SHORTCUT_LANGUAGES)


def test_installer_shortcut_names_match_the_app(monkeypatch):
    """Every Inno language must name the shortcut exactly as the app does."""
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    messages = _installer_shortcut_names()
    assert messages["AppShortcutName"] == sm.APP_NAME
    assert messages["DesktopAppShortcutName"] == sm.DESKTOP_APP_NAME

    for inno_name, codes in sm.INNO_SHORTCUT_LANGUAGES.items():
        localized = next(
            (name for name in map(sm._localized_creation_name, codes) if name), ""
        )
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
