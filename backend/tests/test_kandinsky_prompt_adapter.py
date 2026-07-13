from app.media.kandinsky_prompt_adapter import adapt_kandinsky_prompt


def test_pet_scene_prompt_preserves_fine_reference_details() -> None:
    result = adapt_kandinsky_prompt(
        "Добавь персонажа с первой картинки на вторую в центр",
        task="pet_creation/scene",
    )

    assert "НЕ ПЕРЕРИСОВЫВАЙ И НЕ УПРОЩАЙ" in result
    assert "слои одежды" in result
    assert "заклёпки" in result
    assert "Не удаляй" in result
    assert "тихий мшистый лес" in result
    assert "малая глубина резкости" in result
    assert "матовую смолу" in result
    assert "55–60% высоты кадра" in result
    assert "35–40% полного роста" in result
    assert "не превращай питомца во взрослого" in result


def test_pet_creation_style_frame_fits_maximum_description() -> None:
    result = adapt_kandinsky_prompt("д" * 300, task="pet_creation/image")

    assert len(result) <= 2048
    assert "коллекционную дизайнерскую арт-игрушку" in result
    assert "ИДЕНТИЧНОСТЬ" in result
    assert "МАТЕРИАЛЫ" in result
    assert "КОСТЮМ" in result
    assert "АКЦЕНТ" in result
    assert "макрореализм фактур при полном росте" in result
    assert "2,5–3 головы" in result
    assert "реалистичных пропорций в 6–8 голов" in result
    assert result.endswith("водяного знака.")


def test_pet_restyle_prompt_changes_only_rendering_and_preserves_design() -> None:
    result = adapt_kandinsky_prompt("unused", task="pet_creation/restyle")

    assert len(result) <= 2048
    assert "ИЗМЕНИ ТОЛЬКО СПОСОБ ИЗОБРАЖЕНИЯ" in result
    assert "Ничего не добавляй, не удаляй" in result
    assert "плетение" in result
    assert "преломление" in result
    assert "чистый белый фон" in result


def test_background_story_prompt_is_rebuilt_in_russian() -> None:
    source = """
СЦЕНА:
Дракончик тянет рычаг у старого моста.

HERO POSE — REQUIRED:
- Body mechanics: наклоняется вперёд и держит рычаг обеими лапами
- Camera and framing: низкая камера в три четверти

COLOR SCRIPT — REQUIRED FOR THIS SCENE:
- Main dark-muted-pastel palette: тёмный шалфейный, пыльный коралловый
- Restrained accent: приглушённый янтарный
"""

    result = adapt_kandinsky_prompt(source, task="background_story/image")

    assert len(result) <= 2048
    assert "СОЗДАЙ ОДИН ЦЕЛЬНЫЙ КАДР" in result
    assert "Дракончик тянет рычаг" in result
    assert "наклоняется вперёд" in result
    assert "низкая камера" in result
    assert "Body mechanics" not in result
    assert "COLOR SCRIPT" not in result


def test_travel_prompt_is_rebuilt_in_russian() -> None:
    source = """
SCENE DESCRIPTION:
Дракончик переходит ручей по мокрым камням.
SCENE TITLE:
Через ручей
SCENE STORY:
Он удерживает фонарь над водой.
SHARED ART STYLE:
English style block.
CHARACTER APPEARANCE TO PRESERVE EXACTLY:
Серый дракончик в зелёном плаще с медным фонарём.
CHARACTER REFERENCE ASSETS:
https://example.test/pet.png
Character consistency rules:
English rules.
"""

    result = adapt_kandinsky_prompt(source, task="travel/scene_01_image")

    assert len(result) <= 2048
    assert "СОЗДАЙ ОДНУ ИЛЛЮСТРАЦИЮ" in result
    assert "переходит ручей" in result
    assert "Серый дракончик" in result
    assert "SCENE DESCRIPTION" not in result
    assert "Character consistency rules" not in result
