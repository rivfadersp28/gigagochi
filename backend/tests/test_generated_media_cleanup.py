from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.generated_media_cleanup import (
    cleanup_owned_generated_asset_directory,
    cleanup_stale_generated_processing_temp_directories,
    cleanup_unreferenced_background_story_media,
    collect_background_story_media_references,
    generated_media_cleanup_is_enabled,
)


def _write_with_age(path: Path, *, now: datetime, age: timedelta) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic-media")
    timestamp = (now - age).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_cleanup_enablement_prefers_explicit_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENERATED_MEDIA_CLEANUP_ENABLED", "false")

    assert generated_media_cleanup_is_enabled(True) is True
    assert generated_media_cleanup_is_enabled(False) is False
    assert generated_media_cleanup_is_enabled(None) is False


def test_cleanup_removes_only_old_unreferenced_owned_story_media(tmp_path: Path) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    root = tmp_path / "static" / "generated"
    owner = root / "pet-1"
    referenced = owner / "background-story-20260710T120000000000Z.png"
    orphan = owner / "background-story-20260710T120001000000Z.mp4"
    young = owner / "background-story-20260715T110000000000Z.png"
    pet_asset = owner / "teen-idle.mp4"
    metadata = owner / "finale.json"
    nested = owner / "nested" / "background-story-20260710T120002000000Z.png"
    for path in (referenced, orphan, pet_asset, metadata, nested):
        _write_with_age(path, now=now, age=timedelta(days=10))
    _write_with_age(young, now=now, age=timedelta(hours=1))

    result = cleanup_unreferenced_background_story_media(
        generated_root=root,
        owner_directories={"pet-1": owner},
        saved_values=[
            {"imageUrl": (f"https://example.test/static/generated/pet-1/{referenced.name}?v=123")}
        ],
        now=now,
        minimum_age=timedelta(days=3),
    )

    assert result.removed == (orphan,)
    assert result.referenced == 1
    assert result.too_young == 1
    assert referenced.exists()
    assert young.exists()
    assert pet_asset.exists()
    assert metadata.exists()
    assert nested.exists()


def test_cleanup_rejects_owner_directory_outside_generated_root(tmp_path: Path) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    root = tmp_path / "static" / "generated"
    outside = tmp_path / "outside" / "pet-1"
    media = outside / "background-story-20260710T120000000000Z.png"
    _write_with_age(media, now=now, age=timedelta(days=10))

    result = cleanup_unreferenced_background_story_media(
        generated_root=root,
        owner_directories={"pet-1": outside},
        saved_values=[],
        now=now,
    )

    assert result.removed == ()
    assert result.unsafe == 1
    assert media.exists()


def test_cleanup_skips_symlinked_story_media(tmp_path: Path) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    root = tmp_path / "static" / "generated"
    owner = root / "pet-1"
    outside = tmp_path / "outside.png"
    _write_with_age(outside, now=now, age=timedelta(days=10))
    owner.mkdir(parents=True)
    linked = owner / "background-story-20260710T120000000000Z.png"
    linked.symlink_to(outside)

    result = cleanup_unreferenced_background_story_media(
        generated_root=root,
        owner_directories={"pet-1": owner},
        saved_values=[],
        now=now,
    )

    assert result.removed == ()
    assert result.unsafe == 1
    assert linked.is_symlink()
    assert outside.exists()


def test_global_cleanup_discovers_orphan_owner_directories(tmp_path: Path) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    root = tmp_path / "static" / "generated"
    orphan = root / "deleted-pet" / "background-story-20260710T120000000000Z.mp4"
    _write_with_age(orphan, now=now, age=timedelta(days=10))

    result = cleanup_unreferenced_background_story_media(
        generated_root=root,
        saved_values=[],
        now=now,
    )

    assert result.removed == (orphan,)
    assert not orphan.exists()


def test_reference_collection_is_exact_and_cycle_safe() -> None:
    payload: dict[str, object] = {
        "good": ("/static/generated/pet-1/background-story-20260710T120000000000Z.mp4?v=7"),
        "wrongPrefix": (
            "/static/generated/pet-1/archive-background-story-20260710T120000000000Z.mp4"
        ),
        "traversal": ("/static/generated/pet-1/../background-story-20260710T120000000000Z.mp4"),
    }
    payload["cycle"] = payload

    assert collect_background_story_media_references([payload]) == {
        ("pet-1", "background-story-20260710T120000000000Z.mp4")
    }


def test_owned_asset_directory_cleanup_removes_only_exact_direct_child(tmp_path: Path) -> None:
    root = tmp_path / "static" / "generated"
    asset_directory = root / "job-asset-1"
    (asset_directory / "nested").mkdir(parents=True)
    (asset_directory / "nested" / "partial.png").write_bytes(b"partial")

    assert (
        cleanup_owned_generated_asset_directory(
            generated_root=root,
            asset_directory=asset_directory,
            expected_owner_name="job-asset-1",
        )
        is True
    )
    assert not asset_directory.exists()


def test_owned_asset_directory_cleanup_rejects_outside_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "static" / "generated"
    outside = tmp_path / "outside" / "job-asset-1"
    outside.mkdir(parents=True)
    with pytest.raises(ValueError, match="outside"):
        cleanup_owned_generated_asset_directory(
            generated_root=root,
            asset_directory=outside,
            expected_owner_name="job-asset-1",
        )

    root.mkdir(parents=True)
    linked = root / "job-asset-2"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        cleanup_owned_generated_asset_directory(
            generated_root=root,
            asset_directory=linked,
            expected_owner_name="job-asset-2",
        )
    assert outside.exists()


def test_processing_temp_cleanup_removes_only_old_known_directories(tmp_path: Path) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    root = tmp_path / "generated"
    temp_root = root / ".private" / "processing-tmp"
    old = temp_root / "pet-ping-pong-old"
    young = temp_root / "generated-video-main-stream-active"
    unknown = temp_root / "operator-data"
    for directory, age in (
        (old, timedelta(days=2)),
        (young, timedelta(minutes=10)),
        (unknown, timedelta(days=2)),
    ):
        directory.mkdir(parents=True)
        (directory / "source.mp4").write_bytes(b"synthetic")
        timestamp = (now - age).timestamp()
        os.utime(directory, (timestamp, timestamp))

    result = cleanup_stale_generated_processing_temp_directories(
        generated_root=root,
        now=now,
    )

    assert result.removed == (old,)
    assert result.too_young == 1
    assert not old.exists()
    assert young.exists()
    assert unknown.exists()


def test_processing_temp_cleanup_skips_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "generated"
    temp_root = root / ".private" / "processing-tmp"
    outside = tmp_path / "outside"
    outside.mkdir()
    temp_root.mkdir(parents=True)
    linked = temp_root / "pet-ping-pong-linked"
    linked.symlink_to(outside, target_is_directory=True)

    result = cleanup_stale_generated_processing_temp_directories(
        generated_root=root,
        minimum_age=timedelta(0),
    )

    assert result.unsafe == 1
    assert linked.is_symlink()
    assert outside.exists()
