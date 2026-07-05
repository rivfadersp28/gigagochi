from __future__ import annotations

import re
from typing import Literal

from app.services.pet_reply_engine.models import PetRecentMessage

PetReplyIntent = Literal[
    "care",
    "answer_lore",
    "answer_preference",
    "why",
    "appearance",
    "status",
    "continue_thread",
    "playful_offer",
    "boundary",
    "memory_control",
    "smalltalk",
]

_APPEARANCE_QUESTION_PATTERN = re.compile(
    r"(как\s+(?:ты\s+)?выгляд|как\s+выглядишь|"
    r"опиши\s+себя|какая\s+у\s+тебя\s+внешность|"
    r"какой\s+ты\s+на\s+вид|что\s+у\s+тебя\s+на\s+виду|"
    r"what\s+do\s+you\s+look\s+like|describe\s+yourself)",
    re.IGNORECASE,
)
_LOCATION_QUESTION_PATTERN = re.compile(
    r"(где\s+ты|ты\s+где|где\s+находишься|где\s+ты\s+сейчас|"
    r"где\s+(?:ты\s+)?жив[её]шь|where\s+are\s+you|where\s+do\s+you\s+live)",
    re.IGNORECASE,
)
_HOME_QUESTION_PATTERN = re.compile(
    r"(где\s+(?:ты\s+)?жив[её]шь|где\s+твой\s+дом|твой\s+дом|у\s+тебя\s+есть\s+дом|"
    r"\bдом(?:ик|е|а|у|ом)?\b|комнат[ауы]|логов[ое]|гнезд[ооае]|пещерк|пещер[аеуы]|мир|"
    r"теплиц|оранжере|сад|подоконн|полк[аеуы]|уголок|город|район|мест[оаеуы]|"
    r"любимое\s+место|where\s+do\s+you\s+live|your\s+home|your\s+room|"
    r"your\s+world|favorite\s+place)",
    re.IGNORECASE,
)
_RELATIONSHIP_QUESTION_PATTERN = re.compile(
    r"(родител|семь[яеию]|мам[ауы]|пап[ауы]|друз|друг|подруг|кто\s+с\s+тобой|"
    r"сосед|знаком|приятел|близк|родн|компаньон|family|parents|mother|father|"
    r"friends?|who\s+is\s+with\s+you)",
    re.IGNORECASE,
)
_PREFERENCE_QUESTION_PATTERN = re.compile(
    r"(что\s+(?:ты\s+)?любишь|что\s+тебе\s+нравится|нравится|не\s+любишь|"
    r"боишься|страшно|мечтаешь|мечта|привычк|что\s+у\s+тебя\s+есть|"
    r"игрушк|любим[а-я]+\s+(?:вещь|предмет)|what\s+do\s+you\s+like|"
    r"what\s+do\s+you\s+dislike|fear|dream|favorite\s+(?:thing|toy|object)|"
    r"what\s+do\s+you\s+have)",
    re.IGNORECASE,
)
_ORIGIN_QUESTION_PATTERN = re.compile(
    r"(откуда\s+ты|где\s+родил|как\s+появил|кто\s+ты|какой\s+ты|"
    r"расскажи\s+о\s+себе|твоя\s+история|прошл|истори|событ|детств|"
    r"воспомин|памят|случил|произош|напугал|раньше|where\s+are\s+you\s+from|"
    r"where\s+were\s+you\s+born|tell\s+me\s+about\s+yourself|your\s+story|"
    r"what\s+happened)",
    re.IGNORECASE,
)
_LORE_EXPANSION_QUESTION_PATTERN = re.compile(
    r"("
    r"(?:расскажи|покажи|объясни|опиши|напомни)\s+(?:мне\s+)?"
    r"(?:(?:побольше|подробнее|подетальнее|ещ[её]\s+немного)\s+)?"
    r"(?:о|об|про)\s+"
    r"|(?:а\s+)?(?:побольше|подробнее|подетальнее)\??\s*$"
    r"|(?:что|кто|как|почему|зачем)\s+.*(?:случил|было|произош|появил|стал|стала|стали)"
    r"|tell\s+me\s+(?:more\s+)?about|more\s+about|what\s+happened"
    r")",
    re.IGNORECASE,
)
_STATUS_QUESTION_PATTERN = re.compile(
    r"(как\s+(?:ты|у\s+тебя\s+дела|дела|сам|сама|настроение)|"
    r"что\s+с\s+тобой|how\s+are\s+you)",
    re.IGNORECASE,
)
_NAME_QUESTION_PATTERN = re.compile(
    r"(как\s+(?:тебя|вас)\s+зовут|как\s+звать|"
    r"какое\s+у\s+тебя\s+имя|тво[её]\s+имя|"
    r"who\s+are\s+you|what\s+is\s+your\s+name)",
    re.IGNORECASE,
)
_REASON_QUESTION_PATTERN = re.compile(
    r"^\s*(?:а\s+)?(?:почему|зачем|отчего|why)\??\s*$",
    re.IGNORECASE,
)
_BOUNDARY_PATTERN = re.compile(
    r"(не\s+(?:задавай|спрашивай)\s+(?:мне\s+)?вопрос|без\s+вопросов|"
    r"не\s+пиши\s+вопрос|перестань\s+спрашивать|don't\s+ask|no\s+questions)",
    re.IGNORECASE,
)
_MEMORY_CONTROL_PATTERN = re.compile(
    r"(что\s+(?:ты\s+)?(?:помнишь|запомнил)|что\s+ты\s+знаешь\s+обо\s+мне|"
    r"запомни|забудь|не\s+запоминай|не\s+помни|удали\s+из\s+памяти|"
    r"remember|forget|memory)",
    re.IGNORECASE,
)
_CARE_PATTERN = re.compile(
    r"(обним|глаж|поглаж|держи|покорм|накорм|почеш|укрою|я\s+с\s+тобой|"
    r"не\s+бойся|спокойно|иди\s+сюда|hug|pet\s+you|feed\s+you)",
    re.IGNORECASE,
)
_PLAYFUL_OFFER_PATTERN = re.compile(
    r"(давай\s+(?:играть|поиграем|придумаем|сделаем)|во\s+что\s+сыграем|"
    r"что\s+(?:мы\s+)?сделаем|придумай.*(?:вечером|игру|дело)|"
    r"play|game|what\s+should\s+we\s+do)",
    re.IGNORECASE,
)
_CONTINUE_THREAD_PATTERN = re.compile(
    r"^\s*(?:а\s+)?(?:дальше|продолжай|продолжим|подробнее|побольше|"
    r"ещ[её]|расскажи\s+ещ[её]|и\s+что\s+потом|more|continue)\??\s*$",
    re.IGNORECASE,
)


def is_appearance_question(text: str | None) -> bool:
    return bool(text and _APPEARANCE_QUESTION_PATTERN.search(text))


def is_location_question(text: str | None) -> bool:
    return bool(text and _LOCATION_QUESTION_PATTERN.search(text))


def is_home_question(text: str | None) -> bool:
    return bool(text and _HOME_QUESTION_PATTERN.search(text))


def is_relationship_question(text: str | None) -> bool:
    return bool(text and _RELATIONSHIP_QUESTION_PATTERN.search(text))


def is_preference_question(text: str | None) -> bool:
    return bool(text and _PREFERENCE_QUESTION_PATTERN.search(text))


def is_origin_question(text: str | None) -> bool:
    return bool(text and _ORIGIN_QUESTION_PATTERN.search(text))


def is_lore_question(text: str | None) -> bool:
    return bool(
        text
        and (
            _LORE_EXPANSION_QUESTION_PATTERN.search(text)
            or
            is_home_question(text)
            or is_relationship_question(text)
            or is_preference_question(text)
            or is_origin_question(text)
        )
    )


def is_status_question(text: str | None) -> bool:
    return bool(
        text
        and not is_appearance_question(text)
        and not is_location_question(text)
        and not is_lore_question(text)
        and _STATUS_QUESTION_PATTERN.search(text)
    )


def is_name_question(text: str | None) -> bool:
    return bool(text and _NAME_QUESTION_PATTERN.search(text))


def is_reason_question(text: str | None) -> bool:
    return bool(text and _REASON_QUESTION_PATTERN.search(text))


def _has_recent_context(recent_messages: tuple[PetRecentMessage, ...] | None) -> bool:
    return bool(recent_messages and any(item.text.strip() for item in recent_messages[-3:]))


def detect_reply_intent(
    text: str | None,
    recent_messages: tuple[PetRecentMessage, ...] | None = None,
) -> PetReplyIntent:
    if not text:
        return "smalltalk"
    if _MEMORY_CONTROL_PATTERN.search(text):
        return "memory_control"
    if _BOUNDARY_PATTERN.search(text):
        return "boundary"
    if is_appearance_question(text):
        return "appearance"
    if is_status_question(text):
        return "status"
    if is_reason_question(text):
        return "why"
    if _CONTINUE_THREAD_PATTERN.search(text) and _has_recent_context(recent_messages):
        return "continue_thread"
    if is_preference_question(text):
        return "answer_preference"
    if is_lore_question(text):
        return "answer_lore"
    if _CARE_PATTERN.search(text):
        return "care"
    if _PLAYFUL_OFFER_PATTERN.search(text):
        return "playful_offer"
    if _CONTINUE_THREAD_PATTERN.search(text):
        return "continue_thread"
    return "smalltalk"
