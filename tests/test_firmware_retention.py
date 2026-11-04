"""A retry must never find the firmware gone.

Extraction used to delete the archive it came from, so a retry, the automatic
switch to the other engine, or a pruned cache left the install with nothing to
extract and surfaced ``CACHE_FILE_MISSING`` mid-install. These tests pin the
new rule: the archive stays, and when it is missing the files already on disk
are what gets flashed.
"""

import zipfile
from pathlib import Path

import pytest

from src import config, downloads
from src.flash_service import ExtractWorker
from src.fw_package import read_fw_package_ini

SCATTER = """\
- general: MTK_PLATFORM_CFG
  info:
    - config_version: V1.1.1
      platform: MT6582
      project: y2_baseline
      storage: EMMC
      boot_channel: MSDC_0
      block_size: 0x20000

- partition_index: SYS0
  partition_name: boot
  file_name: boot.img
  is_download: true
  type: SV5_BL_BIN
  linear_start_addr: 0x0
  physical_start_addr: 0x0
  partition_size: 0x1000
  region: EMMC_USER

- partition_index: SYS1
  partition_name: userdata
  file_name: NONE
  is_download: true
  type: NORMAL_ROM
  linear_start_addr: 0x1000
  physical_start_addr: 0x1000
  partition_size: 0x2000
  region: EMMC_USER
"""


def _package(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MT6582_Android_scatter.txt").write_text(SCATTER, encoding="utf-8")
    (folder / "boot.img").write_bytes(b"\0" * 64)
    archive = folder.parent / "rom_y2.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in folder.iterdir():
            zf.write(item, item.name)
    return archive


@pytest.fixture()
def downloads_root(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path)
    return tmp_path


def test_extraction_keeps_the_archive_for_a_retry(downloads_root):
    archive = _package(downloads_root / "src")
    extracted = Path(ExtractWorker(str(archive))._extract(str(archive)))

    assert (extracted / "boot.img").is_file()
    assert archive.is_file(), "the archive is the retry's only copy of the firmware"


def test_a_missing_archive_uses_the_files_still_on_disk(downloads_root):
    archive = _package(downloads_root / "src")
    first = Path(ExtractWorker(str(archive))._extract(str(archive)))
    archive.unlink()

    again = Path(ExtractWorker(str(archive))._extract(str(archive)))

    assert again == first
    assert (again / "boot.img").is_file(), "a retry flashes what is on disk"


def test_a_missing_archive_with_nothing_on_disk_is_cache_missing(downloads_root):
    archive = _package(downloads_root / "src")
    archive.unlink()

    with pytest.raises(RuntimeError, match="CACHE_FILE_MISSING"):
        ExtractWorker(str(archive))._extract(str(archive))


def test_model_naming_rules_ignore_packaging_and_repo_names():
    # MX-prefixed zips are the manufacturer's packaging of the same names.
    assert config.detect_model_and_type_from_name("MX_rom_type_b.zip") == ("Y1", "B")
    assert config.detect_model_and_type_from_name("MX-rom_a5.zip") == ("A5", None)
    # rom.zip is the stock Y1 image; rom_type_b is its pre-2.0 revision.
    assert config.detect_model_and_type_from_name("rom.zip") == ("Y1", "A")
    assert config.detect_model_and_type_from_name("rom_type_b.zip") == ("Y1", "B")
    # A repository name is not a model identifier.
    assert config.detect_model_and_type_from_name("y1-community/rockbox-3.0.zip") == ("", None)


def test_fw_package_ini_lists_rom_derivatives(tmp_path):
    ini = tmp_path / "fw_package.ini"
    ini.write_text(
        "[fw_package]\n"
        "software_name = Rockbox\n"
        "target_model = Y1\n"
        "derivatives = Original Software, Rockbox\n",
        encoding="utf-8",
    )

    assert read_fw_package_ini(ini).derivatives == ("Original Software", "Rockbox")


def test_mtk_failures_are_routed_to_the_power_cycle_step():
    from src.ui.main_window import _is_mtk_install_failure

    assert _is_mtk_install_failure("MTK_INIT_FAILED")
    assert _is_mtk_install_failure("MTK_INSTALL_FAILED")
    assert _is_mtk_install_failure("WRITE_FAILED")
    # Engine-agnostic failures keep their own cards.
    assert not _is_mtk_install_failure("S_DA_INIT_SYNC_ERROR (1093)")
    assert not _is_mtk_install_failure("CACHE_FILE_MISSING")


def test_manifest_entries_bring_new_models_without_code_changes():
    """A hosted release for another model is listed from slidia_manifest.xml alone.

    The app must be ready to serve Timmkoo/Innioasis firmware for models it has
    no built-in catalogue row for (G5, Q8, D1, ...) as soon as the manifest
    names one: the model becomes selectable and its package is a normal row.
    """
    from src import catalog, manifest

    xml = (
        '<?xml version="1.0"?>'
        "<catalog>"
        '<package name="Stock" repo="innioasis/g5-stock" device="G5" '
        'url="https://example.com/g5-stock.zip" tag="v1.0.0" '
        'type="img" handler="Custom Firmware" />'
        "</catalog>"
    )
    original = list(catalog.LIVE_CATALOG)
    try:
        entries = manifest.parse_manifest_xml(xml)
        assert len(entries) == 1
        catalog.set_live_catalog(entries)
        assert "G5" in catalog.available_models()
        rows = catalog.packages_for_model("G5")
        assert rows and rows[0].name == "Stock"
    finally:
        catalog.set_live_catalog(original)
