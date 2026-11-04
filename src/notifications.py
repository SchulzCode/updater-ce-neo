"""Desktop notification and window focus management across macOS, Windows, and Linux.

Provides window focus-grabbing when background tasks finish and surfaces native
OS system notifications so users are alerted when firmware installation or theme
transfers complete.

On macOS the notification is posted by this app through Notification Center, so
it carries the app's name, icon and permission. That needs the process to have a
bundle identifier, which the build gives the bundled interpreter (see
``write_interpreter_identity`` in ``scripts/build_universal_app.py``).

There is deliberately no AppleScript/``osascript`` channel on any platform: macOS
files those banners under Script Editor, so the notice would arrive as another
app that the user never opened and can neither see nor allow in Notifications.
When this app has no channel of its own the notice is simply not posted; it is
still waiting on its page in the app.
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
        # Standard Qt window state restoration, including a copy that was
        # closed to the menu bar/tray (hidden rather than minimised).
        if window.isMinimized():
            window.showNormal()
        window.setWindowState(window.windowState() | Qt.WindowActive)
        window.show()
        window.raise_()
        window.activateWindow()
        handle = window.windowHandle()
        if handle is not None:
            handle.requestActivate()
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


def _macos_bundle_identifier() -> str:
    """This process's bundle identifier, or "" when it has none.

    The build gives the bundled interpreter the app's identity, because the
    launcher hands over to it and the app's code then runs in that process. A
    source checkout has no bundle at all.
    """
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        identifier = bundle.bundleIdentifier() if bundle is not None else None
        return str(identifier or "")
    except Exception:
        return ""


def system_notifications_available() -> bool:
    """Whether this computer has a channel that can show a system notification."""
    try:
        if sys.platform == "darwin":
            try:
                from AppKit import NSUserNotificationCenter  # noqa: F401
            except Exception:
                return False
            # Without a bundle identifier the center is nil and no banner can
            # carry this app's name, so the switch has to read as off.
            return bool(_macos_bundle_identifier())
        if os.name == "nt":
            return True
        if shutil.which("notify-send"):
            return True
        return QSystemTrayIcon.isSystemTrayAvailable()
    except Exception:
        return False


_macos_delegate = None


def _macos_notification_center():
    """Notification Center for this app with a click delegate installed, or None.

    A click on a delivered notice must reach the window so it can open the page
    the notification is about; macOS reports it through the center's delegate.
    The center is nil for a process without a bundle identifier, which is why
    the build gives the bundled interpreter one.
    """
    global _macos_delegate
    try:
        from AppKit import NSUserNotificationCenter
        from Foundation import NSObject

        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:
            return None
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
        center.setDelegate_(_macos_delegate)
        return center
    except Exception:
        logger.debug("macOS notification click routing unavailable", exc_info=True)
        return None


def _show_macos_notification(center, title: str, message: str) -> bool:
    """Deliver one notice through this app's Notification Center entry."""
    try:
        from Foundation import NSUserNotification

        notification = NSUserNotification.alloc().init()
        notification.setTitle_(title)
        notification.setInformativeText_(message)
        center.deliverNotification_(notification)
        return True
    except Exception:
        logger.debug("macOS notification delivery failed", exc_info=True)
        return False


def _show_tray_message(tray, title: str, message: str, timeout_ms: int) -> bool:
    try:
        tray.showMessage(title, message, QSystemTrayIcon.Information, timeout_ms)
        return True
    except Exception as e:
        logger.debug("Tray notification failed: %s", e)
        return False


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

    ``tray`` is the app's own background tray when one exists. On macOS the
    app's Notification Center entry is tried first: it is what the user sees
    under this app's name, and a click routes back to the window. The tray and
    Linux's ``notify-send`` are the other channels; there is no script channel
    anywhere (see the module docstring).
    """
    app = QApplication.instance()
    if app is not None:
        # Which page a click should open; the window's click handler reads it.
        setattr(app, "_last_notification_route", route or "")

    if sys.platform == "darwin":
        # 1. The app's own Notification Center channel.
        center = _macos_notification_center()
        if center is not None and _show_macos_notification(center, title, message):
            return True
        # 2. The background tray, when the user keeps one running.
        if tray is not None and _show_tray_message(tray, title, message, timeout_ms):
            return True
        # 3. Nothing else: the notice is already on its page, and it must never
        #    be handed to a script host that would show it under its own name.
        logger.info(
            "macOS could not show a notification for this app; it is still on its page in the app"
        )
        return False

    # 1. The app's background tray, when the user keeps one running.
    if tray is not None and _show_tray_message(tray, title, message, timeout_ms):
        return True

    # 2. Linux notification daemon.
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

    # 3. Balloon through a tray icon (Windows, and any host left above).
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
