"""Asset-selection rules for repositories that serve more than one model.

Inniclassic's Y2 repository republishes the legacy Y1 zips (rom.zip,
rom_type_b.zip, rom_y1_*.zip) beside its own rom_y2.zip. The classifier reads
the repository name as Y2, and that used to hand the unqualified rom.zip to Y2
users. A device selection must always end on the model's own archive.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import catalog, manifest  # noqa: E402
from src.catalog import FirmwarePackage, ReleasesClient  # noqa: E402

REPO_Y2 = "JonaszPotoniec/inniclassic-y2"
TAG = "v2.0.0"
Y2_ASSETS = ("rom.zip", "rom_type_b.zip", "rom_y1_1.0.zip", "rom_y2.zip")


def _asset(name):
    return {
        "name": name,
        "browser_download_url": f"https://example.invalid/{name}",
        "size": 100,
    }


def _release(*names):
    return {
        "tag_name": TAG,
        "name": "Inniclassic 2.0",
        "body": "",
        "published_at": "2026-11-04T00:00:00Z",
        "prerelease": False,
        "assets": [_asset(name) for name in names],
    }


def _package(model="Y2", repo=REPO_Y2, asset_pattern=""):
    return FirmwarePackage(
        f"inniclassic-{model.lower()}",
        "Inniclassic",
        model,
        repo,
        "rom_y2.zip" if model == "Y2" else "rom.zip",
        asset_pattern=asset_pattern,
    )


def _rows(*names, package=None):
    client = ReleasesClient.__new__(ReleasesClient)
    raw = [_release(*names)]
    client.get_all_releases = lambda repo, force_refresh=False: client._build_releases(
        raw, repo
    )
    return client.releases_for_package(package or _package(), "Y2")


def test_a_model_suffixed_zip_beats_an_unqualified_zip_from_the_same_repo():
    variants = catalog._release_rom_variants(
        [_asset(name) for name in Y2_ASSETS], TAG, REPO_Y2
    )
    preferred = catalog.select_preferred_rom_asset(variants, model="Y2")
    assert preferred["asset"]["name"] == "rom_y2.zip"


def test_the_manifest_asset_pattern_pins_the_y2_zip():
    variants = catalog._release_rom_variants(
        [_asset(name) for name in Y2_ASSETS],
        TAG,
        REPO_Y2,
        package=_package(asset_pattern="rom_y2*.zip"),
    )
    assert [v["asset"]["name"] for v in variants] == ["rom_y2.zip"]

    rows = _rows(*Y2_ASSETS, package=_package(asset_pattern="rom_y2*.zip"))
    assert [row["asset_name"] for row in rows] == ["rom_y2.zip"]
    assert rows[0]["download_url"].endswith("/rom_y2.zip")


def test_y2_rows_pick_rom_y2_without_the_manifest_shim_too():
    rows = _rows(*Y2_ASSETS, package=_package())
    assert [row["asset_name"] for row in rows] == ["rom_y2.zip"]
    assert rows[0]["download_url"].endswith("/rom_y2.zip")


def test_a_cached_row_is_re_evaluated_with_the_manifest_shim():
    """A release cache written before the shim must not keep the wrong URL."""
    assets = [_asset(name) for name in Y2_ASSETS]
    cached = [
        {
            "tag_name": TAG,
            "name": "Inniclassic 2.0",
            "body": "",
            "published_at": "2026-11-04T00:00:00Z",
            "prerelease": False,
            "assets": assets,
            # Old cache shape: no model_explicit field, and the unqualified
            # rom.zip was chosen when the row was built.
            "rom_variants": [
                catalog._parse_rom_asset_variant(a, TAG, REPO_Y2) for a in assets
            ],
            "download_url": "https://example.invalid/rom.zip",
            "asset_name": "rom.zip",
            "asset_size": 100,
            "source_repo": REPO_Y2,
        }
    ]
    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: cached
    rows = client.releases_for_package(
        _package(asset_pattern="rom_y2*.zip"), "Y2"
    )
    assert [row["asset_name"] for row in rows] == ["rom_y2.zip"]


def test_multiple_non_rom_zips_are_not_guessed():
    variants = catalog._release_rom_variants(
        [_asset("InniclassicY1.zip"), _asset("InniclassicY2.zip")], TAG, REPO_Y2
    )
    assert variants == []


def test_a_lone_non_rom_zip_is_still_a_firmware_candidate():
    variants = catalog._release_rom_variants(
        [_asset("Y2Linux-1.0-first-install.zip")], TAG, "schulzcode/y2linux"
    )
    assert [v["asset"]["name"] for v in variants] == [
        "Y2Linux-1.0-first-install.zip"
    ]


def test_manifest_xml_and_feed_dicts_carry_the_asset_pattern(tmp_path, monkeypatch):
    xml = (
        '<slidia><package name="inniclassic" '
        f'repo="{REPO_Y2}" device="Y2" asset_pattern="rom_y2*.zip" '
        'type="img" handler="Custom Firmware" /></slidia>'
    )
    entries = manifest.parse_manifest_xml(xml)
    assert entries and entries[0].asset_pattern == "rom_y2*.zip"

    fed = manifest.parse_manifest_packages(
        [
            {
                "name": "inniclassic",
                "repo": REPO_Y2,
                "device": "Y2",
                "asset_pattern": "rom_y2*.zip",
            }
        ]
    )
    assert fed and fed[0].asset_pattern == "rom_y2*.zip"

    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path)
    manifest.cache_manifest(entries)
    restored = manifest.load_cached_manifest()
    assert restored and restored[0].asset_pattern == "rom_y2*.zip"


def test_the_bundled_manifest_pins_the_y2_inniclassic_entry():
    text = (ROOT / "assets" / "online" / "slidia_manifest.xml").read_text(
        encoding="utf-8"
    )
    entries = manifest.parse_manifest_xml(text)
    y2 = [
        entry
        for entry in entries
        if entry.name.lower() == "inniclassic" and entry.model.upper() == "Y2"
    ]
    assert len(y2) == 1
    assert y2[0].repo == REPO_Y2
    assert y2[0].asset_pattern == "rom_y2*.zip"
