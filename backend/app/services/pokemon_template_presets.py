from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.character_cards import upgrade_character_bible_v2
from app.services.pet_reply_engine.age_profiles import (
    TEMPLATE_SOURCE_AGE_RULE,
    sanitize_source_age_claims,
)

TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{2,}")
HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s*$")
FIELD_PATTERN = re.compile(r"^-\s+([^:]+):\s*(.*?)\s*$")

SOURCE_FILE_NAME = "pokemon_descriptions.md"
SOURCE_URL = "internal://description_presets/species_descriptions.md"

BRAND_TERMS: tuple[str, ...] = (
    "Pokémon",
    "Pokemon",
    "POKéMON",
    "PokéAPI",
    "PokeAPI",
    "Poké Ball",
    "Poke Ball",
    "POKé BALL",
    "trainer",
    "trainers",
)

QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "огонь": ("fire", "flame", "hot", "burn", "ember"),
    "огнен": ("fire", "flame", "hot", "burn", "ember"),
    "плам": ("fire", "flame", "hot", "burn", "ember"),
    "жар": ("fire", "flame", "hot", "burn", "ember"),
    "ящер": ("lizard", "reptile", "tail"),
    "рептил": ("lizard", "reptile", "scale", "tail"),
    "дракон": ("dragon", "fire", "lizard", "wing", "scale"),
    "вода": ("water", "sea", "ocean", "river", "swim", "bubble"),
    "водн": ("water", "sea", "ocean", "river", "swim", "bubble"),
    "черепах": ("turtle", "shell", "water"),
    "панцир": ("shell", "armor", "protect", "turtle"),
    "электр": ("electric", "electricity", "thunder", "lightning", "zap", "spark"),
    "молни": ("electric", "electricity", "thunder", "lightning", "zap", "spark"),
    "мыш": ("mouse", "small", "quick"),
    "раст": ("plant", "seed", "leaf", "flower", "sunlight", "bloom"),
    "лист": ("plant", "leaf", "seed", "flower"),
    "цвет": ("flower", "bloom", "plant", "aroma"),
    "сем": ("seed", "plant", "growth"),
    "птиц": ("bird", "wing", "fly", "feather"),
    "крыл": ("wing", "fly", "bird"),
    "зме": ("snake", "serpent", "coil", "poison"),
    "яд": ("poison", "venom", "sting", "toxic"),
    "призрак": ("ghost", "shadow", "night"),
    "тень": ("shadow", "ghost", "night"),
    "ноч": ("night", "moon", "shadow"),
    "кам": ("rock", "stone", "mountain", "cave"),
    "скал": ("rock", "stone", "mountain", "cave"),
    "лед": ("ice", "snow", "cold", "freeze"),
    "снег": ("ice", "snow", "cold", "freeze"),
    "жук": ("bug", "cocoon", "silk", "insect"),
    "псих": ("psychic", "mind", "dream", "hypnosis"),
    "сон": ("sleep", "dream", "hypnosis"),
    "звук": ("sound", "sing", "voice", "screech"),
    "песня": ("sound", "sing", "voice"),
    "поет": ("sound", "sing", "voice"),
    "лес": ("forest", "plant", "leaf"),
    "быстр": ("quick", "fast", "speed"),
    "защит": ("protect", "shell", "armor"),
    "брон": ("armor", "shell", "protect"),
}

NAME_REPLACEMENTS = {
    "дракон": "Дракон",
    "дракончик": "Дракончик",
    "ящер": "Ящер",
    "ящерица": "Ящерица",
    "черепаха": "Черепаха",
    "мышь": "Мышь",
    "птица": "Птица",
    "кот": "Кот",
    "котенок": "Котенок",
    "кошка": "Кошка",
    "лиса": "Лиса",
    "змея": "Змея",
    "рыцарь": "Рыцарь",
}

PROMPT_PREFIX_PATTERNS = (
    re.compile(r"^\s*я\s+хочу\s+(?:сделать|создать)\s+", re.I),
    re.compile(r"^\s*хочу\s+(?:сделать|создать)\s+", re.I),
    re.compile(r"^\s*(?:сделай|создай)\s+(?:мне\s+)?", re.I),
    re.compile(r"^\s*(?:персонажа|питомца)\s+", re.I),
)


@dataclass(frozen=True)
class PokemonDescriptionRecord:
    source_id: str
    source_name: str
    display_name: str
    generation: str
    genus: str
    evolution_path: tuple[str, ...]
    descriptions: tuple[str, ...]
    source_path: str = SOURCE_URL


@dataclass(frozen=True)
class PokemonPresetSelection:
    record: PokemonDescriptionRecord
    score: float
    confidence: str
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class FeatureMotif:
    key: str
    terms: tuple[str, ...]
    summary: str
    core_want: str
    inner_conflict: str
    comfort_action: str
    fear: str
    routine: str
    voice_rule: str
    sample_reply: str
    colors: tuple[str, ...]
    materials: tuple[str, ...]


FEATURE_MOTIFS: tuple[FeatureMotif, ...] = (
    FeatureMotif(
        key="fire",
        terms=("fire", "flame", "hot", "burn", "ember", "tail flame", "blaze"),
        summary="бережет внутреннее тепло и быстро оживляется, когда вокруг появляется дело",
        core_want="научиться держать свой внутренний жар ровным и полезным",
        inner_conflict=(
            "легко вспыхивает идеей, но переживает, что может слишком резко "
            "отреагировать"
        ),
        comfort_action="прижимает лапки к теплому месту и считает короткие вдохи",
        fear="холодный дождь, резкие слова и ситуации, где его пыл никому не нужен",
        routine="проверяет, достаточно ли в доме тепла для маленьких дел",
        voice_rule="говорит живо и тепло, но старается не давить напором",
        sample_reply="я уже разогрелся идеей, но скажу аккуратно: сначала попробуем маленький шаг.",
        colors=("теплый янтарный", "мягкий красный", "угольный акцент"),
        materials=("теплая матовая кожа", "мягкие искристые акценты"),
    ),
    FeatureMotif(
        key="electric",
        terms=(
            "electric",
            "electricity",
            "thunder",
            "lightning",
            "zap",
            "spark",
            "power plant",
            "current",
        ),
        summary="копит маленькие импульсы и реагирует на мир быстрыми вспышками внимания",
        core_want="научиться выпускать энергию вовремя, не пугая близких",
        inner_conflict=(
            "быстро возбуждается от новых сигналов и иногда боится задеть кого-то "
            "резкой реакцией"
        ),
        comfort_action="перебирает кончиками лап невидимые искры, пока не становится спокойнее",
        fear="внезапные прикосновения, перегруз и слишком тихие паузы без понятного сигнала",
        routine="проверяет, где в доме есть безопасный запас бодрости",
        voice_rule="отвечает короткими быстрыми фразами, как будто ловит искру мысли",
        sample_reply="ага, сигнал поймал. я отвечу быстро, пока мысль не убежала в проводах.",
        colors=("солнечно-желтый", "теплый кремовый", "мягкий темный контур"),
        materials=("гладкая мягкая шерсть", "маленькие световые акценты"),
    ),
    FeatureMotif(
        key="water",
        terms=("water", "sea", "ocean", "river", "swim", "bubble", "rain", "pond"),
        summary="держится плавно, любит ясный ритм и умеет успокаиваться через воду",
        core_want="сохранять спокойный поток даже тогда, когда вокруг много шума",
        inner_conflict=(
            "хочет быть мягким, но иногда прячется, если разговор становится слишком "
            "резким"
        ),
        comfort_action="проводит лапкой по краю чаши или воображаемой волне",
        fear="сухая суета, громкие споры и просьбы торопиться без причины",
        routine="выбирает самый тихий уголок и приводит мысли в ровные круги",
        voice_rule="говорит плавно, с ясной причиной и без лишнего шума",
        sample_reply="я понял. давай пустим это по тихой воде: сначала главное, потом деталь.",
        colors=("мягкий голубой", "морской синий", "белый блик"),
        materials=("гладкая влажная фактура", "полупрозрачные водяные акценты"),
    ),
    FeatureMotif(
        key="plant",
        terms=("seed", "plant", "bulb", "flower", "leaf", "sunlight", "bloom", "aroma"),
        summary="растет постепенно, набирает силы от света, заботы и повторяющихся ритуалов",
        core_want="расти своим темпом и однажды раскрыть то, что пока прячет внутри",
        inner_conflict=(
            "хочет тянуться к свету, но смущается, когда на него смотрят слишком "
            "пристально"
        ),
        comfort_action="устраивается ближе к светлому месту и тихо расправляет плечи",
        fear="долгая темнота, спешка и ощущение, что рост нужно показать немедленно",
        routine="ищет немного света и отмечает, что за день стало хоть чуть-чуть лучше",
        voice_rule="говорит мягко и терпеливо, через образы роста, света и маленькой заботы",
        sample_reply="я не спешу. если дать этому немного света, мысль сама расправится.",
        colors=("листовой зеленый", "теплый салатовый", "мягкий цветочный акцент"),
        materials=("бархатистая растительная фактура", "мягкие листовые детали"),
    ),
    FeatureMotif(
        key="shell",
        terms=("shell", "turtle", "withdraw", "hide", "protect", "armor"),
        summary="умеет защищать нежное внутри и открывается только там, где безопасно",
        core_want="научиться выходить из защиты, не теряя чувства безопасности",
        inner_conflict="любит быть рядом, но при резкости сразу собирается в себя",
        comfort_action="проверяет свой маленький защитный край и выглядывает снова",
        fear="давление, громкий смех над ошибками и ситуации без пути назад",
        routine="перед каждым новым делом проверяет, где можно спокойно укрыться",
        voice_rule="сначала отвечает осторожно, потом теплеет, если чувствует безопасность",
        sample_reply="я выгляну чуть-чуть. если тут спокойно, скажу больше и не спрячусь.",
        colors=("мягкий бирюзовый", "теплый песочный", "глубокий синий контур"),
        materials=("гладкий защитный панцирь", "мягкая округлая кожа"),
    ),
    FeatureMotif(
        key="air",
        terms=("bird", "wing", "fly", "feather", "sky", "gust"),
        summary="замечает движение воздуха, быстро меняет направление и любит высоту мысли",
        core_want="найти свой маршрут и не сбиваться от каждого встречного ветра",
        inner_conflict="тянется к свободе, но боится потерять связь с теми, кто ждет внизу",
        comfort_action="расправляет плечи и будто проверяет направление ветра",
        fear="тесные углы, тяжелые обещания и слишком долгие неподвижные паузы",
        routine="смотрит, откуда сегодня дует настроение, и выбирает легкий путь",
        voice_rule="говорит подвижно, с быстрыми поворотами и короткими наблюдениями",
        sample_reply="ветер в голове сменился. я отвечу прямо, пока маршрут ясный.",
        colors=("небесный голубой", "молочный белый", "мягкий серый"),
        materials=("легкая перьевая фактура", "мягкие воздушные края"),
    ),
    FeatureMotif(
        key="poison",
        terms=("poison", "venom", "sting", "toxic", "fang", "needle"),
        summary="очень тонко чувствует опасность и учится держать острые реакции под контролем",
        core_want="защищаться так, чтобы не ранить тех, кто пришел с добром",
        inner_conflict="может насторожиться раньше, чем поймет намерение собеседника",
        comfort_action="убирает острые детали ближе к себе и делает короткую паузу",
        fear="обманчивая ласковость, внезапные угрозы и просьбы не защищаться",
        routine="проверяет безопасную дистанцию перед тем, как довериться",
        voice_rule="говорит осторожно, иногда колко, но без жестокости",
        sample_reply="я сперва проверю, не колется ли эта мысль. потом подпущу ее ближе.",
        colors=("приглушенный фиолетовый", "темный сливовый", "кислотный маленький акцент"),
        materials=("матовая кожа", "мягкие защитные выступы"),
    ),
    FeatureMotif(
        key="stone",
        terms=("rock", "stone", "mountain", "cave", "ground", "boulder"),
        summary="держится основательно, любит надежные места и медленно набирает доверие",
        core_want="стать тем, на кого можно опереться без громких обещаний",
        inner_conflict="кажется твердым, но внутри долго переживает сдвиги и перемены",
        comfort_action="прижимается к устойчивой поверхности и проверяет опору",
        fear="резкие перемены, шаткие планы и обещания без основания",
        routine="выбирает одно надежное дело и доводит его до конца",
        voice_rule="говорит спокойно, весомо и конкретно",
        sample_reply="я поставлю это на ровное место. тогда будет видно, что делать дальше.",
        colors=("теплый серый", "песочный", "мягкий коричневый"),
        materials=("каменистая матовая фактура", "гладкие округлые грани"),
    ),
    FeatureMotif(
        key="night",
        terms=("night", "moon", "shadow", "ghost", "dark", "dream"),
        summary="лучше всего раскрывается в тишине, замечает скрытое и бережет чужие тайны",
        core_want="понять невысказанное и не напугать своей внимательностью",
        inner_conflict="видит слишком много мелких тревог и не всегда знает, когда о них говорить",
        comfort_action="садится в полутень и выбирает самый тихий тон",
        fear="яркая суета, насмешки над страхами и разговоры без доверия",
        routine="проверяет тихие углы дома и запоминает, где кому спокойнее",
        voice_rule="говорит мягко, чуть загадочно, но всегда понятно",
        sample_reply="я заметил тень у этой мысли. не испугаю ее, просто назову осторожно.",
        colors=("глубокий синий", "дымчатый фиолетовый", "лунный белый"),
        materials=("мягкая бархатная поверхность", "полупрозрачные темные акценты"),
    ),
    FeatureMotif(
        key="sound",
        terms=("sing", "sound", "voice", "screech", "cry", "song"),
        summary="понимает мир через звук, интонацию и маленькие повторы",
        core_want="найти такой голос, который слышно без крика",
        inner_conflict="боится прозвучать слишком громко или не попасть в настроение",
        comfort_action="повторяет короткий ритм и слушает, как он затихает",
        fear="резкие звуки, насмешки над голосом и разговоры без пауз",
        routine="проверяет настроение по маленькому звуку в начале дня",
        voice_rule="использует короткий ритм, но не превращает ответ в песенку",
        sample_reply="я поймал ритм вопроса. отвечу тихо, чтобы он не рассыпался.",
        colors=("мягкий розовый", "теплый кремовый", "тонкий синий акцент"),
        materials=("мягкая гладкая фактура", "маленькие звуковые детали"),
    ),
)

GENERIC_MOTIF = FeatureMotif(
    key="generic",
    terms=(),
    summary="замечает маленькие изменения вокруг и строит доверие через повторяющиеся детали",
    core_want="понять свое место рядом с пользователем и стать смелее в маленьких делах",
    inner_conflict="хочет быть полезным, но иногда сомневается, правильно ли его поймут",
    comfort_action="трогает знакомую деталь рядом с собой и собирается с мыслью",
    fear="слишком резкие перемены, шум и просьбы сразу стать другим",
    routine="каждый день выбирает одну маленькую привычку, которую можно сделать лучше",
    voice_rule="говорит прямо, тепло и через конкретные бытовые детали",
    sample_reply="я понял. возьму эту мысль аккуратно и проверю, где у нее маленькая ручка.",
    colors=("мягкий теплый цвет", "светлый нейтральный", "небольшой контрастный акцент"),
    materials=("мягкая матовая фактура", "приятные округлые детали"),
)


class PokemonPresetDataError(RuntimeError):
    pass


def default_pokemon_descriptions_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "data"
        / "pokemon_template_presets"
        / SOURCE_FILE_NAME
    )


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00ad", "").replace("\f", " ")).strip()


def _strip_md_value(value: str) -> str:
    clean = _normalize_space(value)
    if clean.startswith("`") and clean.endswith("`") and len(clean) >= 2:
        clean = clean[1:-1]
    return clean.strip()


def _slug_name(value: str) -> str:
    clean = _normalize_space(value).casefold()
    clean = re.sub(r"[^a-z0-9а-яё]+", "-", clean)
    return clean.strip("-")


def _dedupe_descriptions(descriptions: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for item in descriptions:
        clean = _normalize_space(item)
        if not clean:
            continue
        identity = clean.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(clean)
    return tuple(result)


def _parse_evolution_path(raw_value: str) -> tuple[str, ...]:
    clean = _strip_md_value(raw_value)
    if not clean or clean.casefold() in {"none", "нет", "-"}:
        return ()
    parts = re.split(r"\s*(?:->|→|\||,|;)\s*", clean)
    return tuple(_slug_name(part) for part in parts if _slug_name(part))


def parse_pokemon_descriptions_markdown(
    markdown: str,
    *,
    source_path: str = SOURCE_URL,
) -> tuple[PokemonDescriptionRecord, ...]:
    records: list[PokemonDescriptionRecord] = []
    current_name = ""
    fields: dict[str, str] = {}
    descriptions: list[str] = []
    in_descriptions = False

    def flush() -> None:
        nonlocal current_name, fields, descriptions
        if not current_name:
            return
        display_name = _normalize_space(current_name)
        source_name = _slug_name(display_name)
        source_id = fields.get("pokeapi species id", "")
        generation = fields.get("generation", "")
        genus = fields.get("genus", "")
        evolution_path = _parse_evolution_path(fields.get("evolution", ""))
        unique_descriptions = _dedupe_descriptions(descriptions)
        if source_id or unique_descriptions:
            records.append(
                PokemonDescriptionRecord(
                    source_id=source_id,
                    source_name=source_name,
                    display_name=display_name,
                    generation=generation,
                    genus=genus,
                    evolution_path=evolution_path,
                    descriptions=unique_descriptions,
                    source_path=source_path,
                )
            )

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        heading_match = HEADING_PATTERN.match(line)
        if heading_match:
            flush()
            current_name = heading_match.group(1)
            fields = {}
            descriptions = []
            in_descriptions = False
            continue

        if not current_name:
            continue
        if line == "Descriptions:":
            in_descriptions = True
            continue
        if in_descriptions and line.startswith("- "):
            descriptions.append(line[2:].strip())
            continue
        field_match = FIELD_PATTERN.match(line)
        if field_match and not in_descriptions:
            fields[field_match.group(1).strip().casefold()] = _strip_md_value(
                field_match.group(2)
            )

    flush()
    return tuple(records)


@lru_cache(maxsize=4)
def _load_pokemon_description_presets_cached(
    path: str,
    mtime_ns: int,
    size: int,
) -> tuple[PokemonDescriptionRecord, ...]:
    del mtime_ns, size
    data_path = Path(path)
    markdown = data_path.read_text(encoding="utf-8")
    records = parse_pokemon_descriptions_markdown(markdown, source_path=SOURCE_URL)
    if not records:
        raise PokemonPresetDataError(f"No pokemon description presets parsed from {data_path}")
    return records


def load_pokemon_description_presets(
    *,
    data_path: Path | None = None,
) -> tuple[PokemonDescriptionRecord, ...]:
    path = data_path or default_pokemon_descriptions_path()
    if not path.exists():
        raise PokemonPresetDataError(f"Pokemon description presets file not found: {path}")
    stat = path.stat()
    return _load_pokemon_description_presets_cached(str(path), stat.st_mtime_ns, stat.st_size)


def _tokens(text: str | None) -> tuple[str, ...]:
    return tuple(token.casefold() for token in TOKEN_PATTERN.findall(text or ""))


def _stem_variants(token: str) -> tuple[str, ...]:
    variants = [token]
    for suffix in (
        "ого",
        "его",
        "ому",
        "ему",
        "ыми",
        "ими",
        "ая",
        "яя",
        "ое",
        "ее",
        "ый",
        "ий",
        "ой",
        "ого",
        "его",
        "ами",
        "ями",
        "ах",
        "ях",
        "ам",
        "ям",
        "ом",
        "ем",
        "а",
        "у",
        "ю",
        "ы",
        "и",
        "е",
    ):
        if token.endswith(suffix) and len(token) > len(suffix) + 3:
            variants.append(token[: -len(suffix)])
    if token.endswith("ies") and len(token) > 5:
        variants.append(token[:-3] + "y")
    if token.endswith("s") and len(token) > 4:
        variants.append(token[:-1])
    return tuple(dict.fromkeys(variants))


def _expanded_query_terms(text: str) -> tuple[str, ...]:
    result: list[str] = []

    def add(term: str) -> None:
        clean = term.casefold().strip()
        if clean and clean not in result:
            result.append(clean)

    for token in _tokens(text):
        for variant in _stem_variants(token):
            add(variant)
            for key, aliases in QUERY_ALIASES.items():
                if variant.startswith(key) or key.startswith(variant):
                    for alias in aliases:
                        add(alias)
    return tuple(result)


def _record_text(record: PokemonDescriptionRecord) -> str:
    return " ".join((record.genus, " ".join(record.descriptions))).casefold()


def _record_terms(record: PokemonDescriptionRecord) -> tuple[str, ...]:
    forbidden = set(_tokens(" ".join(pokemon_source_forbidden_terms(record))))
    terms: list[str] = []
    for token in _tokens(_record_text(record)):
        if token in forbidden:
            continue
        for variant in _stem_variants(token):
            if variant not in terms:
                terms.append(variant)
    for motif in FEATURE_MOTIFS:
        if _contains_term(_record_text(record), motif.terms):
            terms.extend(term for term in motif.terms if term not in terms)
            terms.append(motif.key)
    return tuple(dict.fromkeys(terms))


def _genus_terms(record: PokemonDescriptionRecord) -> tuple[str, ...]:
    genus = _sanitize_text_for_terms(record.genus, pokemon_source_forbidden_terms(record))
    return tuple(dict.fromkeys(term for token in _tokens(genus) for term in _stem_variants(token)))


def _term_matches(term: str, candidate: str) -> bool:
    if term == candidate:
        return True
    if len(term) >= 5 and candidate.startswith(term):
        return True
    return len(candidate) >= 5 and term.startswith(candidate)


def _contains_term(text: str, terms: Iterable[str]) -> bool:
    lower = text.casefold()
    for term in terms:
        clean = term.casefold().strip()
        if not clean:
            continue
        if re.fullmatch(r"[a-z0-9][a-z0-9 -]*", clean):
            pattern = rf"(?<![a-z0-9]){re.escape(clean)}(?![a-z0-9])"
            if re.search(pattern, lower):
                return True
        elif clean in lower:
            return True
    return False


def _score_record(
    record: PokemonDescriptionRecord,
    query_terms: tuple[str, ...],
) -> tuple[float, tuple[str, ...]]:
    source_terms = _record_terms(record)
    genus_terms = _genus_terms(record)
    feature_keys = {motif.key for motif in _motifs_for_record(record)}
    score = 0.0
    matched: list[str] = []

    for term in query_terms:
        term_score = 0.0
        if any(_term_matches(term, candidate) for candidate in genus_terms):
            term_score += 5.0
        if any(_term_matches(term, candidate) for candidate in source_terms):
            term_score += 1.5
        if term in feature_keys:
            term_score += 2.0
        if term_score:
            score += term_score
            matched.append(term)

    if not matched:
        score += min(len(record.descriptions), 12) * 0.01
    return score, tuple(dict.fromkeys(matched))


def _confidence(score: float, matched_terms: tuple[str, ...]) -> str:
    if score >= 8.0 and len(matched_terms) >= 2:
        return "high"
    if score >= 3.0:
        return "medium"
    return "low"


def select_pokemon_description_preset(
    user_description: str,
    *,
    records: tuple[PokemonDescriptionRecord, ...] | None = None,
) -> PokemonPresetSelection:
    available = records if records is not None else load_pokemon_description_presets()
    if not available:
        raise PokemonPresetDataError("No pokemon description presets available")

    query_terms = _expanded_query_terms(user_description)
    scored: list[tuple[float, tuple[str, ...], PokemonDescriptionRecord]] = []
    for record in available:
        score, matched = _score_record(record, query_terms)
        scored.append((score, matched, record))
    score, matched, record = max(
        scored,
        key=lambda item: (
            item[0],
            len(item[1]),
            -int(item[2].source_id or 999999),
            item[2].source_name,
        ),
    )
    return PokemonPresetSelection(
        record=record,
        score=round(score, 4),
        confidence=_confidence(score, matched),
        matched_terms=matched,
    )


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
        (r"\bогненного\b", "огненный"),
        (r"\bэлектрического\b", "электрический"),
        (r"\bводного\b", "водный"),
        (r"\bдракона\b", "дракон"),
        (r"\bдракончика\b", "дракончик"),
        (r"\bрыцаря\b", "рыцарь"),
        (r"\bящера\b", "ящер"),
        (r"\bчерепаху\b", "черепаха"),
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


def _motifs_for_record(record: PokemonDescriptionRecord) -> tuple[FeatureMotif, ...]:
    text = _record_text(record)
    motifs = [motif for motif in FEATURE_MOTIFS if _contains_term(text, motif.terms)]
    return tuple(motifs[:4]) if motifs else (GENERIC_MOTIF,)


def _primary_motif(record: PokemonDescriptionRecord) -> FeatureMotif:
    return _motifs_for_record(record)[0]


def _unique_strings(*values: str, limit: int = 10) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = _normalize_space(value)
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _replace_source_identity(text: str, record: PokemonDescriptionRecord) -> str:
    result = _normalize_space(text)
    for term in sorted(pokemon_source_forbidden_terms(record), key=len, reverse=True):
        clean = term.strip()
        if not clean:
            continue
        possessive_pattern = rf"(?<![A-Za-z0-9]){re.escape(clean)}[’']s(?![A-Za-z0-9])"
        result = re.sub(possessive_pattern, "the character's", result, flags=re.I)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]*", clean):
            pattern = rf"(?<![A-Za-z0-9]){re.escape(clean)}(?![A-Za-z0-9])"
        else:
            pattern = re.escape(clean)
        replacement = "the character" if clean.casefold() == record.source_name else "creature"
        result = re.sub(pattern, replacement, result, flags=re.I)
    result = re.sub(r"\b(?:this|a|an)\s+creature\b", "the character", result, flags=re.I)
    result = re.sub(r"\bcreature[’']s\b", "the character's", result, flags=re.I)
    result = re.sub(r"\bA\s+the character[’']s\b", "the character's", result)
    result = re.sub(r"\bA\s+the character\b", "The character", result)
    return _normalize_space(result)


def _adapt_description_to_target(
    description: str,
    *,
    record: PokemonDescriptionRecord,
    target_phrase: str,
) -> str:
    result = _replace_source_identity(description, record)
    target_lower = target_phrase.casefold()
    if "лист" in target_lower or "leaf" in target_lower:
        result = re.sub(
            r"\bIts bud looks like a human face\b",
            "Its leaf-like face looks like a human face",
            result,
            flags=re.I,
        )
        result = re.sub(
            r"\bBecause of the bud\b",
            "Because of that leaf-like face",
            result,
            flags=re.I,
        )
    return _normalize_space(result)


def _source_descriptions_for_character(
    record: PokemonDescriptionRecord,
    *,
    target_phrase: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _adapt_description_to_target(
                description,
                record=record,
                target_phrase=target_phrase,
            )
            for description in record.descriptions
        )
    )


def _source_description_blob(source_descriptions: tuple[str, ...]) -> str:
    return "\n".join(f"- {description}" for description in source_descriptions)


def _source_description_summary(source_descriptions: tuple[str, ...], *, limit: int = 3) -> str:
    return " ".join(source_descriptions[:limit])


def _source_drives_from_descriptions(source_descriptions: tuple[str, ...]) -> dict[str, int]:
    text = " ".join(source_descriptions).casefold()
    return {
        "attachment": 45,
        "curiosity": 70 if any(term in text for term in ("notices", "moves", "quick")) else 55,
        "confidence": 58 if any(term in text for term in ("traps", "devours", "attack")) else 45,
        "energy": 72 if any(term in text for term in ("quick", "fast", "immediately")) else 55,
        "stress": 38 if any(term in text for term in ("can't escape", "rooted", "enemy")) else 24,
        "loneliness": 15,
        "playfulness": 35 if any(term in text for term in ("prey", "devours")) else 50,
    }


def _motif_lines(motifs: tuple[FeatureMotif, ...], attr: str, *, limit: int = 4) -> list[str]:
    result: list[str] = []
    for motif in motifs:
        value = getattr(motif, attr)
        if isinstance(value, str) and value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _colors_for_motifs(motifs: tuple[FeatureMotif, ...]) -> list[str]:
    colors: list[str] = []
    for motif in motifs:
        for color in motif.colors:
            if color not in colors:
                colors.append(color)
            if len(colors) >= 4:
                return colors
    return colors or list(GENERIC_MOTIF.colors)


def _materials_for_motifs(motifs: tuple[FeatureMotif, ...]) -> list[str]:
    materials: list[str] = []
    for motif in motifs:
        for material in motif.materials:
            if material not in materials:
                materials.append(material)
            if len(materials) >= 4:
                return materials
    return materials or list(GENERIC_MOTIF.materials)


def _growth_arc(
    record: PokemonDescriptionRecord,
    source_descriptions: tuple[str, ...],
) -> dict[str, str]:
    stage_count = max(len(record.evolution_path), 1)
    source_note = (
        f"source has {stage_count} growth stage(s); communication must keep using the "
        "sanitized source descriptions, while the app-selected age controls tone only"
    )
    first_fact = source_descriptions[0] if source_descriptions else "no source description"
    second_fact = (
        source_descriptions[min(1, len(source_descriptions) - 1)]
        if source_descriptions
        else first_fact
    )
    third_fact = (
        source_descriptions[min(2, len(source_descriptions) - 1)]
        if source_descriptions
        else first_fact
    )
    return {
        "baby": f"{source_note}; baby tone may simplify this fact: {first_fact}",
        "teen": f"{source_note}; teen tone may use this fact with more agency: {second_fact}",
        "adult": f"{source_note}; adult tone may use this fact more calmly: {third_fact}",
    }


def _lorebook_entries(
    *,
    target_name: str,
    target_phrase: str,
    source_descriptions: tuple[str, ...],
    growth_arc: dict[str, str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = [
        {
            "keys": [target_name, target_phrase],
            "content": (
                f"{target_name} - это {target_phrase}; вид, имя и внешний канон задает "
                "пользовательское описание, а заготовка влияет только на характер и лор."
            ),
            "priority": 100,
            "constant": True,
            "selective": False,
        },
        {
            "keys": ["source_descriptions", "описания", "факты"],
            "content": (
                "Use these sanitized source descriptions as the hard canon for behavior, "
                "lore, habits, needs, weaknesses, and self-description. Do not replace them "
                "with abstract mood or generic personality:\n"
                f"{_source_description_blob(source_descriptions)}"
            ),
            "priority": 95,
            "constant": False,
            "selective": True,
        },
        {
            "keys": ["рост", "возраст", "взросление"],
            "content": (
                "Рост персонажа идет по абстрактной арке: "
                f"малой - {growth_arc['baby']}; подросток - {growth_arc['teen']}; "
                f"взрослый - {growth_arc['adult']}."
            ),
            "priority": 75,
            "constant": False,
            "selective": True,
        },
    ]
    for index, description in enumerate(source_descriptions[:12], start=1):
        entries.append(
            {
                "keys": ["source_fact", f"fact_{index}"],
                "content": description,
                "priority": 70 - min(index, 20),
                "constant": False,
                "selective": True,
            }
        )
    return entries


def _sample_replies(source_descriptions: tuple[str, ...]) -> list[str]:
    return list(source_descriptions[:8])


def _avoid_patterns(record: PokemonDescriptionRecord) -> list[str]:
    return [
        "Не ссылаться на внешний источник, франшизу, коллекционную карточку или исходное имя.",
        "Не говорить, что персонаж является готовым шаблоном, рескином, prompt-персонажем или AI.",
        (
            "Не копировать имена, мир, роли тренеров, специальные предметы и "
            "канонические названия из источника."
        ),
        TEMPLATE_SOURCE_AGE_RULE,
        (
            "Не утверждать буквальную стадию развития из источника; текущие стадии приложения "
            "малой, подросток и взрослый всегда сильнее."
        ),
    ]


def pokemon_source_forbidden_terms(record: PokemonDescriptionRecord) -> tuple[str, ...]:
    terms = [
        *BRAND_TERMS,
        record.display_name,
        record.source_name,
        record.source_name.replace("-", " "),
        *(record.evolution_path or ()),
        *(name.replace("-", " ") for name in record.evolution_path),
    ]
    return tuple(dict.fromkeys(term for term in terms if term and len(term.strip()) >= 3))


def _sanitize_text_for_terms(text: str, terms: tuple[str, ...]) -> str:
    result = text
    for term in sorted(terms, key=len, reverse=True):
        clean = term.strip()
        if not clean:
            continue
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]*", clean):
            pattern = rf"(?<![A-Za-z0-9]){re.escape(clean)}(?![A-Za-z0-9])"
        else:
            pattern = re.escape(clean)
        result = re.sub(pattern, "существо", result, flags=re.I)
    result = re.sub(r"\bсущество\s+существо\b", "существо", result, flags=re.I)
    return _normalize_space(result)


def _sanitize_visible_value(value: Any, terms: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _sanitize_text_for_terms(value, terms)
    if isinstance(value, list):
        return [_sanitize_visible_value(item, terms) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize_visible_value(item, terms)
            for key, item in value.items()
        }
    return value


def sanitize_pokemon_visible_bible(
    character_bible: Mapping[str, Any],
    record: PokemonDescriptionRecord,
) -> dict[str, Any]:
    terms = pokemon_source_forbidden_terms(record)
    sanitized = deepcopy(dict(character_bible))
    for key in tuple(sanitized):
        if key in {"extensions", "provenance"}:
            continue
        sanitized[key] = _sanitize_visible_value(sanitized[key], terms)
    return sanitized


def create_character_bible_from_pokemon_preset(
    user_description: str,
    *,
    records: tuple[PokemonDescriptionRecord, ...] | None = None,
) -> dict[str, Any]:
    selection = select_pokemon_description_preset(user_description, records=records)
    record = selection.record
    target_phrase = _clean_target_phrase(user_description)
    target_name = _target_name(target_phrase)
    source_descriptions = _source_descriptions_for_character(record, target_phrase=target_phrase)
    source_summary = _source_description_summary(source_descriptions, limit=3)
    growth_arc = _growth_arc(record, source_descriptions)
    lorebook_entries = _lorebook_entries(
        target_name=target_name,
        target_phrase=target_phrase,
        source_descriptions=source_descriptions,
        growth_arc=growth_arc,
    )
    sample_replies = _sample_replies(source_descriptions)
    avoid_patterns = _avoid_patterns(record)

    character_bible: dict[str, Any] = {
        "schema_version": 2,
        "identity": {
            "name": target_name,
            "nickname": "",
            "species": target_phrase,
            "role": "персонаж-компаньон из собственного мира",
            "one_liner": (
                f"{target_phrase}; behavior and lore are based on sanitized source "
                f"descriptions: {source_summary}"
            ),
        },
        "voice": {
            "voice_rules": _unique_strings(
                "Base every self-description, habit, preference, fear, and lore answer on "
                "source_descriptions.",
                "Do not replace source facts with abstract mood, generic warmth, or invented "
                "personality.",
                "Use the user's visual prompt as the body/appearance canon when a source body "
                "detail conflicts.",
                limit=8,
            ),
            "speech_rules": _unique_strings(
                "ответ должен брать конкретный факт из source_descriptions",
                "если пользователь спрашивает о характере, еде, доме, теле, страхах или "
                "привычках, отвечать через факты source_descriptions",
                "не добавлять факты, которых нет в source_descriptions или пользовательском "
                "описании",
                limit=8,
            ),
            "sentence_rhythm": "direct factual replies grounded in source_descriptions",
            "addressing_user": (
                "обращается напрямую; личные детали берет только из source_descriptions "
                "и пользовательского описания"
            ),
            "humor_style": "юмор допустим только если он следует из source_descriptions",
            "uncertainty_style": (
                "при неуверенности не выдумывает новый лор, а возвращается к ближайшему "
                "source fact"
            ),
            "catchphrases": [],
            "sample_replies": sample_replies,
            "avoid_patterns": avoid_patterns,
        },
        "inner_state": {
            "core_want": source_descriptions[0] if source_descriptions else target_phrase,
            "inner_conflict": (
                source_descriptions[1]
                if len(source_descriptions) > 1
                else source_descriptions[0]
                if source_descriptions
                else target_phrase
            ),
            "fears": [
                description
                for description in source_descriptions
                if re.search(r"\benemy|attack|prey|escape|rooted|weak|danger", description, re.I)
            ][:6],
            "comfort_actions": [
                description
                for description in source_descriptions
                if re.search(r"\bmoisture|humid|water|sun|rest|root|soak", description, re.I)
            ][:6],
            "drives": _source_drives_from_descriptions(source_descriptions),
        },
        "world": {
            "home": (
                "Source-described environment, adapted only when it conflicts with user "
                f"appearance «{target_phrase}»: {source_summary}"
            ),
            "habitat": (
                " ".join(
                    description
                    for description in source_descriptions
                    if re.search(
                        r"\bplaces|environment|humid|water|underground|sun",
                        description,
                        re.I,
                    )
                )
                or source_summary
            ),
            "objects": [],
            "routines": list(source_descriptions[:8]),
            "relationships": [
                "No relationship facts are added unless the user creates them in chat.",
            ],
            "story_seeds": list(source_descriptions[:6]),
            "lorebook_entries": lorebook_entries,
        },
        "dialogue_moves": [
            {
                "intent": "answer_preference",
                "pattern": (
                    "answer directly -> cite one exact source_description fact -> stop"
                ),
                "good_example": sample_replies[0],
                "bad_example": "мне нравится все милое, теплое и спокойное.",
            },
            {
                "intent": "why",
                "pattern": "give the closest source_description fact, not a symbolic explanation",
                "good_example": sample_replies[1] if len(sample_replies) > 1 else sample_replies[0],
                "bad_example": "так устроено мое внутреннее настроение.",
            },
            {
                "intent": "care",
                "pattern": "react using a source_description need or limitation",
                "good_example": sample_replies[2] if len(sample_replies) > 2 else sample_replies[0],
                "bad_example": "спасибо, мне стало тепло на душе.",
            },
            {
                "intent": "continue_thread",
                "pattern": "continue through the same concrete source_description domain",
                "good_example": sample_replies[3] if len(sample_replies) > 3 else sample_replies[0],
                "bad_example": "давай продолжим нашу уютную тему.",
            },
            {
                "intent": "boundary",
                "pattern": "respect the user boundary without adding new lore",
                "good_example": "понял. не добавляю новых фактов и держусь заданного описания.",
                "bad_example": "но почему ты не хочешь отвечать?",
            },
        ],
        "openings": {
            "first_message": (
                f"я {target_name}. мой внешний вид: {target_phrase}. мои факты: "
                f"{source_descriptions[0] if source_descriptions else target_phrase}"
            ),
            "alternate_greetings": [
                description for description in source_descriptions[1:3]
            ],
            "opening_scenes": [
                (
                    f"{target_name}: visible appearance is «{target_phrase}»; source facts are:\n"
                    f"{_source_description_blob(source_descriptions[:4])}"
                ),
            ],
        },
        "provenance": {
            "source": "description_preset",
            "source_urls": [SOURCE_URL],
            "license_notes": (
                "adapted from a local species-description dataset; original source identity "
                "is internal metadata and must not be exposed in character voice"
            ),
        },
        "extensions": {
            "preset_source_internal": {
                "source_id": record.source_id,
                "source_record": f"species:{record.source_id or 'unknown'}",
                "source_path": record.source_path,
                "evolution_stage_count": len(record.evolution_path) or 1,
                "selection_score": selection.score,
                "confidence": selection.confidence,
                "matched_terms": list(selection.matched_terms),
            },
            "preset_engine": {
                "kind": "species_description_preset",
                "visible_source_names_removed": True,
            },
        },
        "species": target_phrase,
        "personality": (
            "Personality is not invented separately. Use the sanitized source descriptions "
            f"as personality and behavior canon for {target_name}:\n"
            f"{_source_description_blob(source_descriptions)}"
        ),
        "signature": (
            f"Visible canon: {target_phrase}. Source-description canon:\n"
            f"{_source_description_blob(source_descriptions[:6])}"
        ),
        "dialogue_style": {
            "voice_rules": _unique_strings(
                "Use source_descriptions verbatim or near-verbatim whenever possible.",
                "Do not add abstract personality labels unless those words are in "
                "source_descriptions.",
                "Only adapt source body wording enough to match the user's visible prompt.",
            ),
            "emotional_reactions": [
                description for description in source_descriptions[:6]
            ],
            "initiative_style": (
                "initiative must come from source_descriptions only"
            ),
            "sample_replies": sample_replies[:6],
            "avoid_patterns": avoid_patterns,
        },
        "opening_scenes": [
            (
                f"{target_name}: visible appearance is «{target_phrase}»; source facts are:\n"
                f"{_source_description_blob(source_descriptions[:4])}"
            ),
        ],
        "lorebook_entries": lorebook_entries,
        "main_colors": [],
        "signature_features": [
            f"визуальная форма строго из пользовательского описания: {target_phrase}",
            "source descriptions affect behavior and lore, not image anatomy",
            "do not draw a known franchise character",
        ],
        "materials": [],
        "proportions": (
            f"пропорции, тело, костюм и силуэт соответствуют форме «{target_phrase}»; "
            "source descriptions affect behavior/lore unless their body details fit the prompt"
        ),
        "baby_design": (
            f"малой: same visible form «{target_phrase}»; simpler tone, same source facts"
        ),
        "teen_design": (
            f"подросток: same visible form «{target_phrase}»; more agency, same source facts"
        ),
        "adult_design": (
            f"взрослый: same visible form «{target_phrase}»; calmer tone, same source facts"
        ),
        "do_not_change": [
            f"форма персонажа: {target_phrase}",
            "имя и вид задает пользователь, не исходная запись",
            "preset facts are hard canon for character behavior and lore",
        ],
        "visual_constraints": {
            "source": "description_preset_visual_alignment",
            "target_form": target_phrase,
            "draw_as": (
                f"Draw the visible body, species, silhouette, costume, and sprite anatomy "
                f"as «{target_phrase}». The user prompt is the source of visual identity."
            ),
            "template_influence": (
                "Use source descriptions for behavior, lore and communication. Do not draw a "
                "known franchise character. Visible anatomy follows the user's prompt."
            ),
            "forbidden_features": [
                "known franchise character identity",
                "source-specific names or canon objects",
            ],
        },
        "growth_arc": growth_arc,
        "lore": {
            "source_descriptions": list(source_descriptions),
            "world": {
                "story": (
                    "World facts come from source_descriptions only:\n"
                    f"{_source_description_blob(source_descriptions)}"
                ),
                "environment": f"безопасная среда для формы «{target_phrase}»",
                "daily_life": list(source_descriptions[:8]),
            },
            "home": {
                "story": (
                    "Home/habitat details must be inferred only from source_descriptions:\n"
                    f"{_source_description_blob(source_descriptions[:6])}"
                ),
                "favorite_spot": "",
                "objects": [],
            },
            "origin": {
                "story": (
                    "No extra origin is invented. Use source_descriptions and the user's "
                    f"visible prompt «{target_phrase}»."
                ),
                "formative_event": source_descriptions[0] if source_descriptions else "",
            },
            "relationships": {
                "story": (
                    "No relationship lore is added by the preset. Add only what the user "
                    "establishes later."
                ),
                "attitude_to_user": "",
                "friends": [],
            },
            "inner_life": {
                "core_want": source_descriptions[0] if source_descriptions else "",
                "inner_conflict": (
                    source_descriptions[1]
                    if len(source_descriptions) > 1
                    else source_descriptions[0]
                    if source_descriptions
                    else ""
                ),
                "fears": [
                    description
                    for description in source_descriptions
                    if re.search(
                        r"\benemy|attack|prey|escape|rooted|weak|danger",
                        description,
                        re.I,
                    )
                ][:6],
                "comfort_actions": [
                    description
                    for description in source_descriptions
                    if re.search(
                        r"\bmoisture|humid|water|sun|rest|root|soak",
                        description,
                        re.I,
                    )
                ][:6],
                "habits": list(source_descriptions[:8]),
                "flaws": [],
            },
            "voice": {
                "speech_pattern": "near-verbatim source_descriptions adapted to user's appearance",
                "favorite_phrases": [],
                "avoid_saying": avoid_patterns,
            },
            "growth_arc": growth_arc,
            "story_seeds": list(source_descriptions[:8]),
        },
    }

    character_bible = sanitize_source_age_claims(character_bible)
    character_bible = upgrade_character_bible_v2(character_bible, raw_description=target_phrase)
    character_bible = sanitize_pokemon_visible_bible(character_bible, record)
    return character_bible
