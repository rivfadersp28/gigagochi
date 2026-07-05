from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.pet_memory.models import PetMemoryStateV1, ProactiveIntent
from app.services.pet_reply_engine.models import PetAgeStage, PetMood, PetRecentMessage, PetStats

CLOSING_PATTERN = re.compile(
    r"^\s*(?:ок|окей|понял|поняла|спасибо|ладно|пока|хватит)[.!?…)]*\s*$", re.IGNORECASE
)
NO_QUESTION_PATTERN = re.compile(
    r"(?:без\s+вопросов|не\s+задавай|не\s+спрашивай|коротко)", re.IGNORECASE
)
SENSITIVE_PATTERN = re.compile(
    r"(?:болезн|диагноз|деньг|долг|политик|религи|парол|адрес|страшно|опасно)",
    re.IGNORECASE,
)
QUESTION_END_PATTERN = re.compile(r"\?\s*[)]?\s*$")


@dataclass(frozen=True)
class ProactivityDecision:
    reply: str
    allowed: bool
    flags: tuple[str, ...] = ()


def _pet_question_count(messages: tuple[PetRecentMessage, ...], limit: int = 2) -> int:
    pet_messages = [item.text for item in messages if item.role == "pet"]
    return sum(1 for text in pet_messages[-limit:] if "?" in text)


def _recent_question_rate(messages: tuple[PetRecentMessage, ...]) -> float:
    pet_messages = [item.text for item in messages if item.role == "pet"][-6:]
    if not pet_messages:
        return 0
    return sum(1 for text in pet_messages if "?" in text) / len(pet_messages)


def _strip_trailing_question(reply: str) -> str:
    text = reply.strip()
    if "?" not in text:
        return text
    parts = re.split(r"(?<=[.!?…])\s+", text)
    while parts and "?" in parts[-1]:
        parts.pop()
    stripped = " ".join(parts).strip()
    if stripped:
        return stripped
    return re.sub(r"\?.*$", ".", text).strip() or "я рядом."


def _limit_to_one_question(reply: str) -> str:
    if reply.count("?") <= 1:
        return reply
    first_question = reply.find("?")
    return reply[: first_question + 1].strip()


def _has_boundary(memory: PetMemoryStateV1) -> bool:
    return any(
        "не задавать вопрос" in boundary.casefold() or "без вопросов" in boundary.casefold()
        for boundary in memory.relationship.boundaries
    )


def _is_related(intent: ProactiveIntent, user_text: str | None, memory: PetMemoryStateV1) -> bool:
    if intent.kind in ("continue_lore", "return_to_thread", "request_care", "share_observation"):
        return True
    text = " ".join(
        [
            user_text or "",
            intent.text or "",
            " ".join(thread.topic for thread in memory.threads if thread.status == "open"),
            " ".join(goal.text for goal in memory.activeGoals if goal.status == "active"),
        ]
    ).casefold()
    words = set(re.findall(r"[А-Яа-яЁёA-Za-z0-9]{5,}", text))
    return len(words) >= 2


def apply_proactivity_gate(
    *,
    reply: str,
    proactive_intent: ProactiveIntent | None,
    recent_messages: tuple[PetRecentMessage, ...],
    memory: PetMemoryStateV1,
    user_text: str | None,
    age_stage: PetAgeStage,
    mood: PetMood,
    stats: PetStats,
) -> ProactivityDecision:
    intent = proactive_intent if proactive_intent and proactive_intent.kind != "none" else None
    flags: list[str] = []
    text = user_text or ""

    if _has_boundary(memory):
        flags.append("proactivity_blocked_boundary")
    if CLOSING_PATTERN.search(text):
        flags.append("proactivity_blocked_closing")
    if NO_QUESTION_PATTERN.search(text):
        flags.append("proactivity_blocked_user_request")
    if SENSITIVE_PATTERN.search(text):
        flags.append("proactivity_blocked_sensitive")
    if _pet_question_count(recent_messages, 2) >= 2:
        flags.append("proactivity_dropped_recent_questions")
    if _recent_question_rate(recent_messages) > 0.45:
        flags.append("proactivity_dropped_question_rate")
    if intent and intent.kind == "request_care":
        recent_pet_text = " ".join(item.text for item in recent_messages[-4:] if item.role == "pet")
        if mood in ("hungry", "sad") and re.search(
            r"(?:есть|ням|побудь|рядом|крош)", recent_pet_text, re.IGNORECASE
        ):
            flags.append("proactivity_dropped_repeated_care")
    if intent and not _is_related(intent, text, memory):
        flags.append("proactivity_dropped_unrelated")
    if age_stage == "baby" and intent and intent.text and len(intent.text) > 140:
        flags.append("proactivity_dropped_baby_long_question")
    if stats.hunger < 20 and intent and intent.kind == "ask_user":
        flags.append("proactivity_dropped_state_mismatch")

    allowed = not flags
    next_reply = reply.strip()
    if not allowed:
        return ProactivityDecision(
            reply=_strip_trailing_question(next_reply),
            allowed=False,
            flags=tuple(flags),
        )

    if (
        intent
        and intent.text
        and "?" not in next_reply
        and QUESTION_END_PATTERN.search(intent.text)
    ):
        separator = " " if next_reply.endswith((".", "!", "…", ")")) else ". "
        next_reply = f"{next_reply}{separator}{intent.text.strip()}"

    next_reply = _limit_to_one_question(next_reply)
    if "?" in next_reply:
        return ProactivityDecision(reply=next_reply, allowed=True, flags=("proactivity_allowed",))
    return ProactivityDecision(reply=next_reply, allowed=True, flags=())
