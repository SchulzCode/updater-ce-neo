"""Keep App on Linux downloads the architecture-matched AppImage.

A portable run keeps the app as one file. The release also carries portable
tar.gz/zip packages, and the normal update picker prefers those for a
non-AppImage launch; Keep App must still take the AppImage for this machine.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import updates  # noqa: E402

ASSETS = [
    {"name": "UpdaterCE-Linux-Portable.tar.gz", "browser_download_url": "https://example.invalid/portable.tar.gz"},
    {"name": "UpdaterCE.appimage", "browser_download_url": "https://example.invalid/UpdaterCE.appimage"},
    {"name": "UpdaterCE-arm64.appimage", "browser_download_url": "https://example.invalid/UpdaterCE-arm64.appimage"},
]


def test_keep_app_takes_the_appimage_for_each_linux_architecture(monkeypatch):
    monkeypatch.setattr(updates, "_host_is_arm64", lambda: False)
    x86_64 = updates.pick_portable_appimage(ASSETS, platform="linux")
    assert x86_64 is not None
    assert x86_64["name"] == "UpdaterCE.appimage"

    monkeypatch.setattr(updates, "_host_is_arm64", lambda: True)
    arm64 = updates.pick_portable_appimage(ASSETS, platform="linux")
    assert arm64 is not None
    assert arm64["name"] == "UpdaterCE-arm64.appimage"


def test_keep_app_never_takes_the_other_architecture(monkeypatch):
    monkeypatch.setattr(updates, "_host_is_arm64", lambda: True)
    assert updates.pick_portable_appimage(
        [{"name": "UpdaterCE.appimage"}], platform="linux"
    ) is None

    monkeypatch.setattr(updates, "_host_is_arm64", lambda: False)
    assert updates.pick_portable_appimage(
        [{"name": "UpdaterCE-arm64.appimage"}], platform="linux"
    ) is None


def test_keep_app_is_linux_only():
    assert updates.pick_portable_appimage(ASSETS, platform="darwin") is None
    assert updates.pick_portable_appimage(ASSETS, platform="win32") is None
