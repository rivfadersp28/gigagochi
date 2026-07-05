from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.character_cards import import_character_card, upgrade_character_bible_v2
from app.services.pet_reply_engine.age_profiles import (
    TEMPLATE_SOURCE_AGE_RULE,
    sanitize_source_age_claims,
)
from app.services.pokemon_template_presets import create_character_bible_from_pokemon_preset

TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")

QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "dragon": ("dragon", "fantasy", "creature", "fire", "rpg", "harpy", "demihuman"),
    "дракон": ("dragon", "fantasy", "creature", "fire", "rpg", "harpy", "demihuman"),
    "дракона": ("dragon", "fantasy", "creature", "fire", "rpg", "harpy", "demihuman"),
    "дракончик": ("dragon", "fantasy", "creature", "fire", "rpg", "harpy", "demihuman"),
    "маг": ("fantasy", "rpg", "otome", "isekai", "royalty"),
    "магия": ("fantasy", "rpg", "otome", "isekai", "royalty"),
    "волшеб": ("fantasy", "rpg", "otome", "isekai", "royalty"),
    "принцесса": ("fantasy", "otome", "royalty", "villainess"),
    "рыцарь": ("knight", "fantasy", "rpg", "royalty", "historical"),
    "рыцаря": ("knight", "fantasy", "rpg", "royalty", "historical"),
    "knight": ("knight", "fantasy", "rpg", "royalty", "historical"),
    "замок": ("fantasy", "royalty", "historical"),
    "супергерой": ("superhero", "action", "adventure", "sci-fi"),
    "супергероя": ("superhero", "action", "adventure", "sci-fi"),
    "герой": ("superhero", "action", "adventure"),
    "hero": ("superhero", "action", "adventure"),
    "демон": ("goth", "fantasy", "romance", "roleplay"),
    "демона": ("goth", "fantasy", "romance", "roleplay"),
    "суккуб": ("succubus", "goth", "romance", "roleplay"),
    "гот": ("goth", "punk", "angst"),
    "панк": ("punk", "goth", "angst"),
    "семья": ("family", "wholesome", "drama"),
    "семью": ("family", "wholesome", "drama"),
    "сестра": ("sister", "family", "platonic"),
    "сестру": ("sister", "family", "platonic"),
    "мама": ("family", "wholesome", "slice", "life"),
    "мать": ("family", "wholesome", "slice", "life"),
    "хоррор": ("horror", "cryptid", "murderer"),
    "страш": ("horror", "cryptid"),
    "криптид": ("cryptid", "horror", "non", "human"),
    "птица": ("birds", "harpy", "fantasy"),
    "птицу": ("birds", "harpy", "fantasy"),
    "гарпия": ("harpy", "birds", "fantasy"),
    "королев": ("royalty", "fantasy", "historical"),
    "исекай": ("isekai", "fantasy", "otome"),
    "отоме": ("otome", "fantasy", "villainess"),
}

NAME_REPLACEMENTS = {
    "дракона": "Дракон",
    "дракон": "Дракон",
    "дракончика": "Дракончик",
    "дракончик": "Дракончик",
    "птицу": "Птица",
    "птица": "Птица",
    "гарпию": "Гарпия",
    "гарпия": "Гарпия",
    "кота": "Кот",
    "кот": "Кот",
    "котенка": "Котенок",
    "кошку": "Кошка",
    "кошка": "Кошка",
    "лису": "Лиса",
    "лиса": "Лиса",
    "демона": "Демон",
    "демон": "Демон",
    "криптида": "Криптид",
    "криптид": "Криптид",
    "рыцаря": "Рыцарь",
    "рыцарь": "Рыцарь",
    "knight": "Knight",
}

VISUAL_TRAIT_TERMS: dict[str, tuple[str, ...]] = {
    "tail": ("tail", "tails", "хвост", "хвостик", "хвосты"),
    "horns": ("horn", "horns", "рог", "рога", "рожки"),
    "wings": ("wing", "wings", "крыло", "крылья", "крыльями"),
    "scales": ("scale", "scales", "scaled", "чешуя", "чешуй", "чешуйчат"),
    "animal_traits": (
        "animal part",
        "animal parts",
        "demihuman",
        "demihumans",
        "beast",
        "звер",
        "животн",
        "полузвер",
        "демихуман",
    ),
    "dragon_body": ("dragon", "dragon-line", "wyvern", "дракон", "драконь"),
}

VISUAL_TRAIT_ALLOW_TERMS: dict[str, tuple[str, ...]] = {
    "tail": (
        "tail",
        "хвост",
        "дракон",
        "dragon",
        "wyvern",
        "кот",
        "кошка",
        "cat",
        "лиса",
        "fox",
        "собак",
        "dog",
        "птиц",
        "bird",
        "гарп",
        "harpy",
        "звер",
        "animal",
        "антро",
        "anthro",
        "демихуман",
        "demihuman",
        "демон",
        "demon",
        "суккуб",
        "succubus",
    ),
    "horns": (
        "horn",
        "рог",
        "дракон",
        "dragon",
        "wyvern",
        "демон",
        "demon",
        "суккуб",
        "succubus",
        "олень",
        "deer",
        "коз",
        "goat",
    ),
    "wings": (
        "wing",
        "крыл",
        "дракон",
        "dragon",
        "wyvern",
        "птиц",
        "bird",
        "гарп",
        "harpy",
        "ангел",
        "angel",
        "фея",
        "fairy",
        "демон",
        "demon",
    ),
    "scales": (
        "scale",
        "чешу",
        "дракон",
        "dragon",
        "wyvern",
        "ящер",
        "lizard",
        "зме",
        "snake",
        "рептил",
        "reptile",
    ),
    "animal_traits": (
        "звер",
        "animal",
        "животн",
        "антро",
        "anthro",
        "демихуман",
        "demihuman",
        "полу",
        "кот",
        "кошка",
        "cat",
        "лиса",
        "fox",
        "собак",
        "dog",
        "птиц",
        "bird",
        "гарп",
        "harpy",
    ),
    "dragon_body": ("дракон", "dragon", "wyvern"),
}

VISUAL_TRAIT_LABELS = {
    "tail": "tail / хвост",
    "horns": "horns / рога",
    "wings": "wings / крылья",
    "scales": "scales or scaled limbs / чешуя",
    "animal_traits": "demihuman or animal-body traits",
    "dragon_body": "dragon body or dragon lineage",
}

VISUAL_TERM_REPLACEMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "tail": (
        (r"\btails\b", "cloak hems"),
        (r"\btail\b", "cloak hem"),
        (r"\bхвостик(?:ом|а|е)?\b", "край плаща"),
        (r"\bхвост(?:ом|а|е|ы|ов)?\b", "плащ"),
    ),
    "horns": (
        (r"\bhorns\b", "helmet crests"),
        (r"\bhorn\b", "helmet crest"),
        (r"\bрожки\b", "детали шлема"),
        (r"\bрога\b", "нашлемник"),
        (r"\bрог(?:ом|а|е)?\b", "нашлемник"),
    ),
    "wings": (
        (r"\bwings\b", "cape panels"),
        (r"\bwing\b", "cape panel"),
        (r"\bкрыльями\b", "плащом"),
        (r"\bкрылья\b", "плащ"),
        (r"\bкрыло\b", "полотнище плаща"),
    ),
    "scales": (
        (r"\bscaled\b", "armored"),
        (r"\bscales\b", "armor plates"),
        (r"\bscale\b", "armor plate"),
        (r"\bчешуйчат\w*\b", "пластинчатый"),
        (r"\bчешу[яие]\b", "доспех"),
    ),
    "animal_traits": (
        (r"\bdemihumans\b", "people"),
        (r"\bdemihuman\b", "person"),
        (r"\banimal parts\b", "personal traits"),
        (r"\banimal part\b", "personal trait"),
        (r"\bbeast\b", "fighter"),
        (r"\bживотн\w*\b", "особые"),
        (r"\bзверин\w*\b", "особые"),
        (r"\bдемихуман\w*\b", "жители"),
        (r"\bполузвер\w*\b", "жители"),
    ),
    "dragon_body": (
        (r"\bdragon-line\b", "old royal line"),
        (r"\bdragons\b", "old powers"),
        (r"\bdragon\b", "old power"),
        (r"\bwyverns\b", "old powers"),
        (r"\bwyvern\b", "old power"),
        (r"\bдраконь\w*\b", "старинный"),
        (r"\bдракон(?:а|ы|ов|ом|е)?\b", "старая сила"),
    ),
}

PROMPT_PREFIX_PATTERNS = (
    re.compile(r"^\s*я\s+хочу\s+(?:сделать|создать)\s+", re.I),
    re.compile(r"^\s*хочу\s+(?:сделать|создать)\s+", re.I),
    re.compile(r"^\s*(?:сделай|создай)\s+(?:мне\s+)?", re.I),
    re.compile(r"^\s*(?:персонажа|питомца)\s+", re.I),
)


@dataclass(frozen=True)
class CharacterTemplate:
    id: str
    filename: str
    path: Path
    payload: dict[str, Any]
    name: str
    tags: tuple[str, ...]
    search_text: str


@dataclass(frozen=True)
class CharacterTemplateSelection:
    template: CharacterTemplate
    score: float
    matched_terms: tuple[str, ...]


def _default_templates_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "character_templates"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _strings(value: Any, *, limit: int = 40) -> tuple[str, ...]:
    result: list[str] = []
    for item in _list(value):
        text = _string(item)
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _card_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _dict(payload.get("data"))
    return data if data else _dict(payload)


def _collect_text(value: Any, *, limit: int = 20000) -> str:
    parts: list[str] = []

    def collect(item: Any) -> None:
        if sum(len(part) for part in parts) >= limit:
            return
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            for child in item:
                collect(child)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)

    collect(value)
    return " ".join(parts)[:limit]


def _tokens(text: str | None) -> tuple[str, ...]:
    return tuple(token.casefold() for token in TOKEN_PATTERN.findall(text or ""))


def _expanded_query_terms(text: str) -> tuple[str, ...]:
    result: list[str] = []

    def add(term: str) -> None:
        clean = term.casefold().strip()
        if clean and clean not in result:
            result.append(clean)

    for token in _tokens(text):
        add(token)
        if token.endswith(("а", "у", "ю", "ы", "и")) and len(token) > 4:
            add(token[:-1])
        for suffix in ("ого", "его", "ому", "ему", "ыми", "ими"):
            if token.endswith(suffix) and len(token) > len(suffix) + 3:
                add(token[: -len(suffix)])
        for alias in QUERY_ALIASES.get(token, ()):
            add(alias)
        for key, aliases in QUERY_ALIASES.items():
            if token.startswith(key) or key.startswith(token):
                for alias in aliases:
                    add(alias)
    return tuple(result)


def _template_from_path(path: Path) -> CharacterTemplate | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = _card_data(payload)
    name = _string(data.get("name"))
    tags = _strings(data.get("tags"), limit=50)
    search_text = " ".join(
        item
        for item in (
            name,
            " ".join(tags),
            _string(data.get("description")),
            _string(data.get("personality")),
            _string(data.get("scenario")),
            _string(data.get("first_mes")),
            _collect_text(data.get("mes_example"), limit=4000),
            _collect_text(data.get("character_book"), limit=6000),
        )
        if item
    )
    if not name and not search_text:
        return None
    return CharacterTemplate(
        id=path.stem,
        filename=path.name,
        path=path,
        payload=payload,
        name=name,
        tags=tags,
        search_text=search_text,
    )


@lru_cache(maxsize=8)
def _load_character_templates_cached(data_dir: str) -> tuple[CharacterTemplate, ...]:
    root = Path(data_dir)
    if not root.exists():
        return ()
    templates: list[CharacterTemplate] = []
    for path in sorted(root.glob("*.json")):
        template = _template_from_path(path)
        if template:
            templates.append(template)
    return tuple(templates)


def load_character_templates(
    *,
    data_dir: Path | None = None,
) -> tuple[CharacterTemplate, ...]:
    return _load_character_templates_cached(str(data_dir or _default_templates_dir()))


def _score_template(
    template: CharacterTemplate,
    query_terms: tuple[str, ...],
) -> tuple[float, tuple[str, ...]]:
    name_tokens = set(_tokens(template.name))
    tag_tokens = set(_tokens(" ".join(template.tags)))
    search_tokens = set(_tokens(template.search_text))

    score = 0.0
    matched: list[str] = []
    for term in query_terms:
        term_score = 0.0
        if term in name_tokens:
            term_score += 6.0
        if term in tag_tokens:
            term_score += 4.0
        if term in search_tokens:
            term_score += 1.0
        if term_score:
            score += term_score
            matched.append(term)

    if not matched and {"fantasy", "creature", "dragon"} & set(query_terms):
        if "fantasy" in tag_tokens:
            score += 1.5
            matched.append("fantasy")

    score += min(len(template.search_text) / 12000, 1.0) * 0.05
    return score, tuple(dict.fromkeys(matched))


def select_character_template(
    user_description: str,
    *,
    templates: tuple[CharacterTemplate, ...] | None = None,
) -> CharacterTemplateSelection:
    available = templates if templates is not None else load_character_templates()
    if not available:
        raise ValueError("No character templates found")

    query_terms = _expanded_query_terms(user_description)
    scored = []
    for template in available:
        score, matched = _score_template(template, query_terms)
        scored.append((score, matched, template))
    score, matched, template = max(
        scored,
        key=lambda item: (item[0], len(item[1]), item[2].filename),
    )
    return CharacterTemplateSelection(template=template, score=score, matched_terms=matched)


def _clean_target_phrase(user_description: str) -> str:
    phrase = user_description.strip().strip("\"'«»“”")
    for pattern in PROMPT_PREFIX_PATTERNS:
        phrase = pattern.sub("", phrase).strip()
    phrase = re.sub(r"\s+", " ", phrase).strip(" .,!?:;")
    lower = phrase.casefold()
    replacements = (
        (r"\bмаленького\b", "маленький"),
        (r"\bсинего\b", "синий"),
        (r"\bкрасного\b", "красный"),
        (r"\bчерного\b", "черный"),
        (r"\bбелого\b", "белый"),
        (r"\bмилого\b", "милый"),
        (r"\bпушистого\b", "пушистый"),
        (r"\bдракона\b", "дракон"),
        (r"\bдракончика\b", "дракончик"),
        (r"\bрыцаря\b", "рыцарь"),
        (r"\bknights\b", "knight"),
    )
    for pattern, replacement in replacements:
        lower = re.sub(pattern, replacement, lower, flags=re.I)
    return lower.strip() or user_description.strip() or "новый персонаж"


def _target_name(target_phrase: str) -> str:
    tokens = _tokens(target_phrase)
    for token in reversed(tokens):
        name = NAME_REPLACEMENTS.get(token)
        if name:
            return name
    if not target_phrase:
        return "Новый персонаж"
    compact = target_phrase.strip()
    if len(compact) > 42:
        compact = compact[:42].rsplit(" ", 1)[0] or compact[:42]
    return compact[:1].upper() + compact[1:]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lower = text.casefold()
    for term in terms:
        clean = term.casefold()
        if re.fullmatch(r"[a-z0-9][a-z0-9 -]*", clean):
            pattern = rf"(?<![a-z0-9]){re.escape(clean)}(?![a-z0-9])"
            if re.search(pattern, lower):
                return True
        elif clean in lower:
            return True
    return False


def _forbidden_visual_traits(target_phrase: str) -> tuple[str, ...]:
    return tuple(
        trait
        for trait, allowed_terms in VISUAL_TRAIT_ALLOW_TERMS.items()
        if not _contains_any(target_phrase, allowed_terms)
    )


def _template_conflicting_visual_traits(
    template: CharacterTemplate,
    forbidden_traits: tuple[str, ...],
) -> tuple[str, ...]:
    search_text = template.search_text.casefold()
    return tuple(
        trait
        for trait in forbidden_traits
        if _contains_any(search_text, VISUAL_TRAIT_TERMS.get(trait, ()))
    )


def _replace_visual_conflicts_in_text(text: str, forbidden_traits: tuple[str, ...]) -> str:
    result = text
    for trait in forbidden_traits:
        for pattern, replacement in VISUAL_TERM_REPLACEMENTS.get(trait, ()):
            result = re.sub(pattern, replacement, result, flags=re.I)
    return result


def _sanitize_visual_conflicts(value: Any, forbidden_traits: tuple[str, ...]) -> Any:
    if not forbidden_traits:
        return value
    if isinstance(value, str):
        return _replace_visual_conflicts_in_text(value, forbidden_traits)
    if isinstance(value, list):
        return [_sanitize_visual_conflicts(item, forbidden_traits) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize_visual_conflicts(item, forbidden_traits)
            for key, item in value.items()
        }
    return value


def _build_visual_constraints(
    target_phrase: str,
    template: CharacterTemplate,
) -> dict[str, Any]:
    forbidden_traits = _forbidden_visual_traits(target_phrase)
    source_conflicts = _template_conflicting_visual_traits(template, forbidden_traits)
    forbidden_features = [
        VISUAL_TRAIT_LABELS[trait]
        for trait in source_conflicts
        if trait in VISUAL_TRAIT_LABELS
    ]
    return {
        "source": "template_preset_visual_alignment",
        "target_form": target_phrase,
        "draw_as": (
            f"Draw the visible body, species, silhouette, costume, and sprite anatomy as "
            f"«{target_phrase}». The user prompt is the source of visual identity."
        ),
        "template_influence": (
            "Use the selected Character Card for story structure, relationships, dialogue rhythm, "
            "and scene logic only. Do not copy incompatible body anatomy from the source card."
        ),
        "forbidden_features": forbidden_features,
        "source_conflicts": [VISUAL_TRAIT_LABELS[trait] for trait in source_conflicts],
        "forbidden_traits": list(source_conflicts),
    }


def _ensure_template_visual_fields(
    character_bible: dict[str, Any],
    *,
    target_phrase: str,
    visual_constraints: dict[str, Any],
) -> None:
    character_bible["species"] = target_phrase
    character_bible["signature_features"] = [
        f"ясная форма и силуэт: {target_phrase}",
        "визуальные детали берутся из пользовательского описания",
    ]
    character_bible["materials"] = [
        f"материалы и костюм соответствуют форме «{target_phrase}»",
    ]
    character_bible["proportions"] = (
        f"пропорции, тело и поза соответствуют форме «{target_phrase}»; "
        "выбранная карточка не задает внешнюю анатомию"
    )
    character_bible["baby_design"] = (
        f"детская версия формы «{target_phrase}»: проще, округлее, меньше деталей"
    )
    character_bible["teen_design"] = (
        f"подростковая версия формы «{target_phrase}»: чуть выше и энергичнее"
    )
    character_bible["adult_design"] = (
        f"взрослая версия формы «{target_phrase}»: завершенный силуэт той же формы"
    )
    anchors = _strings(character_bible.get("do_not_change"), limit=8)
    character_bible["do_not_change"] = list(
        dict.fromkeys((*anchors, f"форма «{target_phrase}»"))
    )
    character_bible["visual_constraints"] = visual_constraints


def _apply_template_visual_alignment(
    character_bible: dict[str, Any],
    *,
    target_phrase: str,
    template: CharacterTemplate,
) -> dict[str, Any]:
    visual_constraints = _build_visual_constraints(target_phrase, template)
    forbidden_traits = tuple(visual_constraints.get("forbidden_traits", ()))
    for key in tuple(character_bible):
        if key in {"extensions", "provenance", "visual_constraints"}:
            continue
        character_bible[key] = _sanitize_visual_conflicts(
            character_bible[key],
            forbidden_traits,
        )

    if forbidden_traits:
        forbidden_labels = [
            VISUAL_TRAIT_LABELS[trait]
            for trait in forbidden_traits
            if trait in VISUAL_TRAIT_LABELS
        ]
        avoid_text = (
            "Не упоминать исходную визуальную анатомию шаблона как канон тела персонажа; "
            f"если встречаются старые признаки ({', '.join(forbidden_labels)}), считать "
            "их замененными текущей формой персонажа."
        )
        dialogue_style = _dict(character_bible.get("dialogue_style"))
        dialogue_style["avoid_patterns"] = list(
            dict.fromkeys((*_strings(dialogue_style.get("avoid_patterns")), avoid_text))
        )
        character_bible["dialogue_style"] = dialogue_style

        voice = _dict(character_bible.get("voice"))
        voice["avoid_patterns"] = list(
            dict.fromkeys((*_strings(voice.get("avoid_patterns")), avoid_text))
        )
        character_bible["voice"] = voice

    _ensure_template_visual_fields(
        character_bible,
        target_phrase=target_phrase,
        visual_constraints=visual_constraints,
    )
    return character_bible


def _apply_template_age_override(character_bible: dict[str, Any]) -> None:
    avoid_text = (
        f"{TEMPLATE_SOURCE_AGE_RULE} Не утверждать в reply числовой возраст из исходной "
        "карточки и не говорить 'мне 26/35/за 30'."
    )
    for key in ("dialogue_style", "voice"):
        section = _dict(character_bible.get(key))
        section["avoid_patterns"] = list(
            dict.fromkeys((*_strings(section.get("avoid_patterns"), limit=12), avoid_text))
        )
        character_bible[key] = section


def _identity_names(original_name: str) -> tuple[str, ...]:
    names = [original_name.strip()]
    names.extend(
        item.strip()
        for item in re.split(r"[\[\]&/,|:;()]+", original_name)
        if item.strip() and len(item.strip()) >= 3
    )
    return tuple(dict.fromkeys(names))


def _replace_identity(value: Any, *, names: tuple[str, ...], target_name: str) -> Any:
    if isinstance(value, str):
        result = value
        for name in names:
            result = re.sub(re.escape(name), target_name, result, flags=re.I)
        return result
    if isinstance(value, list):
        return [_replace_identity(item, names=names, target_name=target_name) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_identity(item, names=names, target_name=target_name)
            for key, item in value.items()
        }
    return value


def _prefixed(value: Any, prefix: str) -> str:
    text = _string(value)
    return f"{prefix}\n\n{text}" if text else prefix


def _appended(value: Any, suffix: str) -> str:
    text = _string(value)
    return f"{text}\n\n{suffix}" if text else suffix


def _ensure_character_book_entry(
    data: dict[str, Any],
    target_phrase: str,
    target_name: str,
) -> None:
    book = _dict(data.get("character_book"))
    entries = _list(book.get("entries"))
    entries.insert(
        0,
        {
            "keys": [target_name, target_phrase],
            "content": (
                f"Главный персонаж этой истории - {target_phrase}. "
                "Сюжетные роли, отношения, стартовые сцены и диалоговые ходы "
                "относятся к этой форме персонажа."
            ),
            "priority": 100,
            "constant": True,
            "selective": False,
        },
    )
    book["entries"] = entries
    data["character_book"] = book


def adapt_character_template_card(
    user_description: str,
    template: CharacterTemplate,
) -> dict[str, Any]:
    target_phrase = _clean_target_phrase(user_description)
    target_name = _target_name(target_phrase)
    original_name = template.name
    names = _identity_names(original_name)
    payload = _replace_identity(deepcopy(template.payload), names=names, target_name=target_name)
    data = _card_data(payload)

    data["name"] = target_name
    data["description"] = _prefixed(
        data.get("description"),
        (
            f"Главный персонаж этой истории - {target_phrase}. "
            "Форма, имя и вид закреплены как канон, а сцены, отношения и диалоговая "
            "интонация остаются частью его истории."
        ),
    )
    data["scenario"] = _appended(
        data.get("scenario"),
        f"Все события и роли этой сцены происходят вокруг персонажа «{target_phrase}».",
    )
    tags = [*(_strings(data.get("tags"), limit=40)), "TemplatePreset", target_phrase]
    data["tags"] = list(dict.fromkeys(item for item in tags if item))
    _ensure_character_book_entry(data, target_phrase, target_name)

    if "data" in payload and isinstance(payload["data"], dict):
        payload["data"] = data
    else:
        payload.update(data)
    return sanitize_source_age_claims(payload)


def _create_character_bible_from_character_template(
    user_description: str,
    *,
    templates: tuple[CharacterTemplate, ...],
) -> dict[str, Any]:
    selection = select_character_template(user_description, templates=templates)
    template = selection.template
    adapted_card = adapt_character_template_card(user_description, template)
    target_phrase = _clean_target_phrase(user_description)
    target_name = _target_name(target_phrase)
    character_bible = import_character_card(
        adapted_card,
        source_url=f"internal://character_templates/{template.filename}",
    )
    character_bible = upgrade_character_bible_v2(
        character_bible,
        raw_description=target_phrase,
    )

    identity = _dict(character_bible.get("identity"))
    identity["name"] = target_name
    identity["species"] = target_phrase
    identity["one_liner"] = (
        f"{target_phrase} с готовыми сценами, отношениями и речевыми привычками"
    )
    character_bible["identity"] = identity
    character_bible["species"] = target_phrase
    character_bible["signature"] = (
        f"{target_phrase} держится на уже заданных сценах, отношениях и диалоговой "
        "интонации. Его имя, вид и телесная логика закреплены как главный канон."
    )
    _apply_template_visual_alignment(
        character_bible,
        target_phrase=target_phrase,
        template=template,
    )
    _apply_template_age_override(character_bible)

    provenance = _dict(character_bible.get("provenance"))
    provenance["source"] = "template_preset"
    provenance["source_urls"] = [f"internal://character_templates/{template.filename}"]
    provenance["license_notes"] = (
        "adapted from user-provided local Character Card template; verify source license "
        "before production distribution"
    )
    character_bible["provenance"] = provenance

    extensions = _dict(character_bible.get("extensions"))
    extensions["template_preset"] = {
        "id": template.id,
        "filename": template.filename,
        "original_name": template.name,
        "target_phrase": target_phrase,
        "target_name": target_name,
        "selection_score": selection.score,
        "matched_terms": list(selection.matched_terms),
        "adaptation_instructions": (
            f"The character is now {target_phrase}. Never say this is a template, prompt, "
            "AI, app character, or reskin. Preserve the original plot and dialogue logic, "
            "but treat the new name, species, body, form, and current app-selected age "
            "stage as canon. Source-card numeric ages are not canon."
        ),
        "age_override_rule": TEMPLATE_SOURCE_AGE_RULE,
    }
    character_bible["extensions"] = extensions
    return character_bible


def create_character_bible_from_template(
    user_description: str,
    *,
    templates: tuple[CharacterTemplate, ...] | None = None,
) -> dict[str, Any]:
    if templates is not None:
        return _create_character_bible_from_character_template(
            user_description,
            templates=templates,
        )
    return create_character_bible_from_pokemon_preset(user_description)
