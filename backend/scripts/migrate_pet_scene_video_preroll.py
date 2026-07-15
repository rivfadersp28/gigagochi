from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.image_service import render_ping_pong_video_bytes

RAW_VIDEO_MAX_DURATION_SECONDS = 6.0
EXTRA_PING_PONG_TRIM_SECONDS = 0.1
SEEDANCE_START_OFFSET_SECONDS = 0.2
MIGRATION_STATE_FILENAME = ".seedance-preroll-v2.json"
PET_VIDEO_NAMES = {"teen-idle.mp4", "teen-sad.mp4", "teen-happy.mp4"}


def _duration_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-f",
            "mov",
            "-protocol_whitelist",
            "file",
            "-enable_drefs",
            "0",
            "-use_absolute_path",
            "0",
            "-i",
            str(path),
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return float(result.stdout.strip())


def _trim_existing_ping_pong(path: Path, output_path: Path, duration: float) -> None:
    end = duration - EXTRA_PING_PONG_TRIM_SECONDS
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "mov",
            "-protocol_whitelist",
            "file",
            "-enable_drefs",
            "0",
            "-use_absolute_path",
            "0",
            "-i",
            str(path),
            "-filter_complex",
            (
                f"[0:v]trim=start={EXTRA_PING_PONG_TRIM_SECONDS:.6f}:end={end:.6f},"
                "setpts=PTS-STARTPTS,fps=24[out]"
            ),
            "-map",
            "[out]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-level:v",
            "3.1",
            "-video_track_timescale",
            "12288",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )


def _migrated_bytes(path: Path, output_path: Path) -> None:
    duration = _duration_seconds(path)
    if duration <= RAW_VIDEO_MAX_DURATION_SECONDS:
        output_path.write_bytes(
            render_ping_pong_video_bytes(
                path.read_bytes(),
                start_offset_seconds=SEEDANCE_START_OFFSET_SECONDS,
            )
        )
        return
    _trim_existing_ping_pong(path, output_path, duration)


def _load_completed(state_path: Path) -> set[str]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    completed = payload.get("completed") if isinstance(payload, dict) else None
    return {str(value) for value in completed} if isinstance(completed, list) else set()


def _save_completed(state_path: Path, completed: set[str]) -> None:
    temp_path = state_path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps({"completed": sorted(completed)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, state_path)


def migrate(
    root: Path,
    backup_root: Path,
    *,
    dry_run: bool = False,
    restore_from_backup: bool = False,
) -> int:
    root = root.resolve()
    backup_root = backup_root.resolve()
    state_path = root / MIGRATION_STATE_FILENAME
    completed = _load_completed(state_path)
    candidates = sorted(path for path in root.glob("*/*.mp4") if path.name in PET_VIDEO_NAMES)
    pending = (
        candidates
        if restore_from_backup
        else [path for path in candidates if str(path.relative_to(root)) not in completed]
    )
    if dry_run:
        for path in pending:
            print(path.relative_to(root))
        return len(pending)

    for path in pending:
        relative_path = path.relative_to(root)
        backup_path = backup_root / relative_path
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if restore_from_backup and not backup_path.exists():
            raise FileNotFoundError(f"Missing migration backup: {backup_path}")
        if not restore_from_backup and not backup_path.exists():
            shutil.copy2(path, backup_path)
        source_path = backup_path if restore_from_backup else path

        with TemporaryDirectory(prefix=".seedance-preroll-", dir=path.parent) as temp_dir_value:
            output_path = Path(temp_dir_value) / path.name
            _migrated_bytes(source_path, output_path)
            shutil.copymode(path, output_path)
            os.replace(output_path, path)

        completed.add(str(relative_path))
        _save_completed(state_path, completed)
        print(relative_path)

    return len(pending)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trim Seedance preroll from existing pet-scene MP4 files.",
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--restore-from-backup", action="store_true")
    args = parser.parse_args()
    count = migrate(
        args.root,
        args.backup_dir,
        dry_run=args.dry_run,
        restore_from_backup=args.restore_from_backup,
    )
    print(f"processed={0 if args.dry_run else count} pending={count if args.dry_run else 0}")


if __name__ == "__main__":
    main()
