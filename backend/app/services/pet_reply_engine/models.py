from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PetAgeStage = Literal["baby", "teen", "adult"]
PetMood = Literal["idle", "happy", "hungry", "sad"]
PetUserAction = Literal[
    "chat_message",
    "feed",
    "play",
    "clean",
    "pet",
    "idle_return",
    "creation_intro",
    "system_nudge",
]
MessageRole = Literal["user", "pet"]
EnergyBand = Literal["low", "medium", "high"]
HungerBand = Literal["low", "medium", "high"]
SocialStyle = Literal["clingy", "warm", "independent", "mischievous"]
Temperament = Literal["soft", "playful", "shy", "bold", "curious", "calm"]

PROMPT_LAYER_FIELDS: tuple[tuple[str, str], ...] = (
    ("ageStyle", "age_style"),
    ("moodStyle", "mood_style"),
    ("statNeeds", "stat_needs"),
    ("characterCore", "character_core"),
    ("importedSeedchat", "imported_seedchat"),
    ("lore", "lore"),
    ("characterBook", "character_book"),
    ("memory", "memory"),
    ("referenceCards", "reference_cards"),
    ("dialogueMoves", "dialogue_moves"),
    ("proactivity", "proactivity"),
    ("postHistoryInstructions", "post_history_instructions"),
)


@dataclass(frozen=True)
class PetStats:
    hunger: int
    happiness: int
    energy: int | None = None
    cleanliness: int | None = None


@dataclass(frozen=True)
class PetChatCues:
    body_words: tuple[str, ...] = ()
    sound_words: tuple[str, ...] = ()
    metaphor_words: tuple[str, ...] = ()
    avoid_in_speech: tuple[str, ...] = ()


@dataclass(frozen=True)
class PetVisualIdentity:
    raw_description: str
    safe_description: str | None = None
    species: str = ""
    visual_concept: str | None = None
    dominant_body_shape: str | None = None
    silhouette: str | None = None
    main_colors: tuple[str, ...] = ()
    accent_color: str | None = None
    signature_features: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    proportions: str | None = None
    accessories: tuple[str, ...] = ()
    baby_design: str | None = None
    teen_design: str | None = None
    adult_design: str | None = None
    do_not_change: tuple[str, ...] = ()
    chat_cues: PetChatCues = field(default_factory=PetChatCues)


@dataclass(frozen=True)
class PetPersonality:
    temperament: Temperament = "playful"
    social_style: SocialStyle = "warm"
    speech_flavor: str | None = "коротко, живо, немного нежно"
    favorite_words: tuple[str, ...] = ()
    forbidden_words: tuple[str, ...] = ()
    quirks: tuple[str, ...] = ()
    speech_rules: tuple[str, ...] = ()
    emotional_reactions: tuple[str, ...] = ()
    initiative_style: str | None = None
    sample_replies: tuple[str, ...] = ()
    avoid_patterns: tuple[str, ...] = ()
    opening_scenes: tuple[str, ...] = ()
    lorebook_entries: tuple[str, ...] = ()
    dialogue_moves: tuple[str, ...] = ()


@dataclass(frozen=True)
class PetReplyPet:
    age_stage: PetAgeStage
    mood: PetMood
    stats: PetStats
    visual_identity: PetVisualIdentity
    personality: PetPersonality
    lore: dict[str, Any] | None = None
    name: str | None = None
    character_profile_v2: dict[str, Any] | None = None
    effective_character_bible: dict[str, Any] | None = None


@dataclass(frozen=True)
class PetRecentMessage:
    role: MessageRole
    text: str


@dataclass(frozen=True)
class PetPromptLayers:
    age_style: bool = True
    mood_style: bool = True
    stat_needs: bool = True
    character_core: bool = True
    imported_seedchat: bool = True
    lore: bool = True
    character_book: bool = True
    memory: bool = True
    reference_cards: bool = True
    dialogue_moves: bool = True
    proactivity: bool = True
    post_history_instructions: bool = True

    def included_layer_names(self) -> tuple[str, ...]:
        return tuple(
            public_name
            for public_name, field_name in PROMPT_LAYER_FIELDS
            if getattr(self, field_name)
        )

    def excluded_layer_names(self) -> tuple[str, ...]:
        return tuple(
            public_name
            for public_name, field_name in PROMPT_LAYER_FIELDS
            if not getattr(self, field_name)
        )


@dataclass(frozen=True)
class PetReplyInput:
    user_action: PetUserAction
    pet: PetReplyPet
    user_text: str | None = None
    recent_messages: tuple[PetRecentMessage, ...] = ()
    lore_memories: tuple[str, ...] = ()
    memory_context: Any | None = None
    prompt_layers: PetPromptLayers = field(default_factory=PetPromptLayers)


@dataclass(frozen=True)
class PetPromptContext:
    detected_intent: str
    reference_cards: tuple[Any, ...] = ()
    speech_anchors: tuple[Any, ...] = ()
    rejected_speech_anchors: tuple[Any, ...] = ()
    expression_cues: tuple[Any, ...] = ()
    included_layers: tuple[str, ...] = ()
    excluded_layers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PetStateCues:
    age_cue: str
    mood_cue: str
    hunger_cue: str
    energy_cue: str
    action_cue: str
    hunger_band: HungerBand
    energy_band: EnergyBand
    cleanliness_cue: str | None = None
    recent_food_mention: bool = False


@dataclass(frozen=True)
class PetTextStyle:
    max_words: int
    max_chars: int
    sentence_limit: int
    style_rules: tuple[str, ...]


@dataclass(frozen=True)
class PetValidationResult:
    is_valid: bool
    normalized_reply: str
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PetReplyResult:
    reply: str
    mood_hint: PetMood | None = None
    used_fallback: bool = False
    validation_flags: tuple[str, ...] = ()
    lore_memories_to_save: tuple[str, ...] = ()
    proactive_intent: Any | None = None
    memory_candidates: tuple[Any, ...] = ()
    relationship_patch: Any | None = None
    development_patch: Any | None = None
    thread_patch: Any | None = None
    goal_patch: Any | None = None
    detected_intent: str | None = None
    reference_card_ids: tuple[str, ...] = ()
    speech_anchor_ids: tuple[str, ...] = ()
    speech_anchor_debug: tuple[Any, ...] = ()
    rejected_speech_anchor_debug: tuple[Any, ...] = ()
    quality_axes: dict[str, int] | None = None
    included_layers: tuple[str, ...] = ()
    excluded_layers: tuple[str, ...] = ()
    prompt_debug: tuple[Any, ...] = ()
