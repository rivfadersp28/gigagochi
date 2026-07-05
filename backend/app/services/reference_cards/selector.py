from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")


@dataclass(frozen=True)
class ReferenceCard:
    id: str
    type: str
    locale: str
    source_family: str
    source_url: str
    license_note: str
    tags: tuple[str, ...]
    use_for: tuple[str, ...]
    trigger_intents: tuple[str, ...]
    pattern: str
    positive_constraints: tuple[str, ...]
    negative_constraints: tuple[str, ...]
    example: str

    @property
    def text_for_search(self) -> str:
        return " ".join(
            (
                self.id,
                self.type,
                *self.tags,
                *self.trigger_intents,
                self.pattern,
                *self.positive_constraints,
                *self.negative_constraints,
                self.example,
            )
        )


def _strings(value: Any, *, limit: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        if len(result) >= limit:
            break
    return tuple(result)


def _card_from_payload(payload: Mapping[str, Any]) -> ReferenceCard | None:
    card_id = str(payload.get("id") or "").strip()
    card_type = str(payload.get("type") or "").strip()
    pattern = str(payload.get("pattern") or "").strip()
    if not card_id or not card_type or not pattern:
        return None
    return ReferenceCard(
        id=card_id,
        type=card_type,
        locale=str(payload.get("locale") or "ru").strip(),
        source_family=str(payload.get("source_family") or "").strip(),
        source_url=str(payload.get("source_url") or "").strip(),
        license_note=str(payload.get("license_note") or "").strip(),
        tags=_strings(payload.get("tags"), limit=20),
        use_for=_strings(payload.get("use_for"), limit=10),
        trigger_intents=_strings(payload.get("trigger_intents"), limit=12),
        pattern=pattern,
        positive_constraints=_strings(payload.get("positive_constraints"), limit=10),
        negative_constraints=_strings(payload.get("negative_constraints"), limit=10),
        example=str(payload.get("example") or "").strip(),
    )


def _default_cards_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "reference_cards"


@lru_cache(maxsize=8)
def load_reference_cards(
    locale: str = "ru",
    data_dir: str | None = None,
) -> tuple[ReferenceCard, ...]:
    cards_dir = Path(data_dir) if data_dir else _default_cards_dir()
    cards: list[ReferenceCard] = []
    for path in sorted(cards_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            card = _card_from_payload(payload)
            if card and card.locale == locale:
                cards.append(card)
    return tuple(cards)


def _tokens(text: str | None) -> tuple[str, ...]:
    return tuple(token.casefold() for token in TOKEN_PATTERN.findall(text or ""))


def _profile_text(character_profile: Mapping[str, Any] | None) -> str:
    if not character_profile:
        return ""
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

    collect(character_profile)
    return " ".join(parts)


def _document_frequency(cards: Iterable[ReferenceCard]) -> dict[str, int]:
    frequencies: dict[str, int] = {}
    for card in cards:
        for token in set(_tokens(card.text_for_search)):
            frequencies[token] = frequencies.get(token, 0) + 1
    return frequencies


def _bm25_score(
    card: ReferenceCard,
    query_tokens: tuple[str, ...],
    df: dict[str, int],
    n_docs: int,
) -> float:
    if not query_tokens:
        return 0.0
    doc_tokens = _tokens(card.text_for_search)
    if not doc_tokens:
        return 0.0
    doc_len = len(doc_tokens)
    avg_len = 34
    k1 = 1.2
    b = 0.75
    score = 0.0
    for token in set(query_tokens):
        tf = doc_tokens.count(token)
        if tf == 0:
            continue
        idf = math.log(1 + (n_docs - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5))
        denom = tf + k1 * (1 - b + b * doc_len / avg_len)
        score += idf * (tf * (k1 + 1)) / denom
    return score


def _score_card(
    card: ReferenceCard,
    *,
    intent: str,
    query_tokens: tuple[str, ...],
    profile_tokens: set[str],
    df: dict[str, int],
    n_docs: int,
) -> float:
    score = _bm25_score(card, query_tokens, df, n_docs)
    if intent in card.trigger_intents:
        score += 4.0
    if "reply_prompt" in card.use_for:
        score += 1.2
    if "character_generation" in card.use_for and intent in {"answer_lore", "answer_preference"}:
        score += 0.4
    tag_overlap = set(card.tags) & profile_tokens
    score += min(len(tag_overlap), 4) * 0.35
    if card.type == "dialogue_act" and intent in card.trigger_intents:
        score += 1.5
    if card.type == "negative_pattern":
        score += 0.8
    if card.type == "voice_example":
        score += 0.35
    return score


def select_reference_cards(
    *,
    user_text: str | None,
    intent: str,
    character_profile: Mapping[str, Any] | None = None,
    limit: int = 5,
    cards: tuple[ReferenceCard, ...] | None = None,
) -> tuple[ReferenceCard, ...]:
    available = cards if cards is not None else load_reference_cards()
    if not available or limit <= 0:
        return ()

    profile = _profile_text(character_profile)
    query_tokens = _tokens(" ".join((user_text or "", intent, profile[:800])))
    profile_tokens = set(_tokens(profile))
    df = _document_frequency(available)
    scored = sorted(
        (
            (
                _score_card(
                    card,
                    intent=intent,
                    query_tokens=query_tokens,
                    profile_tokens=profile_tokens,
                    df=df,
                    n_docs=len(available),
                ),
                card,
            )
            for card in available
        ),
        key=lambda item: (item[0], item[1].type == "dialogue_act", item[1].id),
        reverse=True,
    )

    selected: list[ReferenceCard] = []
    for score, card in scored:
        if score <= 0:
            continue
        if card in selected:
            continue
        selected.append(card)
        if len(selected) >= limit:
            break

    if selected and not any(card.type == "negative_pattern" for card in selected):
        negative = next((card for _, card in scored if card.type == "negative_pattern"), None)
        if negative:
            selected = [*selected[: max(0, limit - 1)], negative]
    return tuple(selected[:limit])


def format_reference_cards_for_prompt(cards: tuple[ReferenceCard, ...]) -> str:
    if not cards:
        return "- нет"
    lines: list[str] = []
    for card in cards:
        do = "; ".join(card.positive_constraints[:3]) or "нет"
        avoid = "; ".join(card.negative_constraints[:3]) or "нет"
        example = f"; example_do_not_copy: {card.example}" if card.example else ""
        lines.append(
            f"- {card.id} [{card.type}; intents={','.join(card.trigger_intents)}]: "
            f"pattern: {card.pattern}; do: {do}; avoid: {avoid}{example}"
        )
    return "\n".join(lines)
