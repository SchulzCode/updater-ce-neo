"""The Windows-on-ARM package must ship an ARM64 libusb, not the x64 helper.

``libusb-1.0.dll`` is loaded into the application's own process, so an ARM64
build cannot use the x64 copy that mirrors the shipped Chinese build. The ARM64
DLL under ``vendor/mtkclient/mtkclient/Windows/arm64`` is this repository's
addition (its README records the upstream release); these tests pin both the
file's machine type and the order the runtime searches in.
"""

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_DIR = ROOT / "vendor" / "mtkclient" / "mtkclient" / "Windows"
ARM64_DLL = WINDOWS_DIR / "arm64" / "libusb-1.0.dll"
X64_DLL = WINDOWS_DIR / "libusb-1.0.dll"

IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_FILE_MACHINE_ARM64 = 0xAA64


def _pe_machine(path: Path) -> int:
    data = path.read_bytes()
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    return struct.unpack_from("<H", data, pe_offset + 4)[0]


def test_the_added_libusb_is_a_real_arm64_binary():
    assert ARM64_DLL.is_file(), ARM64_DLL
    assert _pe_machine(ARM64_DLL) == IMAGE_FILE_MACHINE_ARM64


def test_the_mirrored_libusb_is_still_x64():
    assert _pe_machine(X64_DLL) == IMAGE_FILE_MACHINE_AMD64


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows DLL search order")
def test_windows_on_arm_prefers_the_arm64_libusb(tmp_path, monkeypatch):
    from src import mtk_api, paths

    class _Platform:
        def __init__(self, machine):
            self._machine = machine

        def machine(self):
            return self._machine

    vendor = tmp_path / "vendor" / "mtkclient"
    arm64 = vendor / "mtkclient" / "Windows" / "arm64" / "libusb-1.0.dll"
    x64 = vendor / "mtkclient" / "Windows" / "libusb-1.0.dll"
    for dll in (arm64, x64):
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(b"")

    # Nothing in the working directory or the bundle root may win first.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "MTKCLIENT_DIR", vendor)
    monkeypatch.setattr(paths, "BUNDLE_DIR", tmp_path / "bundle")

    monkeypatch.setattr(mtk_api, "platform", _Platform("ARM64"))
    assert Path(mtk_api.find_cross_platform_libusb()).resolve() == arm64.resolve()

    monkeypatch.setattr(mtk_api, "platform", _Platform("AMD64"))
    assert Path(mtk_api.find_cross_platform_libusb()).resolve() == x64.resolve()
