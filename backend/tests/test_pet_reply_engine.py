from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.services.pet_reply_engine import (
    PetRecentMessage,
    PetReplyInput,
    PetReplyPet,
    PetStats,
    build_default_personality,
    build_visual_identity,
    generate_pet_reply,
)
from app.services.pet_reply_engine.age_message_examples import (
    adapt_template,
    format_age_message_examples_for_prompt,
)
from app.services.pet_reply_engine.fallbacks import (
    appearance_fallback,
    fallback_reply,
    select_fallback_reply,
)
from app.services.pet_reply_engine.intent import (
    detect_reply_intent,
    is_home_question,
    is_lore_question,
    is_name_question,
    is_status_question,
)
from app.services.pet_reply_engine.lore import compact_lore_lines, preference_fragment
from app.services.pet_reply_engine.models import PetAgeStage, PetMood, PetPromptLayers
from app.services.pet_reply_engine.prompt_builder import build_pet_reply_messages
from app.services.pet_reply_engine.reply_validator import validate_reply
from app.services.pet_reply_engine.speech_anchors import (
    format_expression_variety_for_prompt,
    load_speech_anchor_examples,
    select_expression_variety_cues,
    select_speech_anchors,
)
from app.services.pet_reply_engine.state_interpreter import interpret_state
from app.services.pet_reply_engine.text_style import style_for_age


def make_reply_input(
    *,
    age_stage: PetAgeStage = "baby",
    mood: PetMood = "idle",
    hunger: int = 80,
    energy: int = 60,
) -> PetReplyInput:
    character_bible = {
        "species": "leaf-faced soft mascot",
        "personality": (
            "Тихий и любопытный листолик сначала прячется за своим листом, "
            "а потом осторожно тянется к тому, кто говорит мягко. Ему важно "
            "чувствовать тепло рядом, но он стесняется просить о нем прямо."
        ),
        "signature": (
            "Лист вместо лица работает как главный язык общения: он кивает им вместо "
            "улыбки и прячет край, когда волнуется. С пользователем лист раскрывается "
            "шире, будто ловит теплый свет."
        ),
        "dialogue_style": {
            "voice_rules": [
                "говорит мягко и коротко, будто проверяет, не слишком ли громко шелестит",
                "эмоции показывает через край листа и маленький шорох",
            ],
            "emotional_reactions": [
                "на заботу отвечает благодарным шорохом",
                "при тревоге честно говорит, что листик дрожит",
            ],
            "initiative_style": "мягко предлагает одно маленькое действие рядом с домом.",
            "sample_replies": [
                "шур... я тут. листик почти не дрожит)",
                "давай посмотрим на теплую полку? там спокойнее.",
                "мне страшно в темном углу, но с тобой я высуну лист.",
            ],
            "avoid_patterns": [
                "не звучать как ассистент",
                "не говорить пустое я рядом без телесной детали",
            ],
        },
        "opening_scenes": [
            "шур... я выглянул из мха и держу листик двумя лапками. как тебя звать?",
        ],
        "lorebook_entries": [
            {
                "keys": ["Кап", "друг", "роса"],
                "content": "Кап - капля росы, которая подбадривает питомца утром.",
            },
            {
                "keys": ["темный угол", "страх"],
                "content": "Темные углы пугают питомца, потому что лист там не ловит теплый свет.",
            },
        ],
        "main_colors": ["grey", "green"],
        "signature_features": ["green leaf face", "round grey body"],
        "materials": ["soft toy skin", "leaf texture"],
        "proportions": "large head, tiny limbs",
        "baby_design": "smaller and rounder version",
        "teen_design": "slightly taller version",
        "adult_design": "fully developed version",
        "do_not_change": ["green leaf face"],
        "lore": {
            "world": {
                "name": "Тихая оранжерея",
                "environment": "маленький теплый сад под стеклянной крышей",
                "story": (
                    "Тихая оранжерея стоит внутри теплого окна, где каждый росток "
                    "знает свой уголок и просыпается от мягкого света."
                ),
                "rules": ["Листья шепчут только при доверии, поэтому громкие слова гаснут в мхе."],
                "sensory_details": ["запах росы", "мягкий зеленый свет"],
            },
            "home": {
                "place": "нижняя полка оранжереи",
                "room": "горшочный уголок",
                "favorite_spot": "теплая полка у окна",
                "story": (
                    "Теплая полка у окна хранит его мох, каплю-колокольчик и следы "
                    "первых робких шагов."
                ),
                "objects": ["капля-колокольчик", "мягкий мох"],
            },
            "origin": {
                "birthplace": "старый глиняный горшок",
                "caretakers": ["садовый фонарь"],
                "formative_event": "однажды поймал первую росинку",
                "story": (
                    "Он появился в старом горшке после ночи, когда фонарь грел землю "
                    "дольше обычного и не дал первому листу замерзнуть."
                ),
            },
            "relationships": {
                "family": ["тихий росток-сосед"],
                "friends": [
                    {
                        "name": "Кап",
                        "role": "друг",
                        "species_or_form": "капля росы",
                        "relationship_dynamic": "подбадривает утром",
                    }
                ],
                "story": (
                    "Он держится рядом с теми, кто умеет ждать, потому что сам долго "
                    "учился не прятаться от каждого звука."
                ),
                "attitude_to_user": "тянется к собеседнику как к теплому свету",
            },
            "inner_life": {
                "core_want": "Хочет стать достаточно смелым, чтобы первым шелестеть привет.",
                "inner_conflict": "Ему нужно внимание, но он боится выглядеть навязчивым.",
                "likes": ["роса", "теплый свет"],
                "dislikes": ["сухой ветер"],
                "fears": ["темные углы"],
                "dreams": ["вырастить большой лист"],
                "habits": ["трогает листик, когда думает"],
                "comfort_actions": ["прячется в мох"],
                "flaws": ["иногда стесняется просить внимания"],
            },
            "voice": {
                "speech_pattern": "Говорит коротко, мягко и часто заменяет улыбку шорохом.",
                "favorite_phrases": ["шур"],
                "topic_hooks": ["утренняя роса"],
                "secret_details": ["прячет блестящую росинку"],
                "avoid_saying": ["я из металла"],
            },
            "growth_arc": {
                "baby": "учится узнавать тепло",
                "teen": "становится смелее у окна",
                "adult": "бережет свой маленький сад",
            },
        },
    }
    visual_identity = build_visual_identity("серый челик с листом вместо лица", character_bible)
    return PetReplyInput(
        user_action="chat_message",
        user_text="как ты?",
        pet=PetReplyPet(
            name="Листик",
            age_stage=age_stage,
            mood=mood,
            stats=PetStats(
                hunger=hunger,
                happiness=70,
                energy=energy,
                cleanliness=90,
            ),
            visual_identity=visual_identity,
            personality=build_default_personality(
                "серый челик с листом вместо лица",
                character_bible,
            ),
            lore=character_bible["lore"],
        ),
        recent_messages=(PetRecentMessage(role="user", text="привет"),),
    )


def test_state_interpreter_translates_numbers_to_qualitative_cues() -> None:
    reply_input = make_reply_input(mood="hungry", hunger=18, energy=25)

    cues = interpret_state(reply_input)

    assert cues.hunger_band == "low"
    assert cues.energy_band == "low"
    assert "сильный голод" in cues.hunger_cue
    assert "низкая энергия" in cues.energy_cue
    assert "18" not in cues.hunger_cue
    assert "25" not in cues.energy_cue


def test_state_interpreter_avoids_repeating_food_request_too_often() -> None:
    reply_input = replace(
        make_reply_input(mood="hungry", hunger=18),
        recent_messages=(
            PetRecentMessage(role="pet", text="пи... животик просит крошку"),
            PetRecentMessage(role="user", text="как ты?"),
        ),
    )

    cues = interpret_state(reply_input)

    assert cues.recent_food_mention
    assert "не повторяй" in cues.hunger_cue
    assert validate_reply(fallback_reply(reply_input), "baby").is_valid


def test_text_style_changes_limits_by_age() -> None:
    baby = style_for_age("baby")
    teen = style_for_age("teen")
    adult = style_for_age("adult")

    assert baby.max_words == 12
    assert baby.max_chars == 120
    assert "2-5 слов" in " ".join(baby.style_rules)
    assert "речевые ошибки" in " ".join(baby.style_rules)
    assert teen.max_words == 18
    assert adult.max_chars == 300
    assert style_for_age("baby", "low").max_words == 7
    assert style_for_age("adult", "low").sentence_limit == 3


def test_prompt_builder_uses_selected_age_behavior_profile() -> None:
    baby_prompt = build_pet_reply_messages(make_reply_input(age_stage="baby"))[0]["content"]
    teen_prompt = build_pet_reply_messages(make_reply_input(age_stage="teen"))[0]["content"]
    adult_prompt = build_pet_reply_messages(make_reply_input(age_stage="adult"))[0]["content"]

    assert "Примеры фраз по возрасту" in baby_prompt
    assert "selected_stage: baby" in baby_prompt
    assert "2-5 слов" in baby_prompt
    assert "Звукоподражания ОБЯЗАТЕЛЬНЫ" in baby_prompt
    assert "Глустно" in baby_prompt or "Приветик" in baby_prompt
    assert "скрытая нежность" not in baby_prompt

    assert "selected_stage: teen" in teen_prompt
    assert "5-12 слов" in teen_prompt
    assert "сленг" in teen_prompt
    assert "скрытая нежность" in teen_prompt
    assert "10-25 слов" not in teen_prompt

    assert "selected_stage: adult" in adult_prompt
    assert "10-25 слов" in adult_prompt
    assert "спокойный" in adult_prompt
    assert "с юмором" in adult_prompt
    assert "Звукоподражания ОБЯЗАТЕЛЬНЫ" not in adult_prompt


def test_age_message_examples_adapt_placeholders_from_character_bible() -> None:
    reply_input = make_reply_input(age_stage="baby", mood="hungry")

    text = adapt_template(
        "[звук]-[звук]! [имя] хочет [еда], боится [страх], держит [часть тела].",
        reply_input,
    )

    assert "[" not in text
    assert "Листик" in text
    assert "роса" in text
    assert "темные углы" in text


def test_age_message_examples_prompt_block_stays_compact() -> None:
    reply_input = make_reply_input(age_stage="baby", mood="hungry")

    block = format_age_message_examples_for_prompt(reply_input, "status")

    assert "selected_stage: baby" in block
    assert "Speech rules from dataset" in block
    assert block.count("- hungry:") <= 2
    assert sum(1 for line in block.splitlines() if line.startswith("- ") and ":" in line) < 30
    assert "adult" not in block


def test_turn_level_speech_anchor_dataset_is_loaded() -> None:
    examples = load_speech_anchor_examples()

    assert len(examples) == 243
    assert any(item.intent == "correction_no_hallucination" for item in examples)
    assert any(item.intent == "proactive" for item in examples)
    assert all(item.id.startswith("turn:") for item in examples)


def test_expression_variety_cues_use_enriched_dataset_sections() -> None:
    reply_input = replace(
        make_reply_input(age_stage="teen"),
        user_text="как дела?",
        pet=replace(
            make_reply_input(age_stage="teen").pet,
            visual_identity=replace(
                make_reply_input(age_stage="teen").pet.visual_identity,
                species="электрический дракончик",
                signature_features=("маленькие рога", "заряд на хвосте"),
            ),
        ),
    )

    cues = select_expression_variety_cues(reply_input, "status", limit=4)
    prompt_block = format_expression_variety_for_prompt(cues)

    assert cues
    assert any(cue.id.startswith("speech_style:electric:") for cue in cues)
    assert any(cue.id.startswith("character_axis:energy:") for cue in cues)
    assert any(cue.id.startswith("character_axis:intellect:") for cue in cues)
    assert any(cue.id.startswith("expression:") for cue in cues)
    assert "Подсказки для разнообразия речи" in prompt_block
    assert "готовый ответ" in prompt_block
    assert "thinking style" in prompt_block


def test_speech_anchor_retrieval_selects_intent_specific_turn_examples() -> None:
    reply_input = replace(make_reply_input(age_stage="baby"), user_text="как ты?")

    anchors, rejected = select_speech_anchors(reply_input, "status", limit=3)

    assert anchors
    assert anchors[0].intent == "status"
    assert anchors[0].stage == "baby"
    assert "intent_match" in anchors[0].score_reasons
    assert "[" not in anchors[0].source_text
    assert isinstance(rejected, tuple)


def test_prompt_builder_includes_speech_anchors_and_hides_stage_actions() -> None:
    prompt = build_pet_reply_messages(
        replace(make_reply_input(age_stage="baby"), user_text="как ты?")
    )[0]["content"]

    assert "Примеры ближайших реплик" in prompt
    assert "Темп, мышление и каналы выражения" in prompt
    assert "source_text:" in prompt
    assert "Ближайшие речевые примеры" in prompt
    assert "do not transfer body_parts" not in prompt
    assert "не выводи сценические действия" not in prompt
    assert "*трёт" not in prompt


def test_prompt_builder_disables_speech_anchors_with_age_style() -> None:
    reply_input = replace(
        make_reply_input(age_stage="baby"),
        prompt_layers=PetPromptLayers(age_style=False),
    )

    prompt = build_pet_reply_messages(reply_input)[0]["content"]

    assert "Примеры ближайших реплик" not in prompt


def test_prompt_builder_disables_age_examples_when_age_style_is_off() -> None:
    reply_input = replace(
        make_reply_input(age_stage="baby"),
        prompt_layers=PetPromptLayers(age_style=False),
    )

    prompt = build_pet_reply_messages(reply_input)[0]["content"]

    assert "Примеры фраз по возрасту" not in prompt
    assert "2-5 слов" not in prompt
    assert "Звукоподражания ОБЯЗАТЕЛЬНЫ" not in prompt
    assert "selected_stage: baby" not in prompt


def test_prompt_builder_keeps_character_core_without_visual_body_cues() -> None:
    messages = build_pet_reply_messages(make_reply_input(age_stage="teen", mood="happy"))
    prompt = messages[0]["content"]

    assert "leaf-faced soft mascot" in prompt
    assert "green leaf face" not in prompt
    assert "телесные слова" not in prompt
    assert "звуки для" not in prompt
    assert "метафоры для редкого использования" not in prompt
    assert "Лор питомца" in prompt
    assert "Тихая оранжерея" in prompt
    assert "теплая полка у окна" in prompt
    assert "главное желание" in prompt
    assert "история отношений" not in prompt
    assert "не пересказывай весь лор" not in prompt
    assert "Референс голоса" in prompt
    assert "Character Profile V2 stable core" in prompt
    assert "Диалоговые ходы персонажа" not in prompt
    assert "Reference cards" not in prompt
    assert "Текущий intent: status" in prompt
    assert "sample_replies" in prompt
    assert "sample_replies_do_not_copy" not in prompt
    assert "шур... я тут. листик почти не дрожит)" in prompt
    assert "Ситуативный character book" in prompt
    assert "Кап, друг, роса" in prompt
    assert "темперамент: shy" in prompt
    assert "Отвечай как Листик" in prompt
    assert "Не отвечай как ассистент" in prompt
    assert "не начинай реплику с имени питомца" not in prompt
    assert "Лор ниже - опора, а не клетка" in prompt
    assert "Сквозная стилистическая настройка" not in prompt
    assert "Mature baseline" not in prompt
    assert "Примеры фраз по возрасту" in prompt
    assert "переведи смысл в простой русский" not in prompt
    assert "так я быстрее оживаю" not in prompt
    assert "как ты выглядишь" not in prompt
    assert "внутри этой игры" not in prompt
    assert "место в приложении" not in prompt
    assert "Baby voice" not in prompt
    assert "mood" in prompt
    assert messages[-1]["content"].endswith("как ты?")


def test_compact_lore_lines_prioritize_story_fields() -> None:
    lines = compact_lore_lines(make_reply_input().pet.lore, age_stage="teen")
    text = "\n".join(lines)

    assert "Тихая оранжерея стоит внутри теплого окна" in text
    assert "Теплая полка у окна хранит его мох" in text
    assert "история отношений" in text
    assert "главное желание" in text
    assert "внутренний конфликт" in text


def test_compact_lore_lines_light_mode_omits_world_rules() -> None:
    lore = {
        "world": {
            "story": "Электрический дракон живет у медных камней.",
            "rules": [
                "магнитное поле иногда сбивает компасы",
                "приборы рядом с ним щелкают",
            ],
        },
        "home": {"story": "Он спит в теплой нише.", "favorite_spot": "медный камень"},
        "inner_life": {
            "core_want": "научиться не пугать друзей разрядами",
            "inner_conflict": "хочет подойти ближе, но боится щелчков",
            "habits": ["перекладывает гладкие камушки"],
        },
        "voice": {"speech_pattern": "говорит быстро и обрывает фразы"},
    }

    full = "\n".join(compact_lore_lines(lore, age_stage="teen"))
    light = "\n".join(compact_lore_lines(lore, age_stage="teen", detail_mode="light"))

    assert "магнитное поле" in full
    assert "магнитное поле" not in light
    assert "главное желание" in light
    assert "привычки" in light


def test_lore_preferences_skip_weak_template_phrases() -> None:
    lore = {
        "world": {"story": "В теплице номер четыре Кап спрятал его после кошки."},
        "home": {
            "favorite_spot": "моховая полка",
            "story": "На моховой полке Кап спрятал его после кошки и оставил каплю.",
        },
        "origin": {"formative_event": "Кап спрятал его после кошки"},
        "inner_life": {
            "likes": [
                "теплый утренний туман",
                "синие лейки",
                "короткие просьбы",
                "моховая полка, где Кап спрятал его после кошки",
            ]
        },
    }

    lines = compact_lore_lines(lore, age_stage="teen")
    fragment = preference_fragment(lore)

    assert "короткие просьбы" not in "\n".join(lines)
    assert "синие лейки" not in "\n".join(lines)
    assert fragment
    assert "моховая полка" in fragment
    assert "после кошки" in fragment


def test_prompt_builder_keeps_lore_question_context_with_300_char_limit() -> None:
    messages = build_pet_reply_messages(
        replace(make_reply_input(age_stage="baby"), user_text="где ты живешь?")
    )
    prompt = messages[0]["content"]

    assert "максимум слов" not in prompt
    assert "Длина reply: максимум 300 символов" in prompt
    assert "Лор питомца" in prompt
    assert "если детали не хватает, придумай ее органично на ходу" in prompt
    assert "где ты живешь" in messages[-1]["content"]


def test_lore_intent_detects_specific_background_followups() -> None:
    assert is_lore_question("расскажи побольше про теплицу номер четыре")
    assert is_lore_question("что случилось в теплице номер четыре?")
    assert is_lore_question("а подробнее?")
    assert not is_lore_question("расскажи что-нибудь")


def test_home_intent_detects_bare_home_followup() -> None:
    assert is_home_question("расскажи подробнее про дом")
    assert is_lore_question("расскажи подробнее про дом")


def test_reply_intent_router_covers_character_dialogue_moves() -> None:
    assert detect_reply_intent("что ты любишь?") == "answer_preference"
    assert detect_reply_intent("почему?") == "why"
    assert detect_reply_intent("не задавай мне вопросов") == "boundary"
    assert detect_reply_intent("что ты запомнил обо мне?") == "memory_control"
    assert detect_reply_intent("обнимаю тебя") == "care"
    assert detect_reply_intent("придумай, что мы сделаем вечером") == "playful_offer"
    assert (
        detect_reply_intent(
            "подробнее",
            (PetRecentMessage(role="pet", text="я живу в бюро находок."),),
        )
        == "continue_thread"
    )


def test_prompt_builder_uses_lore_as_context_without_internal_preparation_rules() -> None:
    messages = build_pet_reply_messages(
        replace(
            make_reply_input(age_stage="teen"),
            user_text="расскажи побольше про теплицу номер четыре",
        )
    )
    prompt = messages[0]["content"]

    assert "максимум слов" not in prompt
    assert "не уходи в общую фразу" not in prompt
    assert "Внутренняя подготовка перед ответом" not in prompt
    assert "Лор ниже - опора, а не клетка" in prompt
    assert "Тихая оранжерея" in prompt


def test_prompt_builder_keeps_preference_answers_organic() -> None:
    messages = build_pet_reply_messages(
        replace(make_reply_input(age_stage="teen"), user_text="что ты любишь?")
    )
    prompt = messages[0]["content"]

    assert "не перечисляй список likes" not in prompt
    assert "декоративная таб-фраза" not in prompt
    assert "если детали не хватает, придумай ее органично на ходу" in prompt
    assert "Лор питомца" in prompt


def test_prompt_builder_keeps_small_self_invention_as_one_general_rule() -> None:
    messages = build_pet_reply_messages(
        replace(make_reply_input(age_stage="teen"), user_text="что ты обычно ешь?")
    )
    prompt = messages[0]["content"]

    assert "если детали не хватает, придумай ее органично на ходу" in prompt
    assert "не уходи в отказ" not in prompt
    assert "не заявляй конкретных фактов о доме или семье" not in prompt


def test_prompt_builder_includes_lore_memory_rules() -> None:
    reply_input = replace(
        make_reply_input(age_stage="teen"),
        lore_memories=("ЛОР: друзья зовут меня Листикор, когда я прячусь за листом.",),
    )
    prompt = build_pet_reply_messages(reply_input)[0]["content"]

    assert "Закрепленная память лора" in prompt
    assert "ЛОР: друзья зовут меня Листикор" in prompt
    assert "memoryCandidates" in prompt
    assert "loreMemoriesToSave" not in prompt
    assert 'префиксом "ЛОР: "' not in prompt


def test_prompt_builder_handles_legacy_profile_without_lore() -> None:
    visual = build_visual_identity("маленький дракон")
    reply_input = PetReplyInput(
        user_action="chat_message",
        user_text="где ты живешь?",
        pet=PetReplyPet(
            name=None,
            age_stage="teen",
            mood="idle",
            stats=PetStats(hunger=80, happiness=70),
            visual_identity=visual,
            personality=build_default_personality("маленький дракон"),
        ),
    )

    prompt = build_pet_reply_messages(reply_input)[0]["content"]

    assert "лора нет; опирайся на визуальную идею: маленький дракон" in prompt
    assert "если лора нет, не заявляй конкретных фактов о доме или семье" not in prompt


def test_prompt_builder_replaces_old_baby_voice_with_message_examples() -> None:
    messages = build_pet_reply_messages(make_reply_input(age_stage="baby"))
    prompt = messages[0]["content"]

    assert "Baby voice" not in prompt
    assert "звуки для частого малышового использования" not in prompt
    assert "чат-скобочку" not in prompt
    assert "я безымянен" not in prompt
    assert "только звуки из образа" not in prompt
    assert "шур-шур" not in prompt
    assert "Примеры фраз по возрасту" in prompt
    assert "Звукоподражания ОБЯЗАТЕЛЬНЫ" in prompt
    assert "Речевые ошибки" in prompt
    assert "телесные слова" not in prompt
    assert "метафоры для редкого использования" not in prompt


@pytest.mark.parametrize(
    ("reply", "expected_flag"),
    [
        ("Конечно, пользователь, чем могу помочь?", "banned_word"),
        ("Мне хочется, чтобы ты побыл рядом, так я быстрее оживаю", "banned_word"),
        ("внутри меня стало светлее", "banned_word"),
        ("я цифровой питомец в приложении", "banned_word"),
        ("я здесь, на экране", "banned_word"),
        ("мне нужно, чтобы ты остался рядом", "unclear_abstraction"),
        ("- Я рядом", "markdown_or_list"),
        ("первая строка\nвторая строка", "multi_paragraph"),
        ("я безымянен", "dry_baby_reply"),
        ("я не знаю", "dry_baby_reply"),
        ("мне 35 лет, но я все равно слушаю.", "literal_age_claim"),
        ("мне за тридцать, хотя я выгляжу иначе.", "literal_age_claim"),
        (" ".join(["слово"] * 13), "too_many_words"),
    ],
)
def test_reply_validator_rejects_bad_outputs(reply: str, expected_flag: str) -> None:
    result = validate_reply(reply, "baby")

    assert not result.is_valid
    assert expected_flag in result.flags


def test_reply_validator_rejects_third_person_pet_name() -> None:
    result = validate_reply("Листик тихо радуется.", "teen", pet_name="Листик")

    assert not result.is_valid
    assert "third_person" in result.flags


def test_reply_validator_allows_baby_name_self_reference() -> None:
    result = validate_reply("Листик хочет ням!", "baby", pet_name="Листик")

    assert result.is_valid


def test_reply_validator_rejects_adult_baby_coded_speech() -> None:
    result = validate_reply("Уля! Очень-очень хочу спатки!", "adult")

    assert not result.is_valid
    assert "age_style_mismatch" in result.flags


def test_reply_validator_rejects_multiple_copied_age_examples() -> None:
    result = validate_reply("Приветик! Ты пришёл! Ты... ты мой Болшой?", "baby")

    assert not result.is_valid
    assert "copied_age_examples" in result.flags


def test_reply_validator_rejects_copied_turn_level_speech_anchor() -> None:
    result = validate_reply(
        "Понятия не имею. Не был. Но если интересно — можем вместе сходить.",
        "teen",
    )

    assert not result.is_valid
    assert "copied_speech_anchor" in result.flags


def test_reply_validator_rejects_repeated_lore_term_from_recent_pet_replies() -> None:
    result = validate_reply(
        "Моё магнитное поле опять тихо щёлкает, так что я осторожничаю.",
        "teen",
        user_text="как ты?",
        recent_pet_replies=(
            "Магнитное поле сегодня щёлкает у хвоста.",
            "Я лучше отойду, магнитное поле шалит.",
            "А потом я посмотрел на камушек.",
        ),
    )

    assert not result.is_valid
    assert "repeated_lore_term" in result.flags


@pytest.mark.parametrize(
    "reply",
    [
        "я люблю теплый утренний туман и синие лейки",
        "я люблю короткие просьбы",
    ],
)
def test_reply_validator_rejects_template_lore_phrases(reply: str) -> None:
    result = validate_reply(reply, "teen", user_text="что ты любишь?")

    assert not result.is_valid
    assert "template_lore_phrase" in result.flags


@pytest.mark.parametrize(
    ("mood", "reply"),
    [
        ("sad", "у меня все хорошо"),
        ("hungry", "я сыт и спокоен"),
        ("happy", "мне грустно"),
        ("idle", "ура, я в полном восторге!"),
    ],
)
def test_reply_validator_rejects_state_mismatches(mood: PetMood, reply: str) -> None:
    result = validate_reply(
        reply,
        "teen",
        current_mood=mood,
        user_text="как у тебя дела?",
    )

    assert not result.is_valid
    assert "mood_mismatch" in result.flags


def test_appearance_question_is_not_status_question() -> None:
    assert not is_status_question("как ты выглядишь?")


def test_name_question_is_detected() -> None:
    assert is_name_question("как тебя зовут?")


def test_sad_pet_can_answer_appearance_question_without_mood_fallback() -> None:
    result = validate_reply(
        "я серый, с листом вместо лица",
        "teen",
        current_mood="sad",
        user_text="как ты выглядишь?",
    )

    assert result.is_valid


@pytest.mark.parametrize(
    ("mood", "reply"),
    [
        ("sad", "можно я побуду рядом?"),
        ("hungry", "перекус бы кстати"),
        ("happy", "мне хорошо!"),
        ("idle", "я спокойно рядом"),
    ],
)
def test_reply_validator_accepts_state_matching_status_replies(
    mood: PetMood,
    reply: str,
) -> None:
    result = validate_reply(
        reply,
        "teen",
        current_mood=mood,
        user_text="как у тебя дела?",
    )

    assert result.is_valid


def test_reply_validator_accepts_short_pet_reply() -> None:
    result = validate_reply("мр... побудь рядом", "baby")

    assert result.is_valid
    assert result.flags == ()


def test_reply_validator_does_not_ban_words_containing_soul_root() -> None:
    result = validate_reply(
        "Мохруша укрыл меня моховой подушечкой, и я успокоился.",
        "teen",
        user_text="расскажи подробнее про дом",
    )

    assert result.is_valid


def test_reply_validator_allows_slightly_longer_baby_lore_answer() -> None:
    result = validate_reply("шур... теплая полка у окна)", "baby", user_text="где ты живешь?")

    assert result.is_valid


def test_reply_validator_allows_expanded_teen_lore_answer() -> None:
    result = validate_reply(
        (
            "В теплице была нижняя полка, где я нашел каплю-колокольчик. "
            "Кап подбадривал меня утром, так что да, место важное."
        ),
        "teen",
        user_text="расскажи побольше про теплицу номер четыре",
    )

    assert result.is_valid


def test_fallbacks_cover_base_age_mood_energy_matrix() -> None:
    for age_stage in ("baby", "teen", "adult"):
        for mood in ("idle", "happy", "hungry", "sad"):
            for energy_band in ("low", "medium", "high"):
                reply = select_fallback_reply(age_stage, mood, energy_band, action="chat_message")

                assert reply
                assert validate_reply(reply, age_stage).is_valid


def test_fallback_avoids_repeating_last_pet_reply() -> None:
    reply_input = replace(
        make_reply_input(age_stage="baby", mood="happy", energy=90),
        recent_messages=(
            PetRecentMessage(role="user", text="поиграем?"),
            PetRecentMessage(role="pet", text="шур-шур! листик"),
            PetRecentMessage(role="user", text="расскажи что-нибудь"),
        ),
    )

    reply = fallback_reply(reply_input)

    assert reply != "шур-шур! листик"
    assert validate_reply(reply, "baby").is_valid


def test_baby_fallback_uses_character_sound_and_body_word() -> None:
    reply = fallback_reply(make_reply_input(age_stage="baby", mood="happy", energy=90))

    assert reply
    assert reply != "я тут. слушаю тебя."
    assert validate_reply(reply, "baby").is_valid


def test_baby_name_fallback_uses_pet_name_warmly() -> None:
    reply_input = replace(make_reply_input(age_stage="baby"), user_text="как тебя зовут?")

    reply = fallback_reply(reply_input)

    assert reply == "я Листик! а ты?"
    assert validate_reply(reply, "baby", pet_name="Листик").is_valid


def test_baby_reason_fallback_is_not_dry() -> None:
    reply_input = replace(make_reply_input(age_stage="baby"), user_text="а почему?")

    reply = fallback_reply(reply_input)

    assert reply == "я не знаю... стланно."
    assert validate_reply(reply, "baby").is_valid


def test_baby_action_fallback_keeps_action_specific_response() -> None:
    reply_input = replace(
        make_reply_input(age_stage="baby", mood="happy", energy=90),
        user_action="feed",
        user_text=None,
    )

    reply = fallback_reply(reply_input)

    assert reply
    assert validate_reply(reply, "baby").is_valid


def test_lore_question_fallback_uses_home_detail() -> None:
    reply_input = replace(make_reply_input(age_stage="baby"), user_text="где ты живешь?")

    reply = fallback_reply(reply_input)

    assert reply.startswith("мой дом: Теплая полка у окна")
    assert "мне там спокойно" in reply
    assert validate_reply(reply, "baby", user_text="где ты живешь?").is_valid


def test_teen_lore_question_fallback_uses_more_home_detail() -> None:
    reply_input = replace(
        make_reply_input(age_stage="teen"),
        user_text="расскажи побольше про теплицу номер четыре",
    )

    reply = fallback_reply(reply_input)

    assert "Теплая полка у окна" in reply
    assert "каплю-колокольчик" in reply
    assert validate_reply(reply, "teen", user_text=reply_input.user_text).is_valid


def test_home_followup_fallback_uses_home_lore_detail() -> None:
    reply_input = replace(
        make_reply_input(age_stage="teen"),
        user_text="расскажи подробнее про дом",
    )

    reply = fallback_reply(reply_input)

    assert "Теплая полка у окна" in reply
    assert "что делаем" not in reply
    assert validate_reply(reply, "teen", user_text=reply_input.user_text).is_valid


def test_home_fallback_finishes_long_lore_fragment() -> None:
    reply_input = make_reply_input(age_stage="teen")
    lore = dict(reply_input.pet.lore or {})
    lore["home"] = {
        **lore["home"],
        "story": (
            "После того как соседская кошка за стеклом резко ударила лапой по раме, "
            "Листик уронил первый горшок и укатился под нижнюю полку. "
            "Мохруша укрыл его моховой подушечкой."
        ),
    }
    reply_input = replace(
        reply_input,
        user_text="расскажи подробнее про дом",
        pet=replace(reply_input.pet, lore=lore),
    )

    reply = fallback_reply(reply_input)
    validation = validate_reply(reply, "teen", user_text=reply_input.user_text)

    assert reply.endswith(".")
    assert "banned_word" not in validation.flags
    assert validation.is_valid


def test_visual_identity_adds_electric_baby_sound() -> None:
    visual = build_visual_identity("желтый электрический зверек")

    assert "пику" in visual.chat_cues.sound_words


def test_visual_identity_adds_cat_baby_sound() -> None:
    visual = build_visual_identity("мягкий котенок с большими ушами")

    assert "мяу" in visual.chat_cues.sound_words


def test_visual_identity_does_not_treat_mascot_as_cat() -> None:
    visual = build_visual_identity("soft mascot with leaf face")

    assert "мяу" not in visual.chat_cues.sound_words


def test_fallback_answers_appearance_question_from_visual_identity() -> None:
    reply_input = replace(
        make_reply_input(age_stage="teen", mood="sad", energy=90),
        user_text="как ты выглядишь?",
    )

    reply = fallback_reply(reply_input)

    assert reply == "я выгляжу так: серый челик с листом вместо лица"
    assert "взбодрюсь" not in reply


def test_fallback_answers_location_question() -> None:
    reply_input = replace(
        make_reply_input(age_stage="adult", mood="sad", energy=90),
        user_text="где ты?",
    )

    reply = fallback_reply(reply_input)

    assert reply.startswith("я сейчас: Теплая полка у окна")
    assert "экран" not in reply
    assert validate_reply(reply, "adult", user_text="где ты?").is_valid


def test_baby_appearance_fallback_stays_short() -> None:
    reply = appearance_fallback(
        replace(make_reply_input(age_stage="baby"), user_text="как выглядишь?")
    )

    assert validate_reply(reply, "baby").is_valid


def test_reply_generator_returns_fallback_for_invalid_model_output() -> None:
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"reply": "Как языковая модель, я вижу mood happy.", '
                    '"moodHint": "happy"}'
                )
            )
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: completion),
        )
    )

    result = generate_pet_reply(
        make_reply_input(age_stage="teen", mood="happy"),
        client=client,
        model="test-model",
        timeout=1,
    )

    assert result.used_fallback
    assert "banned_word" in result.validation_flags
    assert result.reply == "ДА! Я знал, что получится! Я крут!"


def test_reply_generator_uses_state_fallback_for_mood_mismatch() -> None:
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"reply": "у меня все хорошо", "moodHint": "sad"}')
            )
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: completion),
        )
    )

    result = generate_pet_reply(
        make_reply_input(age_stage="teen", mood="sad"),
        client=client,
        model="test-model",
        timeout=1,
    )

    assert result.used_fallback
    assert "mood_mismatch" in result.validation_flags
    assert result.reply == "Да норм всё. *отворачивается* Норм..."


def test_reply_generator_replaces_dry_baby_name_reply() -> None:
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"reply": "я безымянен"}')),
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: completion),
        )
    )

    result = generate_pet_reply(
        replace(make_reply_input(age_stage="baby"), user_text="как тебя зовут?"),
        client=client,
        model="test-model",
        timeout=1,
    )

    assert result.used_fallback
    assert "dry_baby_reply" in result.validation_flags
    assert result.reply == "я Листик! а ты?"


def test_reply_generator_replaces_template_lore_phrase() -> None:
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"reply": "я люблю теплый утренний туман и синие лейки", '
                    '"moodHint": null}'
                )
            ),
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: completion),
        )
    )

    result = generate_pet_reply(
        replace(make_reply_input(age_stage="teen"), user_text="что ты любишь?"),
        client=client,
        model="test-model",
        timeout=1,
    )

    assert result.used_fallback
    assert "template_lore_phrase" in result.validation_flags
    assert "синие лейки" not in result.reply


def test_reply_generator_returns_lore_memories_to_save() -> None:
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"reply": "друзья зовут меня Листикор, когда я прячусь.", '
                        '"moodHint": null, '
                        '"loreMemoriesToSave": ["ЛОР: друзья зовут питомца Листикор."]}'
                    )
                )
            ),
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: completion),
        )
    )

    result = generate_pet_reply(
        replace(make_reply_input(age_stage="teen"), user_text="как тебя друзья зовут?"),
        client=client,
        model="test-model",
        timeout=1,
    )

    assert not result.used_fallback
    assert result.lore_memories_to_save == ("ЛОР: друзья зовут питомца Листикор.",)


def test_reply_generator_sends_light_reasoning_without_temperature(monkeypatch) -> None:
    captured_kwargs: dict[str, object] = {}
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"reply": "пи! я тут", "moodHint": null}')
            )
        ]
    )

    def create_completion(**kwargs):
        captured_kwargs.update(kwargs)
        return completion

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_completion)),
    )
    monkeypatch.setattr(
        "app.services.pet_reply_engine.reply_generator.get_settings",
        lambda: SimpleNamespace(
            openai_chat_model="test-model",
            openai_chat_timeout_seconds=1,
            openai_chat_reasoning_effort="low",
        ),
    )

    result = generate_pet_reply(
        make_reply_input(age_stage="baby", mood="idle", energy=90),
        client=client,
        model="test-model",
        timeout=1,
    )

    assert not result.used_fallback
    assert captured_kwargs["reasoning_effort"] == "low"
    assert "temperature" not in captured_kwargs
