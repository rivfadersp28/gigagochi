from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.schemas import InteractiveTravelDemoResponse

DEMO_PATH = Path(__file__).resolve().parents[2] / "data" / "interactive_travel_demo.json"
DEMO_MEDIA_PREFIX = "/static/demo/interactive-travel/"


@lru_cache(maxsize=1)
def read_interactive_travel_demo() -> InteractiveTravelDemoResponse:
    payload = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    demo = InteractiveTravelDemoResponse.model_validate(payload)
    if not demo.travel.completed:
        raise ValueError("interactive travel demo must contain a completed story")
    if any(
        not part.backgroundImageUrl
        or not part.backgroundImageUrl.startswith(DEMO_MEDIA_PREFIX)
        or not part.backgroundVideoUrl
        or not part.backgroundVideoUrl.startswith(DEMO_MEDIA_PREFIX)
        for part in demo.travel.parts
    ):
        raise ValueError("interactive travel demo must contain local image and video assets")
    return demo
