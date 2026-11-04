"""The menu bar / system tray mode is opt-in.

Off by default: closing the window quits the app and no tray icon exists.
On: closing hides the window, keeps a tray icon, and an app update found while
hidden is announced natively instead of by an in-window toast.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from src import login_item, tray
from src.state import FlashState
from src.ui.main_window import MainWindow
from src.updates import UpdateInfo


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def _window():
    app = _app()
    win = MainWindow()
    win.show()
    app.processEvents()
    return app, win


def _clear_opt_in():
    QSettings("innioasis", "updater").remove(tray.RUN_IN_BACKGROUND_KEY)


def test_background_opt_in_defaults_to_off():
    _app()
    _clear_opt_in()
    assert tray.run_in_background_enabled() is False


def test_background_opt_in_reads_back_when_enabled():
    _app()
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        assert tray.run_in_background_enabled() is True
    finally:
        _clear_opt_in()


def test_close_with_opt_in_off_quits_and_creates_no_tray(monkeypatch):
    _clear_opt_in()
    app, win = _window()
    monkeypatch.setattr(login_item, "set_login_item", lambda enabled: None)
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted()
    assert getattr(win, "_background_tray", None) is None


def test_close_with_opt_in_on_hides_to_tray_and_keeps_process(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        app, win = _window()
        event = QCloseEvent()
        win.closeEvent(event)
        assert not event.isAccepted()
        assert not win.isVisible()
        assert win._background_tray.is_visible()
    finally:
        _clear_opt_in()


def test_close_is_blocked_during_install_even_with_opt_in_on():
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        app, win = _window()
        win._install_complete = False
        win._install_failed = False
        win._download_active = True
        event = QCloseEvent()
        win.closeEvent(event)
        # The install prompt must run before any hide: an unanswered prompt
        # keeps the window and its install open.
        assert getattr(win, "_background_tray", None) is None or not win._background_tray.is_visible() or win.isVisible() or not event.isAccepted()
        assert not (event.isAccepted() and getattr(win, "_closing_accepted", False))
    finally:
        _clear_opt_in()


def test_tray_quit_does_not_quit_while_install_blocks_close(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    quit_calls = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quit_calls.append(True)))
    try:
        app, win = _window()
        win._download_active = True
        win._install_complete = False
        win._install_failed = False
        win._test_close_prompt = True
        # The user declines the stop prompt: the install keeps running.
        monkeypatch.setattr(win._flash_page, "prompt_stop", lambda *a, **k: False)
        win._quit_from_tray()
        assert quit_calls == []
        assert not win._closing_accepted
    finally:
        _clear_opt_in()


def test_app_update_while_hidden_is_announced_natively(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        app, win = _window()
        win._hide_to_tray(QCloseEvent())
        assert win._window_hidden_in_tray()
        sent = []
        monkeypatch.setattr(win, "_notify_app_update", lambda info: sent.append(info.tag))
        win._update_offered_once = False
        win._update_manual_pending = False
        win._on_update_check_done(UpdateInfo(tag="v9.9.9"), manual=False)
        assert sent == ["v9.9.9"]
    finally:
        _clear_opt_in()


def test_close_to_tray_holds_off_macos_automatic_termination(monkeypatch):
    """A hidden menu-bar app must not be reaped by AppKit automatic termination."""
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    calls = []
    monkeypatch.setattr(tray, "hold_off_automatic_termination", lambda hold: calls.append(hold))
    try:
        app, win = _window()
        win._hide_to_tray(QCloseEvent())
        assert calls == [True]
        assert win._window_hidden_in_tray()

        win._show_from_tray()
        assert calls == [True, False]
        assert win.isVisible()
    finally:
        _clear_opt_in()
        if getattr(win, "_background_tray", None) is not None:
            win._background_tray.hide()
        win._force_quit = True
        win.close()
        app.processEvents()


def test_tray_that_is_not_visible_yet_does_not_quit_the_app(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    quit_calls = []
    monkeypatch.setattr(tray, "hold_off_automatic_termination", lambda hold: None)
    app, win = _window()

    class _InvisibleTray:
        def show(self):
            pass

        def is_visible(self):
            return False

        def is_available(self):
            return True

    monkeypatch.setattr(win, "_ensure_background_tray", lambda: _InvisibleTray())
    monkeypatch.setattr(win, "_schedule_app_quit", lambda: quit_calls.append(True))
    try:
        win._hide_to_tray(QCloseEvent())
        assert quit_calls == []
        assert not win.isVisible()
    finally:
        _clear_opt_in()
        win._force_quit = True
        win.close()
        app.processEvents()


def test_start_in_tray_holds_off_macos_automatic_termination(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    calls = []
    monkeypatch.setattr(tray, "hold_off_automatic_termination", lambda hold: calls.append(hold))
    try:
        app, win = _window()
        win._hide_to_tray_at_launch()
        assert calls == [True]
    finally:
        _clear_opt_in()
        if getattr(win, "_background_tray", None) is not None:
            win._background_tray.hide()
        win._force_quit = True
        win.close()
        app.processEvents()


def test_hidden_window_pauses_its_timers_and_resumes_on_show():
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        app, win = _window()
        win._hide_to_tray(QCloseEvent())
        assert win._background_tray.is_visible()
        timer = getattr(win, "_chrome_watchdog", None)
        if timer is not None:
            assert not timer.isActive()
        win._show_from_tray()
        if timer is not None:
            assert timer.isActive()
    finally:
        _clear_opt_in()


def test_background_tray_show_message(monkeypatch):
    """BackgroundTray must delegate showMessage to QSystemTrayIcon without AttributeError."""
    app = _app()
    from PySide6.QtGui import QIcon
    bg_tray = tray.BackgroundTray(app, lambda: None, lambda: None, QIcon())
    messages = []
    monkeypatch.setattr(bg_tray._tray, "showMessage", lambda *args: messages.append(args))
    bg_tray.showMessage("Test Title", "Test Message")
    assert len(messages) == 1
    assert messages[0][0] == "Test Title"
    assert messages[0][1] == "Test Message"


def test_macos_gets_the_native_menu_bar_item(monkeypatch):
    """Qt's tray loses the template flag, so macOS must build an NSStatusItem."""
    from PySide6.QtGui import QIcon

    created = []
    monkeypatch.setattr(tray, "_appkit_available", lambda: True)
    monkeypatch.setattr(tray, "NativeStatusItem", lambda *a, **k: created.append(a) or "native")
    assert tray.create_background_tray(object(), lambda: None, lambda: None, QIcon()) == "native"
    assert len(created) == 1


def test_other_hosts_keep_qt_tray(monkeypatch):
    from PySide6.QtGui import QIcon

    monkeypatch.setattr(tray, "_appkit_available", lambda: False)
    bg_tray = tray.create_background_tray(_app(), lambda: None, lambda: None, QIcon())
    assert isinstance(bg_tray, tray.BackgroundTray)


def test_a_host_without_a_menu_bar_quits_instead_of_hiding(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    quit_calls = []
    app, win = _window()

    class _UnavailableTray:
        def show(self):
            pass

        def is_visible(self):
            return False

        def is_available(self):
            return False

    monkeypatch.setattr(win, "_ensure_background_tray", lambda: _UnavailableTray())
    monkeypatch.setattr(win, "_schedule_app_quit", lambda: quit_calls.append(True))
    try:
        win._hide_to_tray(QCloseEvent())
        assert quit_calls == [True]
    finally:
        _clear_opt_in()
        win._force_quit = True
        win.close()
        app.processEvents()


def test_settings_run_in_background_toggled_updates_quit_on_last_window_closed(monkeypatch):
    """Toggling run_in_background immediately syncs QApplication.quitOnLastWindowClosed."""
    from src.ui.settings_page import SettingsPage

    app = _app()
    monkeypatch.setattr("src.login_item.set_login_item", lambda enabled: None)
    settings_page = SettingsPage()
    try:
        settings_page._on_run_in_background_toggled(True)
        assert app.quitOnLastWindowClosed() is False

        settings_page._on_run_in_background_toggled(False)
        assert app.quitOnLastWindowClosed() is True
    finally:
        _clear_opt_in()
        settings_page.deleteLater()

