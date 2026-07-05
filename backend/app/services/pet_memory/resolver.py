from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.pet_memory.compaction import compact_memory
from app.services.pet_memory.decay import apply_memory_decay
from app.services.pet_memory.models import (
    ActiveGoal,
    AppliedDevelopmentPatch,
    CanonMemoryFact,
    ConversationThread,
    DevelopmentPatch,
    GeneratedFactCandidate,
    GoalPatch,
    MemoryCandidate,
    PetEvent,
    PetMemoryPatch,
    PetMemoryStateV1,
    RejectedMemoryCandidate,
    RelationshipEvent,
    RelationshipMemoryPatch,
    RelationshipPatch,
    ThreadPatch,
    UserFact,
)
from app.services.pet_memory.normalizer import (
    MAX_CANON_FACTS,
    MAX_GENERATED_FACTS,
    clamp_float,
    clamp_int,
    make_memory_id,
    normalize_memory,
    normalize_text,
    normalized_key,
    now_iso,
)
from app.services.pet_memory.retrieval import MemoryContext

TECHNICAL_PATTERN = re.compile(
    r"\b(?:ии|ai|модель|ассистент|prompt|промпт|api|state|mood|json|backend|frontend)\b",
    re.IGNORECASE,
)
SENSITIVE_PATTERN = re.compile(
    r"(?:\+?\d[\d\s().-]{7,}\d|[\w.+-]+@[\w-]+\.[\w.-]+|паспорт|парол|"
    r"адрес|диагноз|банк|карта|религи|политическ|несовершеннолет)",
    re.IGNORECASE,
)
NO_MEMORY_PATTERN = re.compile(r"\b(?:не\s+запоминай|не\s+помни|не\s+сохраняй)\b", re.IGNORECASE)
NO_QUESTIONS_PATTERN = re.compile(
    r"\b(?:не\s+задавай\s+вопрос\w*|без\s+вопросов|не\s+спрашивай)\b",
    re.IGNORECASE,
)
REMEMBER_ME_PATTERN = re.compile(
    r"(?:что\s+ты\s+(?:обо\s+мне\s+)?помнишь|что\s+помнишь\s+обо\s+мне|"
    r"как\s+меня\s+зовут)",
    re.IGNORECASE,
)
FORGET_PATTERN = re.compile(r"\b(?:забудь|удали\s+из\s+памяти)\b", re.IGNORECASE)
NAME_PATTERN = re.compile(r"(?:меня\s+зовут|зови\s+меня)\s+([А-Яа-яЁёA-Za-z0-9_-]{2,40})")
FRIEND_QUESTION_PATTERN = re.compile(
    r"(?:кто\s+(?:твой|твоя|твои)\s+друз|кто\s+(?:твой|твоя)\s+друг|"
    r"как\s+зовут\s+(?:твоего\s+)?друга|есть\s+ли\s+у\s+тебя\s+друг)",
    re.IGNORECASE,
)
FRIEND_REPLY_PATTERN = re.compile(
    r"(?:мой|моя|у\s+меня\s+есть)\s+"
    r"(?P<role>лучший\s+друг|лучшая\s+подруга|друг|подруга|приятель|приятельница)"
    r"\s*(?:[-—–:]\s*|по\s+имени\s+)?(?P<detail>[^.!?\n]{2,180})",
    re.IGNORECASE,
)
COMPANION_REPLY_PATTERN = re.compile(
    r"(?:у\s+меня(?:\s+рядом)?\s+есть|рядом\s+со\s+мной|ещ[её]\s+есть)\s+"
    r"(?P<detail>[^.!?\n]{2,180})",
    re.IGNORECASE,
)
HOME_CHANGE_PATTERN = re.compile(
    r"(?:теперь\s+жив|теперь\s+обита|переех|новый\s+дом|другой\s+дом|"
    r"другом\s+доме|забыл[ао]?\s+(?:свой\s+)?дом)",
    re.IGNORECASE,
)
SPECIES_CHANGE_PATTERN = re.compile(
    r"(?:сменил[ао]?\s+вид|стал[ао]?\s+человеком|теперь\s+я\s+человек|"
    r"стал[ао]?\s+другим\s+существом|стал[ао]?\s+другой\s+вид)",
    re.IGNORECASE,
)
WORLD_CHANGE_PATTERN = re.compile(
    r"(?:теперь\s+(?:его|ее|мой|наш)?\s*мир|друг\w*\s+мир\w*|нов\w*\s+мир\w*|"
    r"сменил[ао]?\s+мир|забыл[ао]?\s+(?:свой\s+)?мир|больше\s+не\s+живет)",
    re.IGNORECASE,
)
MAJOR_EVENT_PATTERN = re.compile(
    r"(?:войн|битв|смерт|погиб|убил|травм|катастроф|изгнан|навсегда\s+исчез|"
    r"спас[а-яё]*\s+весь\s+мир)",
    re.IGNORECASE,
)
ROLE_NAME_PATTERN = re.compile(
    r"(?:друг|подруга|приятель|приятельница|родственник|родственница|наставник|"
    r"сосед|соседка|соперник|хранитель|мастер|учитель|брат|сестра|тетя|дядя)"
    r"\s+(?:питомца\s+)?[-—–]?\s*([А-ЯЁA-Z][А-Яа-яЁёA-Za-z0-9_-]{1,40})"
)
ENTITY_PATTERN = re.compile(r"\b(?:[А-ЯЁ][а-яё]{2,}|[A-Z][A-Za-z]{2,})\b")
FRIEND_SCOPE_PATTERN = re.compile(
    r"\b(?:друг|друз\w*|подруг\w*|приятел\w*|сосед\w*|соседк\w*)\b",
    re.IGNORECASE,
)
GENERATED_FACT_CANDIDATE_TYPES = (
    "pet_generated_fact",
    "pet_canon_fact",
    "pet_emotional_fact",
)
GENERATED_SCOPE_BY_TYPE = {
    "world_fact": "world",
    "home_fact": "home",
    "friend_fact": "friend",
    "family_fact": "family",
    "origin_fact": "origin",
    "preference_fact": "preference",
    "preference": "preference",
    "fear_fact": "fear",
    "habit_fact": "habit",
    "voice_fact": "voice",
    "pet_emotional_fact": "voice",
    "milestone": "world",
}
CANON_TYPE_BY_GENERATED_SCOPE = {
    "world": "world_fact",
    "home": "home_fact",
    "friend": "friend_fact",
    "family": "family_fact",
    "origin": "origin_fact",
    "preference": "preference_fact",
    "fear": "fear_fact",
    "habit": "habit_fact",
    "voice": "voice_fact",
    "relationship": "voice_fact",
    "thread": "world_fact",
}


@dataclass(frozen=True)
class MemoryControlResult:
    reply: str
    patch: PetMemoryPatch
    debug_flags: tuple[str, ...] = ()


def is_no_memory_write_message(text: str | None) -> bool:
    return bool(NO_MEMORY_PATTERN.search(text or ""))


def _is_sensitive_or_technical(text: str) -> str | None:
    if TECHNICAL_PATTERN.search(text):
        return "technical_memory"
    if SENSITIVE_PATTERN.search(text):
        return "sensitive_memory"
    return None


def _character_text(character_bible: dict[str, Any] | None) -> str:
    parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    collect(character_bible or {})
    return " ".join(parts).casefold()


def _canon_conflict_reason(
    candidate: MemoryCandidate,
    character_bible: dict[str, Any] | None,
) -> str | None:
    text = candidate.text.casefold()
    bible_text = _character_text(character_bible)
    if candidate.type == "home_fact" and bible_text and HOME_CHANGE_PATTERN.search(text):
        return "canon_home_conflict"
    if SPECIES_CHANGE_PATTERN.search(text):
        return "canon_species_conflict"
    if candidate.type in ("world_fact", "origin_fact", "home_fact") and WORLD_CHANGE_PATTERN.search(
        text
    ):
        return "canon_world_conflict"
    if MAJOR_EVENT_PATTERN.search(text):
        return "canon_major_event_conflict"
    do_not_change = (
        character_bible.get("do_not_change") if isinstance(character_bible, dict) else None
    )
    if isinstance(do_not_change, list):
        anchors = [str(item).casefold() for item in do_not_change if str(item).strip()]
        if anchors and any("без " + anchor in text for anchor in anchors):
            return "canon_visual_anchor_conflict"
    return None


def _relationship_entity_key(text: str) -> str | None:
    match = ROLE_NAME_PATTERN.search(text)
    if match:
        return match.group(1).casefold()
    return None


def _reject(candidate: MemoryCandidate, reason: str, now: str) -> RejectedMemoryCandidate:
    return RejectedMemoryCandidate(
        id=make_memory_id("rejected"),
        type=candidate.type,
        text=normalize_text(candidate.text),
        reason=reason,
        confidence=candidate.confidence,
        importance=candidate.importance,
        createdAt=now,
    )


def _implicit_lore_candidates(
    user_text: str,
    pet_reply: str,
    explicit_candidates: list[MemoryCandidate],
) -> list[MemoryCandidate]:
    if any(
        candidate.type in ("friend_fact", "pet_generated_fact", "pet_canon_fact")
        for candidate in explicit_candidates
    ):
        return []
    if not FRIEND_QUESTION_PATTERN.search(user_text):
        return []
    match = FRIEND_REPLY_PATTERN.search(pet_reply)
    if match:
        role = normalize_text(match.group("role"), 80).casefold()
        detail = normalize_text(match.group("detail"), 220).strip(" -—–:,.")
        source_span = normalize_text(match.group(0), 240)
    else:
        companion_match = COMPANION_REPLY_PATTERN.search(pet_reply)
        if not companion_match:
            return []
        role = "друг"
        detail = normalize_text(companion_match.group("detail"), 220).strip(" -—–:,.")
        source_span = normalize_text(companion_match.group(0), 240)
    if not role or not detail:
        return []

    return [
        MemoryCandidate(
            type="pet_generated_fact",
            text=f"У питомца есть {role} {detail}.",
            importance=0.68,
            confidence=0.66,
            sourceSpan=source_span,
        )
    ]


def _relationship_patch_or_new(patch: PetMemoryPatch) -> RelationshipMemoryPatch:
    if patch.relationshipPatch is None:
        patch.relationshipPatch = RelationshipMemoryPatch()
    return patch.relationshipPatch


def _append_event(
    patch: PetMemoryPatch,
    kind: str,
    text: str,
    now: str,
    importance: float = 0.5,
    related_id: str | None = None,
) -> None:
    clean = normalize_text(text)
    if not clean:
        return
    patch.eventAppends.append(
        PetEvent(
            id=make_memory_id("event"),
            kind=kind,
            text=clean,
            importance=importance,
            createdAt=now,
            relatedMemoryId=related_id,
        )
    )


def _apply_relationship_patch_to_memory(
    memory: PetMemoryStateV1,
    relationship_patch: RelationshipMemoryPatch,
) -> PetMemoryStateV1:
    relationship = memory.relationship
    user_facts = list(relationship.userFacts)
    shared_events = list(relationship.sharedEvents)
    boundaries = list(relationship.boundaries)
    if relationship_patch.clearUserName:
        user_name = None
    else:
        user_name = relationship_patch.userName or relationship.userName
    preferred_address = (
        None
        if relationship_patch.clearPreferredAddress
        else relationship_patch.preferredAddress or relationship.preferredAddress
    )
    delete_user_fact_ids = set(relationship_patch.userFactDeletes)
    if delete_user_fact_ids:
        user_facts = [item for item in user_facts if item.id not in delete_user_fact_ids]
    user_facts.extend(relationship_patch.userFactUpserts)
    shared_events.extend(relationship_patch.sharedEventUpserts)
    for boundary in relationship_patch.boundaryUpserts:
        if boundary not in boundaries:
            boundaries.append(boundary)
    relationship = relationship.model_copy(
        update={
            "userName": user_name,
            "preferredAddress": preferred_address,
            "trust": relationship_patch.trust
            if relationship_patch.trust is not None
            else relationship.trust,
            "attachment": relationship_patch.attachment
            if relationship_patch.attachment is not None
            else relationship.attachment,
            "familiarity": relationship_patch.familiarity
            if relationship_patch.familiarity is not None
            else relationship.familiarity,
            "sharedEvents": shared_events,
            "userFacts": user_facts,
            "boundaries": boundaries,
            "lastWarmMomentAt": relationship_patch.lastWarmMomentAt
            or relationship.lastWarmMomentAt,
        }
    )
    return memory.model_copy(update={"relationship": relationship})


def _apply_patch_to_memory(memory: PetMemoryStateV1, patch: PetMemoryPatch) -> PetMemoryStateV1:
    canon = {fact.id: fact for fact in memory.canon}
    for deleted_id in patch.canonDeletes:
        canon.pop(deleted_id, None)
    canon.update({fact.id: fact for fact in patch.canonUpserts})

    generated_facts = {fact.id: fact for fact in memory.generatedFacts}
    for deleted_id in patch.generatedFactDeletes:
        generated_facts.pop(deleted_id, None)
    generated_facts.update({fact.id: fact for fact in patch.generatedFactUpserts})

    threads = {item.id: item for item in memory.threads}
    for deleted_id in patch.threadDeletes:
        threads.pop(deleted_id, None)
    threads.update({item.id: item for item in patch.threadUpserts})

    reflections = {item.id: item for item in memory.reflections}
    for deleted_id in patch.reflectionDeletes:
        reflections.pop(deleted_id, None)
    reflections.update({item.id: item for item in patch.reflectionUpserts})

    goals = {item.id: item for item in memory.activeGoals}
    for deleted_id in patch.activeGoalDeletes:
        goals.pop(deleted_id, None)
    goals.update({item.id: item for item in patch.activeGoalUpserts})

    memory = memory.model_copy(
        update={
            "canon": list(canon.values()),
            "generatedFacts": list(generated_facts.values()),
            "threads": list(threads.values()),
            "reflections": list(reflections.values()),
            "activeGoals": list(goals.values()),
            "events": [*memory.events, *patch.eventAppends],
            "rejectedCandidates": [
                *memory.rejectedCandidates,
                *patch.rejectedCandidateAppends,
            ],
        }
    )
    if patch.relationshipPatch:
        memory = _apply_relationship_patch_to_memory(memory, patch.relationshipPatch)
    if patch.developmentPatch:
        development_updates = patch.developmentPatch.model_dump(exclude_none=True)
        development_updates.pop("lastDevelopmentReason", None)
        memory = memory.model_copy(
            update={
                "development": memory.development.model_copy(
                    update={
                        **development_updates,
                        "lastDevelopmentReason": patch.developmentPatch.lastDevelopmentReason
                        or memory.development.lastDevelopmentReason,
                    }
                )
            }
        )
    return normalize_memory(memory)


def _upsert_user_fact(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    text: str,
    now: str,
    *,
    confidence: float = 0.7,
    importance: float = 0.5,
) -> None:
    clean = normalize_text(text)
    if not clean or _is_sensitive_or_technical(clean):
        return
    relationship_patch = _relationship_patch_or_new(patch)
    key = normalized_key(clean)
    for fact in memory.relationship.userFacts:
        if normalized_key(fact.text) == key:
            updated = fact.model_copy(
                update={
                    "confidence": max(fact.confidence, confidence),
                    "importance": max(fact.importance, importance),
                    "updatedAt": now,
                    "lastUsedAt": now,
                }
            )
            relationship_patch.userFactUpserts.append(updated)
            return
    relationship_patch.userFactUpserts.append(
        UserFact(
            id=make_memory_id("userfact"),
            text=clean,
            confidence=confidence,
            importance=importance,
            createdAt=now,
            updatedAt=now,
        )
    )


def _upsert_relationship_event(
    patch: PetMemoryPatch,
    text: str,
    now: str,
    *,
    importance: float = 0.5,
) -> None:
    clean = normalize_text(text)
    if not clean:
        return
    _relationship_patch_or_new(patch).sharedEventUpserts.append(
        RelationshipEvent(
            id=make_memory_id("relevent"),
            text=clean,
            importance=importance,
            createdAt=now,
            updatedAt=now,
        )
    )


def _apply_relationship_patch(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    model_patch: RelationshipPatch | None,
    user_text: str,
    now: str,
) -> None:
    relationship_patch = _relationship_patch_or_new(patch)
    name_match = NAME_PATTERN.search(user_text)
    if name_match:
        name = normalize_text(name_match.group(1), 80)
        if name and not _is_sensitive_or_technical(name):
            relationship_patch.userName = name
            relationship_patch.preferredAddress = name
            _upsert_user_fact(memory, patch, f"Пользователь просил называть его {name}.", now)

    if NO_QUESTIONS_PATTERN.search(user_text):
        boundary = "Пользователь попросил не задавать вопросы."
        if boundary not in memory.relationship.boundaries:
            relationship_patch.boundaryUpserts.append(boundary)

    if not model_patch:
        relationship_patch.familiarity = clamp_int(memory.relationship.familiarity + 1)
        return

    if model_patch.userName and not _is_sensitive_or_technical(model_patch.userName):
        relationship_patch.userName = normalize_text(model_patch.userName, 80)
    if model_patch.preferredAddress and not _is_sensitive_or_technical(
        model_patch.preferredAddress
    ):
        relationship_patch.preferredAddress = normalize_text(model_patch.preferredAddress, 80)
    if model_patch.sharedEvent and not _is_sensitive_or_technical(model_patch.sharedEvent):
        _upsert_relationship_event(
            patch,
            model_patch.sharedEvent,
            now,
            importance=0.55,
        )
    if model_patch.userFact:
        _upsert_user_fact(
            memory, patch, model_patch.userFact, now, confidence=0.75, importance=0.55
        )

    relationship_patch.trust = clamp_int(memory.relationship.trust + (model_patch.trustDelta or 0))
    relationship_patch.attachment = clamp_int(
        memory.relationship.attachment + (model_patch.attachmentDelta or 0)
    )
    relationship_patch.familiarity = clamp_int(
        memory.relationship.familiarity + (model_patch.familiarityDelta or 0) + 1
    )
    if any(
        value and value > 0
        for value in (
            model_patch.trustDelta,
            model_patch.attachmentDelta,
            model_patch.familiarityDelta,
        )
    ):
        relationship_patch.lastWarmMomentAt = now
    _append_event(patch, "relationship", "Отношения с пользователем немного обновились.", now)


def _apply_development_patch(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    model_patch: DevelopmentPatch | None,
    now: str,
) -> None:
    development = memory.development
    if not model_patch:
        patch.developmentPatch = AppliedDevelopmentPatch(
            trust=development.trust,
            attachment=development.attachment,
            curiosity=development.curiosity,
            confidence=development.confidence,
            loneliness=clamp_int(development.loneliness - 1),
            playfulness=development.playfulness,
            lastDevelopmentReason=development.lastDevelopmentReason,
        )
        return

    patch.developmentPatch = AppliedDevelopmentPatch(
        trust=clamp_int(development.trust + (model_patch.trustDelta or 0)),
        attachment=clamp_int(development.attachment + (model_patch.attachmentDelta or 0)),
        curiosity=clamp_int(development.curiosity + (model_patch.curiosityDelta or 0)),
        confidence=clamp_int(development.confidence + (model_patch.confidenceDelta or 0)),
        loneliness=clamp_int(development.loneliness + (model_patch.lonelinessDelta or 0)),
        playfulness=clamp_int(development.playfulness + (model_patch.playfulnessDelta or 0)),
        lastDevelopmentReason=normalize_text(model_patch.reason, 300)
        if model_patch.reason
        else development.lastDevelopmentReason,
    )
    _append_event(
        patch,
        "development",
        patch.developmentPatch.lastDevelopmentReason or "Развитие питомца немного изменилось.",
        now,
    )


def _generated_scope_for_candidate(candidate: MemoryCandidate) -> str:
    mapped = GENERATED_SCOPE_BY_TYPE.get(candidate.type)
    if mapped:
        return mapped
    text = candidate.text.casefold()
    if any(word in text for word in ("семь", "родн", "брат", "сестр", "мам", "пап")):
        return "family"
    if any(word in text for word in ("дом", "полк", "нор", "гнезд", "комнат", "жив", "обита")):
        return "home"
    if FRIEND_SCOPE_PATTERN.search(text):
        return "friend"
    if any(word in text for word in ("откуда", "прошл", "родил", "появил", "нашли")):
        return "origin"
    if any(word in text for word in ("страш", "бою", "боиш", "страх")):
        return "fear"
    if any(word in text for word in ("люб", "нрав", "любим")):
        return "preference"
    if any(word in text for word in ("привыч", "обычно", "всегда", "часто")):
        return "habit"
    if any(word in text for word in ("говор", "голос", "шеп", "бурч", "ворч", "мурч")):
        return "voice"
    return "world"


def _has_named_entity(text: str) -> bool:
    ignored = {"Питомец", "Пользователь"}
    return any(match.group(0) not in ignored for match in ENTITY_PATTERN.finditer(text))


def _generated_initial_status(scope: str, candidate: MemoryCandidate) -> str:
    if scope in ("friend", "family", "origin"):
        return "needs_user_confirmation"
    if scope in ("home", "world") and candidate.importance >= 0.75 and _has_named_entity(
        candidate.text
    ):
        return "needs_user_confirmation"
    return "draft"


def _generated_promotion_policy(status: str, scope: str) -> str:
    if status == "rejected":
        return "never_promote_conflict"
    if status == "needs_user_confirmation":
        return "ask_user_before_canon"
    if scope in ("friend", "family", "origin", "home", "world"):
        return "needs_reinforcement_and_confirmation"
    return "reinforce_twice_for_soft_accept"


def _generated_conflict_reasons(
    candidate: MemoryCandidate,
    scope: str,
    character_bible: dict[str, Any] | None,
) -> list[str]:
    reason = _is_sensitive_or_technical(candidate.text)
    if reason:
        return [reason]
    shadow_type = CANON_TYPE_BY_GENERATED_SCOPE.get(scope, "world_fact")
    shadow = candidate.model_copy(update={"type": shadow_type})
    canon_reason = _canon_conflict_reason(shadow, character_bible)
    return [canon_reason] if canon_reason else []


def _related_canon_fact_id(memory: PetMemoryStateV1, text: str) -> str | None:
    key = normalized_key(text)
    for fact in memory.canon:
        if normalized_key(fact.text) == key:
            return fact.id
    return None


def _promote_generated_status(
    existing_status: str,
    proposed_status: str,
    scope: str,
    reinforcement_count: int,
) -> str:
    if proposed_status == "rejected":
        return "rejected"
    if existing_status == "canon":
        return "canon"
    if existing_status == "rejected":
        return proposed_status
    if existing_status == "needs_user_confirmation" or proposed_status == "needs_user_confirmation":
        return "needs_user_confirmation"
    if reinforcement_count >= 2 and scope in (
        "voice",
        "habit",
        "preference",
        "fear",
        "world",
        "home",
    ):
        return "accepted_soft"
    return proposed_status


def _upsert_generated_fact(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    candidate: MemoryCandidate,
    character_bible: dict[str, Any] | None,
    now: str,
) -> None:
    clean = normalize_text(candidate.text)
    if not clean:
        return
    scope = _generated_scope_for_candidate(candidate)
    conflict_reasons = _generated_conflict_reasons(candidate, scope, character_bible)
    proposed_status = (
        "rejected" if conflict_reasons else _generated_initial_status(scope, candidate)
    )
    key = f"{scope}:{normalized_key(clean)}"
    existing = next(
        (
            fact
            for fact in [*memory.generatedFacts, *patch.generatedFactUpserts]
            if f"{fact.scope}:{normalized_key(fact.text)}" == key
        ),
        None,
    )
    if existing:
        reinforcement_count = existing.reinforcementCount + (
            0 if proposed_status == "rejected" else 1
        )
        status = _promote_generated_status(
            existing.status,
            proposed_status,
            scope,
            reinforcement_count,
        )
        patch.generatedFactUpserts.append(
            existing.model_copy(
                update={
                    "text": clean,
                    "sourceSpan": normalize_text(candidate.sourceSpan, 240)
                    if candidate.sourceSpan
                    else existing.sourceSpan,
                    "confidence": max(existing.confidence, clamp_float(candidate.confidence)),
                    "importance": max(existing.importance, clamp_float(candidate.importance)),
                    "status": status,
                    "promotionPolicy": _generated_promotion_policy(status, scope),
                    "conflictReasons": list(
                        dict.fromkeys([*existing.conflictReasons, *conflict_reasons])
                    )[:6],
                    "reinforcementCount": reinforcement_count,
                    "relatedCanonFactId": existing.relatedCanonFactId
                    or _related_canon_fact_id(memory, clean),
                    "updatedAt": now,
                }
            )
        )
    else:
        status = proposed_status
        patch.generatedFactUpserts.append(
            GeneratedFactCandidate(
                id=make_memory_id("genfact"),
                scope=scope,
                text=clean,
                source="model",
                sourceSpan=normalize_text(candidate.sourceSpan, 240)
                if candidate.sourceSpan
                else None,
                confidence=clamp_float(candidate.confidence),
                importance=clamp_float(candidate.importance),
                status=status,
                promotionPolicy=_generated_promotion_policy(status, scope),
                conflictReasons=conflict_reasons,
                reinforcementCount=1,
                relatedCanonFactId=_related_canon_fact_id(memory, clean),
                createdAt=now,
                updatedAt=now,
            )
        )
    if conflict_reasons:
        patch.rejectedCandidateAppends.append(_reject(candidate, conflict_reasons[0], now))


def _resolve_canon_candidate(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    candidate: MemoryCandidate,
    character_bible: dict[str, Any] | None,
    now: str,
) -> None:
    clean = normalize_text(candidate.text)
    if not clean:
        return
    reason = _is_sensitive_or_technical(clean) or _canon_conflict_reason(candidate, character_bible)
    if reason:
        patch.rejectedCandidateAppends.append(_reject(candidate, reason, now))
        return
    key = normalized_key(clean)
    fact_type = _canon_fact_type_for_candidate(candidate)
    relationship_entity_key = (
        _relationship_entity_key(clean)
        if fact_type in ("friend_fact", "family_fact")
        else None
    )
    for fact in memory.canon:
        if normalized_key(fact.text) == key:
            patch.canonUpserts.append(
                fact.model_copy(
                    update={
                        "importance": max(fact.importance, clamp_float(candidate.importance)),
                        "confidence": max(fact.confidence, clamp_float(candidate.confidence)),
                        "useCount": fact.useCount + 1,
                        "lastUsedAt": now,
                        "lastReinforcedAt": now,
                        "decayScore": clamp_float(fact.decayScore - 0.15),
                        "updatedAt": now,
                    }
                )
            )
            _append_event(patch, "memory_accepted", f"Подтвержден факт: {clean}", now, 0.6, fact.id)
            return
        if (
            relationship_entity_key
            and fact.type == fact_type
            and _relationship_entity_key(fact.text) == relationship_entity_key
        ):
            updated_text = clean if len(clean) > len(fact.text) else fact.text
            patch.canonUpserts.append(
                fact.model_copy(
                    update={
                        "text": updated_text,
                        "importance": max(fact.importance, clamp_float(candidate.importance)),
                        "confidence": max(fact.confidence, clamp_float(candidate.confidence)),
                        "useCount": fact.useCount + 1,
                        "lastUsedAt": now,
                        "lastReinforcedAt": now,
                        "decayScore": clamp_float(fact.decayScore - 0.15),
                        "updatedAt": now,
                    }
                )
            )
            _append_event(
                patch,
                "memory_accepted",
                f"Уточнен факт: {updated_text}",
                now,
                0.6,
                fact.id,
            )
            return
    fact = CanonMemoryFact(
        id=make_memory_id("canon"),
        type=fact_type,
        text=clean,
        source="model",
        confidence=clamp_float(candidate.confidence),
        importance=clamp_float(candidate.importance),
        useCount=0,
        decayScore=0.02,
        createdAt=now,
        updatedAt=now,
    )
    patch.canonUpserts.append(fact)
    _append_event(
        patch, "memory_accepted", f"Появился новый факт: {clean}", now, fact.importance, fact.id
    )


def _canon_fact_type_for_candidate(candidate: MemoryCandidate) -> str:
    mapping = {
        "pet_canon_fact": "world_fact",
        "pet_emotional_fact": "voice_fact",
        "preference": "preference_fact",
    }
    return mapping.get(candidate.type, candidate.type)


def _resolve_thread_candidate(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    candidate: MemoryCandidate,
    now: str,
) -> None:
    clean = normalize_text(candidate.text)
    if not clean:
        return
    clean_words = set(re.findall(r"[A-Za-zА-Яа-яЁё0-9]{4,}", clean.casefold()))
    for thread in memory.threads:
        thread_words = set(
            re.findall(r"[A-Za-zА-Яа-яЁё0-9]{4,}", f"{thread.topic} {thread.summary}".casefold())
        )
        if thread.status == "open" and clean_words and len(clean_words & thread_words) >= 2:
            patch.threadUpserts.append(
                thread.model_copy(
                    update={
                        "summary": clean,
                        "priority": max(thread.priority, clamp_float(candidate.importance)),
                        "updatedAt": now,
                        "lastMentionedAt": now,
                    }
                )
            )
            _append_event(patch, "thread", f"Обновлена тема: {clean}", now, 0.45, thread.id)
            return
    thread = ConversationThread(
        id=make_memory_id("thread"),
        topic=clean[:120],
        summary=clean,
        status="open",
        priority=clamp_float(candidate.importance),
        createdAt=now,
        updatedAt=now,
        lastMentionedAt=now,
    )
    patch.threadUpserts.append(thread)
    _append_event(patch, "thread", f"Открыта тема: {clean}", now, thread.priority, thread.id)


def _resolve_candidates(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    candidates: list[MemoryCandidate],
    character_bible: dict[str, Any] | None,
    now: str,
) -> None:
    for candidate in candidates[:3]:
        if candidate.type == "user_fact":
            reason = _is_sensitive_or_technical(candidate.text)
            if reason:
                patch.rejectedCandidateAppends.append(_reject(candidate, reason, now))
                continue
            _upsert_user_fact(
                memory,
                patch,
                candidate.text,
                now,
                confidence=candidate.confidence,
                importance=candidate.importance,
            )
            continue
        if candidate.type == "relationship_event":
            reason = _is_sensitive_or_technical(candidate.text)
            if reason:
                patch.rejectedCandidateAppends.append(_reject(candidate, reason, now))
                continue
            _upsert_relationship_event(patch, candidate.text, now, importance=candidate.importance)
            continue
        if candidate.type == "boundary":
            reason = _is_sensitive_or_technical(candidate.text)
            if reason:
                patch.rejectedCandidateAppends.append(_reject(candidate, reason, now))
                continue
            relationship_patch = patch.relationshipPatch or RelationshipMemoryPatch()
            if candidate.text not in relationship_patch.boundaryUpserts:
                relationship_patch.boundaryUpserts.append(normalize_text(candidate.text, 160))
            patch.relationshipPatch = relationship_patch
            continue
        if candidate.type == "open_thread":
            reason = _is_sensitive_or_technical(candidate.text)
            if reason:
                patch.rejectedCandidateAppends.append(_reject(candidate, reason, now))
                continue
            _resolve_thread_candidate(memory, patch, candidate, now)
            continue
        if candidate.type in GENERATED_FACT_CANDIDATE_TYPES:
            _upsert_generated_fact(memory, patch, candidate, character_bible, now)
            continue
        _resolve_canon_candidate(memory, patch, candidate, character_bible, now)


def _apply_thread_patch(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    model_patch: ThreadPatch | None,
    now: str,
    *,
    proactivity_allowed: bool,
) -> None:
    if not model_patch:
        return
    if model_patch.update:
        for thread in memory.threads:
            if thread.id == model_patch.update.threadId:
                patch.threadUpserts.append(
                    thread.model_copy(
                        update={
                            "summary": normalize_text(model_patch.update.summary or thread.summary),
                            "suggestedFollowUp": normalize_text(
                                model_patch.update.suggestedFollowUp
                                or thread.suggestedFollowUp
                                or "",
                                240,
                            )
                            or None,
                            "status": model_patch.update.status or thread.status,
                            "updatedAt": now,
                            "lastMentionedAt": now,
                        }
                    )
                )
                return
    if not model_patch.open:
        return
    if model_patch.open.suggestedFollowUp and not proactivity_allowed:
        return
    topic_key = normalized_key(model_patch.open.topic)
    for thread in memory.threads:
        if normalized_key(thread.topic) == topic_key and thread.status != "resolved":
            patch.threadUpserts.append(
                thread.model_copy(
                    update={
                        "summary": normalize_text(model_patch.open.summary),
                        "suggestedFollowUp": model_patch.open.suggestedFollowUp,
                        "priority": max(thread.priority, model_patch.open.priority),
                        "status": "open",
                        "updatedAt": now,
                        "lastMentionedAt": now,
                    }
                )
            )
            return
    thread = ConversationThread(
        id=make_memory_id("thread"),
        topic=normalize_text(model_patch.open.topic, 160),
        summary=normalize_text(model_patch.open.summary),
        status="open",
        priority=clamp_float(model_patch.open.priority),
        createdAt=now,
        updatedAt=now,
        lastMentionedAt=now,
        suggestedFollowUp=model_patch.open.suggestedFollowUp,
    )
    patch.threadUpserts.append(thread)
    _append_event(patch, "thread", f"Открыта тема: {thread.topic}", now, thread.priority, thread.id)


def _apply_goal_patch(
    memory: PetMemoryStateV1,
    patch: PetMemoryPatch,
    model_patch: GoalPatch | None,
    now: str,
    *,
    proactivity_allowed: bool,
) -> None:
    if not model_patch:
        return
    if model_patch.update:
        for goal in memory.activeGoals:
            if goal.id == model_patch.update.goalId:
                patch.activeGoalUpserts.append(
                    goal.model_copy(
                        update={
                            "status": model_patch.update.status or goal.status,
                            "priority": (
                                model_patch.update.priority
                                if model_patch.update.priority is not None
                                else goal.priority
                            ),
                            "updatedAt": now,
                        }
                    )
                )
                return
    if not model_patch.open:
        return
    if (
        model_patch.open.kind in ("seek_care", "return_to_thread", "learn_about_user")
        and not proactivity_allowed
    ):
        return
    text_key = normalized_key(model_patch.open.text)
    for goal in memory.activeGoals:
        if normalized_key(goal.text) == text_key and goal.status == "active":
            patch.activeGoalUpserts.append(
                goal.model_copy(
                    update={
                        "priority": max(goal.priority, model_patch.open.priority),
                        "updatedAt": now,
                    }
                )
            )
            return
    goal = ActiveGoal(
        id=make_memory_id("goal"),
        kind=model_patch.open.kind,
        text=normalize_text(model_patch.open.text, 300),
        priority=clamp_float(model_patch.open.priority),
        status="active",
        createdAt=now,
        updatedAt=now,
        expiresAt=model_patch.open.expiresAt,
        relatedThreadId=model_patch.open.relatedThreadId,
    )
    patch.activeGoalUpserts.append(goal)
    _append_event(patch, "goal", f"Появилось желание: {goal.text}", now, goal.priority, goal.id)


def _memory_from_patch(memory: PetMemoryStateV1, patch: PetMemoryPatch) -> PetMemoryStateV1:
    return _apply_patch_to_memory(memory, patch)


def _merge_patch(target: PetMemoryPatch, source: PetMemoryPatch) -> None:
    target.canonUpserts.extend(source.canonUpserts)
    target.canonDeletes.extend(source.canonDeletes)
    target.generatedFactUpserts.extend(source.generatedFactUpserts)
    target.generatedFactDeletes.extend(source.generatedFactDeletes)
    target.threadUpserts.extend(source.threadUpserts)
    target.threadDeletes.extend(source.threadDeletes)
    target.reflectionUpserts.extend(source.reflectionUpserts)
    target.reflectionDeletes.extend(source.reflectionDeletes)
    target.activeGoalUpserts.extend(source.activeGoalUpserts)
    target.activeGoalDeletes.extend(source.activeGoalDeletes)
    target.eventAppends.extend(source.eventAppends)
    target.rejectedCandidateAppends.extend(source.rejectedCandidateAppends)
    if source.relationshipPatch:
        if target.relationshipPatch is None:
            target.relationshipPatch = source.relationshipPatch
        else:
            current = target.relationshipPatch
            update = source.relationshipPatch.model_dump(exclude_defaults=True)
            target.relationshipPatch = current.model_copy(update=update)
    if source.developmentPatch:
        target.developmentPatch = source.developmentPatch


def _dedupe_canon_upserts(patch: PetMemoryPatch) -> None:
    by_id: dict[str, CanonMemoryFact] = {}
    for fact in patch.canonUpserts:
        by_id[fact.id] = fact
    patch.canonUpserts = list(by_id.values())


def _dedupe_generated_fact_upserts(patch: PetMemoryPatch) -> None:
    by_id: dict[str, GeneratedFactCandidate] = {}
    for fact in patch.generatedFactUpserts:
        by_id[fact.id] = fact
    patch.generatedFactUpserts = list(by_id.values())


def _memory_summary_reply(memory: PetMemoryStateV1) -> str:
    parts: list[str] = []
    relationship = memory.relationship
    if relationship.userName:
        parts.append(f"тебя зовут {relationship.userName}")
    for fact in relationship.userFacts[:4]:
        text = _human_user_fact(fact.text, relationship.userName)
        if text and text not in parts:
            parts.append(text)
    if not parts:
        return "я пока помню совсем немного: ты рядом со мной."
    return "я помню: " + "; ".join(parts[:5]) + "."


def _human_user_fact(text: str, user_name: str | None) -> str | None:
    clean = normalize_text(text).rstrip(".!?…")
    lowered = clean.casefold()
    if user_name and (
        "зовут" in lowered or "называть" in lowered or user_name.casefold() == lowered
    ):
        return None
    clean = re.sub(r"^(пользователь|собеседник|собеседница)\s+", "", clean, flags=re.IGNORECASE)
    if user_name:
        clean = re.sub(
            rf"^{re.escape(user_name)}\s+любит\s+",
            "ты любишь ",
            clean,
            flags=re.IGNORECASE,
        )
    clean = re.sub(r"^любит\s+", "ты любишь ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^просил[аи]?\s+", "ты просил ", clean, flags=re.IGNORECASE)
    if not clean:
        return None
    return clean[0].lower() + clean[1:]


def _forget_patch(memory: PetMemoryStateV1, text: str, now: str) -> PetMemoryPatch:
    patch = PetMemoryPatch()
    relationship_patch = _relationship_patch_or_new(patch)
    lowered = text.casefold()
    if "как меня зовут" in lowered or "имя" in lowered or "зовут" in lowered:
        relationship_patch.clearUserName = True
        relationship_patch.clearPreferredAddress = True
        relationship_patch.userFactDeletes.extend(
            fact.id for fact in memory.relationship.userFacts if "зов" in fact.text.casefold()
        )
        _append_event(patch, "relationship", "Пользователь попросил забыть имя.", now)
        return patch

    tokens = set(re.findall(r"[А-Яа-яЁёA-Za-z0-9]{4,}", lowered))
    if tokens:
        for fact in memory.relationship.userFacts:
            if tokens & set(re.findall(r"[А-Яа-яЁёA-Za-z0-9]{4,}", fact.text.casefold())):
                relationship_patch.userFactDeletes.append(fact.id)
        for fact in memory.canon:
            if tokens & set(re.findall(r"[А-Яа-яЁёA-Za-z0-9]{4,}", fact.text.casefold())):
                patch.canonDeletes.append(fact.id)
        for fact in memory.generatedFacts:
            if tokens & set(re.findall(r"[А-Яа-яЁёA-Za-z0-9]{4,}", fact.text.casefold())):
                patch.generatedFactDeletes.append(fact.id)
    if not relationship_patch.userFactDeletes and not patch.canonDeletes:
        recent_fact = memory.relationship.userFacts[0] if memory.relationship.userFacts else None
        if recent_fact:
            relationship_patch.userFactDeletes.append(recent_fact.id)
    _append_event(patch, "relationship", "Пользователь попросил забыть часть памяти.", now)
    return patch


def handle_memory_control_message(
    message: str,
    memory: PetMemoryStateV1,
    *,
    now: str | None = None,
) -> MemoryControlResult | None:
    now_value = now or now_iso()
    if FORGET_PATTERN.search(message):
        patch = _forget_patch(memory, message, now_value)
        return MemoryControlResult(
            reply="хорошо, я отпущу это из памяти.",
            patch=patch,
            debug_flags=("memory_forget",),
        )
    if REMEMBER_ME_PATTERN.search(message):
        return MemoryControlResult(
            reply=_memory_summary_reply(memory),
            patch=PetMemoryPatch(),
            debug_flags=("memory_read",),
        )
    if NO_QUESTIONS_PATTERN.search(message):
        patch = PetMemoryPatch()
        boundary = "Пользователь попросил не задавать вопросы."
        if boundary not in memory.relationship.boundaries:
            _relationship_patch_or_new(patch).boundaryUpserts.append(boundary)
        return MemoryControlResult(
            reply="ладно, буду отвечать тише и без вопросов.",
            patch=patch,
            debug_flags=("memory_boundary_no_questions",),
        )
    return None


def _legacy_lore_candidates(legacy_lore_memories: tuple[str, ...]) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for item in legacy_lore_memories[:3]:
        text = normalize_text(item.removeprefix("ЛОР:").removeprefix("LORE:").strip())
        if text:
            candidates.append(
                MemoryCandidate(
                    type="world_fact",
                    text=text,
                    importance=0.55,
                    confidence=0.7,
                )
            )
    return candidates


def resolve_memory_update(
    memory: PetMemoryStateV1,
    *,
    character_bible: dict[str, Any] | None,
    memory_context: MemoryContext | None,
    user_text: str,
    pet_reply: str,
    memory_candidates: list[MemoryCandidate],
    legacy_lore_memories: tuple[str, ...] = (),
    relationship_patch: RelationshipPatch | None = None,
    development_patch: DevelopmentPatch | None = None,
    thread_patch: ThreadPatch | None = None,
    goal_patch: GoalPatch | None = None,
    no_memory_write: bool = False,
    proactivity_allowed: bool = True,
    now: str | None = None,
) -> PetMemoryPatch:
    now_value = now or now_iso()
    patch = PetMemoryPatch()

    if no_memory_write:
        return patch

    _append_event(patch, "user_message", user_text, now_value, 0.35)
    _append_event(patch, "pet_reply", pet_reply, now_value, 0.35)

    candidates = [
        *memory_candidates,
        *_implicit_lore_candidates(user_text, pet_reply, memory_candidates),
        *_legacy_lore_candidates(legacy_lore_memories),
    ]
    _resolve_candidates(memory, patch, candidates, character_bible, now_value)
    _apply_relationship_patch(memory, patch, relationship_patch, user_text, now_value)
    _apply_development_patch(memory, patch, development_patch, now_value)

    interim_memory = _memory_from_patch(memory, patch)
    _apply_thread_patch(
        interim_memory,
        patch,
        thread_patch,
        now_value,
        proactivity_allowed=proactivity_allowed,
    )
    interim_memory = _memory_from_patch(memory, patch)
    _apply_goal_patch(
        interim_memory,
        patch,
        goal_patch,
        now_value,
        proactivity_allowed=proactivity_allowed,
    )

    interim_memory = _memory_from_patch(memory, patch)
    decay_upserts, decay_deletes = apply_memory_decay(
        interim_memory,
        used_canon_fact_ids=memory_context.canon_fact_ids if memory_context else (),
        confirmed_canon_fact_ids=tuple(fact.id for fact in patch.canonUpserts),
        now=now_value,
    )
    patch.canonUpserts.extend(decay_upserts)
    patch.canonDeletes.extend(decay_deletes)

    # Keep the outgoing patch under the first-stage memory cap even before frontend normalization.
    if (
        len(interim_memory.canon) + len(patch.canonUpserts) - len(patch.canonDeletes)
        > MAX_CANON_FACTS
    ):
        by_value = sorted(
            interim_memory.canon,
            key=lambda fact: (
                fact.pinned,
                fact.type == "milestone",
                fact.importance,
                -fact.decayScore,
            ),
        )
        overflow = (
            len(interim_memory.canon)
            + len(patch.canonUpserts)
            - len(patch.canonDeletes)
            - MAX_CANON_FACTS
        )
        removable_ids = [
            fact.id for fact in by_value if not fact.pinned and fact.type != "milestone"
        ][:overflow]
        patch.canonDeletes.extend(removable_ids)

    if (
        len(interim_memory.generatedFacts)
        + len(patch.generatedFactUpserts)
        - len(patch.generatedFactDeletes)
        > MAX_GENERATED_FACTS
    ):
        by_value = sorted(
            interim_memory.generatedFacts,
            key=lambda fact: (
                fact.status in ("accepted_soft", "needs_user_confirmation"),
                fact.importance,
                fact.updatedAt,
            ),
        )
        overflow = (
            len(interim_memory.generatedFacts)
            + len(patch.generatedFactUpserts)
            - len(patch.generatedFactDeletes)
            - MAX_GENERATED_FACTS
        )
        patch.generatedFactDeletes.extend(fact.id for fact in by_value[:overflow])

    compacted = compact_memory(_memory_from_patch(memory, patch), now=now_value)
    _merge_patch(patch, compacted)
    _dedupe_canon_upserts(patch)
    _dedupe_generated_fact_upserts(patch)
    return patch
