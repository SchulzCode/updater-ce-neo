"""A browsed package is described from its own folder, and a dead source falls back.

Two rules the offline-friendly install path depends on:

* ``fw_package.ini`` and the ``updater`` image may sit beside the scatter, in a
  neighbouring folder of the same package, or above it — but never outside the
  folder the user actually browsed.
* When a release download fails permanently (the repository was deleted, or a
  network blocks it), the same release on the portable mirror is tried before
  the install is reported as failed.
"""

from pathlib import Path

from src import fw_package, portable


def _mirror_index() -> dict:
    tag = "3.2.1"
    mirror = "y1-community/y1-stock-rom-mirror"

    def url(name: str) -> str:
        return f"https://github.com/{mirror}/releases/download/{tag}/{name}"

    return {
        "schema": 1,
        "source_repo": "y1-community/y1-stock-rom",
        "mirror_repo": mirror,
        "platforms": [
            {"key": "macos", "os": "macos", "arch": "universal", "suffix": "-macos.zip"},
            {"key": "windows", "os": "windows", "arch": "x86-64", "suffix": "-windows.zip"},
        ],
        "releases": [
            {
                "tag": tag,
                "mirror_tag": tag,
                "assets": [
                    {
                        "name": "rom_y2-macos.zip",
                        "platform": "macos",
                        "source_asset": "rom_y2.zip",
                        "size": 120,
                        "url": url("rom_y2-macos.zip"),
                    },
                    {
                        "name": "rom_y2-windows.zip",
                        "platform": "windows",
                        "source_asset": "rom_y2.zip",
                        "size": 120,
                        "url": url("rom_y2-windows.zip"),
                    },
                ],
                "mirror_originals": {
                    "rom_y2.zip": {
                        "name": "rom_y2.zip",
                        "original_url": "https://github.com/y1-community/y1-stock-rom/"
                                        "releases/download/3.2.1/rom_y2.zip",
                        "size": 120,
                        "mirror_original_url": url("rom_y2.zip"),
                    }
                },
            }
        ],
    }


def test_fallback_urls_prefer_this_machines_package_then_the_original(monkeypatch):
    index = _mirror_index()
    monkeypatch.setattr(portable, "mirrors_for_repo", lambda repo: ["y1-community/y1-stock-rom-mirror"])
    monkeypatch.setattr(portable, "load_index", lambda repo, fetch=None: index)

    urls = portable.fallback_urls_for(
        "https://github.com/y1-community/y1-stock-rom/releases/download/3.2.1/rom_y2.zip",
        platform="macos",
    )

    assert urls, "a mirror release must produce fallback URLs"
    assert urls[0].endswith("rom_y2-macos.zip")
    assert any(url.endswith("rom_y2.zip") for url in urls)
    assert len(urls) == len(set(urls))


def test_fallback_urls_are_empty_for_a_repo_without_a_mirror(monkeypatch):
    monkeypatch.setattr(portable, "mirrors_for_repo", lambda repo: [])
    assert portable.fallback_urls_for(
        "https://github.com/someone/unmirrored/releases/download/1.0/rom.zip",
        platform="windows",
    ) == []


def test_no_mirror_asset_keeps_the_plain_download(monkeypatch):
    """Anything unresolved returns nothing, so the caller keeps its own URL."""
    monkeypatch.setattr(portable, "mirrors_for_repo", lambda repo: ["y1-community/mirror"])
    monkeypatch.setattr(portable, "load_index", lambda repo, fetch=None: {})
    assert portable.fallback_urls_for(
        "https://github.com/y1-community/y1-stock-rom/releases/download/9.9.9/rom.zip"
    ) == []


def _write_package(root: Path, ini_dir: Path | None, icon_dir: Path) -> Path:
    scatter_dir = root / "Firmware"
    scatter_dir.mkdir(parents=True)
    (scatter_dir / "MT6582_Android_scatter.txt").write_text(
        "- general: MTK_PLATFORM_CFG\n"
        "  info:\n"
        "    - config_version: V1.1.1\n"
        "      platform: MT6582\n"
        "      project: eastaeon82_wet_kk\n"
        "      storage: EMMC\n",
        encoding="utf-8",
    )
    if ini_dir is not None:
        ini_dir.mkdir(parents=True, exist_ok=True)
        (ini_dir / fw_package.FW_PACKAGE_INI).write_text(
            "[package]\nname=Reborn (Y2Linux)\nmodel=Y2\n",
            encoding="utf-8",
        )
    icon_dir.mkdir(parents=True, exist_ok=True)
    (icon_dir / "updater.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return scatter_dir


def test_release_ini_found_in_a_parent_folder(tmp_path):
    root = tmp_path / "portable"
    scatter_dir = _write_package(root, root, root)

    found = fw_package._release_ini(scatter_dir, browse_root=root)

    assert found == root / fw_package.FW_PACKAGE_INI


def test_release_ini_found_in_a_neighbouring_folder(tmp_path):
    root = tmp_path / "portable"
    scatter_dir = _write_package(root, root / "Extras", root / "Extras")

    found = fw_package._release_ini(scatter_dir, browse_root=root)

    assert found == root / "Extras" / fw_package.FW_PACKAGE_INI


def test_release_ini_outside_the_browsed_folder_is_ignored(tmp_path):
    """A package must never be described by a file the user did not browse."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / fw_package.FW_PACKAGE_INI).write_text("[package]\nname=Other\n", encoding="utf-8")
    root = tmp_path / "portable"
    scatter_dir = _write_package(root, None, root / "Extras")

    found = fw_package._release_ini(scatter_dir, browse_root=root)

    assert found is None


def test_updater_image_found_above_the_scatter_folder(tmp_path):
    root = tmp_path / "portable"
    scatter_dir = _write_package(root, root / "Extras", root)

    scope = fw_package._browse_scope(scatter_dir, root)
    image = fw_package._package_image(scatter_dir, scope)

    assert image == root / "updater.png"


def test_browse_scope_stays_inside_the_package(tmp_path):
    root = tmp_path / "portable"
    scatter_dir = _write_package(root, root, root)
    (tmp_path / "unrelated").mkdir()

    scope = fw_package._browse_scope(scatter_dir, root)

    assert scope, "the browsed package always has an enclosing scope"
    assert all(path == root or root in path.parents for path in scope)
    assert (tmp_path / "unrelated") not in scope
