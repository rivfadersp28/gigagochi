from __future__ import annotations

import base64
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
    / "5d37cc57-5dbc-514b-836c-74ff06d73587"
    / ".generation.json"
)
OUTPUT_ROOT = EXPERIMENT_ROOT / "output"
EXPERIMENT_JOB_ID = "kandinsky-app-prompt-exact-toilet-v1"

sys.path.insert(0, str(BACKEND_ROOT))

from app.media.kandinsky_prompt_adapter import adapt_kandinsky_prompt  # noqa: E402
from app.prompts.pet_image_prompts import build_pet_single_sprite_prompt  # noqa: E402
from app.services.image_service import (  # noqa: E402
    PET_SCENE_BACKGROUND_PATH,
    PET_SCENE_COMPOSITION_PROMPT,
    _atomic_write_nonempty,
    _is_valid_image_file,
    generate_image_bytes,
    make_character_foreground_image_bytes,
    normalize_pet_scene_video_frame_bytes,
)
from app.services.provider_task_checkpoint import generation_provider_task_scope  # noqa: E402


def _image_reference(path: Path) -> dict[str, object]:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }


def main() -> None:
    metadata = json.loads(SOURCE_METADATA.read_text(encoding="utf-8"))
    description = str(metadata["description"])
    character_bible = metadata["characterBible"]
    if description != "унитаз":
        raise RuntimeError("Expected the toilet character metadata")

    raw_character_prompt = build_pet_single_sprite_prompt(
        description,
        character_bible,
        stage="teen",
        state="idle",
    )
    adapted_character_prompt = adapt_kandinsky_prompt(
        raw_character_prompt,
        task="pet_creation/image",
    )
    adapted_scene_prompt = adapt_kandinsky_prompt(
        PET_SCENE_COMPOSITION_PROMPT,
        task="pet_creation/scene",
    )

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    character_path = OUTPUT_ROOT / "teen-idle-character.png"
    foreground_path = OUTPUT_ROOT / "teen-idle-foreground.png"
    normal_path = OUTPUT_ROOT / "teen-idle.png"
    _atomic_write_nonempty(
        OUTPUT_ROOT / "request.json",
        json.dumps(
            {
                "experiment": "kandinsky-app-prompt-exact-v1",
                "description": description,
                "sourceMetadata": str(SOURCE_METADATA),
                "rawApplicationCharacterPrompt": raw_character_prompt,
                "adaptedKandinskyCharacterPrompt": adapted_character_prompt,
                "rawApplicationScenePrompt": PET_SCENE_COMPOSITION_PROMPT,
                "adaptedKandinskyScenePrompt": adapted_scene_prompt,
                "applicationIntegration": False,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
    )

    if not _is_valid_image_file(character_path):
        with generation_provider_task_scope(
            job_id=EXPERIMENT_JOB_ID,
            stage="character",
        ):
            character_bytes = generate_image_bytes(
                raw_character_prompt,
                label="pet_creation/image",
                provider="kandinsky",
            )
        _atomic_write_nonempty(character_path, character_bytes)

    if not _is_valid_image_file(foreground_path):
        _atomic_write_nonempty(
            foreground_path,
            make_character_foreground_image_bytes(character_path.read_bytes()),
        )

    if not _is_valid_image_file(normal_path):
        with generation_provider_task_scope(
            job_id=EXPERIMENT_JOB_ID,
            stage="scene",
        ):
            scene_bytes = generate_image_bytes(
                PET_SCENE_COMPOSITION_PROMPT,
                label="pet_creation/scene",
                input_references=[
                    _image_reference(character_path),
                    _image_reference(PET_SCENE_BACKGROUND_PATH),
                ],
                provider="kandinsky",
            )
        _atomic_write_nonempty(
            normal_path,
            normalize_pet_scene_video_frame_bytes(scene_bytes),
        )

    print(normal_path.resolve())


if __name__ == "__main__":
    main()
