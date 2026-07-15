from __future__ import annotations

import random
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.schemas import (
    InteractiveTravelAnimationResponse,
    InteractiveTravelIllustrationResponse,
    InteractiveTravelIntroReaction,
    InteractiveTravelPart,
    InteractiveTravelPlan,
    InteractiveTravelResponse,
    InteractiveTravelResult,
    InteractiveTravelState,
    InteractiveTravelSuggestionsResponse,
    InteractiveTravelTaskPlan,
    LocalPetChatContext,
)
from app.services.interactive_travel_media_service import (
    generate_interactive_travel_part_image,
    generate_interactive_travel_part_video,
)
from app.services.task_bank_mode import TaskBankMode, read_task_bank_mode

STORY_PART_COUNT = 4
GENERATOR_VERSION = "task-bank-location-v4"
TASK_BANK_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "задачи_путешественника_без_расчётов.md"
)
EASY_TASK_BANK_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "задачи_путешественника_до_6_класса.md"
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

SUCCESS_REACTIONS = (
    "Я кое-чему научился... Кажется, я стал немного умнее.",
    "Вот это сработало! Запомню этот приём.",
    "Ура, мы справились! Я многому научился.",
    "Хороший выбор. Теперь я знаю чуть больше.",
)
FAILURE_REACTIONS = (
    "Ой... В следующий раз буду осторожнее.",
    "Кажется, это была не лучшая идея.",
    "Не получилось... Но теперь я знаю, как не надо.",
    "Вот же незадача. Попробуем иначе в следующий раз.",
)
MOOD_CONTEXT_WORDS = (
    "друг", "довер", "обид", "груст", "страш", "стыд", "спор", "ссор",
    "одинок", "настроен", "чувств", "разочар", "вежлив", "поддерж",
)


def _negative_travel_stat(task: InteractiveTravelTaskPlan) -> str:
    context = " ".join(
        (
            task.situation,
            task.question,
            task.explanation or "",
            *task.choiceOutcomes,
        )
    ).casefold()
    return "happiness" if any(word in context for word in MOOD_CONTEXT_WORDS) else "energy"


def scheduled_interactive_episode_result(
    *,
    situation: str,
    question: str,
    outcomes: list[str],
    correct_choice: str,
    selected_choice: str,
) -> InteractiveTravelResult:
    if not selected_choice:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_ANSWER_INVALID")
    is_correct = selected_choice == correct_choice
    context = " ".join((situation, question, *outcomes)).casefold()
    affected_stat = (
        "happiness" if any(word in context for word in MOOD_CONTEXT_WORDS) else "energy"
    )
    stat_label = "настроение" if affected_stat == "happiness" else "здоровье"
    return InteractiveTravelResult(
        text=outcomes[0] if outcomes else "История завершилась.",
        adviceAssessment="helpful" if is_correct else "harmful",
        reaction=random.choice(SUCCESS_REACTIONS if is_correct else FAILURE_REACTIONS),
        reactionTone="determined" if is_correct else "worried",
        consequence=(
            "Выбран правильный ответ."
            if is_correct
            else f"Этот вариант не подходит; правильный ответ — {correct_choice}."
        ),
        outcomeValence="positive" if is_correct else "negative",
        experienceGained=random.randint(100, 150) if is_correct else 0,
        statImpacts=[] if is_correct else [
            {
                "stat": affected_stat,
                "amount": -random.randint(8, 15),
                "reason": f"Неудачный выбор уменьшил {stat_label}.",
            }
        ],
    )


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


def _task_bank_path(mode: TaskBankMode) -> Path:
    return EASY_TASK_BANK_PATH if mode == "easy" else TASK_BANK_PATH


@lru_cache(maxsize=2)
def _task_bank_from_path(path_value: str) -> tuple[dict[str, Any], ...]:
    markdown = Path(path_value).read_text(encoding="utf-8")
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
            r"\*\*Ситуация\.\*\*\s*(.+?)(?=\n(?:\s*\n)?\*\*Вопрос)",
            body,
            re.DOTALL,
        )
        question_match = re.search(
            r"\*\*Вопрос\.\*\*\s*(.+?)(?=\n(?:\s*\n)?- )",
            body,
            re.DOTALL,
        )
        options = re.findall(r"(?m)^- ([А-Г])\)\s*(.+?)\s*$", body)
        answer_match = re.search(
            r"(?m)^\*\*Ответ:\*\*\s*([А-Г])\)\s*(.+?)\s*$",
            body,
        )
        outcomes = re.findall(r"(?m)^- \*\*([А-Г])\)\*\*\s*(.+?)\s*$", body)
        explanation_match = re.search(
            r"\*\*Почему[.:]\*\*\s*(.+?)(?=\n(?:\s*\n)?\*\*Источник:)",
            body,
            re.DOTALL,
        )
        source_match = re.search(
            r"\*\*Источник:\*\*\s*(.+?)(?=\n\s*\n|\Z)",
            body,
            re.DOTALL,
        )
        if (
            not all((situation_match, question_match, answer_match, source_match))
            or len(options) != 4
            or len(outcomes) != 4
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_TASK_BANK_INVALID")
        task_id = f"traveler-{task_number:03d}"
        situation = " ".join(situation_match.group(1).split())
        question = " ".join(question_match.group(1).split())
        choices_by_letter = {letter: " ".join(choice.split()) for letter, choice in options}
        outcomes_by_letter = {letter: " ".join(outcome.split()) for letter, outcome in outcomes}
        answer = choices_by_letter.get(answer_match.group(1))
        stated_answer = " ".join(answer_match.group(2).split())
        choices = list(choices_by_letter.values())
        choice_outcomes = list(outcomes_by_letter.values())
        explanation = (
            " ".join(explanation_match.group(1).split()) if explanation_match else None
        )
        source = " ".join(source_match.group(1).split())
        if (
            task_number != len(tasks) + 1
            or task_id in seen_ids
            or question in seen_questions
            or list(choices_by_letter) != ["А", "Б", "В", "Г"]
            or list(outcomes_by_letter) != ["А", "Б", "В", "Г"]
            or not situation
            or not question
            or any(not choice for choice in choices)
            or not answer
            or answer not in choices
            or stated_answer != answer
            or any(not outcome for outcome in choice_outcomes)
            or not source
        ):
            raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_TASK_BANK_INVALID")
        seen_ids.add(task_id)
        seen_questions.add(question)
        tasks.append(
            {
                "id": task_id,
                "title": title,
                "situation": situation,
                "question": question,
                "choices": choices,
                "answer": answer,
                "outcomes": choice_outcomes,
                "explanation": explanation,
                "source": source,
            }
        )
    if len(tasks) < STORY_PART_COUNT:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_TASK_BANK_TOO_SMALL")
    return tuple(tasks)


def _task_bank(mode: TaskBankMode | None = None) -> tuple[dict[str, Any], ...]:
    selected_mode = mode or read_task_bank_mode()
    return _task_bank_from_path(str(_task_bank_path(selected_mode)))


_task_bank.cache_clear = _task_bank_from_path.cache_clear  # type: ignore[attr-defined]


def _select_story_tasks() -> list[dict[str, Any]]:
    return list(random.sample(list(_task_bank()), STORY_PART_COUNT))


def _task_plan(task: dict[str, Any], lead_in: str) -> InteractiveTravelTaskPlan:
    return InteractiveTravelTaskPlan(
        taskId=task["id"], leadIn=lead_in, situation=task["situation"],
        question=task["question"], choices=list(task["choices"]),
        correctChoice=task["answer"], explanation=task["explanation"],
        choiceOutcomes=list(task["outcomes"]),
    )


def generate_scheduled_interactive_episode_plan() -> dict[str, Any]:
    destination = random.choice(DESTINATION_FALLBACKS)
    task = random.choice(_task_bank())
    return {
        "destination": destination,
        "title": task["title"],
        "storyText": task["situation"],
        "question": task["question"],
        "choices": list(task["choices"]),
        "outcomes": list(task["outcomes"]),
        "correctChoice": task["answer"],
    }


def scheduled_interactive_episode_correct_choice(
    *, question: str, choices: list[str]
) -> str:
    normalized_question = _answer_key(question)
    active_mode = read_task_bank_mode()
    fallback_mode: TaskBankMode = "hard" if active_mode == "easy" else "easy"
    for mode in (active_mode, fallback_mode):
        for task in _task_bank(mode):
            if _answer_key(task["question"]) != normalized_question:
                continue
            answer = str(task["answer"])
            if answer in choices:
                return answer
    raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_TASK_NOT_FOUND")


def _location_lead_ins(destination: str) -> list[str]:
    location = _compact_text(destination, 140, "выбранное место")
    ordinals = ("первая", "вторая", "третья", "четвёртая")
    return [
        _sentence(
            f"По пути {location} меня ждёт {ordinal} встреча",
            fallback="На пути меня ждёт новая встреча",
            limit=200,
        )
        for ordinal in ordinals
    ]


def _part_from_plan_task(
    *,
    task: InteractiveTravelTaskPlan,
    part_number: int,
    previous_result: InteractiveTravelResult | None = None,
) -> InteractiveTravelPart:
    transition = None
    if previous_result is not None:
        transition = {
            "elapsedHours": 1,
            "summary": previous_result.consequence,
            "departureHook": "Чуть позже в этой же локации происходит новая встреча.",
        }
    return InteractiveTravelPart(
        partNumber=part_number,
        title=f"Эпизод {part_number}",
        storyText=f"{task.leadIn} {task.situation}",
        transition=transition,
        challenge=task.question,
        actionSuggestions=list(task.choices),
    )


def _intro_text(destination: str) -> str:
    return _sentence(
        f"Я отправляюсь {destination}. Интересно, что встречу по пути",
        fallback="Я отправляюсь в путешествие",
        limit=220,
    )


def prepare_interactive_travel_start(
    *,
    destination: str,
    travel_id: str | None = None,
) -> InteractiveTravelResponse:
    """Build the immediately visible shell while the story plan is generated."""

    clean_destination = _compact_text(destination, 500, "в путешествие")
    travel = InteractiveTravelState(
        travelId=travel_id or f"interactive-travel-{uuid4().hex}",
        generatedAt=datetime.now(UTC),
        destination=clean_destination,
        overallTitle="Путешествие готовится",
        plan=None,
        introReaction=InteractiveTravelIntroReaction(
            text=_intro_text(clean_destination),
            tone="determined",
        ),
        generationStatus="generating",
        parts=[
            InteractiveTravelPart(
                partNumber=1,
                title="Начало пути",
                storyText="Я собираюсь в путь.",
                challenge="Путешествие готовится.",
                actionSuggestions=[],
            )
        ],
        completed=False,
    )
    return InteractiveTravelResponse(travel=travel)


def _answer_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def _resolve_selected_choice(task: InteractiveTravelTaskPlan, advice: str) -> str:
    requested = _answer_key(advice)
    for choice in task.choices:
        if _answer_key(choice) == requested:
            return choice
    raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_ANSWER_INVALID")


def _deterministic_result(
    task: InteractiveTravelTaskPlan,
    *,
    selected_choice: str,
) -> InteractiveTravelResult:
    is_correct = selected_choice == task.correctChoice
    selected_index = task.choices.index(selected_choice)
    if len(task.choiceOutcomes) == len(task.choices):
        result_text = task.choiceOutcomes[selected_index]
    else:
        explanation = task.explanation or "Правильный вариант указан в банке задач."
        result_text = (
            f"Верно. Правильный ответ: {task.correctChoice}. {explanation}"
            if is_correct
            else (
                f"Выбрано: {selected_choice}. Правильный ответ: {task.correctChoice}. "
                f"{explanation}"
            )
        )
    consequence = (
        "Выбран правильный ответ."
        if is_correct
        else f"Этот вариант не подходит; правильный ответ — {task.correctChoice}."
    )
    affected_stat = _negative_travel_stat(task)
    stat_label = "настроение" if affected_stat == "happiness" else "здоровье"
    return InteractiveTravelResult(
        text=result_text,
        adviceAssessment="helpful" if is_correct else "harmful",
        reaction=random.choice(SUCCESS_REACTIONS if is_correct else FAILURE_REACTIONS),
        reactionTone="determined" if is_correct else "worried",
        consequence=consequence,
        outcomeValence="positive" if is_correct else "negative",
        experienceGained=random.randint(100, 150) if is_correct else 0,
        statImpacts=[] if is_correct else [
            {
                "stat": affected_stat,
                "amount": -random.randint(8, 15),
                "reason": f"Неудачный выбор уменьшил {stat_label}.",
            }
        ],
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
    destination: str,
    travel_id: str | None = None,
    include_debug: bool = False,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> InteractiveTravelResponse:
    del client, model, timeout
    clean_destination = _compact_text(destination, 500, "в путешествие")
    story_tasks = _select_story_tasks()
    lead_ins = _location_lead_ins(clean_destination)
    plan = InteractiveTravelPlan(
        version=GENERATOR_VERSION,
        tasks=[
            _task_plan(task, lead_in)
            for task, lead_in in zip(story_tasks, lead_ins, strict=True)
        ],
    )
    travel = InteractiveTravelState(
        travelId=travel_id or f"interactive-travel-{uuid4().hex}",
        generatedAt=datetime.now(UTC),
        destination=clean_destination,
        overallTitle=_compact_text(
            f"Путешествие {clean_destination}",
            120,
            "Путешествие",
        ),
        plan=plan,
        introReaction=InteractiveTravelIntroReaction(
            text=_intro_text(clean_destination),
            tone="determined",
        ),
        generationStatus="ready",
        parts=[_part_from_plan_task(task=plan.tasks[0], part_number=1)],
        completed=False,
    )
    return InteractiveTravelResponse(
        travel=travel,
        debug={"promptDebug": []} if include_debug else None,
    )


def continue_interactive_travel(
    *,
    travel: InteractiveTravelState,
    advice: str,
    include_debug: bool = False,
) -> InteractiveTravelResponse:
    if travel.completed:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_ALREADY_COMPLETED")
    if travel.generationStatus != "ready" or travel.plan is None:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_MISSING")
    current_part = travel.parts[-1]
    if current_part.result is not None:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PENDING_PART_MISSING")
    part_index = current_part.partNumber - 1
    if part_index < 0 or part_index >= len(travel.plan.tasks):
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_MISSING")
    task = travel.plan.tasks[part_index]
    if current_part.challenge != task.question or current_part.actionSuggestions != task.choices:
        raise InteractiveTravelGenerationError("INTERACTIVE_TRAVEL_PLAN_MISMATCH")
    selected_choice = _resolve_selected_choice(task, advice)
    result = _deterministic_result(task, selected_choice=selected_choice)
    is_final = current_part.partNumber == STORY_PART_COUNT
    resolved_part = InteractiveTravelPart.model_validate(
        current_part.model_dump(mode="json")
        | {
            "answer": selected_choice,
            "result": result.model_dump(mode="json"),
        }
    )
    parts = [*travel.parts[:-1], resolved_part]
    if not is_final:
        next_number = current_part.partNumber + 1
        parts.append(
            _part_from_plan_task(
                task=travel.plan.tasks[next_number - 1],
                part_number=next_number,
                previous_result=result,
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
        debug={"promptDebug": []} if include_debug else None,
    )
