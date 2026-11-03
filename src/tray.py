"""Menu bar (macOS) / system tray icon for running in the background.

The icon only exists while the app is kept running on purpose: the user opts in
with "Run in background for notifications". Clicking it brings the main window
back.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Optional

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

logger = logging.getLogger(__name__)

RUN_IN_BACKGROUND_KEY = "run_in_background"
SF_SYMBOL = "arrow.triangle.2.circlepath"
_AUTOMATIC_TERMINATION_HELD = False


def run_in_background_enabled(settings: Optional[QSettings] = None) -> bool:
    """The opt-in switch. Off unless the user turned it on."""
    try:
        from .remote_flags import is_remote_flag_active
        if is_remote_flag_active("background_off") or is_remote_flag_active("no_background"):
            return False
        if is_remote_flag_active("background_on") or is_remote_flag_active("force_background"):
            return True
    except Exception:
        pass
    settings = settings or QSettings("innioasis", "updater")
    value = settings.value(RUN_IN_BACKGROUND_KEY, False)
    return str(value).lower() in ("1", "true", "yes")


def hold_off_automatic_termination(hold: bool) -> None:
    """Keep macOS from reaping an idle app that lives only in the menu bar.

    AppKit treats a hidden window as "no windows" and enables automatic
    termination when the close-to-tray path hides it. A few seconds later the
    process can exit with status 0 while its menu bar icon is still up, which
    looks like a crash. Hold that off while hidden and give it back when the
    window is shown again.
    """
    global _AUTOMATIC_TERMINATION_HELD
    if sys.platform != "darwin" or hold == _AUTOMATIC_TERMINATION_HELD:
        return
    try:
        from AppKit import NSProcessInfo

        info = NSProcessInfo.processInfo()
        if hold:
            info.disableAutomaticTermination_("Running in the menu bar")
            if hasattr(info, "disableSuddenTermination"):
                info.disableSuddenTermination()
        else:
            info.enableAutomaticTermination_("Running in the menu bar")
            if hasattr(info, "enableSuddenTermination"):
                info.enableSuddenTermination()
    except Exception:
        logger.debug("Could not change macOS automatic termination", exc_info=True)
        return
    _AUTOMATIC_TERMINATION_HELD = hold


def _macos_template_icon(fallback: QIcon) -> QIcon:
    """A system-tinted SF Symbol, so macOS draws it black or white with the menu bar."""
    try:
        from AppKit import NSImage

        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(SF_SYMBOL, None)
        if image is None:
            return fallback
        image.setTemplate_(True)
        from AppKit import NSBitmapImageRep, NSPNGFileType
        from PySide6.QtGui import QImage, QPixmap

        rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        if rep is None:
            return fallback
        png = bytes(rep.representationUsingType_properties_(NSPNGFileType, None))
        pixmap = QPixmap.fromImage(QImage.fromData(png))
        if pixmap.isNull():
            return fallback
        icon = QIcon(pixmap)
        icon.setIsMask(True)
        return icon
    except Exception:
        logger.debug("SF Symbol tray icon unavailable", exc_info=True)
        return fallback


class BackgroundTray(QObject):
    """Owns the tray icon and its menu for one app session."""

    # A notification shown through this tray was clicked; the window decides
    # which page that opens (a notice, or the waiting app update).
    message_clicked = Signal()

    def __init__(self, app: QApplication, show_window: Callable[[], None], quit_app: Callable[[], None], fallback_icon: QIcon):
        super().__init__(app)
        self._app = app
        self._show_window = show_window
        self._quit_app = quit_app
        icon = _macos_template_icon(fallback_icon) if sys.platform == "darwin" else fallback_icon
        self._tray = QSystemTrayIcon(icon, app)
        self._tray.setToolTip(app.applicationName())
        menu = QMenu()
        menu.addAction(app.applicationName(), self._on_show)
        menu.addSeparator()
        menu.addAction("Quit", self._on_quit)
        self._menu = menu
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.messageClicked.connect(self.message_clicked.emit)

    def showMessage(
        self,
        title: str,
        message: str,
        icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.Information,
        timeout_ms: int = 10000,
    ) -> None:
        self._tray.showMessage(title, message, icon, timeout_ms)

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def is_visible(self) -> bool:
        return self._tray.isVisible()

    def is_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._on_show()

    def _on_show(self) -> None:
        self._show_window()

    def _on_quit(self) -> None:
        self._quit_app()
