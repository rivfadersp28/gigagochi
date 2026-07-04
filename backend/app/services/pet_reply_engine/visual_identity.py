from __future__ import annotations

import re
from typing import Any

from app.prompts.pet_image_prompts import rewrite_known_character_references
from app.services.pet_reply_engine.models import PetChatCues, PetVisualIdentity

_TECHNICAL_AVOID_WORDS = (
    "3D",
    "sprite sheet",
    "mascot",
    "render",
    "matte material",
    "silhouette",
    "prompt",
    "рендер",
    "маскот",
    "спрайт",
    "силуэт",
    "матовый",
)

_BODY_CUE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("tail", "хвост"), "хвостик"),
    (("wing", "крыл"), "крылышки"),
    (("horn", "рог", "рожк"), "рожки"),
    (("ear", "уш"), "ушки"),
    (("paw", "лап"), "лапки"),
    (("leaf", "лист"), "листик"),
    (("scarf", "шарф"), "шарфик"),
    (("crystal", "кристалл"), "кристаллик"),
    (("drop", "капл"), "капелька"),
    (("shell", "панцир", "ракуш"), "панцирь"),
)

_SOUND_CUE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("electric", "spark", "yellow", "электр", "искра", "желт"), "пику"),
    (("cat", "kitten", "кот", "кош", "котен", "котён"), "мяу"),
    (("dog", "puppy", "пес", "собак", "щен"), "тяв"),
    (("dragon", "дракон", "horn", "рог"), "фыр"),
    (("alien", "space", "cosmic", "иноплан", "косм"), "бип"),
    (("leaf", "лист", "grass", "трава"), "шур"),
    (("water", "drop", "bubble", "вода", "капл", "пузыр"), "буль"),
    (("crystal", "кристалл", "glass", "стекл"), "дзинь"),
    (("bird", "птиц", "wing", "крыл"), "пи"),
    (("cloud", "wind", "облак", "ветер"), "фу"),
    (("purr", "fluffy", "soft", "мур", "пуш", "мягк"), "мр"),
)

_METAPHOR_CUE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("spark", "electric", "искра", "электр"), "искорка"),
    (("leaf", "лист"), "листочек"),
    (("drop", "water", "капл", "вода"), "капелька"),
    (("cloud", "облак"), "облачко"),
    (("moon", "лун", "silver", "сереб"), "лунный блик"),
    (("crystal", "кристалл"), "звонкий блик"),
)

_SHAPE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("sphere", "round", "шар", "круг"), "круглая форма"),
    (("drop", "капл"), "каплевидная форма"),
    (("bean", "боб"), "бобовая форма"),
    (("star", "звезд"), "звездная форма"),
    (("cloud", "облак"), "облачная форма"),
    (("crystal", "кристалл"), "кристальная форма"),
)

_ACCESSORY_KEYWORDS = ("scarf", "шарф", "headphones", "наушник", "hat", "шляп", "collar", "ворот")
_WHOLE_WORD_KEYWORDS = frozenset(("cat", "кот"))


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return tuple(result)


def _first_keyword_match(text: str, options: tuple[tuple[tuple[str, ...], str], ...]) -> str | None:
    lowered = text.casefold()
    for needles, cue in options:
        if any(_has_keyword(lowered, needle) for needle in needles):
            return cue
    return None


def _has_keyword(lowered_text: str, needle: str) -> bool:
    if needle in _WHOLE_WORD_KEYWORDS:
        return bool(re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", lowered_text))
    return needle in lowered_text


def _collect_keyword_cues(
    text: str,
    options: tuple[tuple[tuple[str, ...], str], ...],
    limit: int = 4,
) -> tuple[str, ...]:
    lowered = text.casefold()
    cues: list[str] = []
    for needles, cue in options:
        if any(_has_keyword(lowered, needle) for needle in needles) and cue not in cues:
            cues.append(cue)
        if len(cues) >= limit:
            break
    return tuple(cues)


def _extract_accessories(text: str) -> tuple[str, ...]:
    lowered = text.casefold()
    return tuple(keyword for keyword in _ACCESSORY_KEYWORDS if keyword in lowered)


def build_chat_cues(character_text: str) -> PetChatCues:
    return PetChatCues(
        body_words=_collect_keyword_cues(character_text, _BODY_CUE_KEYWORDS),
        sound_words=_collect_keyword_cues(character_text, _SOUND_CUE_KEYWORDS, limit=3),
        metaphor_words=_collect_keyword_cues(character_text, _METAPHOR_CUE_KEYWORDS),
        avoid_in_speech=_TECHNICAL_AVOID_WORDS,
    )


def build_visual_identity(
    raw_description: str,
    character_bible: dict[str, Any] | None = None,
) -> PetVisualIdentity:
    safe_description = rewrite_known_character_references(raw_description.strip())
    bible = character_bible or {}
    species = _string(bible.get("species")) or safe_description or raw_description.strip()
    main_colors = _string_tuple(bible.get("main_colors"))
    signature_features = _string_tuple(bible.get("signature_features"))
    materials = _string_tuple(bible.get("materials"))
    proportions = _string(bible.get("proportions"))
    do_not_change = _string_tuple(bible.get("do_not_change"))
    character_text = " ".join(
        item
        for item in (
            safe_description,
            species,
            _string(bible.get("personality")) or "",
            " ".join(signature_features),
            " ".join(materials),
            proportions or "",
            " ".join(do_not_change),
        )
        if item
    )

    return PetVisualIdentity(
        raw_description=raw_description.strip(),
        safe_description=safe_description,
        species=species,
        visual_concept=species,
        dominant_body_shape=_first_keyword_match(character_text, _SHAPE_KEYWORDS),
        silhouette=proportions,
        main_colors=main_colors,
        accent_color=main_colors[-1] if len(main_colors) > 2 else None,
        signature_features=signature_features,
        materials=materials,
        proportions=proportions,
        accessories=_extract_accessories(character_text),
        baby_design=_string(bible.get("baby_design")),
        teen_design=_string(bible.get("teen_design")),
        adult_design=_string(bible.get("adult_design")),
        do_not_change=do_not_change,
        chat_cues=build_chat_cues(character_text),
    )
