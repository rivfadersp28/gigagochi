from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PetAgeStage = Literal["baby", "teen", "adult"]
PetMood = Literal["idle", "happy", "hungry", "sad"]
PetUserAction = Literal["chat_message"]
MessageRole = Literal["user", "pet"]
EnergyBand = Literal["low", "medium", "high"]
HungerBand = Literal["low", "medium", "high"]
SocialStyle = Literal["clingy", "warm", "independent", "mischievous"]
Temperament = Literal["soft", "playful", "shy", "bold", "curious", "calm"]


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
class PetReplyInput:
    user_action: PetUserAction
    pet: PetReplyPet
    user_text: str | None = None
    recent_messages: tuple[PetRecentMessage, ...] = ()


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
