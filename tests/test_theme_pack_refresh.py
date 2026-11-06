"""Weekly Rockbox 360p theme pack refresh (the archive is no longer bundled)."""

import zipfile

from src import theme_pack


def _cached_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(theme_pack, "downloads_dir", lambda: tmp_path)
    cached = tmp_path / theme_pack.THEME_PACK_ZIP_NAME
    with zipfile.ZipFile(cached, "w") as zf:
        zf.writestr("themes/sample.cfg", "x" * 2048)
    return cached


def test_missing_cache_is_due(tmp_path, monkeypatch):
    monkeypatch.setattr(theme_pack, "downloads_dir", lambda: tmp_path)
    assert theme_pack.theme_pack_refresh_due(now=1_000.0) is True


def test_refresh_waits_seven_days_after_success(tmp_path, monkeypatch):
    _cached_pack(tmp_path, monkeypatch)
    now = 1_700_000_000.0
    theme_pack.mark_theme_pack_refreshed(now=now)

    assert theme_pack.theme_pack_refresh_due(now=now + 6 * 86_400) is False
    assert theme_pack.theme_pack_refresh_due(now=now + 7 * 86_400 + 1) is True


def test_a_bad_or_missing_stamp_forces_refresh(tmp_path, monkeypatch):
    _cached_pack(tmp_path, monkeypatch)
    assert theme_pack.theme_pack_refresh_due(now=1_000.0) is True

    theme_pack.theme_pack_refresh_stamp_path().write_text("not-a-time", encoding="utf-8")
    assert theme_pack.theme_pack_refresh_due(now=1_000.0) is True


def test_async_refresh_runs_once_and_marks_success(tmp_path, monkeypatch):
    _cached_pack(tmp_path, monkeypatch)
    monkeypatch.setattr("src.config.is_offline_mode", lambda: False)
    calls = []

    def fake_download(*args, **kwargs):
        calls.append(kwargs)
        theme_pack.mark_theme_pack_refreshed()
        return True, "merged", 5

    monkeypatch.setattr(theme_pack, "download_and_merge_theme_pack", fake_download)

    assert theme_pack.refresh_theme_pack_if_due_async() is True
    worker = theme_pack._refresh_thread
    worker.join(5)
    assert not worker.is_alive()
    assert calls
    assert theme_pack.theme_pack_refresh_stamp_path().is_file()
    assert theme_pack.refresh_theme_pack_if_due_async() is False


def test_async_refresh_is_skipped_offline(monkeypatch):
    monkeypatch.setattr("src.config.is_offline_mode", lambda: True)
    assert theme_pack.refresh_theme_pack_if_due_async() is False
