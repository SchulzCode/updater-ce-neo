"""Dropped firmware is accepted only for zip, rar, scatter files, and scatter folders."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.drop_install import (
    argument_path,
    classify_drop,
    external_package_from_arguments,
    first_accepted_drop,
    package_check_error,
)
from src.i18n import _STRINGS


def test_accepted_extensions_and_scatter_folders():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "rom.zip"
        archive.write_bytes(b"PK")
        rar = root / "rom.rar"
        rar.write_bytes(b"Rar")
        other = root / "notes.txt"
        other.write_text("hello", encoding="utf-8")
        loose = root / "loose"
        loose.mkdir()
        scatter = loose / "MT6572_Android_scatter.txt"
        scatter.write_text("file_name: boot.img\n", encoding="utf-8")
        empty = root / "empty_dir"
        empty.mkdir()
        packed = root / "firmware"
        packed.mkdir()
        (packed / "MT6582_Android_scatter.txt").write_text(
            "file_name: system.img\n", encoding="utf-8"
        )

        assert classify_drop(archive) == "archive"
        assert classify_drop(rar) == "archive"
        assert classify_drop(scatter) == "scatter"
        assert classify_drop(other) is None
        assert classify_drop(empty) is None
        assert classify_drop(packed) == "folder"
        assert first_accepted_drop([other, archive]) == archive
        assert first_accepted_drop([other]) is None

        assert package_check_error(loose) == "missing_images"
        (loose / "boot.img").write_bytes(b"img")
        assert package_check_error(loose) == ""
        assert package_check_error(packed) == "missing_images"
        (packed / "system.img").write_bytes(b"img")
        assert package_check_error(packed) == ""


def test_dropped_package_takes_the_android_scatter_over_its_variants():
    """A dropped zip/folder is accepted like a browsed one: the stock scatter wins."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pkg = root / "pkg"
        pkg.mkdir()
        (pkg / "MT6582_Android_scatter.txt").write_text(
            "file_name: system.img\n", encoding="utf-8"
        )
        (pkg / "MT6582_Android_scatter_preserve_data.txt").write_text(
            "file_name: userdata.img\n", encoding="utf-8"
        )
        (pkg / "rom_scatter.txt").write_text("file_name: absent.img\n", encoding="utf-8")
        (pkg / "system.img").write_bytes(b"img")

        # Only the stock scatter and the images it lists are checked; the
        # preserve variant and the stray scatter are ignored.
        assert package_check_error(pkg) == ""

        # Two models in one drop is still not one package.
        (pkg / "MT6572_Android_scatter.txt").write_text(
            "file_name: system.img\n", encoding="utf-8"
        )
        assert package_check_error(pkg) == "missing_scatter"


def test_drop_overlay_string_in_six_locales():
    table = _STRINGS["drop_firmware_here"]
    assert table["en"] == "Drop firmware file here to install"
    for lang in ("en", "zh-CN", "fr", "es", "de", "ja"):
        text = table.get(lang) or ""
        assert text.strip()
        assert text != "drop_firmware_here"


def test_drop_check_message_says_please_wait():
    assert "Please wait" in _STRINGS["drop_checking"]["en"]


def test_icon_drop_arguments_accept_plain_paths_and_file_urls():
    """A taskbar/shortcut drop can arrive as a path or a %U file URL."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "firmware.zip"
        archive.write_bytes(b"PK")
        notes = root / "notes.txt"
        notes.write_text("not firmware", encoding="utf-8")

        assert external_package_from_arguments(["--tray", str(notes), str(archive)]) == archive
        assert external_package_from_arguments([archive.as_uri()]) == archive
        assert external_package_from_arguments(["--tray"]) is None
        assert argument_path(archive.as_uri()) == archive


if __name__ == "__main__":
    test_accepted_extensions_and_scatter_folders()
    test_drop_overlay_string_in_six_locales()
    print("drop install tests passed")
