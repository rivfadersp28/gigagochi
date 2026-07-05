from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.character_cards import upgrade_character_bible_v2  # noqa: E402


def _upgrade_value(value: Any, raw_description: str) -> Any:
    if isinstance(value, list):
        return [_upgrade_value(item, raw_description) for item in value]
    if isinstance(value, dict) and isinstance(value.get("characterBible"), dict):
        return {
            **value,
            "characterBible": upgrade_character_bible_v2(
                value["characterBible"],
                raw_description=raw_description,
            ),
        }
    if isinstance(value, dict):
        return upgrade_character_bible_v2(value, raw_description=raw_description)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upgrade character bibles to Character Profile V2."
    )
    parser.add_argument("input", nargs="?", help="Input JSON file. Reads stdin when omitted.")
    parser.add_argument("-o", "--output", help="Output JSON file. Writes stdout when omitted.")
    parser.add_argument(
        "--description",
        default="",
        help="Optional raw user description used to fill missing identity/species fields.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input file. Requires an input path and no --output.",
    )
    args = parser.parse_args()

    if args.in_place and (not args.input or args.output):
        parser.error("--in-place requires an input file and cannot be combined with --output")

    source = Path(args.input) if args.input else None
    raw_json = source.read_text(encoding="utf-8") if source else sys.stdin.read()
    payload = json.loads(raw_json)
    upgraded = _upgrade_value(payload, args.description)
    rendered = json.dumps(upgraded, ensure_ascii=False, indent=2) + "\n"

    if args.in_place and source:
        source.write_text(rendered, encoding="utf-8")
    elif args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
