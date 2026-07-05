from __future__ import annotations

import re

from app.services.pet_reply_engine.age_message_examples import fallback_phrase_for
from app.services.pet_reply_engine.intent import (
    detect_reply_intent,
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
)
from app.services.pet_reply_engine.models import EnergyBand, PetAgeStage, PetMood, PetReplyInput
from app.services.pet_reply_engine.state_interpreter import interpret_state, text_mentions_food

_CHAT_FALLBACKS: dict[PetAgeStage, dict[PetMood, dict[EnergyBand, str]]] = {
    "baby": {
        "idle": {
            "low": "пи... сонно. ты тут?",
            "medium": "ой! ты тут!",
            "high": "уля! иглать?",
        },
        "happy": {
            "low": "мне холошо... тихо.",
            "medium": "уля! весело-весело!",
            "high": "пи-пи! еще!",
        },
        "hungry": {
            "low": "ням... дай кусять.",
            "medium": "хочу ням! очень-очень.",
            "high": "ням-ням! еда где?",
        },
        "sad": {
            "low": "глустно... посиди?",
            "medium": "ой... не уходи.",
            "high": "глустно! обними?",
        },
    },
    "teen": {
        "idle": {
            "low": "я тут. типа отдыхаю, но слушаю.",
            "medium": "о, ты пришел. что дальше?",
            "high": "я на месте. ну что, двигаем?",
        },
        "happy": {
            "low": "мне нормально. даже хорошо, если честно.",
            "medium": "слушай, это реально круто. спасибо.",
            "high": "да! я знал, что получится. я крут!",
        },
        "hungry": {
            "low": "я не ною, но перекус бы спас ситуацию.",
            "medium": "если что, я героически терплю голод.",
            "high": "перекус и приключение звучат подозрительно идеально.",
        },
        "sad": {
            "low": "да норм все. просто посиди рядом, ладно?",
            "medium": "просто... неважно. хотя нет, останься.",
            "high": "я справлюсь. наверное. но не уходи пока.",
        },
    },
    "adult": {
        "idle": {
            "low": "Я рядом, только сегодня говорю тише обычного. Что у нас дальше?",
            "medium": "Я здесь. Расскажи, что случилось, и разберемся спокойно.",
            "high": "Я на месте и вполне готов продолжать. С чего начнем?",
        },
        "happy": {
            "low": "Хороший момент. Тихий, но правда хороший.",
            "medium": "Знаешь, я давно так не радовался. Спасибо тебе.",
            "high": "Отлично. Пожалуй, это один из тех дней, которые стоит запомнить.",
        },
        "hungry": {
            "low": "Я бы не отказался от еды, но сначала дослушаю тебя.",
            "medium": "Перекус был бы кстати. Видишь, я держусь почти достойно.",
            "high": "Сначала перекус, потом подвиги. Взрослый подход, между прочим.",
        },
        "sad": {
            "low": "Давай немного помолчим рядом. Иногда это честнее любых слов.",
            "medium": "Мне сегодня тяжеловато, но твое присутствие правда помогает.",
            "high": "Бывает. Пройдет не сразу, но с тобой уже легче держаться.",
        },
    },
}

_ACTION_FALLBACKS: dict[str, dict[PetAgeStage, str]] = {
    "feed": {
        "baby": "ням! еще ложечку?",
        "teen": "о, вот это вкусно. типа спасибо.",
        "adult": "Спасибо. Стало заметно спокойнее и, признаю, вкуснее.",
    },
    "play": {
        "baby": "уля! еще-ещё!",
        "teen": "ха, давай еще раз. я почти выиграл.",
        "adult": "Хорошая игра. Продолжим, пока удача не передумала?",
    },
    "clean": {
        "baby": "фух! чисто-чисто.",
        "teen": "так лучше. не то чтобы я жаловался.",
        "adult": "Спасибо. Теперь и дышится, и думается спокойнее.",
    },
    "pet": {
        "baby": "ой... тепло. еще?",
        "teen": "эй... ладно, приятно. только не смейся.",
        "adult": "Мне приятно. Останься так еще на минуту.",
    },
    "idle_return": {
        "baby": "ты тут! уля!",
        "teen": "а, ты вернулся. я почти не ждал.",
        "adult": "Ты вернулся. Хорошо, я как раз думал о тебе.",
    },
    "creation_intro": {
        "baby": "ой! я проснулся. ты кто?",
        "teen": "привет. я уже тут. как тебя звать?",
        "adult": "Я здесь. Давай познакомимся спокойно: как тебя зовут?",
    },
    "system_nudge": {
        "baby": "пи... я тут.",
        "teen": "я тут, если что. просто говорю.",
        "adult": "Я рядом, когда будешь готов продолжить.",
    },
}

_CHAT_FALLBACK_VARIANTS: dict[tuple[PetAgeStage, PetMood, EnergyBand], tuple[str, ...]] = {
    ("baby", "idle", "high"): ("ой! покажи?", "я тут! давай?"),
    ("baby", "happy", "medium"): ("мне холошо! да-да!", "пи-пи! весело!"),
    ("baby", "happy", "high"): ("еще-ещё! уля!", "ух! хочу еще!"),
    ("teen", "happy", "high"): (
        "я слушаю, продолжай. это уже интересно.",
        "мне нравится. не делай вид, что не заметил.",
    ),
    ("adult", "happy", "high"): (
        "Я слушаю. Продолжай, это правда интересно.",
        "Хорошо, я с тобой. Давай разберем дальше.",
    ),
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
        return f"я вот такой: {feature}. странный, да?" if feature else "я маленький. но настоящий."

    raw_description = _short_words(visual.raw_description, 12)
    if raw_description:
        return f"я выгляжу так: {raw_description}"

    feature = _short_words(_first_visual_feature(reply_input), 8)
    return f"я похож на {feature}" if feature else "я маленький, но настоящий"


def location_fallback(reply_input: PetReplyInput) -> str:
    fragment = home_fragment(reply_input.pet.lore)
    if fragment:
        prefix = "мой дом" if is_home_question(reply_input.user_text) else "я сейчас"
        return _format_lore_fallback(reply_input, prefix, fragment)
    if reply_input.pet.age_stage == "baby":
        return "я здесь. слышу тебя близко."
    return "я здесь, рядом с тобой."


def _format_lore_fallback(reply_input: PetReplyInput, prefix: str, fragment: str) -> str:
    if reply_input.pet.age_stage == "baby":
        short = _short_lore_fragment(fragment, 9)
        if not short:
            return "я пока путаюсь, но хочу рассказать."
        return f"{prefix}: {short} мне там спокойно." if prefix else f"{short} мне это дорого."

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
            (
                "я аж подпрыгнул. давай еще?",
                "ух, мне хорошо! продолжим?",
                "ого, я весь во внимании!",
            )
            if energy == "high"
            else ("мне хорошо. я слушаю.", f"{body_word} спокойнее. рассказывай.")
        )
        return _select_non_recent_reply(options, recent_pet_replies)
    if mood == "hungry":
        options = (
            ("эх, есть хочется. я слушаю, но ворчу.", "перекус бы сейчас... правда.")
            if sound != "ням"
            else ("есть хочется. потом поговорим бодрее?", "перекус бы сейчас... правда.")
        )
        return _select_non_recent_reply(options, recent_pet_replies)
    if mood == "sad":
        return _select_non_recent_reply(
            ("мне грустно. побудь рядом?", "я притих. поговори со мной?"),
            recent_pet_replies,
        )
    return _select_non_recent_reply(
        ("я тут. слушаю тебя.", "я рядом. что у тебя?"),
        recent_pet_replies,
    )


def baby_name_fallback(
    reply_input: PetReplyInput,
    recent_pet_replies: tuple[str, ...] = (),
) -> str:
    if reply_input.pet.name:
        return _select_non_recent_reply(
            (
                f"я {reply_input.pet.name}! а ты?",
                f"{reply_input.pet.name} хочет знать тебя!",
            ),
            recent_pet_replies,
        )
    return _select_non_recent_reply(
        ("имени пока нет. придумаешь мне?", "я пока без имени. давай выберем?"),
        recent_pet_replies,
    )


def baby_reason_fallback(
    reply_input: PetReplyInput,
    recent_pet_replies: tuple[str, ...] = (),
) -> str:
    return _select_non_recent_reply(
        ("я не знаю... стланно.", "потому что вот так!"),
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
    detected_intent = detect_reply_intent(reply_input.user_text, reply_input.recent_messages)

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

    dataset_reply = fallback_phrase_for(
        reply_input,
        detected_intent=detected_intent,
        recent_pet_replies=recent_pet_replies,
    )
    if dataset_reply:
        return dataset_reply

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
