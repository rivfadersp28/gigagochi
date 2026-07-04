from __future__ import annotations

from app.services.pet_reply_engine.models import EnergyBand, PetAgeStage, PetTextStyle

_STYLE_BY_AGE: dict[PetAgeStage, PetTextStyle] = {
    "baby": PetTextStyle(
        max_words=3,
        max_chars=36,
        sentence_limit=1,
        style_rules=(
            "одно-два смысловых слова, максимум звук плюс короткое слово",
            "чаще используй детский звук, подходящий образу питомца",
            "можно ласковые короткие слова: лапки, ушки, хвостик, листик",
            "без советов, объяснений, метафор и абстрактных слов",
        ),
    ),
    "teen": PetTextStyle(
        max_words=18,
        max_chars=160,
        sentence_limit=2,
        style_rules=(
            "одна-две короткие фразы",
            "можно больше эмоции, но формулировка должна быть простой",
            "без токсичности и пассивной агрессии",
        ),
    ),
    "adult": PetTextStyle(
        max_words=20,
        max_chars=180,
        sentence_limit=2,
        style_rules=(
            "спокойная взрослая реплика",
            "можно короткую заботливую фразу",
            "без образных объяснений, сложных метафор и детских уменьшительных форм",
        ),
    ),
}

_LORE_STYLE_BY_AGE: dict[PetAgeStage, PetTextStyle] = {
    "baby": PetTextStyle(
        max_words=6,
        max_chars=48,
        sentence_limit=1,
        style_rules=(
            "одна простая деталь про дом, мир или прошлое",
            "сохраняй малышовый звук и очень короткую фразу",
        ),
    ),
    "teen": PetTextStyle(
        max_words=55,
        max_chars=420,
        sentence_limit=4,
        style_rules=(
            "ответь 2-4 связными короткими предложениями",
            "держись вопроса собеседника и деталей лора",
            "можно добавить мелкую фактуру, если она не меняет канон",
        ),
    ),
    "adult": PetTextStyle(
        max_words=70,
        max_chars=520,
        sentence_limit=5,
        style_rules=(
            "ответь спокойно и развернуто, но без монолога",
            "свяжи детали лора в понятную историю или воспоминание",
            "можно добавить мелкую фактуру, если она не меняет канон",
        ),
    ),
}


def style_for_age(age_stage: PetAgeStage, energy_band: EnergyBand = "medium") -> PetTextStyle:
    base = _STYLE_BY_AGE[age_stage]
    if energy_band != "low":
        return base

    return PetTextStyle(
        max_words=max(2, base.max_words - 1),
        max_chars=max(24, base.max_chars - 12),
        sentence_limit=1,
        style_rules=(*base.style_rules, "из-за усталости реплика особенно короткая"),
    )


def style_for_reply(
    age_stage: PetAgeStage,
    energy_band: EnergyBand = "medium",
    *,
    lore_question: bool = False,
) -> PetTextStyle:
    if lore_question:
        return _LORE_STYLE_BY_AGE[age_stage]
    return style_for_age(age_stage, energy_band)
