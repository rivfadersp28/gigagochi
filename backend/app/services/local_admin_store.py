from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.prompts.world_description_anchors import load_world_description_dataset
from app.services.character_bible_template import (
    character_bible_template_config,
    validate_character_bible_template_config,
)
from app.services.pet_reply_engine.age_message_examples import _dataset as age_speech_dataset
from app.services.pet_reply_engine.speech_runtime import (
    speech_runtime_config,
    validate_speech_runtime_config,
)
from app.services.story_constructor import story_constructor_catalog
from app.services.story_library import _catalog as story_library_catalog
from app.services.story_library import global_story_bricks
from app.services.travel_service import _travel_template_catalog

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
BACKUP_ROOT_NAME = ".admin-backups"

FileFormat = Literal["json", "jsonl"]
AdminDataSource = Literal["local", "production"]


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
        "Главные правила persona contract, ambient self-prompt и world context для реплик.",
    ),
    ManagedFile(
        "story_library",
        "Лор в диалоге",
        "story_library.json",
        "json",
        "Глобальные stories, которые подтягиваются в chat/proactive/ambient по сигналам.",
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
        "character_bible_template",
        "Шаблон библии персонажа",
        "character_bible_template.json",
        "json",
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
        normalized = _validate_json(content)
        if spec.file_id == "speech_runtime":
            validate_speech_runtime_config(json.loads(normalized))
        if spec.file_id == "character_bible_template":
            validate_character_bible_template_config(json.loads(normalized))
        return normalized
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
        "format": spec.file_format,
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


def dialogue_influence_manifest() -> dict[str, Any]:
    surfaces = ["chat", "proactive", "ambient", "push"]
    return {
        "modifiers": [
            {
                "id": "identity_line",
                "label": "Identity line",
                "surfaces": surfaces,
                "source": "pet",
                "editable": False,
                "fileId": None,
                "configPath": None,
                "summary": (
                    "Имя, description и текущая стадия питомца формируют базовое "
                    "«Отвечай мне как ...»."
                ),
            },
            {
                "id": "pet_profile",
                "label": "Pet profile and overlays",
                "surfaces": surfaces,
                "source": "localStorage",
                "editable": False,
                "fileId": None,
                "configPath": "assetSet.characterBible / extensions.lite_overlay",
                "summary": (
                    "Текущий characterBible, lite_overlay, имя, описание, стадия, "
                    "настроение и параметры конкретного питомца."
                ),
            },
            {
                "id": "conversation_context",
                "label": "Conversation context",
                "surfaces": ["chat", "ambient"],
                "source": "request + localStorage",
                "editable": False,
                "fileId": None,
                "configPath": "message / history / recentAmbientReplies",
                "summary": (
                    "Текущая реплика пользователя и последние сообщения чата; idle "
                    "использует только recentAmbientReplies через {recent_replies}."
                ),
            },
            {
                "id": "proactive_reason",
                "label": "Proactive reason",
                "surfaces": ["proactive", "push"],
                "source": "localStorage memory recall / push scheduler",
                "editable": False,
                "fileId": None,
                "configPath": "memoryContext.proactiveCandidate.reason / push.reason",
                "summary": (
                    "Повод для самостоятельной proactive-реплики или Telegram push "
                    "передается как контекст, без отдельного набора hidden rules."
                ),
            },
            {
                "id": "state_layer",
                "label": "Возраст, настроение, голод, энергия",
                "surfaces": surfaces,
                "source": "speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "stateLayer",
                "summary": (
                    "Age hints, пороги и словесные подписи голода/счастья/энергии, "
                    "а также короткие state-модификаторы."
                ),
            },
            {
                "id": "surface_prompts",
                "label": "Surface prompts",
                "surfaces": surfaces,
                "source": "speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "surfacePrompts",
                "summary": (
                    "Единые prompt-поля для chat, proactive, idle и Telegram push "
                    "без отдельных hidden rules/user prompts."
                ),
            },
            {
                "id": "identity_template",
                "label": "Identity template",
                "surfaces": surfaces,
                "source": "speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "identityTemplate",
                "summary": (
                    "Один шаблон identity/age/state/reply-limit строки для всех "
                    "видимых реплик."
                ),
            },
            {
                "id": "user_memory_prompt",
                "label": "User memory block",
                "surfaces": surfaces,
                "source": "localStorage + speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "memoryUsageRule",
                "summary": (
                    "Профиль, summary и relevantMemories владельца; для idle "
                    "фильтруются deadline/event."
                ),
            },
            {
                "id": "world_context_prompt",
                "label": "WORLD_CONTEXT framing",
                "surfaces": surfaces,
                "source": "speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "worldContext",
                "summary": "Обертка для уже выбранных stories перед финальной генерацией.",
            },
            {
                "id": "context_routing",
                "label": "Context routing",
                "surfaces": surfaces,
                "source": "speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "contextRouting",
                "summary": (
                    "Единый LLM-router решает, подключать ли world context, "
                    "character profile, user memory, chat history и recent replies."
                ),
            },
            {
                "id": "context_sources",
                "label": "Копилки контекста",
                "surfaces": [*surfaces, "background_story"],
                "source": "speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "contextSources",
                "summary": "Единая матрица disabled/auto/always для подключаемых копилок.",
            },
            {
                "id": "chat_tools",
                "label": "Chat tool definitions",
                "surfaces": ["chat"],
                "source": "backend runtime",
                "editable": False,
                "fileId": None,
                "configPath": "update_pet_name / read_character_json",
                "summary": (
                    "Модель может переименовать питомца; чтение character JSON доступно, "
                    "когда contextSources и router разрешили characterProfile."
                ),
            },
            {
                "id": "reply_limits",
                "label": "Reply length limits",
                "surfaces": surfaces,
                "source": "request + backend runtime",
                "editable": False,
                "fileId": None,
                "configPath": "replyMaxChars / MAX_REPLY_CHARS",
                "summary": (
                    "Лимит символов попадает в identity line и влияет на форму всех "
                    "видимых реплик."
                ),
            },
            {
                "id": "memory_extractors",
                "label": "Фоновые extractors",
                "surfaces": ["chat"],
                "source": "speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "characterMemory / userMemory",
                "summary": (
                    "После ответа сохраняют новые факты персонажа, stories и "
                    "память владельца."
                ),
            },
            {
                "id": "background_story_prompt",
                "label": "Фоновые истории",
                "surfaces": ["background_story"],
                "source": "speech_runtime",
                "editable": True,
                "fileId": "speech_runtime",
                "configPath": "backgroundStory",
                "summary": (
                    "Prompt генерации фонового события и лимиты текста; источники "
                    "берутся из contextSources."
                ),
            },
        ],
        "collections": [
            {
                "id": "story_library",
                "label": "Global story library",
                "role": "rag",
                "surfaces": surfaces,
                "source": "backend/data",
                "editable": True,
                "fileId": "story_library",
                "configPath": "pools",
                "summary": (
                    "Основная RAG-коллекция для WORLD_CONTEXT; подключается только "
                    "решением contextRouting.worldContext."
                ),
            },
            {
                "id": "story_library_overlay",
                "label": "Per-pet stories",
                "role": "rag",
                "surfaces": surfaces,
                "source": "localStorage",
                "editable": False,
                "fileId": None,
                "configPath": "characterBible.extensions.story_library_overlay",
                "summary": (
                    "Личные stories конкретного питомца, накопленные из диалога; при поиске "
                    "идут перед global. Для /story не используются."
                ),
            },
            {
                "id": "user_memory",
                "label": "User memory",
                "role": "memory",
                "surfaces": surfaces,
                "source": "localStorage",
                "editable": False,
                "fileId": None,
                "configPath": "LocalPetMemoryStateV1",
                "summary": (
                    "Память владельца: recall для chat/proactive/push и мягко "
                    "отфильтрованный контекст для idle."
                ),
            },
            {
                "id": "age_speech_examples",
                "label": "Age speech examples",
                "role": "examples",
                "surfaces": ["chat"],
                "source": "backend/data",
                "editable": True,
                "fileId": "age_speech_examples",
                "configPath": "creature_phrases_dataset",
                "summary": (
                    "Примеры детской манеры для baby-стадии; используются как "
                    "rhythm/examples, не как шаблон."
                ),
            },
            {
                "id": "story_constructor",
                "label": "Story constructor",
                "role": "travel",
                "surfaces": [],
                "source": "backend/data",
                "editable": True,
                "fileId": "story_constructor",
                "configPath": "pools",
                "summary": "Не влияет на обычный диалог; используется travel/adventure pipeline.",
            },
            {
                "id": "travel_story_templates",
                "label": "Travel templates",
                "role": "travel",
                "surfaces": [],
                "source": "backend/data",
                "editable": True,
                "fileId": "travel_story_templates",
                "configPath": "templates",
                "summary": "Не влияет на chat/idle/proactive; задает структуру приключений.",
            },
            {
                "id": "world_descriptions",
                "label": "World description anchors",
                "role": "creation",
                "surfaces": [],
                "source": "backend/data",
                "editable": True,
                "fileId": "world_descriptions",
                "configPath": "dataset",
                "summary": (
                    "Влияет на создание template character bible, но не подтягивается "
                    "в текущий диалог напрямую."
                ),
            },
            {
                "id": "character_bible_template",
                "label": "Character bible template",
                "role": "creation",
                "surfaces": [],
                "source": "backend/data",
                "editable": True,
                "fileId": "character_bible_template",
                "configPath": "schema / prompt / runtimeMappings",
                "summary": (
                    "Шаблон новых characterBible: JSON schema, prompt-правила и mapping "
                    "voice.catchphrases -> lore.voice.favorite_phrases."
                ),
            },
        ],
    }


def read_admin_manifest(
    *,
    mode: AdminDataSource = "local",
    file_entries: list[dict[str, Any]] | None = None,
    deploy_enabled: bool = False,
    deploy_message: str | None = None,
    sync_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "generatedAt": _now_iso(),
        "mode": mode,
        "files": (
            file_entries
            if file_entries is not None
            else [_file_entry(spec) for spec in MANAGED_FILES]
        ),
        "dialogue": dialogue_influence_manifest(),
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
    character_bible_template_config.cache_clear()


def clear_admin_runtime_caches() -> None:
    _clear_runtime_caches()


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
