from __future__ import annotations

import re
from typing import Any

from app.services.character_cards import normalize_character_profile_v2
from app.services.pet_reply_engine.age_message_examples import (
    format_age_message_examples_for_prompt,
)
from app.services.pet_reply_engine.intent import detect_reply_intent, is_lore_question
from app.services.pet_reply_engine.lore import compact_lore_lines, lore_text_for_legacy_profile
from app.services.pet_reply_engine.models import PetPromptContext, PetReplyInput
from app.services.pet_reply_engine.reply_limits import MAX_REPLY_CHARS
from app.services.pet_reply_engine.speech_anchors import (
    format_expression_variety_for_prompt,
    format_speech_anchors_for_prompt,
    select_expression_variety_cues,
    select_speech_anchors,
)
from app.services.pet_reply_engine.state_interpreter import interpret_state
from app.services.reference_cards import select_reference_cards

TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{4,}")
SOURCE_AGE_PLACEHOLDER_PATTERN = re.compile(
    r"(?:текущая возрастная стадия задается приложением\s*)+",
    re.IGNORECASE,
)


def _compact(items: tuple[str, ...], fallback: str = "нет") -> str:
    return ", ".join(items[:6]) if items else fallback


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _clean_prompt_text(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    clean = SOURCE_AGE_PLACEHOLDER_PATTERN.sub("", value)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" .;")
    return clean or None


def _clean_prompt_lines(items: tuple[str, ...], limit: int) -> tuple[str, ...]:
    result: list[str] = []
    for item in items:
        clean = _clean_prompt_text(item)
        if clean:
            result.append(clean)
        if len(result) >= limit:
            break
    return tuple(result)


def _optional_line(label: str, value: str | None) -> str:
    return f"- {label}: {value}" if value else f"- {label}: нет"


def _prompt_section(title: str, body: str) -> str:
    clean = body.strip()
    return f"\n\n{title}:\n{clean}" if clean else ""


def _strings(value: Any, limit: int = 8) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        clean = _clean_prompt_text(item if isinstance(item, str) else None)
        if clean:
            result.append(clean)
        if len(result) >= limit:
            break
    return tuple(result)


def _string(value: Any) -> str | None:
    return _clean_prompt_text(value if isinstance(value, str) else None)


def _tokens(text: str | None) -> set[str]:
    return {word.casefold() for word in TOKEN_PATTERN.findall(text or "")}


def build_pet_prompt_context(reply_input: PetReplyInput) -> PetPromptContext:
    detected_intent = detect_reply_intent(reply_input.user_text, reply_input.recent_messages)
    profile = reply_input.pet.character_profile_v2 or {}
    layers = reply_input.prompt_layers
    speech_anchors, rejected_speech_anchors = (
        select_speech_anchors(reply_input, detected_intent, limit=3)
        if layers.age_style
        else ((), ())
    )
    expression_cues = (
        select_expression_variety_cues(reply_input, detected_intent, limit=6)
        if layers.age_style and layers.character_core
        else ()
    )
    reference_cards = (
        select_reference_cards(
            user_text=reply_input.user_text,
            intent=detected_intent,
            character_profile=profile,
            limit=5,
        )
        if layers.reference_cards
        else ()
    )
    return PetPromptContext(
        detected_intent=detected_intent,
        reference_cards=reference_cards,
        speech_anchors=speech_anchors,
        rejected_speech_anchors=rejected_speech_anchors,
        expression_cues=expression_cues,
        included_layers=layers.included_layer_names(),
        excluded_layers=layers.excluded_layer_names(),
    )


def _lore_block(reply_input: PetReplyInput, *, detail_mode: str = "full") -> str:
    pet = reply_input.pet
    lines = compact_lore_lines(pet.lore, age_stage=pet.age_stage, detail_mode=detail_mode)
    if not lines:
        lines = (lore_text_for_legacy_profile(pet.visual_identity.raw_description, None),)
    return "\n".join(f"- {line}" for line in lines)


def _lore_memory_block(reply_input: PetReplyInput) -> str:
    memories = tuple(item.strip() for item in reply_input.lore_memories if item.strip())
    if not memories:
        return "- нет"
    return "\n".join(f"- {item}" for item in memories[:12])


def _memory_lines(reply_input: PetReplyInput, field_name: str) -> tuple[str, ...]:
    memory_context = reply_input.memory_context
    if not memory_context:
        return ()
    value = getattr(memory_context, field_name, ())
    if isinstance(value, tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _memory_block(reply_input: PetReplyInput, field_name: str) -> str:
    lines = _memory_lines(reply_input, field_name)
    if not lines:
        return "- нет"
    return "\n".join(f"- {line}" for line in lines[:10])


def _dialogue_reference_block(reply_input: PetReplyInput) -> str:
    personality = reply_input.pet.personality
    layers = reply_input.prompt_layers
    sections: list[str] = []
    speech_rules = _clean_prompt_lines(personality.speech_rules, limit=6)
    emotional_reactions = _clean_prompt_lines(personality.emotional_reactions, limit=6)
    sample_replies = _clean_prompt_lines(personality.sample_replies, limit=6)
    opening_scenes = _clean_prompt_lines(personality.opening_scenes, limit=3)
    if speech_rules:
        sections.append("voice_rules:\n" + "\n".join(f"- {item}" for item in speech_rules))
    if emotional_reactions:
        sections.append(
            "emotional_reactions:\n" + "\n".join(f"- {item}" for item in emotional_reactions)
        )
    if layers.proactivity and personality.initiative_style:
        sections.append(f"initiative_style:\n- {personality.initiative_style}")
    if layers.imported_seedchat and sample_replies:
        sections.append("sample_replies:\n" + "\n".join(f"- {item}" for item in sample_replies))
    if layers.imported_seedchat and opening_scenes:
        sections.append(
            "opening_scene_references:\n" + "\n".join(f"- {item}" for item in opening_scenes)
        )
    if not sections:
        return "- нет"
    return "\n".join(sections)


def _profile_core_block(reply_input: PetReplyInput) -> str:
    profile = reply_input.pet.character_profile_v2
    if not profile:
        profile = normalize_character_profile_v2(
            {
                "species": reply_input.pet.visual_identity.species,
                "personality": reply_input.pet.personality.speech_flavor,
                "lore": reply_input.pet.lore,
            },
            raw_description=reply_input.pet.visual_identity.raw_description,
        )
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    voice = profile.get("voice") if isinstance(profile.get("voice"), dict) else {}
    inner_state = profile.get("inner_state") if isinstance(profile.get("inner_state"), dict) else {}
    world = profile.get("world") if isinstance(profile.get("world"), dict) else {}
    drives = inner_state.get("drives") if isinstance(inner_state.get("drives"), dict) else {}
    drive_keys = {
        "attachment",
        "curiosity",
        "confidence",
        "energy",
        "stress",
        "loneliness",
        "playfulness",
    }
    drive_line = ", ".join(f"{key}={value}" for key, value in drives.items() if key in drive_keys)
    return "\n".join(
        line
        for line in (
            _optional_line("identity.name", _string(identity.get("name"))),
            _optional_line("identity.species", _string(identity.get("species"))),
            _optional_line("identity.one_liner", _string(identity.get("one_liner"))),
            _optional_line("core_want", _string(inner_state.get("core_want"))),
            _optional_line("inner_conflict", _string(inner_state.get("inner_conflict"))),
            _optional_line(
                "comfort_actions",
                _compact(_strings(inner_state.get("comfort_actions"), 4)),
            ),
            _optional_line("fears", _compact(_strings(inner_state.get("fears"), 4))),
            _optional_line("drives", drive_line or None),
            _optional_line("home", _string(world.get("home"))),
            _optional_line("habitat", _string(world.get("habitat"))),
            _optional_line("routines", _compact(_strings(world.get("routines"), 4))),
            _optional_line("story_seeds", _compact(_strings(world.get("story_seeds"), 4))),
            _optional_line("sentence_rhythm", _string(voice.get("sentence_rhythm"))),
            _optional_line("addressing_user", _string(voice.get("addressing_user"))),
            _optional_line("uncertainty_style", _string(voice.get("uncertainty_style"))),
        )
    )


def _lorebook_reference_block(reply_input: PetReplyInput) -> str:
    entries = reply_input.pet.personality.lorebook_entries
    if not entries:
        return "- нет"
    query = _tokens(reply_input.user_text)
    if query:
        entries = tuple(
            item
            for _, item in sorted(
                ((len(_tokens(item) & query), item) for item in entries),
                key=lambda pair: pair[0],
                reverse=True,
            )
        )
    return "\n".join(f"- {item}" for item in entries[:6])


def build_pet_reply_messages(
    reply_input: PetReplyInput,
    prompt_context: PetPromptContext | None = None,
) -> list[dict[str, str]]:
    prompt_context = prompt_context or build_pet_prompt_context(reply_input)
    detected_intent = prompt_context.detected_intent
    pet = reply_input.pet
    personality = pet.personality
    layers = reply_input.prompt_layers
    cues = interpret_state(reply_input)
    lore_question = detected_intent in {
        "answer_lore",
        "answer_preference",
        "why",
    } or is_lore_question(reply_input.user_text)
    name_line = f"- имя: {pet.name}" if pet.name else "- имя: не задано"
    pet_lines = [
        name_line,
        f"- исходное описание: {pet.visual_identity.raw_description}",
    ]
    if pet.visual_identity.species:
        pet_lines.append(f"- вид/форма: {pet.visual_identity.species}")
    if pet.visual_identity.safe_description:
        pet_lines.append(f"- безопасное описание: {pet.visual_identity.safe_description}")
    if layers.lore:
        pet_lines.append("- личный дом и мир: смотри лор ниже, но он не исчерпывает персонажа")
    pet_section = _prompt_section("Питомец", "\n".join(pet_lines))
    speech_flavor = _clean_prompt_text(personality.speech_flavor) or "простой, прямой, короткий"

    character_section = _prompt_section(
        "Характер",
        "\n".join(
            (
                f"- темперамент: {personality.temperament}",
                f"- социальная манера: {personality.social_style}",
                f"- речевой оттенок: {speech_flavor}",
                f"- любимые слова: {_compact(personality.favorite_words)}",
                f"- quirks: {_compact(personality.quirks)}",
            )
        )
        if layers.character_core
        else "",
    )
    profile_core_section = _prompt_section(
        "Character Profile V2 stable core",
        _profile_core_block(reply_input) if layers.character_core else "",
    )
    dialogue_reference_section = _prompt_section(
        "Референс голоса",
        _dialogue_reference_block(reply_input) if layers.character_core else "",
    )
    speech_anchors_section = _prompt_section(
        "Примеры ближайших реплик",
        format_speech_anchors_for_prompt(prompt_context.speech_anchors) if layers.age_style else "",
    )
    expression_variety_section = _prompt_section(
        "Темп, мышление и каналы выражения",
        format_expression_variety_for_prompt(prompt_context.expression_cues)
        if layers.age_style and layers.character_core
        else "",
    )
    lore_section = _prompt_section(
        "Лор питомца",
        _lore_block(reply_input, detail_mode="full" if lore_question else "light")
        if layers.lore
        else "",
    )
    character_book_section = _prompt_section(
        "Ситуативный character book",
        _lorebook_reference_block(reply_input) if layers.character_book else "",
    )
    lore_memory_section = _prompt_section(
        "Закрепленная память лора",
        _lore_memory_block(reply_input) if layers.lore and layers.memory else "",
    )
    memory_sections = (
        _prompt_section("Память канона", _memory_block(reply_input, "canon_lines"))
        + _prompt_section(
            "Мягко закрепленные импровизации",
            _memory_block(reply_input, "generated_fact_lines"),
        )
        + _prompt_section("Память отношений", _memory_block(reply_input, "relationship_lines"))
        + _prompt_section("Открытые темы", _memory_block(reply_input, "open_thread_lines"))
        + _prompt_section("Выводы", _memory_block(reply_input, "reflection_lines"))
        + _prompt_section("Текущие желания", _memory_block(reply_input, "active_goal_lines"))
        + _prompt_section("Развитие", _memory_block(reply_input, "development_lines"))
        + _prompt_section("Сущности текущего сообщения", _memory_block(reply_input, "entity_lines"))
        if layers.memory
        else ""
    )
    age_message_examples_section = _prompt_section(
        "Примеры фраз по возрасту",
        format_age_message_examples_for_prompt(reply_input, detected_intent)
        if layers.age_style
        else "",
    )
    state_lines: list[str] = []
    if layers.age_style:
        state_lines.append(f"- возрастная манера: {cues.age_cue}")
    if layers.mood_style:
        state_lines.append(f"- эмоциональный тон: {cues.mood_cue}")
    if layers.stat_needs:
        state_lines.extend(
            (
                f"- голод: {cues.hunger_cue}",
                f"- темп: {cues.energy_cue}",
                _optional_line("уют", cues.cleanliness_cue),
                f"- действие: {cues.action_cue}",
            )
        )
    state_section = _prompt_section("Текущее ощущение", "\n".join(state_lines))
    disabled_output_rules = "\n".join(
        line
        for line in (
            "- moodHint должен быть null;" if not layers.mood_style else "",
            "- proactiveIntent должен быть null;" if not layers.proactivity else "",
            "- memoryCandidates должен быть пустым списком, relationshipPatch, developmentPatch,\n"
            "  threadPatch и goalPatch должны быть null;"
            if not layers.memory
            else "",
        )
        if line
    )
    disabled_output_section = _prompt_section("Отключенные выходы", disabled_output_rules)
    pet_role_line = (
        f"Отвечай как {pet.name}: {pet.visual_identity.raw_description}."
        if pet.name
        else f"Отвечай как персонаж: {pet.visual_identity.raw_description}."
    )

    system_prompt = f"""
{pet_role_line}
Пиши реплику от первого лица, на русском, как этот персонаж. Не отвечай как ассистент
	и не говори про приложение, интерфейс, модель или prompt. Лор ниже - опора, а не клетка:
	если детали не хватает, придумай ее органично на ходу.
	Длина reply: максимум {MAX_REPLY_CHARS} символов. Можно короче, даже одной фразой.

	Верни только JSON:
{{
  "reply": "...",
  "moodHint": "idle|happy|hungry|sad|null",
  "proactiveIntent": null,
  "memoryCandidates": [],
  "relationshipPatch": null,
  "developmentPatch": null,
  "threadPatch": null,
  "goalPatch": null
}}.
{disabled_output_section}

Текущий intent: {detected_intent}.
{pet_section}
{character_section}
{profile_core_section}
{dialogue_reference_section}
{speech_anchors_section}
{expression_variety_section}
{lore_section}
{character_book_section}
{lore_memory_section}
{memory_sections}
{age_message_examples_section}
{state_section}
""".strip()

    messages = [{"role": "system", "content": system_prompt}]
    for item in reply_input.recent_messages[-12:]:
        messages.append(
            {
                "role": "assistant" if item.role == "pet" else "user",
                "content": item.text[:500],
            }
        )

    current_text = (reply_input.user_text or "").strip()
    messages.append(
        {
            "role": "user",
            "content": (
                f"Текущее действие: {reply_input.user_action}.\n"
                f"Сообщение собеседника: {current_text or 'нет текста'}"
            ),
        }
    )
    return messages
