"""Only the latest firmware install is kept on disk: its extracted folder and
its archive survive, and older extractions and archives are pruned.

The archive is deliberately kept: it is the only copy a retry, a backend
switch, or a re-extraction can work from."""

import zipfile
from pathlib import Path

import pytest

from src import downloads
from src.flash_service import (
    EXTRACT_COMPLETE_MARKER,
    ExtractWorker,
    completed_extract_dir,
)


def _make_zip(path: Path, scatter_name: str = "MT6572_Android_scatter.txt") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(scatter_name, "platform: MT6572\n")
        zf.writestr("MTK_AllInOne_DA.bin", b"da")
    return path


@pytest.fixture()
def downloads_root(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path)
    return tmp_path


def test_extract_keeps_the_latest_archive_and_prunes_the_older(downloads_root):
    old_zip = _make_zip(downloads_root / "old.zip")
    old_worker = ExtractWorker(str(old_zip))
    old_dir = Path(old_worker._extract(str(old_zip)))
    assert (old_dir / EXTRACT_COMPLETE_MARKER).is_file()
    assert old_zip.exists(), "the archive is the retry's only copy of the firmware"

    # A later download arrives only after the previous install was extracted.
    new_zip = _make_zip(downloads_root / "new.zip")
    new_worker = ExtractWorker(str(new_zip))
    new_dir = Path(new_worker._extract(str(new_zip)))

    assert new_dir.is_dir()
    assert (new_dir / "MTK_AllInOne_DA.bin").is_file()
    assert new_zip.exists(), "the current package keeps its archive"
    assert not old_dir.exists(), "older extraction must be pruned"
    assert not old_zip.exists(), "older archives are pruned with their extraction"


def test_reextract_reuses_completed_folder(downloads_root):
    zip_path = _make_zip(downloads_root / "pkg.zip")
    first = Path(ExtractWorker(str(zip_path))._extract(str(zip_path)))
    marker_mtime = (first / EXTRACT_COMPLETE_MARKER).stat().st_mtime_ns

    again = Path(ExtractWorker(str(zip_path))._extract(str(zip_path)))
    assert again == first
    assert (again / EXTRACT_COMPLETE_MARKER).stat().st_mtime_ns == marker_mtime
    assert completed_extract_dir(str(zip_path)) == str(first)
