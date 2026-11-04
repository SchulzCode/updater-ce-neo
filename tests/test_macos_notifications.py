"""macOS notifications and identity: the app posts its own, never AppleScript.

The launcher hands over to the bundled interpreter, so that process is what
macOS names and what owns the app's notifications. These tests pin the things
that depend on it: nothing ever falls back to ``osascript`` (macOS files those
banners under Script Editor, an app the user never opened), the notification
switch only offers a channel that carries this app's name, and the login item
starts the .app bundle rather than the raw interpreter.
"""

import importlib.util
import plistlib
import sys
import types
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


# -- notification channel ---------------------------------------------------


def test_a_bundled_app_never_posts_through_osascript(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(notifications, "_macos_notification_center", lambda: None)
    monkeypatch.setattr(notifications, "_macos_bundle_identifier", lambda: "com.innioasis.updater")
    spawned = []
    monkeypatch.setattr(notifications.subprocess, "Popen", lambda *a, **k: spawned.append(a))

    assert notifications.show_system_notification("Installation Complete", "Y2") is False
    assert spawned == []


def test_a_source_checkout_never_posts_through_osascript(monkeypatch):
    """No bundle and no center: the notice is kept in the app, not handed to a script host."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(notifications, "_macos_notification_center", lambda: None)
    monkeypatch.setattr(notifications, "_macos_bundle_identifier", lambda: "")
    monkeypatch.setattr(notifications.shutil, "which", lambda name: "/usr/bin/osascript")
    spawned = []
    monkeypatch.setattr(notifications.subprocess, "Popen", lambda argv, **k: spawned.append(argv))

    assert notifications.show_system_notification("Installation Complete", "Y2") is False
    assert spawned == []


def test_notifications_are_unavailable_without_the_apps_own_identity(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules, "AppKit", types.SimpleNamespace(NSUserNotificationCenter=object)
    )
    monkeypatch.setattr(notifications, "_macos_bundle_identifier", lambda: "")

    assert notifications.system_notifications_available() is False

    monkeypatch.setattr(notifications, "_macos_bundle_identifier", lambda: "com.innioasis.updater")
    assert notifications.system_notifications_available() is True


def test_the_apps_own_notification_channel_wins_over_the_tray(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
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


def test_housekeeping_notifies_natively_or_not_at_all(monkeypatch):
    """The scheduled check must never pop a banner under Script Editor."""
    from housekeeping import core

    monkeypatch.setattr(sys, "platform", "darwin")
    # A launchd run of a source checkout has no PyObjC bundle identity to use.
    monkeypatch.setitem(sys.modules, "Foundation", None)
    calls = []
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: calls.append(a))

    core.notify("Mirror Housekeeping", "2 mirror(s) need attention")

    assert calls == []


def test_no_source_file_can_hand_a_notice_to_a_script_host():
    """The rule everywhere, not only at the call sites covered above."""
    candidates = [ROOT / "launcher.py", ROOT / "housekeeping" / "core.py"]
    candidates += sorted((ROOT / "src").rglob("*.py"))
    needles = ("display notification", "display alert")
    offenders = [
        str(path.relative_to(ROOT))
        for path in candidates
        if any(needle in path.read_text(encoding="utf-8") for needle in needles)
    ]
    assert offenders == []


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
