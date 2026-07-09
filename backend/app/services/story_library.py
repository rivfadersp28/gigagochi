from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

STORY_LIBRARY_PATH = Path(__file__).resolve().parents[2] / "data" / "story_library.json"
MAX_SEARCH_LIMIT = 5
MAX_OVERLAY_BRICKS = 80
MAX_TEXT_CHARS = 700

WORD_PATTERN = re.compile(r"[0-9A-Za-zА-Яа-яЁё-]+")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _text_value(value: Any, *, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    text = _compact_spaces(value)
    return text[:limit].rstrip()


def _string_list(value: Any, *, limit: int = 12) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text_value(item, limit=160)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _hash_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


def _tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in WORD_PATTERN.findall(value.casefold()):
        if len(token) <= 2:
            continue
        tokens.add(token)
        if len(token) >= 5:
            tokens.add(token[:5])
        if len(token) >= 7:
            tokens.add(token[:7])
    return tokens


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _text_value(value, limit=240)
        return [text] if text else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_text(item))
        return result
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            if key in {"source", "sourceNotes", "provenance"}:
                continue
            result.extend(_flatten_text(item))
        return result
    return []


def _brick_text(record: dict[str, Any], fallback: str) -> str:
    priority_keys = (
        "name",
        "label",
        "type",
        "danger",
        "detail",
        "desc",
        "encounter",
        "habitat",
        "flora",
        "fauna",
        "trait",
        "text",
    )
    parts: list[str] = []
    for key in priority_keys:
        value = record.get(key)
        if isinstance(value, str):
            text = _text_value(value, limit=260)
            if text:
                parts.append(text)
    if not parts:
        parts = _flatten_text(record)
    return _compact_spaces("; ".join(parts)) or fallback


def _normalize_global_brick(
    *,
    pool: str,
    pool_label: str,
    index: int,
    value: Any,
) -> dict[str, Any] | None:
    if isinstance(value, str):
        text = _text_value(value)
        if not text:
            return None
        name = text
        attributes: dict[str, Any] = {}
    elif isinstance(value, dict):
        name = _text_value(value.get("name") or value.get("label") or value.get("title"), limit=120)
        text = _brick_text(value, name)
        attributes = {
            key: item
            for key, item in value.items()
            if key not in {"name", "label", "title"} and item not in ("", None, [], {})
        }
        if not name:
            name = text[:80].rstrip()
    else:
        return None

    if not name or not text:
        return None

    return {
        "id": f"global:{pool}:{index:03d}",
        "source": "global",
        "pool": pool,
        "poolLabel": pool_label,
        "name": name,
        "text": text,
        "attributes": attributes,
    }


def _normalize_overlay_brick(value: Any, index: int) -> dict[str, Any] | None:
    if not _is_record(value):
        return None
    pool = _text_value(value.get("pool"), limit=60) or "personal"
    name = _text_value(value.get("name") or value.get("title"), limit=120)
    text = _text_value(
        value.get("text") or value.get("description") or value.get("detail"),
        limit=MAX_TEXT_CHARS,
    )
    if not name or not text:
        return None
    brick_id = _text_value(value.get("id"), limit=120)
    if not brick_id:
        brick_id = f"pet:{pool}:{_hash_id(name, text)}"
    attributes = value.get("attributes") if _is_record(value.get("attributes")) else {}
    return {
        "id": brick_id,
        "source": "pet_overlay",
        "pool": pool,
        "poolLabel": _text_value(value.get("poolLabel"), limit=120) or pool,
        "name": name,
        "text": text,
        "attributes": attributes,
        "basedOnBrickIds": _string_list(value.get("basedOnBrickIds"), limit=8),
        "createdAt": _text_value(value.get("createdAt"), limit=80) or _now_iso(),
    }


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    return json.loads(STORY_LIBRARY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def global_story_bricks() -> tuple[dict[str, Any], ...]:
    catalog = _catalog()
    pools = catalog.get("pools") if _is_record(catalog.get("pools")) else {}
    bricks: list[dict[str, Any]] = []
    for pool, pool_payload in pools.items():
        if not isinstance(pool, str) or not _is_record(pool_payload):
            continue
        pool_label = _text_value(pool_payload.get("label"), limit=120) or pool
        data = pool_payload.get("data")
        if not isinstance(data, list):
            continue
        for index, value in enumerate(data):
            brick = _normalize_global_brick(
                pool=pool,
                pool_label=pool_label,
                index=index,
                value=value,
            )
            if brick:
                bricks.append(brick)
    return tuple(bricks)


def story_library_overlay_from_bible(character_bible: Any) -> list[dict[str, Any]]:
    bible = character_bible if _is_record(character_bible) else {}
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    overlay = extensions.get("story_library_overlay") if _is_record(extensions) else {}
    values = overlay.get("bricks") if _is_record(overlay) else []
    if not isinstance(values, list):
        return []

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(values[-MAX_OVERLAY_BRICKS:]):
        brick = _normalize_overlay_brick(value, index)
        if not brick or brick["id"] in seen:
            continue
        seen.add(brick["id"])
        result.append(brick)
    return result


def _combined_bricks(
    character_bible: Any,
    story_library_patch: dict[str, Any] | None = None,
    *,
    include_global: bool = True,
    include_overlay: bool = False,
    include_patch: bool = True,
) -> list[dict[str, Any]]:
    overlay = story_library_overlay_from_bible(character_bible) if include_overlay else []
    patch_bricks = []
    if include_patch and _is_record(story_library_patch):
        raw_bricks = story_library_patch.get("bricks")
        if isinstance(raw_bricks, list):
            patch_bricks = [
                brick
                for index, value in enumerate(raw_bricks)
                if (brick := _normalize_overlay_brick(value, index))
            ]
    global_bricks = list(global_story_bricks()) if include_global else []
    return [*patch_bricks, *overlay, *global_bricks]


def _hint_tokens(pool_hints: Any) -> set[str]:
    if isinstance(pool_hints, str):
        pool_hints = [pool_hints]
    if not isinstance(pool_hints, list):
        return set()
    return _tokens(" ".join(_text_value(item, limit=80) for item in pool_hints))


def _brick_score(brick: dict[str, Any], query_tokens: set[str], hint_tokens: set[str]) -> int:
    pool_text = f"{brick.get('pool', '')} {brick.get('poolLabel', '')}"
    pool_tokens = _tokens(pool_text)
    haystack = _tokens(
        " ".join(
            [
                pool_text,
                str(brick.get("name", "")),
                str(brick.get("text", "")),
                json.dumps(brick.get("attributes", {}), ensure_ascii=False),
            ]
        )
    )
    score = 0
    score += len(query_tokens & haystack) * 3
    score += len(query_tokens & pool_tokens) * 5
    score += len(hint_tokens & pool_tokens) * 8
    if brick.get("source") == "pet_overlay":
        score += 2
    return score


def _sort_scored_bricks(
    scored: list[tuple[int, dict[str, Any]]],
) -> list[tuple[int, dict[str, Any]]]:
    return sorted(
        scored,
        key=lambda item: (-item[0], item[1].get("source") != "pet_overlay", item[1]["id"]),
    )


def _diverse_pool_slice(
    scored: list[tuple[int, dict[str, Any]]],
    limit: int,
) -> list[tuple[int, dict[str, Any]]]:
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for score, brick in _sort_scored_bricks(scored):
        grouped.setdefault(str(brick.get("pool") or ""), []).append((score, brick))

    result: list[tuple[int, dict[str, Any]]] = []
    while len(result) < limit:
        added = False
        for pool in sorted(grouped):
            group = grouped[pool]
            if not group:
                continue
            result.append(group.pop(0))
            added = True
            if len(result) >= limit:
                break
        if not added:
            break
    return result


def search_story_library(
    *,
    query: str,
    pool_hints: Any = None,
    limit: int = 3,
    character_bible: Any = None,
    story_library_patch: dict[str, Any] | None = None,
    diverse_pools: bool = False,
    include_global: bool = True,
    include_overlay: bool = False,
    include_patch: bool = True,
) -> dict[str, Any]:
    query_text = _text_value(query, limit=300)
    query_tokens = _tokens(query_text)
    hints = _hint_tokens(pool_hints)
    effective_limit = min(MAX_SEARCH_LIMIT, max(1, int(limit or 3)))

    scored: list[tuple[int, dict[str, Any]]] = []
    for brick in _combined_bricks(
        character_bible,
        story_library_patch,
        include_global=include_global,
        include_overlay=include_overlay,
        include_patch=include_patch,
    ):
        score = _brick_score(brick, query_tokens, hints)
        if score > 0:
            scored.append((score, brick))

    selected = (
        _diverse_pool_slice(scored, effective_limit)
        if diverse_pools
        else _sort_scored_bricks(scored)[:effective_limit]
    )
    bricks = [
        {
            "id": brick["id"],
            "source": brick["source"],
            "pool": brick["pool"],
            "poolLabel": brick.get("poolLabel"),
            "name": brick["name"],
            "text": brick["text"],
            "attributes": brick.get("attributes", {}),
            "score": score,
        }
        for score, brick in selected
    ]
    return {
        "query": query_text,
        "poolHints": pool_hints if isinstance(pool_hints, list) else [],
        "bricks": bricks,
    }
