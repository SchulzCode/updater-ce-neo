"""A running copy marks its work so a second copy cannot sweep it away."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import instance_marker, process_guard  # noqa: E402


def _publish(monkeypatch, tmp_path, payload):
    path = tmp_path / "busy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(instance_marker, "marker_path", lambda: path)
    return path


def test_a_live_copy_blocks_the_startup_sweep(monkeypatch, tmp_path):
    _publish(
        monkeypatch,
        tmp_path,
        {"pid": os.getppid(), "tasks": ["install"], "owned_pids": [4242]},
    )
    assert instance_marker.foreign_busy() is True
    assert instance_marker.owned_pids() == frozenset({4242})
    # The sweep must not touch a tool the other copy is flashing with.
    assert process_guard.sweep_stray_tools(reason="test") == []


def test_a_dead_owner_leaves_no_refusal_behind(monkeypatch, tmp_path):
    _publish(
        monkeypatch,
        tmp_path,
        {"pid": 999_999_999, "tasks": ["install"], "owned_pids": [4242]},
    )
    assert instance_marker.live_owner() is None
    assert instance_marker.foreign_busy() is False
    assert instance_marker.owned_pids() == frozenset()


def test_an_idle_live_copy_does_not_block_a_takeover(monkeypatch, tmp_path):
    _publish(monkeypatch, tmp_path, {"pid": os.getppid(), "tasks": [], "owned_pids": []})
    assert instance_marker.live_owner() is not None
    assert instance_marker.foreign_busy() is False


def test_this_copy_publishes_its_own_tasks(monkeypatch, tmp_path):
    path = tmp_path / "busy.json"
    monkeypatch.setattr(instance_marker, "marker_path", lambda: path)
    instance_marker.begin_task("install")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        assert state["pid"] == os.getpid()
        assert "install" in state["tasks"]
        # Its own marker never reads as somebody else's work.
        assert instance_marker.foreign_busy() is False
    finally:
        instance_marker.end_task("install")
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"] == []
