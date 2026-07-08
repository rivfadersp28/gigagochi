from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "tone_runtime.json"

REQUIRED_PRESET_STRING_KEYS = (
    "label",
    "voice",
    "worldMood",
    "conflictPolicy",
    "agePolicy",
    "visualStyle",
    "avoid",
)
SURFACE_KEYS = (
    "visibleReply",
    "contextRouting",
    "worldContext",
    "characterBible",
    "backgroundStory",
    "travelStory",
    "storyboard",
    "imagePrompt",
)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _path_label(path: tuple[str, ...] | list[str]) -> str:
    return ".".join(path)


def _read_path(config: dict[str, Any], path: tuple[str, ...] | list[str]) -> Any:
    current: Any = config
    for key in path:
        if not _is_record(current) or key not in current:
            raise ValueError(f"missing required key: {_path_label(path)}")
        current = current[key]
    return current


def _required_string(
    config: dict[str, Any],
    path: tuple[str, ...] | list[str],
) -> str:
    value = _read_path(config, path)
    if not isinstance(value, str):
        raise ValueError(f"{_path_label(path)} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{_path_label(path)} must not be empty")
    return result


def _active_preset_from_config(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    active_preset = _required_string(config, ("activePreset",))
    presets = _read_path(config, ("presets",))
    if not _is_record(presets):
        raise ValueError("presets must be a JSON object")
    preset = presets.get(active_preset)
    if not _is_record(preset):
        raise ValueError(f"activePreset {active_preset!r} is not configured")
    return active_preset, preset


def validate_tone_runtime_config(config: Any) -> None:
    if not _is_record(config):
        raise ValueError("tone_runtime must be a JSON object")
    if _required_string(config, ("meta", "format")) != "tamagochi-tone-runtime-v1":
        raise ValueError("meta.format must be tamagochi-tone-runtime-v1")
    _active_preset, preset = _active_preset_from_config(config)
    for key in REQUIRED_PRESET_STRING_KEYS:
        _required_string(preset, (key,))
    surfaces = _read_path(preset, ("surfaces",))
    if not _is_record(surfaces):
        raise ValueError("active preset surfaces must be a JSON object")
    for key in SURFACE_KEYS:
        _required_string(preset, ("surfaces", key))


@lru_cache(maxsize=1)
def tone_runtime_config() -> dict[str, Any]:
    if not DATA_PATH.exists():
        raise RuntimeError(f"{DATA_PATH} is missing")
    try:
        parsed = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid tone_runtime.json: {exc.msg}") from exc
    try:
        validate_tone_runtime_config(parsed)
    except ValueError as exc:
        raise RuntimeError(f"Invalid tone_runtime.json: {exc}") from exc
    return parsed


def active_tone_id() -> str:
    tone_id, _preset = _active_preset_from_config(tone_runtime_config())
    return tone_id


def active_tone_preset() -> dict[str, Any]:
    _tone_id, preset = _active_preset_from_config(tone_runtime_config())
    return json.loads(json.dumps(preset))


def _surface_rule(preset: dict[str, Any], surface: str) -> str:
    surfaces = preset.get("surfaces")
    if not _is_record(surfaces):
        return ""
    value = surfaces.get(surface)
    return value.strip() if isinstance(value, str) else ""


def tone_context_payload(surface: str) -> dict[str, str]:
    preset = active_tone_preset()
    return {
        "preset": active_tone_id(),
        "label": _required_string(preset, ("label",)),
        "voice": _required_string(preset, ("voice",)),
        "worldMood": _required_string(preset, ("worldMood",)),
        "conflictPolicy": _required_string(preset, ("conflictPolicy",)),
        "agePolicy": _required_string(preset, ("agePolicy",)),
        "avoid": _required_string(preset, ("avoid",)),
        "surfaceRule": _surface_rule(preset, surface),
    }


def tone_prompt_block(surface: str) -> str:
    preset = active_tone_preset()
    surface_rule = _surface_rule(preset, surface)
    lines = [
        "TONE_PROFILE:",
        f"- preset: {active_tone_id()} ({_required_string(preset, ('label',))})",
        f"- voice: {_required_string(preset, ('voice',))}",
        f"- world mood: {_required_string(preset, ('worldMood',))}",
        f"- conflict policy: {_required_string(preset, ('conflictPolicy',))}",
        f"- age policy: {_required_string(preset, ('agePolicy',))}",
        f"- avoid: {_required_string(preset, ('avoid',))}",
    ]
    if surface_rule:
        lines.append(f"- surface rule: {surface_rule}")
    return "\n".join(lines)


def tone_visual_style(surface: str = "imagePrompt") -> str:
    preset = active_tone_preset()
    surface_rule = _surface_rule(preset, surface)
    parts = [
        f"TONE_PROFILE: {active_tone_id()} ({_required_string(preset, ('label',))})",
        _required_string(preset, ("visualStyle",)),
        surface_rule,
    ]
    return "\n".join(part for part in parts if part)
