from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest

from app.schemas import LocalChatHistoryItem, LocalPetChatContext, LocalPetMemoryContext
from app.services import background_story_service

TEST_CAUSAL_PLAN = {
    "setup": "Олег находится у лесной тропы.",
    "problem": "На тропе возникает одна проблема.",
    "action": "Олег отвечает одним действием.",
    "whyActionWorks": "Действие воздействует на причину проблемы.",
    "consequence": "Действие приводит к прямому последствию.",
}


def test_story_direction_uses_all_modes_before_repeating() -> None:
    rng = random.Random(7)
    history: list[dict[str, str]] = []

    for _ in range(len(background_story_service.STORY_DIRECTION_SPECS)):
        direction = background_story_service.select_background_story_direction(
            history,
            rng=rng,
        )
        assert direction["plotMode"] not in {
            item["plotMode"] for item in history[-background_story_service.STORY_MODE_COOLDOWN :]
        }
        history.append(direction)

    assert {item["plotMode"] for item in history} == set(
        background_story_service.STORY_DIRECTION_SPECS
    )


def test_story_direction_block_forbids_fallback_to_trap_pattern() -> None:
    direction = {
        "plotMode": "mystery",
        "incidentClass": "other_agent_action",
        "causalOrigin": "other_agent",
        "eventScale": "shared_situation",
        "settingClass": "castle_or_tower",
        "oppositionClass": "supernatural",
        "resolutionMode": "investigation",
        "resolutionFamily": "evidence_based_investigation",
        "valenceTarget": "positive",
    }

    block = background_story_service._story_direction_block(direction)

    assert "замок, башня" in block
    assert "дух, привидение" in block
    assert "герой случайно попал в ловушку" in block
    assert "каждый statImpact положительный" in block
    assert "incidentClass=other_agent_action" in block
    assert "рисунок, травинка" in block
    assert "Не упоминай автоматически каждую текущую травму" in block


def test_full_story_direction_uses_overall_valence_without_empty_impacts_rule() -> None:
    direction = {
        "plotMode": "peaceful_change",
        "incidentClass": "unexpected_opportunity",
        "causalOrigin": "temporary_change",
        "eventScale": "shared_situation",
        "settingClass": "inhabited_place",
        "oppositionClass": "none",
        "resolutionMode": "celebration_or_rest",
        "resolutionFamily": "social_resolution",
        "valenceTarget": "neutral",
    }

    block = background_story_service.story_direction_block(
        direction,
        enforce_single_valence=False,
    )

    assert "плюсы и минусы арки уравновешены" in block
    assert "statImpacts пуст" not in block
    assert "отдельные части могут иметь разную valence" in block


def test_story_direction_valence_distribution_keeps_neutral_rare() -> None:
    rng = random.Random(11)
    history: list[dict[str, str]] = []

    for _ in range(10):
        direction = background_story_service.select_background_story_direction(
            history,
            current_stats={"hunger": 50, "happiness": 50, "energy": 50},
            rng=rng,
        )
        history.append(direction)

    counts = {
        valence: sum(item["valenceTarget"] == valence for item in history)
        for valence in background_story_service.STORY_VALENCE_WEIGHTS
    }
    assert sum(counts.values()) == 10
    assert counts["neutral"] <= 2


def test_peaceful_story_direction_never_forces_negative_valence() -> None:
    rng = random.Random(3)
    history = [
        {"plotMode": mode}
        for mode in background_story_service.STORY_DIRECTION_SPECS
        if mode != "peaceful_change"
    ]

    direction = background_story_service.select_background_story_direction(
        history,
        current_stats={"hunger": 50, "happiness": 50, "energy": 50},
        rng=rng,
    )

    assert direction["plotMode"] == "peaceful_change"
    assert direction["valenceTarget"] != "negative"


def test_puzzle_incident_waits_for_structural_cooldown() -> None:
    rng = random.Random(4)
    other_modes = [
        mode for mode in background_story_service.STORY_DIRECTION_SPECS if mode != "exploration"
    ]
    non_puzzle = [
        "accident",
        "plan_disrupted",
        "environmental_change",
        "unexpected_opportunity",
    ]
    history = [
        {
            "plotMode": mode,
            "incidentClass": non_puzzle[index % len(non_puzzle)],
        }
        for index, mode in enumerate(other_modes)
    ]

    direction = background_story_service.select_background_story_direction(
        history,
        rng=rng,
    )

    assert direction["plotMode"] == "exploration"
    assert direction["incidentClass"] == "puzzle_discovery"
    next_direction = background_story_service.select_background_story_direction(
        [*history, direction],
        rng=rng,
    )
    assert next_direction["incidentClass"] != "puzzle_discovery"


@pytest.mark.parametrize(
    ("valence", "minimum", "maximum", "min_items", "max_items"),
    [
        ("positive", 1, 25, 1, 2),
        ("negative", -25, -1, 1, 2),
        ("neutral", -25, 25, None, 0),
    ],
)
def test_story_schema_enforces_valence_stat_contract(
    valence: str,
    minimum: int,
    maximum: int,
    min_items: int | None,
    max_items: int,
) -> None:
    schema = background_story_service._background_story_schema_for_direction(
        {"valenceTarget": valence}
    )
    stat_impacts = schema["properties"]["statImpacts"]
    amount = stat_impacts["items"]["properties"]["amount"]

    assert schema["properties"]["valence"]["enum"] == [valence]
    assert amount["minimum"] == minimum
    assert amount["maximum"] == maximum
    assert stat_impacts.get("minItems") == min_items
    assert stat_impacts["maxItems"] == max_items


def test_story_schema_requires_three_compact_paragraphs() -> None:
    schema = background_story_service._background_story_schema_for_direction(
        {"valenceTarget": "positive"}
    )

    paragraphs = schema["properties"]["storyParagraphs"]
    assert "storyParagraphs" in schema["required"]
    assert "storyText" not in schema["properties"]
    assert paragraphs["minItems"] == 3
    assert paragraphs["maxItems"] == 3
    assert paragraphs["items"]["maxLength"] == 220


def test_story_payload_joins_structured_paragraphs_and_keeps_legacy_fallback() -> None:
    payload = {
        "title": "Три шага",
        "summary": "Олег решил задачу.",
        "storyParagraphs": [
            "Олег заметил закрытый проход.",
            "Он нашёл другой путь.",
            "К вечеру Олег добрался до укрытия.",
        ],
        "eventType": "journey",
        "valence": "positive",
        "tags": [],
        "statImpacts": [],
        "ragText": "Олег нашёл путь к укрытию.",
    }

    result = background_story_service._normalize_story_payload(payload)
    legacy = background_story_service._normalize_story_payload(
        {**payload, "storyParagraphs": None, "storyText": "Старый цельный текст."}
    )

    assert result.story_text == (
        "Олег заметил закрытый проход.\n\n"
        "Он нашёл другой путь.\n\n"
        "К вечеру Олег добрался до укрытия."
    )
    assert legacy.story_text == "Старый цельный текст."


class FakeBackgroundStoryCompletions:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = [content] if isinstance(content, str) else content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        content = self.contents[min(len(self.calls), len(self.contents) - 1)]
        self.calls.append(kwargs)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _call_by_schema(completions: FakeBackgroundStoryCompletions, schema_name: str) -> dict:
    for call in completions.calls:
        if call.get("response_format", {}).get("json_schema", {}).get("name") == schema_name:
            return call
    raise AssertionError(f"schema call not found: {schema_name}")


def _pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "name": "Олег",
            "description": "чел с листом вместо лица",
            "stage": "baby",
            "mood": "happy",
            "stats": {
                "hunger": 96,
                "happiness": 100,
                "energy": 71,
            },
            "characterBible": {
                "identity": {
                    "name": "Листик",
                    "species": "Чел с листом вместо лица",
                    "one_liner": ("Лист на лице стук, сердце в растениях"),
                },
                "inner_state": {
                    "core_want": ("ощущать тепло и заботу через листовую чешую"),
                    "fears": ["ветер слишком сильный"],
                },
                "lore": {
                    "home": {"place": "лесная поляна под кроной"},
                    "story_seeds": ["древний дуб отвечает шепотом"],
                },
                "extensions": {
                    "lite_overlay": {
                        "facts": [
                            {
                                "sphere": "appearance",
                                "kind": "appearance_fact",
                                "text": ("Листики выпускают запахи-сигналы опасности."),
                            }
                        ]
                    },
                    "story_library_overlay": {
                        "bricks": [
                            {
                                "name": "Каменная тропа",
                                "text": "На тропе живет стеклянный шорох.",
                            }
                        ]
                    },
                },
            },
            "assetImages": {
                "baby": {
                    "happy": "https://example.com/oleg-baby-happy.png",
                },
                "teen": {
                    "idle": "https://example.com/static/generated/asset-1/teen-idle.png?v=7",
                },
            },
        }
    )


def test_background_story_image_extracts_scene_and_uses_openai_image_path(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    completions = FakeBackgroundStoryCompletions(
        json.dumps(
            {
                "scene": (
                    "Олег стоит под древним дубом, лист на его лице светится, "
                    "а вокруг кружатся теплые золотые знаки."
                ),
                "poseFamily": "reaching_or_manipulating",
                "heroPose": (
                    "Олег наклоняет корпус к дубу, переносит вес на переднюю лапу "
                    "и тянется второй лапой к светящемуся листу."
                ),
                "camera": "Низкая камера в три четверти, средний общий план.",
            }
        )
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def fake_generate_image_bytes(prompt: str, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return b"story-image"

    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(
            ai_provider="openai",
            openai_chat_model="gpt-5.5",
            openai_chat_timeout_seconds=90,
            openai_chat_reasoning_effort=None,
            backend_public_url=None,
            webapp_url=None,
        ),
    )
    monkeypatch.setattr(background_story_service, "get_openai_client", lambda: client)
    monkeypatch.setattr(background_story_service, "generate_image_bytes", fake_generate_image_bytes)
    story = background_story_service.BackgroundStoryResult(
        title="След под кроной",
        summary="Олег нашел теплый знак под древним дубом.",
        story_text="Олег заметил, что древний дуб отвечает шепотом листа.",
        event_type="discovery",
        valence="positive",
        tags=("дуб", "лист"),
        rag_text="Олег нашел знак под дубом.",
        story_library_patch=None,
        lite_overlay_patch=None,
        recent_story_event=None,
        prompt_debug=[],
    )

    image_bytes = background_story_service.generate_background_story_image_bytes(
        pet=_pet(),
        story=story,
    )

    assert image_bytes == b"story-image"
    scene_request = _call_by_schema(completions, "background_story_image_scene")
    scene_prompt = scene_request["messages"][1]["content"]
    assert background_story_service.BACKGROUND_STORY_IMAGE_SCENE_INSTRUCTION in scene_prompt
    assert "человек остаётся человеком, дух — духом" in scene_prompt
    assert "не копируй им автоматически" in scene_prompt
    assert "Олег заметил, что древний дуб отвечает шепотом листа" in scene_prompt
    assert calls[0]["label"] == "background_story/image"
    assert calls[0]["input_references"] == [
        {
            "type": "image_url",
            "image_url": {
                "url": ("https://example.com/static/generated/asset-1/teen-idle-character.png?v=7")
            },
        }
    ]
    assert "TRANSLATE THE REFERENCE INTO THE SAME STOP-MOTION WORLD" in calls[0]["prompt"]
    assert "VISUAL_CHARACTER_STYLE:" not in calls[0]["prompt"]
    prompt = str(calls[0]["prompt"])
    assert "лист на его лице светится" in prompt
    assert "древний дуб отвечает шепотом листа" not in prompt
    assert "След под кроной" not in prompt
    assert "чел с листом вместо лица" not in prompt
    assert "GENERATION_PROFILE" not in prompt
    assert "Тип события" not in prompt
    assert "Теги:" not in prompt
    assert "Дизайн персонажа" not in prompt
    assert "Стадия:" not in prompt
    assert "Опорный дизайн" not in prompt
    assert "Tone style" not in prompt
    assert "Базовая визуальная рамка" not in prompt
    normalized_shared_style = " ".join(background_story_service.VISUAL_CHARACTER_STYLE.split())
    assert normalized_shared_style not in prompt
    assert "collectible designer art toy" not in prompt
    assert "pure white seamless background" not in prompt
    assert "MAIN CHARACTER — TRANSLATE THE REFERENCE INTO THE SAME STOP-MOTION WORLD" in prompt
    assert "Do not turn every other character into a copy of the hero" in prompt
    assert "stylized humans, animals, spirits" in prompt
    assert "Humans must remain recognizably human" in prompt
    assert "They may be cheerful, alert, busy" in prompt
    assert "Do not give everyone the hero's sleepy eyes" in prompt
    assert "ENVIRONMENT FRAME — APPLY ONLY TO THE WORLD AROUND THE CHARACTERS" in prompt
    assert "must never redesign, replace or restyle" in prompt
    assert "handcrafted stop-motion miniature set" in prompt
    assert "painted wood, cardboard, paper, fabric, matte resin, clay" in prompt
    assert "Japanese-inspired minimalism" in prompt
    assert "three to five large readable environmental shapes" in prompt
    assert "uncluttered open area around the main action" in prompt
    assert "near-symmetrical composition when the story action allows it" in prompt
    assert "soft diffused practical lighting" in prompt
    assert "without overriding the actual emotion or valence" in prompt
    assert "Detail hierarchy: highest detail on the main character" in prompt
    assert "Do not invent background people, animals, vehicles" in prompt
    assert "selectively crafted detail" in prompt
    assert "Avoid micro-detail everywhere" in prompt
    assert len(prompt) <= background_story_service.BACKGROUND_STORY_IMAGE_PROMPT_MAX_CHARS
    assert "Листики выпускают запахи-сигналы опасности" not in prompt
    assert "Pose family: reaching_or_manipulating" in prompt
    assert "переносит вес на переднюю лапу" in prompt
    assert "Низкая камера в три четверти" in prompt
    assert story.prompt_debug[0]["label"] == "background_story/image_scene"


def test_background_story_image_pose_options_exclude_three_recent_families() -> None:
    recent_events = [
        {"imagePoseFamily": "locomotion"},
        {"imagePoseFamily": "crouching_observation"},
        {"imagePoseFamily": "reaching_or_manipulating"},
    ]

    available = background_story_service._available_background_story_pose_families(recent_events)

    assert "locomotion" not in available
    assert "crouching_observation" not in available
    assert "reaching_or_manipulating" not in available
    assert "physical_interaction" in available


def test_background_story_image_passes_current_sprite_reference_to_image_helper(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []
    pet = _pet().model_copy(
        update={
            "assetImages": {
                "teen": {
                    "idle": "/static/generated/pets/teen-idle.png?v=7",
                },
            }
        }
    )

    def fake_generate_image_bytes(prompt: str, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return b"story-image"

    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(
            backend_public_url="https://gigagochi.serega.works",
            webapp_url=None,
        ),
    )
    monkeypatch.setattr(
        background_story_service,
        "extract_background_story_image_scene",
        lambda story, **_: "Олег стоит под древним дубом с теплым знаком на листе.",
    )
    monkeypatch.setattr(background_story_service, "generate_image_bytes", fake_generate_image_bytes)
    story = background_story_service.BackgroundStoryResult(
        title="След под кроной",
        summary="Олег нашел теплый знак под древним дубом.",
        story_text="Олег заметил, что древний дуб отвечает шепотом листа.",
        event_type="discovery",
        valence="positive",
        tags=("дуб", "лист"),
        rag_text="Олег нашел знак под дубом.",
        story_library_patch=None,
        lite_overlay_patch=None,
        recent_story_event=None,
        prompt_debug=[],
    )

    image_bytes = background_story_service.generate_background_story_image_bytes(
        pet=pet,
        story=story,
    )

    assert image_bytes == b"story-image"
    assert calls[0]["input_references"] == [
        {
            "type": "image_url",
            "image_url": {
                "url": (
                    "https://gigagochi.serega.works/static/generated/pets/"
                    "teen-idle-character.png?v=7"
                )
            },
        }
    ]
    prompt = str(calls[0]["prompt"])
    assert "Олег стоит под древним дубом" in prompt
    assert "чел с листом вместо лица" not in prompt
    assert len(prompt) <= background_story_service.BACKGROUND_STORY_IMAGE_PROMPT_MAX_CHARS


def test_background_story_image_prompt_keeps_full_style_for_long_scene() -> None:
    prompt = background_story_service.build_background_story_image_prompt(
        scene="Старинная башня, бумажные деревья и мягкий туман. " * 30,
    )

    assert len(prompt) <= background_story_service.BACKGROUND_STORY_IMAGE_PROMPT_MAX_CHARS
    assert prompt.endswith("quietly magical.")


def test_background_story_isolated_identity_prompt_scopes_character_style() -> None:
    prompt = background_story_service.build_background_story_image_prompt(
        scene="Олег идёт по бумажному лесу.",
        mode="isolated_identity",
    )

    assert "identity reference, not as a composition or" in prompt
    assert "Ignore and replace any white, transparent or studio background" in prompt
    assert "VISUAL_CHARACTER_STYLE:" not in prompt
    assert "handcrafted stop-motion miniature set" in prompt


def test_background_story_full_stop_motion_prompt_restylizes_whole_scene() -> None:
    prompt = background_story_service.build_background_story_image_prompt(
        scene="Олег идёт по бумажному лесу.",
        mode="full_stop_motion",
        pose_family="locomotion",
        hero_pose="Олег шагает через ручей, балансируя с разведёнными лапами.",
        camera="Боковой общий план на уровне воды.",
    )

    assert "TRANSLATE THE REFERENCE INTO THE SAME STOP-MOTION WORLD" in prompt
    assert "made by the same miniature workshop" in prompt
    assert "Avoid photoreal fur, skin, vegetation or stone" in prompt
    assert "VISUAL_CHARACTER_STYLE:" not in prompt
    assert "Pose family: locomotion" in prompt
    assert "балансируя с разведёнными лапами" in prompt
    assert "Боковой общий план" in prompt
    assert "reference stance must not survive" in prompt


def test_background_story_image_prompt_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported background story image prompt mode"):
        background_story_service.build_background_story_image_prompt(
            scene="Олег идёт по лесу.",
            mode="unknown",  # type: ignore[arg-type]
        )


def test_background_story_image_requires_character_reference() -> None:
    pet = _pet().model_copy(update={"assetImages": None})
    story = background_story_service.BackgroundStoryResult(
        title="След под кроной",
        summary="Олег нашел теплый знак.",
        story_text="Олег нашел теплый знак под древним дубом.",
        event_type="discovery",
        valence="positive",
        tags=("дуб",),
        rag_text="Олег нашел знак.",
        story_library_patch=None,
        lite_overlay_patch=None,
        recent_story_event=None,
        prompt_debug=[],
    )

    with pytest.raises(RuntimeError, match="BACKGROUND_STORY_IMAGE_REFERENCE_MISSING"):
        background_story_service.generate_background_story_image_bytes(pet=pet, story=story)


def test_generate_background_story_stores_recent_event_without_lite_patch(monkeypatch) -> None:
    routing_content = json.dumps(
        {
            "sources": {
                "worldContext": {"enabled": False, "query": ""},
                "characterProfile": {"enabled": True, "query": "описание персонажа"},
                "userMemory": {"enabled": False, "query": ""},
                "chatHistory": {"enabled": False, "query": ""},
                "recentReplies": {"enabled": False, "query": ""},
            },
            "reason": "Нужен профиль персонажа.",
        },
        ensure_ascii=False,
    )
    content = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Налет стеклянных улиток",
            "summary": ("На Олега напали стеклянные улитки у лесной миски."),
            "storyParagraphs": [
                "У лесной миски Олег услышал хруст: стеклянные улитки поползли к его листу.",
                "Он отвёл их от миски светом листа.",
                "Улитки ушли, но Олег выбился из сил.",
            ],
            "eventType": "attack",
            "valence": "negative",
            "tags": ["лес", "улитки"],
            "statImpacts": [
                {
                    "stat": "energy",
                    "amount": -25,
                    "reason": "Стеклянные улитки повредили лист Олега.",
                }
            ],
            "ragText": (
                "На Олега у лесной миски напали стеклянные улитки, "
                "охотившиеся за запахами-сигналами листа."
            ),
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions([routing_content, content])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )

    result = background_story_service.generate_background_story(
        pet=_pet(),
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    assert result.title == "Налет стеклянных улиток"
    assert result.event_type == "attack"
    assert result.story_text.count("\n\n") == 2
    assert result.story_library_patch is None
    assert result.lite_overlay_patch is None
    assert result.recent_story_event is not None
    assert result.plot_mode in background_story_service.STORY_DIRECTION_SPECS
    assert result.setting_class
    assert result.opposition_class
    assert result.resolution_mode
    assert result.valence_target in background_story_service.STORY_VALENCE_WEIGHTS
    assert any(item.get("event") == "background_story_direction" for item in result.prompt_debug)
    assert result.stat_impact == {
        "stat": "energy",
        "amount": -25,
        "reason": "Стеклянные улитки повредили лист Олега.",
    }
    assert list(result.stat_impacts) == [result.stat_impact]
    assert result.recent_story_event["summary"] == (
        "На Олега напали стеклянные улитки у лесной миски."
    )
    request = _call_by_schema(completions, "background_story")
    assert request["response_format"]["json_schema"]["name"] == "background_story"
    assert request["reasoning_effort"] == "medium"
    story_schema = request["response_format"]["json_schema"]["schema"]
    assert "causalPlan" in story_schema["required"]
    assert story_schema["properties"]["causalPlan"]["required"] == [
        "setup",
        "problem",
        "action",
        "whyActionWorks",
        "consequence",
    ]
    assert story_schema["properties"]["valence"]["enum"] == [result.valence_target]
    prompt = request["messages"][1]["content"]
    assert "наевшийся" in prompt
    assert "счастливый" in prompt
    assert "крепкое здоровье" in prompt
    assert '"stats"' not in prompt
    assert '"голод"' in prompt
    assert '"здоровье"' in prompt
    assert '"value": 96' in prompt
    assert '"value": 100' in prompt
    assert '"value": 71' in prompt
    assert "Листики выпускают запахи-сигналы опасности." in prompt
    assert len(completions.calls) == 4
    assert _call_by_schema(completions, "background_story_coherence_check")
    assert _call_by_schema(completions, "background_story_aftermath_extraction")


def test_background_story_accepts_explicit_recovery_stat_change() -> None:
    result = background_story_service._normalize_story_payload(
        {
            "title": "Теплый привал",
            "summary": "Олег отдохнул у теплого камня и восстановил силы.",
            "storyText": "Олег выспался у теплого камня, подлечился и снова набрал сил.",
            "eventType": "recovery",
            "valence": "positive",
            "tags": ["отдых"],
            "statImpacts": [
                {
                    "stat": "energy",
                    "amount": 18,
                    "reason": "Отдых восстановил силы Олега.",
                }
            ],
            "ragText": "Олег восстановил силы у теплого камня.",
        }
    )

    assert result.stat_impacts == (
        {
            "stat": "energy",
            "amount": 18,
            "reason": "Отдых восстановил силы Олега.",
        },
    )


def test_background_story_retries_once_after_incoherent_verdict(monkeypatch) -> None:
    bad_story = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Три слова",
            "summary": "Необъяснённые слова открыли переправу.",
            "storyParagraphs": ["Кошка пришла к реке.", "Она сказала три слова.", "Путь открылся."],
            "eventType": "encounter",
            "valence": "neutral",
            "tags": [],
            "statImpacts": [],
            "ragText": "Кошка открыла переправу словами.",
        },
        ensure_ascii=False,
    )
    verdict = json.dumps(
        {
            "coherent": True,
            "eventful": False,
            "patternClass": "micro_clue_unlock",
            "issues": ["Не объяснено, почему слова открывают переправу."],
            "retryInstruction": (
                "Покажи наблюдаемое препятствие и действие, которое физически его устраняет."
            ),
        },
        ensure_ascii=False,
    )
    repaired_story = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Ветка у затвора",
            "summary": "Кошка убрала ветку из водяного затвора и опустила мостки.",
            "storyParagraphs": [
                "Ветка заклинила водяной затвор у переправы.",
                "Кошка вытащила ветку, и освобождённый поток повернул колесо.",
                "Колесо опустило мостки, поэтому Кошка перешла реку.",
            ],
            "eventType": "encounter",
            "valence": "neutral",
            "tags": [],
            "statImpacts": [],
            "ragText": "Кошка освободила затвор и опустила мостки.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions([bad_story, verdict, repaired_story])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        background_story_service,
        "context_source_mode",
        lambda surface, source: "disabled",
    )
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )

    result = background_story_service.generate_background_story(
        pet=_pet(),
        client=client,
        model="test-model",
        timeout=10,
    )

    assert result.title == "Ветка у затвора"
    story_calls = [
        call
        for call in completions.calls
        if call["response_format"]["json_schema"]["name"] == "background_story"
    ]
    assert len(story_calls) == 2
    assert "QUALITY_RETRY" in story_calls[1]["messages"][1]["content"]


def test_background_story_keeps_stat_impacts_without_lexical_filter() -> None:
    result = background_story_service._normalize_story_payload(
        {
            "title": "Еда у теплой решетки",
            "summary": "Кошка съела несколько крекеров и перестала чувствовать себя одинокой.",
            "storyText": (
                "Кошка съела несколько крекеров, согрелась у решетки "
                "и почувствовала себя спокойнее."
            ),
            "eventType": "recovery",
            "valence": "positive",
            "tags": ["еда", "отдых"],
            "statImpacts": [
                {"stat": "hunger", "amount": 8, "reason": "Кошка съела крекеры."},
                {
                    "stat": "happiness",
                    "amount": 5,
                    "reason": "Тепло и отдых успокоили кошку.",
                },
            ],
            "ragText": "Кошка поела и успокоилась.",
        }
    )

    assert [impact["stat"] for impact in result.stat_impacts] == ["hunger", "happiness"]
    assert result.stat_validation == {"dropped": False, "reason": ""}


def test_background_story_profile_toggle_controls_description() -> None:
    source_flags = {
        "characterProfile": False,
        "stateParams": False,
        "liteOverlay": False,
        "storyOverlay": False,
        "userMemory": False,
        "chatHistory": False,
        "recentReplies": False,
    }

    without_profile = background_story_service.character_dossier_for_background_story(
        pet=_pet(),
        source_flags=source_flags,
        include_story_library=False,
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
    )
    with_profile = background_story_service.character_dossier_for_background_story(
        pet=_pet(),
        source_flags={**source_flags, "characterProfile": True},
        include_story_library=False,
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
    )

    assert '"identitySeed": "Олег: Чел с листом вместо лица"' in without_profile
    assert '"description": "чел с листом вместо лица"' not in without_profile
    assert '"identityDescription": "чел с листом вместо лица"' in with_profile


def test_background_story_dossier_does_not_use_bible_as_plot_source() -> None:
    pet = _pet().model_copy(
        update={
            "name": "Мяу",
            "description": "кошка-волшебница",
            "characterBible": {
                "identity": {"name": "Мяу", "species": "кошка-волшебница"},
                "genesis": {
                    "character_trait": "смелая",
                    "story_engine": "ритуалы с маленькими реликвиями",
                },
                "inner_state": {
                    "comfort_actions": ["шепчет травинке"],
                    "core_want": "собирать мелкие рисунки",
                },
                "world": {
                    "objects": ["травинка", "мелкая плита"],
                    "routines": ["рисует знаки"],
                    "story_seeds": ["щель открывается после шёпота"],
                },
                "extensions": {
                    "lite_overlay": {
                        "facts": [
                            {
                                "sphere": "appearance",
                                "text": "Мяу временно прихрамывает.",
                            },
                            {
                                "sphere": "relationship",
                                "text": "Мяу обещала назвать ворота первому путнику.",
                            },
                        ]
                    }
                },
            },
        }
    )

    dossier = background_story_service.character_dossier_for_background_story(
        pet=pet,
        source_flags={
            "characterProfile": False,
            "stateParams": False,
            "liteOverlay": False,
            "storyOverlay": False,
            "userMemory": False,
            "chatHistory": False,
            "recentReplies": False,
        },
        include_story_library=False,
    )

    assert '"name": "Мяу"' in dossier
    assert '"species": "кошка-волшебница"' in dossier
    assert '"temperament": "смелая"' in dossier
    assert "временно прихрамывает" in dossier
    assert "назвать ворота" not in dossier
    for forbidden in ("ритуалы", "травинка", "рисует знаки", "щель открывается"):
        assert forbidden not in dossier


def test_micro_clue_unlock_is_rejected_outside_puzzle_incident() -> None:
    payload = {
        "storyParagraphs": [
            "Кошка заметила меловую метку.",
            "Она положила травинку у щели.",
            "В стене открылся скрытый ход.",
        ]
    }

    assert background_story_service._has_forbidden_micro_unlock_pattern(
        payload,
        {"incidentClass": "unexpected_opportunity"},
    )
    assert not background_story_service._has_forbidden_micro_unlock_pattern(
        payload,
        {"incidentClass": "puzzle_discovery"},
    )


def test_background_story_resolves_name_from_character_identity() -> None:
    pet = _pet().model_copy(
        update={
            "name": None,
            "characterBible": {
                "identity": {"name": "Луна", "species": "кошка-волшебница"},
            },
        }
    )

    dossier = background_story_service.character_dossier_for_background_story(
        pet=pet,
        source_flags={
            "characterProfile": False,
            "stateParams": True,
            "liteOverlay": False,
            "storyOverlay": False,
            "userMemory": False,
            "chatHistory": False,
            "recentReplies": False,
        },
        include_story_library=False,
        now_iso="2026-07-10T06:00:00Z",
        timezone="Europe/Moscow",
    )

    assert '"identitySeed": "Луна: кошка-волшебница"' in dossier
    assert '"name": "Луна"' in dossier


def test_background_story_context_sources_policy_controls_dossier(monkeypatch) -> None:
    content = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Тихий налет",
            "summary": "На Олега напали.",
            "storyText": "На Олега напали у миски.",
            "eventType": "attack",
            "valence": "negative",
            "tags": [],
            "statImpacts": [],
            "ragText": "На Олега напали у миски.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    memory_context = LocalPetMemoryContext.model_validate(
        {
            "summary": "Сергей любит стеклянный шорох.",
            "userProfile": "Сергей собирает лунные листья.",
            "relevantMemories": [
                {
                    "id": "m1",
                    "kind": "user_fact",
                    "text": "Сергей принес листовой амулет.",
                }
            ],
        }
    )
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(
        background_story_service,
        "context_source_enabled",
        lambda surface, source, *, router_enabled=None, auto_default=False: False,
    )

    background_story_service.generate_background_story(
        pet=_pet(),
        memory_context=memory_context,
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert "Олег: Чел с листом вместо лица" in prompt
    assert '"description": "чел с листом вместо лица"' not in prompt
    assert "params" not in prompt
    assert "наевшийся" not in prompt
    assert "Лист на лице стук" not in prompt
    assert "Листики выпускают запахи-сигналы опасности." in prompt
    assert "стеклянный шорох" not in prompt
    assert "Сергей принес листовой амулет" not in prompt
    assert "Каменная тропа" not in prompt


def test_background_story_auto_sources_use_context_router(monkeypatch) -> None:
    routing_content = json.dumps(
        {
            "sources": {
                "worldContext": {"enabled": True, "query": "лор мира"},
                "characterProfile": {"enabled": False, "query": ""},
                "userMemory": {"enabled": False, "query": ""},
                "chatHistory": {"enabled": False, "query": ""},
                "recentReplies": {"enabled": False, "query": ""},
            },
            "reason": "Нужен только лор мира.",
        },
        ensure_ascii=False,
    )
    story_content = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Световая капля",
            "summary": "На Олега напала световая капля.",
            "storyText": "На Олега напала световая капля у тропы.",
            "eventType": "attack",
            "valence": "negative",
            "tags": ["свет"],
            "statImpacts": [],
            "ragText": "На Олега напала световая капля.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions([routing_content, story_content])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    memory_context = LocalPetMemoryContext.model_validate(
        {
            "summary": "Сергей любит стеклянный шорох.",
            "userProfile": "Сергей собирает лунные листья.",
            "relevantMemories": [
                {
                    "id": "m1",
                    "kind": "user_fact",
                    "text": "Сергей принес листовой амулет.",
                }
            ],
        }
    )

    def fake_mode(surface, source):
        if surface == "backgroundStory" and source in {
            "characterProfile",
            "liteOverlay",
            "storyLibrary",
            "storyOverlay",
            "userMemory",
        }:
            return "auto"
        return "disabled"

    def fake_enabled(surface, source, *, router_enabled=None, auto_default=False):
        mode = fake_mode(surface, source)
        if mode == "disabled":
            return False
        if mode == "always":
            return True
        return router_enabled if router_enabled is not None else auto_default

    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(background_story_service, "context_source_mode", fake_mode)
    monkeypatch.setattr(background_story_service, "context_source_enabled", fake_enabled)
    captured_story_queries: list[str | None] = []

    def fake_global_story_briefs(*, pet, query=None):
        captured_story_queries.append(query)
        return [
            {
                "name": "Кристаллическая капля",
                "text": "Капля удерживает чужой свет на тропе.",
            }
        ]

    monkeypatch.setattr(
        background_story_service,
        "_global_story_briefs",
        fake_global_story_briefs,
    )

    result = background_story_service.generate_background_story(
        pet=_pet(),
        memory_context=memory_context,
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    assert result.title == "Световая капля"
    assert len(completions.calls) == 4
    assert completions.calls[0]["response_format"]["json_schema"]["name"] == (
        "background_story_context_routing"
    )
    assert completions.calls[1]["response_format"]["json_schema"]["name"] == "background_story"
    assert completions.calls[2]["response_format"]["json_schema"]["name"] == (
        "background_story_coherence_check"
    )
    assert completions.calls[3]["response_format"]["json_schema"]["name"] == (
        "background_story_aftermath_extraction"
    )
    routing_payload = json.loads(completions.calls[0]["messages"][1]["content"])
    assert "eventType" not in routing_payload
    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert captured_story_queries == ["лор мира"]
    assert "Кристаллическая капля" in prompt
    assert "Каменная тропа" not in prompt
    assert "Лист на лице стук" not in prompt
    assert "Листики выпускают запахи-сигналы опасности." in prompt
    assert "Сергей принес листовой амулет" not in prompt


def test_background_story_never_uses_previous_generated_stories(monkeypatch) -> None:
    content = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Новая история",
            "summary": "На Олега напали у миски.",
            "storyText": "На Олега напали у миски.",
            "eventType": "attack",
            "valence": "negative",
            "tags": [],
            "statImpacts": [],
            "ragText": "На Олега напали у миски.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def fake_enabled(surface, source, *, router_enabled=None, auto_default=False):
        if surface == "backgroundStory" and source == "storyOverlay":
            return True
        if surface == "backgroundStory" and source == "storyLibrary":
            return False
        return auto_default

    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(background_story_service, "context_source_enabled", fake_enabled)

    background_story_service.generate_background_story(
        pet=_pet(),
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert "recentStoryBricks" not in prompt
    assert "Каменная тропа" not in prompt


def test_background_story_uses_recent_events_only_as_anti_repeat(monkeypatch) -> None:
    content = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Новая случайность",
            "summary": "Олег споткнулся у миски.",
            "storyText": "Олег споткнулся у миски и поднялся.",
            "eventType": "accident",
            "valence": "mixed",
            "tags": ["случайность"],
            "statImpacts": [],
            "ragText": "Олег споткнулся у миски.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(
        background_story_service,
        "context_source_mode",
        lambda surface, source: "disabled",
    )

    background_story_service.generate_background_story(
        pet=_pet(),
        recent_story_events=[
            {
                "title": "Искра у миски",
                "summary": "Олег уже споткнулся о мягкий камень у миски.",
                "eventType": "accident",
                "valence": "mixed",
                "tags": ["искра", "падение"],
                "participants": ["Олег"],
                "actions": ["споткнулся"],
                "objects": ["мягкий камень"],
                "location": "миска",
                "outcome": "поднялся сам",
            }
        ],
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    request = _call_by_schema(completions, "background_story")
    system_prompt = request["messages"][0]["content"]
    prompt = request["messages"][1]["content"]
    assert "GENERATION_PROFILE" not in system_prompt
    assert "не задают детский тон" in system_prompt
    assert "Заполни causalPlan" in system_prompt
    assert "storyParagraphs только по этому плану" in system_prompt
    assert "первый абзац объединяет завязку и проблему" in system_prompt
    assert "Не более одного нового магического эффекта" in system_prompt
    assert "ОБЩАЯ БИБЛИЯ МИРА" in system_prompt
    assert "древние леса, чащи и туманные луга" in system_prompt
    assert "Современная бытовая инфраструктура не является фоном" in system_prompt
    assert "материальная потеря, предмет и травма не обязательны" in system_prompt
    assert "STORY_DIRECTION" in prompt
    assert "Не своди каждый сюжет к физической опасности" in prompt
    assert "Сравни новую историю с ANTI_REPEAT" in prompt
    assert "Центральное событие заверши внутри эпизода" in prompt
    assert "ровно 3 смысловых абзаца" in prompt
    assert "4–5 предложений" in prompt
    assert "ANTI_REPEAT" in prompt
    assert "Используй список только как запрет на повтор" in prompt
    assert "название: Искра у миски" in prompt
    assert "ключевые мотивы: искра, падение" in prompt
    assert "тип: accident" not in prompt
    assert "тон исхода: смешанный" not in prompt
    assert "предметы: мягкий камень" not in prompt
    assert "Олег уже споткнулся о мягкий камень" not in prompt
    assert "действия: споткнулся" not in prompt
    assert "развязка: поднялся сам" not in prompt


def test_background_story_aftermath_keeps_episode_but_ignores_ephemeral_lite_fact(
    monkeypatch,
) -> None:
    story_content = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Меловая тень",
            "summary": "На Олега напала меловая тень.",
            "storyText": "На Олега напала меловая тень и исчезла.",
            "eventType": "attack",
            "valence": "negative",
            "tags": ["тень"],
            "statImpacts": [],
            "ragText": "На Олега напала меловая тень.",
        },
        ensure_ascii=False,
    )
    aftermath_content = json.dumps(
        {
            "facts": [
                {
                    "sphere": "world",
                    "kind": "world_fact",
                    "text": "На Олега однажды напала меловая тень.",
                    "pathHint": "lite_overlay.spheres.world",
                    "source": "background_story_aftermath",
                    "confidence": 0.4,
                }
            ],
            "recentEvent": {
                "summary": "На Олега напала меловая тень и исчезла.",
                "eventType": "attack",
                "participants": ["меловая тень", "Олег"],
                "actions": ["нападение"],
                "objects": [],
                "location": "",
                "outcome": "тень исчезла",
                "compactText": "На Олега напала меловая тень и исчезла.",
                "canonicalFacts": ["на Олега напала меловая тень"],
                "statusChanges": [],
            },
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions([story_content, aftermath_content])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(
        background_story_service,
        "context_source_mode",
        lambda surface, source: "disabled",
    )

    result = background_story_service.generate_background_story(
        pet=_pet(),
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    assert result.story_library_patch is None
    assert result.lite_overlay_patch is None
    assert result.recent_story_event is not None
    assert result.recent_story_event["summary"] == ("На Олега напала меловая тень и исчезла.")
    assert result.recent_story_event["participants"] == ["меловая тень", "Олег"]
    assert result.recent_story_event["canonicalFacts"] == ["на Олега напала меловая тень"]


def test_background_story_aftermath_persists_acquired_item_and_relationship(
    monkeypatch,
) -> None:
    story_content = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Искра фонарщика",
            "summary": "Олег помог фонарщику и получил теплую искру.",
            "storyText": (
                "Олег освободил фонарщика из веток. Фонарщик подарил ему теплую искру, "
                "которую Олег сохранил у себя."
            ),
            "eventType": "meeting",
            "valence": "positive",
            "tags": ["фонарщик", "искра"],
            "statImpacts": [],
            "ragText": "Олег встретил фонарщика и получил теплую искру.",
        },
        ensure_ascii=False,
    )
    aftermath_content = json.dumps(
        {
            "facts": [
                {
                    "sphere": "world",
                    "kind": "world_fact",
                    "text": "У Олега есть теплая искра — подарок фонарщика.",
                    "pathHint": "lite_overlay.spheres.world",
                    "source": "background_story_aftermath",
                    "confidence": 0.95,
                },
                {
                    "sphere": "relationship",
                    "kind": "relationship_fact",
                    "text": "Олег знаком с фонарщиком, которому помог выбраться из веток.",
                    "pathHint": "lite_overlay.spheres.relationship",
                    "source": "background_story_aftermath",
                    "confidence": 0.9,
                },
            ],
            "recentEvent": {
                "summary": "Олег помог фонарщику и получил теплую искру.",
                "eventType": "meeting",
                "participants": ["Олег", "фонарщик"],
                "actions": ["Олег освободил фонарщика"],
                "objects": ["теплая искра"],
                "location": "колючие ветки",
                "outcome": "Теплая искра осталась у Олега.",
                "compactText": "Олег спас фонарщика и сохранил подаренную теплую искру.",
                "canonicalFacts": ["фонарщик подарил Олегу теплую искру"],
                "statusChanges": [{"entity": "теплая искра", "state": "owned", "owner": "Олег"}],
            },
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions([story_content, aftermath_content])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(
        background_story_service,
        "context_source_mode",
        lambda surface, source: "disabled",
    )

    result = background_story_service.generate_background_story(
        pet=_pet(),
        client=client,
        model="test-model",
        timeout=10,
    )

    assert result.lite_overlay_patch is not None
    assert len(result.lite_overlay_patch["facts"]) == 2
    assert result.recent_story_event is not None
    assert result.recent_story_event["objects"] == ["теплая искра"]
    assert result.recent_story_event["statusChanges"] == [
        {"entity": "теплая искра", "state": "owned", "owner": "Олег"}
    ]


def test_background_story_uses_snapshot_history_when_story_toggles_allow(
    monkeypatch,
) -> None:
    content = json.dumps(
        {
            "causalPlan": TEST_CAUSAL_PLAN,
            "title": "Налет из прошлой темы",
            "summary": "На Олега напали после разговора о тропе.",
            "storyText": "На Олега напали у тропы после старого разговора.",
            "eventType": "attack",
            "valence": "negative",
            "tags": ["тропа"],
            "statImpacts": [],
            "ragText": "На Олега напали у тропы.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    history = [
        LocalChatHistoryItem(role="user", text="Помнишь стеклянную тропу?"),
        LocalChatHistoryItem(role="pet", text="Я уже слышал ее шорох."),
    ]

    def fake_enabled(surface, source, *, router_enabled=None, auto_default=False):
        if surface == "backgroundStory" and source in {"chatHistory", "recentReplies"}:
            return True
        if surface == "backgroundStory" and source in {
            "characterProfile",
            "stateParams",
            "liteOverlay",
            "storyLibrary",
            "storyOverlay",
            "userMemory",
        }:
            return False
        return auto_default

    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(background_story_service, "context_source_enabled", fake_enabled)

    background_story_service.generate_background_story(
        pet=_pet(),
        history=history,
        recent_replies=["Не буду снова говорить про светляков."],
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert "recentChatHistory" in prompt
    assert "Помнишь стеклянную тропу?" in prompt
    assert "recentReplies" in prompt
    assert "Не буду снова говорить про светляков." in prompt
