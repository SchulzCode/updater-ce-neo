"""When the Firmware Archive is unreachable, the app says so and offers a way out.

GitHub's API can be blocked outright (a work network, a firewall, a regional
filter) and the static fallback feed can be unreachable too. The catalogue then
has nothing to draw, and a screen that silently shows no releases reads as a
broken app. The connection toast must state the archive cannot be reached and
its action must open ``innioasis.app/firmware-list.html``, which needs no GitHub
API, so the package can still be fetched elsewhere and installed from the
local-file screen.

The methods are exercised on a stand-in rather than a built SelectPage: the page
starts catalogue workers and icon preloads that have no place in a unit test,
and none of that is what these assertions are about.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import feed_fallback  # noqa: E402
from src.i18n import tr  # noqa: E402
from src.ui import select_page  # noqa: E402


class _FakeToast:
    def __init__(self):
        self.message = None
        self.action_text = None

    def show_message(self, text, *, action_text="", icon=""):
        self.message = text
        self.action_text = action_text


class _Host:
    """The slice of SelectPage these methods actually read."""

    def __init__(self, *, online=False, visible=True):
        self._conn_toast = _FakeToast()
        self._online_available = online
        self._visible = visible
        self._pending_archive_notice = False
        # Bound the way the real page has it, so _maybe_show_archive_notice can
        # call into the method under test rather than a missing attribute.
        self._show_connection_toast = lambda active: select_page.SelectPackagePage._show_connection_toast(
            self, active
        )
        self.local_view_shown = 0
        self.online_refreshed = 0

    def isVisible(self):  # noqa: N802 (Qt naming)
        return self._visible

    def _show_local_view(self):
        self.local_view_shown += 1

    def _show_online_view(self, refresh=False):
        self.online_refreshed += 1


def test_offline_notice_names_the_archive_and_offers_the_static_list():
    host = _Host()
    select_page.SelectPackagePage._show_connection_toast(host, False)

    assert host._conn_toast.message == tr("conn_archive_unavailable")
    assert host._conn_toast.action_text == tr("conn_action_archive_list")


def test_the_offered_action_opens_the_firmware_list(monkeypatch):
    opened = []
    monkeypatch.setattr(
        "src.browser.open_browser", lambda url, **kw: opened.append(url) or True
    )

    host = _Host(online=False)
    select_page.SelectPackagePage._on_conn_toast_action(host)

    assert opened == [feed_fallback.FIRMWARE_LIST_URL]
    assert opened[0].endswith("/firmware-list.html")


def test_a_browser_that_will_not_open_still_reaches_the_local_screen(monkeypatch):
    monkeypatch.setattr("src.browser.open_browser", lambda *a, **k: False)

    host = _Host(online=False)
    select_page.SelectPackagePage._on_conn_toast_action(host)

    assert host.local_view_shown == 1


def test_online_action_still_refreshes_the_catalogue(monkeypatch):
    monkeypatch.setattr(
        "src.browser.open_browser",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not open a page")),
    )

    host = _Host(online=True)
    select_page.SelectPackagePage._on_conn_toast_action(host)

    assert host.online_refreshed == 1


def test_a_session_that_starts_offline_explains_itself_once():
    """Not a connection *change*, but it is when the app would look broken."""
    host = _Host()
    host._pending_archive_notice = True

    select_page.SelectPackagePage._maybe_show_archive_notice(host)
    assert host._conn_toast.message == tr("conn_archive_unavailable")

    host._conn_toast.message = None
    select_page.SelectPackagePage._maybe_show_archive_notice(host)
    assert host._conn_toast.message is None


def test_the_startup_notice_waits_for_the_page_and_for_a_real_failure():
    hidden = _Host(visible=False)
    hidden._pending_archive_notice = True
    select_page.SelectPackagePage._maybe_show_archive_notice(hidden)
    assert hidden._conn_toast.message is None
    assert hidden._pending_archive_notice is True

    online = _Host(online=True)
    online._pending_archive_notice = True
    select_page.SelectPackagePage._maybe_show_archive_notice(online)
    assert online._conn_toast.message is None

    recovered = _Host(online=True)
    recovered._pending_archive_notice = True
    select_page.SelectPackagePage._maybe_show_archive_notice(recovered)
    assert recovered._pending_archive_notice is True
