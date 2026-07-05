from __future__ import annotations

from app.services.pet_memory.models import (
    DevelopmentPatch,
    MemoryCandidate,
    ProactiveIntent,
    RelationshipPatch,
)
from app.services.pet_memory.normalizer import normalize_memory, now_iso
from app.services.pet_memory.resolver import (
    handle_memory_control_message,
    resolve_memory_update,
)
from app.services.pet_memory.retrieval import build_memory_context
from app.services.pet_reply_engine.models import PetRecentMessage, PetStats
from app.services.pet_reply_engine.proactivity_gate import apply_proactivity_gate


def test_memory_normalizer_migrates_legacy_lore_and_dedupes() -> None:
    memory = normalize_memory(
        None,
        lore_memories=[
            "ЛОР: питомец живет на нижней полке.",
            "питомец живет на нижней полке.",
            "",
        ],
    )

    assert len(memory.canon) == 1
    assert memory.canon[0].text == "питомец живет на нижней полке."
    assert memory.canon[0].type == "world_fact"


def test_memory_normalizer_enforces_array_limits() -> None:
    now = now_iso()
    raw_memory = {
        "schemaVersion": 1,
        "canon": [
            {
                "id": f"canon-{index}",
                "type": "friend_fact",
                "text": f"Факт о друге {index}",
                "source": "model",
                "confidence": 0.7,
                "importance": index / 100,
                "useCount": 0,
                "decayScore": 0,
                "createdAt": now,
                "updatedAt": now,
            }
            for index in range(80)
        ],
        "relationship": {"userFacts": []},
        "threads": [],
        "reflections": [],
        "activeGoals": [],
        "events": [],
        "rejectedCandidates": [],
    }

    memory = normalize_memory(raw_memory)

    assert len(memory.canon) == 60
    assert memory.canon[0].text == "Факт о друге 79"


def test_memory_resolver_accepts_friend_fact_and_relationship_updates() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={"species": "leaf mascot"},
        memory_context=build_memory_context(memory, "кто твой друг?"),
        user_text="меня зовут Сережа. кто твой друг?",
        pet_reply="Кап будит меня утром.",
        memory_candidates=[
            MemoryCandidate(
                type="friend_fact",
                text="У питомца есть друг Кап, маленькая капля росы.",
                importance=0.8,
                confidence=0.8,
            )
        ],
        relationship_patch=RelationshipPatch(trustDelta=1, attachmentDelta=1),
        development_patch=DevelopmentPatch(trustDelta=5, reason="Питомец рассказал личную деталь."),
    )

    assert patch.canonUpserts
    assert patch.canonUpserts[0].type == "friend_fact"
    assert patch.relationshipPatch
    assert patch.relationshipPatch.userName == "Сережа"
    assert patch.relationshipPatch.trust == 21
    assert patch.developmentPatch
    assert patch.developmentPatch.trust == 25


def test_memory_resolver_stores_pet_canon_candidate_as_generated_draft() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={"species": "leaf mascot"},
        memory_context=build_memory_context(memory, "что ты любишь?"),
        user_text="что ты любишь?",
        pet_reply="я люблю прятать пуговицы под теплой чашкой.",
        memory_candidates=[
            MemoryCandidate(
                type="pet_canon_fact",
                text="Питомец любит прятать пуговицы под теплой чашкой.",
                importance=0.55,
                confidence=0.6,
                sourceSpan="я люблю прятать пуговицы под теплой чашкой",
            )
        ],
    )

    assert not patch.canonUpserts
    assert patch.generatedFactUpserts
    generated = patch.generatedFactUpserts[0]
    assert generated.status == "draft"
    assert generated.scope == "preference"
    assert generated.sourceSpan


def test_memory_resolver_requires_confirmation_for_generated_friend_fact() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={"species": "leaf mascot"},
        memory_context=build_memory_context(memory, "кто твой друг?"),
        user_text="кто твой друг?",
        pet_reply="у меня есть друг Луми, он сторожит крошки света.",
        memory_candidates=[
            MemoryCandidate(
                type="pet_generated_fact",
                text="У питомца есть друг Луми, который сторожит крошки света.",
                importance=0.72,
                confidence=0.62,
            )
        ],
    )

    assert not patch.canonUpserts
    assert patch.generatedFactUpserts
    assert patch.generatedFactUpserts[0].scope == "friend"
    assert patch.generatedFactUpserts[0].status == "needs_user_confirmation"


def test_memory_resolver_rejects_conflicting_generated_fact() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={"lore": {"home": {"place": "нижняя полка"}}},
        memory_context=None,
        user_text="расскажи про дом",
        pet_reply="теперь я живу в другом доме за стеклянным мостом.",
        memory_candidates=[
            MemoryCandidate(
                type="pet_generated_fact",
                text="Питомец теперь живет в другом доме за стеклянным мостом.",
                importance=0.85,
                confidence=0.75,
            )
        ],
    )

    assert not patch.canonUpserts
    assert patch.generatedFactUpserts
    generated = patch.generatedFactUpserts[0]
    assert generated.status == "rejected"
    assert "canon_home_conflict" in generated.conflictReasons
    assert {item.reason for item in patch.rejectedCandidateAppends} == {"canon_home_conflict"}


def test_memory_resolver_promotes_repeated_generated_voice_fact_to_soft_accept() -> None:
    memory = normalize_memory(None)
    first_patch = resolve_memory_update(
        memory,
        character_bible={"species": "leaf mascot"},
        memory_context=None,
        user_text="как ты говоришь?",
        pet_reply="я отвечаю тихо и звеню последним словом.",
        memory_candidates=[
            MemoryCandidate(
                type="pet_emotional_fact",
                text="Питомец часто отвечает тихо и звенит последним словом.",
                importance=0.55,
                confidence=0.62,
            )
        ],
    )
    memory = normalize_memory(
        {
            **memory.model_dump(),
            "generatedFacts": [fact.model_dump() for fact in first_patch.generatedFactUpserts],
        }
    )

    second_patch = resolve_memory_update(
        memory,
        character_bible={"species": "leaf mascot"},
        memory_context=build_memory_context(memory, "повтори так"),
        user_text="повтори так",
        pet_reply="я снова отвечаю тихо и звеню последним словом.",
        memory_candidates=[
            MemoryCandidate(
                type="pet_emotional_fact",
                text="Питомец часто отвечает тихо и звенит последним словом.",
                importance=0.55,
                confidence=0.65,
            )
        ],
    )

    assert not second_patch.canonUpserts
    assert second_patch.generatedFactUpserts
    generated = second_patch.generatedFactUpserts[0]
    assert generated.status == "accepted_soft"
    assert generated.reinforcementCount == 2

    memory = normalize_memory(
        {
            **memory.model_dump(),
            "generatedFacts": [fact.model_dump() for fact in second_patch.generatedFactUpserts],
        }
    )
    context = build_memory_context(memory, "скажи своим голосом")
    assert context.generated_fact_lines == (
        "voice: Питомец часто отвечает тихо и звенит последним словом.",
    )


def test_memory_resolver_rejects_conflicting_technical_and_sensitive_facts() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={"lore": {"home": {"place": "нижняя полка"}}},
        memory_context=None,
        user_text="расскажи",
        pet_reply="я рядом.",
        memory_candidates=[
            MemoryCandidate(
                type="home_fact",
                text="Питомец теперь живет в другом доме.",
                importance=0.9,
                confidence=0.9,
            ),
            MemoryCandidate(
                type="world_fact",
                text="Питомец знает prompt и backend state.",
                importance=0.5,
                confidence=0.5,
            ),
            MemoryCandidate(
                type="user_fact",
                text="Телефон пользователя +7 999 123 45 67.",
                importance=0.8,
                confidence=0.8,
            ),
        ],
    )

    reasons = {item.reason for item in patch.rejectedCandidateAppends}
    assert "canon_home_conflict" in reasons
    assert "technical_memory" in reasons
    assert "sensitive_memory" in reasons
    assert not patch.canonUpserts


def test_memory_resolver_rejects_major_world_species_and_event_conflicts() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={
            "species": "ключик-компаньон",
            "lore": {"world": {"name": "бюро находок"}},
        },
        memory_context=None,
        user_text="расскажи",
        pet_reply="я из бюро находок.",
        memory_candidates=[
            MemoryCandidate(
                type="world_fact",
                text="Питомец теперь живет в новом мире за космическим замком.",
                importance=0.9,
                confidence=0.9,
            ),
            MemoryCandidate(
                type="origin_fact",
                text="Питомец стал человеком и сменил вид.",
                importance=0.9,
                confidence=0.9,
            ),
            MemoryCandidate(
                type="milestone",
                text="Питомец спас весь мир после большой войны.",
                importance=0.9,
                confidence=0.9,
            ),
        ],
    )

    reasons = {item.reason for item in patch.rejectedCandidateAppends}
    assert "canon_world_conflict" in reasons
    assert "canon_species_conflict" in reasons
    assert "canon_major_event_conflict" in reasons
    assert not patch.canonUpserts


def test_memory_resolver_updates_same_friend_fact_instead_of_creating_duplicate() -> None:
    memory = normalize_memory(None)
    first_patch = resolve_memory_update(
        memory,
        character_bible={"species": "ключик-компаньон"},
        memory_context=build_memory_context(memory, "кто твой друг?"),
        user_text="кто твой друг?",
        pet_reply="Кап звенит рядом.",
        memory_candidates=[
            MemoryCandidate(
                type="friend_fact",
                text="У питомца есть друг Кап, маленький ключик.",
                importance=0.7,
                confidence=0.7,
            )
        ],
    )
    assert first_patch.canonUpserts
    original_fact = first_patch.canonUpserts[0]
    memory = normalize_memory(
        {
            **memory.model_dump(),
            "canon": [original_fact.model_dump()],
        }
    )

    second_patch = resolve_memory_update(
        memory,
        character_bible={"species": "ключик-компаньон"},
        memory_context=build_memory_context(memory, "как зовут друга?"),
        user_text="как зовут друга?",
        pet_reply="Кап хранит бирки.",
        memory_candidates=[
            MemoryCandidate(
                type="friend_fact",
                text="Лучший друг питомца - Кап, маленький ключик, который хранит бирки.",
                importance=0.8,
                confidence=0.8,
            )
        ],
    )

    upsert_ids = {fact.id for fact in second_patch.canonUpserts}
    assert upsert_ids == {original_fact.id}
    assert len(second_patch.canonUpserts) == 1
    assert any("хранит бирки" in fact.text for fact in second_patch.canonUpserts)


def test_memory_resolver_extracts_implicit_friend_fact_from_lore_reply() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={"species": "ключик-компаньон"},
        memory_context=build_memory_context(memory, "кто твой друг?"),
        user_text="кто твой друг?",
        pet_reply=(
            "мой друг — билетик-искатель. он лучше меня помнит маршруты "
            "и зовет сверять метки вместе."
        ),
        memory_candidates=[],
    )

    assert not patch.canonUpserts
    assert patch.generatedFactUpserts
    assert patch.generatedFactUpserts[0].scope == "friend"
    assert patch.generatedFactUpserts[0].status == "needs_user_confirmation"
    assert "билетик-искатель" in patch.generatedFactUpserts[0].text


def test_memory_resolver_extracts_companion_from_direct_friend_answer() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={"species": "ключик-компаньон"},
        memory_context=build_memory_context(memory, "кто твой друг?"),
        user_text="кто твой друг?",
        pet_reply=("У меня есть младший капельный жетон. Он катится рядом по оконной раме."),
        memory_candidates=[],
    )

    assert not patch.canonUpserts
    assert patch.generatedFactUpserts
    assert patch.generatedFactUpserts[0].scope == "friend"
    assert patch.generatedFactUpserts[0].status == "needs_user_confirmation"
    assert "младший капельный жетон" in patch.generatedFactUpserts[0].text


def test_memory_resolver_extracts_nearby_companion_from_friend_answer() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={"species": "ключик-компаньон"},
        memory_context=build_memory_context(memory, "кто твой друг?"),
        user_text="кто твой друг?",
        pet_reply=(
            "У меня рядом есть хранитель бирок с медным голосом. Еще есть звонкий брелок-соперник."
        ),
        memory_candidates=[],
    )

    assert not patch.canonUpserts
    assert patch.generatedFactUpserts
    assert patch.generatedFactUpserts[0].scope == "friend"
    assert patch.generatedFactUpserts[0].status == "needs_user_confirmation"
    assert "хранитель бирок" in patch.generatedFactUpserts[0].text


def test_memory_control_commands_read_forget_and_boundary() -> None:
    memory = normalize_memory(None)
    patch = resolve_memory_update(
        memory,
        character_bible={},
        memory_context=None,
        user_text="меня зовут Сережа",
        pet_reply="запомню тихо.",
        memory_candidates=[],
    )
    memory = normalize_memory(
        {
            **memory.model_dump(),
            "relationship": {
                **memory.relationship.model_dump(),
                "userName": patch.relationshipPatch.userName,
                "userFacts": [
                    item.model_dump() for item in patch.relationshipPatch.userFactUpserts
                ],
            },
        }
    )

    remembered = handle_memory_control_message("что ты обо мне помнишь?", memory)
    forgotten = handle_memory_control_message("забудь, как меня зовут", memory)
    no_questions = handle_memory_control_message("не задавай вопросы", memory)

    assert remembered
    assert "Сережа" in remembered.reply
    assert forgotten and forgotten.patch.relationshipPatch
    assert forgotten.patch.relationshipPatch.clearUserName
    assert no_questions and no_questions.patch.relationshipPatch
    assert no_questions.patch.relationshipPatch.boundaryUpserts


def test_proactivity_gate_blocks_recent_questions_and_allows_lore_followup() -> None:
    memory = normalize_memory(None)
    blocked = apply_proactivity_gate(
        reply="я рядом. хочешь еще?",
        proactive_intent=ProactiveIntent(kind="ask_user", text="хочешь еще?", priority=0.7),
        recent_messages=(
            PetRecentMessage(role="pet", text="как ты?"),
            PetRecentMessage(role="user", text="норм"),
            PetRecentMessage(role="pet", text="расскажешь?"),
        ),
        memory=memory,
        user_text="ок",
        age_stage="teen",
        mood="idle",
        stats=PetStats(hunger=80, happiness=80, energy=80, cleanliness=80),
    )
    allowed = apply_proactivity_gate(
        reply="Кап будит меня утром тихим звоном.",
        proactive_intent=ProactiveIntent(
            kind="continue_lore",
            text="хочешь, расскажу, где мы прячемся?",
            priority=0.8,
        ),
        recent_messages=(PetRecentMessage(role="user", text="кто твои друзья?"),),
        memory=memory,
        user_text="кто твои друзья?",
        age_stage="teen",
        mood="happy",
        stats=PetStats(hunger=80, happiness=80, energy=80, cleanliness=80),
    )

    assert not blocked.allowed
    assert "?" not in blocked.reply
    assert allowed.allowed
    assert allowed.reply.endswith("где мы прячемся?")
