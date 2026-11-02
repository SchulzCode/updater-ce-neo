"""Windows CPU-based install method lock: ARM64 gets MTKClient only, x86-64 gets SP only."""

from unittest.mock import patch

import src.flash_service as fs


def _windows(hardware_arm64: bool, machine: str):
    return (
        patch("src.paths.IS_WINDOWS", True),
        patch("src.paths.IS_MAC", False),
        patch("src.flash_service._windows_hardware_is_arm64", return_value=hardware_arm64),
        patch("platform.machine", return_value=machine),
    )


def _enter(patches):
    for p in patches:
        p.start()
    return patches


def _exit(patches):
    for p in patches:
        p.stop()


def test_x86_64_windows_locked_to_sp_flash_tool():
    patches = _enter(list(_windows(False, "AMD64")))
    try:
        assert fs.windows_only_method() == fs.METHOD_SP
        assert fs.default_flash_method() == fs.METHOD_SP
        assert fs.normalise_method("mtk") == fs.METHOD_SP
    finally:
        _exit(patches)


def test_arm64_windows_locked_to_mtk_even_when_python_is_x86_64():
    # x86-64 build under Prism: platform.machine() says AMD64, hardware is ARM64.
    patches = _enter(list(_windows(True, "AMD64")))
    try:
        assert fs.windows_only_method() == fs.METHOD_MTK
        assert fs.default_flash_method() == fs.METHOD_MTK
        assert fs.normalise_method("sp") == fs.METHOD_MTK
    finally:
        _exit(patches)
