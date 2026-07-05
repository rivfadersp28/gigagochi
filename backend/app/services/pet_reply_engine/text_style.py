from __future__ import annotations

from app.services.pet_reply_engine.models import EnergyBand, PetAgeStage, PetTextStyle
from app.services.pet_reply_engine.reply_limits import MAX_REPLY_CHARS

_STYLE_BY_AGE: dict[PetAgeStage, PetTextStyle] = {
    "baby": PetTextStyle(
        max_words=12,
        max_chars=120,
        sentence_limit=3,
        style_rules=(
            "форма baby берется из message examples: 2-5 слов, редко до 7",
            "звукоподражания, междометия, повторы и речевые ошибки допустимы и желательны",
            "эмоция должна быть очень прямой: хочу, боюсь, рад, грустно, дай, еще",
            "факты, имя, страхи, еду и тело бери из Character Bible, но не повторяй "
            "главную способность без нужды",
        ),
    ),
    "teen": PetTextStyle(
        max_words=18,
        max_chars=220,
        sentence_limit=4,
        style_rules=(
            "форма teen берется из message examples: 5-12 слов, живой разговорный ритм",
            "можно сленг, браваду, позерство, ворчание и скрытую нежность",
            "показывай эмоцию через защитную фразу, шутку, колкость или неловкое признание",
            "не звучать как взрослый ассистент и не терять факты Character Bible, но "
            "не повторять одну способность как речевой тик",
        ),
    ),
    "adult": PetTextStyle(
        max_words=32,
        max_chars=MAX_REPLY_CHARS,
        sentence_limit=4,
        style_rules=(
            "форма adult берется из message examples: 10-25 слов, спокойнее и связнее",
            "уместны мягкий юмор, короткая рефлексия и уверенное мнение",
            "сохраняй тепло и характер, но не используй baby/teen-манеру без причины",
        ),
    ),
}

_LORE_STYLE_BY_AGE: dict[PetAgeStage, PetTextStyle] = {
    "baby": PetTextStyle(
        max_words=18,
        max_chars=180,
        sentence_limit=4,
        style_rules=(
            "для lore-вопроса baby может сказать чуть больше, но все равно очень просто",
            "дай одну личную деталь из Character Bible и короткую эмоцию",
            "не делай взрослое объяснение; звуки, повторы и ошибки все еще допустимы",
        ),
    ),
    "teen": PetTextStyle(
        max_words=35,
        max_chars=MAX_REPLY_CHARS,
        sentence_limit=5,
        style_rules=(
            "для lore-вопроса teen может раскрыться чуть длиннее, но с подростковой защитой",
            "держись вопроса и деталей Character Bible",
            "можно добавить браваду, неловкую нежность или короткую колкость",
        ),
    ),
    "adult": PetTextStyle(
        max_words=60,
        max_chars=MAX_REPLY_CHARS,
        sentence_limit=6,
        style_rules=(
            "для lore-вопроса adult может дать спокойную мини-историю",
            "свяжи 1-3 детали Character Bible в понятный ответ",
            "можно добавить мягкий юмор или рефлексию, но без лекционного тона",
        ),
    ),
}


def style_for_age(age_stage: PetAgeStage, energy_band: EnergyBand = "medium") -> PetTextStyle:
    base = _STYLE_BY_AGE[age_stage]
    if energy_band != "low":
        return base

    return PetTextStyle(
        max_words=max(7, base.max_words - 6),
        max_chars=max(80, base.max_chars - 60),
        sentence_limit=max(2, base.sentence_limit - 1),
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
