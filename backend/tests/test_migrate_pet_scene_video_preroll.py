from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import migrate_pet_scene_video_preroll as migration


def _assert_local_mp4_input(command: list[str], path: Path) -> None:
    assert command[command.index("-f") + 1] == "mov"
    assert command[command.index("-protocol_whitelist") + 1] == "file"
    assert command[command.index("-enable_drefs") + 1] == "0"
    assert command[command.index("-use_absolute_path") + 1] == "0"
    assert command[command.index("-i") + 1] == str(path)


def test_duration_probe_forces_local_mp4_demuxer(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return SimpleNamespace(stdout="5.25\n")

    monkeypatch.setattr(migration.subprocess, "run", fake_run)

    assert migration._duration_seconds(source) == 5.25
    _assert_local_mp4_input(captured["command"], source)
    assert captured["kwargs"]["timeout"] == 30


def test_trim_forces_local_mp4_demuxer(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(migration.subprocess, "run", fake_run)

    migration._trim_existing_ping_pong(source, output, 10.0)

    _assert_local_mp4_input(captured["command"], source)
    assert captured["kwargs"]["timeout"] == 180
