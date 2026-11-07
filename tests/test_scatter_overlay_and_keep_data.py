"""Tests for solid scatter parsing, overlay packages (Y2Linux/DebianY2), and Keep Data."""

from pathlib import Path
import pytest

from src import flash_service as fs
from src import mtk_legacy_layout as mtk_layout
from src import sp_console_config

DEBIAN_SCATTER = Path("/Users/ryan/Downloads/debiany2/MT6582_Android_scatter.txt")
Y2_STOCK_SCATTER = Path("assets/compat/MT6582_Android_scatter.txt")
Y1_STOCK_SCATTER = Path("assets/compat/MT6572_Android_scatter.txt")

requires_debian_fixture = pytest.mark.skipif(
    not DEBIAN_SCATTER.is_file(),
    reason="the optional DebianY2 package is not present on this machine",
)


@requires_debian_fixture
def test_is_overlay_package():
    assert fs.is_overlay_package(DEBIAN_SCATTER) is True
    assert fs.is_overlay_package(Y2_STOCK_SCATTER) is False
    assert fs.is_overlay_package(Y1_STOCK_SCATTER) is False


@requires_debian_fixture
def test_overlay_scatter_bias_is_zero():
    bias = fs._detect_legacy_user_addr_bias(DEBIAN_SCATTER)
    assert bias == 0


def test_stock_scatter_bias_maintained():
    bias = fs._detect_legacy_user_addr_bias(Y2_STOCK_SCATTER)
    assert bias == fs.LEGACY_MBR_USER_ADDR_BIAS


@requires_debian_fixture
def test_debiany2_write_plan_addresses():
    overrides = fs._y2linux_manifest_addresses(DEBIAN_SCATTER)
    plan = fs.build_mtk_write_plan(
        DEBIAN_SCATTER,
        model="Y2",
        preloader_first=False,
        skip_data_partitions=False,
        address_overrides=overrides,
    )
    by_name = {step["name"].upper(): step for step in plan}
    assert "BOOTIMG" in by_name
    assert "RECOVERY" in by_name
    assert "ANDROID" in by_name
    assert "USRDATA" in by_name

    assert by_name["BOOTIMG"]["offset"] == 0x1D80000
    assert by_name["RECOVERY"]["offset"] == 0x2D80000
    assert by_name["ANDROID"]["offset"] == 0x5180000
    assert by_name["USRDATA"]["offset"] == 0x40380000


@requires_debian_fixture
def test_debiany2_keep_data_excludes_userdata():
    overrides = fs._y2linux_manifest_addresses(DEBIAN_SCATTER)
    plan = fs.build_mtk_write_plan(
        DEBIAN_SCATTER,
        model="Y2",
        preloader_first=False,
        skip_data_partitions=True,
        address_overrides=overrides,
    )
    by_name = {step["name"].upper(): step for step in plan}
    assert "BOOTIMG" in by_name
    assert "RECOVERY" in by_name
    assert "ANDROID" in by_name
    assert "USRDATA" not in by_name


def test_runtime_reset_partitions_does_not_contain_critical_security_or_calibration():
    critical = {"seccfg", "protect_f", "protect_s", "protect1", "protect2", "nvdata", "nvram"}
    assert not (mtk_layout.RUNTIME_RESET_PARTITIONS & critical)


def test_sp_flash_tool_console_args_uses_download_only():
    args = fs.sp_flash_tool_console_args("dummy_scatter.txt", "dummy_da.bin")
    assert "-c" in args
    idx = args.index("-c")
    assert args[idx + 1] == "download"
    assert "format-download" not in args


def test_sp_console_config_xml_uses_download_only(tmp_path):
    scatter = tmp_path / "scatter.txt"
    scatter.write_text("platform: MT6582\nstorage: EMMC\n", encoding="utf-8")
    xml = sp_console_config.build_console_config_xml(scatter, "DA.bin")
    assert "<download-only/>" in xml
    assert "format-download" not in xml
