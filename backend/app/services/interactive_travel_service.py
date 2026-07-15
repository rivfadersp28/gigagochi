from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.llm.compat import complete_chat, response_log_value
from app.llm.contracts import LLMProviderError
from app.llm.runtime import resolve_llm_model
from app.schemas import (
    InteractiveTravelAnimationResponse,
    InteractiveTravelIllustrationResponse,
    InteractiveTravelIntroReaction,
    InteractiveTravelPart,
    InteractiveTravelResponse,
    InteractiveTravelResult,
    InteractiveTravelState,
    InteractiveTravelStatImpact,
    InteractiveTravelSuggestionsResponse,
    LocalChatHistoryItem,
    LocalPetChatContext,
    LocalPetMemoryContext,
)
from app.services.openai_service import chat_reasoning_effort_kwargs, get_chat_model
from app.services.pet_reply_engine.speech_runtime import background_story_reasoning_effort
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response
from app.services.travel_service import (
    generate_interactive_travel_part_image,
    generate_interactive_travel_part_video,
)

STORY_PART_COUNT = 4
GENERATOR_VERSION = "fixed-4-v1"
MIN_TIMEOUT_SECONDS = 120.0
CHOICE_MAX_WORDS = 3
CHOICE_MAX_LENGTH = 40
CHOICE_TRAILING_PREPOSITIONS = {
    "без",
    "в",
    "для",
    "до",
    "за",
    "из",
    "к",
    "на",
    "над",
    "от",
    "по",
    "под",
    "с",
    "у",
    "через",
}
TIME_SKIP_MIN_HOURS = 2
TIME_SKIP_MAX_HOURS = 5
PET_STAT_KEYS = {"hunger", "happiness", "energy"}

MONTH_NAMES = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)

DESTINATION_FALLBACKS = (
    "в подземелье",
    "на болото",
    "в лес",
    "к маяку",
    "в пещеру",
    "на остров",
    "в пустыню",
    "к вулкану",
    "на ярмарку",
    "в крепость",
    "к озеру",
    "в деревню",
    "в шахту",
    "на кладбище",
    "в башню",
)

CHOICE_FALLBACKS = (
    "Осмотреться",
    "Позвать помощь",
    "Идти дальше",
)

# Модель строит приключение вокруг одного готового факта. Она не отвечает за его истинность.
FUN_FACTS = (
    "В Северном полушарии Полярная звезда показывает направление на север",
    "Многие летучие мыши находят путь с помощью эха собственных звуков",
    "Лёд плавает, потому что он менее плотный, чем жидкая вода",
    "На холодной поверхности водяной пар из воздуха превращается в жидкие капли",
    "Белый свет разделяется на разные цвета, проходя через призму",
    "Ржавчина появляется, когда железо долго соприкасается с водой и кислородом",
)

SYSTEM_PROMPT = "\n".join(
    (
        "Создай короткое интерактивное приключение для ребёнка 9–14 лет.",
        "Пиши просто, от первого лица и в настоящем времени.",
        "Одна ситуация — одно интересное событие, максимум два коротких предложения.",
        "Каждая ситуация мешает идти дальше и требует выбора: препятствие, конфликт, "
        "встреча с требованием или головоломка.",
        "Пассивное наблюдение без проблемы и решения не считается ситуацией.",
        "Между соседними ситуациями проходит несколько часов пути.",
        "Каждый вариант действия — максимум три слова и не заканчивается предлогом.",
        "Лёгкое фэнтези допустимо.",
        "Выполняй выбранное пользователем действие буквально или показывай честную попытку.",
        "Выбор меняет только результат текущей ситуации, но не следующую часть готового сюжета.",
        "Если дан правдивый факт, естественно вплети его смысл в первую ситуацию.",
        "Не оформляй факт отдельной сноской и не придумывай другие научные факты.",
        "Верни только JSON по схеме.",
    )
)

CHOICE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": CHOICE_MAX_LENGTH,
    "description": "Короткое действие: от одного до трёх слов.",
}


PLAN_PART_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "situation": {
            "type": "string",
            "minLength": 1,
            "maxLength": 300,
            "description": (
                "Одно-два коротких предложения от первого лица: куда я пришёл и что случилось."
            ),
        },
        "question": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        },
        "choices": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": CHOICE_SCHEMA,
        },
    },
    "required": ["situation", "question", "choices"],
}


START_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "goal": {"type": "string", "minLength": 1, "maxLength": 180},
        "ending": {
            "type": "string",
            "minLength": 1,
            "maxLength": 180,
            "description": (
                "Готовая развязка с конкретным предметом или результатом: что именно я получил, "
                "как достиг цели и что могу вернуться домой."
            ),
        },
        "parts": {
            "type": "array",
            "minItems": STORY_PART_COUNT,
            "maxItems": STORY_PART_COUNT,
            "items": PLAN_PART_SCHEMA,
        },
    },
    "required": ["title", "goal", "ending", "parts"],
}

RESULT_PROPERTIES: dict[str, Any] = {
    "result": {"type": "string", "minLength": 1, "maxLength": 240},
    "outcome": {"type": "string", "enum": ["positive", "negative"]},
    "stat": {
        "type": "string",
        "enum": ["none", "hunger", "happiness", "energy"],
    },
    "amount": {"type": "integer", "minimum": -15, "maximum": 15},
    "reason": {"type": "string", "minLength": 1, "maxLength": 120},
}

CONTINUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **RESULT_PROPERTIES,
        "nextSituation": {
            "type": "string",
            "minLength": 1,
            "maxLength": 300,
            "description": (
                "Новое препятствие после нескольких часов пути. Без решения герой не может "
                "продолжить путешествие."
            ),
        },
        "nextQuestion": {"type": "string", "minLength": 1, "maxLength": 120},
        "nextChoice1": CHOICE_SCHEMA,
        "nextChoice2": CHOICE_SCHEMA,
        "nextChoice3": CHOICE_SCHEMA,
    },
    "required": [
        *RESULT_PROPERTIES,
        "nextSituation",
        "nextQuestion",
        "nextChoice1",
        "nextChoice2",
        "nextChoice3",
    ],
}

FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": RESULT_PROPERTIES,
    "required": list(RESULT_PROPERTIES),
}


class InteractiveTravelGenerationError(RuntimeError):
    pass


def _compact_text(value: Any, limit: int, fallback: str = "") -> str:
    text = " ".join(str(value or "").split()).strip() or fallback
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—–-")
    return shortened or text[:limit]


def _sentence(value: Any, *, fallback: str, limit: int, question: bool = False) -> str:
    text = _compact_text(value, limit - 1, fallback).rstrip(" .!?…")
    return f"{text}{'?' if question else '.'}"


def _choices(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*values, *CHOICE_FALLBACKS]:
        compact = _compact_text(value, CHOICE_MAX_LENGTH).rstrip(" .!?…")
        all_words = compact.split()
        if len(all_words) > CHOICE_MAX_WORDS and all_words[1].casefold() in {
            "в",
            "к",
            "на",
            "по",
            "под",
            "через",
        }:
            words = [all_words[0], all_words[1], all_words[-1]]
        else:
            words = all_words[:CHOICE_MAX_WORDS]
        while len(words) > 1 and words[-1].casefold() in CHOICE_TRAILING_PREPOSITIONS:
            words.pop()
        choice = " ".join(words)
        key = choice.casefold()
        if not choice or key in seen:
            continue
        seen.add(key)
        result.append(choice)
        if len(result) == 3:
            break
    return result


def _hours_phrase(hours: int) -> str:
    return f"{hours} {'часа' if hours in {2, 3, 4} else 'часов'}"


def _current_month() -> str:
    return MONTH_NAMES[datetime.now(UTC).month - 1]


def _story_plan_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_parts = payload.get("parts")
    if not isinstance(raw_parts, list) or len(raw_parts) != STORY_PART_COUNT:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
        if any(
            not isinstance(raw_part.get(field), str) or not raw_part[field].strip()
            for field in ("situation", "question")
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
        choices = raw_part.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 3
            or any(not isinstance(choice, str) or not choice.strip() for choice in choices)
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
    return raw_parts


def _parts_from_plan_payload(payload: dict[str, Any]) -> list[InteractiveTravelPart]:
    return [
        InteractiveTravelPart(
            partNumber=part_number,
            title=f"Часть {part_number}",
            storyText=_sentence(
                raw_part["situation"],
                fallback="На пути происходит что-то неожиданное",
                limit=300,
            ),
            challenge=_sentence(
                raw_part["question"],
                fallback="Что мне сделать",
                limit=120,
                question=True,
            ),
            actionSuggestions=_choices(raw_part["choices"]),
        )
        for part_number, raw_part in enumerate(_story_plan_items(payload), start=1)
    ]


def _arc_plan_from_payload(
    payload: dict[str, Any], *, parts: list[InteractiveTravelPart], fun_fact: str
) -> dict[str, str]:
    arc_plan = {
        "generatorVersion": GENERATOR_VERSION,
        "funFact": fun_fact,
        "storyGoal": _sentence(
            payload.get("goal"),
            fallback="Завершить путешествие",
            limit=180,
        ),
        "storyEnding": _sentence(
            payload.get("ending"),
            fallback="Я достигаю цели и могу вернуться домой",
            limit=180,
        ),
    }
    for part in parts[1:]:
        part_number = part.partNumber
        prefix = f"part{part_number}"
        arc_plan[f"{prefix}Situation"] = part.storyText
        arc_plan[f"{prefix}Question"] = part.challenge
        for choice_number, choice in enumerate(part.actionSuggestions, start=1):
            arc_plan[f"{prefix}Choice{choice_number}"] = choice
    return arc_plan


def _required_arc_value(arc_plan: dict[str, str], key: str) -> str:
    value = _compact_text(arc_plan.get(key), 500)
    if not value:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_MISSING")
    return value


def _planned_part_from_arc(
    *,
    arc_plan: dict[str, str],
    part_number: int,
    previous_result: InteractiveTravelResult,
    elapsed_hours: int,
) -> InteractiveTravelPart:
    prefix = f"part{part_number}"
    return InteractiveTravelPart(
        partNumber=part_number,
        title=f"Часть {part_number}",
        storyText=_required_arc_value(arc_plan, f"{prefix}Situation"),
        transition={
            "elapsedHours": elapsed_hours,
            "summary": previous_result.consequence,
            "departureHook": f"Я продолжаю путь. Проходит {_hours_phrase(elapsed_hours)}.",
        },
        challenge=_required_arc_value(arc_plan, f"{prefix}Question"),
        actionSuggestions=[
            _required_arc_value(arc_plan, f"{prefix}Choice{choice_number}")
            for choice_number in range(1, 4)
        ],
    )


def _intro_text(destination: str) -> str:
    return _sentence(
        random.choice(
            (
                f"Отправлюсь {destination}. Посмотрим, что меня там ждёт.",
                f"Пора отправляться {destination}. Скоро узнаю, что там происходит.",
                f"Я отправляюсь {destination}. Интересно, что встречу по пути.",
            )
        ),
        fallback="Я отправляюсь в путешествие",
        limit=220,
    )


def _character_summary(pet: LocalPetChatContext) -> str:
    name = _compact_text(pet.name, 40, "Герой")
    identity = pet.characterBible.get("identity") if isinstance(pet.characterBible, dict) else None
    species = identity.get("species") if isinstance(identity, dict) else None
    description = _compact_text(pet.description, 150)
    pieces = [name]
    if species:
        pieces.append(_compact_text(species, 40))
    if description:
        pieces.append(description)
    return ", ".join(pieces)


def _completion_payload(completion: Any) -> dict[str, Any]:
    try:
        payload = json.loads(completion.content or "{}")
    except json.JSONDecodeError as exc:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_JSON_INVALID") from exc
    if not isinstance(payload, dict):
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_JSON_INVALID")
    return payload


def _model_and_timeout(
    *, client: Any | None, model: str | None, timeout: float | None
) -> tuple[str | None, float]:
    settings = get_settings()
    fallback_model = getattr(settings, "full_story_model", None) or get_chat_model(settings)
    resolved_model = model or (
        fallback_model if client is not None else resolve_llm_model("full_story", fallback_model)
    )
    configured_timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    return resolved_model, max(float(configured_timeout), MIN_TIMEOUT_SECONDS)


def _request(
    *,
    label: str,
    schema_name: str,
    schema: dict[str, Any],
    user_content: str,
    client: Any | None,
    model: str | None,
    timeout: float,
    required_text_fields: tuple[str, ...] = (),
    require_story_plan: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(background_story_reasoning_effort()),
    }
    debug = [log_chat_completion_prompt(label, kwargs)]

    def complete(request_label: str, request_kwargs: dict[str, Any]) -> dict[str, Any]:
        completion = complete_chat("full_story", request_kwargs, client=client)
        debug.append(log_chat_completion_response(request_label, response_log_value(completion)))
        payload = _completion_payload(completion)
        if any(
            not isinstance(payload.get(field), str) or not payload[field].strip()
            for field in required_text_fields
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
        if require_story_plan:
            _story_plan_items(payload)
        return payload

    try:
        return complete(label, kwargs), debug
    except (LLMProviderError, InteractiveTravelGenerationError):
        retry_label = f"{label}_technical_retry"
        retry_kwargs = {
            **kwargs,
            "messages": [
                *kwargs["messages"],
                {"role": "user", "content": "Верни только корректный JSON по исходной схеме."},
            ],
        }
        debug.append(log_chat_completion_prompt(retry_label, retry_kwargs))
        return complete(retry_label, retry_kwargs), debug


def _result_from_payload(
    payload: dict[str, Any], *, advice: str, fallback_outcome: str
) -> InteractiveTravelResult:
    outcome = payload.get("outcome")
    if outcome not in {"positive", "negative"}:
        outcome = fallback_outcome
    consequence = _sentence(
        payload.get("result"),
        fallback=f"Я пробую: {_compact_text(advice, 100, 'идти дальше')}",
        limit=240,
    )
    stat_impacts: list[InteractiveTravelStatImpact] = []
    stat = payload.get("stat")
    amount = payload.get("amount")
    if (
        stat in PET_STAT_KEYS
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        and amount
    ):
        stat_impacts.append(
            InteractiveTravelStatImpact(
                stat=stat,
                amount=max(-15, min(15, amount)),
                reason=_sentence(
                    payload.get("reason"),
                    fallback="Это повлияло на моё состояние",
                    limit=120,
                ),
            )
        )
    return InteractiveTravelResult(
        text=consequence,
        adviceAssessment="helpful" if outcome == "positive" else "harmful",
        reaction=_sentence(
            f"Выбираю: {_compact_text(advice, 180, 'идти дальше')}",
            fallback="Пробую",
            limit=220,
        ),
        reactionTone="determined" if outcome == "positive" else "worried",
        consequence=consequence,
        outcomeValence=outcome,
        statImpacts=stat_impacts,
    )


def generate_interactive_travel_suggestions(
    *,
    pet: LocalPetChatContext,
    include_debug: bool = False,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> InteractiveTravelSuggestionsResponse:
    del pet, client, model, timeout
    return InteractiveTravelSuggestionsResponse(
        destinations=random.sample(DESTINATION_FALLBACKS, 3),
        debug={"promptDebug": []} if include_debug else None,
    )


def illustrate_interactive_travel_part(
    *,
    pet: LocalPetChatContext,
    travel_id: str,
    destination: str,
    part_number: int,
    title: str,
    story_text: str,
) -> InteractiveTravelIllustrationResponse:
    image_url = generate_interactive_travel_part_image(
        pet=pet,
        travel_id=travel_id,
        destination=destination,
        part_number=part_number,
        title=title,
        story_text=story_text,
    )
    return InteractiveTravelIllustrationResponse(partNumber=part_number, imageUrl=image_url)


def animate_interactive_travel_part(
    *,
    travel_id: str,
    part_number: int,
) -> InteractiveTravelAnimationResponse:
    video_url = generate_interactive_travel_part_video(
        travel_id=travel_id,
        part_number=part_number,
    )
    return InteractiveTravelAnimationResponse(partNumber=part_number, videoUrl=video_url)


def start_interactive_travel(
    *,
    pet: LocalPetChatContext,
    destination: str,
    history: list[LocalChatHistoryItem] | None = None,
    memory_context: LocalPetMemoryContext | None = None,
    include_debug: bool = False,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> InteractiveTravelResponse:
    del history, memory_context
    model, timeout = _model_and_timeout(client=client, model=model, timeout=timeout)
    clean_destination = _compact_text(destination, 500, "в путешествие")
    fun_fact = random.choice(FUN_FACTS)
    month = _current_month()
    payload, debug = _request(
        label="interactive_travel/start_fixed_4",
        schema_name="interactive_travel_fixed_story_v2",
        schema=START_SCHEMA,
        user_content=(
            "Давай сыграем. Придумай простую законченную историю ровно из четырёх частей.\n"
            f"Я — {_character_summary(pet)}.\n"
            f"Я отправляюсь {clean_destination}.\n"
            f"Месяц: {month}.\n"
            f"Правдивый факт для первой ситуации: {fun_fact}.\n\n"
            "Задай одну ясную цель всей истории. В отдельной развязке ending прямо назови, что "
            "именно я получил или сделал из исходной цели, и закончи словами о возвращении домой. "
            "Не используй вместо конкретного результата слова «искомое» или просто «цель».\n"
            "Все четыре части — один связанный маршрут. В situation каждой части сначала коротко "
            "скажи, куда я пришёл, затем что здесь случилось. После situation задай один вопрос "
            "«Что мне сделать?» и три коротких действия.\n"
            "Часть 1 начинает путь и объясняет цель. Части 2 и 3 приближают к ней. Между частями "
            "проходит несколько часов. Любой выбор может дать хороший или плохой локальный "
            "результат, но герой всё равно идёт в следующую заранее написанную часть. Поэтому "
            "Следующая часть не зависит от конкретного предмета или способа из предыдущего "
            "выбора.\n"
            "Часть 4 происходит прямо у цели. Это последнее испытание: после любого из трёх "
            "действий можно сразу показать ending. Не добавляй после него ворота, дорогу, новую "
            "тайну или новое задание.\n"
            "Естественно вплети смысл факта только в часть 1. Не пиши слово «фанфакт» и не "
            "выноси факт отдельно."
        ),
        client=client,
        model=model,
        timeout=timeout,
        required_text_fields=("title", "goal", "ending"),
        require_story_plan=True,
    )
    planned_parts = _parts_from_plan_payload(payload)
    travel = InteractiveTravelState(
        travelId=f"interactive-travel-{uuid4().hex}",
        generatedAt=datetime.now(UTC),
        destination=clean_destination,
        overallTitle=_compact_text(payload.get("title"), 120, "Приключение"),
        arcPlan=_arc_plan_from_payload(
            payload,
            parts=planned_parts,
            fun_fact=fun_fact,
        ),
        introReaction=InteractiveTravelIntroReaction(
            text=_intro_text(clean_destination),
            tone="determined",
        ),
        parts=[planned_parts[0]],
        completed=False,
    )
    return InteractiveTravelResponse(
        travel=travel,
        debug={"promptDebug": debug} if include_debug else None,
    )


def continue_interactive_travel(
    *,
    pet: LocalPetChatContext,
    travel: InteractiveTravelState,
    advice: str,
    history: list[LocalChatHistoryItem] | None = None,
    memory_context: LocalPetMemoryContext | None = None,
    include_debug: bool = False,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
    tie_break_valence: str | None = None,
) -> InteractiveTravelResponse:
    del history, memory_context
    if travel.completed:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_ALREADY_COMPLETED")
    current_part = travel.parts[-1]
    if current_part.result is not None:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PENDING_PART_MISSING")

    fallback_outcome = tie_break_valence or random.choice(("positive", "negative"))
    if fallback_outcome not in {"positive", "negative"}:
        raise ValueError("tie_break_valence must be positive or negative")
    model, timeout = _model_and_timeout(client=client, model=model, timeout=timeout)
    clean_advice = _compact_text(advice, 1000, "идти дальше")
    is_final = current_part.partNumber >= STORY_PART_COUNT
    elapsed_hours = 0 if is_final else random.randint(TIME_SKIP_MIN_HOURS, TIME_SKIP_MAX_HOURS)
    uses_fixed_plan = travel.arcPlan.get("generatorVersion") == GENERATOR_VERSION
    story_ending: str | None = None
    if uses_fixed_plan:
        story_goal = _required_arc_value(travel.arcPlan, "storyGoal")
        if is_final:
            story_ending = _required_arc_value(travel.arcPlan, "storyEnding")
            instruction = (
                "Это четвёртая и последняя часть. Покажи только прямой результат выбранного "
                "действия в текущей ситуации. Не начинай новую задачу и не повторяй готовую "
                f"развязку — сервер добавит её следом: {story_ending} Исходная цель: {story_goal}"
            )
        else:
            next_situation = _required_arc_value(
                travel.arcPlan,
                f"part{current_part.partNumber + 1}Situation",
            )
            instruction = (
                "Покажи только результат действия в текущей ситуации. После него герой всё равно "
                f"может продолжить путь и через {_hours_phrase(elapsed_hours)} попадает в уже "
                f"готовую следующую ситуацию: {next_situation} "
                "Закончи результат словами о том, что я иду дальше. Не меняй и не решай "
                "следующую ситуацию сейчас."
            )
        schema = FINAL_SCHEMA
        label = f"interactive_travel/part_{current_part.partNumber}_result_fixed"
        schema_name = "interactive_travel_part_result_fixed_v1"
    else:
        instruction = (
            "Покажи результат действия и закончи приключение. Не придумывай новую ситуацию."
            if is_final
            else (
                f"Покажи результат действия. Затем проходит {_hours_phrase(elapsed_hours)} пути. "
                "В новом месте придумай отдельное препятствие, конфликт, встречу с требованием "
                "или головоломку. Без решения нельзя идти дальше. Добавь три действия."
            )
        )
        schema = FINAL_SCHEMA if is_final else CONTINUE_SCHEMA
        label = f"interactive_travel/part_{current_part.partNumber}_simple"
        schema_name = (
            "interactive_travel_final_simple_v1"
            if is_final
            else "interactive_travel_continue_simple_v1"
        )
    payload, debug = _request(
        label=label,
        schema_name=schema_name,
        schema=schema,
        user_content=(
            "Покажи результат выбора в приключении.\n"
            f"Я — {_character_summary(pet)}.\n"
            f"Место: {travel.destination}.\n"
            f"Цель: {travel.arcPlan.get('storyGoal', 'завершить путешествие')}\n"
            f"Сейчас: {current_part.storyText}\n"
            f"Я делаю: {clean_advice}.\n\n"
            f"{instruction}\n"
            "Пиши просто, максимум два коротких предложения. "
            "Действие должно реально повлиять "
            "на текущую ситуацию: хорошо или плохо. Можно изменить один показатель hunger, "
            "happiness или energy на число от -15 до 15. Если показатель не меняется, верни "
            "stat=none, amount=0 и короткую reason. Не добавляй научные факты."
        ),
        client=client,
        model=model,
        timeout=timeout,
    )
    result = _result_from_payload(
        payload,
        advice=clean_advice,
        fallback_outcome=fallback_outcome,
    )
    if story_ending is not None:
        result = result.model_copy(
            update={"text": _compact_text(f"{result.text} {story_ending}", 700)}
        )
    resolved_part = InteractiveTravelPart.model_validate(
        current_part.model_dump(mode="json")
        | {
            "answer": clean_advice,
            "result": result.model_dump(mode="json"),
        }
    )
    parts = [*travel.parts[:-1], resolved_part]
    if not is_final:
        next_number = current_part.partNumber + 1
        if uses_fixed_plan:
            parts.append(
                _planned_part_from_arc(
                    arc_plan=travel.arcPlan,
                    part_number=next_number,
                    previous_result=result,
                    elapsed_hours=elapsed_hours,
                )
            )
        else:
            parts.append(
                InteractiveTravelPart(
                    partNumber=next_number,
                    title=f"Часть {next_number}",
                    storyText=_sentence(
                        payload.get("nextSituation"),
                        fallback="Впереди происходит новое событие",
                        limit=300,
                    ),
                    transition={
                        "elapsedHours": elapsed_hours,
                        "summary": result.consequence,
                        "departureHook": (
                            f"Я продолжаю путь. Проходит {_hours_phrase(elapsed_hours)}."
                        ),
                    },
                    challenge=_sentence(
                        payload.get("nextQuestion"),
                        fallback="Что мне сделать",
                        limit=120,
                        question=True,
                    ),
                    actionSuggestions=_choices(
                        [
                            payload.get("nextChoice1"),
                            payload.get("nextChoice2"),
                            payload.get("nextChoice3"),
                        ]
                    ),
                )
            )

    next_travel = InteractiveTravelState.model_validate(
        travel.model_dump(mode="json")
        | {
            "parts": [part.model_dump(mode="json") for part in parts],
            "completed": is_final,
            "outcomeValence": result.outcomeValence if is_final else None,
            "statImpact": None,
        }
    )
    return InteractiveTravelResponse(
        travel=next_travel,
        debug={"promptDebug": debug} if include_debug else None,
    )
