from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from app.services.pet_reply_engine.age_message_examples import stage_label, stage_speech_rules
from app.services.pet_reply_engine.models import PetAgeStage

SHORT_STAGE_LABELS: dict[PetAgeStage, str] = {
    "baby": "малыш",
    "teen": "подросток",
    "adult": "взрослый",
}


@dataclass(frozen=True)
class PetAgeBehaviorProfile:
    label: str
    voice_description: str
    behavior_rules: tuple[str, ...]
    low_energy_rule: str
    hungry_rule: str


def _profile(
    age_stage: PetAgeStage,
    *,
    low_energy_rule: str,
    hungry_rule: str,
) -> PetAgeBehaviorProfile:
    dataset_label = stage_label(age_stage)
    rules = stage_speech_rules(age_stage)
    return PetAgeBehaviorProfile(
        label=SHORT_STAGE_LABELS[age_stage],
        voice_description=(
            f"{dataset_label}. Форма речи берется из message examples этой стадии."
        ),
        behavior_rules=rules,
        low_energy_rule=low_energy_rule,
        hungry_rule=hungry_rule,
    )


AGE_BEHAVIOR_PROFILES: dict[PetAgeStage, PetAgeBehaviorProfile] = {
    "baby": _profile(
        "baby",
        low_energy_rule=(
            "При низкой энергии оставь малышовую форму, но сделай ее еще короче: "
            "1-2 маленьких фразы, сонные междометия, меньше инициативы."
        ),
        hungry_rule=(
            "При сильном голоде выбирай hungry-примеры: прямые просьбы еды, 'ням', "
            "капризность и повторы допустимы."
        ),
    ),
    "teen": _profile(
        "teen",
        low_energy_rule=(
            "При низкой энергии сохраняй подростковую защитную манеру: короче, ворчливее, "
            "можно делать вид, что все нормально."
        ),
        hungry_rule=(
            "При сильном голоде выбирай hungry/angry-примеры: раздражение, бравада и "
            "непрямая просьба еды допустимы."
        ),
    ),
    "adult": _profile(
        "adult",
        low_energy_rule=(
            "При низкой энергии сохраняй взрослую форму: тише, спокойнее, меньше слов, "
            "но без детской манеры."
        ),
        hungry_rule=(
            "При сильном голоде выбирай hungry-примеры: прямолинейность и сухой юмор "
            "уместны, но без потери самоконтроля."
        ),
    ),
}

TEMPLATE_SOURCE_AGE_RULE = (
    "Выбранная стадия возраста в приложении задает текущий возрастной режим персонажа. "
    "Любые исходные Age/years old/лет/mid-thirties из template preset являются метаданными "
    "донорской карточки и не являются каноном реплик. Не говори, что тебе 19, 22, 26, 35, "
    "за 30 или другой числовой возраст; если спрашивают возраст, отвечай через текущую "
    "стадию и свое самоощущение."
)

SOURCE_AGE_PLACEHOLDER = "текущая возрастная стадия задается приложением"
SOURCE_AGE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\bAge\s*\(\s*[^)]*\)", SOURCE_AGE_PLACEHOLDER),
    (r"\bAge\s*:\s*[^\n.;]+[.;]?", SOURCE_AGE_PLACEHOLDER),
    (
        r"\b(?:appears|looks|resembles)\s+(?:a\s+)?(?:young\s+adult|"
        r"(?:early|mid|late)[-\s]*(?:teens|twenties|thirties|forties)|"
        r"around\s+\*{0,2}\d{1,3}\*{0,2}\s+years?\s+old)[^\n.;]*[.;]?",
        SOURCE_AGE_PLACEHOLDER,
    ),
    (r"\b\d{1,3}\s*[- ]?year[- ]old\b", SOURCE_AGE_PLACEHOLDER),
    (r"\b\d{1,3}\s+years?\s+old\b", SOURCE_AGE_PLACEHOLDER),
    (r"\b(?:Возраст|возраст)\s*:\s*[^\n.;]+[.;]?", SOURCE_AGE_PLACEHOLDER),
    (r"\b\d{1,3}\s*(?:лет|года|год)\b", SOURCE_AGE_PLACEHOLDER),
    (r"\bза\s+(?:\d{2}|тридц\w+|сорок\w+)\b", SOURCE_AGE_PLACEHOLDER),
)

AGE_STAGE_VOICE_DESCRIPTIONS: dict[PetAgeStage, str] = {
    stage: profile.voice_description
    for stage, profile in AGE_BEHAVIOR_PROFILES.items()
}


def normalize_age_stage(value: str | None) -> PetAgeStage:
    if value in AGE_BEHAVIOR_PROFILES:
        return cast(PetAgeStage, value)
    return "baby"


def age_stage_voice_description(value: str | None) -> str:
    return AGE_BEHAVIOR_PROFILES[normalize_age_stage(value)].voice_description


def replace_source_age_claims_in_text(text: str) -> str:
    result = text
    for pattern, replacement in SOURCE_AGE_REPLACEMENTS:
        result = re.sub(pattern, replacement, result, flags=re.I)
    return re.sub(r"[ \t]{2,}", " ", result).strip()


def sanitize_source_age_claims(value: Any) -> Any:
    if isinstance(value, str):
        return replace_source_age_claims_in_text(value)
    if isinstance(value, list):
        return [sanitize_source_age_claims(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_source_age_claims(item) for key, item in value.items()}
    return value


def format_age_behavior_profile_for_prompt(value: str | None) -> str:
    profile = AGE_BEHAVIOR_PROFILES[normalize_age_stage(value)]
    lines = [
        f"- текущая стадия: {profile.label}",
        f"- кратко: {profile.voice_description}",
        "- это не абстрактная подсказка: speech_rules ниже взяты из message examples dataset "
        "и задают форму речи текущей стадии.",
        "- индивидуальная Библия персонажа важнее для имени, характера, лора, background и "
        "устойчивых фактов; выбранная стадия возраста важнее для формы реплики.",
        f"- {TEMPLATE_SOURCE_AGE_RULE}",
    ]
    lines.extend(f"- {rule}" for rule in profile.behavior_rules)
    lines.append(f"- низкая энергия: {profile.low_energy_rule}")
    lines.append(f"- сильный голод: {profile.hungry_rule}")
    return "\n".join(lines)
