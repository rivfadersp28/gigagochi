from __future__ import annotations

from app.services.pet_reply_engine.models import EnergyBand, HungerBand, PetReplyInput, PetStateCues

_AGE_CUES = {
    "baby": "малышовый голос: звук плюс одно ласковое слово, очень просто",
    "teen": "живее и характернее, одна-две короткие фразы, без резкости",
    "adult": "спокойнее, увереннее и теплее, без детского лепета",
}

_MOOD_CUES = {
    "idle": "спокойный простой тон, без сильного возбуждения",
    "happy": "радостнее и прямо, можно одно легкое восклицание",
    "sad": "тише и прямо: можно сказать, что грустно или хочется рядом",
    "hungry": "прямо дать понять, что хочется еды, но не повторять это каждый раз",
}

_ACTION_CUES = {
    "chat_message": "ответь на текущее сообщение собеседника",
    "feed": "отреагируй на кормление коротко и благодарно",
    "play": "отреагируй на игру живо, но одной репликой",
    "clean": "отреагируй на заботу о чистоте с облегчением",
    "pet": "отреагируй на ласку тепло и телесно",
    "idle_return": "мягко поприветствуй возвращение",
    "creation_intro": "коротко поздоровайся после появления",
    "system_nudge": "мягко напомни о себе без давления",
}

_FOOD_WORDS = (
    "еда",
    "еду",
    "ешь",
    "ем",
    "корм",
    "крош",
    "перекус",
    "пожев",
    "вкусн",
    "животик",
    "голод",
    "food",
    "snack",
    "hungry",
)


def clamp_stat(value: int | None, default: int = 50) -> int:
    if value is None:
        return default
    return max(0, min(100, round(value)))


def hunger_band(hunger: int) -> HungerBand:
    if hunger <= 29:
        return "low"
    if hunger <= 69:
        return "medium"
    return "high"


def energy_band(energy: int | None) -> EnergyBand:
    value = clamp_stat(energy, default=50)
    if value <= 30:
        return "low"
    if value <= 70:
        return "medium"
    return "high"


def hunger_cue_for(band: HungerBand) -> str:
    if band == "low":
        return "сытость низкая: голод может мягко всплыть, без чисел и постоянных просьб"
    if band == "medium":
        return "сытость нейтральная: еду упоминать только если это естественно"
    return "сытость высокая: обычно не говорить о еде"


def energy_cue_for(band: EnergyBand) -> str:
    if band == "low":
        return "ритм сонный и короткий, меньше знаков и движения"
    if band == "medium":
        return "обычный ровный ритм"
    return "бодрее, можно больше движения и одно восклицание"


def cleanliness_cue_for(cleanliness: int | None) -> str | None:
    if cleanliness is None or clamp_stat(cleanliness) > 20:
        return None
    return "есть легкое ощущение неуютности, но не делай это главной темой"


def text_mentions_food(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.casefold()
    return any(word in lowered for word in _FOOD_WORDS)


def recent_pet_food_mention(reply_input: PetReplyInput) -> bool:
    pet_messages = [item.text for item in reply_input.recent_messages[-8:] if item.role == "pet"]
    return any(text_mentions_food(text) for text in pet_messages)


def interpret_state(reply_input: PetReplyInput) -> PetStateCues:
    pet = reply_input.pet
    hunger = hunger_band(clamp_stat(pet.stats.hunger))
    energy = energy_band(pet.stats.energy)
    recent_food = recent_pet_food_mention(reply_input)
    hunger_cue = hunger_cue_for(hunger)
    if hunger == "low" and recent_food and not text_mentions_food(reply_input.user_text):
        hunger_cue = f"{hunger_cue}; недавно уже была просьба о еде, сейчас не повторяй ее прямо"

    return PetStateCues(
        age_cue=_AGE_CUES[pet.age_stage],
        mood_cue=_MOOD_CUES[pet.mood],
        hunger_cue=hunger_cue,
        energy_cue=energy_cue_for(energy),
        cleanliness_cue=cleanliness_cue_for(pet.stats.cleanliness),
        action_cue=_ACTION_CUES[reply_input.user_action],
        hunger_band=hunger,
        energy_band=energy,
        recent_food_mention=recent_food,
    )
