"""Menu bar (macOS) / system tray icon for running in the background.

The icon only exists while the app is kept running on purpose: the user opts in
with "Run in background for notifications". Clicking it brings the main window
back.

macOS gets a real ``NSStatusItem`` (see :class:`NativeStatusItem`); Windows and
Linux keep Qt's own tray through :class:`BackgroundTray`.
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


def _appkit_available() -> bool:
    """Whether the native menu bar item can be built on this host."""
    if sys.platform != "darwin":
        return False
    try:
        import AppKit  # noqa: F401
        import Foundation  # noqa: F401

        return True
    except Exception:
        logger.debug("AppKit unavailable for the menu bar item", exc_info=True)
        return False


def _localized_label(keys: tuple[str, ...], fallback: str) -> str:
    """The first catalog string that exists, else the English fallback."""
    for key in keys:
        try:
            from .i18n import tr

            text = str(tr(key) or "")
        except Exception:
            text = ""
        if text and text != key:
            return text
    return fallback


_active_native_item: "NativeStatusItem | None" = None


def _status_menu_target():
    """The ObjC object NSMenu sends its items' actions to."""
    global _status_menu_target_class
    if _status_menu_target_class is None:
        from Foundation import NSObject

        class _StatusMenuTarget(NSObject):
            def showWindow_(self, sender):  # noqa: N802
                item = _active_native_item
                if item is not None:
                    item._open_window()

            def quitApp_(self, sender):  # noqa: N802
                item = _active_native_item
                if item is not None:
                    item._quit()

        _status_menu_target_class = _StatusMenuTarget
    return _status_menu_target_class.alloc().init()


_status_menu_target_class = None


class NativeStatusItem(QObject):
    """The menu bar item, built from AppKit instead of Qt.

    Qt's ``QSystemTrayIcon`` hands macOS a rasterised ``QPixmap``, which loses
    the template flag an SF Symbol needs: the glyph then paints its own colour
    regardless of the menu bar, so on a dark bar the icon is simply not there.
    An ``NSStatusItem`` keeps the symbol as a template image, so the host draws
    it in the menu bar's own colour, and it takes a real ``NSMenu`` with the
    system's tracking, highlight and keyboard behaviour.

    The menu is the standard menu-bar app pair: open the window, or quit.
    """

    # A notification shown through this item was clicked; the window decides
    # which page that opens (a notice, or the waiting app update).
    message_clicked = Signal()

    def __init__(
        self,
        app: QApplication,
        show_window: Callable[[], None],
        quit_app: Callable[[], None],
    ) -> None:
        super().__init__(app)
        from AppKit import (
            NSApplication,
            NSMenu,
            NSMenuItem,
            NSStatusBar,
            NSVariableStatusItemLength,
        )

        global _active_native_item
        self._show_window = show_window
        self._quit_app = quit_app
        self._app = app

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._item.setVisible_(False)

        app_name = str(app.applicationName() or "Innioasis Updater")
        button = self._item.button()
        if button is not None:
            button.setImage_(self._menu_bar_image())
            button.setToolTip_(app_name)

        target = _status_menu_target()
        menu = NSMenu.alloc().init()
        open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _localized_label(("tray_open", "menu_open_window"), app_name),
            "showWindow:",
            "",
        )
        open_item.setTarget_(target)
        menu.addItem_(open_item)
        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _localized_label(("tray_quit", "menu_quit", "quit"), "Quit"),
            "quitApp:",
            "q",
        )
        quit_item.setTarget_(target)
        menu.addItem_(quit_item)

        self._item.setMenu_(menu)
        self._target = target
        self._menu = menu
        _active_native_item = self
        logger.info("Menu bar item ready (%s)", SF_SYMBOL)

    # -- appearance --------------------------------------------------------
    def _menu_bar_image(self):
        """A template SF Symbol, so the host tints it to match the menu bar."""
        from AppKit import NSApplication, NSImage

        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            SF_SYMBOL, "Update available"
        )
        if image is not None:
            image.setTemplate_(True)
            return image
        # No SF Symbols on this host: the app's own icon, drawn as it is.
        return NSApplication.sharedApplication().applicationIconImage()

    # -- the API the window uses ------------------------------------------
    def show(self) -> None:
        self._item.setVisible_(True)

    def hide(self) -> None:
        self._item.setVisible_(False)

    def is_visible(self) -> bool:
        return bool(self._item.isVisible())

    def is_available(self) -> bool:
        return True

    def showMessage(
        self,
        title: str,
        message: str,
        icon=None,
        timeout_ms: int = 10000,
    ) -> None:
        """Post the notice through the app's own Notification Center entry."""
        from .notifications import show_system_notification

        show_system_notification(title, message)

    # -- menu actions ------------------------------------------------------
    def _open_window(self) -> None:
        try:
            self._show_window()
        except Exception:
            logger.debug("Menu bar open failed", exc_info=True)

    def _quit(self) -> None:
        try:
            self._quit_app()
        except Exception:
            logger.debug("Menu bar quit failed", exc_info=True)


class BackgroundTray(QObject):
    """Owns the tray icon and its menu for one app session (Windows/Linux)."""

    # A notification shown through this tray was clicked; the window decides
    # which page that opens (a notice, or the waiting app update).
    message_clicked = Signal()

    def __init__(self, app: QApplication, show_window: Callable[[], None], quit_app: Callable[[], None], fallback_icon: QIcon):
        super().__init__(app)
        self._app = app
        self._show_window = show_window
        self._quit_app = quit_app
        icon = fallback_icon
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


def create_background_tray(
    app: QApplication,
    show_window: Callable[[], None],
    quit_app: Callable[[], None],
    fallback_icon: QIcon,
):
    """The menu bar item this host can really show.

    macOS gets its own ``NSStatusItem``; anything else keeps Qt's tray. A host
    where the native item cannot be built falls back to Qt's rather than losing
    the background mode entirely.
    """
    if _appkit_available():
        try:
            return NativeStatusItem(app, show_window, quit_app)
        except Exception:
            logger.warning("Native menu bar item unavailable; using Qt's tray", exc_info=True)
    return BackgroundTray(app, show_window, quit_app, fallback_icon)
