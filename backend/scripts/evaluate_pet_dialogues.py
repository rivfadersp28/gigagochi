from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.admin_generation_lab_service import generate_admin_profile_only

DEFAULT_DESCRIPTIONS = (
    "челик с листом вместо лица",
    "маленький дракон с мягкими крыльями",
    "сонное облако с маленьким ключом",
)


def _read_descriptions(path: Path | None) -> list[str]:
    if path is None:
        return list(DEFAULT_DESCRIPTIONS)
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _benchmark_turns(result: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = result.get("benchmark") or {}
    turns = benchmark.get("turns")
    if isinstance(turns, list):
        return [turn for turn in turns if isinstance(turn, dict)]
    return [benchmark] if benchmark else []


def _summarize_result(description: str, result: dict[str, Any]) -> dict[str, Any]:
    profile = result.get("characterBible") or {}
    lore = profile.get("lore") if isinstance(profile, dict) else {}
    lore = lore if isinstance(lore, dict) else {}
    inner_life = lore.get("inner_life") if isinstance(lore.get("inner_life"), dict) else {}
    turns = _benchmark_turns(result)
    scores = [
        turn.get("qualityScore")
        for turn in turns
        if isinstance(turn.get("qualityScore"), int)
    ]
    failing_turns = [
        {
            "question": turn.get("question"),
            "reply": turn.get("reply"),
            "qualityScore": turn.get("qualityScore"),
            "qualityFlags": turn.get("qualityFlags"),
            "validationFlags": turn.get("validationFlags"),
        }
        for turn in turns
        if turn.get("qualityPassed") is False
    ]

    return {
        "description": description,
        "species": profile.get("species") if isinstance(profile, dict) else None,
        "likes": inner_life.get("likes") if isinstance(inner_life, dict) else None,
        "turnCount": len(turns),
        "averageQualityScore": round(mean(scores), 1) if scores else None,
        "passed": not failing_turns and bool(turns),
        "failingTurns": failing_turns,
        "turns": turns,
    }


def run_eval(descriptions: list[str], *, conversation: bool) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for description in descriptions:
        try:
            result = generate_admin_profile_only(
                description,
                include_debug_prompts=False,
                include_self_intro_benchmark=True,
                include_conversation_benchmark=conversation,
            )
            results.append(_summarize_result(description, result))
        except Exception as exc:
            results.append(
                {
                    "description": description,
                    "passed": False,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            )

    scores = [
        item.get("averageQualityScore")
        for item in results
        if isinstance(item.get("averageQualityScore"), int | float)
    ]
    failures = [item for item in results if not item.get("passed")]
    return {
        "summary": {
            "count": len(results),
            "passed": len(results) - len(failures),
            "failed": len(failures),
            "averageQualityScore": round(mean(scores), 1) if scores else None,
        },
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate generated pet dialogue quality.")
    parser.add_argument(
        "descriptions",
        nargs="*",
        help="Pet descriptions to evaluate. Defaults to a small built-in set.",
    )
    parser.add_argument(
        "--descriptions-file",
        type=Path,
        help="UTF-8 text file with one pet description per line.",
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--no-conversation",
        action="store_true",
        help="Run only the self-intro benchmark instead of the multi-turn benchmark.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    descriptions = args.descriptions or _read_descriptions(args.descriptions_file)
    descriptions = descriptions[: max(1, args.limit)]
    payload = run_eval(descriptions, conversation=not args.no_conversation)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
