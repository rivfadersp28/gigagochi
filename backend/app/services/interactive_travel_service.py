from __future__ import annotations

import json
import random
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
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
from app.services.interactive_travel_media_service import (
    generate_interactive_travel_part_image,
    generate_interactive_travel_part_video,
)
from app.services.openai_service import chat_reasoning_effort_kwargs, get_chat_model
from app.services.pet_reply_engine.speech_runtime import background_story_reasoning_effort
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response

STORY_PART_COUNT = 4
GENERATOR_VERSION = "erudition-4-v1"
STORY_APPLICATION_RULE = (
    "Верный ответ нужно применить, чтобы преодолеть препятствие и пройти дальше."
)
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
TASK_BANK_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "100_задач_для_путешественника_с_ответами.md"
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

SYSTEM_PROMPT = "\n".join(
    (
        "Создай законченную интерактивную фэнтези-историю из четырёх частей.",
        "В каждой части героя останавливает препятствие или враг, которого можно преодолеть "
        "только знанием из задачи на эрудицию.",
        "Используй физику, окружающий мир, математику или логику не сложнее 8 класса.",
        "У каждой задачи три коротких понятных ответа и ровно один правильный.",
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
        "obstacle": {
            "type": "string",
            "minLength": 1,
            "maxLength": 240,
            "description": (
                "Одно короткое законченное предложение: конкретное препятствие или враг и "
                "почему без правильного ответа герой не может продолжить путь."
            ),
        },
        "question": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        },
        "subject": {
            "type": "string",
            "enum": ["physics", "nature", "math", "logic"],
        },
        "choices": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": CHOICE_SCHEMA,
        },
        "correctChoice": CHOICE_SCHEMA,
        "explanation": {
            "type": "string",
            "minLength": 1,
            "maxLength": 180,
            "description": "Короткое объективное объяснение правильного ответа.",
        },
    },
    "required": [
        "situation",
        "obstacle",
        "question",
        "subject",
        "choices",
        "correctChoice",
        "explanation",
    ],
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
            "maxLength": 260,
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


@lru_cache(maxsize=1)
def _task_bank() -> tuple[dict[str, Any], ...]:
    markdown = TASK_BANK_PATH.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^###\s+(\d+)\.\s+(.+?)\s*$", markdown)
    if len(sections) != 1 + 100 * 3:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_TASK_BANK_INVALID")
    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_questions: set[str] = set()
    for index in range(1, len(sections), 3):
        task_number = int(sections[index])
        title = " ".join(sections[index + 1].split())
        body = sections[index + 2]
        situation_match = re.search(
            r"\*\*Ситуация\.\*\*\s*(.+?)(?=\n\s*\n\*\*Вопрос)",
            body,
            re.DOTALL,
        )
        question_match = re.search(
            r"\*\*Вопрос\.\*\*\s*(.+?)(?=\n\s*\n- )",
            body,
            re.DOTALL,
        )
        options = re.findall(r"(?m)^- ([А-Г])\)\s*(.+?)\s*$", body)
        answer_match = re.search(r"(?m)^\*\*Ответ:\*\*\s*([А-Г])\)", body)
        explanation_match = re.search(
            r"\*\*Почему:\*\*\s*(.+?)(?=\n\s*\n\*\*Источник:)",
            body,
            re.DOTALL,
        )
        source_match = re.search(
            r"\*\*Источник:\*\*\s*(.+?)(?=\n\s*\n|\Z)",
            body,
            re.DOTALL,
        )
        if (
            not all(
                (situation_match, question_match, answer_match, explanation_match, source_match)
            )
            or len(options) != 4
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_TASK_BANK_INVALID")
        task_id = f"traveler-{task_number:03d}"
        situation = " ".join(situation_match.group(1).split())
        question = " ".join(question_match.group(1).split())
        choices_by_letter = {letter: " ".join(choice.split()) for letter, choice in options}
        answer = choices_by_letter.get(answer_match.group(1))
        choices = list(choices_by_letter.values())
        explanation = " ".join(explanation_match.group(1).split())
        source = " ".join(source_match.group(1).split())
        subject = (
            "physics"
            if task_number <= 34
            else "nature"
            if task_number <= 93
            else "logic"
            if task_number in {97, 100}
            else "math"
        )
        if (
            task_number != len(tasks) + 1
            or task_id in seen_ids
            or question in seen_questions
            or not situation
            or not question
            or any(not choice for choice in choices)
            or not answer
            or answer not in choices
            or not explanation
            or not source
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_TASK_BANK_INVALID")
        seen_ids.add(task_id)
        seen_questions.add(question)
        tasks.append(
            {
                "id": task_id,
                "title": title,
                "subject": subject,
                "situation": situation,
                "question": question,
                "choices": choices,
                "answer": answer,
                "explanation": explanation,
                "source": source,
            }
        )
    if len(tasks) < STORY_PART_COUNT:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_TASK_BANK_TOO_SMALL")
    return tuple(tasks)


def _select_story_tasks() -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for task in random.sample(list(_task_bank()), STORY_PART_COUNT):
        answer = task["answer"]
        wrong_choices = [choice for choice in task["choices"] if choice != answer]
        choices = [answer, *random.sample(wrong_choices, 2)]
        random.shuffle(choices)
        selected.append({**task, "choices": choices})
    return selected


def _task_bank_prompt(tasks: list[dict[str, Any]]) -> str:
    blocks = []
    for index, task in enumerate(tasks, start=1):
        blocks.append(
            "\n".join(
                (
                    f"ИСПЫТАНИЕ {index}",
                    f"Область: {task['subject']}",
                    f"Исходная ситуация: {task['situation']}",
                    f"Вопрос: {task['question']}",
                    f"Варианты: {' | '.join(task['choices'])}",
                    f"Правильный ответ: {task['answer']}",
                    f"Пояснение: {task['explanation']}",
                )
            )
        )
    return "\n\n".join(blocks)


def _apply_story_tasks(payload: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    raw_parts = payload.get("parts")
    if not isinstance(raw_parts, list) or len(raw_parts) != STORY_PART_COUNT:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
    for raw_part, task in zip(raw_parts, tasks, strict=True):
        if not isinstance(raw_part, dict):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
        raw_part.update(
            question=task["question"],
            subject=task["subject"],
            choices=task["choices"],
            correctChoice=task["answer"],
            explanation=task["explanation"],
        )
    return payload


def _story_plan_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_parts = payload.get("parts")
    if not isinstance(raw_parts, list) or len(raw_parts) != STORY_PART_COUNT:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
        if any(
            not isinstance(raw_part.get(field), str) or not raw_part[field].strip()
            for field in ("situation", "obstacle", "question", "explanation")
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
        if raw_part.get("subject") not in {"physics", "nature", "math", "logic"}:
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
        choices = raw_part.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 3
            or any(not isinstance(choice, str) or not choice.strip() for choice in choices)
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
        correct_choice = raw_part.get("correctChoice")
        if (
            not isinstance(correct_choice, str)
            or not correct_choice.strip()
            or correct_choice not in choices
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_INCOMPLETE")
    return raw_parts


def _parts_from_plan_payload(payload: dict[str, Any]) -> list[InteractiveTravelPart]:
    return [
        InteractiveTravelPart(
            partNumber=part_number,
            title=f"Часть {part_number}",
            storyText=" ".join(
                (
                    _sentence(
                        raw_part["situation"],
                        fallback="На пути происходит что-то неожиданное",
                        limit=200,
                    ),
                    _sentence(
                        raw_part["obstacle"],
                        fallback="Без правильного ответа путь дальше закрыт",
                        limit=220,
                    ),
                    STORY_APPLICATION_RULE,
                )
            ),
            challenge=_sentence(
                raw_part["question"],
                fallback="Что мне сделать",
                limit=280,
                question=True,
            ),
            actionSuggestions=_choices(raw_part["choices"]),
        )
        for part_number, raw_part in enumerate(_story_plan_items(payload), start=1)
    ]


def _arc_plan_from_payload(
    payload: dict[str, Any], *, parts: list[InteractiveTravelPart], task_ids: list[str]
) -> dict[str, str]:
    raw_parts = _story_plan_items(payload)
    arc_plan = {
        "generatorVersion": GENERATOR_VERSION,
        "taskBankIds": ",".join(task_ids),
        "storyGoal": _sentence(
            payload.get("goal"),
            fallback="Завершить путешествие",
            limit=180,
        ),
        "storyEnding": _sentence(
            payload.get("ending"),
            fallback="Я достигаю цели и могу вернуться домой",
            limit=260,
        ),
    }
    for part, raw_part in zip(parts, raw_parts, strict=True):
        part_number = part.partNumber
        prefix = f"part{part_number}"
        correct_index = raw_part["choices"].index(raw_part["correctChoice"])
        arc_plan[f"{prefix}CorrectChoice"] = part.actionSuggestions[correct_index]
        arc_plan[f"{prefix}Subject"] = raw_part["subject"]
        arc_plan[f"{prefix}Explanation"] = _sentence(
            raw_part["explanation"],
            fallback="Это правильный ответ",
            limit=180,
        )
        if part_number == 1:
            continue
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
    del pet
    return "персонаж пользователя"


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
    travel_id: str | None = None,
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
    story_tasks = _select_story_tasks()
    payload, debug = _request(
        label="interactive_travel/start_erudition_4",
        schema_name="interactive_travel_erudition_story_v1",
        schema=START_SCHEMA,
        user_content=(
            "Создай историю в стиле фэнтези с законченным сюжетом из 4 частей.\n"
            f"Главный герой: {_character_summary(pet)}.\n\n"
            "В каждой части — ситуация или препятствие, которое герой преодолевает выбором "
            "ответа. Каждое испытание содержит задачу на эрудицию: физика, окружающий мир, "
            "математика или логика, уровень сложности — до 8 класса. Ответы должны быть "
            "короткими и понятными, а правильный выбор ведёт к развитию сюжета.\n"
            "Все четыре части составляют один связный сюжет с ясной целью и конкретной "
            "развязкой. Для каждой части верни три разных ответа в choices, ровно один из них "
            "дословно повтори в correctChoice. Не ставь правильный ответ всегда на одну позицию.\n"
            "Используй четыре испытания ниже в указанном порядке. Не заменяй их другими задачами "
            "и не меняй правильные ответы. Для каждого испытания придумай конкретное препятствие "
            "или врага. Ответ должен быть не пропуском за викторину, а полезным знанием: герой "
            "применяет его к действию, механизму, среде или слабости врага и благодаря этому "
            "проходит дальше. В obstacle прямо укажи, что перекрывает путь и почему без решения "
            "задачи его не преодолеть. Придумай только связующие фэнтезийные ситуации.\n\n"
            f"{_task_bank_prompt(story_tasks)}"
        ),
        client=client,
        model=model,
        timeout=timeout,
        required_text_fields=("title", "goal", "ending"),
        require_story_plan=True,
    )
    payload = _apply_story_tasks(payload, story_tasks)
    planned_parts = _parts_from_plan_payload(payload)
    travel = InteractiveTravelState(
        travelId=travel_id or f"interactive-travel-{uuid4().hex}",
        generatedAt=datetime.now(UTC),
        destination=clean_destination,
        overallTitle=_compact_text(payload.get("title"), 120, "Приключение"),
        arcPlan=_arc_plan_from_payload(
            payload,
            parts=planned_parts,
            task_ids=[task["id"] for task in story_tasks],
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
    answer_rule = ""
    answer_is_correct = False
    if uses_fixed_plan:
        story_goal = _required_arc_value(travel.arcPlan, "storyGoal")
        correct_choice = _required_arc_value(
            travel.arcPlan,
            f"part{current_part.partNumber}CorrectChoice",
        )
        correct_explanation = _required_arc_value(
            travel.arcPlan,
            f"part{current_part.partNumber}Explanation",
        )
        answer_is_correct = clean_advice.casefold() == correct_choice.casefold()
        answer_rule = (
            f"Выбранный ответ правильный: {correct_choice}. Объяснение: {correct_explanation} "
            "Покажи, как знание помогает герою."
            if answer_is_correct
            else (
                f"Выбранный ответ неправильный. Правильный ответ: {correct_choice}. "
                f"Объяснение: {correct_explanation} "
                "Коротко покажи локальную неудачу и объясни верный ответ внутри события."
            )
        )
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
            f"Главный герой: {_character_summary(pet)}.\n"
            f"Цель: {travel.arcPlan.get('storyGoal', 'завершить путешествие')}\n"
            f"Сейчас: {current_part.storyText}\n"
            f"Я делаю: {clean_advice}.\n\n"
            f"{answer_rule}\n{instruction}\n"
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
        fallback_outcome=(
            "positive"
            if uses_fixed_plan and answer_is_correct
            else "negative"
            if uses_fixed_plan
            else fallback_outcome
        ),
    )
    if uses_fixed_plan:
        result.outcomeValence = "positive" if answer_is_correct else "negative"
        result.adviceAssessment = "helpful" if answer_is_correct else "harmful"
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
