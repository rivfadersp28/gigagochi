from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
SOURCE_METADATA = (
    BACKEND_ROOT
    / "static"
    / "generated"
    / "227fa172-765b-5f52-b32d-be53afee0236"
    / ".generation.json"
)
STORY_PLAN = (
    PROJECT_ROOT
    / "provider-comparison-materials-20260720"
    / "apple-person"
    / "story-plan.json"
)
OUTPUT_PATH = EXPERIMENT_ROOT / "prompts.json"

sys.path.insert(0, str(BACKEND_ROOT))

from app.media.kandinsky_prompt_adapter import adapt_kandinsky_prompt  # noqa: E402
from app.prompts.pet_image_prompts import build_pet_single_sprite_prompt  # noqa: E402
from app.services.image_service import PET_SCENE_COMPOSITION_PROMPT  # noqa: E402
from app.services.outfit_service import _outfit_edit_prompt  # noqa: E402


EXPECTED_HASHES = {
    "openaiCharacterPrompt": "08501285d5085eb0dab1a6762492737f73090804aa66fb5ef31752e6b1901952",
    "kandinskyCharacterPrompt": "a1f3520e493b5632a2cd009dc633fd9c4984d84042e9134b3b15b57d322108a5",
    "openaiScenePrompt": "d565716af8719f5d41b46835974aaef15cbe91e5b3abd5502774c08d23148e08",
    "kandinskyScenePrompt": "b641e0c991c684ac36821dc61eb6dd347e4dce1eb0817f53bdcd0c844ec2c417",
    "openaiOutfitPrompt": "4554c2c90b6fca0f243a448c5fdee526ad7be9c747fc7344f432a05e4ed0c7fc",
    "kandinskyOutfitPrompt": "4554c2c90b6fca0f243a448c5fdee526ad7be9c747fc7344f432a05e4ed0c7fc",
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> None:
    metadata = json.loads(SOURCE_METADATA.read_text(encoding="utf-8"))
    description = str(metadata["description"])
    if description != "человек яблоко":
        raise RuntimeError("Unexpected source character")

    openai_character = build_pet_single_sprite_prompt(
        description,
        metadata["characterBible"],
        stage="teen",
        state="idle",
    )
    openai_scene = PET_SCENE_COMPOSITION_PROMPT
    openai_outfit = _outfit_edit_prompt("Одень персонажа в футболку Iron Maiden.")
    prompts = {
        "openaiCharacterPrompt": openai_character,
        "kandinskyCharacterPrompt": adapt_kandinsky_prompt(
            openai_character,
            task="pet_creation/image",
        ),
        "openaiScenePrompt": openai_scene,
        "kandinskyScenePrompt": adapt_kandinsky_prompt(
            openai_scene,
            task="pet_creation/scene",
        ),
        "openaiOutfitPrompt": openai_outfit,
        "kandinskyOutfitPrompt": adapt_kandinsky_prompt(
            openai_outfit,
            task="pet_outfit/idle_image",
        ),
    }
    actual_hashes = {key: _hash(value) for key, value in prompts.items()}
    if actual_hashes != EXPECTED_HASHES:
        raise RuntimeError(
            "Reconstructed prompts do not match generation logs:\n"
            + json.dumps(actual_hashes, indent=2)
        )

    story_plan = json.loads(STORY_PLAN.read_text(encoding="utf-8"))
    payload = {
        "description": description,
        "sourceMetadata": str(SOURCE_METADATA),
        "verification": {
            "status": "exact_hash_match",
            "sha256": actual_hashes,
        },
        "normal": {
            "openaiCharacterPrompt": prompts["openaiCharacterPrompt"],
            "kandinskyCharacterPrompt": prompts["kandinskyCharacterPrompt"],
            "openaiScenePrompt": prompts["openaiScenePrompt"],
            "kandinskyScenePrompt": prompts["kandinskyScenePrompt"],
        },
        "outfit": {
            "request": "Одень персонажа в футболку Iron Maiden.",
            "openaiPrompt": prompts["openaiOutfitPrompt"],
            "kandinskyPrompt": prompts["kandinskyOutfitPrompt"],
        },
        "interactiveStoryStart": {
            "storyPlan": story_plan,
            "exactPromptTextAvailable": False,
            "reason": (
                "The art-director response was not stored verbatim; only the final "
                "provider prompt hashes remain in ai-prompts.jsonl."
            ),
            "actualProviderPromptEvidence": {
                "openai": {
                    "sha256": "04b8cddfc33069cd63e5a5b626253b4c98c6b8c3ac9b8f39f6e1577437374203",
                    "chars": 7407,
                },
                "kandinsky": {
                    "sha256": "ee0a7c278519bd36b6c2e694686c11a013a2acfd4663d2b413a0fd5646f2b7ae",
                    "chars": 1893,
                },
            },
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT_PATH.resolve())


if __name__ == "__main__":
    main()
