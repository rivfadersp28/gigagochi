from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.schemas import LocalPetChatContext
from app.services import interactive_travel_media_service as media_service


def _reserved(fake):
    @contextmanager
    def reservation(*args, **kwargs):
        yield fake(*args, **kwargs)

    return reservation


def sample_pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "name": "Листик",
            "description": "маленький листолицый питомец",
            "stage": "baby",
            "mood": "happy",
            "stats": {
                "hunger": 80,
                "happiness": 90,
                "energy": 75,
            },
            "characterBible": {
                "identity": {"name": "Листик"},
                "main_colors": ["moss green", "cream"],
                "signature_features": ["leaf-shaped face", "tiny sprout tail"],
                "baby_design": "small leaf-faced pet with rounded cream cheeks",
            },
            "assetImages": {
                "baby": {
                    "idle": "https://cdn.example.test/assets/baby-idle.png",
                    "happy": "https://cdn.example.test/assets/baby-happy.png",
                },
                "teen": {
                    "happy": "https://cdn.example.test/assets/teen-happy.png",
                },
            },
        }
    )


def sample_png_bytes() -> bytes:
    image = Image.new("RGB", (900, 1200), (94, 131, 87))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_interactive_travel_video_returns_url_and_writes_mp4(monkeypatch, tmp_path) -> None:
    travel_id = "interactive-travel-video-test"
    output_dir = tmp_path / travel_id
    output_dir.mkdir(parents=True)
    (output_dir / "interactive-travel-part-01-video-source.png").write_bytes(sample_png_bytes())
    monkeypatch.setattr(media_service, "generated_dir_for", lambda _travel_id: output_dir)
    reservation_events: list[str] = []
    captured_options: dict[str, str] = {}

    @contextmanager
    def reserved_video(_source, **kwargs):
        captured_options.update(kwargs)
        reservation_events.append("enter")
        try:
            yield b"video-bytes"
        finally:
            assert (output_dir / "interactive-travel-part-01.mp4").read_bytes() == b"video-bytes"
            reservation_events.append("exit-after-commit")

    monkeypatch.setattr(
        media_service,
        "reserve_background_story_video_bytes",
        reserved_video,
    )

    video_url = media_service.generate_interactive_travel_part_video(
        travel_id=travel_id,
        part_number=1,
    )

    assert video_url.startswith(f"/static/generated/{travel_id}/interactive-travel-part-01.mp4?v=")
    assert (output_dir / "interactive-travel-part-01.mp4").read_bytes() == b"video-bytes"
    assert reservation_events == ["enter", "exit-after-commit"]
    assert captured_options["aspect_ratio"] == "3:4"


def test_interactive_travel_image_replay_reuses_persisted_media(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-image-replay"
    output_dir = tmp_path / travel_id
    provider_calls = 0
    captured_options: dict[str, str] = {}
    monkeypatch.setattr(media_service, "generated_dir_for", lambda _travel_id: output_dir)

    def fake_provider(**kwargs) -> bytes:
        nonlocal provider_calls
        provider_calls += 1
        captured_options.update(kwargs)
        return sample_png_bytes()

    monkeypatch.setattr(
        media_service,
        "reserve_background_story_image_bytes",
        _reserved(fake_provider),
    )
    kwargs = {
        "pet": sample_pet(),
        "travel_id": travel_id,
        "destination": "в город облаков",
        "part_number": 1,
        "title": "Начало",
        "story_text": "Передо мной появляется мост.",
    }

    first_url = media_service.generate_interactive_travel_part_image(**kwargs)
    persisted_mtime = (output_dir / "interactive-travel-part-01.png").stat().st_mtime_ns
    second_url = media_service.generate_interactive_travel_part_image(**kwargs)

    assert provider_calls == 1
    assert first_url == second_url
    assert first_url.endswith(f"?v={persisted_mtime}")
    assert captured_options["image_size"] == "768x1024"
    assert "3:4 portrait canvas" in captured_options["composition_direction"]
    with Image.open(output_dir / "interactive-travel-part-01.png") as poster:
        assert poster.size == (450, 600)
    with Image.open(output_dir / "interactive-travel-part-01-video-source.png") as video_source:
        assert video_source.size == (720, 960)
    assert not list(output_dir.glob(".*.tmp"))


def test_interactive_travel_video_cross_process_single_flight(tmp_path) -> None:
    travel_id = "interactive-travel-cross-process"
    output_dir = tmp_path / travel_id
    output_dir.mkdir(parents=True)
    (output_dir / "interactive-travel-part-01-video-source.png").write_bytes(sample_png_bytes())
    provider_calls_path = tmp_path / "provider-calls"
    script = textwrap.dedent(
        """
        import os
        import sys
        import time
        from contextlib import contextmanager
        from pathlib import Path

        from app.services import interactive_travel_media_service as media_service

        output_dir = Path(sys.argv[1])
        calls_path = Path(sys.argv[2])
        media_service.generated_dir_for = lambda _travel_id: output_dir

        @contextmanager
        def provider(_source, **_kwargs):
            descriptor = os.open(calls_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, b"call\\n")
            finally:
                os.close(descriptor)
            time.sleep(0.3)
            yield b"synthetic-video"

        media_service.reserve_background_story_video_bytes = provider
        print(
            media_service.generate_interactive_travel_part_video(
                travel_id="interactive-travel-cross-process",
                part_number=1,
            )
        )
        """
    )
    backend_dir = str(Path(__file__).resolve().parents[1])
    child_pythonpath = os.pathsep.join(
        part for part in (backend_dir, os.environ.get("PYTHONPATH", "")) if part
    )
    environment = {**os.environ, "PYTHONPATH": child_pythonpath}
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(output_dir), str(provider_calls_path)],
            cwd=backend_dir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]

    results = [process.communicate(timeout=15) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    urls = [stdout.strip() for stdout, _stderr in results]
    assert urls[0] == urls[1]
    assert provider_calls_path.read_text(encoding="utf-8").splitlines() == ["call"]
    assert (output_dir / "interactive-travel-part-01.mp4").read_bytes() == b"synthetic-video"


def test_interactive_travel_cross_process_reset_during_provider_prevents_resurrection(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-reset-cross-process"
    output_dir = tmp_path / travel_id
    output_dir.mkdir(parents=True)
    (output_dir / "interactive-travel-part-01-video-source.png").write_bytes(sample_png_bytes())
    provider_started = tmp_path / "provider-started"
    provider_release = tmp_path / "provider-release"
    script = textwrap.dedent(
        """
        import sys
        import time
        from contextlib import contextmanager
        from pathlib import Path

        from app.services import interactive_travel_media_service as media_service

        output_dir = Path(sys.argv[1])
        provider_started = Path(sys.argv[2])
        provider_release = Path(sys.argv[3])
        media_service.generated_dir_for = lambda _travel_id: output_dir

        @contextmanager
        def provider(_source, **_kwargs):
            provider_started.write_text("started", encoding="utf-8")
            deadline = time.monotonic() + 10
            while not provider_release.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("test release timed out")
                time.sleep(0.01)
            yield b"must-not-be-committed"

        media_service.reserve_background_story_video_bytes = provider
        try:
            media_service.generate_interactive_travel_part_video(
                travel_id="interactive-travel-reset-cross-process",
                part_number=1,
            )
        except RuntimeError as exc:
            if str(exc) == "INTERACTIVE_TRAVEL_GENERATION_CANCELLED":
                raise SystemExit(0)
            raise
        raise SystemExit(2)
        """
    )
    backend_dir = str(Path(__file__).resolve().parents[1])
    child_pythonpath = os.pathsep.join(
        part for part in (backend_dir, os.environ.get("PYTHONPATH", "")) if part
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(output_dir),
            str(provider_started),
            str(provider_release),
        ],
        cwd=backend_dir,
        env={**os.environ, "PYTHONPATH": child_pythonpath},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not provider_started.exists() and process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            pytest.fail("provider subprocess did not start")
        time.sleep(0.01)

    monkeypatch.setattr(media_service, "generated_dir_for", lambda _travel_id: output_dir)
    media_service.reset_interactive_travel_generation(travel_id)
    provider_release.write_text("release", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode == 0, (stdout, stderr)
    assert not output_dir.exists()
    cancel_marker = tmp_path / ".interactive-travel-locks" / f"{travel_id}.cancelled"
    assert cancel_marker.read_text(encoding="utf-8") == "cancelled\n"
    with pytest.raises(RuntimeError, match="INTERACTIVE_TRAVEL_GENERATION_CANCELLED"):
        media_service.generate_interactive_travel_part_video(
            travel_id=travel_id,
            part_number=1,
        )
    retry_script = textwrap.dedent(
        """
        import sys
        from pathlib import Path

        from app.services import interactive_travel_media_service as media_service

        output_dir = Path(sys.argv[1])
        media_service.generated_dir_for = lambda _travel_id: output_dir
        try:
            media_service.generate_interactive_travel_part_video(
                travel_id="interactive-travel-reset-cross-process",
                part_number=1,
            )
        except RuntimeError as exc:
            if str(exc) == "INTERACTIVE_TRAVEL_GENERATION_CANCELLED":
                raise SystemExit(0)
            raise
        raise SystemExit(2)
        """
    )
    retry = subprocess.run(
        [sys.executable, "-c", retry_script, str(output_dir)],
        cwd=backend_dir,
        env={**os.environ, "PYTHONPATH": child_pythonpath},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert retry.returncode == 0, (retry.stdout, retry.stderr)


def test_interactive_travel_reset_deletes_assets_and_tombstones_id(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-reset-test"
    output_dir = tmp_path / travel_id
    output_dir.mkdir(parents=True)
    (output_dir / "interactive-travel-part-01.png").write_bytes(sample_png_bytes())
    monkeypatch.setattr(media_service, "generated_dir_for", lambda _travel_id: output_dir)

    media_service.reset_interactive_travel_generation(travel_id)

    assert not output_dir.exists()
    with pytest.raises(RuntimeError, match="INTERACTIVE_TRAVEL_GENERATION_CANCELLED"):
        media_service.generate_interactive_travel_part_video(
            travel_id=travel_id,
            part_number=1,
        )


def test_interactive_travel_cancel_deletes_incomplete_assets(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-cancel-incomplete"
    output_dir = tmp_path / travel_id
    output_dir.mkdir()
    (output_dir / "interactive-travel-part-01.png").write_bytes(sample_png_bytes())
    monkeypatch.setattr(media_service, "generated_dir_for", lambda _travel_id: output_dir)

    finale_preserved = media_service.cancel_interactive_travel_generation(travel_id)

    assert finale_preserved is False
    assert not output_dir.exists()
    assert (tmp_path / ".interactive-travel-locks" / f"{travel_id}.cancelled").is_file()


def test_interactive_travel_cancel_preserves_completed_finale_and_fences_media(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-cancel-completed"
    output_dir = tmp_path / travel_id
    output_dir.mkdir()
    finale = output_dir / "finale.json"
    finale.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "travel": {"travelId": travel_id, "completed": True},
            }
        ),
        encoding="utf-8",
    )
    poster = output_dir / "interactive-travel-part-01.png"
    poster.write_bytes(sample_png_bytes())
    monkeypatch.setattr(media_service, "generated_dir_for", lambda _travel_id: output_dir)

    finale_preserved = media_service.cancel_interactive_travel_generation(travel_id)

    assert finale_preserved is True
    assert finale.is_file()
    assert poster.is_file()
    with pytest.raises(RuntimeError, match="INTERACTIVE_TRAVEL_GENERATION_CANCELLED"):
        media_service.generate_interactive_travel_part_video(
            travel_id=travel_id,
            part_number=1,
        )


def test_interactive_travel_reset_prunes_expired_cancel_markers(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-current-reset"
    output_dir = tmp_path / travel_id
    output_dir.mkdir()
    lock_root = tmp_path / ".interactive-travel-locks"
    lock_root.mkdir()
    expired_marker = lock_root / "interactive-travel-expired.cancelled"
    expired_marker.write_text("cancelled\n", encoding="utf-8")
    os.utime(expired_marker, (1, 1))
    monkeypatch.setattr(media_service, "generated_dir_for", lambda _travel_id: output_dir)
    monkeypatch.setattr(
        media_service,
        "get_settings",
        lambda: SimpleNamespace(interactive_travel_owner_retention_seconds=1),
    )

    media_service.reset_interactive_travel_generation(travel_id)

    assert not expired_marker.exists()
    assert (lock_root / f"{travel_id}.cancelled").is_file()


def test_interactive_travel_reset_cannot_delete_a_pet_asset_directory(
    monkeypatch,
    tmp_path,
) -> None:
    protected_dir = tmp_path / "pet-assets"
    protected_dir.mkdir()
    protected_file = protected_dir / "teen-idle.png"
    protected_file.write_bytes(sample_png_bytes())
    monkeypatch.setattr(media_service, "generated_dir_for", lambda _travel_id: protected_dir)

    with pytest.raises(ValueError, match="Invalid interactive travel id"):
        media_service.reset_interactive_travel_generation("550e8400-e29b-41d4-a716-446655440000")

    assert protected_file.exists()
