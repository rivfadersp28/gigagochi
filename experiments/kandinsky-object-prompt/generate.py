from __future__ import annotations

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

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.image_service import (  # noqa: E402
    PET_SCENE_BACKGROUND_PATH,
    _atomic_write_nonempty,
    _is_valid_image_file,
    generate_image_bytes,
)


def _character_prompt() -> str:
    return """
ЗАДАЧА: создай одного персонажа-предмет — живой старинный унитаз. Это именно
унитаз, а не человек, животное, яблоко, демон или дракон.

ОБЯЗАТЕЛЬНЫЙ СИЛУЭТ: хорошо читаемая широкая овальная фарфоровая чаша унитаза с
глубоким открытым отверстием, толстым ободом и дубовым сиденьем с медными
заклёпками. Сзади видна компактная фарфоровая спинка. Сбоку свисает латунная
цепочка с глиняной грушей. На борту проходит тонкая трещина-молния.

ОПОРА: чаша стоит на четырёх коротких витых чугунных ножках-подпорах. Это ножки
мебели и сантехнического предмета, не человеческие и не звериные конечности.

МАТЕРИАЛЫ И ПАЛИТРА: глазурованный состаренный фаянс, потускневший чугун, дуб,
латунь и медные заклёпки. Молочно-белый фарфор, пыльный терракотовый кант,
приглушённый кирпично-красный обод, копчёный бирюзовый налёт, выцветшая горчица.
Матовые ручные фактуры, патина, небольшие сколы, царапины и пыль.

ХАРАКТЕР: спокойный, меланхоличный коллекционный персонаж. Допустимы только два
маленьких простых глаза непосредственно на передней стенке чаши. Не превращай
чашу в голову и не добавляй отдельную голову или лицо.

КОМПОЗИЦИЯ: весь предмет целиком, строго по центру вертикального кадра, ракурс
три четверти, хорошо видны чаша, отверстие, сиденье, цепочка и четыре опоры.
Чистый равномерный белый фон с полями, без пола и без тени. Премиальная фотография
коллекционной арт-игрушки ручной работы, высокая детализация.

ЖЁСТКИЕ ЗАПРЕТЫ: без яблока и других фруктов; без рогов, ушей, крыльев, хвоста,
морды, лап, рук, человеческих ног, туловища, кожи, волос и одежды; без шлема,
пальто, шарфа, ремней, рюкзака и оружия. Не делай гуманоида или животное.
Без текста, логотипа, рамки, интерфейса и водяного знака.
""".strip()


def _scene_prompt() -> str:
    return """
Первая картинка — точный эталон живого унитаза-персонажа. Вторая картинка —
обязательный лесной фон. Перенеси неизменённый предмет с первой картинки в центр
сцены на второй картинке.

Полностью сохрани форму овальной фарфоровой чаши, открытое отверстие, толстый
обод, дубовое сиденье, четыре короткие чугунные опоры, латунную цепочку,
трещину-молнию, цвета, патину и материалы. Унитаз должен однозначно читаться как
унитаз, а не как голова, человек, яблоко, демон или дракон.

Покажи предмет целиком. Он занимает 55–60% высоты центральной части вертикального
кадра. Точка опоры находится примерно на 84% высоты. Оставь воздух сверху и по
бокам. Сохрани фактическое содержание лесного фона, мягкий рассеянный свет,
контактную тень и малую глубину резкости.

Не добавляй отдельную голову, рога, уши, крылья, хвост, руки, человеческие ноги,
туловище или одежду. Без новых предметов, текста, логотипов, рамок и интерфейса.
""".strip()


def _image_reference(path: Path) -> dict[str, object]:
    import base64

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }


def main() -> None:
    metadata = json.loads(SOURCE_METADATA.read_text(encoding="utf-8"))
    if metadata.get("description") != "унитаз":
        raise RuntimeError("Expected the toilet character metadata")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    character_path = OUTPUT_ROOT / "toilet-character.png"
    normal_path = OUTPUT_ROOT / "toilet-normal.png"
    character_prompt = _character_prompt()
    scene_prompt = _scene_prompt()

    request = {
        "experiment": "kandinsky-object-prompt-v1",
        "sourceMetadata": str(SOURCE_METADATA),
        "sourceCharacterBible": metadata["characterBible"],
        "characterPrompt": character_prompt,
        "scenePrompt": scene_prompt,
        "applicationIntegration": False,
    }
    _atomic_write_nonempty(
        OUTPUT_ROOT / "request.json",
        json.dumps(request, ensure_ascii=False, indent=2).encode("utf-8"),
    )

    if not _is_valid_image_file(character_path):
        _atomic_write_nonempty(
            character_path,
            generate_image_bytes(
                character_prompt,
                label="experiment/kandinsky_object/toilet_character",
                size="768x1280",
                provider="kandinsky",
            ),
        )

    if not _is_valid_image_file(normal_path):
        _atomic_write_nonempty(
            normal_path,
            generate_image_bytes(
                scene_prompt,
                label="experiment/kandinsky_object/toilet_scene",
                input_references=[
                    _image_reference(character_path),
                    _image_reference(PET_SCENE_BACKGROUND_PATH),
                ],
                provider="kandinsky",
            ),
        )

    print(normal_path.resolve())


if __name__ == "__main__":
    main()
