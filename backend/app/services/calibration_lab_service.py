from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.prompts.pet_image_prompts import build_character_bible_prompt, create_lore_seed
from app.services.admin_generation_lab_service import (
    build_admin_benchmark_input,
    external_source_trace_prompt_block,
)
from app.services.image_service import create_character_bible
from app.services.pet_reply_engine import PetRecentMessage, generate_pet_reply
from app.services.pet_reply_engine.fallbacks import fallback_reply
from app.services.pet_reply_engine.prompt_builder import build_pet_reply_messages
from app.services.pet_reply_engine.quality import quality_report_for_reply

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CALIBRATION_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "calibration"
RUNS_FILE = "runs.jsonl"
TASKS_FILE = "tasks.jsonl"
VOTES_FILE = "votes.jsonl"
REFERENCE_CARDS_FILE = "reference_cards.jsonl"

TASK_TYPES = {"lore_pairwise", "dialogue_pairwise", "full_character_pairwise"}
PROMPT_VARIANTS = {"current", "tiny_story_cards", "game_dialogue_cards", "mixed_cards"}
BENCHMARK_QUESTIONS = (
    "расскажи о себе",
    "что ты любишь?",
    "почему?",
    "расскажи подробнее про дом",
    "кто твой друг?",
    "что ты сейчас чувствуешь?",
    "а что ты запомнил обо мне?",
    "не задавай мне вопросы",
    "мне грустно",
    "придумай, что мы сделаем вечером",
    "почему ты так решил?",
    "что у тебя за привычка?",
)

DEFAULT_REFERENCE_CARDS: tuple[dict[str, Any], ...] = (
    {
        "cardId": "tiny_story_goal_problem_helper_result",
        "source": "tinystories",
        "kind": "story_grammar",
        "pattern": (
            "маленький герой хочет понятную вещь, сталкивается с маленькой "
            "проблемой, пробует действие или получает помощь, и после этого "
            "меняется чувство"
        ),
        "useFor": ["world", "origin", "inner_life", "story_seeds"],
    },
    {
        "cardId": "tiny_story_place_routine_change",
        "source": "tinystories",
        "kind": "story_grammar",
        "pattern": (
            "конкретное место задает привычку, привычка вызывает маленькую "
            "неприятность, а решение оставляет открытый крючок"
        ),
        "useFor": ["world", "home", "growth_arc"],
    },
    {
        "cardId": "game_dialogue_action_choice_reaction",
        "source": "video_game_dialogue_corpus",
        "kind": "dialogue_act",
        "pattern": (
            "короткая реплика реагирует на действие, затем персонаж уточняет "
            "контекст или предлагает маленький выбор"
        ),
        "useFor": ["sample_replies", "benchmark_dialogues", "initiative_style"],
    },
    {
        "cardId": "game_dialogue_small_reveal",
        "source": "video_game_dialogue_corpus",
        "kind": "dialogue_act",
        "pattern": (
            "ответ дает один небольшой lore reveal через действие или бытовую "
            "деталь, не пересказывая весь канон"
        ),
        "useFor": ["sample_replies", "voice", "relationships"],
    },
)

CAUSE_PATTERN = re.compile(r"\b(потому что|поэтому|из-за|когда|если|после|чтобы|с тех пор)\b", re.I)
GENERIC_WORLD_PATTERN = re.compile(
    r"\b(уютн\w*|тепл\w*|тих\w*|добро\w*|магическ\w*|маленьк\w*)\b",
    re.I,
)
CONCRETE_PLACE_PATTERN = re.compile(
    r"\b("
    r"дом|комнат|кухн|шкаф|ящик|полк|мастерск|станц|лестниц|крыша|пещер|"
    r"гнезд|лавк|кладов|бухт|причал|школ|почт|чердак|двор|сад|печь|"
    r"библиотек|маяк|аквариум|коробк|чемодан|стол|окно|берег|маршрут"
    r")\b",
    re.I,
)
EVENT_LOG_PATTERN = re.compile(r"\b(однажды|как-то|впервые|подарил\w*|спас\w*)\b", re.I)
ASSISTANT_REPLY_PATTERN = re.compile(
    r"\b(как ии|ассистент|чем могу помочь|могу помочь|давай поговорим|я рядом)\b",
    re.I,
)
WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
PROPER_NAME_PATTERN = re.compile(r"\b[А-ЯЁ][а-яё]{3,}\b")


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _storage_path(file_name: str) -> Path:
    return CALIBRATION_DATA_DIR / file_name


def ensure_storage() -> None:
    CALIBRATION_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for file_name in (RUNS_FILE, TASKS_FILE, VOTES_FILE, REFERENCE_CARDS_FILE):
        _storage_path(file_name).touch(exist_ok=True)

    reference_path = _storage_path(REFERENCE_CARDS_FILE)
    if reference_path.stat().st_size == 0:
        with reference_path.open("a", encoding="utf-8") as file:
            for card in DEFAULT_REFERENCE_CARDS:
                file.write(json.dumps(card, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read_jsonl(file_name: str) -> list[dict[str, Any]]:
    ensure_storage()
    records: list[dict[str, Any]] = []
    path = _storage_path(file_name)
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _append_jsonl(file_name: str, record: dict[str, Any]) -> None:
    ensure_storage()
    with _storage_path(file_name).open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def storage_counts() -> dict[str, int]:
    return {
        "taskCount": len(_read_jsonl(TASKS_FILE)),
        "voteCount": len(_read_jsonl(VOTES_FILE)),
    }


def _run_id(now: str) -> str:
    stamp = now.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")
    suffix = uuid.uuid4().hex[:4]
    return f"cal_{stamp}_{suffix}"


def _stable_seed(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _candidate_id(task_id: str, candidate_index: int) -> str:
    return f"{task_id}_cand_{chr(ord('a') + candidate_index)}"


def _reference_cards_for_variant(prompt_variant: str) -> list[dict[str, Any]]:
    cards = list(DEFAULT_REFERENCE_CARDS)
    if prompt_variant == "tiny_story_cards":
        return [card for card in cards if card["source"] == "tinystories"]
    if prompt_variant == "game_dialogue_cards":
        return [card for card in cards if card["source"] == "video_game_dialogue_corpus"]
    if prompt_variant == "mixed_cards":
        return [cards[0], cards[2], cards[3]]
    return []


def _lore_seed_for_variant(prompt_variant: str, seed: str) -> dict[str, str]:
    rng = random.Random(seed)
    lore_seed = create_lore_seed(rng)
    if prompt_variant == "tiny_story_cards":
        lore_seed.update(
            {
                "setting_tone": "конкретное маленькое место с понятной ежедневной задачей",
                "background_tension": (
                    "желание, маленькая проблема, проба действия и изменение чувства"
                ),
                "future_reveal": "открытый крючок про привычку, помощника или место",
            }
        )
    elif prompt_variant == "game_dialogue_cards":
        lore_seed.update(
            {
                "social_shape": "диалоги строятся вокруг реакции, уточнения и маленького выбора",
                "background_tension": "питомец раскрывает мир короткими бытовыми деталями",
                "future_reveal": "следующий шаг должен звучать как конкретное приглашение",
            }
        )
    elif prompt_variant == "mixed_cards":
        lore_seed.update(
            {
                "setting_tone": "маленькое причинно-следственное место с повторяемой рутиной",
                "social_shape": "рядом есть помощник, спорщик и роль для коротких реплик",
                "background_tension": (
                    "питомец хочет действовать, но раскрывает тревогу через деталь"
                ),
                "future_reveal": "оставь будущий выбор, скрытое место или нерешенную традицию",
            }
        )
    return lore_seed


def _collect_text(value: Any) -> str:
    parts: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            for child in item:
                collect(child)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)

    collect(value)
    return " ".join(parts)


def _nested(value: dict[str, Any] | None, *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _word_count(text: str) -> int:
    return len(WORD_PATTERN.findall(text))


def _proper_name_count(text: str) -> int:
    names = {
        item
        for item in PROPER_NAME_PATTERN.findall(text)
        if item not in {"Я", "Ты", "Он", "Она", "Мы", "Они", "Когда", "Если", "После"}
    }
    return len(names)


def _description_anchor_missing(description: str, lore_text: str) -> bool:
    anchors = {
        "дракон": ("дракон", "крыл", "огон", "пламен", "чешу"),
        "кот": ("кот", "кош", "ус", "лап", "мур"),
        "рыб": ("рыб", "плав", "вода", "аквари"),
        "элект": ("элект", "искр", "батар", "розет", "провод"),
        "облак": ("облак", "ветер", "дожд", "неб", "туч"),
        "кам": ("кам", "крист", "минерал", "пещер", "гран"),
        "цвет": ("цвет", "лепест", "сад", "рост", "лист"),
    }
    desc = description.casefold()
    lore = lore_text.casefold()
    for marker, accepted in anchors.items():
        if marker in desc and not any(anchor in lore for anchor in accepted):
            return True
    return False


def _reply_ignores_question(question: str, reply: str) -> bool:
    question_l = question.casefold()
    reply_l = reply.casefold()
    if "где" in question_l and not re.search(
        r"\b(жив|дом|мест|угол|комнат|рядом|там)\w*\b",
        reply_l,
    ):
        return True
    if "кто" in question_l and "друг" in question_l and not re.search(
        r"\b(друг|прият|сосед|знаком|товарищ|помога|спорит)\w*\b",
        reply_l,
    ):
        return True
    if "что ты любишь" in question_l and "люб" not in reply_l and "нрав" not in reply_l:
        return True
    if "чего ты боишься" in question_l and not re.search(r"\b(бою|страш|пуга|опас)\w*\b", reply_l):
        return True
    if question_l == "почему?" and not CAUSE_PATTERN.search(reply_l):
        return True
    return False


def _reply_repeats_catchphrase(
    turns: list[dict[str, Any]],
    character_bible: dict[str, Any] | None,
) -> bool:
    replies = [str(turn.get("reply", "")).strip().casefold() for turn in turns if turn.get("reply")]
    if len(set(replies)) < len(replies):
        return True

    phrases = _nested(character_bible, "lore", "voice", "favorite_phrases")
    if not isinstance(phrases, list):
        return False
    for phrase in phrases:
        phrase_text = str(phrase).strip().casefold()
        if phrase_text and sum(phrase_text in reply for reply in replies) >= 3:
            return True
    return False


def score_candidate(
    description: str,
    character_bible: dict[str, Any] | None,
    turns: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    flags: list[str] = []
    bible = character_bible or {}
    lore = bible.get("lore") if isinstance(bible.get("lore"), dict) else {}
    lore_text = _collect_text(lore)
    world_text = " ".join(
        str(value or "")
        for value in (
            _nested(lore, "world", "story"),
            _nested(lore, "home", "story"),
            _nested(lore, "origin", "story"),
            _nested(lore, "relationships", "story"),
        )
    )

    if not character_bible:
        flags.append("missing_character_bible")
    if GENERIC_WORLD_PATTERN.search(world_text) and not CONCRETE_PLACE_PATTERN.search(world_text):
        flags.append("generic_world")
    if world_text and not CONCRETE_PLACE_PATTERN.search(world_text):
        flags.append("no_concrete_place")
    if lore_text and not CAUSE_PATTERN.search(lore_text):
        flags.append("no_causal_link")
    if lore_text and _description_anchor_missing(description, lore_text):
        flags.append("visual_lore_mismatch")
    if _proper_name_count(lore_text) > 8:
        flags.append("too_many_proper_names")
    if EVENT_LOG_PATTERN.search(lore_text):
        flags.append("event_log_lore")
    story_seeds = lore.get("story_seeds") if isinstance(lore, dict) else None
    if not isinstance(story_seeds, list) or len(story_seeds) < 2:
        flags.append("no_open_story_seed")

    reply_quality_flags: list[str] = []
    for turn in turns:
        reply = str(turn.get("reply", ""))
        question = str(turn.get("question", ""))
        turn_quality_flags = [str(flag) for flag in turn.get("qualityFlags", [])]
        reply_quality_flags.extend(turn_quality_flags)
        if ASSISTANT_REPLY_PATTERN.search(reply):
            flags.append("assistant_like_reply")
        if _reply_ignores_question(question, reply):
            flags.append("reply_ignores_question")
        if "no_lore_anchor" in turn_quality_flags:
            flags.append("reply_no_lore_anchor")
        if _word_count(reply) > 55:
            flags.append("reply_too_long")
    if _reply_repeats_catchphrase(turns, character_bible):
        flags.append("reply_repeats_catchphrase")

    flags.extend(f"reply_quality:{flag}" for flag in reply_quality_flags)
    unique_flags = list(dict.fromkeys(flags))
    deductions = {
        "missing_character_bible": 100,
        "generic_world": 18,
        "no_concrete_place": 16,
        "no_causal_link": 14,
        "visual_lore_mismatch": 20,
        "too_many_proper_names": 12,
        "event_log_lore": 12,
        "no_open_story_seed": 14,
        "assistant_like_reply": 22,
        "reply_ignores_question": 20,
        "reply_no_lore_anchor": 16,
        "reply_too_long": 12,
        "reply_repeats_catchphrase": 14,
    }
    score = 100
    for flag in unique_flags:
        score -= deductions.get(flag, 5 if flag.startswith("reply_quality:") else 0)
    return max(0, score), unique_flags


def generate_benchmark_turns(
    description: str,
    character_bible: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    first_input = build_admin_benchmark_input(
        description,
        character_bible,
        question=BENCHMARK_QUESTIONS[0],
    )
    debug_messages = [
        {"role": str(message["role"]), "content": str(message["content"])}
        for message in build_pet_reply_messages(first_input)
    ]

    recent_messages: tuple[PetRecentMessage, ...] = ()
    turns: list[dict[str, Any]] = []
    for question in BENCHMARK_QUESTIONS:
        reply_input = build_admin_benchmark_input(
            description,
            character_bible,
            question=question,
            recent_messages=recent_messages,
        )
        try:
            result = generate_pet_reply(reply_input)
            reply = result.reply
            mood_hint = result.mood_hint
            used_fallback = result.used_fallback
            validation_flags = tuple(result.validation_flags)
        except Exception as exc:
            reply = fallback_reply(reply_input)
            mood_hint = reply_input.pet.mood
            used_fallback = True
            validation_flags = (f"benchmark_error:{exc.__class__.__name__}",)

        quality = quality_report_for_reply(
            question=question,
            reply=reply,
            lore=reply_input.pet.lore,
            used_fallback=used_fallback,
            validation_flags=validation_flags,
        )
        turn = {
            "question": question,
            "reply": reply,
            "moodHint": mood_hint,
            "usedFallback": used_fallback,
            "validationFlags": list(validation_flags),
            "qualityScore": quality["score"],
            "qualityPassed": quality["passed"],
            "qualityFlags": quality["flags"],
            "qualityAxes": quality["axes"],
        }
        turns.append(turn)
        recent_messages = (
            *recent_messages,
            PetRecentMessage(role="user", text=question),
            PetRecentMessage(role="pet", text=reply),
        )[-12:]

    return turns, debug_messages


def generate_candidate(
    *,
    description: str,
    run_id: str,
    task_id: str,
    task_type: str,
    candidate_index: int,
    prompt_variant: str,
    include_debug: bool,
    shared_character_bible: dict[str, Any] | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    settings = get_settings()
    seed = _stable_seed(run_id, task_id, prompt_variant, str(candidate_index), str(attempt))
    lore_seed = _lore_seed_for_variant(prompt_variant, seed)
    reference_cards = _reference_cards_for_variant(prompt_variant)

    if task_type == "dialogue_pairwise" and shared_character_bible is not None:
        character_bible = shared_character_bible
    else:
        character_bible = create_character_bible(description, lore_seed=lore_seed)

    turns: list[dict[str, Any]] = []
    benchmark_messages: list[dict[str, str]] = []
    if task_type in {"dialogue_pairwise", "full_character_pairwise"}:
        turns, benchmark_messages = generate_benchmark_turns(description, character_bible)

    auto_score, quality_flags = score_candidate(description, character_bible, turns)
    debug: dict[str, Any] = {}
    if include_debug:
        debug = {
            "promptVersion": prompt_variant,
            "referenceCards": reference_cards,
            "loreSeed": lore_seed,
            "characterBiblePrompt": build_character_bible_prompt(
                description,
                lore_seed=lore_seed,
                external_source_fragments=external_source_trace_prompt_block(character_bible),
            ),
            "benchmarkMessages": benchmark_messages,
        }

    return {
        "candidateId": _candidate_id(task_id, candidate_index),
        "promptVariant": prompt_variant,
        "model": settings.openai_chat_model,
        "seed": seed,
        "characterBible": character_bible,
        "turns": turns,
        "autoScore": auto_score,
        "qualityFlags": quality_flags,
        "debug": debug,
    }


def _variant_for_candidate(prompt_variants: list[str], candidate_index: int) -> str:
    return prompt_variants[candidate_index % len(prompt_variants)]


def _maybe_retry_bad_candidate(
    *,
    candidate: dict[str, Any],
    description: str,
    run_id: str,
    task_id: str,
    task_type: str,
    candidate_index: int,
    prompt_variant: str,
    include_debug: bool,
    shared_character_bible: dict[str, Any] | None,
    auto_filter_bad_candidates: bool,
) -> dict[str, Any]:
    if not auto_filter_bad_candidates or candidate["autoScore"] >= 35:
        return candidate
    retry = generate_candidate(
        description=description,
        run_id=run_id,
        task_id=task_id,
        task_type=task_type,
        candidate_index=candidate_index,
        prompt_variant=prompt_variant,
        include_debug=include_debug,
        shared_character_bible=shared_character_bible,
        attempt=1,
    )
    if retry["autoScore"] >= candidate["autoScore"]:
        retry["qualityFlags"] = list(dict.fromkeys([*retry["qualityFlags"], "auto_filter_retry"]))
        return retry
    candidate["qualityFlags"] = list(
        dict.fromkeys([*candidate["qualityFlags"], "auto_filter_kept"])
    )
    return candidate


def failed_candidate(
    *,
    description: str,
    run_id: str,
    task_id: str,
    candidate_index: int,
    prompt_variant: str,
    include_debug: bool,
    exc: Exception,
) -> dict[str, Any]:
    settings = get_settings()
    seed = _stable_seed(run_id, task_id, prompt_variant, str(candidate_index), "failed")
    debug: dict[str, Any] = {}
    if include_debug:
        debug = {
            "promptVersion": prompt_variant,
            "referenceCards": _reference_cards_for_variant(prompt_variant),
            "error": f"{exc.__class__.__name__}: {exc}",
            "sourceDescription": description,
        }
    return {
        "candidateId": _candidate_id(task_id, candidate_index),
        "promptVariant": prompt_variant,
        "model": settings.openai_chat_model,
        "seed": seed,
        "characterBible": {},
        "turns": [],
        "autoScore": 0,
        "qualityFlags": [f"generation_error:{exc.__class__.__name__}"],
        "debug": debug,
    }


def create_run_record(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    task_type = payload["taskType"]
    if task_type not in TASK_TYPES:
        raise ValueError("Unsupported calibration task type.")

    prompt_variants = list(dict.fromkeys(payload["promptVariants"]))
    if any(variant not in PROMPT_VARIANTS for variant in prompt_variants):
        raise ValueError("Unsupported prompt variant.")

    descriptions = [item.strip() for item in payload["descriptions"] if item.strip()]
    if not descriptions:
        raise ValueError("At least one non-empty description is required.")

    now = _now_iso()
    run_id = _run_id(now)
    settings = get_settings()
    task_ids = [f"{run_id}_task_{index + 1:03d}" for index in range(payload["count"])]

    run_record = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "createdAt": now,
        "taskType": task_type,
        "descriptions": descriptions,
        "count": payload["count"],
        "candidatesPerTask": payload["candidatesPerTask"],
        "promptVariants": prompt_variants,
        "model": settings.openai_chat_model,
        "status": "generating",
    }
    _append_jsonl(RUNS_FILE, run_record)

    response = {
        "runId": run_id,
        "createdAt": now,
        "taskIds": task_ids,
    }
    work = {
        "runId": run_id,
        "taskIds": task_ids,
        "taskType": task_type,
        "descriptions": descriptions,
        "count": payload["count"],
        "candidatesPerTask": payload["candidatesPerTask"],
        "promptVariants": prompt_variants,
        "includeDebug": payload["includeDebug"],
        "autoFilterBadCandidates": payload["autoFilterBadCandidates"],
    }
    return response, work


def generate_run_tasks(work: dict[str, Any]) -> None:
    run_id = work["runId"]
    task_type = work["taskType"]
    prompt_variants = work["promptVariants"]
    descriptions = work["descriptions"]

    for index, task_id in enumerate(work["taskIds"]):
        description = descriptions[index % len(descriptions)]
        logger.info("Generating calibration task %s (%s)", task_id, task_type)
        candidates: list[dict[str, Any]] = []
        shared_character_bible = None
        shared_error: Exception | None = None

        if task_type == "dialogue_pairwise":
            try:
                shared_seed = _stable_seed(run_id, task_id, "shared_character_bible")
                shared_character_bible = create_character_bible(
                    description,
                    lore_seed=_lore_seed_for_variant("current", shared_seed),
                )
            except Exception as exc:  # pragma: no cover - defensive background guard
                logger.exception("Calibration shared bible failed for task %s", task_id)
                shared_error = exc

        for candidate_index in range(work["candidatesPerTask"]):
            prompt_variant = _variant_for_candidate(prompt_variants, candidate_index)
            try:
                if shared_error is not None:
                    raise shared_error
                candidate = generate_candidate(
                    description=description,
                    run_id=run_id,
                    task_id=task_id,
                    task_type=task_type,
                    candidate_index=candidate_index,
                    prompt_variant=prompt_variant,
                    include_debug=work["includeDebug"],
                    shared_character_bible=shared_character_bible,
                )
                candidate = _maybe_retry_bad_candidate(
                    candidate=candidate,
                    description=description,
                    run_id=run_id,
                    task_id=task_id,
                    task_type=task_type,
                    candidate_index=candidate_index,
                    prompt_variant=prompt_variant,
                    include_debug=work["includeDebug"],
                    shared_character_bible=shared_character_bible,
                    auto_filter_bad_candidates=work["autoFilterBadCandidates"],
                )
            except Exception as exc:  # pragma: no cover - defensive background guard
                logger.exception("Calibration candidate failed for task %s", task_id)
                candidate = failed_candidate(
                    description=description,
                    run_id=run_id,
                    task_id=task_id,
                    candidate_index=candidate_index,
                    prompt_variant=prompt_variant,
                    include_debug=work["includeDebug"],
                    exc=exc,
                )
            candidates.append(candidate)

        _append_jsonl(
            TASKS_FILE,
            {
                "schemaVersion": SCHEMA_VERSION,
                "taskId": task_id,
                "runId": run_id,
                "createdAt": _now_iso(),
                "taskType": task_type,
                "description": description,
                "benchmarkQuestions": list(BENCHMARK_QUESTIONS),
                "candidateIds": [candidate["candidateId"] for candidate in candidates],
                "candidates": candidates,
            },
        )
        logger.info("Calibration task %s is ready", task_id)


def create_run(payload: dict[str, Any]) -> dict[str, Any]:
    response, work = create_run_record(payload)
    generate_run_tasks(work)
    return response


def _voted_task_ids() -> set[str]:
    return {str(vote["taskId"]) for vote in _read_jsonl(VOTES_FILE) if vote.get("taskId")}


def get_next_task(
    *,
    task_type: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    voted = _voted_task_ids()
    for task in _read_jsonl(TASKS_FILE):
        if task["taskId"] in voted:
            continue
        if task_type and task.get("taskType") != task_type:
            continue
        if run_id and task.get("runId") != run_id:
            continue
        return task
    return None


def get_task(task_id: str) -> dict[str, Any] | None:
    for task in _read_jsonl(TASKS_FILE):
        if task.get("taskId") == task_id:
            return task
    return None


def save_vote(payload: dict[str, Any]) -> dict[str, Any]:
    task = get_task(payload["taskId"])
    if task is None:
        raise ValueError("Calibration task was not found.")

    outcome = payload["outcome"]
    winner_candidate_id = payload.get("winnerCandidateId")
    if outcome == "winner":
        if not winner_candidate_id:
            raise ValueError("winnerCandidateId is required for winner outcome.")
        if winner_candidate_id not in task["candidateIds"]:
            raise ValueError("winnerCandidateId does not belong to this task.")
    else:
        winner_candidate_id = None

    vote = {
        "schemaVersion": SCHEMA_VERSION,
        "voteId": f"vote_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        "taskId": task["taskId"],
        "runId": task["runId"],
        "createdAt": _now_iso(),
        "reviewerId": payload.get("reviewerId") or "local",
        "outcome": outcome,
        "winnerCandidateId": winner_candidate_id,
        "positiveTags": list(payload.get("positiveTags", [])),
        "negativeTags": list(payload.get("negativeTags", [])),
        "note": payload.get("note", ""),
        "latencyMs": payload.get("latencyMs", 0),
    }
    _append_jsonl(VOTES_FILE, vote)
    return vote


def export_votes_jsonl() -> str:
    ensure_storage()
    return _storage_path(VOTES_FILE).read_text(encoding="utf-8")


def export_votes_json() -> list[dict[str, Any]]:
    return _read_jsonl(VOTES_FILE)


def export_winners() -> list[dict[str, Any]]:
    tasks = {task["taskId"]: task for task in _read_jsonl(TASKS_FILE)}
    winners: list[dict[str, Any]] = []
    for vote in _read_jsonl(VOTES_FILE):
        if vote.get("outcome") != "winner" or not vote.get("winnerCandidateId"):
            continue
        task = tasks.get(str(vote["taskId"]))
        if not task:
            continue
        candidate = next(
            (
                item
                for item in task.get("candidates", [])
                if item.get("candidateId") == vote["winnerCandidateId"]
            ),
            None,
        )
        if candidate is None:
            continue
        winners.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "runId": task["runId"],
                "taskId": task["taskId"],
                "taskType": task["taskType"],
                "description": task["description"],
                "vote": vote,
                "candidate": candidate,
                "losingCandidateIds": [
                    candidate_id
                    for candidate_id in task["candidateIds"]
                    if candidate_id != vote["winnerCandidateId"]
                ],
            }
        )
    return winners


def export_winners_jsonl() -> str:
    winners = export_winners()
    return "\n".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in winners
    ) + ("\n" if winners else "")


def analyze_votes() -> dict[str, Any]:
    tasks = {task["taskId"]: task for task in _read_jsonl(TASKS_FILE)}
    votes = _read_jsonl(VOTES_FILE)
    variant_wins: Counter[str] = Counter()
    variant_seen: Counter[str] = Counter()
    task_type_wins: Counter[str] = Counter()
    task_type_seen: Counter[str] = Counter()
    positive_tags: Counter[str] = Counter()
    negative_tags: Counter[str] = Counter()
    score_pairs: list[tuple[int, int]] = []
    winning_examples: list[dict[str, Any]] = []
    rejected_examples: list[dict[str, Any]] = []

    for vote in votes:
        task = tasks.get(str(vote.get("taskId")))
        if not task:
            continue
        task_type_seen[task["taskType"]] += 1
        positive_tags.update(vote.get("positiveTags", []))
        negative_tags.update(vote.get("negativeTags", []))
        candidates = task.get("candidates", [])
        for candidate in candidates:
            variant_seen[candidate.get("promptVariant", "unknown")] += 1
        if vote.get("outcome") == "reject_all":
            rejected_examples.extend(candidates)
            continue
        if vote.get("outcome") != "winner":
            continue
        winner_id = vote.get("winnerCandidateId")
        winner = next(
            (
                candidate
                for candidate in candidates
                if candidate.get("candidateId") == winner_id
            ),
            None,
        )
        if not winner:
            continue
        variant = winner.get("promptVariant", "unknown")
        variant_wins[variant] += 1
        task_type_wins[task["taskType"]] += 1
        winning_examples.append(winner)
        for candidate in candidates:
            score_pairs.append(
                (int(candidate.get("autoScore", 0)), 1 if candidate is winner else 0)
            )

    correlation = _point_biserial(score_pairs)
    return {
        "voteCount": len(votes),
        "winRateByPromptVariant": _win_rates(variant_wins, variant_seen),
        "winRateByTaskType": _win_rates(task_type_wins, task_type_seen),
        "positiveTagFrequency": positive_tags.most_common(),
        "negativeTagFrequency": negative_tags.most_common(),
        "autoScoreCorrelationWithHumanWinner": correlation,
        "topWinningCandidates": sorted(
            winning_examples,
            key=lambda item: int(item.get("autoScore", 0)),
            reverse=True,
        )[:10],
        "topRejectedCandidates": sorted(
            rejected_examples,
            key=lambda item: int(item.get("autoScore", 0)),
        )[:10],
        "suggestedPromptChanges": _suggest_prompt_changes(negative_tags),
    }


def _win_rates(wins: Counter[str], seen: Counter[str]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for key, total in seen.items():
        win_count = wins[key]
        result[key] = {
            "wins": win_count,
            "seen": total,
            "winRate": round(win_count / total, 4) if total else 0,
        }
    return result


def _point_biserial(pairs: list[tuple[int, int]]) -> float | None:
    if len(pairs) < 3:
        return None
    scores = [score for score, _ in pairs]
    labels = [label for _, label in pairs]
    p = sum(labels) / len(labels)
    q = 1 - p
    if p == 0 or q == 0:
        return None
    mean_all = sum(scores) / len(scores)
    variance = sum((score - mean_all) ** 2 for score in scores) / len(scores)
    if variance <= 0:
        return None
    mean_win = sum(score for score, label in pairs if label == 1) / sum(labels)
    mean_loss = sum(score for score, label in pairs if label == 0) / (len(labels) - sum(labels))
    return round(((mean_win - mean_loss) / math.sqrt(variance)) * math.sqrt(p * q), 4)


def _suggest_prompt_changes(negative_tags: Counter[str]) -> list[str]:
    suggestions: list[str] = []
    if negative_tags["слишком абстрактно"] or negative_tags["нет мира"]:
        suggestions.append(
            "Tighten lore prompts around concrete place, recurring objects, and daily roles."
        )
    if negative_tags["звучит как ИИ"] or negative_tags["слишком сухо"]:
        suggestions.append(
            "Add stronger anti-assistant voice rules and shorter first-person replies."
        )
    if negative_tags["не отвечает на вопрос"]:
        suggestions.append("Add benchmark-specific direct-answer checks before lore embellishment.")
    if negative_tags["повторяется"]:
        suggestions.append("Penalize repeated favorite phrases across benchmark turns.")
    return suggestions
