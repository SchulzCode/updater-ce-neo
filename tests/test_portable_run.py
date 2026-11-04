"""A portable (bundled-firmware) run leaves no working files behind.

The app stages firmware, extracts it and caches manifests in the user's
application-support folders. For a run started from a portable package those
files are temporary: cleanup removes only what appeared after the session
snapshot, never a file that was already there.
"""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import portable_run  # noqa: E402


@pytest.fixture
def folders(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    cache = tmp_path / "cache"
    downloads.mkdir()
    cache.mkdir()
    monkeypatch.setattr(portable_run, "_downloads_dir", lambda: downloads)
    monkeypatch.setattr(portable_run, "_cache_dir", lambda: cache)
    return downloads, cache


def test_cleanup_removes_only_files_created_after_the_session_started(folders):
    downloads, cache = folders
    keep_archive = downloads / "earlier_firmware.zip"
    keep_archive.write_text("already here")
    keep_cache = cache / "packages.json"
    keep_cache.write_text("{}")

    session = portable_run.PortableSession(app_installed=False)

    staged = downloads / "staged_firmware" / "stage_abc"
    staged.mkdir(parents=True)
    (staged / "scatter.txt").write_text("firmware")
    (downloads / "new_firmware.zip").write_text("new")
    (cache / "release_icons").mkdir()
    (cache / "release_icons" / "icon.png").write_text("png")

    removed = session.cleanup()

    assert not staged.exists()
    assert not (downloads / "new_firmware.zip").exists()
    assert not (cache / "release_icons").exists()
    assert keep_archive.exists()
    assert keep_cache.exists()
    assert str(staged) in removed


def test_cleanup_reaches_inside_a_pre_existing_working_folder(folders):
    downloads, _cache = folders
    # An earlier run may already have created staged_firmware/; this run's own
    # stage below it must still go, while the earlier stage stays.
    staged_parent = downloads / "staged_firmware"
    older = staged_parent / "stage_older"
    older.mkdir(parents=True)
    (older / "keep.img").write_text("older")

    session = portable_run.PortableSession(app_installed=False)

    current = staged_parent / "stage_current"
    current.mkdir()
    (current / "scatter.txt").write_text("current")

    session.cleanup()

    assert not current.exists()
    assert older.exists()
    assert (older / "keep.img").exists()


def test_cleanup_removes_app_temporary_work(folders, tmp_path, monkeypatch):
    downloads, _cache = folders
    temp = tmp_path / "system-temp"
    temp.mkdir()
    monkeypatch.setattr(portable_run, "_temp_dir", lambda: temp)
    other_app = temp / "unrelated-tool"
    other_app.mkdir()

    session = portable_run.PortableSession(app_installed=False)

    wrapped = temp / "preloader_wrapped_abc.bin"
    wrapped.write_text("boot header")
    themes = temp / "rockbox_360p_xyz"
    themes.mkdir()
    (themes / "pack.zip").write_text("themes")

    session.cleanup()

    assert not wrapped.exists()
    assert not themes.exists()
    assert other_app.exists()


def test_cleanup_reports_every_step_and_ends_at_one_hundred(folders):
    session = portable_run.PortableSession(app_installed=False)
    steps = []

    session.cleanup(lambda percent, key: steps.append((percent, key)))

    assert [percent for percent, _key in steps] == [33, 67, 100, 100]
    assert steps[-1][1] == "portable_cleanup_done"
    assert [key for _percent, key in steps] == [
        "portable_cleanup_downloads",
        "portable_cleanup_cache",
        "portable_cleanup_temp",
        "portable_cleanup_done",
    ]


def test_a_missing_folder_is_not_created_or_reported(folders, monkeypatch):
    downloads, cache = folders
    # Remove the cache folder before the session: nothing should be created.
    cache.rmdir()
    session = portable_run.PortableSession(app_installed=False)
    assert not cache.exists()

    session.cleanup()

    assert not cache.exists()
    assert downloads.exists()


def test_targets_lists_working_files_without_removing_them(folders):
    downloads, cache = folders
    session = portable_run.PortableSession(app_installed=False)
    staged = downloads / "staged_firmware"
    staged.mkdir()
    (cache / "packages.json").write_text("{}")

    targets = session.targets()

    assert staged in targets
    assert cache / "packages.json" in targets
    assert staged.exists()
    assert (cache / "packages.json").exists()
