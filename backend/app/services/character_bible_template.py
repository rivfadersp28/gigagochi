from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "character_bible_template.json"

LEGACY_DEFAULT_PATHS: tuple[tuple[str, ...], ...] = (
    ("legacyDefaults", "identityRole"),
    ("legacyDefaults", "voiceRhythm"),
    ("legacyDefaults", "addressingUser"),
    ("legacyDefaults", "humorStyle"),
    ("legacyDefaults", "uncertaintyStyle"),
    ("legacyDefaults", "initiativeStyle"),
    ("legacyDefaults", "attitudeToUser"),
    ("legacyDefaults", "provenanceLicenseNotes"),
)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _string_at(config: dict[str, Any], path: tuple[str, ...], *, allow_empty: bool = False) -> str:
    current: Any = config
    for key in path:
        if not _is_record(current) or key not in current:
            raise ValueError(f"missing required key: {'.'.join(path)}")
        current = current[key]
    if not isinstance(current, str):
        raise ValueError(f"{'.'.join(path)} must be a string")
    value = current.strip()
    if not value and not allow_empty:
        raise ValueError(f"{'.'.join(path)} must not be empty")
    return value


def _string_list_at(config: dict[str, Any], path: tuple[str, ...]) -> tuple[str, ...]:
    current: Any = config
    for key in path:
        if not _is_record(current) or key not in current:
            raise ValueError(f"missing required key: {'.'.join(path)}")
        current = current[key]
    if not isinstance(current, list):
        raise ValueError(f"{'.'.join(path)} must be a list")
    result = tuple(item.strip() for item in current if isinstance(item, str) and item.strip())
    if not result:
        raise ValueError(f"{'.'.join(path)} must contain at least one string")
    return result


def validate_character_bible_template_config(config: Any) -> None:
    if not _is_record(config):
        raise ValueError("character_bible_template must be a JSON object")
    _string_at(config, ("systemPrompt",))
    _string_at(config, ("prompt", "intro"))
    _string_list_at(config, ("prompt", "personaShape"))
    _string_at(config, ("prompt", "generationRule"))
    _string_list_at(config, ("prompt", "topLevelFields"))
    _string_list_at(config, ("prompt", "languageRules"))
    _string_list_at(config, ("prompt", "rules"))
    for path in LEGACY_DEFAULT_PATHS:
        _string_at(config, path)
    schema = config.get("schema")
    if not _is_record(schema):
        raise ValueError("schema must be a JSON object")
    if schema.get("type") != "object":
        raise ValueError("schema.type must be object")
    properties = schema.get("properties")
    if not _is_record(properties):
        raise ValueError("schema.properties must be a JSON object")
    voice = properties.get("voice")
    if not _is_record(voice):
        raise ValueError("schema.properties.voice must be configured")
    voice_properties = voice.get("properties")
    if not _is_record(voice_properties) or "catchphrases" not in voice_properties:
        raise ValueError("schema.properties.voice.properties.catchphrases must be configured")


@lru_cache(maxsize=1)
def character_bible_template_config() -> dict[str, Any]:
    if not DATA_PATH.exists():
        raise RuntimeError(f"{DATA_PATH} is missing")
    try:
        parsed = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid character_bible_template.json: {exc.msg}") from exc
    try:
        validate_character_bible_template_config(parsed)
    except ValueError as exc:
        raise RuntimeError(f"Invalid character_bible_template.json: {exc}") from exc
    return parsed


def character_bible_schema() -> dict[str, Any]:
    schema = character_bible_template_config()["schema"]
    return json.loads(json.dumps(schema))


def character_bible_system_prompt() -> str:
    return _string_at(character_bible_template_config(), ("systemPrompt",))


def character_bible_prompt_config() -> dict[str, Any]:
    prompt = character_bible_template_config()["prompt"]
    return json.loads(json.dumps(prompt))


def character_bible_legacy_defaults() -> dict[str, str]:
    config = character_bible_template_config()
    return {
        path[-1]: _string_at(config, path)
        for path in LEGACY_DEFAULT_PATHS
    }
