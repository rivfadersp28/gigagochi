from __future__ import annotations

import re
from typing import Any

from app.schemas import LocalChatRequest
from app.services.pet_reply_engine.speech_runtime import context_source_mode
from app.services.temporal_context import format_temporal_reference, temporal_age_days

MAX_RECENT_EVENTS_CONTEXT_ITEMS = 3


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _text_value(value: Any) -> str:
    return _compact_spaces(str(value or ""))


RECENT_EVENT_RECENT_RE = re.compile(
    r"\b("
    r"недавн\w*|последн\w*|сейчас|теперь|случил\w*|"
    r"произош\w*|истори\w*|помн\w*|вчера|позавчера|назад"
    r")\b",
    re.IGNORECASE,
)
RECENT_EVENT_STATUS_RE = re.compile(
    r"\b("
    r"украл\w*|украден\w*|утащил\w*|вернул\w*|верну\w*|"
    r"защитил\w*|потерял\w*|потерян\w*|где|наш[её]л\w*|"
    r"нашл\w*|остал\w*)\b",
    re.IGNORECASE,
)
RECENT_EVENT_STOPWORDS = {
    "что",
    "как",
    "где",
    "кто",
    "это",
    "тебя",
    "тебе",
    "твой",
    "твоя",
    "твое",
    "твоё",
    "ты",
    "он",
    "она",
    "оно",
    "его",
    "ее",
    "её",
    "про",
    "или",
    "уже",
    "сейчас",
    "после",
}
RECENT_EVENT_RUSSIAN_ENDINGS = (
    "иями",
    "ями",
    "ами",
    "ого",
    "ему",
    "ому",
    "ыми",
    "ими",
    "иях",
    "ах",
    "ях",
    "ов",
    "ев",
    "ей",
    "ом",
    "ем",
    "ам",
    "ям",
    "ую",
    "юю",
    "ая",
    "яя",
    "ое",
    "ее",
    "ок",
    "ек",
    "ы",
    "и",
    "а",
    "я",
    "у",
    "ю",
    "е",
    "о",
)


def _recent_event_stem(token: str) -> str:
    normalized = token.replace("ё", "е")
    if not re.fullmatch(r"[а-я]+", normalized) or len(normalized) < 5:
        return normalized
    for ending in RECENT_EVENT_RUSSIAN_ENDINGS:
        if normalized.endswith(ending) and len(normalized) - len(ending) >= 4:
            return normalized[: -len(ending)]
    return normalized


def _recent_event_tokens(value: Any) -> set[str]:
    text = _compact_spaces(str(value or "")).casefold()
    tokens = re.findall(r"[0-9a-zа-яё]{3,}", text, flags=re.IGNORECASE)
    return {_recent_event_stem(token) for token in tokens if token not in RECENT_EVENT_STOPWORDS}


def _recent_event_token_overlap(query_tokens: set[str], event_tokens: set[str]) -> int:
    overlap = len(query_tokens & event_tokens)
    if overlap:
        return overlap
    for query_token in query_tokens:
        for event_token in event_tokens:
            shorter, longer = sorted((query_token, event_token), key=len)
            if len(shorter) >= 5 and longer.startswith(shorter):
                return 1
    return 0


def _recent_story_events_from_pet(pet: Any) -> list[dict[str, Any]]:
    bible = pet.characterBible if _is_record(getattr(pet, "characterBible", None)) else {}
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    events = extensions.get("recent_story_events") if _is_record(extensions) else []
    return [event for event in events if _is_record(event)] if isinstance(events, list) else []


def _recent_event_text_parts(event: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key in ("title", "summary", "compactText", "eventType", "outcome", "location"):
        value = event.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("participants", "objects", "actions", "canonicalFacts", "tags"):
        value = event.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if isinstance(item, (str, int, float)))
    status_changes = event.get("statusChanges")
    if isinstance(status_changes, list):
        for item in status_changes:
            if _is_record(item):
                parts.extend(
                    str(item.get(key) or "")
                    for key in ("entity", "state", "owner")
                    if item.get(key)
                )
    return parts


def _recent_event_id(event: dict[str, Any], index: int) -> str:
    return _text_value(event.get("id")) or f"recent_event_{index}"


def _select_recent_events_for_text(
    *,
    events: list[dict[str, Any]],
    text: str,
    mode: str,
    now_iso: str | None = None,
    timezone: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    debug: dict[str, Any] = {
        "mode": mode,
        "includedEventIds": [],
        "triggerReason": "",
    }
    if mode == "disabled" or not events:
        debug["triggerReason"] = "disabled_or_empty"
        return [], debug

    newest = list(reversed(events[-10:]))
    if mode == "always":
        selected = newest[:MAX_RECENT_EVENTS_CONTEXT_ITEMS]
        debug["includedEventIds"] = [
            _recent_event_id(event, idx) for idx, event in enumerate(selected)
        ]
        debug["triggerReason"] = "always"
        return selected, debug

    text_tokens = _recent_event_tokens(text)
    has_recent_intent = bool(RECENT_EVENT_RECENT_RE.search(text))
    has_status_intent = bool(RECENT_EVENT_STATUS_RE.search(text))
    candidates: list[tuple[dict[str, Any], str]] = []
    for event in newest:
        event_time = _text_value(event.get("generatedAt")) or _text_value(event.get("createdAt"))
        age_days = temporal_age_days(event_time, now_iso=now_iso, timezone=timezone)
        if age_days is not None and age_days > 30 and not has_recent_intent:
            continue
        event_tokens = set().union(
            *(_recent_event_tokens(part) for part in _recent_event_text_parts(event))
        )
        overlap = _recent_event_token_overlap(text_tokens, event_tokens)
        if overlap <= 0 and not has_recent_intent:
            continue
        reason = "token_overlap"
        if has_status_intent and overlap > 0:
            reason = "status_overlap"
        if has_recent_intent:
            reason = "recent_intent" if overlap == 0 else reason
        candidates.append((event, reason))

    selected_pairs = candidates[:MAX_RECENT_EVENTS_CONTEXT_ITEMS]
    selected = [item[0] for item in selected_pairs]
    debug["includedEventIds"] = [_recent_event_id(event, idx) for idx, event in enumerate(selected)]
    debug["triggerReason"] = selected_pairs[0][1] if selected_pairs else "no_match"
    return selected, debug


def _format_recent_events_block(
    events: list[dict[str, Any]],
    *,
    now_iso: str | None = None,
    timezone: str | None = None,
) -> str | None:
    if not events:
        return None
    lines = [
        "Недавние события персонажа:",
        "Это устойчивый контекст. Не противоречь ему; если общий лор спорит с ним, "
        "опирайся на недавнее событие.",
    ]
    for index, event in enumerate(events, start=1):
        title = _text_value(event.get("title")) or f"Событие {index}"
        summary = _text_value(event.get("summary")) or _text_value(event.get("compactText"))
        lines.append(f"{index}. {title}")
        temporal = format_temporal_reference(
            _text_value(event.get("generatedAt")) or _text_value(event.get("createdAt")),
            now_iso=now_iso,
            timezone=timezone,
        )
        if temporal:
            lines.append(f"Произошло: {temporal}")
        if summary:
            lines.append(f"Кратко: {summary}")
        raw_canonical_facts = event.get("canonicalFacts")
        canonical_facts = [
            _text_value(item)
            for item in (raw_canonical_facts if isinstance(raw_canonical_facts, list) else [])
            if _text_value(item)
        ][:5]
        if canonical_facts:
            lines.append("Факты:")
            lines.extend(f"- {fact}" for fact in canonical_facts)
        status_changes = event.get("statusChanges")
        if isinstance(status_changes, list):
            status_lines: list[str] = []
            for item in status_changes[:5]:
                if not _is_record(item):
                    continue
                entity = _text_value(item.get("entity"))
                state = _text_value(item.get("state"))
                owner = _text_value(item.get("owner"))
                if entity and state:
                    owner_suffix = f" ({owner})" if owner else ""
                    status_lines.append(f"- {entity}: {state}{owner_suffix}")
            if status_lines:
                lines.append("Изменения:")
                lines.extend(status_lines)
    return "\n".join(lines)


def _recent_events_context_for_chat(payload: LocalChatRequest) -> tuple[str | None, dict[str, Any]]:
    mode = context_source_mode("chat", "recentEvents")
    events = _recent_story_events_from_pet(payload.pet)
    selected, debug = _select_recent_events_for_text(
        events=events,
        text=payload.message,
        mode=mode,
        now_iso=payload.nowIso,
        timezone=payload.timezone,
    )
    return _format_recent_events_block(
        selected,
        now_iso=payload.nowIso,
        timezone=payload.timezone,
    ), debug
