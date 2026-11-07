"""Packages whose layout differs from a stock release.

Y2Linux-style releases ship more than one scatter, and portable packages also
carry their own Download Agent (the tool's ``MTK_AllInOne_DA.bin`` stays the one
every install uses). The install flow must follow the package's files rather
than a fixed partition list, and must say what a 6104 rejection means.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import flash_service as fs
from src.i18n import tr


def _block(index, name, file_name, download, linear, physical, size, region="EMMC_USER"):
    return (
        f"- partition_index: SYS{index}\n"
        f"  partition_name: {name}\n"
        f"  file_name: {file_name}\n"
        f"  is_download: {'true' if download else 'false'}\n"
        "  type: NORMAL_ROM\n"
        f"  linear_start_addr: 0x{linear:X}\n"
        f"  physical_start_addr: 0x{physical:X}\n"
        f"  partition_size: 0x{size:X}\n"
        f"  region: {region}\n"
        "  storage: HW_STORAGE_EMMC\n"
        "  boundary_check: true\n"
        "  is_reserved: false\n"
        "  operation_type: UPDATE\n"
        "  reserve: 0x00\n\n"
    )


def _header():
    return (
        "- general: MTK_PLATFORM_CFG\n"
        "  info:\n"
        "    - config_version: V1.1.1\n"
        "      platform: MT6582\n"
        "      project: eastaeon82_wet_kk\n"
        "      storage: EMMC\n"
        "      block_size: 0x20000\n\n"
    )


def _scatter(*rows):
    return _header() + "".join(
        _block(i, name, file_name, download, linear, physical, size, region)
        for i, (name, file_name, download, linear, physical, size, region) in enumerate(rows)
    )


BOOTIMG = ("BOOTIMG", "BOOTIMG.img", True, 0x3180000, 0x1D80000, 0x1000000, "EMMC_USER")
RECOVERY = ("RECOVERY", "RECOVERY.img", True, 0x4280000, 0x2D80000, 0x1000000, "EMMC_USER")
ANDROID = ("ANDROID", "Y2ROOT.img", True, 0x6580000, 0x5180000, 0x33400000, "EMMC_USER")
USRDATA = ("USRDATA", "Y2DATA.img", True, 0x41780000, 0x40380000, 0x32000000, "EMMC_USER")
PRELOADER = ("PRELOADER", "NONE", False, 0x0, 0x0, 0x1400000, "EMMC_BOOT_1")
PRELOADER_DL = ("PRELOADER", "preloader.bin", True, 0x0, 0x0, 0x1400000, "EMMC_BOOT_1")
MBR = ("MBR", "NONE", False, 0xB80000, 0xB80000, 0x80000, "EMMC_USER")
EBR1 = ("EBR1", "NONE", False, 0xC00000, 0xC00000, 0x80000, "EMMC_USER")


def _write_images(folder: Path, *names):
    for name in names:
        (folder / name).write_bytes(b"\x01" * 64)


def _overlay_package(tmp_path: Path):
    """A Y2Linux/Reborn-shaped package with the manifest's absolute map."""
    scatter = tmp_path / "MT6582_Android_scatter.txt"
    scatter.write_text(
        _scatter(PRELOADER, MBR, EBR1, BOOTIMG, RECOVERY, ANDROID, USRDATA),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "org.schulzcode.y2linux.release.1",
                "payloads": [
                    {"target_partition": "BOOTIMG", "absolute_start_bytes": 0x1D80000},
                    {"target_partition": "ANDROID", "absolute_start_bytes": 0x5180000},
                    {"target_partition": "USRDATA", "absolute_start_bytes": 0x40380000},
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_images(
        tmp_path,
        "BOOTIMG.img",
        "RECOVERY.img",
        "Y2ROOT.img",
        "Y2DATA.img",
        "preloader.bin",
    )
    return scatter


def _package(tmp_path: Path, preserve_rows, preserve_name="MT6582_preserve_data_scatter.txt"):
    (tmp_path / "MT6582_Android_scatter.txt").write_text(
        _scatter(PRELOADER, BOOTIMG, ANDROID, USRDATA), encoding="utf-8"
    )
    (tmp_path / preserve_name).write_text(
        _scatter(PRELOADER, *preserve_rows), encoding="utf-8"
    )
    _write_images(tmp_path, "BOOTIMG.img", "Y2ROOT.img", "Y2DATA.img")
    return tmp_path / "MT6582_Android_scatter.txt"


def test_overlay_package_is_detected_by_its_manifest_and_missing_foundations(tmp_path):
    scatter = _overlay_package(tmp_path)

    assert fs.is_overlay_package(scatter) is True


def test_a_partial_stock_update_with_a_foundation_is_not_an_overlay(tmp_path):
    scatter = tmp_path / "MT6582_Android_scatter.txt"
    scatter.write_text(_scatter(PRELOADER_DL, BOOTIMG), encoding="utf-8")

    assert fs.is_overlay_package(scatter) is False


def test_overlay_package_skips_the_legacy_mbr_probe(tmp_path):
    """The probe must not run before Y2Linux's own install map is used."""
    scatter = _overlay_package(tmp_path)
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None
    worker._is_legacy_mode = lambda _mtk: True

    def fail_probe(*_args, **_kwargs):
        raise AssertionError("overlay package issued a legacy MBR probe")

    worker._read_user_blob = fail_probe
    current = worker._configure_scatter_user_addressing(scatter)

    result = worker._auto_configure_scatter_user_addressing(
        SimpleNamespace(), scatter, current
    )

    assert result == current


def test_overlay_manifest_payload_without_an_address_fails_closed(tmp_path):
    scatter = _overlay_package(tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "org.schulzcode.y2linux.release.1",
                "payloads": [{"target_partition": "BOOTIMG"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(fs.PackageLayoutError, match="absolute_start_bytes"):
        fs._y2linux_manifest_addresses(scatter)


def test_overlay_manifest_addresses_drive_the_write_plan(tmp_path):
    scatter = _overlay_package(tmp_path)

    overrides = fs._y2linux_manifest_addresses(scatter)
    plan = fs.build_mtk_write_plan(
        scatter,
        model="Y2",
        preloader_first=False,
        skip_data_partitions=False,
        address_overrides=overrides,
    )
    by_name = {step["name"].upper(): step for step in plan}

    assert by_name["BOOTIMG"]["offset"] == 0x1D80000
    assert by_name["RECOVERY"]["offset"] == 0x2D80000
    assert by_name["ANDROID"]["offset"] == 0x5180000
    assert by_name["USRDATA"]["offset"] == 0x40380000


def test_preserve_scatter_is_the_packages_own_exclusion_list(tmp_path):
    scatter = _package(tmp_path, (BOOTIMG, ANDROID))

    preserve = fs.find_preserve_data_scatter(scatter)

    assert preserve is not None and preserve.name == "MT6582_preserve_data_scatter.txt"
    assert fs.downloaded_partition_names(scatter) == ["BOOTIMG", "ANDROID", "USRDATA"]
    assert fs.downloaded_partition_names(preserve) == ["BOOTIMG", "ANDROID"]

    plan = fs.build_mtk_write_plan(
        scatter, skip_partition_names={"USRDATA"}
    )
    assert {step["name"] for step in plan} == {"BOOTIMG", "ANDROID"}


def test_preserve_scatter_that_adds_a_partition_is_ignored(tmp_path):
    extra = ("SEC_RO", "secro.img", True, 0x5180000, 0x3D80000, 0x1000000, "EMMC_USER")
    scatter = _package(tmp_path, (BOOTIMG, ANDROID, extra))

    assert fs.find_preserve_data_scatter(scatter) is None


def test_the_tools_own_download_agent_is_always_the_one_used(tmp_path):
    scatter = _package(tmp_path, (BOOTIMG, ANDROID))
    (tmp_path / "MTK_AllInOne_DA.bin").write_bytes(b"\x00" * 32)

    tool = tmp_path / "SP_Flash_Tool"
    tool.mkdir()
    tool_da = tool / "MTK_AllInOne_DA.bin"
    tool_da.write_bytes(b"\x01" * 32)

    worker = fs.FlashWorker("pkg.zip", method="sp")
    assert worker._resolve_da_file(tool, scatter) == tool_da
    assert not hasattr(fs, "_package_download_agent")


def test_6104_is_reported_as_a_secure_da_rejection():
    name = "S_SECURITY_SECURE_USB_DL_DA_RETURN_INVALID_TYPE"
    assert fs.SP_ERROR_CODE_NAMES["6104"] == name
    assert fs.SP_ERROR_GUIDANCE[name] == "err_sp_da_invalid_type"
    guidance = tr("err_sp_da_invalid_type")
    assert guidance and guidance != "err_sp_da_invalid_type"
