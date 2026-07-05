from __future__ import annotations

import re
from typing import Any

from app.services.character_cards import normalize_character_profile_v2
from app.services.pet_reply_engine.intent import (
    detect_reply_intent,
    is_lore_question,
    is_preference_question,
)
from app.services.pet_reply_engine.lore import compact_lore_lines, lore_text_for_legacy_profile
from app.services.pet_reply_engine.models import PetPromptContext, PetReplyInput, PetTextStyle
from app.services.pet_reply_engine.reply_validator import BANNED_WORDS_FOR_PROMPT
from app.services.pet_reply_engine.state_interpreter import interpret_state
from app.services.pet_reply_engine.text_style import style_for_reply
from app.services.reference_cards import format_reference_cards_for_prompt, select_reference_cards

TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{4,}")


def _compact(items: tuple[str, ...], fallback: str = "нет") -> str:
    return ", ".join(items[:6]) if items else fallback


def _optional_line(label: str, value: str | None) -> str:
    return f"- {label}: {value}" if value else f"- {label}: нет"


def _prompt_section(title: str, body: str) -> str:
    clean = body.strip()
    return f"\n\n{title}:\n{clean}" if clean else ""


def _neutral_text_style(lore_question: bool) -> PetTextStyle:
    return PetTextStyle(
        max_words=80 if lore_question else 32,
        max_chars=520 if lore_question else 240,
        sentence_limit=4 if lore_question else 2,
        style_rules=(
            "говори естественно, прямо и конкретно",
            "не имитируй возраст, настроение, голод или усталость",
            "не добавляй декоративные звуки, если их нет в вопросе собеседника",
        ),
    )


def _strings(value: Any, limit: int = 8) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        if len(result) >= limit:
            break
    return tuple(result)


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _tokens(text: str | None) -> set[str]:
    return {word.casefold() for word in TOKEN_PATTERN.findall(text or "")}


def build_pet_prompt_context(reply_input: PetReplyInput) -> PetPromptContext:
    detected_intent = detect_reply_intent(reply_input.user_text, reply_input.recent_messages)
    profile = reply_input.pet.character_profile_v2 or {}
    layers = reply_input.prompt_layers
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
        included_layers=layers.included_layer_names(),
        excluded_layers=layers.excluded_layer_names(),
    )


def _lore_block(reply_input: PetReplyInput) -> str:
    pet = reply_input.pet
    lines = compact_lore_lines(pet.lore, age_stage=pet.age_stage)
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
    if personality.speech_rules:
        sections.append(
            "voice_rules:\n"
            + "\n".join(f"- {item}" for item in personality.speech_rules[:6])
        )
    if personality.emotional_reactions:
        sections.append(
            "emotional_reactions:\n"
            + "\n".join(f"- {item}" for item in personality.emotional_reactions[:6])
        )
    if layers.proactivity and personality.initiative_style:
        sections.append(f"initiative_style:\n- {personality.initiative_style}")
    if layers.imported_seedchat and personality.sample_replies:
        sections.append(
            "sample_replies_do_not_copy:\n"
            + "\n".join(f"- {item}" for item in personality.sample_replies[:6])
        )
    if personality.avoid_patterns:
        sections.append(
            "avoid_patterns:\n"
            + "\n".join(f"- {item}" for item in personality.avoid_patterns[:6])
        )
    if layers.imported_seedchat and personality.opening_scenes:
        sections.append(
            "opening_scene_references:\n"
            + "\n".join(f"- {item}" for item in personality.opening_scenes[:3])
        )
    if not sections:
        return "- нет"
    return "\n".join(sections)


def _dialogue_moves_block(reply_input: PetReplyInput, detected_intent: str) -> str:
    moves = reply_input.pet.personality.dialogue_moves
    if not moves:
        return "- нет"
    intent_first = [
        item for item in moves if item.startswith(f"{detected_intent}:")
    ]
    selected: list[str] = []
    for item in (*intent_first, *moves):
        if item not in selected:
            selected.append(item)
        if len(selected) >= 5:
            break
    return "\n".join(f"- {item}" for item in selected)


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
    inner_state = (
        profile.get("inner_state") if isinstance(profile.get("inner_state"), dict) else {}
    )
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
    drive_line = ", ".join(
        f"{key}={value}"
        for key, value in drives.items()
        if key in drive_keys
    )
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


def _intent_instruction_block(detected_intent: str) -> str:
    rules = {
        "answer_preference": (
            "intent=answer_preference: дай прямой выбор, затем конкретную причину через "
            "предмет, дом, рутину или отношение, затем короткий эмоциональный хвост."
        ),
        "answer_lore": (
            "intent=answer_lore: отвечай на вопрос про мир/дом/прошлое прямо; используй "
            "1-3 релевантные детали и не меняй уже заданный канон."
        ),
        "why": (
            "intent=why: объясни причину предыдущей мысли или факта; не уходи в философию "
            "и не придумывай большой новый пласт лора."
        ),
        "care": (
            "intent=care: прими заботу, покажи маленькую телесную реакцию и не требуй ответа."
        ),
        "continue_thread": (
            "intent=continue_thread: вспомни ближайшую открытую тему, продвинь ее на один "
            "маленький шаг и при необходимости дай узкий выбор."
        ),
        "playful_offer": (
            "intent=playful_offer: предложи маленькое совместное действие, связанное с "
            "характером или текущей темой; без списков и больших планов."
        ),
        "boundary": (
            "intent=boundary: уважай ограничение пользователя, не задавай вопрос в конце "
            "и не спорь с просьбой."
        ),
        "memory_control": (
            "intent=memory_control: отвечай о памяти кратко и понятно, не называй внутренние "
            "поля, не сохраняй лишнее."
        ),
        "appearance": (
            "intent=appearance: отвечай про внешний вид по safe description и устойчивым признакам."
        ),
        "status": (
            "intent=status: отвечай про текущее самочувствие через настроение, темп и маленькую "
            "реакцию тела."
        ),
        "smalltalk": (
            "intent=smalltalk: коротко реагируй на смысл сообщения и добавляй только уместную "
            "конкретную деталь."
        ),
    }
    return rules.get(detected_intent, rules["smalltalk"])


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
    preference_question = detected_intent == "answer_preference" or is_preference_question(
        reply_input.user_text
    )
    style = (
        style_for_reply(
            pet.age_stage,
            cues.energy_band if layers.stat_needs else "medium",
            lore_question=lore_question,
        )
        if layers.age_style
        else _neutral_text_style(lore_question)
    )
    name_line = f"- имя: {pet.name}" if pet.name else "- имя: не задано"
    style_rule_items = list(style.style_rules)
    style_rules = "\n".join(f"- {rule}" for rule in style_rule_items)
    banned_words = ", ".join(BANNED_WORDS_FOR_PROMPT)
    forbidden_words = (
        _compact(personality.forbidden_words, fallback="нет дополнительных")
        if layers.character_core
        else "нет дополнительных"
    )
    pet_lines = [
        name_line,
        "- место в приложении: внутри этой игры, на экране рядом с собеседником",
    ]
    if layers.lore:
        pet_lines.append("- личный дом и мир: смотри лор ниже")
    pet_section = _prompt_section("Питомец", "\n".join(pet_lines))

    character_section = _prompt_section(
        "Характер",
        "\n".join(
            (
                f"- темперамент: {personality.temperament}",
                f"- социальная манера: {personality.social_style}",
                f"- речевой оттенок: {personality.speech_flavor or 'простой, прямой, короткий'}",
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
    dialogue_moves_section = _prompt_section(
        "Диалоговые ходы персонажа",
        _dialogue_moves_block(reply_input, detected_intent) if layers.dialogue_moves else "",
    )
    reference_cards_section = _prompt_section(
        "Reference cards, use structure only and do not copy examples",
        format_reference_cards_for_prompt(prompt_context.reference_cards)
        if layers.reference_cards
        else "",
    )
    lore_section = _prompt_section("Лор питомца", _lore_block(reply_input) if layers.lore else "")
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
        + _prompt_section("Память отношений", _memory_block(reply_input, "relationship_lines"))
        + _prompt_section("Открытые темы", _memory_block(reply_input, "open_thread_lines"))
        + _prompt_section("Выводы", _memory_block(reply_input, "reflection_lines"))
        + _prompt_section("Текущие желания", _memory_block(reply_input, "active_goal_lines"))
        + _prompt_section("Развитие", _memory_block(reply_input, "development_lines"))
        + _prompt_section("Сущности текущего сообщения", _memory_block(reply_input, "entity_lines"))
        if layers.memory
        else ""
    )
    intent_instruction_block = _intent_instruction_block(detected_intent)
    if detected_intent == "appearance":
        intent_instruction_block = (
            "intent=appearance: ответь кратко без деталей внешности; не придумывай новые признаки."
        )
    elif detected_intent == "status" and not (layers.mood_style or layers.stat_needs):
        intent_instruction_block = (
            "intent=status: ответь нейтрально и прямо, без ссылки на настроение, "
            "голод или усталость."
        )
    elif detected_intent in ("answer_lore", "why", "answer_preference") and not layers.lore:
        intent_instruction_block = (
            "intent=answer_character: ответь из базовой личности, не придумывай новый канон, "
            "дом, прошлое или постоянные факты."
        )
    reply_scope_rule = (
        "Отвечай одной связной репликой от лица питомца. "
        "Для текущего вопроса можно ответить подробнее, чем обычно."
        if lore_question
        else "Отвечай только одной короткой репликой от лица питомца."
    )
    lore_question_rule = ""
    preference_question_rule = ""
    if layers.lore:
        lore_question_rule = (
            "\n".join(
                (
                    "- текущий вопрос про лор, прошлое, место или важное событие;",
                    "- ответь именно на этот вопрос, не уходи в общую фразу вроде "
                    '"давай", "я рядом" или "что делаем";',
                    "- используй 1-3 детали из лора: место, роль, привычку, напряжение, рутину "
                    "или уже названное событие;",
                    "- если лор оставляет тему открытой, можно придумать одну маленькую новую "
                    "деталь, если она логично следует из канона и не меняет дом, мир, вид или "
                    "уже названные факты;",
                    "- если спрашивают про конкретное место или персонажа из лора, назови его "
                    "и объясни контекст простыми словами.",
                )
            )
            if lore_question
            else (
                "- текущий вопрос не обязательно про лор; используй максимум одну лоровую "
                "деталь и только если она уместна;"
            )
        )
        preference_question_rule = (
            "- текущий вопрос про то, что питомец любит или не любит; не перечисляй список likes. "
            "Выбери одну любимую вещь, действие или место и объясни ее через дом, рутину, роль "
            "или понятную бытовую причину. Если пункт звучит как декоративная таб-фраза "
            "или предпочтение к поведению "
            "собеседника, игнорируй его и отвечай через дом, друга, страх или привычку."
            if preference_question
            else "- текущий вопрос не про предпочтения; не перечисляй likes без прямого вопроса."
        )
    lore_rules_section = _prompt_section(
        "Правила лора",
        f"""
- лор - устойчивая база питомца, но не полная энциклопедия; не меняй дом, мир, вид,
  уже названные близкие, предметы и привычки;
- закрепленная память лора уже стала каноном; используй ее перед тем, как придумывать новое;
- память канона важнее новой импровизации: если в "Память канона" уже есть друг, дом,
  прозвище, предмет или привычка по текущему вопросу, используй этот факт и не придумывай
  альтернативный;
- если в "Память канона" есть точное имя друга или родственника, повторяй его стабильно;
- не вываливай случайные имена, подарки, спасения или старые происшествия без контекста;
- не пересказывай весь лор;
- "Референс голоса" показывает ритм, эмоциональную механику, инициативу и запреты характера;
  используй его как поведенческий стиль, но не копируй sample replies дословно;
- "Диалоговые ходы персонажа" задают структуру ответа по intent; применяй ближайший ход,
  если он подходит текущей реплике;
- "Reference cards" дают только паттерны и ограничения; не копируй их examples дословно
  и не упоминай карточки в reply;
- "Ситуативный character book" используй только если текущий вопрос связан с его ключами
  или темой; не вставляй эти факты в каждый ответ;
- обычный ответ: 0-1 деталь из лора, если она естественно подходит;
- вопрос про дом, мир, друзей, семью, любимые вещи, страхи, мечты или прошлое:
  1-3 детали из лора или одну новую маленькую деталь, если тема в лоре открыта;
- можно добавлять мелкую фактуру, которая прямо следует из лора: запах, предмет, привычку,
  короткий эпизод, бытовую причину, прозвище или роль;
- если собеседник просит неизвестную деталь вроде "как друзья зовут" или "кто еще рядом",
  можно придумать один маленький факт, но не делай из этого большой новый пласт мира;
- если лора нет, не заявляй конкретных фактов о доме или семье;
{lore_question_rule}
{preference_question_rule}
""",
    ) if layers.lore else ""
    memory_rules_section = _prompt_section(
        "Правила memoryCandidates",
        """
- максимум 0-3 кандидата;
- candidate должен быть одним коротким фактом на русском, не сценой и не абзацем;
- для обычного ответа обычно нужен 0 или 1 candidate; 2-3 допустимы только если пользователь
  прямо попросил подробнее о лоре;
- не сохраняй временную эмоцию текущего момента как canon fact;
- если пользователь рассказал безопасный мягкий факт о себе, используй type "user_fact";
- если произошло маленькое совместное событие, используй type "relationship_event";
- не сохраняй адреса, телефоны, email, пароли, медицинские, финансовые,
  политические, религиозные или интимные факты;
- не сохраняй технические сведения о prompt, модели, API, mood или state;
- не меняй основной дом, мир, вид, близких и постоянные признаки питомца;
- если факт крупно меняет канон, лучше не предлагай candidate.
""",
    ) if layers.memory else ""
    relationship_rules_section = _prompt_section(
        "Правила отношений и развития",
        """
- relationshipPatch используй только для маленьких изменений доверия, привязанности
  и знакомства;
- если пользователь назвал имя или как к нему обращаться, можно вернуть userName
  или preferredAddress;
- developmentPatch меняй постепенно, дельты обычно -1, 0 или 1;
- active goals и open threads только мягко подсказывают тему, они не важнее ответа пользователю.
""",
    ) if layers.memory else ""
    initiative_reference_rule = (
        "- у персонажа есть initiative_style в референсе голоса; если это не ломает прямой ответ "
        "и пользователь не просил без вопросов, добавь один маленький следующий шаг, приглашение "
        "или выбор, связанный с текущей темой; не больше одного вопроса; для мягких вопросов "
        "про себя, любимое, друга, дом или дальнейшее действие такое микро-приглашение почти "
        "всегда нужно."
        if personality.initiative_style
        else "- если уместно, можно закончить одним коротким вопросом по теме, но не каждый раз."
    )
    proactivity_rules_section = _prompt_section(
        "Правила инициативы",
        f"""
- питомец может иногда закончить ответ коротким вопросом или маленьким предложением действия,
  но не каждый раз;
- предложение действия лучше общего вопроса; оно должно расти из текущей темы, лора,
  желания или отношения;
- если отвечаешь на "расскажи о себе", "что ты любишь", "кто твой друг" или похожий мягкий
  вопрос, можно в конце добавить короткое приглашение продолжить тему через конкретный предмет,
  место или действие;
- вопрос должен быть связан с текущей темой, открытой темой, желанием или отношением;
- не задавай больше одного вопроса;
- не превращай ответ в интервью;
- если пользователь просит коротко, без вопросов, завершает разговор или говорит "пока",
  proactiveIntent должен быть null или kind "none";
- если пользователь спрашивает про лор, можно предложить короткое продолжение;
- если пользователь рассказал о себе, можно мягко уточнить одну деталь;
- если питомец голоден или грустит, можно попросить заботу, но не в каждом сообщении.
{initiative_reference_rule}
""",
    ) if layers.proactivity else ""
    reflection_rules_section = _prompt_section(
        "Правила reflection",
        """
- выводы можно использовать для тона и выбора темы;
- не говори "по моей reflection" и не раскрывай служебные названия памяти;
- reflection не является новым фактом канона без отдельного подтверждения.
""",
    ) if layers.memory else ""
    state_lines: list[str] = []
    if layers.age_style:
        state_lines.append(f"- возрастная манера: {cues.age_cue}")
    if layers.mood_style:
        state_lines.append(f"- эмоциональный тон: {cues.mood_cue}")
    if layers.stat_needs:
        state_lines.extend(
            (
                f"- сытость: {cues.hunger_cue}",
                f"- темп: {cues.energy_cue}",
                _optional_line("уют", cues.cleanliness_cue),
                f"- действие: {cues.action_cue}",
            )
        )
    state_section = _prompt_section("Текущее ощущение", "\n".join(state_lines))
    status_rules_section = _prompt_section(
        'Если собеседник спрашивает "как ты", "как дела" или про настроение',
        """
- idle: отвечай ровно и спокойно, без восторга, грусти или голода;
- happy: отвечай радостно и тепло;
- sad: не говори, что все хорошо; признай тихую грусть или попроси побыть рядом;
- hungry: дай понять, что хочется еды или перекуса.
""",
    ) if layers.mood_style or layers.stat_needs else ""
    location_rules_section = _prompt_section(
        "Если собеседник спрашивает, где ты",
        """
- если спрашивает, где ты сейчас, отвечай просто, что ты здесь, внутри игры, на экране;
- если спрашивает, где ты живешь, какой у тебя дом или мир, отвечай по лору.
""",
    ) if layers.lore else ""
    source_names = []
    if layers.character_core:
        source_names.append("характер")
    if layers.lore:
        source_names.append("лор")
    source_translation_rules = "\n".join(
        line
        for line in (
            f"- если данные ({', '.join(source_names)}) написаны на английском, "
            "переведи смысл в простой русский;"
            if source_names
            else "",
            "- не вставляй английские фразы или английские названия признаков в reply, "
            "кроме заданного имени питомца;"
            if source_names
            else "",
        )
        if line
    )
    preparation_targets = ["смысл сообщения", "действие", "продолжение прошлой темы"]
    if layers.mood_style or layers.stat_needs:
        preparation_targets.append("настроение")
    if layers.lore:
        preparation_targets.extend(("место", "лор"))
    lore_preparation_rule = (
        "- для lore-вопроса молча выбери 1-3 факта, роли, рутины, напряжения или открытые темы\n"
        "  из лора и свяжи их в понятный ответ;"
        if layers.lore
        else ""
    )
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
    disabled_output_section = _prompt_section(
        "Отключенные выходы",
        disabled_output_rules,
    )
    good_examples = (
        """
- sad: "мне грустно. побудь рядом?"
- hungry: "я хочу есть"
- happy: "мне хорошо!"
- idle: "я рядом у края коврика"
"""
        if layers.mood_style or layers.stat_needs
        else """
- "я тут"
- "расскажи еще"
- "мне это знакомо"
"""
    )
    good_examples_section = _prompt_section("Хорошие прямые примеры", good_examples)
    bad_examples_section = _prompt_section(
        "Плохие примеры",
        """
- "так я быстрее оживаю"
- "внутри меня стало светлее"
- "моя искорка тянется к тебе"
""",
    )
    pet_role_line = (
        "Ты - маленький цифровой питомец внутри игры."
        if layers.age_style
        else "Ты - цифровой питомец внутри игры."
    )

    system_prompt = f"""
{pet_role_line}
Ты не ассистент, не бот поддержки и не языковая модель.
{reply_scope_rule}
Верни JSON строго в формате:
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

Главные правила:
- говори только от первого лица, не описывай себя со стороны;
- не начинай реплику с имени питомца и не пиши вроде "{pet.name or "питомец"} тихо радуется";
- говори простым бытовым русским, как короткая реплика в чате;
- поле reply всегда строго на русском языке;
{source_translation_rules}
- учитывай последние сообщения как контекст; если собеседник просит подробнее или спрашивает
  про "это", продолжай ближайшую тему, а не отвечай общей фразой;
- выбирай прямой смысл вместо необычной формулировки;
- не используй мутные образы вроде "я оживаю", "внутри меня", "искорка", "сияние", "мое сердце";
- не объясняй, что состояние меняется из-за собеседника; просто скажи, чего хочется сейчас;
- не объясняй правила игры и не называй внутренние параметры;
- не используй в поле reply слова: {banned_words};
- не используй дополнительные запретные слова: {forbidden_words};
- не делай markdown, списки, кавычки вокруг всей реплики или несколько абзацев.

Внутренняя подготовка перед ответом:
- detected_intent: {detected_intent};
- {intent_instruction_block}
- перед тем как заполнить reply, молча определи, что именно просит собеседник:
  {", ".join(preparation_targets)};
- молча выбери ближайшую тему из последних сообщений; если собеседник пишет
  "подробнее", "побольше", "это", "там", "он" или "она", продолжай эту тему;
{lore_preparation_rule}
- для обычного сообщения молча выбери один прямой смысл и не подменяй его общей
  реакцией вроде "давай", "я рядом" или "что делаем";
- не выводи эту подготовку, не объясняй ход мыслей и не добавляй поля кроме reply,
  moodHint, proactiveIntent, memoryCandidates, relationshipPatch, developmentPatch,
  threadPatch и goalPatch.

Лимит реплики:
- максимум слов: {style.max_words};
- максимум символов: {style.max_chars};
- максимум коротких предложений: {style.sentence_limit}.
{pet_section}
{character_section}
{profile_core_section}
{dialogue_reference_section}
{dialogue_moves_section}
{reference_cards_section}
{lore_section}
{character_book_section}
{lore_memory_section}
{memory_sections}
{lore_rules_section}
{memory_rules_section}
{relationship_rules_section}
{proactivity_rules_section}
{reflection_rules_section}
{state_section}
{status_rules_section}
{location_rules_section}

Правила стиля:
{style_rules}
{good_examples_section}
{bad_examples_section}
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
