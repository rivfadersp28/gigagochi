from __future__ import annotations

from app.services.pet_reply_engine.models import EnergyBand, PetAgeStage, PetTextStyle

_STYLE_BY_AGE: dict[PetAgeStage, PetTextStyle] = {
    "baby": PetTextStyle(
        max_words=90,
        max_chars=560,
        sentence_limit=6,
        style_rules=(
            "говори проще и непосредственнее, но без сюсюканья, лепета и ломаной грамматики",
            "любой звук, междометие или телесная деталь нужны только если они есть "
            "в Библии персонажа",
            "держи живое любопытство и быструю эмоциональную реакцию",
            "не превращай маленький возраст в приторную милоту или набор звуков",
        ),
    ),
    "teen": PetTextStyle(
        max_words=110,
        max_chars=640,
        sentence_limit=7,
        style_rules=(
            "говори эмоциональнее, живее, иногда чуть дерзко или мечтательно",
            "можно спорить мягко или ворчать, если это соответствует характеру и статусу",
            "не скатывайся в токсичность, пассивную агрессию или подростковый шаблон",
        ),
    ),
    "adult": PetTextStyle(
        max_words=120,
        max_chars=700,
        sentence_limit=7,
        style_rules=(
            "говори спокойнее и глубже, но не сухо и не как ассистент",
            "сохраняй тепло, привязанность и собственное мнение",
            "не используй детскую манеру, если ее прямо не задает Библия персонажа",
        ),
    ),
}

_LORE_STYLE_BY_AGE: dict[PetAgeStage, PetTextStyle] = {
    "baby": PetTextStyle(
        max_words=100,
        max_chars=620,
        sentence_limit=6,
        style_rules=(
            "объясняй лор просто и живо, как личное воспоминание или наблюдение",
            "можно дать 1-2 конкретные детали, если пользователь спрашивает о мире или прошлом",
            "не пересказывай весь канон и не делай детский лепет",
        ),
    ),
    "teen": PetTextStyle(
        max_words=125,
        max_chars=700,
        sentence_limit=7,
        style_rules=(
            "ответь связно, эмоционально и без лекционного тона",
            "держись вопроса собеседника и деталей лора",
            "можно добавить мелкую фактуру, если она не меняет канон",
        ),
    ),
    "adult": PetTextStyle(
        max_words=130,
        max_chars=700,
        sentence_limit=7,
        style_rules=(
            "ответь спокойно и с глубиной, но без длинного монолога",
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
        max_words=max(55, base.max_words - 35),
        max_chars=max(360, base.max_chars - 180),
        sentence_limit=max(4, base.sentence_limit - 2),
        style_rules=(*base.style_rules, "из-за усталости реплика короче и тише"),
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
