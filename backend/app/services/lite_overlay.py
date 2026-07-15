from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

LITE_FACT_SPHERES = ("character", "appearance", "world", "relationship")
LITE_FACT_KINDS = (
    "character_fact",
    "appearance_fact",
    "world_fact",
    "relationship_fact",
)
MAX_LITE_OVERLAY_FACTS = 80
MAX_LITE_SPHERE_FACTS = 40
MAX_LITE_FACT_TEXT_CHARS = 500
MAX_LITE_FACT_PATH_HINT_CHARS = 120
MAX_LITE_FACT_SOURCE_CHARS = 80
MAX_LITE_FACT_TIMESTAMP_CHARS = 80


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def lite_fact_path_hint(sphere: str) -> str:
    return f"lite_overlay.spheres.{sphere}"


def default_kind_for_sphere(sphere: str) -> str:
    if sphere == "appearance":
        return "appearance_fact"
    if sphere == "world":
        return "world_fact"
    if sphere == "relationship":
        return "relationship_fact"
    return "character_fact"


def normalize_extracted_fact(
    value: Any,
    *,
    default_source: str = "lite_post_reply_extractor",
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    text = compact_spaces(str(value.get("text") or ""))[:MAX_LITE_FACT_TEXT_CHARS].rstrip()
    if not text:
        return None

    sphere = str(value.get("sphere") or "character").strip()
    if sphere not in LITE_FACT_SPHERES:
        sphere = "character"

    kind = str(value.get("kind") or "").strip()
    if kind not in LITE_FACT_KINDS:
        kind = default_kind_for_sphere(sphere)

    path_hint = compact_spaces(str(value.get("pathHint") or ""))[
        :MAX_LITE_FACT_PATH_HINT_CHARS
    ].rstrip() or lite_fact_path_hint(sphere)
    source = (
        compact_spaces(str(value.get("source") or ""))[:MAX_LITE_FACT_SOURCE_CHARS].rstrip()
        or default_source[:MAX_LITE_FACT_SOURCE_CHARS]
    )

    return {
        "sphere": sphere,
        "kind": kind,
        "text": text,
        "pathHint": path_hint,
        "source": source,
        "createdAt": now_iso(),
    }


def lite_fact_key(fact: dict[str, Any]) -> str:
    return f"{fact.get('sphere', 'character')}:{fact.get('text', '')}".casefold()


def _bounded_optional_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = compact_spaces(value)[:limit].rstrip()
    return text or None


def _normalize_persisted_fact(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    text = _bounded_optional_text(value.get("text"), limit=MAX_LITE_FACT_TEXT_CHARS)
    if not text:
        return None

    result: dict[str, Any] = {"text": text}
    if "sphere" in value:
        sphere = str(value.get("sphere") or "").strip()
        result["sphere"] = sphere if sphere in LITE_FACT_SPHERES else "character"
    if "kind" in value:
        sphere = str(result.get("sphere") or "character")
        kind = str(value.get("kind") or "").strip()
        result["kind"] = kind if kind in LITE_FACT_KINDS else default_kind_for_sphere(sphere)
    for key, limit in (
        ("pathHint", MAX_LITE_FACT_PATH_HINT_CHARS),
        ("source", MAX_LITE_FACT_SOURCE_CHARS),
        ("createdAt", MAX_LITE_FACT_TIMESTAMP_CHARS),
    ):
        bounded = _bounded_optional_text(value.get(key), limit=limit)
        if bounded:
            result[key] = bounded
    return result


def _normalize_fact_list(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_fact in reversed(value):
        fact = _normalize_persisted_fact(raw_fact)
        if fact is None:
            continue
        key = lite_fact_key(fact)
        if key in seen:
            continue
        seen.add(key)
        result.append(fact)
        if len(result) >= limit:
            break
    result.reverse()
    return result


def normalize_lite_overlay_patch(value: Any) -> dict[str, Any]:
    """Return the bounded, persistable subset of a client/server overlay."""

    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    if "facts" in value:
        result["facts"] = _normalize_fact_list(
            value.get("facts"),
            limit=MAX_LITE_OVERLAY_FACTS,
        )

    raw_spheres = value.get("spheres")
    if isinstance(raw_spheres, dict):
        spheres: dict[str, dict[str, Any]] = {}
        for sphere in LITE_FACT_SPHERES:
            raw_sphere = raw_spheres.get(sphere)
            if not isinstance(raw_sphere, dict):
                continue
            facts = _normalize_fact_list(
                raw_sphere.get("facts"),
                limit=MAX_LITE_SPHERE_FACTS,
            )
            if facts or "facts" in raw_sphere:
                spheres[sphere] = {"facts": facts}
        if spheres:
            result["spheres"] = spheres

    raw_world_seed = value.get("worldSeed")
    if isinstance(raw_world_seed, dict):
        world_seed: dict[str, str] = {}
        for key in ("source", "createdAt"):
            bounded = _bounded_optional_text(
                raw_world_seed.get(key),
                limit=(
                    MAX_LITE_FACT_SOURCE_CHARS if key == "source" else MAX_LITE_FACT_TIMESTAMP_CHARS
                ),
            )
            if bounded:
                world_seed[key] = bounded
        if world_seed:
            result["worldSeed"] = world_seed
    return result


def overlay_patch_from_extracted_facts(
    raw_facts: Any,
    *,
    default_source: str = "lite_post_reply_extractor",
) -> dict[str, Any] | None:
    if not isinstance(raw_facts, list):
        return None

    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_fact in raw_facts:
        fact = normalize_extracted_fact(raw_fact, default_source=default_source)
        if not fact:
            continue
        key = lite_fact_key(fact)
        if key in seen:
            continue
        seen.add(key)
        facts.append(fact)

    if not facts:
        return None

    spheres: dict[str, dict[str, Any]] = {}
    for sphere in LITE_FACT_SPHERES:
        sphere_facts = [fact for fact in facts if fact["sphere"] == sphere]
        if sphere_facts:
            spheres[sphere] = {"facts": sphere_facts}

    return {
        "facts": facts,
        "spheres": spheres,
    }


def merge_lite_overlay_patch(
    target: dict[str, Any],
    patch: dict[str, Any] | None,
    *,
    _fact_limit: int = MAX_LITE_OVERLAY_FACTS,
) -> None:
    normalized_target = normalize_lite_overlay_patch(target)
    target.clear()
    target.update(normalized_target)
    normalized_patch = normalize_lite_overlay_patch(patch)
    if not normalized_patch:
        return

    facts = target.setdefault("facts", [])
    existing_keys = {lite_fact_key(fact) for fact in facts}
    for fact in normalized_patch.get("facts", []):
        key = lite_fact_key(fact)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        facts.append(fact)
    if len(facts) > _fact_limit:
        del facts[:-_fact_limit]

    spheres = target.setdefault("spheres", {})
    if not isinstance(spheres, dict):
        spheres = {}
        target["spheres"] = spheres
    patch_spheres = normalized_patch.get("spheres")
    if isinstance(patch_spheres, dict):
        for sphere, patch_sphere in patch_spheres.items():
            target_sphere = spheres.setdefault(sphere, {})
            sphere_facts = target_sphere.setdefault("facts", [])
            sphere_keys = {lite_fact_key(fact) for fact in sphere_facts}
            for fact in patch_sphere.get("facts", []):
                key = lite_fact_key(fact)
                if key in sphere_keys:
                    continue
                sphere_keys.add(key)
                sphere_facts.append(fact)
            if len(sphere_facts) > MAX_LITE_SPHERE_FACTS:
                del sphere_facts[:-MAX_LITE_SPHERE_FACTS]

    if isinstance(normalized_patch.get("worldSeed"), dict):
        target["worldSeed"] = normalized_patch["worldSeed"]
