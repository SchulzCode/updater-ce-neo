"""Y2Linux releases use baseline tags and a first-install zip.

Their versions are in the release title, and the release also carries source
archives. The catalogue must use the install zip, keep the release for Y2, and
show the title's semantic version rather than the build tag.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import catalog  # noqa: E402
from src.catalog import FirmwarePackage, ReleasesClient  # noqa: E402

REPO = "schulzcode/y2linux"
PACKAGE = FirmwarePackage(
    "y2reborn-y2",
    "Y2Reborn (Y2Linux)",
    "Y2",
    REPO,
    "rom_y2.zip",
)


def _asset(name):
    return {
        "name": name,
        "browser_download_url": f"https://example.invalid/{name}",
        "size": 100,
    }


def _release(tag, title, published):
    version = title.rsplit(" ", 1)[-1]
    return {
        "tag_name": tag,
        "name": title,
        "body": "",
        "published_at": published,
        "prerelease": False,
        "assets": [
            _asset(f"Y2Linux-1.0.0-beta.{version}-first-install.zip"),
            _asset(f"Y2Linux-1.0.0-beta.{version}-sources-buildroot-legal-info.tar"),
            _asset("Y2Linux-sources-third-party-16db0acea92d.tar"),
            _asset("bootimg.img.gz"),
        ],
    }


def _rows():
    client = ReleasesClient.__new__(ReleasesClient)
    return client._build_releases(
        [
            _release("baseline-204", "Y2Linux 1.0 Beta 9.2.13", "2026-10-29T18:00:00Z"),
            _release("baseline-203", "Y2Linux 1.0 Beta 9.2.12", "2026-10-28T18:00:00Z"),
        ],
        REPO,
    )


def test_y2linux_install_zip_is_identified_as_a_y2_asset():
    asset = _asset("Y2Linux-1.0.0-beta.9.2.13-first-install.zip")
    variant = catalog._parse_rom_asset_variant(asset, "baseline-204", REPO)
    assert variant is not None
    assert variant["model"] == "Y2"


def test_source_archives_are_not_install_candidates():
    sources = [
        _asset("Y2Linux-1.0.0-beta.9.2.13-sources.zip"),
        _asset("Y2Linux-sources-third-party-16db0acea92d.tar"),
    ]
    assert catalog._release_rom_variants(sources, "baseline-204", REPO) == []


def test_y2linux_releases_list_with_the_title_version():
    rows = _rows()
    assert [row["tag_name"] for row in rows] == ["baseline-204", "baseline-203"]
    assert all(
        row["asset_name"].endswith("-first-install.zip") for row in rows
    )
    assert "9.2.13" in rows[0]["asset_name"]
    assert "9.2.12" in rows[1]["asset_name"]

    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: rows
    listed = client.releases_for_package(PACKAGE, "Y2")

    assert [row["tag_name"] for row in listed] == ["baseline-204", "baseline-203"]
    assert [
        catalog.parse_clean_release_tag_label(row) for row in listed
    ] == ["9.2.13", "9.2.12"]
    assert [
        catalog.format_release_display_label(row) for row in listed
    ] == ["9.2.13", "9.2.12"]


def test_title_versions_drive_newest_first_order():
    rows = _rows()
    ordered = sorted(rows, key=catalog.release_sort_key, reverse=True)
    assert [row["tag_name"] for row in ordered] == ["baseline-204", "baseline-203"]
