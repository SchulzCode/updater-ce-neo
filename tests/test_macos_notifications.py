"""macOS notifications and identity: the app posts its own, never AppleScript.

The launcher hands over to the bundled interpreter, so that process is what
macOS names and what owns the app's notifications. These tests pin what depends
on that: notices go through ``UNUserNotificationCenter`` (the only channel that
can show a banner), a bundled build never falls back to ``osascript`` (macOS
files those banners under Script Editor), and the login item starts the .app
bundle rather than the raw interpreter.
"""

import importlib.util
import plistlib
import sys
from pathlib import Path

import pytest

import src.notifications as notifications

ROOT = Path(__file__).resolve().parent.parent


def _builder():
    """The macOS builder, loaded by path (scripts/ is not a package)."""
    path = ROOT / "scripts" / "build_universal_app.py"
    spec = importlib.util.spec_from_file_location("build_universal_app_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _no_modern_center(monkeypatch):
    """Pretend this host has no UserNotifications framework (the harsher host)."""
    monkeypatch.setattr(notifications, "_macos_user_notification_center", lambda: None)


# -- the modern channel -----------------------------------------------------


def test_banner_presentation_is_requested():
    pytest.importorskip("UserNotifications")
    options = notifications._macos_presentation_options()
    assert options & 16  # banner
    assert options & 8  # notification list
    assert options & 2  # sound


def test_the_delegate_answers_the_frameworks_own_selectors():
    """A mis-named delegate method raises BadPrototypeError and loses every notice."""
    pytest.importorskip("UserNotifications")
    delegate = notifications._macos_un_delegate_object()
    assert delegate.respondsToSelector_(
        "userNotificationCenter:willPresentNotification:withCompletionHandler:"
    )
    assert delegate.respondsToSelector_(
        "userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:"
    )


def test_a_notice_goes_through_the_apps_own_center(monkeypatch):
    pytest.importorskip("UserNotifications")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(notifications, "_macos_bundle_identifier", lambda: "com.innioasis.updater")
    monkeypatch.setitem(notifications._macos_un_state, "authorized", True)
    monkeypatch.setitem(notifications._macos_un_state, "pending", [])
    added = []

    class _Center:
        def addNotificationRequest_withCompletionHandler_(self, request, handler):
            added.append(request)

    monkeypatch.setattr(notifications, "_macos_user_notification_center", lambda: _Center())
    monkeypatch.setattr(
        notifications, "_show_macos_script_notification", lambda *a: pytest.fail("osascript used")
    )

    assert notifications.show_system_notification("Installation Complete", "Y2", route="notices")
    content = added[0].content()
    assert content.title() == "Installation Complete"
    assert content.body() == "Y2"
    assert dict(content.userInfo())["route"] == "notices"


def test_a_notice_is_held_until_the_permission_answer_arrives(monkeypatch):
    """macOS files a request posted before the answer, so it must wait."""
    monkeypatch.setitem(notifications._macos_un_state, "authorized", None)
    monkeypatch.setitem(notifications._macos_un_state, "pending", [])

    assert notifications._post_macos_user_notification(object(), "Installation Complete", "Y2", "notices")
    assert notifications._macos_un_state["pending"] == [("Installation Complete", "Y2", "notices")]


def test_declined_permission_is_respected(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(notifications, "_macos_user_notification_center", lambda: object())
    monkeypatch.setitem(notifications._macos_un_state, "authorized", False)
    monkeypatch.setattr(
        notifications, "_show_macos_script_notification", lambda *a: pytest.fail("osascript used")
    )

    assert notifications.show_system_notification("t", "m") is False


# -- the legacy host --------------------------------------------------------


def test_a_bundled_app_never_posts_through_osascript(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    _no_modern_center(monkeypatch)
    monkeypatch.setattr(notifications, "_macos_notification_center", lambda: None)
    monkeypatch.setattr(notifications, "_macos_bundle_identifier", lambda: "com.innioasis.updater")
    spawned = []
    monkeypatch.setattr(notifications.subprocess, "Popen", lambda *a, **k: spawned.append(a))

    assert notifications.show_system_notification("Installation Complete", "Y2") is False
    assert spawned == []


def test_a_source_checkout_passes_the_text_as_arguments(monkeypatch):
    """No interpolation: a quote or "$(...)" in a notice can never become code."""
    monkeypatch.setattr(sys, "platform", "darwin")
    _no_modern_center(monkeypatch)
    monkeypatch.setattr(notifications, "_macos_notification_center", lambda: None)
    monkeypatch.setattr(notifications, "_macos_bundle_identifier", lambda: "")
    monkeypatch.setattr(notifications.shutil, "which", lambda name: "/usr/bin/osascript")
    spawned = []
    monkeypatch.setattr(notifications.subprocess, "Popen", lambda argv, **k: spawned.append(argv))

    title = 'Installation Complete " done" $(rm -rf ~) \\'
    assert notifications.show_system_notification(title, "Y2") is True
    argv = spawned[0]
    assert argv[0] == "/usr/bin/osascript"
    assert argv[1:3] == ["-e", "on run argv"]
    assert argv[-2:] == [title, "Y2"]


def test_the_apps_own_notification_channel_wins_over_the_tray(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    _no_modern_center(monkeypatch)
    monkeypatch.setattr(notifications, "_macos_notification_center", lambda: object())
    monkeypatch.setattr(
        notifications, "_show_macos_notification", lambda center, title, message: True
    )
    tray_calls = []
    monkeypatch.setattr(
        notifications,
        "_show_tray_message",
        lambda tray, title, message, timeout_ms: tray_calls.append(title) or True,
    )

    assert notifications.show_system_notification("t", "m", tray=object()) is True
    assert tray_calls == []


# -- build identity ---------------------------------------------------------


def test_build_gives_the_bundled_interpreter_the_app_identity(tmp_path):
    builder = _builder()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3.11").write_bytes(b"#!/bin/sh\n")

    builder.write_interpreter_identity(tmp_path)

    assert (bin_dir / builder.APP_DISPLAY_NAME).exists()
    info = plistlib.loads((bin_dir / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == builder.BUNDLE_ID
    assert info["CFBundleName"] == builder.APP_DISPLAY_NAME
    assert info["CFBundleDisplayName"] == builder.APP_DISPLAY_NAME
    assert info["CFBundleExecutable"] == builder.APP_DISPLAY_NAME


def test_launcher_prefers_the_interpreter_named_after_the_app():
    builder = _builder()
    source = builder.launcher_source()
    assert "@@APP_EXECUTABLE@@" not in source
    assert f"%s/bin/{builder.APP_DISPLAY_NAME}" in source


def test_build_bundles_the_notification_framework():
    """Without UserNotifications the app can only file notices silently."""
    source = (ROOT / "scripts" / "build_universal_app.py").read_text(encoding="utf-8")
    assert "pyobjc_framework_usernotifications" in source


# -- login item -------------------------------------------------------------


def _fake_bundle(tmp_path, executable="Innioasis Updater"):
    app = tmp_path / "Innioasis Updater.app"
    macos_dir = app / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleExecutable": executable})
    )
    launcher = macos_dir / executable
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter = app / "Contents" / "Resources" / "python" / "bin" / executable
    return launcher, interpreter


def test_macos_login_item_launches_the_app_not_the_interpreter(tmp_path, monkeypatch):
    from src import login_item

    launcher, interpreter = _fake_bundle(tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(interpreter))

    assert login_item._launch_command() == [str(launcher), login_item.TRAY_ARG]


def test_login_item_outside_a_bundle_keeps_the_old_command(tmp_path, monkeypatch):
    from src import login_item

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python3.11"))

    assert login_item._launch_command()[0] == str(tmp_path / "bin" / "python3.11")
