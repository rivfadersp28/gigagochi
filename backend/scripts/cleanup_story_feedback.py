from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.telegram_push_store import JsonTelegramPushStore  # noqa: E402


def _remove_matching_facts(value: Any, pattern: re.Pattern[str]) -> int:
    removed = 0
    if isinstance(value, dict):
        facts = value.get("facts")
        if isinstance(facts, list):
            kept: list[Any] = []
            for fact in facts:
                if isinstance(fact, dict):
                    searchable = " ".join(
                        str(fact.get(field) or "") for field in ("text", "pathHint")
                    )
                    if pattern.search(searchable):
                        removed += 1
                        continue
                kept.append(fact)
            value["facts"] = kept
        for child in value.values():
            removed += _remove_matching_facts(child, pattern)
    elif isinstance(value, list):
        for child in value:
            removed += _remove_matching_facts(child, pattern)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove story-feedback facts from one Telegram push record."
    )
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--telegram-id", required=True, type=int)
    parser.add_argument("--regex", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    pattern = re.compile(args.regex, flags=re.IGNORECASE)
    store = JsonTelegramPushStore(args.store, version=1)
    record = store.read().get("records", {}).get(str(args.telegram_id))
    if not isinstance(record, dict):
        raise SystemExit(f"Telegram record {args.telegram_id} not found")

    removed = _remove_matching_facts(record, pattern)
    if args.apply and removed:
        store.replace_record(record)
    print(
        json.dumps(
            {
                "telegramId": args.telegram_id,
                "removedFacts": removed,
                "applied": bool(args.apply and removed),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
