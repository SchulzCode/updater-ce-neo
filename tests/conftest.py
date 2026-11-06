"""Guards shared by every test module.

The app asks the OS to stay awake while it writes to a player. Tests exercise
those flows headlessly many times over, so they must never spawn an inhibitor
or hold a power assertion; the sleep-inhibition tests opt back in by clearing
this variable themselves.
"""

import os

import pytest

os.environ.setdefault("INNIOASIS_NO_SLEEP_INHIBIT", "1")
# Donation copy is rendered in the reader's currency. Pin the dollar so tests
# that assert "$10" describe the copy rather than the machine's locale; the
# currency tests set their own code with monkeypatch.setenv.
os.environ.setdefault("UPDATER_CURRENCY", "USD")


@pytest.fixture(autouse=True)
def _forget_dismissed_install_hints():
    """A dismissed card hint is a user preference, not test state.

    The paperclip, microSD and keep-data tips keep their "Got it." in
    QSettings, so a developer who dismissed one while looking at the app would
    otherwise hide the tip from every test that expects to see it. The saved
    values go back afterwards: running the suite must never rewrite which
    hints the developer has already read.
    """
    settings = None
    saved = {}
    try:
        from PySide6.QtCore import QSettings

        settings = QSettings("innioasis", "updater")
        for name in ("paperclip", "microsd", "keep_data_warning"):
            key = f"dismissed_install_hints/{name}"
            if settings.contains(key):
                saved[key] = bool(settings.value(key, False, type=bool))
            settings.remove(key)
        settings.sync()
    except Exception:
        settings = None
    yield
    if settings is not None:
        try:
            for key, value in saved.items():
                settings.setValue(key, value)
            settings.sync()
        except Exception:
            pass
