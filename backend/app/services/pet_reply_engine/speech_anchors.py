from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.pet_reply_engine.age_message_examples import adapt_template
from app.services.pet_reply_engine.models import PetAgeStage, PetReplyInput

DATA_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "age_speech_examples"
    / "creature_turn_examples_dataset.json"
)

WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")
ACTION_PATTERN = re.compile(r"\*([^*]{1,160})\*")
NO_QUESTION_PATTERN = re.compile(
    r"(?:не\s+задавай\s+вопрос|без\s+вопросов|не\s+спрашивай)",
    re.IGNORECASE,
)
CORRECTION_PATTERN = re.compile(
    r"(?:не\s+выдум|ты\s+это\s+придумал|это\s+не\s+правда|откуда\s+ты\s+это\s+знаешь|"
    r"не\s+придумывай)",
    re.IGNORECASE,
)

INTENT_ALIASES: dict[str, tuple[str, ...]] = {
    "smalltalk": ("greeting",),
    "appearance": ("status",),
    "memory_control": ("correction_no_hallucination",),
}
MOOD_ALIASES: dict[str, tuple[str, ...]] = {
    "idle": ("neutral", "calm", "warm", "observation"),
    "happy": ("happy", "warm", "excited", "happy_but_hiding", "proud"),
    "sad": ("sad", "sad_then_happy", "sad_then_warm", "softening"),
    "hungry": ("hungry", "happy_then_hungry", "small_desire"),
}
CREATURE_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "electric": (
        "электр",
        "молни",
        "искр",
        "заряд",
        "заряж",
        "магнит",
        "гроз",
        "ток",
        "electric",
        "spark",
        "charge",
        "storm",
    ),
    "fire": ("огон", "плам", "жар", "лав", "угол", "дракон", "fire", "flame", "ember"),
    "water": ("вод", "капл", "дожд", "рек", "озер", "море", "water", "rain", "drop"),
    "earth": ("земл", "кам", "скал", "гор", "глин", "пес", "earth", "stone", "rock"),
    "air": ("возд", "ветер", "крыл", "облак", "перо", "air", "wind", "cloud", "wing"),
    "ice": ("лед", "лёд", "снег", "иней", "мороз", "холод", "ice", "snow", "frost"),
    "dark": ("тень", "ноч", "темн", "сумрак", "dark", "shadow", "night"),
    "light": ("свет", "луч", "звезд", "звёзд", "солнеч", "light", "star", "sun"),
}
EXPRESSION_CATEGORIES_BY_INTENT: dict[str, tuple[str, ...]] = {
    "status": ("body_sensation", "relationship", "micro_observations"),
    "care": ("relationship", "body_sensation", "sensory"),
    "answer_lore": ("sensory", "micro_observations", "body_sensation"),
    "answer_preference": ("sensory", "micro_observations", "relationship"),
    "why": ("body_sensation", "sensory", "micro_observations"),
    "playful_offer": ("micro_observations", "sensory", "relationship"),
    "continue_thread": ("micro_observations", "relationship", "sensory"),
    "boundary": ("body_sensation", "relationship"),
    "smalltalk": ("micro_observations", "relationship", "sensory"),
    "greeting": ("relationship", "micro_observations"),
}
ENERGY_AXIS_FALLBACK = ("active", "calm", "slow")
INTELLECT_AXIS_FALLBACK = ("simple", "average", "sharp")
ENERGY_LEVEL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hyper": (
        "гипер",
        "таратор",
        "быстр",
        "неусид",
        "носит",
        "скач",
        "прыга",
        "молни",
        "искр",
        "электр",
        "hyper",
    ),
    "active": ("актив", "бодр", "энерг", "игрив", "озор", "смел", "playful", "bold"),
    "calm": ("спокой", "ровн", "мягк", "нежн", "береж", "calm", "soft", "gentle"),
    "slow": ("медл", "нетороп", "сон", "ленив", "задум", "тяжел", "slow"),
    "lethargic": ("летар", "вял", "молчал", "почти не говорит", "устал"),
}
INTELLECT_LEVEL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "simple": ("простодуш", "простой", "букваль", "наив", "глуп", "simple"),
    "average": ("обыч", "учится", "любопыт", "интерес", "curious", "average"),
    "sharp": (
        "остр",
        "умн",
        "наблюдат",
        "ирон",
        "хитр",
        "внимател",
        "анализ",
        "sharp",
    ),
    "genius": ("гений", "энциклопед", "мудр", "теори", "на три шага", "genius"),
}


@dataclass(frozen=True)
class SpeechAnchorExample:
    id: str
    stage: PetAgeStage
    intent: str
    mood: str
    user_text: str
    reply_anchor: str
    clean_reply_anchor: str
    nonverbal_cues: tuple[str, ...]
    dialogue_act: str
    proactivity_kind: str
    adaptation_mode: str
    may_invent: bool
    notes: str


@dataclass(frozen=True)
class SpeechAnchorCandidate:
    id: str
    stage: PetAgeStage
    intent: str
    mood: str
    user_text: str
    source_text: str
    nonverbal_cues: tuple[str, ...]
    dialogue_act: str
    proactivity_kind: str
    adaptation_mode: str
    may_invent: bool
    score: float
    score_reasons: tuple[str, ...]
    blocked_transfers: tuple[str, ...]
    notes: str

    def debug_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage,
            "intent": self.intent,
            "dialogueAct": self.dialogue_act,
            "adaptationMode": self.adaptation_mode,
            "score": round(self.score, 3),
            "scoreReasons": list(self.score_reasons),
        }


@dataclass(frozen=True)
class RejectedSpeechAnchor:
    id: str
    reason: str

    def debug_dict(self) -> dict[str, str]:
        return {"id": self.id, "reason": self.reason}


@dataclass(frozen=True)
class ExpressionVarietyCue:
    id: str
    kind: str
    label: str
    source_text: str
    guidance: str


def _tokens(text: str | None) -> set[str]:
    return {word.casefold() for word in WORD_PATTERN.findall(text or "")}


def _clean_anchor_text(text: str) -> tuple[str, tuple[str, ...]]:
    cues = tuple(match.strip() for match in ACTION_PATTERN.findall(text) if match.strip())
    cleaned = ACTION_PATTERN.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, cues


def _stage(value: str | None) -> PetAgeStage | None:
    return value if value in ("baby", "teen", "adult") else None


@lru_cache(maxsize=1)
def _load_dataset() -> dict[str, Any]:
    if not DATA_PATH.exists():
        return {}
    with DATA_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return {}
    return data


@lru_cache(maxsize=1)
def load_speech_anchor_examples() -> tuple[SpeechAnchorExample, ...]:
    data = _load_dataset()
    if not data:
        return ()

    examples: list[SpeechAnchorExample] = []

    def add_examples(container_intent: str, values: Any) -> None:
        if not isinstance(values, list):
            return
        for index, payload in enumerate(values):
            if not isinstance(payload, dict):
                continue
            stage = _stage(str(payload.get("stage") or ""))
            reply_anchor = str(payload.get("reply_anchor") or "").strip()
            if not stage or not reply_anchor:
                continue
            intent = str(payload.get("intent") or container_intent).strip() or container_intent
            clean_reply, cues = _clean_anchor_text(reply_anchor)
            examples.append(
                SpeechAnchorExample(
                    id=f"turn:{container_intent}:{stage}:{index:03d}",
                    stage=stage,
                    intent=intent,
                    mood=str(payload.get("mood") or "").strip(),
                    user_text=str(payload.get("user_text") or "").strip(),
                    reply_anchor=reply_anchor,
                    clean_reply_anchor=clean_reply,
                    nonverbal_cues=cues,
                    dialogue_act=str(payload.get("dialogue_act") or "").strip(),
                    proactivity_kind=str(payload.get("proactivity_kind") or "none").strip(),
                    adaptation_mode=str(payload.get("adaptation_mode") or "rhythm_only").strip(),
                    may_invent=bool(payload.get("may_invent")),
                    notes=str(payload.get("notes") or "").strip(),
                )
            )

    intents = data.get("intents")
    if isinstance(intents, dict):
        for intent, block in intents.items():
            examples_payload = block.get("examples") if isinstance(block, dict) else None
            add_examples(str(intent), examples_payload)

    proactive = data.get("proactive")
    if isinstance(proactive, dict):
        add_examples("proactive", proactive.get("examples"))

    return tuple(examples)


def _stable_index(seed: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.sha256(seed.casefold().encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def _collect_text(value: Any, parts: list[str], *, limit: int = 4000) -> None:
    if sum(len(part) for part in parts) > limit:
        return
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, list):
        for item in value[:20]:
            _collect_text(item, parts, limit=limit)
    elif isinstance(value, dict):
        for item in list(value.values())[:40]:
            _collect_text(item, parts, limit=limit)


def _creature_context_text(reply_input: PetReplyInput) -> str:
    pet = reply_input.pet
    parts = [
        reply_input.user_text or "",
        pet.visual_identity.raw_description,
        pet.visual_identity.safe_description or "",
        pet.visual_identity.species,
        " ".join(pet.visual_identity.signature_features),
        " ".join(pet.visual_identity.chat_cues.metaphor_words),
        pet.personality.speech_flavor or "",
    ]
    _collect_text(pet.lore, parts)
    _collect_text(pet.character_profile_v2, parts)
    return " ".join(parts)


def _axis_context_text(reply_input: PetReplyInput) -> str:
    pet = reply_input.pet
    personality = pet.personality
    parts = [
        pet.visual_identity.raw_description,
        pet.visual_identity.safe_description or "",
        pet.visual_identity.species,
        " ".join(pet.visual_identity.signature_features),
        " ".join(pet.visual_identity.chat_cues.metaphor_words),
        personality.temperament,
        personality.social_style,
        personality.speech_flavor or "",
        " ".join(personality.speech_rules),
        " ".join(personality.emotional_reactions),
        " ".join(personality.quirks),
    ]
    _collect_text(pet.lore, parts, limit=3500)
    _collect_text(pet.character_profile_v2, parts, limit=3500)
    return " ".join(parts).casefold()


def _select_creature_speech_type(reply_input: PetReplyInput) -> str | None:
    text = _creature_context_text(reply_input).casefold()
    scored = []
    for type_name, keywords in CREATURE_TYPE_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in text)
        if score:
            scored.append((score, type_name))
    if not scored:
        return None
    return max(scored, key=lambda item: (item[0], item[1]))[1]


def _select_axis_level(
    *,
    levels: dict[str, Any],
    keyword_map: dict[str, tuple[str, ...]],
    fallback_levels: tuple[str, ...],
    context_text: str,
    seed: str,
) -> str | None:
    if not levels:
        return None
    scored: list[tuple[int, int, str]] = []
    for order, level in enumerate(levels):
        keywords = keyword_map.get(level, ())
        score = sum(1 for keyword in keywords if keyword in context_text)
        if score:
            scored.append((score, -order, level))
    if scored:
        return max(scored)[2]

    available_fallback = tuple(level for level in fallback_levels if level in levels)
    candidates = available_fallback or tuple(levels)
    return candidates[_stable_index(seed, len(candidates))] if candidates else None


def _axis_stage_example(payload: dict[str, Any], age_stage: PetAgeStage) -> str:
    for stage in (age_stage, "adult", "teen", "baby"):
        value = payload.get(stage)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _character_axis_cues(
    reply_input: PetReplyInput,
    *,
    limit: int = 2,
) -> tuple[ExpressionVarietyCue, ...]:
    dataset = _load_dataset()
    axes = dataset.get("character_axes")
    if not isinstance(axes, dict) or limit <= 0:
        return ()

    context_text = _axis_context_text(reply_input)
    seed = " ".join(
        (
            reply_input.pet.visual_identity.raw_description,
            reply_input.pet.visual_identity.species,
            reply_input.pet.personality.speech_flavor or "",
        )
    )
    selected: list[ExpressionVarietyCue] = []

    axis_configs = (
        (
            "energy",
            axes.get("energy_spectrum"),
            ENERGY_LEVEL_KEYWORDS,
            ENERGY_AXIS_FALLBACK,
            ("tempo", "words_per_turn", "pattern"),
            "Apply as speech tempo and amount of motion; stay inside final reply limits.",
        ),
        (
            "intellect",
            axes.get("intellect_spectrum"),
            INTELLECT_LEVEL_KEYWORDS,
            INTELLECT_AXIS_FALLBACK,
            ("vocabulary", "sentence_structure", "pattern"),
            "Apply as thinking style and vocabulary; do not turn the reply into an explanation.",
        ),
    )

    for (
        axis_name,
        axis_payload,
        keyword_map,
        fallback_levels,
        fields,
        base_guidance,
    ) in axis_configs:
        levels = axis_payload.get("levels") if isinstance(axis_payload, dict) else None
        if not isinstance(levels, dict):
            continue
        level = _select_axis_level(
            levels=levels,
            keyword_map=keyword_map,
            fallback_levels=fallback_levels,
            context_text=context_text,
            seed=f"{axis_name}:{seed}",
        )
        payload = levels.get(level) if level else None
        if not isinstance(payload, dict):
            continue
        label = str(payload.get("name") or level).strip()
        guidance_parts = [
            base_guidance,
            *(
                str(payload.get(field) or "").strip()
                for field in fields
                if str(payload.get(field) or "").strip()
            ),
        ]
        source_text = adapt_template(
            _axis_stage_example(payload, reply_input.pet.age_stage),
            reply_input,
        )
        selected.append(
            ExpressionVarietyCue(
                id=f"character_axis:{axis_name}:{level}",
                kind="character_axis",
                label=label,
                source_text=source_text,
                guidance="; ".join(guidance_parts),
            )
        )
        if len(selected) >= limit:
            break

    return tuple(selected)


def select_expression_variety_cues(
    reply_input: PetReplyInput,
    detected_intent: str,
    *,
    limit: int = 4,
) -> tuple[ExpressionVarietyCue, ...]:
    dataset = _load_dataset()
    if not dataset or limit <= 0:
        return ()

    cues: list[ExpressionVarietyCue] = []
    speech_styles = dataset.get("speech_styles")
    speech_types = speech_styles.get("types") if isinstance(speech_styles, dict) else None
    creature_type = _select_creature_speech_type(reply_input)
    if creature_type and isinstance(speech_types, dict):
        style = speech_types.get(creature_type)
        if isinstance(style, dict):
            examples = style.get("examples")
            raw_example = (
                examples.get(reply_input.pet.age_stage) if isinstance(examples, dict) else None
            )
            source = adapt_template(str(raw_example or ""), reply_input)
            guidance = "; ".join(
                str(style.get(key) or "").strip()
                for key in ("rhythm", "tempo", "notes")
                if str(style.get(key) or "").strip()
            )
            if source or guidance:
                cues.append(
                    ExpressionVarietyCue(
                        id=f"speech_style:{creature_type}:{reply_input.pet.age_stage}",
                        kind="speech_style",
                        label=creature_type,
                        source_text=source,
                        guidance=guidance,
                    )
                )

    for cue in _character_axis_cues(reply_input, limit=2):
        if len(cues) >= limit:
            break
        cues.append(cue)

    emotional = dataset.get("emotional_phrases")
    categories = emotional.get("categories") if isinstance(emotional, dict) else None
    category_names = EXPRESSION_CATEGORIES_BY_INTENT.get(
        detected_intent,
        EXPRESSION_CATEGORIES_BY_INTENT["smalltalk"],
    )
    if isinstance(categories, dict):
        for category in category_names:
            if len(cues) >= limit:
                break
            block = categories.get(category)
            examples = block.get("examples") if isinstance(block, dict) else None
            if not isinstance(examples, list) or not examples:
                continue
            index = _stable_index(
                f"{reply_input.pet.age_stage}:{detected_intent}:{category}:{reply_input.user_text}",
                len(examples),
            )
            source = adapt_template(str(examples[index]), reply_input)
            label = str(block.get("label") or category)
            cues.append(
                ExpressionVarietyCue(
                    id=f"expression:{category}:{index:03d}",
                    kind="expression_channel",
                    label=label,
                    source_text=source,
                    guidance=(
                        "Use this as a channel for showing character: feeling, relationship, "
                        "sensory detail or micro-observation, not as canon."
                    ),
                )
            )

    return tuple(cues[:limit])


def _intent_targets(detected_intent: str, user_text: str | None) -> tuple[str, ...]:
    if CORRECTION_PATTERN.search(user_text or ""):
        return ("correction_no_hallucination",)
    return (detected_intent, *INTENT_ALIASES.get(detected_intent, ()))


def _recent_reply_tokens(reply_input: PetReplyInput) -> set[str]:
    return {
        _normalize_copy_text(item.text)
        for item in reply_input.recent_messages[-6:]
        if item.role == "pet"
    }


def _normalize_copy_text(text: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(r"[\"'«»“”„*.,!?…:;()—-]+", " ", text.casefold()),
    ).strip()


def _score_example(
    example: SpeechAnchorExample,
    *,
    reply_input: PetReplyInput,
    detected_intent: str,
    target_intents: tuple[str, ...],
    query_tokens: set[str],
    recent_replies: set[str],
) -> tuple[float, tuple[str, ...], str | None]:
    if example.stage != reply_input.pet.age_stage:
        return 0.0, (), "stage_mismatch"
    if (
        NO_QUESTION_PATTERN.search(reply_input.user_text or "")
        and "?" in example.clean_reply_anchor
    ):
        return 0.0, (), "user_asked_no_questions"
    if _normalize_copy_text(example.clean_reply_anchor) in recent_replies:
        return 0.0, (), "recent_repeat"

    score = 0.0
    reasons: list[str] = []
    if example.intent in target_intents:
        score += 0.42
        reasons.append("intent_match")
    elif detected_intent == "smalltalk" and example.intent == "proactive":
        score += 0.12
        reasons.append("smalltalk_proactive_fallback")

    mood_targets = MOOD_ALIASES.get(reply_input.pet.mood, ())
    if example.mood in mood_targets:
        score += 0.18
        reasons.append("mood_match")

    example_tokens = _tokens(f"{example.user_text} {example.clean_reply_anchor} {example.notes}")
    overlap = query_tokens & example_tokens
    if overlap:
        score += min(len(overlap), 5) * 0.045
        reasons.append("lexical_overlap")

    if example.dialogue_act:
        score += 0.06
        reasons.append("dialogue_act")
    if example.proactivity_kind != "none" and reply_input.prompt_layers.proactivity:
        score += 0.05
        reasons.append("proactivity_shape")
    if example.may_invent:
        score += 0.03
        reasons.append("safe_invention_allowed")

    if score <= 0:
        return 0.0, (), "no_score"
    return score, tuple(reasons), None


def select_speech_anchors(
    reply_input: PetReplyInput,
    detected_intent: str,
    *,
    limit: int = 3,
) -> tuple[tuple[SpeechAnchorCandidate, ...], tuple[RejectedSpeechAnchor, ...]]:
    if limit <= 0:
        return (), ()
    target_intents = _intent_targets(detected_intent, reply_input.user_text)
    query_tokens = _tokens(
        " ".join(
            (
                reply_input.user_text or "",
                detected_intent,
                reply_input.pet.visual_identity.species,
                reply_input.pet.personality.speech_flavor or "",
            )
        )
    )
    recent_replies = _recent_reply_tokens(reply_input)
    rejected: list[RejectedSpeechAnchor] = []
    scored: list[SpeechAnchorCandidate] = []

    for example in load_speech_anchor_examples():
        score, reasons, rejected_reason = _score_example(
            example,
            reply_input=reply_input,
            detected_intent=detected_intent,
            target_intents=target_intents,
            query_tokens=query_tokens,
            recent_replies=recent_replies,
        )
        if rejected_reason:
            if len(rejected) < 8 and example.intent in target_intents:
                rejected.append(RejectedSpeechAnchor(example.id, rejected_reason))
            continue
        if score < 0.36:
            continue
        adapted_source = adapt_template(example.clean_reply_anchor, reply_input)
        scored.append(
            SpeechAnchorCandidate(
                id=example.id,
                stage=example.stage,
                intent=example.intent,
                mood=example.mood,
                user_text=example.user_text,
                source_text=adapted_source,
                nonverbal_cues=example.nonverbal_cues,
                dialogue_act=example.dialogue_act,
                proactivity_kind=example.proactivity_kind,
                adaptation_mode=example.adaptation_mode or "rhythm_only",
                may_invent=example.may_invent,
                score=score,
                score_reasons=reasons,
                blocked_transfers=(
                    "body_parts",
                    "home",
                    "friend_names",
                    "family",
                    "species",
                    "past_events",
                ),
                notes=example.notes,
            )
        )

    selected = sorted(
        scored,
        key=lambda item: (
            item.score,
            item.intent == detected_intent,
            item.proactivity_kind != "none",
            -len(item.source_text),
            item.id,
        ),
        reverse=True,
    )[:limit]
    return tuple(selected), tuple(rejected)


def format_speech_anchors_for_prompt(anchors: tuple[SpeechAnchorCandidate, ...]) -> str:
    if not anchors:
        return "- нет"
    lines: list[str] = [
        "Ближайшие речевые примеры. Бери из них ритм, настроение, слова или форму, "
        "адаптируй свободно под текущего персонажа."
    ]
    for index, anchor in enumerate(anchors, start=1):
        nonverbal = ", ".join(anchor.nonverbal_cues[:2])
        cue = f"; жест/сцена для внутреннего ощущения: {nonverbal}" if anchor.nonverbal_cues else ""
        lines.append(
            f"- {'primary' if index == 1 else 'secondary'}: {anchor.id} "
            f"[intent={anchor.intent}; act={anchor.dialogue_act}; mode={anchor.adaptation_mode}; "
            f"score={anchor.score:.2f}]"
        )
        lines.append(f"  source_text: {anchor.source_text}{cue}")
        lines.append("  use: ритм, длина, эмоция, форма инициативы")
        if anchor.notes:
            lines.append(f"  note: {anchor.notes}")
    return "\n".join(lines)


def format_expression_variety_for_prompt(cues: tuple[ExpressionVarietyCue, ...]) -> str:
    if not cues:
        return "- нет"
    lines = [
        "Подсказки для разнообразия речи: темп, мышление, сенсорика, отношение, жест.",
        "Это не готовый ответ; смешивай с персонажем и текущей репликой.",
    ]
    for cue in cues:
        lines.append(f"- {cue.id} [{cue.kind}; {cue.label}]")
        if cue.guidance:
            lines.append(f"  guidance: {cue.guidance}")
        if cue.source_text:
            lines.append(f"  source_text: {cue.source_text}")
    return "\n".join(lines)


def all_turn_anchor_texts(age_stage: PetAgeStage) -> tuple[str, ...]:
    return tuple(
        example.clean_reply_anchor
        for example in load_speech_anchor_examples()
        if example.stage == age_stage and example.clean_reply_anchor
    )
