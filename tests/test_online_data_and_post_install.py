"""Tests for bundled online data, notices browsing, post-install links and keep-data flashing."""

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src import catalog, manifest, online_data, sp_console_config
from src.flash_service import (
    KEEP_DATA_SCATTER_NAME,
    _scatter_partition_keeps_data,
    _write_scatter_preserving_data,
    sp_flash_tool_console_args,
)
from src.notice_service import Notice, NoticeService, parse_notice_xml
from src.ui.dialogs import NoticesDialog, notice_see_more_url
from src.ui.widgets import NoticeToast

# ── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def online_dirs(tmp_path, monkeypatch):
    """Point the online-data cache and bundle at temporary directories."""
    bundle = tmp_path / "bundle"
    cache = tmp_path / "cache"
    bundle.mkdir()
    cache.mkdir()
    monkeypatch.setattr(online_data, "bundle_dir", lambda: bundle)
    monkeypatch.setattr(online_data, "cache_dir", lambda: cache)
    return bundle, cache


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


SCATTER = """\
- general: MTK_PLATFORM_CFG
  info:
    - config_version: V1.1.2
      platform: MT6572
      storage: EMMC
- partition_index: SYS0
  partition_name: PRELOADER
  file_name: preloader.bin
  is_download: true
  type: SV5_BL_BIN
- partition_index: SYS1
  partition_name: ANDROID
  file_name: system.img
  is_download: true
  type: EXT4_IMG
- partition_index: SYS2
  partition_name: CACHE
  file_name: cache.img
  is_download: true
  type: EXT4_IMG
- partition_index: SYS3
  partition_name: USRDATA
  file_name: userdata.img
  is_download: true
  type: EXT4_IMG
- partition_index: SYS4
  partition_name: userdata
  file_name: usrdata.img
  is_download: true
  type: EXT4_IMG
"""


def _scatter_file(tmp_path: Path) -> Path:
    path = tmp_path / "MT6572_Android_scatter.txt"
    path.write_text(SCATTER, encoding="utf-8")
    return path


# ── online data: cache-first, bundled fallback, lazy refresh ────────────────


def test_read_text_prefers_cache_then_bundle(online_dirs):
    bundle, cache = online_dirs
    (bundle / "notice.xml").write_text("<notices><notice id='a'/></notices>", encoding="utf-8")
    assert "id='a'" in online_data.read_text("notice.xml")

    (cache / "notice.xml").write_text("<notices><notice id='b'/></notices>", encoding="utf-8")
    assert "id='b'" in online_data.read_text("notice.xml")

    # A blank download must not shadow a usable bundled copy.
    (cache / "notice.xml").write_text("   \n", encoding="utf-8")
    assert "id='a'" in online_data.read_text("notice.xml")


def test_refresh_writes_only_a_usable_copy_and_keeps_the_previous_one(online_dirs):
    _, cache = online_dirs

    good = "<notices><notice id='fresh'/></notices>"
    assert online_data.refresh("notice.xml", force=True, fetch=lambda spec, timeout: good)
    assert "fresh" in (cache / "notice.xml").read_text(encoding="utf-8")

    assert not online_data.refresh("notice.xml", force=True, fetch=lambda spec, timeout: None)
    assert "fresh" in online_data.cached_text("notice.xml")

    # Invalid XML and an HTML error page are both refused.
    assert not online_data.refresh("notice.xml", force=True, fetch=lambda s, t: "<notices>")
    assert not online_data.refresh(
        "notice.xml", force=True, fetch=lambda s, t: "<!doctype html><html>no</html>"
    )
    assert "fresh" in online_data.cached_text("notice.xml")


def test_refresh_is_ttl_gated_until_stale(online_dirs):
    bundle, cache = online_dirs
    (bundle / "notice.xml").write_text("<notices/>", encoding="utf-8")
    calls = []

    def fetch(spec, timeout):
        calls.append(1)
        return "<notices><notice id='new'/></notices>"

    assert online_data.refresh("notice.xml", fetch=fetch)
    assert len(calls) == 1
    # Fresh copy: a later launch does not download again.
    assert not online_data.refresh("notice.xml", fetch=fetch)
    assert len(calls) == 1
    # Past the TTL it refreshes.
    assert online_data.refresh(
        "notice.xml", fetch=fetch, now=time.time() + online_data.FILES["notice.xml"].ttl + 60
    )
    assert len(calls) == 2
    assert "new" in (cache / "notice.xml").read_text(encoding="utf-8")


def test_notice_service_reads_the_bundled_copy_when_it_has_no_cache(tmp_path, monkeypatch):
    service = NoticeService()
    service._cache_file = tmp_path / "missing.xml"
    service._cache_dir = tmp_path
    monkeypatch.setattr(
        online_data,
        "read_text",
        lambda name: "<notices><notice id='seed'><title>Seed</title></notice></notices>",
    )
    assert "id='seed'" in service._snapshot_text()


# ── catalogue snapshot + post-install URLs ─────────────────────────────────

MANIFEST_XML = """\
<slidia>
  <package name="Rockbox" repo="rockbox-y1/rockbox" device="Y1" type="img"
           handler="Custom Firmware"
           post_install="https://example.test/after"
           post_install_2="https://example.test/after-support" />
</slidia>
"""


def test_manifest_falls_back_to_the_bundled_snapshot(monkeypatch):
    monkeypatch.setattr(manifest, "load_cached_manifest", lambda: None)
    monkeypatch.setattr(manifest, "fetch_manifest", lambda: None)
    monkeypatch.setattr(online_data, "read_text", lambda name: MANIFEST_XML)
    installed = {}
    monkeypatch.setattr(catalog, "set_live_catalog", lambda entries: installed.setdefault("entries", entries))

    entries = manifest.refresh_catalog(force_refresh=True)

    assert entries and entries[0].name == "Rockbox"
    assert entries[0].post_install == "https://example.test/after"
    assert entries[0].post_install_2 == "https://example.test/after-support"
    assert installed["entries"] == entries


def test_post_install_urls_take_the_first_non_empty_pair():
    local = catalog.FirmwarePackage(
        slug="local", name="Rockbox", model="Y1", repo="", package_name="",
        post_install="https://local.test/after",
    )
    live = catalog.FirmwarePackage(
        slug="rockbox-y1", name="Rockbox", model="Y1", repo="r", package_name="",
        post_install="https://manifest.test/after", post_install_2="https://manifest.test/more",
    )
    plain = catalog.FirmwarePackage(slug="x", name="Solar", model="Y1", repo="r", package_name="")

    assert catalog.post_install_urls_for(None, local, live) == ("https://local.test/after", "")
    assert catalog.post_install_urls_for(None, plain, live) == (
        "https://manifest.test/after",
        "https://manifest.test/more",
    )
    assert catalog.post_install_urls_for(None, plain) == ("", "")


# ── notices: banner content vs full_content, See More + translation ─────────

NOTICE_XML = """\
<notices>
  <notice id="n1" severity="warning" models="Y1">
    <title>Hardware revision</title>
    <content>Y1 units sold after March 2026 need Rockbox 0.5 or later.</content>
    <full_content>Units with Innioasis OS 3.0.7 pre-installed cannot run older Rockbox builds. Install Rockbox 0.5 or newer.</full_content>
    <url>https://innioasis.app/notice/hardware-revision.html</url>
  </notice>
  <notice id="n2">
    <title>Battery</title>
    <content>Charge before flashing.</content>
  </notice>
</notices>
"""


def test_notice_parses_content_full_content_and_url():
    n1, n2 = parse_notice_xml(NOTICE_XML)
    assert n1.content.startswith("Y1 units")
    assert n1.full_content.startswith("Units with Innioasis OS")
    assert n1.url == "https://innioasis.app/notice/hardware-revision.html"
    # Without full_content the banner text is the fallback body.
    assert n2.full_content == n2.content
    assert n2.url == ""


def test_see_more_url_is_translated_outside_english():
    url = "https://innioasis.app/notice/hardware-revision.html"
    assert notice_see_more_url(url, "en") == url
    translated = notice_see_more_url(url, "de")
    assert translated.startswith("https://translate.google.com/translate?")
    assert "tl=de" in translated
    assert "innioasis.app" in translated
    assert notice_see_more_url("", "de") == ""


def test_notice_toast_shows_banner_content_not_full_content(qapp):
    notice = parse_notice_xml(NOTICE_XML)[0]
    toast = NoticeToast()
    toast.set_notice(notice)
    assert toast._title_label.text() == notice.title
    assert toast._content_label.text() == notice.content
    assert notice.full_content not in toast._content_label.text()


def test_notices_dialog_shows_full_content_for_each_notice(qapp):
    notices = parse_notice_xml(NOTICE_XML)
    dialog = NoticesDialog(None, notices)
    try:
        assert len(dialog._rows) == 2
        first = dialog._rows[0]
        # The full content is shown without a click.
        assert not first._body.isHidden()
        assert first._body.toPlainText().strip() != ""
        # The globe button only exists for a notice that names a url.
        assert not first._more.isHidden()
        second = dialog._rows[1]
        assert second._more.isHidden()
    finally:
        dialog.deleteLater()


# ── keep data + Download Only flashing ─────────────────────────────────────


def test_keep_data_matching_covers_user_and_usr_partitions():
    assert _scatter_partition_keeps_data("USRDATA")
    assert _scatter_partition_keeps_data("userdata", "userdata.img")
    assert _scatter_partition_keeps_data("", "usrdata.img")
    assert not _scatter_partition_keeps_data("ANDROID", "system.img")
    assert not _scatter_partition_keeps_data("CACHE", "cache.img")


def test_keep_data_scatter_copy_disables_user_data_downloads_only(tmp_path):
    scatter = _scatter_file(tmp_path)
    original = scatter.read_text(encoding="utf-8")

    copy = _write_scatter_preserving_data(scatter)

    assert copy is not None
    assert copy.name == KEEP_DATA_SCATTER_NAME
    assert "scatter" not in copy.name.lower()
    text = copy.read_text(encoding="utf-8")

    def block(name):
        start = text.index(f"partition_name: {name}")
        return text[start:text.index("- partition_index:", start) if "- partition_index:" in text[start:] else len(text)]

    assert "is_download: false" in block("USRDATA")
    assert "is_download: false" in block("userdata")
    assert "is_download: true" in block("ANDROID")
    assert "is_download: true" in block("CACHE")
    # The package's own scatter is never edited.
    assert scatter.read_text(encoding="utf-8") == original


def test_keep_data_is_a_no_op_when_the_package_has_no_user_data(tmp_path):
    scatter = tmp_path / "MT6572_Android_scatter.txt"
    scatter.write_text(
        "- general: MTK_PLATFORM_CFG\n  info:\n    - platform: MT6572\n"
        "- partition_index: SYS0\n  partition_name: ANDROID\n  file_name: system.img\n  is_download: true\n",
        encoding="utf-8",
    )
    assert _write_scatter_preserving_data(scatter) is None


def test_keep_data_checkbox_follows_userdata_detection(qapp):
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    try:
        page.set_has_userdata(False)
        assert page._keep_data_cb.isHidden()

        # A package with a preserve-data scatter ticks it by default…
        page.set_has_userdata(True, preserve_data_default=True)
        assert not page._keep_data_cb.isHidden()
        assert page._keep_data_cb.isChecked()

        # …and a later package re-shows it, unticked.
        page.set_has_userdata(True)
        assert not page._keep_data_cb.isHidden()
        assert not page._keep_data_cb.isChecked()
    finally:
        page.deleteLater()


def test_rockbox_themes_are_an_opt_out_per_install():
    from types import SimpleNamespace

    from src.ui.main_window import MainWindow

    class _CheckBox:
        def __init__(self):
            self.checked = True

        def isChecked(self):
            return self.checked

    page = SimpleNamespace(
        _local_rockbox_themes_cb=_CheckBox(),
        _current_selected_rel=None,
        current_installed_release_info=lambda: {},
    )
    win = SimpleNamespace(
        _package_model="Y1",
        _package_name="Rockbox",
        _package_path="",
        _select_page=page,
    )

    # Opt-out: checked by default, so a Y1 Rockbox install pushes themes…
    assert MainWindow._rockbox_themes_required(win) == (True, "Y1")
    # …unless the user clears the box for this install.
    page._local_rockbox_themes_cb.checked = False
    assert MainWindow._rockbox_themes_required(win) == (False, "Y1")


def test_post_install_completion_uses_a_globe_and_view_guide(qapp, monkeypatch):
    from src.config import DONATION_LINKS
    from src.ui.flash_page import FlashPage

    opened = []
    monkeypatch.setattr("src.browser.open_browser", opened.append)
    page = FlashPage()
    try:
        page.set_post_install_urls("https://example.test/steps", "https://example.test/guide")

        # No donations: the done mark is a globe and the hint carries the link.
        page.set_device_done(donations_shown=False)
        assert page._done_mark._symbol == "www"
        assert not page._post_install_hint.isHidden()
        assert page._view_guide_btn.isHidden()

        # Donations shown: View Guide points at the second URL, and the
        # coffee button opens that same post instead of the plain ko-fi page.
        page.set_device_done(donations_shown=True)
        assert page._done_mark._symbol == "complete"
        assert page._post_install_hint.isHidden()
        assert not page._view_guide_btn.isHidden()
        page._open_post_install_guide()
        page._open_coffee_page()
        assert opened == ["https://example.test/guide", "https://example.test/guide"]

        # Without a second URL the coffee button is the usual ko-fi page.
        opened.clear()
        page.set_post_install_urls("https://example.test/steps")
        page.set_device_done(donations_shown=True)
        page._open_coffee_page()
        assert opened == [DONATION_LINKS["kofi"]]
    finally:
        page.deleteLater()


def test_sp_flash_tool_console_arguments_are_always_download_only():
    args = sp_flash_tool_console_args("scatter.txt", "DA.bin")
    assert args[:2] == ["-c", "download"]
    assert "format" not in " ".join(args).lower()


def test_sp_auth_config_uses_download_only_never_format_download(tmp_path, monkeypatch):
    monkeypatch.setattr(sp_console_config, "config_dir", lambda: tmp_path)
    scatter = _scatter_file(tmp_path)
    da = tmp_path / "DA.bin"
    da.write_bytes(b"da")
    auth = tmp_path / "custom.auth"
    auth.write_bytes(b"auth")

    args = sp_flash_tool_console_args(str(scatter), str(da), str(auth))

    assert args[0] == "-r" and args[1] == "-i"
    config = Path(args[2]).read_text(encoding="utf-8")
    assert "<download-only/>" in config
    assert "format-download" not in config
    assert f"<scatter>{scatter}</scatter>" in config
    assert f"<authentication>{auth}</authentication>" in config
