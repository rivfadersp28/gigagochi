from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "lore_runtime.json"

LoreSurface = Literal[
    "characterCreation",
    "backgroundStory",
    "dialogueLore",
    "worldSeed",
]

WORLD_LIST_KEYS = (
    "environmentPalette",
    "materialPalette",
    "continuityRules",
    "toneRange",
)
WORLD_STRING_KEYS = (
    "premise",
    "technologyRule",
    "magicRule",
)
SURFACES: tuple[LoreSurface, ...] = (
    "characterCreation",
    "backgroundStory",
    "dialogueLore",
    "worldSeed",
)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _required_string(config: dict[str, Any], *path: str) -> str:
    current: Any = config
    for key in path:
        if not _is_record(current) or key not in current:
            raise ValueError(f"missing required key: {'.'.join(path)}")
        current = current[key]
    if not isinstance(current, str) or not current.strip():
        raise ValueError(f"{'.'.join(path)} must be a non-empty string")
    return current.strip()


def _required_string_list(config: dict[str, Any], *path: str) -> list[str]:
    current: Any = config
    for key in path:
        if not _is_record(current) or key not in current:
            raise ValueError(f"missing required key: {'.'.join(path)}")
        current = current[key]
    if not isinstance(current, list):
        raise ValueError(f"{'.'.join(path)} must be a list")
    values = [item.strip() for item in current if isinstance(item, str) and item.strip()]
    if not values:
        raise ValueError(f"{'.'.join(path)} must contain strings")
    return values


def validate_lore_runtime_config(config: Any) -> None:
    if not _is_record(config):
        raise ValueError("lore_runtime must be a JSON object")
    for key in WORLD_STRING_KEYS:
        _required_string(config, "world", key)
    for key in WORLD_LIST_KEYS:
        _required_string_list(config, "world", key)
    for surface in SURFACES:
        _required_string(config, "surfaces", surface)


@lru_cache(maxsize=1)
def lore_runtime_config() -> dict[str, Any]:
    try:
        parsed = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"{DATA_PATH} is missing") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid lore_runtime.json: {exc.msg}") from exc
    try:
        validate_lore_runtime_config(parsed)
    except ValueError as exc:
        raise RuntimeError(f"Invalid lore_runtime.json: {exc}") from exc
    return parsed


def lore_world_payload() -> dict[str, Any]:
    return json.loads(json.dumps(lore_runtime_config()["world"], ensure_ascii=False))


def lore_prompt_block(surface: LoreSurface) -> str:
    config = lore_runtime_config()
    world = config["world"]

    def lines(key: str) -> str:
        return "\n".join(f"- {item}" for item in world[key])

    return (
        "ОБЩАЯ БИБЛИЯ МИРА:\n"
        f"Основа: {world['premise']}\n"
        f"Пространства:\n{lines('environmentPalette')}\n"
        f"Материалы и фактура:\n{lines('materialPalette')}\n"
        f"Технологии: {world['technologyRule']}\n"
        f"Необычное: {world['magicRule']}\n"
        f"Правила непрерывности:\n{lines('continuityRules')}\n"
        f"Допустимый диапазон тона: {', '.join(world['toneRange'])}.\n"
        f"Задача поверхности: {config['surfaces'][surface]}"
    )


def clear_lore_runtime_cache() -> None:
    lore_runtime_config.cache_clear()
