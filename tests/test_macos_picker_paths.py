"""File pickers must not start in the home folder on macOS.

Starting a native panel there makes macOS enumerate Documents and show a
Documents access prompt. The app's per-user data stays in Application Support;
user-facing pickers start in Downloads or the app download folder.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import downloads  # noqa: E402
from src.ui import dialogs  # noqa: E402


class _FakeLogView:
    class _Text:
        def toPlainText(self):
            return "diagnostic line"

    _view = _Text()

    def current_category(self):
        return "all"

    def _filter_active(self):
        return False

    def _show_temp_status(self, *_args, **_kwargs):
        pass


def test_save_log_dialog_starts_outside_home(tmp_path, monkeypatch):
    captured = {}

    def fake_save_dialog(parent, title, start_path, filters):
        captured["start_path"] = start_path
        return str(tmp_path / "saved-log.txt"), ""

    monkeypatch.setattr(dialogs.QFileDialog, "getSaveFileName", fake_save_dialog)

    dialogs.DiagnosticsView._on_save_file(_FakeLogView())

    assert (tmp_path / "saved-log.txt").read_text(encoding="utf-8") == "diagnostic line"
    start = Path(captured["start_path"])
    assert start.parent == Path(downloads.default_picker_dir())
    assert start.parent != Path.home()
    assert start.name == "updater_ce_all_log.txt"
