"""Tests for the MTKClient Checksum: stage UX and 2-minute stall failure handling.

1. When a line beginning with "Checksum:" is received during MTKClient flashing:
   - The app displays "Getting ready..." where percentage is displayed.
   - Percentage is hidden on the progress bar and shown as text ("Getting ready...") near it.
   - When progress moves past this stage, normal percentage is restored.
2. If it gets stuck on a line beginning with "Checksum:" for 2 minutes:
   - Install is declared a failure ("MTK_CHECKSUM_STALLED").
   - If SP Flash Tool is supported for the OS/arch (and not already tried):
     Retries in SP Flash Tool via the power-off recovery stage.
   - For users with no SP Flash Tool support for their OS/arch:
     Suggests trying Innioasis Updater CE on a Windows or Linux computer where
     the SP Flash Tool engine can recover the player.
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.flash_service import (
    FlashWorker,
    _MtkOutputStallDetector,
    is_mtk_checksum_line,
    is_sp_flash_tool_supported_for_host,
    parse_flash_error,
    METHOD_SP,
    METHOD_MTK,
)
from src.i18n import tr
from src.ui.flash_page import FlashPage
from src.ui.widgets import InstallProgress


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_is_mtk_checksum_line_matching():
    """Verify various forms of Checksum lines match and other lines do not."""
    assert is_mtk_checksum_line("Checksum: 440C") is True
    assert is_mtk_checksum_line("checksum: abcd") is True
    assert is_mtk_checksum_line("  Checksum: 0000  ") is True
    assert is_mtk_checksum_line("[LOG] Checksum: 440C") is True
    assert is_mtk_checksum_line("[MTK] Checksum: 1234") is True

    # Other lines must not match
    assert is_mtk_checksum_line("Progress: 12.0%") is False
    assert is_mtk_checksum_line("DRAM config needed for stage 2") is False
    assert is_mtk_checksum_line("Failed to read stage-2 config checksum") is False
    assert is_mtk_checksum_line("Writing partition BOOTIMG") is False
    assert is_mtk_checksum_line("") is False


def test_install_progress_getting_ready_mode(qapp):
    """InstallProgress in getting_ready mode hides percentage and displays 'Getting ready...'."""
    bar = InstallProgress()
    bar.setValue(12)
    assert bar.text() == "12%"

    bar.set_getting_ready(True)
    assert bar.is_getting_ready() is True
    assert bar.text() == tr("flash_getting_ready")
    assert "12" not in bar.text()
    assert "%" not in bar.text()
    # Fraction still corresponds to current fill (12%)
    assert abs(bar.fraction() - 0.12) < 0.01

    # Turning it off restores the percentage
    bar.set_getting_ready(False)
    assert bar.is_getting_ready() is False
    assert bar.text() == "12%"


def test_flash_page_getting_ready_flow(qapp):
    """FlashPage updates progress bar and step label to 'Getting ready...' and hides 12%."""
    page = FlashPage()
    page.show_flashing()
    page.update_progress(12)
    assert page._progress_bar.text() == "12%"

    # Activate getting ready
    page.set_getting_ready(True)
    assert page._getting_ready is True
    assert page._progress_bar.text() == tr("flash_getting_ready")
    assert page._step_label.text() == tr("flash_getting_ready")
    assert "12" not in page._progress_bar.text()

    # Progress ticks at 12% must not overwrite the Getting ready text
    page.update_progress(12)
    assert page._progress_bar.text() == tr("flash_getting_ready")

    # When progress advances past the checksum stage (e.g. 14%), getting ready clears
    page.update_progress(14)
    assert page._getting_ready is False
    assert page._progress_bar.text() == "14%"


def test_flash_page_getting_ready_cleared_by_step_advance(qapp):
    """Advancing to a write step clears getting ready mode."""
    page = FlashPage()
    page.show_flashing()
    page.update_progress(12)
    page.set_getting_ready(True)
    assert page._getting_ready is True

    page.update_step("step_write")
    assert page._getting_ready is False
    assert page._step_label.text() == tr("step_write")


def test_mtk_output_stall_detector_checksum_stall():
    """Checksum stall detector fires after checksum_window and reports the line."""
    fired = []
    det = _MtkOutputStallDetector(
        lambda line, elapsed: fired.append(line),
        window=10.0,
        checksum_window=0.1,
        interval=0.02,
    )
    det.start()
    try:
        det.note("Checksum: 440C")
        time.sleep(0.3)
    finally:
        det.stop()
        det.join(timeout=2)

    assert fired == ["Checksum: 440C"]


def test_mtk_output_stall_detector_checksum_disarmed_by_progress():
    """Subsequent write activity disarms a pending checksum stall."""
    fired = []
    det = _MtkOutputStallDetector(
        lambda line, elapsed: fired.append(line),
        window=10.0,
        checksum_window=0.2,
        interval=0.02,
    )
    det.start()
    try:
        det.note("Checksum: 440C")
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            det.progress()
            time.sleep(0.03)
    finally:
        det.stop()
        det.join(timeout=2)

    assert fired == []


def test_subprocess_stall_reports_mtk_checksum_stalled():
    """Subprocess stall on Checksum: sets code to MTK_CHECKSUM_STALLED."""
    class _Proc:
        terminated = False
        def poll(self):
            return None
        def terminate(self):
            self.terminated = True

    worker = FlashWorker("pkg.zip", method="mtk")
    proc = _Proc()
    worker._process = proc
    finalized = []
    worker._finalize_mtk_stall = lambda reason, **kw: finalized.append(reason)

    worker._on_mtk_output_stalled_subprocess("Checksum: 440C", 120.0)

    assert proc.terminated is True
    assert finalized == ["MTK_CHECKSUM_STALLED"]


def test_parse_flash_error_no_sp_support():
    """On an OS/arch with no SP Flash Tool support, suggest Windows or Linux with SP Flash Tool."""
    with patch("src.flash_service.is_sp_flash_tool_supported_for_host", return_value=False):
        title, desc = parse_flash_error("MTK_CHECKSUM_STALLED")
        assert title == tr("err_checksum_stall_title")
        assert "SP Flash Tool" in desc
        assert "Windows" in desc
        assert "Linux" in desc
        assert "recover the player" in desc.lower()


def test_main_window_checksum_stall_retries_sp_when_supported(qapp):
    """When MTK_CHECKSUM_STALLED occurs on a system with SP Flash Tool support, retry with SP."""
    from src.ui.main_window import MainWindow

    win = MainWindow()
    win._flash_method = METHOD_MTK
    win._fallback_attempted = False
    win._sp_flash_tool_tried = False

    retried_method = []
    win._return_to_power_off_stage = lambda method, **kw: retried_method.append(method)

    with patch("src.flash_service.is_sp_flash_tool_supported_for_host", return_value=True):
        win._handle_flash_failure("MTK_CHECKSUM_STALLED")

    assert retried_method == [METHOD_SP]
    assert win._fallback_attempted is True
    assert win._sp_flash_tool_tried is True


def test_main_window_checksum_stall_suggests_windows_linux_when_no_sp(qapp):
    """When MTK_CHECKSUM_STALLED occurs on macOS or unsupported arch, show guidance."""
    from src.ui.main_window import MainWindow

    win = MainWindow()
    win._flash_method = METHOD_MTK
    win._fallback_attempted = False

    failure_shown = []
    win._show_install_failure = lambda code: failure_shown.append(code)

    with patch("src.flash_service.is_sp_flash_tool_supported_for_host", return_value=False):
        win._handle_flash_failure("MTK_CHECKSUM_STALLED")

    assert failure_shown == ["MTK_CHECKSUM_STALLED"]


def test_main_window_on_log_message_triggers_getting_ready(qapp):
    """When a line beginning with Checksum: is received in log, flash_page enters getting ready."""
    from src.ui.main_window import MainWindow

    win = MainWindow()
    win._flash_page.show_flashing()
    win._flash_page.update_progress(12)
    assert win._flash_page._progress_bar.text() == "12%"

    win._on_log_message("Checksum: 440C")
    assert win._flash_page.is_getting_ready() if hasattr(win._flash_page, "is_getting_ready") else win._flash_page._getting_ready
    assert win._flash_page._progress_bar.text() == tr("flash_getting_ready")
    assert win._flash_page._step_label.text() == tr("flash_getting_ready")
