"""The static feeds innioasis.app publishes must survive Updater CE's parser.

``scripts/generate_firmware_list.py`` in the website repository writes
``feeds/firmware-releases.json`` and ``feeds/firmware-feed.xml`` from
``slidia_manifest.xml`` plus the published release data. Those files are what
lets the app keep working when the GitHub API refuses a connection, so the
shape here is a contract: the fixtures below are byte-for-byte copies of what
that generator emits, and every lookup the firmware flow uses is exercised
against them.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import feed_fallback  # noqa: E402

ATOM = "http://www.w3.org/2005/Atom"
INNI = "https://innioasis.app/ns/firmware/1.0"

SOURCE_ASSET = "https://github.com/rockbox-y1/rockbox/releases/download/stable-v0.5/rom.zip"
MIRROR_TAG = "https://github.com/y1-community/rockbox-mirror/releases/download/stable-v0.5/"

RELEASE = {
    "id": 1,
    "tag_name": "stable-v0.5",
    "name": "Rockbox stable v0.5",
    "body": "Gapless fixes",
    "draft": False,
    "prerelease": False,
    "created_at": "2026-05-01T10:00:00Z",
    "published_at": "2026-05-02T10:00:00Z",
    "html_url": "https://github.com/rockbox-y1/rockbox/releases/tag/stable-v0.5",
    "icon_url": "",
    "icon_dark_url": "",
    "assets": [
        {
            "id": 11,
            "name": "rom.zip",
            "size": 1500,
            "browser_download_url": SOURCE_ASSET,
            "download_count": 42,
            "content_type": "application/zip",
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": "2026-05-01T10:00:00Z",
            "url": "https://api.github.com/repos/rockbox-y1/rockbox/releases/assets/11",
        }
    ],
}

APP_RELEASE = {
    "id": 2,
    "tag_name": "v3.1.0",
    "name": "Updater CE 3.1.0",
    "body": "Installer fixes",
    "draft": False,
    "prerelease": False,
    "created_at": "2026-06-01T10:00:00Z",
    "published_at": "2026-06-02T10:00:00Z",
    "html_url": "https://github.com/y1-community/updater-ce-neo/releases/tag/v3.1.0",
    "assets": [],
}

FEED = {
    "schema_version": 2,
    "generated_at": "2026-11-04T15:00:00Z",
    "manifest_url": "https://innioasis.app/slidia_manifest.xml",
    "firmware_list_url": "https://innioasis.app/firmware-list.html",
    "packages": [
        {
            "name": "Rockbox",
            "repo": "rockbox-y1/rockbox",
            "portable_zips": "y1-community/rockbox-mirror",
            "device": "Y1",
            "url": "https://github.com/rockbox-y1/rockbox",
            "type": "img",
            "handler": "Custom Firmware",
        }
    ],
    "repos": {"rockbox-y1/rockbox": [RELEASE]},
    "mirrors": {
        "y1-community/rockbox-mirror": {
            "version": 1,
            "source_repo": "rockbox-y1/rockbox",
            "mirror_repo": "y1-community/rockbox-mirror",
            "updated_at": "2026-11-04T14:00:00Z",
            "index_url": "https://raw.githubusercontent.com/y1-community/rockbox-mirror/main/index.json",
            "releases": {
                "stable-v0.5": {
                    "original_tag": "stable-v0.5",
                    "mirror_tag": "stable-v0.5",
                    "release_name": "Rockbox stable v0.5",
                    "published_at": "2026-05-02T10:00:00Z",
                    "assets": {
                        "rom.zip": {
                            "original_url": "",
                            "original_size": 1500,
                            "mirror_original_url": MIRROR_TAG + "rom.zip",
                            "packages": {
                                "windows": {
                                    "name": "rom_windows.zip",
                                    "url": MIRROR_TAG + "rom_windows.zip",
                                    "size": 1600,
                                },
                                "linux": {
                                    "name": "rom_linux.zip",
                                    "url": MIRROR_TAG + "rom_linux.zip",
                                    "size": 1610,
                                },
                                "macos": {
                                    "name": "rom_macos.zip",
                                    "url": MIRROR_TAG + "rom_macos.zip",
                                    "size": 1620,
                                },
                            },
                        }
                    },
                }
            },
        }
    },
    "app_updates": {"y1-community/updater-ce-neo": [APP_RELEASE]},
}


def atom_feed() -> str:
    """The same catalogue as ``feeds/firmware-feed.xml`` writes it."""
    ET.register_namespace("", ATOM)
    ET.register_namespace("inni", INNI)
    feed = ET.Element("{%s}feed" % ATOM)
    ET.SubElement(feed, "{%s}title" % ATOM).text = "Innioasis Firmware Archive"
    ET.SubElement(feed, "{%s}updated" % ATOM).text = FEED["generated_at"]

    pkgs = ET.SubElement(feed, "{%s}packages" % INNI)
    for pkg in FEED["packages"]:
        el = ET.SubElement(pkgs, "{%s}package" % INNI)
        for key, value in pkg.items():
            el.set(key, value)

    mirrors = ET.SubElement(feed, "{%s}mirrors" % INNI)
    for mirror_repo, entry in FEED["mirrors"].items():
        m_el = ET.SubElement(mirrors, "{%s}mirror" % INNI)
        m_el.set("repo", mirror_repo)
        m_el.set("source_repo", entry["source_repo"])
        m_el.set("updated_at", entry["updated_at"])
        for tag, rel in entry["releases"].items():
            for asset_name, bucket in rel["assets"].items():
                ma = ET.SubElement(m_el, "{%s}mirror_asset" % INNI)
                ma.set("mirror_repo", mirror_repo)
                ma.set("name", asset_name)
                ma.set("tag", tag)
                ma.set("mirror_tag", tag)
                ma.set("original_tag", rel["original_tag"])
                ma.set("original_size", str(bucket["original_size"]))
                if bucket.get("mirror_original_url"):
                    ma.set("mirror_original_url", bucket["mirror_original_url"])
                for platform, info in bucket["packages"].items():
                    p_el = ET.SubElement(ma, "{%s}package" % INNI)
                    p_el.set("platform", platform)
                    p_el.set("name", info["name"])
                    p_el.set("url", info["url"])
                    p_el.set("size", str(info["size"]))

    for repo, releases in FEED["repos"].items():
        for rel in releases:
            entry = ET.SubElement(feed, "{%s}entry" % ATOM)
            ET.SubElement(entry, "{%s}id" % ATOM).text = "urn:innioasis:%s:%s" % (
                repo,
                rel["tag_name"],
            )
            ET.SubElement(entry, "{%s}repo" % INNI).text = repo
            ET.SubElement(entry, "{%s}tag_name" % INNI).text = rel["tag_name"]
            ET.SubElement(entry, "{%s}name" % ATOM).text = rel["name"]
            ET.SubElement(entry, "{%s}published_at" % INNI).text = rel["published_at"]
            ET.SubElement(entry, "{%s}prerelease" % INNI).text = "false"
            ET.SubElement(entry, "{%s}html_url" % INNI).text = rel["html_url"]
            ET.SubElement(entry, "{%s}body" % INNI).text = rel["body"]
            for asset in rel["assets"]:
                link = ET.SubElement(entry, "{%s}link" % ATOM)
                link.set("rel", "enclosure")
                link.set("href", asset["browser_download_url"])
                link.set("name", asset["name"])
                link.set("size", str(asset["size"]))
                link.set("type", asset["content_type"])

    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        feed, encoding="unicode"
    )


def test_json_feed_keeps_every_manifest_field():
    parsed = feed_fallback.parse_feed_json(json.dumps(FEED))
    assert parsed is not None
    assert parsed["generated_at"] == FEED["generated_at"]
    assert parsed["firmware_list_url"] == "https://innioasis.app/firmware-list.html"
    pkg = parsed["packages"][0]
    # Every attribute the installer reads must survive, portable mirror included.
    assert pkg["repo"] == "rockbox-y1/rockbox"
    assert pkg["device"] == "Y1"
    assert pkg["portable_zips"] == "y1-community/rockbox-mirror"
    assert pkg["handler"] == "Custom Firmware"


def test_json_feed_normalises_release_assets():
    parsed = feed_fallback.parse_feed_json(FEED)
    release = parsed["repos"]["rockbox-y1/rockbox"][0]
    assert release["tag_name"] == "stable-v0.5"
    assert release["published_at"] == RELEASE["published_at"]
    assert release["assets"][0]["browser_download_url"] == SOURCE_ASSET
    assert release["assets"][0]["size"] == 1500


def test_mirror_release_exposes_portable_packages():
    """A mirror tag carries the original file *and* one package per platform."""
    parsed = feed_fallback.parse_feed_json(FEED)
    mirror = parsed["mirrors"]["y1-community/rockbox-mirror"]
    assert mirror["source_repo"] == "rockbox-y1/rockbox"
    raw = mirror["raw_releases"]
    assert len(raw) == 1
    names = {asset["name"] for asset in raw[0]["assets"]}
    assert names == {"rom.zip", "rom_windows.zip", "rom_linux.zip", "rom_macos.zip"}
    urls = {asset["name"]: asset["browser_download_url"] for asset in raw[0]["assets"]}
    assert urls["rom_linux.zip"] == MIRROR_TAG + "rom_linux.zip"


def test_lookups_the_install_flow_uses():
    feed = feed_fallback.fetch_fallback_feed  # noqa: F841  (documented entry point)
    parsed = feed_fallback.parse_feed_json(FEED)

    assert feed_fallback.get_packages_from_feed(feed=parsed)[0]["name"] == "Rockbox"
    # Case-insensitive on purpose: manifest casing and feed casing can differ.
    assert feed_fallback.get_repo_releases_from_feed(
        "ROCKBOX-Y1/Rockbox", feed=parsed
    )[0]["tag_name"] == "stable-v0.5"
    assert feed_fallback.get_mirror_from_feed(
        "y1-community/rockbox-mirror", feed=parsed
    )["releases"]["stable-v0.5"]["assets"]["rom.zip"]["packages"]["windows"]["size"] == 1600
    assert feed_fallback.get_repo_releases_from_feed(
        "nobody/nothing", feed=parsed
    ) == []


def test_app_updates_are_discoverable():
    parsed = feed_fallback.parse_feed_json(FEED)
    releases = feed_fallback.get_app_update_releases_from_feed(
        "y1-community/updater-ce-neo", feed=parsed
    )
    assert releases[0]["tag_name"] == "v3.1.0"


def test_atom_feed_matches_the_json_feed():
    """The XML fallback must answer the same questions as the JSON one."""
    parsed = feed_fallback.parse_feed_xml(atom_feed())
    assert parsed is not None
    assert parsed["packages"][0]["portable_zips"] == "y1-community/rockbox-mirror"
    assert parsed["repos"]["rockbox-y1/rockbox"][0]["tag_name"] == "stable-v0.5"
    assert (
        parsed["repos"]["rockbox-y1/rockbox"][0]["assets"][0]["browser_download_url"]
        == SOURCE_ASSET
    )
    mirror = parsed["mirrors"]["y1-community/rockbox-mirror"]
    assert mirror["releases"]["stable-v0.5"]["assets"]["rom.zip"]["packages"]["macos"][
        "name"
    ] == "rom_macos.zip"


def test_json_feed_rejects_an_html_error_page():
    """A captive portal must not look like a catalogue."""
    assert feed_fallback.parse_feed_json("<!doctype html><html>blocked</html>") is None


def test_unknown_feed_shape_is_rejected():
    assert feed_fallback.parse_feed_json(json.dumps({"hello": "world"})) is None


def test_offline_falls_back_to_the_stale_disk_cache(tmp_path, monkeypatch):
    """With every host unreachable, the last good copy still serves the catalogue."""
    cache_file = tmp_path / feed_fallback.FEED_CACHE_FILENAME
    cache_file.write_text(
        json.dumps({"timestamp": 0, "feed": FEED}), encoding="utf-8"
    )
    monkeypatch.setattr(feed_fallback, "cache_path", lambda: cache_file)
    monkeypatch.setattr(feed_fallback, "_fetch_url_text", lambda *a, **k: None)
    feed_fallback.clear_memory_cache()

    parsed = feed_fallback.fetch_fallback_feed(force_refresh=True)

    assert parsed is not None
    assert parsed["repos"]["rockbox-y1/rockbox"][0]["tag_name"] == "stable-v0.5"
    feed_fallback.clear_memory_cache()


def test_no_network_and_no_cache_returns_none(tmp_path, monkeypatch):
    """The caller must be able to detect 'nothing worked' and say so."""
    monkeypatch.setattr(
        feed_fallback, "cache_path", lambda: tmp_path / "absent.json"
    )
    monkeypatch.setattr(feed_fallback, "_fetch_url_text", lambda *a, **k: None)
    feed_fallback.clear_memory_cache()

    assert feed_fallback.fetch_fallback_feed(force_refresh=True) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
