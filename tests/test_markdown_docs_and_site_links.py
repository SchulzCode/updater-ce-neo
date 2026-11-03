"""Post-install guides and release notes shown by the app, not by GitHub.

A firmware's ``post_install`` is often a Markdown file in the project's own
repository (Rondo: ``docs/install.md#first-start``). The app reads that file and
shows it in the completion screen under the donations appeal, so the reader
stays in the app. A link to any other kind of page keeps opening in the
browser. "See More" for a release goes to the site's own ``release_notes.html``,
which renders the release title and notes with our styling and our own
download buttons.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtWidgets import QApplication

from src import docs_source, player_update_check
from src.browser import _with_donation_preference
from src.markdown_render import render_markdown_html, strip_front_matter
from src.ui.flash_page import FlashPage

RONDO = "https://github.com/jarvissax/rondo/blob/main/docs/install.md#first-start"
GUIDE = """\
---
title: Install
---

# Rondo

Some introduction.

## First start

1. Copy the themes onto the player.
2. Reboot.

![Step one](images/step-1.png)

| Setting | Value |
| --- | --- |
| Theme | Rondo |
"""


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def doc_cache(tmp_path, monkeypatch):
    """Point the document cache at a temporary folder."""
    monkeypatch.setattr(docs_source, "cache_dir", lambda: tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# Which links we can read
# --------------------------------------------------------------------------

def test_github_blob_links_resolve_to_raw_markdown():
    assert docs_source.markdown_source(RONDO) == (
        "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/install.md"
    )
    assert docs_source.markdown_anchor(RONDO) == "first-start"
    assert docs_source.repo_from_url(RONDO) == "jarvissax/rondo"
    assert docs_source.base_url_for(docs_source.markdown_source(RONDO)) == (
        "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/"
    )


def test_raw_and_plain_markdown_links_are_sources():
    raw = "https://raw.githubusercontent.com/o/r/main/docs/install.md"
    assert docs_source.markdown_source(raw) == raw
    plain = "https://example.test/guide/install.md?raw=1"
    assert docs_source.markdown_source(plain) == plain
    assert docs_source.markdown_source("https://example.test/guide/install.markdown")


def test_other_web_pages_are_not_markdown_sources():
    # The Reborn post-install page is a rendered guide, not a file we can parse.
    assert docs_source.markdown_source("https://innioasis.app/y2reborn_wifibt.html") == ""
    # A release page is not a document either.
    assert docs_source.markdown_source("https://github.com/o/r/releases/tag/v1.0.0") == ""
    assert docs_source.markdown_source("https://github.com/o/r") == ""
    assert docs_source.markdown_source("") == ""


def test_markdown_links_ignore_the_fragment_when_fetching():
    assert docs_source.markdown_source(RONDO).endswith("install.md")


# --------------------------------------------------------------------------
# Preparing a document
# --------------------------------------------------------------------------

def test_relative_images_and_links_are_resolved_against_the_file():
    base = docs_source.base_url_for(docs_source.markdown_source(RONDO))
    text = docs_source.rewrite_relative_urls(
        "![a](images/x.png)\n"
        "[b](other.md)\n"
        "[c](../README.md)\n"
        "[d](https://example.test/keep)\n"
        "[e](#local)\n"
        '[f](/assets/root.png)\n'
        '<img src="images/y.png">',
        base,
    )
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/images/x.png" in text
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/other.md" in text
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/README.md" in text
    assert "https://example.test/keep" in text
    assert "(#local)" in text
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/assets/root.png" in text
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/images/y.png" in text


def test_front_matter_is_not_shown_to_the_reader():
    assert strip_front_matter(GUIDE).lstrip().startswith("# Rondo")
    prepared = docs_source.prepare_markdown(GUIDE)
    assert "title: Install" not in prepared


def test_a_link_section_finds_its_heading():
    assert docs_source.heading_text_for_anchor(GUIDE, "first-start") == "First start"
    assert docs_source.heading_text_for_anchor(GUIDE, "#first-start") == "First start"
    # An anchor that names no heading does not stop the document opening.
    assert docs_source.heading_text_for_anchor(GUIDE, "nowhere") == ""


# --------------------------------------------------------------------------
# Fetching and caching
# --------------------------------------------------------------------------

def test_a_downloaded_guide_is_cached_for_offline_reading(doc_cache):
    calls = []

    def fetch(url, timeout=0):
        calls.append(url)
        return GUIDE

    assert docs_source.fetch_markdown(RONDO, fetch=fetch).startswith("---")
    assert docs_source.cached_markdown(docs_source.markdown_source(RONDO))

    def offline(url, timeout=0):
        raise RuntimeError("no connection")

    # Still fresh: no second request is made.
    assert docs_source.fetch_markdown(RONDO, fetch=offline).startswith("---")
    assert len(calls) == 1
    # Forced refresh that fails keeps the copy we already have.
    assert docs_source.fetch_markdown(RONDO, fetch=offline, force=True).startswith("---")


def test_an_html_error_page_is_not_treated_as_a_guide(doc_cache):
    html = docs_source.fetch_markdown(
        RONDO, fetch=lambda url, timeout=0: "<!DOCTYPE html><html>Not found</html>"
    )
    assert html == ""
    assert docs_source.cached_markdown(docs_source.markdown_source(RONDO)) == ""


def test_a_failed_first_fetch_is_empty(doc_cache):
    def offline(url, timeout=0):
        raise RuntimeError("no connection")

    assert docs_source.fetch_markdown(RONDO, fetch=offline) == ""


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def test_markdown_keeps_tables_and_images_and_drops_colours():
    html = render_markdown_html(
        '<span style="color:#ff0000">red</span>\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n'
        "![shot](https://example.test/x.png)\n",
        text_color="#123456",
    )
    assert "<table" in html
    assert "x.png" in html
    assert "#ff0000" not in html
    assert "#123456" in html


# --------------------------------------------------------------------------
# The site's own pages
# --------------------------------------------------------------------------

def test_release_notes_link_names_the_site_page():
    url = docs_source.release_notes_url(
        "https://github.com/y1-community/y1-stock-rom.git", "v3.1.2",
        name="Original Software", model="y1",
    )
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://innioasis.app/release_notes.html"
    query = parse_qs(parts.query)
    assert query["repo"] == ["y1-community/y1-stock-rom"]
    assert query["tag"] == ["v3.1.2"]
    assert query["name"] == ["Original Software"]
    assert query["model"] == ["Y1"]
    # A release the site cannot name keeps github.com as its page.
    assert docs_source.release_notes_url("", "v1") == ""


def test_post_install_page_carries_the_document_and_its_section():
    url = docs_source.post_install_page_url(
        RONDO, software="Rondo", model="Y1"
    )
    parts = urlsplit(url)
    assert parts.path == "/post_install.html"
    query = parse_qs(parts.query)
    assert query["url"] == ["https://raw.githubusercontent.com/jarvissax/rondo/main/docs/install.md"]
    assert query["section"] == ["first-start"]
    assert query["repo"] == ["jarvissax/rondo"]
    assert query["software"] == ["Rondo"]
    assert query["model"] == ["Y1"]
    # A non-Markdown post_install keeps its own page.
    assert docs_source.post_install_page_url("https://innioasis.app/y2reborn_wifibt.html") == ""


def test_site_links_carry_the_donations_preference(monkeypatch):
    monkeypatch.setattr(
        player_update_check, "is_donation_ui_disabled", lambda: True
    )
    url = _with_donation_preference(docs_source.post_install_page_url(RONDO))
    assert parse_qs(urlsplit(url).query)["donations"] == ["false"]
    notes = _with_donation_preference(docs_source.release_notes_url("a/b", "v1"))
    assert parse_qs(urlsplit(notes).query)["donations"] == ["false"]


# --------------------------------------------------------------------------
# The completion screen
# --------------------------------------------------------------------------

def test_completion_screen_shows_markdown_steps_in_the_app(qapp, monkeypatch):
    monkeypatch.setattr(docs_source, "cached_markdown", lambda url: GUIDE)
    page = FlashPage()
    page.set_post_install_urls(RONDO, "")
    page.set_device_done(donations_shown=False)

    assert not page._post_install_doc.isHidden()
    assert page._post_install_hint.isHidden()
    text = page._post_install_doc.toPlainText()
    assert "Rondo" in text and "First start" in text
    assert "Copy the themes" in text
    # The guide opens at the section the manifest links to.
    assert page._post_install_doc.scroll_to_anchor("first-start")


LONG_GUIDE = (
    "# Rondo install guide\n\nIntro. "
    + ("Words. " * 60)
    + "\n\n## Before you start\n\n- one\n- two\n"
    + ("filler\n\n" * 40)
    + "\n## First start\n\n1. Copy the themes.\n2. Reboot.\n\n## Later\n\nMore.\n"
)


def _shown_completion_page(qapp, monkeypatch, document):
    """A real, shown completion screen with ``document`` as its guide."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    monkeypatch.setattr(docs_source, "cached_markdown", lambda url: document)
    host = QWidget()
    host.resize(780, 640)
    box = QVBoxLayout(host)
    box.setContentsMargins(0, 0, 0, 0)
    page = FlashPage()
    box.addWidget(page)
    host.show()
    qapp.processEvents()
    page.set_post_install_urls(RONDO, "")
    page.set_device_done(donations_shown=False)
    page.show_flashing()
    for _ in range(3):
        qapp.processEvents()
    # The host owns the page: a collected host deletes the view these
    # assertions read, so it is kept alive for as long as the page is.
    page._test_host = host
    return page, page._post_install_doc


def _line_is_on_screen(view, text):
    cursor = view.document().find(text)
    assert not cursor.isNull()
    top = view.cursorRect(cursor).top()
    return 0 <= top < view.viewport().height()


def _line_is_at_top(view, text):
    from PySide6.QtCore import QPoint

    return text in view.cursorForPosition(QPoint(4, 4)).block().text()


def test_the_guide_arrives_at_the_linked_section(qapp, monkeypatch):
    """A link to ``#first-start`` opens on that section, not on the intro.

    Rondo's manifest points at its final install steps, which sit at the end of
    its guide: the pane scrolls to the last screenful, and the heading the link
    names is on it.
    """
    page, view = _shown_completion_page(qapp, monkeypatch, LONG_GUIDE)
    assert _line_is_on_screen(view, "First start")


def test_a_section_with_content_after_it_is_pinned_to_the_top(qapp, monkeypatch):
    """With document left below it, the linked heading is the top line."""
    middle = (
        "# Guide\n\nIntro. "
        + ("Words. " * 60)
        + "\n\n## First start\n\n1. Copy the themes.\n2. Reboot.\n"
        + ("more notes\n\n" * 60)
    )
    page, view = _shown_completion_page(qapp, monkeypatch, middle)
    assert _line_is_at_top(view, "First start")


def test_completion_screen_keeps_the_browser_link_for_other_pages(qapp, monkeypatch):
    monkeypatch.setattr(docs_source, "cached_markdown", lambda url: GUIDE)
    page = FlashPage()
    page.set_post_install_urls("https://innioasis.app/y2reborn_wifibt.html", "")
    page.set_device_done(donations_shown=False)

    assert page._post_install_doc.isHidden()
    assert not page._post_install_hint.isHidden()


def test_an_unreadable_guide_says_so_instead_of_showing_a_blank_pane(qapp, monkeypatch):
    from src.i18n import tr

    monkeypatch.setattr(docs_source, "cached_markdown", lambda url: "")
    monkeypatch.setattr(docs_source, "fetch_markdown", lambda url, **kw: "")
    monkeypatch.setattr(
        docs_source, "cache_dir", lambda: __import__("pathlib").Path("/nonexistent-docs")
    )
    page = FlashPage()
    page.set_post_install_urls(RONDO, "")
    page.set_device_done(donations_shown=False)
    page._on_post_install_doc_ready(docs_source.markdown_source(RONDO), "")

    assert page._post_install_doc.isHidden()
    assert not page._post_install_doc_status.isHidden()
    assert page._post_install_doc_status.text() == tr("post_install_doc_unavailable")


def test_the_globe_opens_the_site_reader_for_a_markdown_guide(qapp, monkeypatch):
    opened = []
    from src import browser

    monkeypatch.setattr(browser, "open_browser", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setattr(player_update_check, "is_donation_ui_disabled", lambda: True)
    monkeypatch.setattr(docs_source, "cached_markdown", lambda url: GUIDE)

    page = FlashPage()
    page.set_post_install_urls(RONDO, "")
    page._open_post_install_guide()

    assert opened and opened[-1].startswith("https://innioasis.app/post_install.html?")
    assert "first-start" in opened[-1]
    assert "donations=false" in opened[-1]
