from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "tone_runtime.json"

REQUIRED_PRESET_STRING_KEYS = (
    "label",
    "setting",
    "toneOfVoice",
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


def _profile_setting(preset: dict[str, Any]) -> str:
    return _required_string(preset, ("setting",))


def _profile_tone_of_voice(preset: dict[str, Any]) -> str:
    return _required_string(preset, ("toneOfVoice",))


def tone_context_payload(surface: str) -> dict[str, str]:
    preset = active_tone_preset()
    setting = _profile_setting(preset)
    tone_of_voice = _profile_tone_of_voice(preset)
    return {
        "setting": setting,
        "toneOfVoice": tone_of_voice,
    }


def tone_prompt_block(surface: str) -> str:
    preset = active_tone_preset()
    return "\n".join([
        "GENERATION_PROFILE:",
        f"- setting: {_profile_setting(preset)}",
        f"- tone: {_profile_tone_of_voice(preset)}",
    ])


def tone_visual_style(surface: str = "imagePrompt") -> str:
    preset = active_tone_preset()
    return "\n".join([
        "GENERATION_PROFILE:",
        f"- setting: {_profile_setting(preset)}",
        f"- tone: {_profile_tone_of_voice(preset)}",
    ])
