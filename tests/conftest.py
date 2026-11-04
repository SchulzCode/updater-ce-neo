"""Guards shared by every test module.

The app asks the OS to stay awake while it writes to a player. Tests exercise
those flows headlessly many times over, so they must never spawn an inhibitor
or hold a power assertion; the sleep-inhibition tests opt back in by clearing
this variable themselves.
"""

import os

os.environ.setdefault("INNIOASIS_NO_SLEEP_INHIBIT", "1")
# Donation copy is rendered in the reader's currency. Pin the dollar so tests
# that assert "$10" describe the copy rather than the machine's locale; the
# currency tests set their own code with monkeypatch.setenv.
os.environ.setdefault("UPDATER_CURRENCY", "USD")
