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

STORY_PART_COUNT = 3
MIN_TIMEOUT_SECONDS = 120.0

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
        "Лёгкое фэнтези допустимо.",
        "Выполняй выбранное пользователем действие буквально.",
        "Если дан правдивый факт, естественно вплети его смысл в первую ситуацию.",
        "Не оформляй факт отдельной сноской и не придумывай другие научные факты.",
        "Верни только JSON по схеме.",
    )
)

CHOICE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 72,
}

START_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "situation": {"type": "string", "minLength": 1, "maxLength": 300},
        "question": {"type": "string", "minLength": 1, "maxLength": 120},
        "choice1": CHOICE_SCHEMA,
        "choice2": CHOICE_SCHEMA,
        "choice3": CHOICE_SCHEMA,
    },
    "required": ["title", "situation", "question", "choice1", "choice2", "choice3"],
}

RESULT_PROPERTIES: dict[str, Any] = {
    "result": {"type": "string", "minLength": 1, "maxLength": 240},
    "outcome": {"type": "string", "enum": ["positive", "negative"]},
}

CONTINUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **RESULT_PROPERTIES,
        "nextSituation": {"type": "string", "minLength": 1, "maxLength": 300},
        "nextQuestion": {"type": "string", "minLength": 1, "maxLength": 120},
        "nextChoice1": CHOICE_SCHEMA,
        "nextChoice2": CHOICE_SCHEMA,
        "nextChoice3": CHOICE_SCHEMA,
    },
    "required": [
        "result",
        "outcome",
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
    "required": ["result", "outcome"],
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
        choice = _compact_text(value, 72).rstrip(" .!?…")
        key = choice.casefold()
        if not choice or key in seen:
            continue
        seen.add(key)
        result.append(choice)
        if len(result) == 3:
            break
    return result


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
        return _completion_payload(completion)

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
        statImpacts=[],
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
    payload, debug = _request(
        label="interactive_travel/start_simple",
        schema_name="interactive_travel_start_simple_v1",
        schema=START_SCHEMA,
        user_content=(
            "Давай сыграем. Сделай для меня короткое приключение.\n"
            f"Я — {_character_summary(pet)}.\n"
            f"Я отправляюсь {clean_destination}.\n"
            f"Правдивый факт для первой ситуации: {fun_fact}.\n\n"
            "Придумай первое интересное событие, вопрос и три действия. Естественно вплети "
            "смысл факта в событие. Не пиши слова «фанфакт» и не выноси факт отдельно."
        ),
        client=client,
        model=model,
        timeout=timeout,
    )
    scene = _sentence(
        payload.get("situation"),
        fallback="На пути происходит что-то неожиданное",
        limit=300,
    )
    part = InteractiveTravelPart(
        partNumber=1,
        title="Часть 1",
        storyText=scene,
        challenge=_sentence(
            payload.get("question"),
            fallback="Что мне сделать",
            limit=120,
            question=True,
        ),
        actionSuggestions=_choices(
            [payload.get("choice1"), payload.get("choice2"), payload.get("choice3")]
        ),
    )
    travel = InteractiveTravelState(
        travelId=f"interactive-travel-{uuid4().hex}",
        generatedAt=datetime.now(UTC),
        destination=clean_destination,
        overallTitle=_compact_text(payload.get("title"), 120, "Приключение"),
        arcPlan={"generatorVersion": "simple-1", "funFact": fun_fact},
        introReaction=InteractiveTravelIntroReaction(
            text=_intro_text(clean_destination),
            tone="determined",
        ),
        parts=[part],
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
    instruction = (
        "Покажи результат действия и закончи приключение. Не придумывай новую ситуацию."
        if is_final
        else (
            "Покажи результат действия и придумай следующую интересную ситуацию "
            "с тремя действиями."
        )
    )
    schema = FINAL_SCHEMA if is_final else CONTINUE_SCHEMA
    payload, debug = _request(
        label=f"interactive_travel/part_{current_part.partNumber}_simple",
        schema_name=(
            "interactive_travel_final_simple_v1"
            if is_final
            else "interactive_travel_continue_simple_v1"
        ),
        schema=schema,
        user_content=(
            "Продолжи приключение.\n"
            f"Я — {_character_summary(pet)}.\n"
            f"Место: {travel.destination}.\n"
            f"Сейчас: {current_part.storyText}\n"
            f"Я делаю: {clean_advice}.\n\n"
            f"{instruction} Пиши просто. Не добавляй научные факты."
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
                    "elapsedHours": 0,
                    "summary": result.consequence,
                    "departureHook": "Я иду дальше.",
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
