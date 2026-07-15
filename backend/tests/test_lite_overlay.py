from app.services.lite_overlay import (
    LITE_FACT_SPHERES,
    MAX_LITE_OVERLAY_FACTS,
    MAX_LITE_SPHERE_FACTS,
    merge_lite_overlay_patch,
    normalize_lite_overlay_patch,
)


def _fact(index: int, sphere: str = "world") -> dict[str, str]:
    return {"sphere": sphere, "text": f"fact-{index}"}


def test_merge_lite_overlay_patch_keeps_the_newest_bounded_facts() -> None:
    target = {
        "facts": [_fact(index) for index in range(90)],
        "spheres": {
            "world": {"facts": [_fact(index) for index in range(50)]},
        },
    }
    patch = {
        "facts": [_fact(index) for index in range(90, 100)],
        "spheres": {
            "world": {"facts": [_fact(index) for index in range(50, 60)]},
        },
    }

    merge_lite_overlay_patch(target, patch)

    assert len(target["facts"]) == MAX_LITE_OVERLAY_FACTS
    assert target["facts"][0]["text"] == "fact-20"
    assert target["facts"][-1]["text"] == "fact-99"
    world_facts = target["spheres"]["world"]["facts"]
    assert len(world_facts) == MAX_LITE_SPHERE_FACTS
    assert world_facts[0]["text"] == "fact-20"
    assert world_facts[-1]["text"] == "fact-59"


def test_normalize_lite_overlay_patch_bounds_fields_and_drops_unknown_shape() -> None:
    normalized = normalize_lite_overlay_patch(
        {
            "facts": [
                {
                    "sphere": "invalid",
                    "kind": "invalid",
                    "text": "x" * 700,
                    "pathHint": "p" * 200,
                    "source": "s" * 200,
                    "createdAt": "t" * 200,
                    "unknown": "drop",
                }
            ],
            "spheres": {
                "invalid": {"facts": [{"text": "drop"}]},
                "world": {"facts": [_fact(index) for index in range(50)]},
            },
            "worldSeed": {"source": "s" * 100, "createdAt": "now", "blob": "drop"},
            "unknown": {"blob": "drop"},
        }
    )

    assert set(normalized) == {"facts", "spheres", "worldSeed"}
    assert set(normalized["facts"][0]) == {
        "sphere",
        "kind",
        "text",
        "pathHint",
        "source",
        "createdAt",
    }
    assert normalized["facts"][0]["sphere"] in LITE_FACT_SPHERES
    assert len(normalized["facts"][0]["text"]) == 500
    assert len(normalized["facts"][0]["pathHint"]) == 120
    assert len(normalized["facts"][0]["source"]) == 80
    assert len(normalized["facts"][0]["createdAt"]) == 80
    assert set(normalized["spheres"]) == {"world"}
    assert len(normalized["spheres"]["world"]["facts"]) == MAX_LITE_SPHERE_FACTS
    assert normalized["worldSeed"] == {"source": "s" * 80, "createdAt": "now"}
