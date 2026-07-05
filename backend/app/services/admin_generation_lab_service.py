from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.prompts.pet_image_prompts import (
    build_character_bible_prompt,
    build_pet_sprite_sheet_prompt,
)
from app.services.character_cards import normalize_character_profile_v2
from app.services.image_service import create_character_bible, generate_pet_asset_set
from app.services.pet_reply_engine import (
    PetRecentMessage,
    PetReplyInput,
    PetReplyPet,
    PetStats,
    build_default_personality,
    build_visual_identity,
    generate_pet_reply,
)
from app.services.pet_reply_engine.fallbacks import fallback_reply
from app.services.pet_reply_engine.lore import extract_lore
from app.services.pet_reply_engine.prompt_builder import build_pet_reply_messages
from app.services.pet_reply_engine.quality import quality_report_for_reply

SELF_INTRO_QUESTION = "расскажи о себе"
CONVERSATION_BENCHMARK_QUESTIONS = (
    SELF_INTRO_QUESTION,
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


def external_source_trace_prompt_block(character_bible: dict[str, Any] | None) -> str | None:
    if not character_bible:
        return None
    extensions = character_bible.get("extensions")
    if not isinstance(extensions, dict):
        return None
    fragments = extensions.get("external_source_fragments_used")
    if not isinstance(fragments, list):
        return None

    lines: list[str] = []
    for index, fragment in enumerate(fragments, start=1):
        if not isinstance(fragment, dict):
            continue
        text = fragment.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        source_family = fragment.get("source_family")
        source_url = fragment.get("source_url")
        kind = fragment.get("kind")
        locale = fragment.get("locale")
        lines.append(
            "- "
            f"source_{index} "
            f"[{source_family or 'external'}; {source_url or 'unknown'}; "
            f"{kind or 'fragment'}; {locale or 'unknown'}]: {text.strip()}"
        )
    return "\n".join(lines) if lines else None


def build_admin_benchmark_input(
    description: str,
    character_bible: dict[str, Any],
    *,
    question: str,
    recent_messages: tuple[PetRecentMessage, ...] = (),
) -> PetReplyInput:
    return PetReplyInput(
        user_action="chat_message",
        user_text=question,
        pet=PetReplyPet(
            name=None,
            age_stage="teen",
            mood="idle",
            stats=PetStats(
                hunger=80,
                happiness=70,
                energy=60,
                cleanliness=90,
            ),
            visual_identity=build_visual_identity(description, character_bible),
            personality=build_default_personality(description, character_bible),
            lore=extract_lore(character_bible),
            character_profile_v2=normalize_character_profile_v2(
                character_bible,
                raw_description=description,
            ),
        ),
        recent_messages=recent_messages,
    )


def build_admin_self_intro_input(
    description: str,
    character_bible: dict[str, Any],
) -> PetReplyInput:
    return build_admin_benchmark_input(
        description,
        character_bible,
        question=SELF_INTRO_QUESTION,
    )


def _benchmark_turn_payload(
    *,
    question: str,
    reply: str,
    mood_hint: str | None,
    used_fallback: bool,
    validation_flags: tuple[str, ...],
    lore: dict[str, Any] | None,
) -> dict[str, Any]:
    quality = quality_report_for_reply(
        question=question,
        reply=reply,
        lore=lore,
        used_fallback=used_fallback,
        validation_flags=validation_flags,
    )
    return {
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


def generate_admin_self_intro_benchmark(
    description: str,
    character_bible: dict[str, Any],
    include_conversation_benchmark: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    questions = (
        CONVERSATION_BENCHMARK_QUESTIONS
        if include_conversation_benchmark
        else (SELF_INTRO_QUESTION,)
    )
    first_input = build_admin_benchmark_input(
        description,
        character_bible,
        question=SELF_INTRO_QUESTION,
    )
    messages = [
        {"role": str(message["role"]), "content": str(message["content"])}
        for message in build_pet_reply_messages(first_input)
    ]

    recent_messages = ()
    turns: list[dict[str, Any]] = []
    for question in questions:
        reply_input = build_admin_benchmark_input(
            description,
            character_bible,
            question=question,
            recent_messages=recent_messages,
        )
        try:
            result = generate_pet_reply(reply_input)
            turn = _benchmark_turn_payload(
                question=question,
                reply=result.reply,
                mood_hint=result.mood_hint,
                used_fallback=result.used_fallback,
                validation_flags=result.validation_flags,
                lore=reply_input.pet.lore,
            )
        except Exception as exc:
            flags = (f"benchmark_error:{exc.__class__.__name__}",)
            turn = _benchmark_turn_payload(
                question=question,
                reply=fallback_reply(reply_input),
                mood_hint=reply_input.pet.mood,
                used_fallback=True,
                validation_flags=flags,
                lore=reply_input.pet.lore,
            )
        turns.append(turn)
        recent_messages = (
            *recent_messages,
            PetRecentMessage(role="user", text=question),
            PetRecentMessage(role="pet", text=turn["reply"]),
        )[-12:]

    first_turn = dict(turns[0])
    if include_conversation_benchmark:
        first_turn["turns"] = turns
    return first_turn, messages

def build_admin_debug(
    description: str,
    *,
    character_bible: dict[str, Any] | None,
    include_debug_prompts: bool,
    include_image_config: bool,
    benchmark_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    debug: dict[str, Any] = {
        "chatModel": settings.openai_chat_model,
    }
    if include_image_config:
        debug.update(
            {
                "imageModel": settings.openai_image_model,
                "imageSize": settings.openai_image_size,
                "imageQuality": settings.openai_image_quality,
            }
        )
    if include_debug_prompts:
        debug["characterBiblePrompt"] = build_character_bible_prompt(
            description,
            external_source_fragments=external_source_trace_prompt_block(character_bible),
        )
        if include_image_config and character_bible is not None:
            debug["spriteSheetPrompt"] = build_pet_sprite_sheet_prompt(
                description,
                character_bible,
            )
        if benchmark_messages is not None:
            debug["selfIntroBenchmarkMessages"] = benchmark_messages
    return debug


def generate_admin_profile_only(
    description: str,
    include_debug_prompts: bool,
    include_self_intro_benchmark: bool = False,
    include_conversation_benchmark: bool = False,
) -> dict[str, Any]:
    character_bible = create_character_bible(description)
    benchmark = None
    benchmark_messages = None
    if include_self_intro_benchmark:
        benchmark, benchmark_messages = generate_admin_self_intro_benchmark(
            description,
            character_bible,
            include_conversation_benchmark,
        )

    return {
        "generatedAt": datetime.now(UTC),
        "characterBible": character_bible,
        "benchmark": benchmark,
        "debug": build_admin_debug(
            description,
            character_bible=character_bible,
            include_debug_prompts=include_debug_prompts,
            include_image_config=False,
            benchmark_messages=benchmark_messages,
        ),
    }


def generate_admin_full_asset_set(
    description: str,
    include_debug_prompts: bool,
    include_self_intro_benchmark: bool = False,
    include_conversation_benchmark: bool = False,
) -> dict[str, Any]:
    asset_result = generate_pet_asset_set(description)
    character_bible = asset_result["characterBible"]
    benchmark = None
    benchmark_messages = None
    if include_self_intro_benchmark:
        benchmark, benchmark_messages = generate_admin_self_intro_benchmark(
            description,
            character_bible,
            include_conversation_benchmark,
        )

    return {
        **asset_result,
        "benchmark": benchmark,
        "debug": build_admin_debug(
            description,
            character_bible=character_bible,
            include_debug_prompts=include_debug_prompts,
            include_image_config=True,
            benchmark_messages=benchmark_messages,
        ),
    }
