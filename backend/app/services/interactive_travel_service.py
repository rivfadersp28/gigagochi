from __future__ import annotations

import ast
import json
import random
import re
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

MIN_PART_COUNT = 3
NEW_STORY_MAX_PART_COUNT = 6
STATE_MAX_PART_COUNT = 7
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

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁ])|(?<=[.!?][»”])\s+(?=[А-ЯЁ])")

SYSTEM_PROMPT = "\n".join(
    (
        "Ты ведёшь приключение для ребёнка 7–12 лет.",
        "Пиши от первого лица в настоящем времени, без метафор и лишних описаний.",
        "Одна ситуация — максимум два коротких предложения про одно событие.",
        "Без атмосферы: только событие и выбор.",
        "Каждая новая ситуация прямо вытекает из результата и приближает к цели.",
        "Первые две ситуации только ведут к цели; цель решается в финале.",
        "Не дублируй предметы: у каждого предмета одно место и один владелец.",
        "Варианты короткие. Добавь лёгкое фэнтези.",
        "Верни только JSON по схеме.",
    )
)

CHOICE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 72,
    "description": "Одно короткое действие без объяснений.",
}

START_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "goal": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "description": (
                "Одно финальное действие без союза «и» и слов «Я хочу»: получить, вернуть, "
                "открыть или раскрыть; не используй «найти», если предмет можно увидеть раньше."
            ),
        },
        "situation": {
            "type": "string",
            "minLength": 1,
            "maxLength": 220,
            "description": "Первая преграда: цель ещё нельзя выполнить.",
        },
        "question": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "Как пройти первую преграду, не выполняя цель.",
        },
        "choice1": CHOICE_SCHEMA,
        "choice2": CHOICE_SCHEMA,
        "choice3": CHOICE_SCHEMA,
    },
    "required": [
        "title",
        "goal",
        "situation",
        "question",
        "choice1",
        "choice2",
        "choice3",
    ],
}

RESULT_PROPERTIES: dict[str, Any] = {
    "result": {
        "type": "string",
        "minLength": 1,
        "maxLength": 220,
        "description": (
            "Сначала буквальное выбранное действие, затем его прямой результат; "
            "максимум два простых предложения."
        ),
    },
    "outcome": {
        "type": "string",
        "enum": ["positive", "negative"],
        "description": (
            "Итог действия. В финале positive означает, что цель достигнута, negative — что "
            "цель окончательно не достигнута; result обязан совпадать с outcome."
        ),
    },
}

INTERMEDIATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **RESULT_PROPERTIES,
        "bridge": {
            "type": "string",
            "minLength": 1,
            "maxLength": 180,
            "description": (
                "Одно предложение от точного результата к следующему месту. "
                "Перемещается герой, а не важные предметы."
            ),
        },
        "nextSituation": {
            "type": "string",
            "minLength": 1,
            "maxLength": 220,
            "description": "Продолжает bridge с теми же важными предметами.",
        },
        "nextQuestion": {"type": "string", "minLength": 1, "maxLength": 120},
        "nextChoice1": CHOICE_SCHEMA,
        "nextChoice2": CHOICE_SCHEMA,
        "nextChoice3": CHOICE_SCHEMA,
    },
    "required": [
        "result",
        "outcome",
        "bridge",
        "nextSituation",
        "nextQuestion",
        "nextChoice1",
        "nextChoice2",
        "nextChoice3",
    ],
}

FLEXIBLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **RESULT_PROPERTIES,
        "complete": {
            "type": "boolean",
            "description": "true, только если result сам полностью завершает исходную цель",
        },
        "bridge": {"type": "string", "maxLength": 180},
        "nextSituation": {"type": "string", "maxLength": 220},
        "nextQuestion": {"type": "string", "maxLength": 120},
        "nextChoice1": {"type": "string", "maxLength": 72},
        "nextChoice2": {"type": "string", "maxLength": 72},
        "nextChoice3": {"type": "string", "maxLength": 72},
    },
    "required": [
        "result",
        "outcome",
        "complete",
        "bridge",
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
    text = " ".join(str(value or "").split()).strip()
    if not text:
        text = fallback
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—–-")
    return shortened or text[:limit]


def _sentence(
    value: Any,
    *,
    fallback: str,
    limit: int,
    question: bool = False,
    max_sentences: int = 2,
) -> str:
    text = _compact_text(value, limit - 1, fallback).rstrip(" .!?…")
    if not text:
        text = fallback.rstrip(" .!?…")
    pieces = SENTENCE_SPLIT_RE.split(text)
    text = " ".join(pieces[:max_sentences]).rstrip(" .!?…")
    return f"{text}{'?' if question else '.'}"


def _goal_phrase(value: Any) -> str:
    goal = _compact_text(value, 100, "узнать секрет этого места").strip(" .!?…")
    lower = goal.casefold()
    for prefix in ("я хочу ", "хочу ", "моя цель — ", "моя цель - "):
        if lower.startswith(prefix):
            goal = goal[len(prefix) :].strip()
            break
    if not goal:
        goal = "узнать секрет этого места"
    return goal[0].lower() + goal[1:]


def _intro_text(destination: str, goal: str) -> str:
    return _sentence(
        f"Я отправляюсь {destination}. Моя цель — {goal}",
        fallback="Я отправляюсь в приключение",
        limit=220,
    )


CHOICE_SPLIT_RE = re.compile(r"[;\n]+|(?<=[.!?])\s+(?=[А-ЯЁ])|(?<=[.!?][»”])\s+(?=[А-ЯЁ])")


def _choice_chunks(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [chunk for item in value for chunk in _choice_chunks(item)]
    if isinstance(value, dict):
        for key in ("string", "text", "value", "label", "step"):
            if value.get(key):
                return _choice_chunks(value[key])
        return []
    raw_text = str(value or "").strip()
    if not raw_text:
        return []
    if raw_text.startswith("[") and raw_text.endswith("]"):
        try:
            nested = ast.literal_eval(raw_text)
        except (SyntaxError, ValueError):
            nested = None
        if isinstance(nested, (list, tuple)):
            return _choice_chunks(nested)
    return [
        " ".join(chunk.split()).strip()
        for chunk in CHOICE_SPLIT_RE.split(raw_text)
        if chunk.strip()
    ]


def _choices(value: Any) -> list[str]:
    source = value if isinstance(value, list) else [value]
    candidates = [chunk for item in source for chunk in _choice_chunks(item)]
    result: list[str] = []
    seen: set[str] = set()
    for item in [*candidates, *CHOICE_FALLBACKS]:
        choice = " ".join(str(item).split()).strip().rstrip(" .!?…")
        if len(choice) > 72:
            continue
        normalized = choice.casefold()
        if not choice or normalized in seen:
            continue
        seen.add(normalized)
        result.append(choice)
        if len(result) == 3:
            return result
    return list(CHOICE_FALLBACKS)


def _fixed_part_count(arc_plan: dict[str, str]) -> int | None:
    raw = arc_plan.get("partCount") or arc_plan.get("targetPartCount")
    if raw is None:
        return None
    try:
        count = int(raw)
    except (TypeError, ValueError):
        count = 4
    return max(MIN_PART_COUNT, min(STATE_MAX_PART_COUNT, count))


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
    payload: dict[str, Any],
    *,
    advice: str,
    tie_break: str,
    final_goal: str | None = None,
) -> InteractiveTravelResult:
    outcome = payload.get("outcome")
    if outcome not in {"positive", "negative"}:
        outcome = tie_break
    result_text = _sentence(
        payload.get("result"),
        fallback=f"Я делаю так: {_compact_text(advice, 100, 'пробую')}",
        limit=220,
    )
    reaction = _sentence(
        f"Выбираю: {_compact_text(advice, 180, 'попробовать')}",
        fallback="Пробую",
        limit=220,
    )
    final_text = result_text
    if final_goal:
        closure = _sentence(
            f"Мне {'удалось' if outcome == 'positive' else 'не удалось'} {final_goal}",
            fallback="Приключение закончено",
            limit=280,
        )
        final_text = _sentence(
            f"{result_text} {closure}",
            fallback=closure,
            limit=500,
            max_sentences=3,
        )
    return InteractiveTravelResult(
        text=final_text,
        adviceAssessment="helpful" if outcome == "positive" else "harmful",
        reaction=reaction,
        reactionTone="determined" if outcome == "positive" else "worried",
        consequence=result_text,
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
    user_content = (
        "Давай сыграем. Сделай для меня короткое приключение.\n\n"
        f"Я — {_character_summary(pet)}.\n"
        f"Я отправляюсь {clean_destination}.\n\n"
        "Придумай одну ясную цель с одним финальным действием, без слова «и». Для предмета "
        "цель — получить или вернуть его, а не просто увидеть. История длится от 3 до 6 "
        "ситуаций. Первая ситуация — только первая преграда: ни один её вариант не может "
        "выполнить цель. Вопрос и три варианта решают одно событие."
    )
    payload, debug = _request(
        label="interactive_travel/start",
        schema_name="interactive_travel_start_simple",
        schema=START_SCHEMA,
        user_content=user_content,
        client=client,
        model=model,
        timeout=timeout,
    )
    goal = _goal_phrase(payload.get("goal"))
    situation = _sentence(
        payload.get("situation"),
        fallback="Передо мной появляется первая преграда",
        limit=220,
    )
    part = InteractiveTravelPart(
        partNumber=1,
        title="Часть 1",
        storyText=situation,
        challenge=_sentence(
            payload.get("question"),
            fallback="Что мне сделать",
            limit=120,
            question=True,
            max_sentences=1,
        ),
        actionSuggestions=_choices(
            [payload.get("choice1"), payload.get("choice2"), payload.get("choice3")]
        ),
    )
    arc_plan = {"goal": goal}
    travel = InteractiveTravelState(
        travelId=f"interactive-travel-{uuid4().hex}",
        generatedAt=datetime.now(UTC),
        destination=clean_destination,
        overallTitle=_compact_text(payload.get("title"), 120, "Путешествие"),
        arcPlan=arc_plan,
        introReaction=InteractiveTravelIntroReaction(
            text=_intro_text(clean_destination, goal),
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

    fixed_part_count = _fixed_part_count(travel.arcPlan)
    must_finish = current_part.partNumber >= (
        fixed_part_count or NEW_STORY_MAX_PART_COUNT
    )
    may_finish = fixed_part_count is None and current_part.partNumber >= MIN_PART_COUNT
    tie_break = tie_break_valence or random.choice(("positive", "negative"))
    if tie_break not in {"positive", "negative"}:
        raise ValueError("tie_break_valence must be positive or negative")
    model, timeout = _model_and_timeout(client=client, model=model, timeout=timeout)
    clean_advice = _compact_text(advice, 1000, "осмотреться")
    goal = _goal_phrase(travel.arcPlan.get("goal"))
    previous_context = ""
    if len(travel.parts) > 1:
        previous_part = travel.parts[-2]
        if previous_part.result is not None:
            previous_context = f"До этого: {previous_part.result.consequence}\n"
    if must_finish:
        final_rule = (
            f"Это финал. Разреши цель «{goal}» сейчас: при outcome=positive покажи её "
            "достижение, при outcome=negative — окончательную неудачу. result должен совпадать "
            "с outcome. Не показывай путь к финалу и не начинай новую проблему."
        )
    elif may_finish:
        final_rule = (
            f"Если это действие само решает цель «{goal}» успехом или окончательной неудачей, "
            "верни complete=true. При positive цель достигнута, при negative — окончательно нет; "
            "result должен совпадать с outcome. Иначе верни complete=false и дай одну прямо "
            "связанную следующую ситуацию с одним выбором."
        )
    else:
        final_rule = (
            "Это ещё не финал: цель пока не выполнена. После результата дай одну следующую "
            "ситуацию, которая прямо из него вытекает и приближает к цели. Оставь одно событие "
            "для нового выбора. Важные предметы остаются на своих местах."
        )
    progress = (
        f"Сейчас ситуация {current_part.partNumber} из {fixed_part_count}."
        if fixed_part_count is not None
        else (
            f"Сейчас ситуация {current_part.partNumber}. История заканчивается естественно "
            f"между ситуациями {MIN_PART_COUNT} и {NEW_STORY_MAX_PART_COUNT}."
        )
    )
    user_content = (
        "Продолжи приключение.\n\n"
        f"Я — {_character_summary(pet)}.\n"
        f"Моя цель — {goal}.\n"
        f"{progress}\n"
        f"{previous_context}"
        f"Сейчас: {current_part.storyText}\n"
        f"Вопрос: {current_part.challenge}\n"
        f"Я делаю: {clean_advice}.\n\n"
        "Сначала буквально выполни это действие, не заменяй его другим. "
        f"{final_rule} У предмета цели нет второй копии. Пиши в настоящем времени. "
        f"Если исход спорный, выбери {tie_break}."
    )
    if must_finish:
        schema = FINAL_SCHEMA
        schema_name = "interactive_travel_final_simple"
    elif may_finish:
        schema = FLEXIBLE_SCHEMA
        schema_name = "interactive_travel_flexible_simple"
    else:
        schema = INTERMEDIATE_SCHEMA
        schema_name = "interactive_travel_continue_simple"
    payload, debug = _request(
        label=f"interactive_travel/part_{current_part.partNumber}_result_simple",
        schema_name=schema_name,
        schema=schema,
        user_content=user_content,
        client=client,
        model=model,
        timeout=timeout,
    )
    is_final = must_finish or (may_finish and payload.get("complete") is True)
    result = _result_from_payload(
        payload,
        advice=clean_advice,
        tie_break=tie_break,
        final_goal=goal if is_final else None,
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
        bridge = _sentence(
            payload.get("bridge"),
            fallback="После этого я продолжаю путь",
            limit=180,
            max_sentences=1,
        )
        next_part = InteractiveTravelPart(
            partNumber=next_number,
            title=f"Часть {next_number}",
            storyText=_sentence(
                payload.get("nextSituation"),
                fallback="Впереди появляется новая преграда",
                limit=220,
            ),
            transition={
                "elapsedHours": 0,
                "summary": result.consequence,
                "departureHook": bridge,
            },
            challenge=_sentence(
                payload.get("nextQuestion"),
                fallback="Что мне сделать",
                limit=120,
                question=True,
                max_sentences=1,
            ),
            actionSuggestions=_choices(
                [
                    payload.get("nextChoice1"),
                    payload.get("nextChoice2"),
                    payload.get("nextChoice3"),
                ]
            ),
        )
        parts.append(next_part)

    outcome_valence = result.outcomeValence if is_final else None
    next_travel = InteractiveTravelState.model_validate(
        travel.model_dump(mode="json")
        | {
            "parts": [part.model_dump(mode="json") for part in parts],
            "completed": is_final,
            "outcomeValence": outcome_valence,
            "statImpact": None,
        }
    )
    return InteractiveTravelResponse(
        travel=next_travel,
        debug={"promptDebug": debug} if include_debug else None,
    )
