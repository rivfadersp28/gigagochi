from __future__ import annotations

import random
from typing import Any

from app.services.background_story_service import (
    STORY_INCIDENT_INSTRUCTIONS,
    STORY_VALENCE_INSTRUCTIONS,
    select_background_story_direction,
)

FULL_STORY_ANTAGONIST_CLASSES = {
    "predatory_beast": "крупный опасный зверь с естественными повадками",
    "fantasy_monster": (
        "фантастический монстр с ясной целью и одной устойчивой способностью; "
        "не заменяй его обычным животным"
    ),
    "intelligent_enemy": "разумный одиночный враг или организованная группа",
    "supernatural_entity": "дух, призрак или иная сверхъестественная сущность",
    "ancient_construct": "древний страж, ожившая статуя или искусственное существо",
    "swarm_collective": "рой или коллектив существ, действующий как единая сила",
}

FULL_STORY_WONDER_CLASSES = {
    "enchanted_ecosystem": "живое волшебное место со своими обитателями и укладом",
    "impossible_architecture": "пространственно невозможное здание или территория",
    "spectral_domain": "место соприкосновения видимого мира и мира духов",
    "ancient_machine": "огромное древнее устройство с наблюдаемым назначением",
    "transformed_landscape": "природное место, устойчиво изменённое необычным явлением",
}

FULL_STORY_LOCATION_CLASSES = {
    "wild_nature": "дикая природная местность вдали от построек",
    "inhabited_settlement": "обитаемое поселение с несколькими действующими местами",
    "road_crossing": "дорога, переправа или постоялый двор",
    "ruined_city": "большой заброшенный город с улицами, площадями и зданиями",
    "magic_tower": "магическая башня, крепость или вертикальный комплекс",
    "grand_dungeon": "большое многоуровневое подземелье",
    "underground_city": "обитаемый или покинутый подземный город",
    "enchanted_grove": "волшебная роща или живая магическая экосистема",
    "spectral_realm": "призрачная территория на границе двух миров",
    "impossible_structure": "пространственно невозможное здание или территория",
    "water_region": "остров, побережье, болото или большое водное пространство",
    "transformed_landscape": "ландшафт, устойчиво изменённый необычным явлением",
}

FULL_STORY_LOCATIONS_BY_MODE = {
    "encounter": (
        "wild_nature",
        "inhabited_settlement",
        "ruined_city",
        "underground_city",
        "enchanted_grove",
        "water_region",
    ),
    "exploration": tuple(FULL_STORY_LOCATION_CLASSES),
    "mystery": (
        "inhabited_settlement",
        "ruined_city",
        "magic_tower",
        "grand_dungeon",
        "underground_city",
        "spectral_realm",
        "impossible_structure",
    ),
    "social_event": (
        "inhabited_settlement",
        "road_crossing",
        "ruined_city",
        "magic_tower",
        "underground_city",
        "water_region",
    ),
    "pursuit_or_conflict": (
        "wild_nature",
        "inhabited_settlement",
        "ruined_city",
        "magic_tower",
        "grand_dungeon",
        "underground_city",
        "spectral_realm",
        "impossible_structure",
    ),
    "rescue_or_help": tuple(FULL_STORY_LOCATION_CLASSES),
    "discovery": tuple(FULL_STORY_LOCATION_CLASSES),
    "environmental_event": (
        "wild_nature",
        "ruined_city",
        "magic_tower",
        "underground_city",
        "water_region",
        "transformed_landscape",
    ),
    "peaceful_change": (
        "inhabited_settlement",
        "road_crossing",
        "underground_city",
        "enchanted_grove",
        "water_region",
        "transformed_landscape",
    ),
}

FULL_STORY_WONDER_BY_LOCATION = {
    "enchanted_grove": "enchanted_ecosystem",
    "impossible_structure": "impossible_architecture",
    "spectral_realm": "spectral_domain",
    "grand_dungeon": "ancient_machine",
    "transformed_landscape": "transformed_landscape",
}

_PLOT_INSTRUCTIONS = {
    "encounter": "встреча с участником, чья практическая цель расходится с целью героя",
    "exploration": "знакомство с необычным местом, его обитателями и устойчивым укладом",
    "mystery": "наблюдаемая загадка, связанные улики и конкретное раскрытие причины",
    "social_event": "просьба, разногласие или общее дело, меняющее отношения",
    "pursuit_or_conflict": "активное противостояние с участником, у которого есть ясная цель",
    "rescue_or_help": "чужая понятная беда, осложнение помощи и видимый результат",
    "discovery": "необычное явление, постепенное понимание и новая возможность",
    "environmental_event": "крупное изменение среды, трудный выбор и новый порядок",
    "peaceful_change": "тёплое или красивое событие, оставляющее заметное изменение",
}

_ARC_INSTRUCTIONS = {
    "encounter": "встреча и цели; развитие контакта; выбор; новый статус отношений",
    "exploration": "вход и главное чудо; углубление; выбор взаимодействия; новое знание или связь",
    "mystery": "загадка; две связанные улики; конкретный ответ; последствие раскрытия",
    "social_event": "просьба или спор; разные интересы; решение; изменение отношений",
    "rescue_or_help": "беда; осложнение помощи; работающий способ; изменившееся положение",
    "discovery": "столкновение; изучение; понимание свойства; новая возможность",
    "environmental_event": "начало перемены; рост последствий; выбор; новый порядок",
    "peaceful_change": "возможность; участие; общий момент; радость, связь или традиция",
}

_CONFLICT_ARCS = {
    "theft": "кража; активный поиск или погоня; столкновение с виновником; итог потери",
    "battle": "первое столкновение; усиление боя; перелом с ценой; решающая схватка",
    "chase": "начало преследования; усиление давления; смена баланса; полный исход",
    "rivalry": "вызов; обмен преимуществами; решающий выбор; новый статус соперников",
}


def _least_used(values: dict[str, str], *, field: str, history: list[dict[str, Any]] | None) -> str:
    counts = {value: 0 for value in values}
    for item in history or []:
        if isinstance(item, dict) and item.get(field) in counts:
            counts[item[field]] += 1
    minimum = min(counts.values())
    return random.SystemRandom().choice(
        [value for value, count in counts.items() if count == minimum]
    )


def select_full_story_direction(
    history: list[dict[str, Any]] | None,
    *,
    current_stats: dict[str, int] | None = None,
) -> dict[str, str]:
    return enrich_full_story_direction(
        select_background_story_direction(history, current_stats=current_stats),
        history=history,
    )


def enrich_full_story_direction(
    direction: dict[str, str], *, history: list[dict[str, Any]] | None
) -> dict[str, str]:
    result = dict(direction)
    plot_mode = result.get("plotMode", "")
    candidates = FULL_STORY_LOCATIONS_BY_MODE.get(plot_mode, tuple(FULL_STORY_LOCATION_CLASSES))
    result["locationClass"] = _least_used(
        {value: FULL_STORY_LOCATION_CLASSES[value] for value in candidates},
        field="locationClass",
        history=history,
    )
    if plot_mode == "pursuit_or_conflict":
        result["arcVariant"] = {
            "theft": "theft",
            "pursuit": "chase",
        }.get(result.get("causalOrigin", ""), "battle")
        result["antagonistClass"] = _least_used(
            FULL_STORY_ANTAGONIST_CLASSES, field="antagonistClass", history=history
        )
    elif plot_mode == "encounter" and result.get("resolutionMode") == "contest":
        result["arcVariant"] = "rivalry"
        result["antagonistClass"] = _least_used(
            FULL_STORY_ANTAGONIST_CLASSES, field="antagonistClass", history=history
        )
    if plot_mode in {"exploration", "discovery"}:
        result["wonderClass"] = FULL_STORY_WONDER_BY_LOCATION.get(
            result["locationClass"]
        ) or _least_used(FULL_STORY_WONDER_CLASSES, field="wonderClass", history=history)
    return result


def full_story_direction_block(direction: dict[str, str]) -> str:
    plot_mode = direction["plotMode"]
    valence = direction["valenceTarget"]
    arc_variant = direction.get("arcVariant", "")
    arc = _CONFLICT_ARCS.get(arc_variant) or _ARC_INSTRUCTIONS[plot_mode]
    extra: list[str] = []
    if antagonist := FULL_STORY_ANTAGONIST_CLASSES.get(direction.get("antagonistClass", "")):
        extra.append(f"- Класс противника: {antagonist}.")
    if wonder := FULL_STORY_WONDER_CLASSES.get(direction.get("wonderClass", "")):
        extra.append(f"- Тип необычного места: {wonder}.")
    return "\n".join(
        [
            "STORY_DIRECTION: широкая рамка, а не список деталей для обязательного упоминания.",
            f"- Тип развития: {_PLOT_INSTRUCTIONS[plot_mode]}.",
            f"- Динамика четырёх частей: {arc}.",
            *extra,
            f"- Исходное происшествие: {STORY_INCIDENT_INSTRUCTIONS[direction['incidentClass']]}.",
            f"- Среда: {FULL_STORY_LOCATION_CLASSES[direction['locationClass']]}.",
            "- Эта локация остаётся главным пространством всех четырёх частей.",
            f"- Общий итог: {STORY_VALENCE_INSTRUCTIONS[valence]}.",
            "Одна ясная причинная линия важнее механического перечисления рамки.",
        ]
    )
