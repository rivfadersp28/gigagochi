from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.prompts.world_description_anchors import load_world_description_dataset
from app.services.character_bible_template import (
    character_bible_template_config,
    validate_character_bible_template_config,
)
from app.services.lore_runtime import lore_runtime_config, validate_lore_runtime_config
from app.services.pet_reply_engine.age_message_examples import _dataset as age_speech_dataset
from app.services.pet_reply_engine.speech_runtime import (
    speech_runtime_config,
    validate_speech_runtime_config,
)
from app.services.story_constructor import story_constructor_catalog
from app.services.story_library import _catalog as story_library_catalog
from app.services.story_library import global_story_bricks
from app.services.tone_runtime import tone_runtime_config, validate_tone_runtime_config

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
BACKUP_ROOT_NAME = ".admin-backups"
_WRITE_LOCK_NAME = ".write.lock"
_ADMIN_STORE_THREAD_LOCK = threading.RLock()

AdminDataSource = Literal["local", "production"]


@dataclass(frozen=True)
class ManagedFile:
    file_id: str
    label: str
    relative_path: str
    description: str

    @property
    def path(self) -> Path:
        return DATA_ROOT / self.relative_path


MANAGED_FILES: tuple[ManagedFile, ...] = (
    ManagedFile(
        "speech_runtime",
        "Рантайм характера",
        "speech_runtime.json",
        "Главные правила persona contract, ambient self-prompt и world context для реплик.",
    ),
    ManagedFile(
        "tone_runtime",
        "Generation profile",
        "tone_runtime.json",
        "Одна ручка setting / tone of voice / visual style для реплик, мира, историй и картинок.",
    ),
    ManagedFile(
        "lore_runtime",
        "Библия мира",
        "lore_runtime.json",
        "Единые правила мира для создания персонажей, диалогового лора и историй.",
    ),
    ManagedFile(
        "story_library",
        "Лор в диалоге",
        "story_library.json",
        "Глобальные stories, которые подтягиваются в chat/proactive/ambient по сигналам.",
    ),
    ManagedFile(
        "story_constructor",
        "Сюжетные кирпичики",
        "story_constructor.json",
        "Seed-пулы для путешествий и compact story context.",
    ),
    ManagedFile(
        "age_speech_examples",
        "Фразы по возрастам",
        "age_speech_examples/creature_phrases_dataset.json",
        "Архивные примеры манеры baby/teen/adult; сейчас не подмешиваются в prompt.",
    ),
    ManagedFile(
        "world_descriptions",
        "Якоря мира",
        "world_descriptions/world_descriptions_dataset.json",
        "Якоря среды, из которых при создании собирается template character bible.",
    ),
    ManagedFile(
        "character_bible_template",
        "Шаблон библии персонажа",
        "character_bible_template.json",
        "JSON schema и prompt-правила для новых characterBible, включая voice.catchphrases.",
    ),
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _spec_by_id(file_id: str) -> ManagedFile:
    for spec in MANAGED_FILES:
        if spec.file_id == file_id:
            return spec
    raise KeyError(file_id)


def managed_admin_git_paths() -> tuple[str, ...]:
    return tuple(f"backend/data/{spec.relative_path}" for spec in MANAGED_FILES)


def validate_admin_files_on_disk() -> dict[str, str]:
    errors: dict[str, str] = {}
    with _admin_store_lock(exclusive=False):
        for spec in MANAGED_FILES:
            try:
                content = spec.path.read_text(encoding="utf-8")
                _validate_content(spec, content)
            except FileNotFoundError:
                errors[spec.file_id] = "file is missing"
            except (json.JSONDecodeError, ValueError) as exc:
                errors[spec.file_id] = str(exc)
    return errors


def _validate_json(content: str) -> str:
    parsed = json.loads(content or "{}")
    return json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"


def _validate_content(spec: ManagedFile, content: str) -> str:
    normalized = _validate_json(content)
    if spec.file_id == "speech_runtime":
        validate_speech_runtime_config(json.loads(normalized))
    if spec.file_id == "tone_runtime":
        validate_tone_runtime_config(json.loads(normalized))
    if spec.file_id == "lore_runtime":
        validate_lore_runtime_config(json.loads(normalized))
    if spec.file_id == "character_bible_template":
        validate_character_bible_template_config(json.loads(normalized))
    return normalized


def _summary(spec: ManagedFile, content: str) -> dict[str, Any]:
    if not content.strip():
        return {"status": "missing"}
    parsed = json.loads(content)
    if isinstance(parsed, dict):
        return {
            "topLevelKeys": len(parsed),
            "keys": list(parsed.keys())[:8],
        }
    if isinstance(parsed, list):
        return {"items": len(parsed)}
    return {"type": type(parsed).__name__}


def file_entry_from_content(
    spec: ManagedFile,
    *,
    content: str,
    exists: bool,
    size_bytes: int,
    updated_at: str | None,
) -> dict[str, Any]:
    return {
        "id": spec.file_id,
        "label": spec.label,
        "path": spec.relative_path,
        "format": "json",
        "description": spec.description,
        "exists": exists,
        "sizeBytes": size_bytes,
        "updatedAt": updated_at,
        "summary": _summary(spec, content) if exists else {"status": "missing"},
        "content": content,
    }


def _file_entry(spec: ManagedFile) -> dict[str, Any]:
    path = spec.path
    exists = path.exists()
    content = path.read_text(encoding="utf-8") if exists else ""
    stat = path.stat() if exists else None
    return file_entry_from_content(
        spec,
        content=content,
        exists=exists,
        size_bytes=stat.st_size if stat else 0,
        updated_at=(
            datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
            if stat
            else None
        ),
    )


def read_admin_manifest(
    *,
    mode: AdminDataSource = "local",
    file_entries: list[dict[str, Any]] | None = None,
    deploy_enabled: bool = False,
    deploy_message: str | None = None,
    sync_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if file_entries is None:
        with _admin_store_lock(exclusive=False):
            file_entries = [_file_entry(spec) for spec in MANAGED_FILES]
    return {
        "generatedAt": _now_iso(),
        "mode": mode,
        "files": file_entries,
        "sync": sync_result
        or {
            "status": "disabled",
            "message": "Синхронизация с сервером отключена.",
            "serverCommit": None,
            "updatedAt": _now_iso(),
        },
        "deploy": {
            "enabled": deploy_enabled,
            "message": (
                deploy_message or ("Публикация отключена. После сохранения нужен обычный deploy.")
            ),
        },
    }


def _backup_path(path: Path) -> Path:
    relative = path.relative_to(DATA_ROOT)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    filename = f"{relative.name}.{timestamp}.{os.getpid()}.{uuid.uuid4().hex}.bak"
    return DATA_ROOT / BACKUP_ROOT_NAME / relative.parent / filename


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(path: Path) -> None:
    if path.is_dir():
        return
    path.mkdir(parents=True, exist_ok=True)
    _fsync_directory(path.parent)


@contextmanager
def _admin_store_lock(*, exclusive: bool):
    with _ADMIN_STORE_THREAD_LOCK:
        _ensure_directory(DATA_ROOT)
        backup_root = DATA_ROOT / BACKUP_ROOT_NAME
        _ensure_directory(backup_root)
        lock_path = backup_root / _WRITE_LOCK_NAME
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(
                descriptor,
                fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
            )
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _write_new_file(path: Path, content: bytes, *, mode: int) -> None:
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
        )
        created = True
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_backup(path: Path, content: bytes) -> Path:
    backup_parent = DATA_ROOT / BACKUP_ROOT_NAME / path.relative_to(DATA_ROOT).parent
    _ensure_directory(backup_parent)
    for _attempt in range(8):
        backup = _backup_path(path)
        try:
            _write_new_file(backup, content, mode=0o600)
        except FileExistsError:
            continue
        _fsync_directory(backup.parent)
        return backup
    raise RuntimeError(f"could not allocate a unique admin backup for {path.name}")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    _ensure_directory(path.parent)
    target_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary_created = False
    try:
        _write_new_file(temporary, content, mode=target_mode)
        temporary_created = True
        os.replace(temporary, path)
        temporary_created = False
        _fsync_directory(path.parent)
    finally:
        if temporary_created:
            temporary.unlink(missing_ok=True)


def _atomic_write(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _restore_original(path: Path, content: bytes | None) -> None:
    if content is not None:
        _atomic_write_bytes(path, content)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _clear_runtime_caches() -> None:
    story_constructor_catalog.cache_clear()
    story_library_catalog.cache_clear()
    global_story_bricks.cache_clear()
    load_world_description_dataset.cache_clear()
    age_speech_dataset.cache_clear()
    tone_runtime_config.cache_clear()
    speech_runtime_config.cache_clear()
    character_bible_template_config.cache_clear()
    lore_runtime_config.cache_clear()


def clear_admin_runtime_caches() -> None:
    _clear_runtime_caches()


def save_admin_files(files: list[dict[str, str]]) -> dict[str, Any]:
    normalized: list[tuple[ManagedFile, str]] = []
    errors: dict[str, str] = {}
    seen_file_ids: set[str] = set()

    for item in files:
        file_id = item.get("id", "")
        content = item.get("content", "")
        try:
            spec = _spec_by_id(file_id)
        except KeyError:
            errors[file_id or "<empty>"] = "unknown file id"
            continue
        if spec.file_id in seen_file_ids:
            errors[spec.file_id] = "duplicate file id"
            continue
        seen_file_ids.add(spec.file_id)
        try:
            normalized.append((spec, _validate_content(spec, content)))
        except (json.JSONDecodeError, ValueError) as exc:
            errors[file_id] = str(exc)

    if errors:
        return {
            "saved": False,
            "updatedAt": _now_iso(),
            "errors": errors,
            "files": [],
        }

    saved_files: list[dict[str, Any]] = []
    if normalized:
        with _admin_store_lock(exclusive=True):
            originals = {
                spec.path: spec.path.read_bytes() if spec.path.exists() else None
                for spec, _content in normalized
            }
            backups = {
                path: _create_backup(path, original) if original is not None else None
                for path, original in originals.items()
            }
            attempted_paths: list[Path] = []
            try:
                for spec, content in normalized:
                    path = spec.path
                    attempted_paths.append(path)
                    _atomic_write(path, content)
                    backup = backups[path]
                    saved_files.append(
                        {
                            "id": spec.file_id,
                            "path": spec.relative_path,
                            "backupPath": (str(backup.relative_to(DATA_ROOT)) if backup else None),
                            "sizeBytes": len(content.encode("utf-8")),
                        }
                    )
            except Exception as write_error:
                rollback_errors: list[OSError] = []
                for path in reversed(attempted_paths):
                    try:
                        _restore_original(path, originals[path])
                    except OSError as rollback_error:
                        rollback_errors.append(rollback_error)
                if rollback_errors:
                    raise RuntimeError(
                        "admin save failed and one or more files could not be restored"
                    ) from write_error
                raise
            _clear_runtime_caches()

    return {
        "saved": True,
        "updatedAt": _now_iso(),
        "errors": {},
        "files": saved_files,
    }
