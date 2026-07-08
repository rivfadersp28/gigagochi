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

    text = compact_spaces(str(value.get("text") or ""))
    if not text:
        return None

    sphere = str(value.get("sphere") or "character").strip()
    if sphere not in LITE_FACT_SPHERES:
        sphere = "character"

    kind = str(value.get("kind") or "").strip()
    if kind not in LITE_FACT_KINDS:
        kind = default_kind_for_sphere(sphere)

    path_hint = compact_spaces(str(value.get("pathHint") or "")) or lite_fact_path_hint(sphere)
    source = compact_spaces(str(value.get("source") or "")) or default_source

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


def merge_lite_overlay_patch(target: dict[str, Any], patch: dict[str, Any] | None) -> None:
    if not patch:
        return

    existing_keys = {
        lite_fact_key(fact) for fact in target.get("facts", []) if isinstance(fact, dict)
    }
    facts = target.setdefault("facts", [])
    if not isinstance(facts, list):
        facts = []
        target["facts"] = facts
    for fact in patch.get("facts", []):
        if not isinstance(fact, dict):
            continue
        key = lite_fact_key(fact)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        facts.append(fact)

    spheres = target.setdefault("spheres", {})
    if not isinstance(spheres, dict):
        spheres = {}
        target["spheres"] = spheres
    patch_spheres = patch.get("spheres")
    if isinstance(patch_spheres, dict):
        for sphere, patch_sphere in patch_spheres.items():
            if not isinstance(patch_sphere, dict):
                continue
            target_sphere = spheres.setdefault(sphere, {})
            if not isinstance(target_sphere, dict):
                target_sphere = {}
                spheres[sphere] = target_sphere
            merge_lite_overlay_patch(target_sphere, patch_sphere)

    if isinstance(patch.get("worldSeed"), dict):
        target["worldSeed"] = patch["worldSeed"]
