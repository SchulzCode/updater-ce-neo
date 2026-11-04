"""Guards shared by every test module.

The app asks the OS to stay awake while it writes to a player. Tests exercise
those flows headlessly many times over, so they must never spawn an inhibitor
or hold a power assertion; the sleep-inhibition tests opt back in by clearing
this variable themselves.
"""

import os

os.environ.setdefault("INNIOASIS_NO_SLEEP_INHIBIT", "1")
