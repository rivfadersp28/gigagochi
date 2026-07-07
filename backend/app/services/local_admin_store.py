from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.prompts.world_description_anchors import load_world_description_dataset
from app.services.pet_reply_engine.age_message_examples import _dataset as age_speech_dataset
from app.services.pet_reply_engine.speech_runtime import speech_runtime_config
from app.services.story_constructor import story_constructor_catalog
from app.services.story_library import _catalog as story_library_catalog
from app.services.story_library import global_story_bricks
from app.services.travel_service import _travel_template_catalog

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
BACKUP_ROOT_NAME = ".admin-backups"

FileFormat = Literal["json", "jsonl"]


@dataclass(frozen=True)
class ManagedFile:
    file_id: str
    label: str
    relative_path: str
    file_format: FileFormat
    description: str

    @property
    def path(self) -> Path:
        return DATA_ROOT / self.relative_path


MANAGED_FILES: tuple[ManagedFile, ...] = (
    ManagedFile(
        "speech_runtime",
        "Рантайм характера",
        "speech_runtime.json",
        "json",
        "Главные правила persona contract, ambient moves и world context для реплик.",
    ),
    ManagedFile(
        "story_library",
        "Лор в диалоге",
        "story_library.json",
        "json",
        "Глобальные story bricks, которые подтягиваются в chat/proactive/ambient по сигналам.",
    ),
    ManagedFile(
        "story_constructor",
        "Сюжетные кирпичики",
        "story_constructor.json",
        "json",
        "Seed-пулы для путешествий и compact story context.",
    ),
    ManagedFile(
        "travel_story_templates",
        "Шаблоны приключений",
        "travel_story_templates.json",
        "json",
        "Структурные шаблоны приключений; не финальная проза.",
    ),
    ManagedFile(
        "age_speech_examples",
        "Фразы по возрастам",
        "age_speech_examples/creature_phrases_dataset.json",
        "json",
        "Примеры манеры baby/teen/adult; быстрый слой настройки тона персонажей.",
    ),
    ManagedFile(
        "world_descriptions",
        "Якоря мира",
        "world_descriptions/world_descriptions_dataset.json",
        "json",
        "Якоря среды, из которых при создании собирается template character bible.",
    ),
    ManagedFile(
        "external_character_sources",
        "Внешние фрагменты",
        "external_character_sources/fragments.jsonl",
        "jsonl",
        "Справочный JSONL-корпус; текущий runtime создания не читает его напрямую.",
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


def _validate_jsonl(content: str) -> str:
    lines: list[str] = []
    for index, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {index}: {exc.msg}") from exc
        lines.append(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + ("\n" if lines else "")


def _validate_content(spec: ManagedFile, content: str) -> str:
    if spec.file_format == "json":
        return _validate_json(content)
    return _validate_jsonl(content)


def _summary(spec: ManagedFile, content: str) -> dict[str, Any]:
    if not content.strip():
        return {"status": "missing"}
    if spec.file_format == "jsonl":
        return {"lines": len([line for line in content.splitlines() if line.strip()])}
    parsed = json.loads(content)
    if isinstance(parsed, dict):
        return {
            "topLevelKeys": len(parsed),
            "keys": list(parsed.keys())[:8],
        }
    if isinstance(parsed, list):
        return {"items": len(parsed)}
    return {"type": type(parsed).__name__}


def _file_entry(spec: ManagedFile) -> dict[str, Any]:
    path = spec.path
    exists = path.exists()
    content = path.read_text(encoding="utf-8") if exists else ""
    stat = path.stat() if exists else None
    return {
        "id": spec.file_id,
        "label": spec.label,
        "path": spec.relative_path,
        "format": spec.file_format,
        "description": spec.description,
        "exists": exists,
        "sizeBytes": stat.st_size if stat else 0,
        "updatedAt": (
            datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
            if stat
            else None
        ),
        "summary": _summary(spec, content) if exists else {"status": "missing"},
        "content": content,
    }


def read_admin_manifest(
    *,
    deploy_enabled: bool = False,
    deploy_message: str | None = None,
) -> dict[str, Any]:
    return {
        "generatedAt": _now_iso(),
        "mode": "local",
        "files": [_file_entry(spec) for spec in MANAGED_FILES],
        "deploy": {
            "enabled": deploy_enabled,
            "message": (
                deploy_message
                or (
                    "Публикация отключена. "
                    "После сохранения нужен обычный deploy."
                )
            ),
        },
    }


def _backup_path(path: Path) -> Path:
    relative = path.relative_to(DATA_ROOT)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DATA_ROOT / BACKUP_ROOT_NAME / relative.parent / f"{relative.name}.{timestamp}.bak"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def _clear_runtime_caches() -> None:
    story_constructor_catalog.cache_clear()
    story_library_catalog.cache_clear()
    global_story_bricks.cache_clear()
    _travel_template_catalog.cache_clear()
    load_world_description_dataset.cache_clear()
    age_speech_dataset.cache_clear()
    speech_runtime_config.cache_clear()


def save_admin_files(files: list[dict[str, str]]) -> dict[str, Any]:
    normalized: list[tuple[ManagedFile, str]] = []
    errors: dict[str, str] = {}

    for item in files:
        file_id = item.get("id", "")
        content = item.get("content", "")
        try:
            spec = _spec_by_id(file_id)
        except KeyError:
            errors[file_id or "<empty>"] = "unknown file id"
            continue
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
    for spec, content in normalized:
        path = spec.path
        backup = None
        if path.exists():
            backup = _backup_path(path)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        _atomic_write(path, content)
        saved_files.append(
            {
                "id": spec.file_id,
                "path": spec.relative_path,
                "backupPath": str(backup.relative_to(DATA_ROOT)) if backup else None,
                "sizeBytes": path.stat().st_size,
            }
        )

    if saved_files:
        _clear_runtime_caches()

    return {
        "saved": True,
        "updatedAt": _now_iso(),
        "errors": {},
        "files": saved_files,
    }
