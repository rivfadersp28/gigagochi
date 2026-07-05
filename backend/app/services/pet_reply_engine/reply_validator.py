from __future__ import annotations

import re

from app.services.pet_reply_engine.age_message_examples import all_stage_phrases
from app.services.pet_reply_engine.intent import is_lore_question, is_status_question
from app.services.pet_reply_engine.models import PetAgeStage, PetMood, PetValidationResult
from app.services.pet_reply_engine.reply_limits import MAX_REPLY_CHARS
from app.services.pet_reply_engine.speech_anchors import all_turn_anchor_texts
from app.services.pet_reply_engine.text_style import style_for_reply

ABSOLUTE_MAX_CHARS = MAX_REPLY_CHARS
BANNED_WORDS_FOR_PROMPT = (
    "ИИ",
    "AI",
    "ассистент",
    "пользователь",
    "языковая модель",
    "как модель",
    "как бот",
    "параметр",
    "состояние",
    "mood",
    "energy",
    "hunger",
    "state",
    "чем могу помочь",
    "как я могу помочь",
    "цифровой",
    "виртуальный",
    "на экране",
    "в приложении",
    "внутри игры",
    "интерфейс",
    "оживаю",
    "душа",
    "внутри меня",
    "искорка",
    "сияние",
)

_BANNED_PATTERNS = (
    re.compile(r"(?<!\w)ии(?!\w)", re.IGNORECASE),
    re.compile(r"(?<!\w)ai(?!\w)", re.IGNORECASE),
    re.compile(r"ассистент", re.IGNORECASE),
    re.compile(r"пользовател", re.IGNORECASE),
    re.compile(r"языковая\s+модель", re.IGNORECASE),
    re.compile(r"как\s+модель", re.IGNORECASE),
    re.compile(r"как\s+бот", re.IGNORECASE),
    re.compile(r"параметр", re.IGNORECASE),
    re.compile(r"(?<!\w)состояни[еяию](?!\w)", re.IGNORECASE),
    re.compile(r"(?<!\w)mood(?!\w)", re.IGNORECASE),
    re.compile(r"(?<!\w)energy(?!\w)", re.IGNORECASE),
    re.compile(r"(?<!\w)hunger(?!\w)", re.IGNORECASE),
    re.compile(r"(?<!\w)state(?!\w)", re.IGNORECASE),
    re.compile(r"чем\s+могу\s+помочь", re.IGNORECASE),
    re.compile(r"как\s+я\s+могу\s+помочь", re.IGNORECASE),
    re.compile(r"\bцифров\w*", re.IGNORECASE),
    re.compile(r"\bвиртуаль\w*", re.IGNORECASE),
    re.compile(r"на\s+экран\w*", re.IGNORECASE),
    re.compile(r"в\s+приложени[еяию]", re.IGNORECASE),
    re.compile(r"внутри\s+игр[ыаеу]", re.IGNORECASE),
    re.compile(r"интерфейс", re.IGNORECASE),
    re.compile(r"ожива", re.IGNORECASE),
    re.compile(r"(?<!\w)душ[аеуы](?!\w)", re.IGNORECASE),
    re.compile(r"внутри\s+меня", re.IGNORECASE),
    re.compile(r"искорк", re.IGNORECASE),
    re.compile(r"сияни", re.IGNORECASE),
)
_UNCLEAR_ABSTRACTION_PATTERNS = (
    re.compile(r"так\s+я\s+быстрее\s+\w+", re.IGNORECASE),
    re.compile(r"мне\s+нужно,\s+чтобы\s+ты", re.IGNORECASE),
    re.compile(r"я\s+становлюсь\s+\w+", re.IGNORECASE),
    re.compile(r"мое\s+сердце", re.IGNORECASE),
    re.compile(r"внутри\s+(?:теплее|светлее|темнее|пусто)", re.IGNORECASE),
)
_TEMPLATE_LORE_PHRASE_PATTERNS = (
    re.compile(r"\bкоротк\w*\s+просьб", re.IGNORECASE),
    re.compile(r"\bлюб\w*\s+[^.!?…]*(?:туман[^.!?…]*лейк|лейк[^.!?…]*туман)", re.IGNORECASE),
)
_LITERAL_AGE_CLAIM_PATTERNS = (
    re.compile(
        r"\bмне\s+(?:уже\s+)?(?:за\s+)?(?:\d{1,3}|тридц\w+|сорок\w+)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:\d{1,3}|тридц\w+|сорок\w+)\s*(?:лет|года|год)\b", re.IGNORECASE),
    re.compile(r"\b(?:за|под)\s+(?:\d{2}|тридц\w+|сорок\w+)\b", re.IGNORECASE),
)
_MARKDOWN_OR_LIST_PATTERN = re.compile(r"^\s*(?:[-*•]|\d+[.)]|#{1,6})\s+", re.MULTILINE)
_WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
_SENTENCE_END_PATTERN = re.compile(r"[.!?…]+")
_WRAPPING_QUOTES = (('"', '"'), ("'", "'"), ("«", "»"), ("“", "”"), ("„", "“"))
_THIRD_PERSON_START_PATTERN = re.compile(
    r"^\s*(?:питомец|малыш|малютка|друг|дракончик|котик)\s+",
    re.IGNORECASE,
)
_POSITIVE_STATE_PATTERN = re.compile(
    r"(вс[её]\s+хорошо|мне\s+хорошо|отлично|супер|классно|здорово|"
    r"я\s+рад|радуюсь|счастлив|весело|ура)",
    re.IGNORECASE,
)
_SAD_STATE_PATTERN = re.compile(
    r"(груст|печал|плохо|не\s+очень|тихо|помолчу|одинок|скуч|рядом|"
    r"посиди|посидишь|обними)",
    re.IGNORECASE,
)
_HUNGER_STATE_PATTERN = re.compile(
    r"(голод|крош|перекус|пожев|животик|вкусн|есть\s+хочу|покорми|ням)",
    re.IGNORECASE,
)
_FULL_STATE_PATTERN = re.compile(
    r"(сыт|сытая|сытый|не\s+голод|есть\s+не\s+хочу|не\s+хочу\s+есть)",
    re.IGNORECASE,
)
_NEGATIVE_STATE_PATTERN = re.compile(
    r"(мне\s+плохо|груст|печал|одинок|не\s+хочу|слез|тоск)",
    re.IGNORECASE,
)
_HIGH_EXCITEMENT_PATTERN = re.compile(
    r"(восторг|ура|супер|отлично|обожаю|счастлив|радуюсь)",
    re.IGNORECASE,
)
_DRY_BABY_REPLY_PATTERNS = (
    re.compile(r"\bя\s+безымян\w*", re.IGNORECASE),
    re.compile(r"\bбезымян\w*", re.IGNORECASE),
    re.compile(r"\bу\s+меня\s+нет\s+имени\b", re.IGNORECASE),
    re.compile(r"^\s*(?:я\s+)?не\s+знаю[.!?…)]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:я\s+)?не\s+понимаю[.!?…)]*\s*$", re.IGNORECASE),
)
_BABY_CODED_PATTERN = re.compile(
    r"(?:\bуля\b|\bпи(?:ку)?\b|\bням\b|глустн|стлашн|болшо|спатк|кусят|"
    r"делжи|клепк|иглат|да-да|нет-нет|ещ[её]-ещ[её]|очень-очень)",
    re.IGNORECASE,
)
_REPEAT_STOPWORDS = {
    "меня",
    "тебя",
    "тебе",
    "тобой",
    "себя",
    "себе",
    "свой",
    "своя",
    "свои",
    "свое",
    "своё",
    "очень",
    "просто",
    "сейчас",
    "потом",
    "когда",
    "если",
    "только",
    "можно",
    "хочу",
    "буду",
    "будет",
    "знаешь",
    "смотри",
}


def _word_count(text: str) -> int:
    return len(_WORD_PATTERN.findall(text))


def _has_wrapping_quotes(text: str) -> bool:
    return any(text.startswith(left) and text.endswith(right) for left, right in _WRAPPING_QUOTES)


def _sentence_count(text: str) -> int:
    return len(_SENTENCE_END_PATTERN.findall(text))


def _has_banned_terms(text: str) -> bool:
    return any(pattern.search(text) for pattern in _BANNED_PATTERNS)


def _has_unclear_abstraction(text: str) -> bool:
    return any(pattern.search(text) for pattern in _UNCLEAR_ABSTRACTION_PATTERNS)


def _has_template_lore_phrase(text: str) -> bool:
    return any(pattern.search(text) for pattern in _TEMPLATE_LORE_PHRASE_PATTERNS)


def _has_literal_age_claim(text: str) -> bool:
    return any(pattern.search(text) for pattern in _LITERAL_AGE_CLAIM_PATTERNS)


def _starts_with_pet_name(text: str, pet_name: str | None) -> bool:
    if not pet_name:
        return False
    escaped_name = re.escape(pet_name.strip())
    if not escaped_name:
        return False
    return bool(re.match(rf"^\s*{escaped_name}\b", text, re.IGNORECASE))


def _has_mood_mismatch(text: str, mood: PetMood | None, user_text: str | None) -> bool:
    if mood is None:
        return False

    status_question = is_status_question(user_text)

    if mood == "sad":
        return bool(_POSITIVE_STATE_PATTERN.search(text)) or (
            status_question and not _SAD_STATE_PATTERN.search(text)
        )
    if mood == "hungry":
        return bool(_FULL_STATE_PATTERN.search(text)) or (
            status_question and not _HUNGER_STATE_PATTERN.search(text)
        )
    if mood == "happy":
        return bool(_NEGATIVE_STATE_PATTERN.search(text)) or (
            status_question and not _POSITIVE_STATE_PATTERN.search(text)
        )
    if mood == "idle":
        return status_question and bool(
            _HIGH_EXCITEMENT_PATTERN.search(text)
            or _NEGATIVE_STATE_PATTERN.search(text)
            or _HUNGER_STATE_PATTERN.search(text)
        )

    return False


def _is_dry_baby_reply(text: str, age_stage: PetAgeStage) -> bool:
    if age_stage != "baby":
        return False
    return any(pattern.search(text) for pattern in _DRY_BABY_REPLY_PATTERNS)


def _has_age_style_mismatch(text: str, age_stage: PetAgeStage) -> bool:
    if age_stage == "adult":
        return bool(_BABY_CODED_PATTERN.search(text))
    return False


def _has_multi_example_copy(text: str, age_stage: PetAgeStage) -> bool:
    normalized_text = _normalize_for_copy_check(text)
    matches = 0
    for phrase in all_stage_phrases(age_stage):
        normalized_phrase = _normalize_for_copy_check(phrase)
        if len(normalized_phrase) < 12:
            continue
        if normalized_phrase in normalized_text:
            matches += 1
        if matches >= 2:
            return True
    return False


def _has_turn_anchor_copy(text: str, age_stage: PetAgeStage) -> bool:
    normalized_text = _normalize_for_copy_check(text)
    if len(normalized_text) < 18:
        return False
    for phrase in all_turn_anchor_texts(age_stage):
        normalized_phrase = _normalize_for_copy_check(phrase)
        if len(normalized_phrase) < 18:
            continue
        if normalized_text == normalized_phrase or normalized_phrase in normalized_text:
            return True
    return False


def _normalize_for_copy_check(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\"'«»“”„*.,!?…:;()—-]+", " ", text.casefold())).strip()


def _content_words(text: str) -> list[str]:
    return [
        word.casefold()
        for word in _WORD_PATTERN.findall(text)
        if len(word) >= 4 and word.casefold() not in _REPEAT_STOPWORDS
    ]


def _repeated_phrases(text: str) -> set[str]:
    words = _content_words(text)
    phrases: set[str] = set()
    for size in (2, 3):
        for index in range(0, max(0, len(words) - size + 1)):
            phrases.add(" ".join(words[index : index + size]))
    return phrases


def _has_repeated_lore_term(
    text: str,
    recent_pet_replies: tuple[str, ...],
    user_text: str | None,
) -> bool:
    if is_lore_question(user_text):
        return False
    current = _repeated_phrases(text)
    if not current:
        return False
    user_phrases = _repeated_phrases(user_text or "")
    recent_matches = 0
    for reply in recent_pet_replies[-4:]:
        overlap = current & _repeated_phrases(reply)
        if user_phrases:
            overlap -= user_phrases
        if overlap:
            recent_matches += 1
    return recent_matches >= 2


def validate_reply(
    reply: str,
    age_stage: PetAgeStage,
    pet_name: str | None = None,
    current_mood: PetMood | None = None,
    user_text: str | None = None,
    recent_pet_replies: tuple[str, ...] = (),
) -> PetValidationResult:
    text = reply.strip()
    flags: list[str] = []

    if not text:
        flags.append("empty")
        return PetValidationResult(False, text, tuple(flags))

    style = style_for_reply(age_stage, lore_question=is_lore_question(user_text))
    if len(text) > ABSOLUTE_MAX_CHARS:
        flags.append("absolute_too_long")
    if len(text) > style.max_chars:
        flags.append("too_many_chars")
    if _word_count(text) > style.max_words:
        flags.append("too_many_words")
    if "\n" in text:
        flags.append("multi_paragraph")
    if _has_wrapping_quotes(text):
        flags.append("wrapping_quotes")
    if _MARKDOWN_OR_LIST_PATTERN.search(text) or "`" in text:
        flags.append("markdown_or_list")
    if text.startswith("{") or text.startswith("["):
        flags.append("structured_text")
    if _has_banned_terms(text):
        flags.append("banned_word")
    if _has_unclear_abstraction(text):
        flags.append("unclear_abstraction")
    if _has_template_lore_phrase(text):
        flags.append("template_lore_phrase")
    if _has_literal_age_claim(text):
        flags.append("literal_age_claim")
    if _is_dry_baby_reply(text, age_stage):
        flags.append("dry_baby_reply")
    if _has_age_style_mismatch(text, age_stage):
        flags.append("age_style_mismatch")
    if _has_multi_example_copy(text, age_stage):
        flags.append("copied_age_examples")
    if _has_turn_anchor_copy(text, age_stage):
        flags.append("copied_speech_anchor")
    if _has_repeated_lore_term(text, recent_pet_replies, user_text):
        flags.append("repeated_lore_term")
    third_person_blocked = age_stage != "baby" and (
        _starts_with_pet_name(text, pet_name) or _THIRD_PERSON_START_PATTERN.search(text)
    )
    if third_person_blocked:
        flags.append("third_person")
    if _has_mood_mismatch(text, current_mood, user_text):
        flags.append("mood_mismatch")
    if _sentence_count(text) > style.sentence_limit:
        flags.append("too_many_sentences")

    return PetValidationResult(not flags, text, tuple(flags))
