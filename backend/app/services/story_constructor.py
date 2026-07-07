from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

STORY_CONSTRUCTOR_PATH = Path(__file__).resolve().parents[2] / "data" / "story_constructor.json"


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _sample(values: list[Any], limit: int) -> list[Any]:
    if len(values) <= limit:
        return list(values)
    return random.sample(values, limit)


def _compact_record(value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not _is_record(value):
        return None
    result = {key: value[key] for key in keys if value.get(key)}
    return result or None


def _compact_records(values: list[Any], keys: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in _sample(values, limit):
        record = _compact_record(value, keys)
        if record:
            result.append(record)
    return result


@lru_cache(maxsize=1)
def story_constructor_catalog() -> dict[str, Any]:
    return json.loads(STORY_CONSTRUCTOR_PATH.read_text(encoding="utf-8"))


def build_story_constructor_context(*, limit_per_pool: int = 5) -> dict[str, Any]:
    catalog = story_constructor_catalog()
    pools = catalog.get("pools") if _is_record(catalog.get("pools")) else {}
    main_screen = catalog.get("mainScreen") if _is_record(catalog.get("mainScreen")) else {}
    travel = catalog.get("travel") if _is_record(catalog.get("travel")) else {}

    items = [item for item in _as_list(pools.get("items")) if isinstance(item, str)]

    return {
        "items": _sample(items, 8),
        "creatures": _compact_records(
            _as_list(pools.get("creatures")),
            ("name", "detail"),
            limit_per_pool,
        ),
        "locations": _compact_records(
            _as_list(pools.get("locations")),
            ("name", "habitat", "detail"),
            limit_per_pool,
        ),
        "neighbors": _compact_records(
            _as_list(pools.get("neighbors")),
            ("name", "trait"),
            limit_per_pool,
        ),
        "softThreats": _compact_records(
            _as_list(pools.get("threats")),
            ("name", "danger", "detail"),
            limit_per_pool,
        ),
        "eventArchetypes": _compact_records(
            _as_list(catalog.get("eventArchetypes")),
            ("id", "text"),
            4,
        ),
        "dialogueHookExamples": _compact_records(
            _as_list(main_screen.get("eventTemplates")),
            ("id", "text"),
            3,
        ),
        "guidance": _sample(
            [item for item in _as_list(travel.get("promptGuidance")) if isinstance(item, str)],
            4,
        ),
    }
