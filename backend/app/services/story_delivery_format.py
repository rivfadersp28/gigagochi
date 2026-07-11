from __future__ import annotations

from typing import Any

TELEGRAM_PHOTO_CAPTION_LIMIT = 1024

STAT_DEBUG_LABELS: tuple[tuple[str, str], ...] = (
    ("energy", "здоровье"),
    ("hunger", "голод"),
    ("happiness", "настроение"),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _format_amount(value: Any) -> str:
    amount = abs(_number(value) or 0.0)
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.1f}".rstrip("0").rstrip(".")


def _stats_delta(story: dict[str, Any]) -> dict[str, float]:
    delta = {key: 0.0 for key, _label in STAT_DEBUG_LABELS}
    raw_delta = story.get("statsDelta")
    if isinstance(raw_delta, dict):
        for key in delta:
            value = _number(raw_delta.get(key))
            if value is not None:
                delta[key] = value
        return delta

    stat_impacts = story.get("statImpacts")
    if isinstance(stat_impacts, list):
        for item in stat_impacts:
            if not isinstance(item, dict):
                continue
            stat = item.get("stat")
            if stat not in delta:
                continue
            delta[stat] += _number(item.get("amount")) or 0.0
        return delta

    stat_impact = story.get("statImpact")
    if not isinstance(stat_impact, dict) or stat_impact.get("applies") is False:
        return delta
    stat = stat_impact.get("stat")
    if stat not in delta:
        return delta
    amount = _number(stat_impact.get("amount")) or 0.0
    if stat_impact.get("isNegativeOutcome") is True:
        amount = -abs(amount)
    delta[stat] = amount
    return delta


def _story_stat_debug_block(story: dict[str, Any]) -> str:
    has_stat_context = (
        isinstance(story.get("statsDelta"), dict)
        or isinstance(story.get("statImpacts"), list)
        or isinstance(story.get("statImpact"), dict)
    )
    if not has_stat_context:
        return ""
    delta = _stats_delta(story)
    lines = ["Влияние на параметры:"]
    changed = []
    for key, label in STAT_DEBUG_LABELS:
        amount = delta[key]
        if amount == 0:
            continue
        direction = "плюс" if amount > 0 else "минус"
        changed.append(f"{label}: {direction} {_format_amount(amount)}")
    lines.extend(changed or ["без изменений"])
    return "\n".join(lines)


def format_story_message(story: dict[str, Any], *, limit: int = 3500) -> str:
    title = str(story.get("title") or "Фоновое событие").strip()
    story_text = str(story.get("storyText") or story.get("summary") or "").strip()
    if not story_text:
        story_text = "История сгенерировалась, но текст пустой."

    base_text = f"{title}\n\n{story_text}"
    debug_block = _story_stat_debug_block(story)
    if not debug_block:
        return base_text[:limit].rstrip()

    separator = "\n\n"
    reserved = len(separator) + len(debug_block)
    if reserved >= limit:
        return debug_block[:limit].rstrip()

    base_limit = limit - reserved
    return f"{base_text[:base_limit].rstrip()}{separator}{debug_block}".rstrip()


def format_story_caption(story: dict[str, Any]) -> str:
    return format_story_message(story, limit=TELEGRAM_PHOTO_CAPTION_LIMIT)


def format_full_story_message(story: dict[str, Any], *, limit: int = 4000) -> str:
    title = str(story.get("overallTitle") or "Большое путешествие").strip()
    raw_parts = story.get("parts") if isinstance(story.get("parts"), list) else []
    sections = [title]
    for index, part in enumerate(raw_parts[:4], start=1):
        if not isinstance(part, dict):
            continue
        part_title = str(part.get("title") or f"Часть {index}").strip()
        story_text = str(part.get("storyText") or part.get("summary") or "").strip()
        impact_block = _story_stat_debug_block(part)
        section = f"Часть {index}. {part_title}\n\n{story_text}"
        if impact_block:
            section = f"{section}\n\n{impact_block}"
        sections.append(section)
    return "\n\n————\n\n".join(sections)[:limit].rstrip()


def format_full_story_part_message(
    story: dict[str, Any],
    part: dict[str, Any],
    *,
    limit: int = TELEGRAM_PHOTO_CAPTION_LIMIT,
) -> str:
    overall_title = str(story.get("overallTitle") or "История одного дня").strip()
    part_number = int(part.get("partNumber") or 1)
    part_title = str(part.get("title") or f"Часть {part_number}").strip()
    story_text = str(part.get("storyText") or part.get("summary") or "").strip()
    base_text = f"{overall_title}\n\nЧасть {part_number}. {part_title}\n\n{story_text}"
    impact_block = _story_stat_debug_block(part)
    if not impact_block:
        return base_text[:limit].rstrip()
    separator = "\n\n"
    reserved = len(separator) + len(impact_block)
    if reserved >= limit:
        return impact_block[:limit].rstrip()
    return f"{base_text[: limit - reserved].rstrip()}{separator}{impact_block}".rstrip()
