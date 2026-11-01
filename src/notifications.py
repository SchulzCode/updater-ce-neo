"""Desktop notification and window focus management across macOS, Windows, and Linux.

Provides window focus-grabbing when background tasks finish and surfaces native
OS system notifications so users are alerted when firmware installation or theme
transfers complete.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

logger = logging.getLogger(__name__)


def bring_window_to_front(window: Optional[QWidget]) -> None:
    """Un-minimize, raise, and activate the given window, grabbing focus across OSes."""
    if window is None or not isinstance(window, QWidget):
        return

    try:
        # Standard Qt window state restoration
        state = window.windowState()
        if state & Qt.WindowMinimized:
            window.setWindowState((state & ~Qt.WindowMinimized) | Qt.WindowActive)
        else:
            window.setWindowState(state | Qt.WindowActive)

        window.show()
        window.raise_()
        window.activateWindow()
    except Exception as e:
        logger.debug("Failed basic Qt window raise: %s", e)

    # OS-specific window activation
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication
            app = NSApplication.sharedApplication()
            app.activateIgnoringOtherApps_(True)
        except Exception as e:
            logger.debug("macOS activateIgnoringOtherApps failed: %s", e)
    elif os.name == "nt":
        try:
            import ctypes
            hwnd = int(window.winId())
            # SW_RESTORE = 9
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.debug("Windows SetForegroundWindow failed: %s", e)
    else:
        # Linux (X11 / Wayland alert)
        try:
            QApplication.alert(window, 0)
        except Exception as e:
            logger.debug("Linux QApplication.alert failed: %s", e)


def system_notifications_available() -> bool:
    """Whether this computer has a channel that can show a system notification."""
    try:
        if sys.platform == "darwin":
            try:
                from Foundation import NSUserNotificationCenter  # noqa: F401

                return True
            except Exception:
                return bool(shutil.which("osascript"))
        if os.name == "nt":
            return True
        if shutil.which("notify-send"):
            return True
        return QSystemTrayIcon.isSystemTrayAvailable()
    except Exception:
        return False


_macos_delegate = None


def _macos_notification_center():
    """Notification center with a click delegate installed, or None.

    A click on a delivered notice must reach the window so it can open the page
    the notification is about; macOS reports it through the center's delegate.
    """
    global _macos_delegate
    try:
        from AppKit import NSUserNotificationCenter
        from Foundation import NSObject

        if _macos_delegate is None:
            class _NotificationClickDelegate(NSObject):
                def userNotificationCenter_didActivateNotification_(self, center, notification):  # noqa: N802
                    try:
                        app = QApplication.instance()
                        handler = getattr(app, "_notification_click_handler", None)
                        if callable(handler):
                            handler(getattr(app, "_last_notification_route", ""))
                    except Exception:
                        logger.debug("Notification click handler failed", exc_info=True)

            _macos_delegate = _NotificationClickDelegate.alloc().init()
            NSUserNotificationCenter.defaultUserNotificationCenter().setDelegate_(_macos_delegate)
        return NSUserNotificationCenter
    except Exception:
        logger.debug("macOS notification click routing unavailable", exc_info=True)
        return None


def _fallback_tray(app: QApplication, window: Optional[QWidget] = None):
    """A tray icon used only as a balloon-notification channel."""
    tray = getattr(app, "_completion_tray_icon", None)
    if tray is None:
        icon = window.windowIcon() if window is not None else app.windowIcon()
        tray = QSystemTrayIcon(icon, app)
        setattr(app, "_completion_tray_icon", tray)
    if not getattr(tray, "_notice_click_connected", False):
        def _route_click():
            handler = getattr(QApplication.instance(), "_notification_click_handler", None)
            if callable(handler):
                handler(getattr(QApplication.instance(), "_last_notification_route", ""))

        tray.messageClicked.connect(_route_click)
        tray._notice_click_connected = True
    tray.show()
    return tray


def show_system_notification(
    title: str,
    message: str,
    tray=None,
    timeout_ms: int = 10000,
    route: str = "",
) -> bool:
    """Deliver a notification without raising the window. Returns True if sent.

    ``tray`` is the app's own background tray when one exists; using it keeps
    the notification clickable (the tray reports ``messageClicked``). Without
    one, macOS gets a native Notification Center banner and Linux uses
    ``notify-send`` so no menu bar icon appears just to show a notice.
    """
    app = QApplication.instance()
    if app is not None:
        # Which page a click should open; the window's click handler reads it.
        setattr(app, "_last_notification_route", route or "")

    # 1. The app's background tray, when the user keeps one running.
    if tray is not None:
        try:
            tray.showMessage(title, message, QSystemTrayIcon.Information, timeout_ms)
            return True
        except Exception as e:
            logger.debug("Tray notification failed: %s", e)

    # 2. macOS native notification (works for a bundled app; clicking it opens
    #    the app, which is also what the tray's message click does).
    if sys.platform == "darwin":
        center = _macos_notification_center()
        if center is not None:
            try:
                from Foundation import NSUserNotification

                notif = NSUserNotification.alloc().init()
                notif.setTitle_(title)
                notif.setInformativeText_(message)
                center.defaultUserNotificationCenter().deliverNotification_(notif)
                return True
            except Exception:
                logger.debug("NSUserNotification delivery failed", exc_info=True)
        try:
            escaped_title = title.replace('"', '\\"')
            escaped_msg = message.replace('"', '\\"')
            subprocess.Popen(
                ["osascript", "-e", f'display notification "{escaped_msg}" with title "{escaped_title}"'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            logger.debug("macOS notification failed: %s", e)

    # 3. Linux notification daemon.
    if sys.platform.startswith("linux"):
        notify_send = shutil.which("notify-send")
        if notify_send:
            try:
                subprocess.Popen(
                    [notify_send, "-a", "Innioasis Updater CE", title, message],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception as e:
                logger.debug("notify-send failed: %s", e)

    # 4. Balloon through a tray icon (Windows, and any host left above).
    try:
        if app is not None and QSystemTrayIcon.supportsMessages():
            _fallback_tray(app).showMessage(
                title, message, QSystemTrayIcon.Information, timeout_ms
            )
            return True
    except Exception as e:
        logger.debug("Fallback tray notification failed: %s", e)
    return False


def show_completion_notification(
    title: str,
    message: str,
    window: Optional[QWidget] = None,
) -> None:
    """Deliver a desktop notification and alert the user's desktop environment."""
    if window is not None:
        bring_window_to_front(window)
    show_system_notification(title, message)

    # Flash window / taskbar icon
    if window is not None:
        try:
            QApplication.alert(window, 0)
        except Exception:
            pass
