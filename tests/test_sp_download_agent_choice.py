"""Every SP Flash Tool install uses the tool's own MTK_AllInOne_DA.bin.

A Download Agent is built against a specific flash_tool, and the copy beside
``flash_tool.exe`` is the matched half of that pair. Firmware packages also
carry a DA (the one the ROM was dumped with); handing that to the bundled tool
was refused outright -- "Error: lib DA NOT match!" -- before it searched USB, so
a package's copy is never used. A DA chosen in the MediaTek Installer is the
only exception.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import flash_service as fs  # noqa: E402
from src import terminal_install  # noqa: E402

SCATTER = (
    "- general: MTK_PLATFORM_CFG\n"
    "  info:\n"
    "    - config_version: V1.1.1\n"
    "      platform: MT6572\n"
    "      project: test\n"
    "      storage: EMMC\n"
    "      block_size: 0x20000\n\n"
    "- partition_index: SYS0\n"
    "  partition_name: PRELOADER\n"
    "  file_name: preloader.bin\n"
    "  is_download: true\n"
    "  type: NORMAL_ROM\n"
    "  linear_start_addr: 0x0\n"
    "  physical_start_addr: 0x0\n"
    "  partition_size: 0x1000\n"
    "  region: EMMC_BOOT_1\n"
    "  storage: HW_STORAGE_EMMC\n"
    "  boundary_check: true\n"
    "  is_reserved: false\n"
    "  operation_type: UPDATE\n"
    "  reserve: 0x00\n\n"
)


def _package(tmp_path, *, with_tool_da=True):
    """A firmware folder with its own DA, and a tool folder with (or without) one."""
    fw = tmp_path / "fw"
    fw.mkdir()
    scatter = fw / "MT6572_Android_scatter.txt"
    scatter.write_text(SCATTER, encoding="utf-8")
    (fw / "preloader.bin").write_bytes(b"\x01" * 64)
    package_da = fw / "MTK_AllInOne_DA.bin"
    package_da.write_bytes(b"\x02" * 512)

    tool = tmp_path / "SP_Flash_Tool"
    tool.mkdir()
    (tool / "flash_tool.exe").write_bytes(b"MZ")
    tool_da = tool / "MTK_AllInOne_DA.bin"
    if with_tool_da:
        tool_da.write_bytes(b"\x03" * 512)
    return scatter, package_da, tool, tool_da


def _worker(monkeypatch, tool, **kwargs):
    worker = fs.FlashWorker(str(tool.parent / "package.zip"), method="sp", model="Y1", **kwargs)
    worker._resolve_auth_file = lambda: ""
    monkeypatch.setattr(fs.paths, "find_sp_flash_tool", lambda: tool)
    monkeypatch.setattr(fs, "IS_WINDOWS", True)
    monkeypatch.setattr(fs, "_to_short_path", lambda path: str(path))
    monkeypatch.setattr(fs, "align_scatter_disk_images", lambda *args, **kwargs: None)
    return worker


def _drive(worker, monkeypatch, *, log_result=(True, "")):
    """Run the flow with a stubbed console search; return the DAs it was given."""
    das = []
    real_args = fs.sp_flash_tool_console_args

    def spy(scatter_arg, da_arg, auth_file="", log=None):
        das.append(Path(da_arg))
        return real_args(scatter_arg, da_arg, auth_file, log)

    monkeypatch.setattr(fs, "sp_flash_tool_console_args", spy)
    monkeypatch.setattr(fs.FlashWorker, "_sp_console_search", lambda self, spawn, log_root: (0, 0.0))
    monkeypatch.setattr(
        fs.FlashWorker, "_read_sp_log_result", lambda self, launch_time, log_root: log_result
    )
    return das


def test_the_tool_own_da_is_used_even_when_the_package_ships_one(tmp_path, monkeypatch):
    scatter, _package_da, tool, tool_da = _package(tmp_path)
    worker = _worker(monkeypatch, tool)
    das = _drive(worker, monkeypatch)
    finished = []
    worker.finished.connect(lambda ok, code: finished.append((ok, code)))

    worker._flash_via_sp_flash_tool(scatter)

    assert das == [tool_da], "the package's own DA must not be handed to the tool"
    assert finished == [(True, "")]


def test_no_package_da_helper_is_left_to_be_picked_up(tmp_path, monkeypatch):
    scatter, _package_da, tool, tool_da = _package(tmp_path)
    worker = _worker(monkeypatch, tool)

    assert not hasattr(fs, "_package_download_agent")
    assert worker._resolve_da_file(tool, scatter) == tool_da


def test_a_user_chosen_da_still_wins(tmp_path, monkeypatch):
    scatter, package_da, tool, _tool_da = _package(tmp_path)
    worker = _worker(monkeypatch, tool, da_file=str(package_da))
    das = _drive(worker, monkeypatch)

    worker._flash_via_sp_flash_tool(scatter)

    assert das == [package_da], "an explicitly chosen DA is the user's decision"


def test_a_tool_folder_without_a_da_is_not_patched_from_the_package(tmp_path, monkeypatch):
    scatter, package_da, tool, tool_da = _package(tmp_path)
    tool_da.unlink()
    worker = _worker(monkeypatch, tool)
    das = _drive(worker, monkeypatch)

    worker._flash_via_sp_flash_tool(scatter)

    assert das == [tool_da], "the tool's own path is still the only DA"
    assert package_da not in das


def test_the_terminal_command_uses_the_tools_da(tmp_path):
    scatter, package_da, tool, tool_da = _package(tmp_path)

    cmd = terminal_install.sp_flash_tool_command(scatter, sp_dir=tool)

    assert cmd is not None
    assert cmd[cmd.index("-d") + 1] == str(tool_da)
    assert str(package_da) not in cmd
