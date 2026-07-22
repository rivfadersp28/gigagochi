from __future__ import annotations

import base64
import json
import sys
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
OUTPUT_ROOT = EXPERIMENT_ROOT / "output"
STORY_PLAN_PATH = (
    PROJECT_ROOT
    / "provider-comparison-materials-20260720"
    / "toilet"
    / "story-plan.json"
)
EXPERIMENT_JOB_ID = "kandinsky-app-prompt-exact-toilet-v1"

sys.path.insert(0, str(BACKEND_ROOT))

from app.media.kandinsky_prompt_adapter import adapt_kandinsky_prompt  # noqa: E402
from app.services.background_story_service import (  # noqa: E402
    BackgroundStoryResult,
    build_background_story_image_prompt,
    extract_background_story_image_scene,
    reserve_background_story_video_bytes,
)
from app.services.image_service import (  # noqa: E402
    _atomic_write_nonempty,
    _is_valid_image_file,
    _is_nonempty_file,
    generate_image_bytes,
    reserve_pet_scene_video_bytes,
)
from app.services.interactive_travel_media_service import (  # noqa: E402
    INTERACTIVE_TRAVEL_PROVIDER_SIZE,
    INTERACTIVE_TRAVEL_VIDEO_ASPECT_RATIO,
    INTERACTIVE_TRAVEL_VERTICAL_COMPOSITION,
    _normalize_interactive_travel_background_image,
    _normalize_interactive_travel_video_source,
)
from app.services.provider_task_checkpoint import generation_provider_task_scope  # noqa: E402


def _image_reference(path: Path) -> dict[str, object]:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }


def main() -> None:
    character_path = OUTPUT_ROOT / "teen-idle-character.png"
    normal_path = OUTPUT_ROOT / "teen-idle.png"
    normal_video_path = OUTPUT_ROOT / "teen-idle.mp4"
    story_image_path = OUTPUT_ROOT / "story-start.png"
    story_video_source_path = OUTPUT_ROOT / "story-start-video-source.png"
    story_video_path = OUTPUT_ROOT / "story-start.mp4"
    if not _is_valid_image_file(character_path) or not _is_valid_image_file(normal_path):
        raise RuntimeError("Run generate.py before generating videos")

    if not _is_nonempty_file(normal_video_path):
        with generation_provider_task_scope(
            job_id=EXPERIMENT_JOB_ID,
            stage="normal-video",
        ):
            with reserve_pet_scene_video_bytes(
                normal_path,
                provider="kandinsky",
            ) as video_bytes:
                _atomic_write_nonempty(normal_video_path, video_bytes)

    plan = json.loads(STORY_PLAN_PATH.read_text(encoding="utf-8"))
    story = BackgroundStoryResult(
        title=str(plan["title"]),
        summary=f"Путешествие в место «{plan['destination']}». Часть 1.",
        story_text=str(plan["storyText"]),
        event_type="interactive_travel_part",
        valence="mixed",
        tags=(str(plan["destination"]),),
        rag_text=str(plan["storyText"]),
        story_library_patch=None,
        lite_overlay_patch=None,
        recent_story_event=None,
        prompt_debug=[],
    )
    direction: dict[str, str] = {}
    scene = extract_background_story_image_scene(
        story,
        direction_output=direction,
    )
    story_prompt = build_background_story_image_prompt(
        scene=scene,
        mode="full_stop_motion",
        pose_family=direction.get("poseFamily", ""),
        hero_pose=direction.get("heroPose", ""),
        camera=direction.get("camera", ""),
        color_palette=direction.get("colorPalette", ""),
        accent_color=direction.get("accentColor", ""),
        palette_family=direction.get("paletteFamily", ""),
        composition_direction=INTERACTIVE_TRAVEL_VERTICAL_COMPOSITION,
    )

    if not _is_valid_image_file(story_image_path):
        with generation_provider_task_scope(
            job_id=EXPERIMENT_JOB_ID,
            stage="story-image",
        ):
            raw_story_image = generate_image_bytes(
                story_prompt,
                label="background_story/image",
                size=INTERACTIVE_TRAVEL_PROVIDER_SIZE,
                input_references=[_image_reference(character_path)],
                provider="kandinsky",
            )
        _atomic_write_nonempty(
            story_video_source_path,
            _normalize_interactive_travel_video_source(raw_story_image),
        )
        _atomic_write_nonempty(
            story_image_path,
            _normalize_interactive_travel_background_image(raw_story_image),
        )

    if not _is_nonempty_file(story_video_source_path):
        _atomic_write_nonempty(
            story_video_source_path,
            _normalize_interactive_travel_video_source(story_image_path.read_bytes()),
        )

    if not _is_nonempty_file(story_video_path):
        with generation_provider_task_scope(
            job_id=EXPERIMENT_JOB_ID,
            stage="story-video",
        ):
            with reserve_background_story_video_bytes(
                story_video_source_path.read_bytes(),
                aspect_ratio=INTERACTIVE_TRAVEL_VIDEO_ASPECT_RATIO,
            ) as video_bytes:
                _atomic_write_nonempty(story_video_path, video_bytes)

    _atomic_write_nonempty(
        OUTPUT_ROOT / "story-request.json",
        json.dumps(
            {
                "storyPlan": plan,
                "imageDirection": direction,
                "rawApplicationStoryPrompt": story_prompt,
                "adaptedKandinskyStoryPrompt": adapt_kandinsky_prompt(
                    story_prompt,
                    task="background_story/image",
                ),
                "applicationIntegration": False,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
    )
    print(normal_video_path.resolve())
    print(story_video_path.resolve())


if __name__ == "__main__":
    main()
