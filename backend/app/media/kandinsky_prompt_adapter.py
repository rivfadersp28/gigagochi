from __future__ import annotations

import re

KANDINSKY_PROMPT_MAX_CHARS = 2048

_COLOR_TRANSLATIONS = {
    "dark sage green": "тёмный шалфейно-зелёный",
    "smoky moss": "дымчато-моховой",
    "faded ochre": "выцветшая охра",
    "subdued coral": "приглушённый коралловый",
    "dusty terracotta": "пыльная терракота",
    "muted brick red": "приглушённый кирпично-красный",
    "smoked teal": "дымчатый сине-зелёный",
    "faded mustard": "выцветший горчичный",
    "deep petrol blue": "глубокий петрольный синий",
    "smoky teal": "дымчатая бирюза",
    "muted sea-glass green": "приглушённый цвет морского стекла",
    "dusty coral": "пыльный коралловый",
    "dark aubergine": "тёмный баклажановый",
    "muted burgundy": "приглушённый бордовый",
    "soft rust": "мягкий ржаво-рыжий",
    "darkened mustard": "затемнённый горчичный",
    "dusty plum": "пыльный сливовый",
    "muted raspberry": "приглушённый малиновый",
    "smoky rose": "дымчато-розовый",
    "slate green": "сланцево-зелёный",
    "slate blue": "сланцево-синий",
    "smoky lavender": "дымчато-лавандовый",
    "muted malachite": "приглушённый малахитовый",
    "dusty rose": "пыльно-розовый",
    "deep indigo": "глубокий индиго",
    "muted violet": "приглушённый фиолетовый",
    "charcoal teal": "угольно-бирюзовый",
    "dusty apricot": "пыльно-абрикосовый",
}


def _clean(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value).strip()


def _truncate(value: str, limit: int) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    shortened = text[: max(1, limit - 1)].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{shortened}…"


def _first_paragraph(prompt: str) -> str:
    return _clean(prompt.split("\n\n", 1)[0])


def _match(prompt: str, pattern: str, *, limit: int) -> str:
    match = re.search(pattern, prompt, flags=re.IGNORECASE | re.MULTILINE)
    return _truncate(match.group(1), limit) if match else ""


def _section(prompt: str, header: str, following_headers: tuple[str, ...], *, limit: int) -> str:
    start = prompt.find(header)
    if start < 0:
        return ""
    start += len(header)
    ends = [prompt.find(item, start) for item in following_headers]
    end = min((value for value in ends if value >= 0), default=len(prompt))
    return _truncate(prompt[start:end], limit)


def _russian_colors(prompt: str) -> str:
    raw = _match(prompt, r"^- Clothing and accessory colors:\s*(.+)$", limit=300)
    colors = [item.strip() for item in raw.split(",") if item.strip()]
    translated = [_COLOR_TRANSLATIONS.get(color.casefold(), color) for color in colors]
    return ", ".join(translated)


def _fit(value: str) -> str:
    normalized = value.strip()
    if len(normalized) <= KANDINSKY_PROMPT_MAX_CHARS:
        return normalized
    separator = "\n\n"
    budget = KANDINSKY_PROMPT_MAX_CHARS - len(separator)
    head_budget = round(budget * 0.65)
    tail_budget = budget - head_budget
    return f"{normalized[:head_budget].rstrip()}{separator}{normalized[-tail_budget:].lstrip()}"


def _fit_prioritized_blocks(blocks: tuple[tuple[int, str], ...]) -> str:
    """Keep complete semantic blocks, preferring lower priority numbers."""
    normalized = tuple((priority, block.strip()) for priority, block in blocks if block.strip())
    required = [block for priority, block in normalized if priority == 0]
    required_text = "\n\n".join(required)
    if len(required_text) > KANDINSKY_PROMPT_MAX_CHARS:
        raise ValueError("Required Kandinsky prompt blocks exceed provider limit")

    selected = {index for index, (priority, _) in enumerate(normalized) if priority == 0}
    optional = sorted(
        (
            (priority, index, block)
            for index, (priority, block) in enumerate(normalized)
            if priority > 0
        ),
        key=lambda item: (item[0], item[1]),
    )
    for _, index, _ in optional:
        candidate = "\n\n".join(
            block
            for block_index, (_, block) in enumerate(normalized)
            if block_index in selected | {index}
        )
        if len(candidate) <= KANDINSKY_PROMPT_MAX_CHARS:
            selected.add(index)

    return "\n\n".join(
        block for index, (_, block) in enumerate(normalized) if index in selected
    )


def _pet_creation_prompt(prompt: str) -> str:
    subject = _truncate(_first_paragraph(prompt), 180)
    colors = _truncate(
        _russian_colors(prompt) or "пыльные зелёные, выцветшие коричневые и медные",
        180,
    )
    return _fit_prioritized_blocks(
        (
            (
                0,
                f"ЗАДАЧА: создай коллекционную дизайнерскую арт-игрушку ручной "
                f"работы по описанию персонажа: {subject}",
            ),
            (
                0,
                "ИДЕНТИЧНОСТЬ: точно сохрани вид существа, силуэт, цвета, анатомию, "
                "морду, рога, уши, крылья, лапы, хвост и узнаваемые признаки.",
            ),
            (
                0,
                "КОМПОЗИЦИЯ: полный рост без обрезки, по центру; персонаж крупно "
                "занимает вертикальный кадр, видны ступни и хвост. Белый фон с "
                "полями. Не портрет.",
            ),
            (
                0,
                "ОБРАЗ: тихий, меланхоличный, задумчивый, невинный и уязвимый, без "
                "героизма. Увеличенная округлая голова занимает 35–40% полного роста; "
                "высота персонажа 2,5–3 головы. Короткое широкое туловище и короткие "
                "конечности, маленькие кисти и ступни, мягкие формы. Это подростковый "
                "виртуальный питомец, не взрослый человекоподобный герой. Без вытянутого "
                "торса, длинных рук и ног и реалистичных пропорций в 6–8 голов.",
            ),
            (
                0,
                "МАТЕРИАЛЫ: матовая окрашенная смола, потёртая ткань, холст, "
                "состаренная кожа, выветренное дерево, окисленный металл, верёвка, "
                "керамика, картон и бумага. Видны сколы, царапины, пыль, морщины, "
                "швы, заплаты, волокна, винты и заклёпки; без глянца.",
            ),
            (
                0,
                "КОСТЮМ: минимум три слоя ручной одежды: большое пальто или плащ, "
                "шарф, ремни, верёвки, подсумки, заплаты, вышивка, ярлыки, подвески "
                "и дорожное снаряжение. Детали функциональны и составны.",
            ),
            (
                0,
                "АКЦЕНТ: один крупный носимый предмет — шлем, фонарь, маска, "
                "корзина, колокол, чайник, клетка, деревянный ящик или другой "
                "уместный объект. Он функционален, детализирован и не закрывает "
                "лицо и анатомию.",
            ),
            (
                0,
                f"ПАЛИТРА: {colors}; тёплый бежевый, мягкий мох, выцветшая кожа и "
                f"состаренная медь. Землистые цвета и тёплые блики без яркой "
                f"насыщенности.",
            ),
            (
                0,
                "СЪЁМКА: премиальная фотография коллекционного объекта, высокая "
                "детализация, макрореализм фактур при полном росте, физически "
                "достоверные материалы, рассеянный свет и глобальное освещение. "
                "Без текста, логотипа, упаковки и водяного знака.",
            ),
        )
    )


def _pet_scene_prompt() -> str:
    return """
Первая картинка — точный эталон персонажа, вторая картинка — обязательный фон.
Помести персонажа с первой картинки в центр сцены на второй картинке, покажи его
целиком. Зафиксируй его внутри центральной вертикальной рамки: весь видимый силуэт
вместе с рогами, ушами, крыльями и хвостом занимает 55–60% высоты кадра и не более
55% ширины. Точка опоры ног находится примерно на 84% высоты кадра. Оставь воздух
над головой и по бокам; не приближай персонажа и не делай крупный план.

НЕ ПЕРЕРИСОВЫВАЙ И НЕ УПРОЩАЙ ПЕРСОНАЖА. Точно сохрани вид существа, силуэт,
пропорции, лицо, выражение, рога, крылья, хвост, цвета и все мелкие элементы:
слои одежды, швы, канты, заплаты, застёжки, карманы, ремни, подвески, вышивку,
заклёпки, патину, стекло, страницы, фурнитуру и фактуры материалов. Не удаляй,
не объединяй и не заменяй сложные детали более простыми формами.

ПРОПОРЦИИ: голова занимает 35–40% полного роста; весь персонаж высотой примерно
2,5–3 головы. Сохрани короткое широкое туловище и короткие конечности. Не вытягивай
торс, руки или ноги и не превращай питомца во взрослого человекоподобного героя.

СРЕДА: тихий мшистый лес, мягкая трава, старые деревья, размытая листва и тонкая
атмосферная глубина. Фон вторичен, свободен от визуального шума и создаёт тихое
сказочное настроение. Сохрани фактическое содержание второй картинки.

СВЕТ И СЪЁМКА: мягкий рассеянный лесной свет с тёплыми бликами, деликатная
объёмная атмосфера, контактная тень, малая глубина резкости, реалистичное
глобальное освещение и премиальная фотография коллекционного объекта. Подчеркни
матовую смолу, ткань, кожу, дерево, окисленный металл, стекло и ручные швы.

Не меняй дизайн, одежду, аксессуары, позу и выражение персонажа. Без новых
предметов, текста, логотипов, рамок и интерфейса.
""".strip()


def _pet_restyle_prompt() -> str:
    return """
Первая картинка — единственный и точный эталон персонажа. Сохрани без изменений
вид существа, анатомию, силуэт, пропорции, позу, выражение, рога, крылья, хвост,
одежду, книгу, фонарь, ремни, застёжки, заклёпки, цвета и расположение каждой
детали. Ничего не добавляй, не удаляй, не упрощай и не меняй местами.

ИЗМЕНИ ТОЛЬКО СПОСОБ ИЗОБРАЖЕНИЯ: это фотореалистичный кинокадр живого
полноразмерного персонажа, физически созданного для съёмки с практическими
спецэффектами. Реальная оптика, естественная перспектива и объёмный мягкий свет.
Убери контурную обводку, рисунок, штриховку, мазки и плоскую заливку.

ФИЗИЧЕСКИЕ ТЕКСТУРЫ: объёмный микрорельеф чешуи и кожи; у тканей видны плетение,
ворс, швы, складки и потрёпанные края; на кожаных ремнях — поры и заломы; на
металле — царапины, патина и окисление; у стекла — толщина, блики, прозрачность
и преломление; у дерева и бумаги — волокна и слои. Свет физически правдоподобно
взаимодействует с каждым материалом.

Сохрани чистый белый фон, полный рост и поля вокруг персонажа. Без окружения,
текста, логотипа, упаковки и водяного знака.
""".strip()


def _background_story_prompt(prompt: str) -> str:
    following = ("HERO POSE", "COLOR SCRIPT", "Используй персонажа", "Персонаж")
    scene = _section(prompt, "СЦЕНА:", following, limit=650)
    pose = _match(prompt, r"^- Body mechanics:\s*(.+)$", limit=280)
    camera = _match(prompt, r"^- Camera and framing:\s*(.+)$", limit=180)
    palette = _match(prompt, r"^- Main dark-muted-pastel palette:\s*(.+)$", limit=240)
    accent = _match(prompt, r"^- Restrained accent:\s*(.+)$", limit=120)
    return _fit(
        f"""
СОЗДАЙ ОДИН ЦЕЛЬНЫЙ КАДР ПО СЦЕНЕ:
{scene}

ГЛАВНЫЙ ПЕРСОНАЖ: используй приложенный референс как точный якорь личности.
Сохрани силуэт, лицо, пропорции, цвета, одежду и аксессуары, но не копируй
нейтральную позу референса. Не переноси внешность главного героя на остальных.

ПОЗА И КАМЕРА: {pose or "поза ясно выражает действие сцены всем телом"}.
Камера: {camera or "ясно показывает действие и контакт персонажа с окружением"}.
Должны читаться наклон корпуса, направление взгляда, работа конечностей, перенос
веса и точки контакта.

ЦВЕТОВОЙ СЦЕНАРИЙ: {palette or "четыре тёмных приглушённо-пастельных цвета"}.
Сдержанный акцент: {accent or "один пыльный контрастный цвет"}. Материалы можно
окрашивать; не своди сцену к бежевому, коричневому, грязно-серому или сепии.
Без неона, электрических оттенков, конфетной насыщенности и глянцевой заливки.

СТИЛЬ: фотореалистичный кинокадр живых полноразмерных героев, физически созданных
для съёмки с практическими спецэффектами. Реальная оптика и свет, без контурной
обводки, рисунка, мазков и плоской заливки. У ткани видны плетение и ворс, у
дерева и бумаги волокна, у стекла толщина и преломление, у металла царапины и
патина. Естественная перспектива без макросъёмки, японский минимализм.

Без текста, подписей, логотипов, водяных знаков, коллажа и интерфейса.
"""
    )


def _travel_prompt(prompt: str) -> str:
    scene = _section(
        prompt,
        "SCENE DESCRIPTION:",
        ("SCENE TITLE:", "SCENE STORY:", "SHARED ART STYLE:"),
        limit=500,
    )
    title = _section(prompt, "SCENE TITLE:", ("SCENE STORY:",), limit=140)
    story = _section(prompt, "SCENE STORY:", ("SHARED ART STYLE:",), limit=400)
    appearance = _section(
        prompt,
        "CHARACTER APPEARANCE TO PRESERVE EXACTLY:",
        ("CHARACTER REFERENCE ASSETS:", "Character consistency rules:"),
        limit=420,
    )
    return _fit(
        f"""
СОЗДАЙ ОДНУ ИЛЛЮСТРАЦИЮ СЦЕНЫ ПУТЕШЕСТВИЯ.

СЦЕНА: {scene}
НАЗВАНИЕ: {title}
СЮЖЕТНЫЙ МОМЕНТ: {story}

ВНЕШНОСТЬ ГЛАВНОГО ПЕРСОНАЖА: {appearance}
Используй приложенный референс как главный источник внешности. Точно сохрани
вид, силуэт, пропорции, расположение лица, цвета, отметины, материалы, одежду,
аксессуары и возраст. Менять можно только позу, выражение и действие. Не меняй
вид существа и палитру, не добавляй доминирующие черты и не скрывай героя реквизитом.

КОМПОЗИЦИЯ: герой ясно виден и остаётся центром истории, но окружение и действие
тоже читаются. Сильный силуэт, понятные передний, средний и дальний планы,
кинематографическая глубина и движение. Один цельный кадр без рамок, карточек,
интерфейса, текста, подписей, облачков речи, водяных знаков, коллажа и разделённых
панелей. Без жестокости, хоррора и персонажей, защищённых авторским правом.
"""
    )


def adapt_kandinsky_prompt(prompt: str, *, task: str) -> str:
    normalized_task = task.strip().lower()
    if normalized_task == "pet_creation/image":
        return _pet_creation_prompt(prompt)
    if normalized_task == "pet_creation/scene":
        return _pet_scene_prompt()
    if normalized_task == "pet_creation/restyle":
        return _pet_restyle_prompt()
    if normalized_task == "background_story/image":
        return _background_story_prompt(prompt)
    if normalized_task.startswith("travel/"):
        return _travel_prompt(prompt)
    return _fit(prompt)
