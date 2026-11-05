"""The launch shows the splash card first; the app window follows it.

A launch may put one thing on screen until the app is ready: the startup card.
The main window is still shown beneath it (Qt has to lay it out, fit the first
page and apply the native chrome), but with no pixels of its own, and it is
revealed in the same frame that retires the card. The start-at-login run shows
neither surface at all; ``tests/test_startup_login.py`` covers that decision.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import app as app_module  # noqa: E402


class _FakeWindow:
    def __init__(self):
        self.calls = []

    def setWindowOpacity(self, value):
        self.calls.append(("opacity", value))

    def show(self):
        self.calls.append(("show",))


class _FakeSplash:
    def __init__(self):
        self.calls = []

    def finish(self, window):
        self.calls.append(("finish", window))

    def deleteLater(self):
        self.calls.append(("deleteLater",))


def test_the_window_is_shown_without_pixels_under_the_card():
    win = _FakeWindow()
    assert app_module._conceal_window_under_splash(win) is True
    assert win.calls == [("opacity", 0.0), ("show",)]


def test_the_card_retires_as_the_window_appears():
    win = _FakeWindow()
    splash = _FakeSplash()
    app_module._reveal_launched_window(win, splash)
    assert win.calls == [("opacity", 1.0)]
    assert splash.calls == [("finish", win), ("deleteLater",)]


def test_a_host_without_window_opacity_still_shows_the_window():
    class _Refusing(_FakeWindow):
        def setWindowOpacity(self, value):
            raise RuntimeError("no compositor")

    win = _Refusing()
    assert app_module._conceal_window_under_splash(win) is False
    assert win.calls == []
