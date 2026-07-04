from __future__ import annotations

import re

from app.services.pet_reply_engine.intent import (
    is_appearance_question,
    is_home_question,
    is_location_question,
    is_lore_question,
    is_name_question,
    is_origin_question,
    is_preference_question,
    is_reason_question,
    is_relationship_question,
)
from app.services.pet_reply_engine.lore import (
    home_fragment,
    origin_fragment,
    preference_fragment,
    relationship_fragment,
    short_words,
)
from app.services.pet_reply_engine.models import EnergyBand, PetAgeStage, PetMood, PetReplyInput
from app.services.pet_reply_engine.state_interpreter import interpret_state, text_mentions_food

_CHAT_FALLBACKS: dict[PetAgeStage, dict[PetMood, dict[EnergyBand, str]]] = {
    "baby": {
        "idle": {
            "low": "мр... я тут)",
            "medium": "пи. я рядом)",
            "high": "пи! я тут)",
        },
        "happy": {
            "low": "мр... хорошо)",
            "medium": "пи! мне тепло)",
            "high": "пи-пи! еще)",
        },
        "hungry": {
            "low": "мр... крошку)",
            "medium": "пи... крошку)",
            "high": "пи! вкусненькое)",
        },
        "sad": {
            "low": "мр... рядом:(",
            "medium": "пи... рядом:(",
            "high": "пи... обними:(",
        },
    },
    "teen": {
        "idle": {
            "low": "я тут, только тихонько.",
            "medium": "я рядом. что делаем?",
            "high": "я на месте! что дальше?",
        },
        "happy": {
            "low": "мне хорошо. правда.",
            "medium": "о, вот это мне нравится.",
            "high": "о, вот это мне нравится!",
        },
        "hungry": {
            "low": "я бы сейчас тихо пожевал что-нибудь...",
            "medium": "если что, я не против перекуса.",
            "high": "перекус и приключение звучат идеально!",
        },
        "sad": {
            "low": "я немного притих. побудешь рядом?",
            "medium": "можно я просто побуду рядом?",
            "high": "я взбодрюсь, если ты не уйдешь.",
        },
    },
    "adult": {
        "idle": {
            "low": "я рядом. только чуть тише.",
            "medium": "я рядом. что делаем?",
            "high": "я рядом. готов продолжать.",
        },
        "happy": {
            "low": "мне спокойно хорошо рядом с тобой.",
            "medium": "мне хорошо. продолжим?",
            "high": "мне хорошо. давай продолжим!",
        },
        "hungry": {
            "low": "я бы не отказался от маленького перекуса.",
            "medium": "перекус был бы кстати, но я слушаю.",
            "high": "сначала перекус, потом подвиги?",
        },
        "sad": {
            "low": "можно я немного помолчу рядом?",
            "medium": "я немного загрустил. посидишь со мной?",
            "high": "мне тише, но с тобой уже лучше.",
        },
    },
}

_ACTION_FALLBACKS: dict[str, dict[PetAgeStage, str]] = {
    "feed": {
        "baby": "ням... спасибо)",
        "teen": "о, вот это вкусно.",
        "adult": "спасибо. стало заметно уютнее.",
    },
    "play": {
        "baby": "пи! еще)",
        "teen": "ха, давай еще раз!",
        "adult": "хорошая игра. продолжим?",
    },
    "clean": {
        "baby": "мр... чисто)",
        "teen": "так намного приятнее.",
        "adult": "спасибо. теперь спокойнее.",
    },
    "pet": {
        "baby": "мрр... тепло)",
        "teen": "эй... ладно, приятно.",
        "adult": "мне приятно. останься рядом.",
    },
    "idle_return": {
        "baby": "пи... ты тут)",
        "teen": "а, ты вернулся.",
        "adult": "я скучал. рад, что ты вернулся.",
    },
    "creation_intro": {
        "baby": "пи... привет)",
        "teen": "привет. я уже тут.",
        "adult": "я появился. познакомимся?",
    },
    "system_nudge": {
        "baby": "мр... я тут)",
        "teen": "я тут, если что.",
        "adult": "я рядом, когда будешь готов.",
    },
}

_CHAT_FALLBACK_VARIANTS: dict[tuple[PetAgeStage, PetMood, EnergyBand], tuple[str, ...]] = {
    ("baby", "idle", "high"): ("мр! слушаю)", "пи, что)"),
    ("baby", "happy", "medium"): ("мр! еще)", "пи, слушаю)"),
    ("baby", "happy", "high"): ("пи! слушаю)", "мр, еще)", "пи! дальше)"),
    ("teen", "happy", "high"): ("я слушаю, продолжай.", "мне нравится, рассказывай дальше."),
    ("adult", "happy", "high"): ("я слушаю. продолжай.", "хорошо, я с тобой."),
}

_FILLER_APPEARANCE_WORDS = {
    "маленький",
    "маленькая",
    "милый",
    "милая",
    "нечеловеческое",
    "существо",
    "питомец",
    "компаньон",
    "маскот",
    "mascot",
    "soft",
}
_SENTENCE_FRAGMENT_PATTERN = re.compile(r"[^.!?…]+[.!?…]?")
_SENTENCE_END_PATTERN = re.compile(r"[.!?…)]$")


def _clean_fragment(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(".", "").strip(" ,;:")).strip()


def _short_words(text: str, limit: int) -> str:
    words = _clean_fragment(text).split()
    return " ".join(words[:limit])


def _short_lore_fragment(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""

    selected: list[str] = []
    used_words = 0
    for match in _SENTENCE_FRAGMENT_PATTERN.finditer(cleaned):
        sentence = match.group(0).strip(" ,;:")
        if not sentence:
            continue
        word_count = len(sentence.split())
        if selected and used_words + word_count > limit:
            break
        if not selected and word_count > limit:
            selected.append(_short_words(sentence, limit))
            break
        selected.append(sentence)
        used_words += word_count

    short = " ".join(selected).strip()
    if not short:
        short = _short_words(cleaned, limit)
    if short and not _SENTENCE_END_PATTERN.search(short):
        short = f"{short}."
    return short


def _first_visual_feature(reply_input: PetReplyInput) -> str:
    visual = reply_input.pet.visual_identity
    candidates = (
        visual.signature_features
        or visual.chat_cues.body_words
        or visual.materials
        or (visual.raw_description,)
    )
    for candidate in candidates:
        cleaned = _clean_fragment(candidate)
        if not cleaned:
            continue
        words = [
            word
            for word in cleaned.split()
            if word.casefold() not in _FILLER_APPEARANCE_WORDS
        ]
        if words:
            return " ".join(words[:3])
    return "маленький"


def appearance_fallback(reply_input: PetReplyInput) -> str:
    visual = reply_input.pet.visual_identity
    if reply_input.pet.age_stage == "baby":
        feature = _short_words(_first_visual_feature(reply_input), 2)
        sound = _baby_sound(reply_input)
        return f"{sound}... {feature})" if feature else f"{sound}... маленький)"

    raw_description = _short_words(visual.raw_description, 12)
    if raw_description:
        return f"я выгляжу так: {raw_description}"

    feature = _short_words(_first_visual_feature(reply_input), 8)
    return f"я похож на {feature}" if feature else "я маленький цифровой питомец"


def location_fallback(reply_input: PetReplyInput) -> str:
    if is_home_question(reply_input.user_text):
        fragment = home_fragment(reply_input.pet.lore)
        if fragment:
            return _format_lore_fallback(reply_input, "мой дом", fragment)
    if reply_input.pet.age_stage == "baby":
        sound = _baby_sound(reply_input)
        return f"{sound}... я тут)"
    return "я здесь, на экране"


def _format_lore_fallback(reply_input: PetReplyInput, prefix: str, fragment: str) -> str:
    if reply_input.pet.age_stage == "baby":
        sound = _baby_sound(reply_input)
        short = short_words(fragment, 4)
        return f"{sound}... {short})" if short else f"{sound}... там)"

    short = _short_lore_fragment(fragment, 32 if reply_input.pet.age_stage == "teen" else 42)
    if not short:
        return "я пока не знаю, как это сказать."
    if prefix:
        return f"{prefix}: {short}"
    return short


def lore_fallback(reply_input: PetReplyInput) -> str | None:
    if not reply_input.pet.lore or not is_lore_question(reply_input.user_text):
        return None

    text = reply_input.user_text
    if is_home_question(text) or is_location_question(text):
        fragment = home_fragment(reply_input.pet.lore)
        return _format_lore_fallback(reply_input, "мой дом", fragment) if fragment else None

    if is_relationship_question(text):
        fragment = relationship_fragment(reply_input.pet.lore)
        return _format_lore_fallback(reply_input, "со мной", fragment) if fragment else None

    if is_preference_question(text):
        fragment = preference_fragment(reply_input.pet.lore)
        return _format_lore_fallback(reply_input, "", fragment) if fragment else None

    if is_origin_question(text):
        fragment = origin_fragment(reply_input.pet.lore)
        return _format_lore_fallback(reply_input, "я оттуда", fragment) if fragment else None

    return None


def _baby_sound(reply_input: PetReplyInput) -> str:
    sounds = reply_input.pet.visual_identity.chat_cues.sound_words
    if sounds:
        return sounds[0]
    if reply_input.pet.mood == "hungry":
        return "ням"
    if reply_input.pet.mood == "sad":
        return "мр"
    return "пи"


def _baby_body_word(reply_input: PetReplyInput) -> str:
    body_words = reply_input.pet.visual_identity.chat_cues.body_words
    return body_words[0] if body_words else "лапки"


def _select_non_recent_reply(
    options: tuple[str, ...],
    recent_pet_replies: tuple[str, ...] = (),
) -> str:
    recent = {_repeat_key(reply) for reply in recent_pet_replies[-4:]}
    for option in options:
        if _repeat_key(option) not in recent:
            return option
    return options[len(recent_pet_replies) % len(options)]


def _repeat_key(reply: str) -> str:
    return re.sub(r"(?:\s|[).:(!?…])+$", "", reply.strip().casefold())


def baby_fallback_reply(
    reply_input: PetReplyInput,
    recent_pet_replies: tuple[str, ...] = (),
) -> str:
    sound = _baby_sound(reply_input)
    body_word = _baby_body_word(reply_input)
    mood = reply_input.pet.mood
    energy = interpret_state(reply_input).energy_band

    if mood == "happy":
        options = (
            (f"{sound}-{sound}! {body_word})", f"{sound}! еще)", f"{sound}! хорошо)")
            if energy == "high"
            else (f"{sound}! хорошо)", f"{sound}! {body_word})")
        )
        return _select_non_recent_reply(options, recent_pet_replies)
    if mood == "hungry":
        options = (
            (f"{sound}... ням)", "ням... крошку)")
            if sound != "ням"
            else ("ням... крошку)", "ням... еще)")
        )
        return _select_non_recent_reply(options, recent_pet_replies)
    if mood == "sad":
        return _select_non_recent_reply(
            (f"{sound}... рядом:(", f"{sound}... тихо:("),
            recent_pet_replies,
        )
    return _select_non_recent_reply(
        (f"{sound}. я тут)", f"{sound}. тут)"),
        recent_pet_replies,
    )


def baby_name_fallback(
    reply_input: PetReplyInput,
    recent_pet_replies: tuple[str, ...] = (),
) -> str:
    sound = _baby_sound(reply_input)
    if reply_input.pet.name:
        return _select_non_recent_reply(
            (f"{sound}, я {reply_input.pet.name})", f"{sound}! {reply_input.pet.name})"),
            recent_pet_replies,
        )
    return _select_non_recent_reply(
        (f"{sound}... назови меня)", f"{sound}... имя хочу)"),
        recent_pet_replies,
    )


def baby_reason_fallback(
    reply_input: PetReplyInput,
    recent_pet_replies: tuple[str, ...] = (),
) -> str:
    sound = _baby_sound(reply_input)
    return _select_non_recent_reply(
        (f"{sound}... так вышло)", f"{sound}... я маленький)"),
        recent_pet_replies,
    )


def select_fallback_reply(
    age_stage: PetAgeStage,
    mood: PetMood,
    energy_band: EnergyBand,
    action: str = "chat_message",
    recent_pet_replies: tuple[str, ...] = (),
) -> str:
    if action != "chat_message" and action in _ACTION_FALLBACKS:
        return _ACTION_FALLBACKS[action][age_stage]

    base_reply = _CHAT_FALLBACKS[age_stage][mood][energy_band]
    options = (
        base_reply,
        *_CHAT_FALLBACK_VARIANTS.get((age_stage, mood, energy_band), ()),
    )
    return _select_non_recent_reply(options, recent_pet_replies)


def fallback_reply(reply_input: PetReplyInput) -> str:
    cues = interpret_state(reply_input)
    recent_pet_replies = tuple(
        item.text.strip() for item in reply_input.recent_messages if item.role == "pet"
    )

    if is_appearance_question(reply_input.user_text):
        return appearance_fallback(reply_input)
    lore_reply = lore_fallback(reply_input)
    if lore_reply:
        return lore_reply
    if is_location_question(reply_input.user_text):
        return location_fallback(reply_input)
    if reply_input.pet.age_stage == "baby" and is_name_question(reply_input.user_text):
        return baby_name_fallback(reply_input, recent_pet_replies)
    if reply_input.pet.age_stage == "baby" and is_reason_question(reply_input.user_text):
        return baby_reason_fallback(reply_input, recent_pet_replies)

    if reply_input.user_action != "chat_message":
        return select_fallback_reply(
            reply_input.pet.age_stage,
            reply_input.pet.mood,
            cues.energy_band,
            reply_input.user_action,
            recent_pet_replies=recent_pet_replies,
        )

    if (
        reply_input.user_action == "chat_message"
        and reply_input.pet.mood == "hungry"
        and cues.hunger_band == "low"
        and cues.recent_food_mention
        and not text_mentions_food(reply_input.user_text)
    ):
        return _CHAT_FALLBACKS[reply_input.pet.age_stage]["idle"][cues.energy_band]

    if reply_input.pet.age_stage == "baby":
        return baby_fallback_reply(reply_input, recent_pet_replies)

    return select_fallback_reply(
        reply_input.pet.age_stage,
        reply_input.pet.mood,
        cues.energy_band,
        reply_input.user_action,
        recent_pet_replies=recent_pet_replies,
    )
