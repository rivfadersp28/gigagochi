from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from app.schemas import LocalPetChatContext
from app.services.full_story_service import FullStoryGenerationError, generate_full_story

CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "battle_ruined_city",
        "direction": {
            "plotMode": "pursuit_or_conflict",
            "incidentClass": "conflict_or_dispute",
            "causalOrigin": "incompatible_goals",
            "eventScale": "shared_situation",
            "settingClass": "ancient_site",
            "oppositionClass": "creature",
            "resolutionMode": "contest",
            "resolutionFamily": "direct_confrontation",
            "valenceTarget": "mixed",
            "locationClass": "ruined_city",
            "arcVariant": "battle",
            "antagonistClass": "ancient_construct",
        },
    },
    {
        "id": "theft_underground_city",
        "direction": {
            "plotMode": "pursuit_or_conflict",
            "incidentClass": "resource_loss_or_damage",
            "causalOrigin": "theft",
            "eventScale": "shared_situation",
            "settingClass": "underground",
            "oppositionClass": "person_or_group",
            "resolutionMode": "contest",
            "resolutionFamily": "direct_confrontation",
            "valenceTarget": "mixed",
            "locationClass": "underground_city",
            "arcVariant": "theft",
            "antagonistClass": "intelligent_enemy",
        },
    },
    {
        "id": "magic_tower_discovery",
        "direction": {
            "plotMode": "discovery",
            "incidentClass": "unexpected_opportunity",
            "causalOrigin": "temporary_change",
            "eventScale": "shared_situation",
            "settingClass": "ancient_site",
            "oppositionClass": "environment",
            "resolutionMode": "discovery",
            "resolutionFamily": "evidence_based_investigation",
            "valenceTarget": "positive",
            "locationClass": "magic_tower",
            "wonderClass": "impossible_architecture",
        },
    },
    {
        "id": "enchanted_grove_peaceful",
        "direction": {
            "plotMode": "peaceful_change",
            "incidentClass": "unexpected_opportunity",
            "causalOrigin": "temporary_change",
            "eventScale": "shared_situation",
            "settingClass": "remote_landscape",
            "oppositionClass": "none",
            "resolutionMode": "celebration_or_rest",
            "resolutionFamily": "social_resolution",
            "valenceTarget": "positive",
            "locationClass": "enchanted_grove",
        },
    },
)


def _pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "name": "Мяу",
            "description": "кошка-волшебница",
            "stage": "teen",
            "mood": "idle",
            "stats": {"hunger": 60, "happiness": 55, "energy": 70},
            "characterBible": {
                "identity": {"name": "Мяу", "species": "кошка-волшебница"},
                "genesis": {"character_trait": "смелая", "story_engine": "исследования"},
                "voice": {"voice_rules": ["говорит прямо"], "sentence_rhythm": "короткий"},
            },
        }
    )


def _usage(prompt_debug: list[dict[str, Any]]) -> dict[str, float]:
    totals = {"prompt_tokens": 0.0, "completion_tokens": 0.0, "total_tokens": 0.0, "cost": 0.0}
    for event in prompt_debug:
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                totals[key] += float(value)
    return {key: value for key, value in totals.items() if value}


def _run_case(model: str, review_model: str | None, case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = generate_full_story(
            pet=_pet(),
            model=model,
            review_model=review_model,
            story_direction=case["direction"],
        )
    except FullStoryGenerationError as exc:
        return {
            "model": model,
            "reviewModel": review_model or model,
            "case": case["id"],
            "accepted": False,
            "error": str(exc),
            "latencySeconds": round(time.perf_counter() - started, 3),
        }
    return {
        "model": model,
        "reviewModel": review_model or model,
        "case": case["id"],
        "accepted": True,
        "latencySeconds": round(time.perf_counter() - started, 3),
        "usage": _usage(result.prompt_debug),
        "story": result.model_dump(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сравнивает модели на одинаковых направлениях полной истории."
    )
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--review-model")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = [_run_case(model, args.review_model, case) for model in args.models for case in CASES]
    output = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
