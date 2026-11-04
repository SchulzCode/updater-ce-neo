"""Portable Flashing Zip Packager mirrors are usable as catalogue sources.

A mirror republishes each firmware release under the same tag, keeping the
original zip and adding one ready-to-run package per platform
(``rom.zip`` -> ``rom-linux.zip``, ``rom-macos.zip``, ...). A catalogue entry
that names its mirror in ``portable_zips`` must download the package built for
the machine it runs on, keep the release list, notes and version labels from
the source release, and fall back to the original zip when a mirror has no
build for that platform.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import catalog, manifest, portable  # noqa: E402
from src.catalog import FirmwarePackage, ReleasesClient  # noqa: E402

MIRROR = "someone/y1-stock-rom-portable"
REPO = "y1-community/y1-stock-rom"
TAG = "3.1.2"


@pytest.fixture(autouse=True)
def _fresh_index_cache():
    portable.clear_cache()
    yield
    portable.clear_cache()


def _platform(key, os_name, arch, suffix):
    return {"key": key, "os": os_name, "arch": arch, "suffix": suffix, "layout": "sp_flash_tool"}


def _index(assets, platforms=None, tag=TAG):
    return {
        "schema": 1,
        "generated_at": "2026-11-03T00:00:00Z",
        "source_repo": REPO,
        "mirror_repo": MIRROR,
        "title_prefix": "Portable Flashing Package: ",
        "platforms": platforms
        if platforms is not None
        else [
            _platform("windows", "windows", "x86-64", "-windows.zip"),
            _platform("windows-arm64", "windows", "arm64", "-windows-arm64.zip"),
            _platform("linux", "linux", "x86-64", "-linux.zip"),
            _platform("linux-arm64", "linux", "arm64", "-linux-arm64.zip"),
            _platform("macos", "macos", "universal", "-macos.zip"),
        ],
        "releases": [{"tag": tag, "assets": assets}],
    }


def _asset(platform, name, source_asset="rom.zip", size=42):
    return {
        "name": name,
        "source_asset": source_asset,
        "platform": platform,
        "size": size,
        "sha256": "0" * 64,
        "url": f"https://github.com/{MIRROR}/releases/download/{TAG}/{name}",
    }


def _serve(index, calls=None):
    def fetch(url, timeout=0):
        if calls is not None:
            calls.append(url)
        return json.dumps(index)

    return fetch


# --------------------------------------------------------------------------
# Platform keys
# --------------------------------------------------------------------------

def test_platform_keys_follow_the_packager_contract():
    assert portable.platform_key("win32", "AMD64") == "windows"
    assert portable.platform_key("win32", "ARM64") == "windows-arm64"
    assert portable.platform_key("linux", "x86_64") == "linux"
    assert portable.platform_key("linux", "aarch64") == "linux-arm64"
    # macOS packages are universal whatever the CPU reports.
    assert portable.platform_key("darwin", "arm64") == "macos"
    assert portable.platform_key("darwin", "x86_64") == "macos"
    assert portable.platform_key("freebsd", "amd64") == ""


def test_x86_64_windows_under_prism_reports_the_native_machine(monkeypatch):
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.setenv("PROCESSOR_ARCHITEW6432", "ARM64")
    assert portable.platform_key("win32", "AMD64") == "windows-arm64"
    monkeypatch.delenv("PROCESSOR_ARCHITEW6432")
    assert portable.platform_key("win32", "AMD64") == "windows"


def test_powerpc_and_32_bit_intel_macs_have_no_portable_package():
    for machine in ("PowerPC", "ppc", "i386", "i686"):
        assert portable.platform_key("darwin", machine) == "", machine
    assert portable.platform_key("darwin", "arm64") == "macos"
    assert portable.platform_key("darwin", "x86_64") == "macos"


# --------------------------------------------------------------------------
# Mirror lists
# --------------------------------------------------------------------------

def test_mirror_list_accepts_urls_and_lists():
    assert portable.parse_mirror_repos(
        "https://github.com/someone/y1-stock-rom-portable.git, other/mirror"
    ) == [MIRROR, "other/mirror"]
    assert portable.parse_mirror_repos(["someone/a", " someone/a ", "someone/b"]) == [
        "someone/a",
        "someone/b",
    ]
    assert portable.parse_mirror_repos("") == []
    assert portable.parse_mirror_repos("not-a-repo") == []


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def _four_platform_index(tag=TAG):
    return _index(
        [
            _asset("windows", "rom-windows.zip"),
            _asset("linux", "rom-linux.zip"),
            _asset("linux-arm64", "rom-linux-arm64.zip"),
            _asset("macos", "rom-macos.zip"),
        ],
        tag=tag,
    )


def test_each_platform_resolves_to_its_own_build():
    fetch = _serve(_four_platform_index())
    expected = {
        "windows": "rom-windows.zip",
        "linux": "rom-linux.zip",
        "linux-arm64": "rom-linux-arm64.zip",
        "macos": "rom-macos.zip",
    }
    for platform, name in expected.items():
        match = portable.resolve([MIRROR], TAG, "rom.zip", platform=platform, fetch=fetch)
        assert match is not None, platform
        assert match["name"] == name
        assert match["platform"] == platform
        assert match["url"].endswith(f"/{name}")
        assert match["size"] == 42


def test_an_arm64_host_never_gets_the_x86_64_build():
    fetch = _serve(_index([_asset("linux", "rom-linux.zip")]))
    assert portable.resolve([MIRROR], TAG, "rom.zip", platform="linux-arm64", fetch=fetch) is None


def test_an_x86_64_host_never_gets_the_arm64_build():
    fetch = _serve(_index([_asset("linux-arm64", "rom-linux-arm64.zip")]))
    assert portable.resolve([MIRROR], TAG, "rom.zip", platform="linux", fetch=fetch) is None


def test_a_tag_the_mirror_has_not_built_yet_falls_back():
    fetch = _serve(_four_platform_index(tag="3.1.1"))
    assert portable.resolve([MIRROR], TAG, "rom.zip", platform="linux", fetch=fetch) is None


def test_the_asset_must_come_from_the_requested_source_zip():
    index = _index([_asset("linux", "rom_y2-linux.zip", source_asset="rom_y2.zip")])
    fetch = _serve(index)
    assert portable.resolve([MIRROR], TAG, "rom.zip", platform="linux", fetch=fetch) is None
    match = portable.resolve([MIRROR], TAG, "rom_y2.zip", platform="linux", fetch=fetch)
    assert match is not None and match["name"] == "rom_y2-linux.zip"


def test_the_second_mirror_is_tried_when_the_first_has_nothing():
    first = _serve(_four_platform_index(tag="0.0.1"))
    calls = []

    def fetch(url, timeout=0):
        calls.append(url)
        if "someone/y1-stock-rom-portable" in url:
            return first(url)
        return json.dumps(_four_platform_index())

    match = portable.resolve(
        [MIRROR, "other/mirror"], TAG, "rom.zip", platform="linux", fetch=fetch
    )
    assert match is not None
    assert match["repo"] == "other/mirror"
    assert len(calls) == 2


def test_an_index_without_source_asset_uses_the_naming_rule():
    index = _index([{"name": "rom-linux-arm64.zip", "platform": "linux-arm64", "size": 7}])
    fetch = _serve(index)
    match = portable.resolve([MIRROR], TAG, "rom.zip", platform="linux-arm64", fetch=fetch)
    assert match is not None
    assert match["name"] == "rom-linux-arm64.zip"
    assert match["size"] == 7
    # The download URL is rebuilt when the index omits it.
    assert match["url"] == f"https://github.com/{MIRROR}/releases/download/{TAG}/rom-linux-arm64.zip"


def test_a_broken_index_is_ignored_without_raising():
    match = portable.resolve(
        [MIRROR], TAG, "rom.zip", platform="linux", fetch=lambda url, timeout=0: "<html>nope</html>"
    )
    assert match is None


def test_the_index_is_fetched_once_per_lookup():
    calls = []
    fetch = _serve(_four_platform_index(), calls)
    for platform in ("linux", "macos", "windows"):
        portable.resolve([MIRROR], TAG, "rom.zip", platform=platform, fetch=fetch)
    assert len(calls) == 1


# --------------------------------------------------------------------------
# Catalogue rows
# --------------------------------------------------------------------------

def _release(assets, tag=TAG):
    return {
        "tag_name": tag,
        "name": "3.1.2",
        "body": "",
        "published_at": "2026-11-01T00:00:00Z",
        "prerelease": False,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://example.invalid/{name}",
                "size": 100,
            }
            for name in assets
        ],
    }


def _package(zips=""):
    return FirmwarePackage(
        "original-y1",
        "Original Software",
        "Y1",
        REPO,
        "rom.zip",
        portable_zips=zips,
    )


def _rows(assets=("rom.zip",)):
    client = ReleasesClient.__new__(ReleasesClient)
    return client._build_releases([_release(list(assets))], REPO)


def test_a_mirror_release_is_listed_by_its_original_asset():
    rows = _rows(("rom.zip", "rom-windows.zip", "rom-linux.zip", "rom-macos.zip", "stats.txt"))
    assert [row["asset_name"] for row in rows] == ["rom.zip"]


def test_portable_bundles_are_kept_when_they_are_the_only_zips():
    assets = [_release(["rom-linux.zip", "rom-macos.zip"])["assets"][0], _release(["rom-linux.zip", "rom-macos.zip"])["assets"][1]]
    variants = catalog._release_rom_variants(assets, TAG, MIRROR)
    assert [variant["asset"]["name"] for variant in variants] == [
        "rom-linux.zip",
        "rom-macos.zip",
    ]


def test_catalogue_rows_link_the_portable_package_and_keep_their_identity(monkeypatch):
    monkeypatch.setattr(portable, "_fetch_text", _serve(_four_platform_index()))
    monkeypatch.setattr(portable, "platform_key", lambda system=None, machine=None: "linux")

    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: _rows()
    rows = client.releases_for_package(_package(MIRROR), "Y1")

    assert len(rows) == 1
    row = rows[0]
    # Updater CE always downloads firmware directly from the community
    # firmware developer's release URL, while recording portable mirror
    # metadata separately on the row.
    assert row["download_url"] == "https://example.invalid/rom.zip"
    assert row["portable_url"].endswith("/rom-linux.zip")
    assert row["asset_name"] == "rom.zip"
    assert row["portable_repo"] == MIRROR
    assert row["portable_platform"] == "linux"
    assert row["portable_asset"] == "rom-linux.zip"
    assert row["tag_name"] == TAG


def test_a_package_without_a_mirror_is_untouched(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("no mirror should be consulted")

    monkeypatch.setattr(portable, "_fetch_text", explode)
    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: _rows()
    rows = client.releases_for_package(_package(), "Y1")
    assert rows[0]["download_url"] == "https://example.invalid/rom.zip"
    assert "portable_platform" not in rows[0]


def test_a_platform_without_a_build_keeps_the_original_download(monkeypatch):
    monkeypatch.setattr(
        portable,
        "_fetch_text",
        _serve(_index([_asset("macos", "rom-macos.zip")])),
    )
    monkeypatch.setattr(portable, "platform_key", lambda system=None, machine=None: "windows")

    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: _rows()
    rows = client.releases_for_package(_package(MIRROR), "Y1")
    assert rows[0]["download_url"] == "https://example.invalid/rom.zip"


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def test_the_manifest_reads_the_mirror_list():
    xml = (
        '<slidia>'
        '<package name="Original Software" repo="y1-community/y1-stock-rom" '
        'device="Y1" type="img" handler="Custom Firmware" '
        'portable_zips="https://github.com/someone/y1-stock-rom-portable.git, other/mirror" />'
        '<package name="Rockbox" repo="rockbox-y1/rockbox" device="Y1" type="img" '
        'handler="Custom Firmware" portable_zip="someone/rockbox-mirror" />'
        '<package name="Solar" repo="thesolarproject/solar" device="Y1" type="img" '
        'handler="Custom Firmware" />'
        '</slidia>'
    )
    entries = manifest.parse_manifest_xml(xml)
    assert [e.portable_zips for e in entries] == [
        f"{MIRROR}, other/mirror",
        "someone/rockbox-mirror",
        "",
    ]


def test_the_manifest_cache_remembers_the_mirror_list(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path)
    entry = FirmwarePackage(
        "original-software-y1",
        "Original Software",
        "Y1",
        REPO,
        "rom.zip",
        portable_zips=MIRROR,
    )
    manifest.cache_manifest([entry])

    restored = manifest.load_cached_manifest()
    assert restored is not None
    assert restored[0].portable_zips == MIRROR


# --------------------------------------------------------------------------
# Text links on release notes / elsewhere
# --------------------------------------------------------------------------

def _link(name="rom.zip", tag=TAG, repo=REPO):
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def test_a_text_link_to_a_rom_zip_resolves_to_the_portable_package(monkeypatch):
    monkeypatch.setattr(manifest, "load_cached_manifest", lambda: [_package(MIRROR)])
    monkeypatch.setattr(catalog, "_catalog_packages", lambda: [])
    fetch = _serve(_four_platform_index())

    resolved = portable.resolve_download_link(_link(), platform="macos", fetch=fetch)

    assert resolved == f"https://github.com/{MIRROR}/releases/download/{TAG}/rom-macos.zip"


def test_a_mac_without_a_portable_build_keeps_the_plain_link(monkeypatch):
    monkeypatch.setattr(manifest, "load_cached_manifest", lambda: [_package(MIRROR)])
    monkeypatch.setattr(catalog, "_catalog_packages", lambda: [])

    def explode(*_args, **_kwargs):
        raise AssertionError("no mirror should be consulted")

    # PowerPC / 32-bit Intel: platform_key() returned nothing.
    assert portable.resolve_download_link(
        _link(), platform=portable.platform_key("darwin", "ppc"), fetch=explode
    ) == ""


def test_only_original_zip_links_are_rewritten(monkeypatch):
    monkeypatch.setattr(manifest, "load_cached_manifest", lambda: [_package(MIRROR)])
    monkeypatch.setattr(catalog, "_catalog_packages", lambda: [])

    def explode(*_args, **_kwargs):
        raise AssertionError("no mirror should be consulted")

    assert portable.resolve_download_link(_link("notes.txt"), platform="macos", fetch=explode) == ""
    assert portable.resolve_download_link(_link("rom-macos.zip"), platform="macos", fetch=explode) == ""
    assert portable.resolve_download_link(
        "https://innioasis.app/firmware.html", platform="macos", fetch=explode
    ) == ""


def test_a_link_without_a_configured_mirror_is_untouched(monkeypatch):
    monkeypatch.setattr(manifest, "load_cached_manifest", lambda: [])
    monkeypatch.setattr(catalog, "_catalog_packages", lambda: [])

    def explode(*_args, **_kwargs):
        raise AssertionError("no mirror should be consulted")

    assert portable.resolve_download_link(_link(), platform="macos", fetch=explode) == ""


def test_the_browser_funnel_uses_the_portable_resolver(monkeypatch):
    from src import browser

    monkeypatch.setattr(
        portable,
        "resolve_download_link",
        lambda url, **_kwargs: "https://mirror.invalid/rom-linux.zip",
    )
    assert browser._portable_download(_link()).endswith("rom-linux.zip")
    assert browser._portable_download("https://innioasis.app/guide.html") == (
        "https://innioasis.app/guide.html"
    )
