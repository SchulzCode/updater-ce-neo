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
    otherwise hide the tip from every test that expects to see it.
    """
    try:
        from PySide6.QtCore import QSettings

        settings = QSettings("innioasis", "updater")
        for name in ("paperclip", "microsd", "keep_data_warning"):
            settings.remove(f"dismissed_install_hints/{name}")
    except Exception:
        pass
    yield
