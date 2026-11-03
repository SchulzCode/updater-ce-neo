from __future__ import annotations

import io
import plistlib
import zipfile

from housekeeping import core


def make_zip(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(name, b"test")
    return buffer.getvalue()


CORRECT = [
    "Install Firmware.app/Contents/Info.plist",
    "Install Firmware.app/Contents/MacOS/Innioasis Updater",
    "Firmware/fw_package.ini",
    "Firmware/MT6572_Android_scatter.txt",
    "Firmware/boot.img",
    "README.txt",
]


def test_classifies_correct_beside_app_layout():
    result = core.classify_zip_names(CORRECT)
    assert result.ok
    assert result.has_app
    assert result.has_info
    assert result.has_fw_ini
    assert result.scatters == ["Firmware/MT6572_Android_scatter.txt"]
    assert "Firmware" in result.roots


def test_classifies_old_flat_layout_as_broken():
    result = core.classify_zip_names(
        [
            "Innioasis Updater.app/Contents/Info.plist",
            "MT6572_Android_scatter.txt",
            "fw_package.ini",
            "boot.img",
        ]
    )
    assert not result.ok
    assert "Install Firmware.app" in result.detail
    assert "Firmware/fw_package.ini" in result.detail


def test_missing_scatter_is_broken():
    result = core.classify_zip_names(
        [
            "Install Firmware.app/Contents/Info.plist",
            "Firmware/fw_package.ini",
            "Firmware/boot.img",
        ]
    )
    assert not result.ok
    assert "scatter" in result.detail


def test_parses_local_zip_names():
    data = make_zip(CORRECT)
    assert core.parse_zip_names(data) == CORRECT


def test_content_range_total_parsing():
    assert core._total_from_content_range("bytes 0-0/123456") == 123456
    assert core._total_from_content_range("bytes 0-0/*") is None
    assert core._total_from_content_range(None) is None


def test_remote_zip_uses_a_range_and_reads_the_directory():
    data = make_zip(CORRECT)
    calls: list[tuple[int, int]] = []

    def fetch(_url: str, start: int, end: int) -> bytes:
        calls.append((start, end))
        return data[start : end + 1]

    names = core.list_remote_zip("https://example.invalid/test.zip", len(data), fetch=fetch)
    assert names == CORRECT
    assert calls
    assert calls[0][1] == len(data) - 1


def test_dispatch_inputs_only_include_requested_values():
    assert core.dispatch_inputs() == {}
    assert core.dispatch_inputs(platforms="macos", force=True) == {
        "platforms": "macos",
        "force": "true",
    }
    assert core.dispatch_inputs(tags="v1.0.0", dry_run=True) == {
        "tags": "v1.0.0",
        "dry_run": "true",
    }


def test_status_counts_and_staleness():
    status = core.MirrorStatus(
        repo="y1-community/example-mirror",
        generated_at="2000-01-01T00:00:00Z",
        releases=2,
        macos_total=3,
        macos_assets=[
            core.MacAsset("y1-community/example-mirror", "v1", "rom-macos.zip", "https://example.invalid/a", 10, "ok"),
            core.MacAsset("y1-community/example-mirror", "v2", "rom-macos.zip", "https://example.invalid/b", 10, "wrong_layout"),
        ],
    )
    assert status.macos_ok == 1
    assert status.macos_broken == 1
    assert status.macos_unknown == 1
    assert status.macos_checked == 2
    assert status.needs_repair
    assert status.is_stale(12)
    assert core.macos_label(status) == "1 broken +1 unchecked"


def test_launchd_plist_is_a_six_hour_job(tmp_path):
    payload = plistlib.loads(
        core.build_launchd_plist(
            ["/usr/bin/python3", "-m", "housekeeping", "check"],
            interval_hours=6,
            working_directory="/tmp/example",
            log_path=tmp_path / "housekeeping.log",
        )
    )
    assert payload["Label"] == core.LAUNCH_AGENT_LABEL
    assert payload["StartInterval"] == 6 * 3600
    assert payload["ProgramArguments"] == ["/usr/bin/python3", "-m", "housekeeping", "check"]
    assert payload["WorkingDirectory"] == "/tmp/example"
    assert payload["RunAtLoad"] is False


def test_schedule_command_uses_module_for_source_runs():
    command = core.schedule_command(auto_update=True, auto_repair=True)
    assert command[0]
    assert command[1:3] == ["-m", "housekeeping"]
    assert "check" in command
    assert "--update" in command
    assert "--repair" in command
    assert "--deep" in command
