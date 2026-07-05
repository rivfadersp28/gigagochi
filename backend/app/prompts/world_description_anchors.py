from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")

DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "world_descriptions"
    / "world_descriptions_dataset.json"
)

HABITAT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "forest": (
        "лес",
        "дерев",
        "лист",
        "мох",
        "гриб",
        "корень",
        "семеч",
        "растен",
        "цвет",
        "сад",
        "forest",
        "leaf",
        "plant",
        "moss",
        "mushroom",
    ),
    "mountain": (
        "гор",
        "скал",
        "кам",
        "снег",
        "лед",
        "коз",
        "орел",
        "ветер",
        "mountain",
        "stone",
        "rock",
        "snow",
        "ice",
    ),
    "waters-edge": (
        "вод",
        "озер",
        "рек",
        "руч",
        "море",
        "океан",
        "дожд",
        "капл",
        "рыб",
        "ракуш",
        "water",
        "river",
        "lake",
        "rain",
        "fish",
        "shell",
    ),
    "grassland": (
        "луг",
        "степ",
        "трава",
        "поле",
        "пчел",
        "бабоч",
        "нектар",
        "grass",
        "meadow",
        "field",
        "bee",
        "butterfly",
    ),
    "cave": (
        "пещ",
        "крист",
        "подзем",
        "темн",
        "шахт",
        "минерал",
        "летуч",
        "cave",
        "crystal",
        "underground",
        "mineral",
    ),
    "urban": (
        "город",
        "дом",
        "чердак",
        "метро",
        "улиц",
        "крыша",
        "робот",
        "механ",
        "час",
        "urban",
        "city",
        "attic",
        "metro",
        "robot",
        "clock",
    ),
    "sky": (
        "небо",
        "облак",
        "крыл",
        "птиц",
        "ветер",
        "звезд",
        "косм",
        "лет",
        "sky",
        "cloud",
        "wing",
        "bird",
        "star",
        "cosmic",
    ),
    "volcanic": (
        "огон",
        "плам",
        "лав",
        "вулкан",
        "жар",
        "пепел",
        "угол",
        "дракон",
        "fire",
        "flame",
        "lava",
        "volcano",
        "ember",
        "dragon",
    ),
}


@dataclass(frozen=True)
class WorldDescriptionAnchor:
    id: str
    habitat: str
    label: str
    vibe: str
    prompt_template: str
    source_text: str
    score: float
    reasons: tuple[str, ...] = ()

    def debug_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "habitat": self.habitat,
            "label": self.label,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
            "sourceText": self.source_text,
        }


def _words(text: str | None) -> set[str]:
    return {word.casefold() for word in WORD_PATTERN.findall(text or "")}


def _stable_index(text: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.sha256(text.casefold().encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


@lru_cache(maxsize=1)
def load_world_description_dataset() -> dict[str, Any]:
    if not DATASET_PATH.exists():
        return {"habitats": {}}
    try:
        return json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"habitats": {}}


def _habitat_keyword_score(habitat: str, description: str) -> float:
    lowered = description.casefold()
    keywords = HABITAT_KEYWORDS.get(habitat, ())
    return sum(1.0 for keyword in keywords if keyword in lowered)


def _textual_overlap_score(description_words: set[str], *texts: str) -> float:
    if not description_words:
        return 0.0
    text_words = set().union(*(_words(text) for text in texts))
    return min(len(description_words & text_words), 8) * 0.18


def select_world_description_anchors(
    user_description: str,
    *,
    count: int = 5,
) -> tuple[WorldDescriptionAnchor, ...]:
    dataset = load_world_description_dataset()
    habitats = dataset.get("habitats") if isinstance(dataset, dict) else None
    if not isinstance(habitats, dict) or not habitats:
        return ()

    description = user_description.strip()
    description_words = _words(description)
    habitat_scores: dict[str, float] = {}
    for habitat, payload in habitats.items():
        if not isinstance(payload, dict):
            continue
        examples = payload.get("examples")
        if not isinstance(examples, list) or not examples:
            continue
        keyword_score = _habitat_keyword_score(habitat, description) * 2.4
        overlap_score = _textual_overlap_score(
            description_words,
            str(payload.get("label", "")),
            str(payload.get("vibe", "")),
            str(payload.get("prompt_template", "")),
            " ".join(str(example) for example in examples[:8]),
        )
        habitat_scores[habitat] = keyword_score + overlap_score

    if not habitat_scores:
        return ()

    best_score = max(habitat_scores.values())
    if best_score <= 0:
        habitat_names = sorted(habitat_scores)
        primary = habitat_names[_stable_index(description or "world", len(habitat_names))]
        habitat_scores[primary] = 1.0

    ranked_habitats = sorted(
        habitat_scores,
        key=lambda habitat: (habitat_scores[habitat], habitat),
        reverse=True,
    )
    selected: list[WorldDescriptionAnchor] = []
    used_examples: set[str] = set()
    habitat_round = ranked_habitats[: max(2, min(4, len(ranked_habitats)))]

    while len(selected) < count and habitat_round:
        made_progress = False
        for habitat in habitat_round:
            if len(selected) >= count:
                break
            payload = habitats.get(habitat)
            if not isinstance(payload, dict):
                continue
            examples = [
                str(item).strip()
                for item in payload.get("examples", ())
                if str(item).strip()
            ]
            if not examples:
                continue
            scored_examples: list[tuple[float, int, str]] = []
            for index, example in enumerate(examples):
                if f"{habitat}:{index}" in used_examples:
                    continue
                score = (
                    habitat_scores.get(habitat, 0)
                    + _textual_overlap_score(description_words, example) * 1.5
                    + (len(examples) - index) * 0.01
                )
                scored_examples.append((score, index, example))
            if not scored_examples:
                continue
            score, index, example = max(scored_examples, key=lambda item: (item[0], -item[1]))
            used_examples.add(f"{habitat}:{index}")
            reasons: list[str] = []
            if _habitat_keyword_score(habitat, description) > 0:
                reasons.append("keyword_habitat_match")
            if _words(example) & description_words:
                reasons.append("lexical_overlap")
            selected.append(
                WorldDescriptionAnchor(
                    id=f"world:{habitat}:{index:03d}",
                    habitat=habitat,
                    label=str(payload.get("label", habitat)),
                    vibe=str(payload.get("vibe", "")),
                    prompt_template=str(payload.get("prompt_template", "")),
                    source_text=example,
                    score=score,
                    reasons=tuple(reasons or ("stable_fallback",)),
                )
            )
            made_progress = True
        if not made_progress:
            break

    return tuple(selected)


def format_world_description_anchors_for_prompt(
    anchors: tuple[WorldDescriptionAnchor, ...],
) -> str:
    if not anchors:
        return "нет"

    blocks: list[str] = []
    for anchor in anchors:
        blocks.append(
            "\n".join(
                (
                    f"- id: {anchor.id}",
                    f"  habitat_family: {anchor.habitat}",
                    f"  label: {anchor.label}",
                    f"  vibe: {anchor.vibe}",
                    f"  template_do_not_copy: {anchor.prompt_template}",
                    f"  source_text_do_not_copy: {anchor.source_text}",
                )
            )
        )
    return "\n".join(blocks)


def all_world_anchor_texts() -> tuple[str, ...]:
    dataset = load_world_description_dataset()
    habitats = dataset.get("habitats") if isinstance(dataset, dict) else None
    if not isinstance(habitats, dict):
        return ()
    values: list[str] = []
    for payload in habitats.values():
        if not isinstance(payload, dict):
            continue
        values.extend(
            str(item).strip()
            for item in payload.get("examples", ())
            if str(item).strip()
        )
    return tuple(values)
