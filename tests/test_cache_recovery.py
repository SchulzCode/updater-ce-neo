"""Cache recovery: a firmware whose files went missing is fetched again.

A missing image inside the extracted package used to surface as
CACHE_FILE_MISSING on the install card, while the retry kept reusing the same
"complete" extract. These tests pin the three rules that make the recovery
work: a cached extract is only complete while its images are present, pruning
never deletes a declared image over letter case, and a purge touches only the
release being reinstalled.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import downloads
from src.catalog import FirmwarePackage
from src.flash_service import EXTRACT_COMPLETE_MARKER, _is_extract_complete
from src.fw_package import prune_to_firmware_package
from src.ui.select_page import SelectPackagePage

SCATTER = """\
- partition_index: SYS0
  partition_name: PRELOADER
  file_name: preloader_k47.bin
  is_download: true
- partition_index: SYS1
  partition_name: BOOTIMG
  file_name: boot.img
  is_download: true
"""


def _cached_package(tmp_path, *, with_images=True):
    folder = tmp_path / ".rom_extracted"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MT6582_Android_scatter.txt").write_text(SCATTER, encoding="utf-8")
    if with_images:
        (folder / "preloader_k47.bin").write_bytes(b"preloader")
        (folder / "boot.img").write_bytes(b"boot")
    (folder / EXTRACT_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    return folder


def test_a_marked_extract_missing_an_image_is_not_reusable(tmp_path):
    folder = _cached_package(tmp_path, with_images=False)
    assert _is_extract_complete(folder) is False

    (folder / "preloader_k47.bin").write_bytes(b"preloader")
    (folder / "boot.img").write_bytes(b"boot")
    assert _is_extract_complete(folder) is True


def test_pruning_keeps_an_image_whose_name_differs_only_in_case(tmp_path):
    folder = _cached_package(tmp_path)
    # The player's storage is case-insensitive; a Linux host is not, so the
    # scatter's lowercase name must not delete the uppercase image.
    (folder / "PRELOADER_K47.BIN").write_bytes(b"preloader")
    (folder / "notes.txt").write_text("extra", encoding="utf-8")

    prune_to_firmware_package(folder)

    assert (folder / "PRELOADER_K47.BIN").exists()
    assert not (folder / "notes.txt").exists()


def test_a_cache_failure_downloads_the_package_before_reporting_it():
    """The install card is not shown while the release is fetched again."""
    from src.ui.main_window import MainWindow

    prompts = []
    logs = []

    class _Page:
        _current_selected_rel = {
            "tag_name": "v1",
            "download_url": "https://example.test/rom.zip",
        }

        def retry_missing_cached_firmware(self, *, prompt=True):
            prompts.append(prompt)

    class _Window:
        _package_path = "/tmp/downloads/rondo_v1_rom.zip"
        _cache_recovery_needed = False
        _cache_recovered_package = ""
        _select_page = _Page()

        def _append_log(self, text):
            logs.append(text)

        def _on_progress_retry(self, *, prompt=True):
            prompts.append(prompt)

    window = _Window()
    assert MainWindow._recover_missing_firmware(window, "FileNotFoundError") is True
    assert prompts == [False], "a run under way must not prompt again"
    assert any("downloading the package" in line for line in logs)

    # Once per package: a second failure after a fresh download is real.
    assert MainWindow._recover_missing_firmware(window, "FileNotFoundError") is False


def test_a_local_firmware_is_not_fetched_over_the_network():
    from src.ui.main_window import MainWindow

    class _Page:
        _current_selected_rel = None

    class _Window:
        _package_path = "/tmp/local/rom.zip"
        _cache_recovered_package = ""
        _select_page = _Page()

        def _append_log(self, text):
            pass

    assert MainWindow._recover_missing_firmware(_Window(), "missing") is False


def test_purging_one_release_keeps_another_release_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path)
    package = FirmwarePackage(
        slug="rondo",
        name="Rondo",
        model="Y1",
        repo="jarvissax/rondo",
        package_name="rom.zip",
    )
    archive = tmp_path / "rondo_v1_rom.zip"
    archive.write_bytes(b"zip")
    extract = tmp_path / ".rondo_v1_rom_extracted"
    extract.mkdir()
    (extract / "boot.img").write_bytes(b"boot")
    other = tmp_path / "solar_v1_rom.zip"
    other.write_bytes(b"zip")

    class _Page:
        _current_selected_rel = None
        _details_package = None
        _release_cache_paths = SelectPackagePage._release_cache_paths
        purge_release_cache = SelectPackagePage.purge_release_cache

        def current_package(self):
            return None

    removed = _Page().purge_release_cache(
        {"tag_name": "v1", "asset_name": "rom.zip"}, package=package
    )

    assert not archive.exists()
    assert not extract.exists()
    assert other.exists(), "another release's firmware must be left alone"
    assert len(removed) == 2
