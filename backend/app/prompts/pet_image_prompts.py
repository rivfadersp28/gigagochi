from __future__ import annotations

import json
import random
import re
from typing import Any

from app.prompts.style_direction import VISUAL_STYLE_FRAME
from app.services.character_bible_template import character_bible_prompt_config

PROMPT_MAX_LENGTH = 300

LORE_SEED_OPTIONS: dict[str, tuple[str, ...]] = {
    "body_mechanism": (
        "заметная часть тела хранит энергию и меняется от состояния",
        "защитная оболочка влияет на движение и поведение",
        "хвост, рога, крылья или плавник показывают настроение",
        "поверхность тела реагирует на погоду, свет или прикосновение",
        "маленький орган чувств помогает заранее замечать опасность",
        "внутренний запас элемента расходуется и восстанавливается",
    ),
    "behavior_trigger": (
        "при радости признак становится ярче или активнее",
        "при страхе creature прячется, сжимается или выпускает защитный эффект",
        "при усталости элемент тускнеет, остывает или затихает",
        "при голоде creature ищет конкретный природный источник энергии",
        "в дождь, жару, холод или темноту способность проявляется иначе",
    ),
    "habitat_pressure": (
        "домом служит простое место, где удобно поддерживать главный элемент",
        "среда дает энергию, но иногда создает бытовые трудности",
        "creature выбирает укрытия, которые подходят его форме тела",
        "рядом есть один-два природных объекта, важные для привычек",
        "опасность связана с противоположным элементом или потерей запаса энергии",
    ),
    "growth_clue": (
        "baby учится управлять признаком, teen проверяет его силу, adult использует уверенно",
        "по мере роста меняется размер, цвет, звук или устойчивость главного признака",
        "growth arc должен быть биологическим или элементальным, а не социальной карьерой",
        "каждая стадия добавляет одну простую способность или более точный контроль",
        "future reveal касается свойства тела, привычки, habitat или element limit",
    ),
}

STYLE_FRAME = VISUAL_STYLE_FRAME

_KNOWN_CHARACTER_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bpikachu\b", re.IGNORECASE),
        "a small yellow fantasy animal with lively electric energy",
    ),
    (re.compile(r"\bpokemon\b", re.IGNORECASE), "a collectible fantasy pet creature"),
    (
        re.compile(r"\bmario\b", re.IGNORECASE),
        "a cheerful round mascot with playful adventure energy",
    ),
    (
        re.compile(r"\bsonic\b", re.IGNORECASE),
        "a fast blue fantasy animal with spiky silhouette cues",
    ),
    (re.compile(r"\bstitch\b", re.IGNORECASE), "a small blue alien-like pet with oversized ears"),
    (re.compile(r"\btotoro\b", re.IGNORECASE), "a large gentle forest spirit-like fantasy animal"),
    (re.compile(r"\bmickey\b", re.IGNORECASE), "a classic black-eared cartoon animal silhouette"),
    (re.compile(r"\bminnie\b", re.IGNORECASE), "a classic round-eared cartoon animal silhouette"),
    (
        re.compile(r"\bspongebob\b", re.IGNORECASE),
        "a bright square-shaped sea-inspired cartoon creature",
    ),
    (
        re.compile(r"пикачу", re.IGNORECASE),
        "маленькое желтое фантазийное животное с электрической энергией",
    ),
    (re.compile(r"покемон", re.IGNORECASE), "коллекционное фантазийное животное-компаньон"),
    (re.compile(r"микки", re.IGNORECASE), "классический мультяшный зверек с круглыми ушами"),
    (re.compile(r"стич", re.IGNORECASE), "маленький синий инопланетный питомец с большими ушами"),
    (re.compile(r"тоторо", re.IGNORECASE), "большой мягкий лесной фантазийный зверь"),
)

_HUMAN_CHARACTER_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\banime[- ]?chibi girl\b", re.IGNORECASE),
        "anime-inspired chibi non-human mascot creature",
    ),
    (
        re.compile(r"\bchibi girl\b", re.IGNORECASE),
        "chibi non-human mascot creature",
    ),
    (re.compile(r"\bgirl\b", re.IGNORECASE), "non-human companion creature"),
    (re.compile(r"\bboy\b", re.IGNORECASE), "non-human companion creature"),
    (re.compile(r"\bhuman\b", re.IGNORECASE), "non-human mascot creature"),
    (
        re.compile(r"аниме[- ]?чиби девочк[а-я]*", re.IGNORECASE),
        "аниме-вдохновленное чиби фантазийное существо",
    ),
    (
        re.compile(r"чиби девочк[а-я]*", re.IGNORECASE),
        "чиби фантазийное существо-компаньон",
    ),
    (
        re.compile(r"девочк[а-я]*", re.IGNORECASE),
        "нечеловеческое милое существо-компаньон",
    ),
    (
        re.compile(r"девушк[а-я]*", re.IGNORECASE),
        "нечеловеческое милое существо-компаньон",
    ),
    (
        re.compile(r"мальчик[а-я]*", re.IGNORECASE),
        "нечеловеческое милое существо-компаньон",
    ),
    (
        re.compile(r"парень|парня|парню|парнем", re.IGNORECASE),
        "нечеловеческое милое существо-компаньон",
    ),
    (
        re.compile(r"человек[а-я]*", re.IGNORECASE),
        "нечеловеческое маскот-существо",
    ),
    (
        re.compile(r"челик[а-я]*", re.IGNORECASE),
        "нечеловеческое маскот-существо",
    ),
)


_IMAGE_PROMPT_TEXT_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bteen(?:age|ager|s)?\b", re.IGNORECASE), "middle growth form"),
    (re.compile(r"\bbaby\b", re.IGNORECASE), "small growth form"),
    (re.compile(r"\byoung\b", re.IGNORECASE), "small"),
    (re.compile(r"\badult\b", re.IGNORECASE), "mature growth form"),
    (re.compile(r"подрост[а-яё]*", re.IGNORECASE), "средняя форма"),
    (re.compile(r"малыш[а-яё]*", re.IGNORECASE), "маленькая форма"),
    (re.compile(r"детеныш[а-яё]*|детёныш[а-яё]*", re.IGNORECASE), "маленькая форма"),
    (re.compile(r"детск[а-яё]*", re.IGNORECASE), "маленькая форма"),
    (re.compile(r"взросл[а-яё]*", re.IGNORECASE), "зрелая форма"),
    (re.compile(r"\bweapons?\b", re.IGNORECASE), "extra props"),
    (re.compile(r"\barmo[u]?r\b", re.IGNORECASE), "heavy accessories"),
    (re.compile(r"\bfork(?:ed)? tail\b", re.IGNORECASE), "tail with a rounded split tip"),
    (re.compile(r"\blightning horns?\b", re.IGNORECASE), "soft zigzag antenna-like horns"),
    (re.compile(r"\belectric arcs?\b", re.IGNORECASE), "yellow decorative markings"),
    (re.compile(r"оружи[а-яё]*", re.IGNORECASE), "лишние предметы"),
    (re.compile(r"брон[а-яё]*", re.IGNORECASE), "тяжёлые аксессуары"),
    (re.compile(r"рог[а-яё-]*-молни[а-яё]*", re.IGNORECASE), "мягкие зигзагообразные антенны"),
    (re.compile(r"\bрогами\b", re.IGNORECASE), "мягкими антеннами"),
    (re.compile(r"\bрогах\b", re.IGNORECASE), "мягких антеннах"),
    (re.compile(r"\bрогов\b", re.IGNORECASE), "мягких антенн"),
    (re.compile(r"\bрога\b", re.IGNORECASE), "мягкие антенны"),
    (re.compile(r"\bрог[а-яё-]*\b", re.IGNORECASE), "мягкие антенны"),
    (re.compile(r"молни[а-яё]*", re.IGNORECASE), "жёлтые зигзаг-акценты"),
    (re.compile(r"хвост[а-яё -]*вилк[а-яё]*", re.IGNORECASE), "хвост с округлым раздвоенным кончиком"),
    (re.compile(r"заземл[а-яё]*", re.IGNORECASE), "устойчивости"),
    (re.compile(r"заряд[а-яё]*", re.IGNORECASE), "энергетический акцент"),
    (re.compile(r"гроз[а-яё]*", re.IGNORECASE), "тёплый каменный"),
    (re.compile(r"остры[а-яё]*", re.IGNORECASE), "выразительные"),
)

_IMAGE_RETRY_TEXT_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\belectric dragon\b", re.IGNORECASE),
        "small rounded fantasy reptile mascot with yellow energy-themed accents",
    ),
    (re.compile(r"\bdragon\b", re.IGNORECASE), "rounded fantasy reptile mascot"),
    (re.compile(r"\belectric\b", re.IGNORECASE), "yellow energy-themed"),
    (
        re.compile(r"электрическ[а-яё]* дракон[а-яё]*", re.IGNORECASE),
        "маленькое округлое фантазийное ящероподобное существо с жёлтыми акцентами",
    ),
    (
        re.compile(r"дракон[а-яё]*", re.IGNORECASE),
        "округлое фантазийное ящероподобное существо",
    ),
    (
        re.compile(r"электрическ[а-яё]*", re.IGNORECASE),
        "с жёлтыми энергетическими акцентами",
    ),
)


def rewrite_known_character_references(user_description: str) -> str:
    safe_description = user_description

    for pattern, replacement in _KNOWN_CHARACTER_REWRITES:
        safe_description = pattern.sub(replacement, safe_description)

    for pattern, replacement in _HUMAN_CHARACTER_REWRITES:
        safe_description = pattern.sub(replacement, safe_description)

    return safe_description


def _sanitize_image_prompt_text(value: str) -> str:
    safe_value = value
    for pattern, replacement in _IMAGE_PROMPT_TEXT_REWRITES:
        safe_value = pattern.sub(replacement, safe_value)
    return re.sub(r"[ \t]{2,}", " ", safe_value).strip()


def _sanitize_retry_image_prompt_text(value: str) -> str:
    safe_value = _sanitize_image_prompt_text(value)
    for pattern, replacement in _IMAGE_RETRY_TEXT_REWRITES:
        safe_value = pattern.sub(replacement, safe_value)
    return re.sub(r"[ \t]{2,}", " ", safe_value).strip()


def _sanitize_image_prompt_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_image_prompt_text(value)
    if isinstance(value, list):
        return [_sanitize_image_prompt_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_image_prompt_value(item) for key, item in value.items()}
    return value


def _sanitize_retry_image_prompt_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_retry_image_prompt_text(value)
    if isinstance(value, list):
        return [_sanitize_retry_image_prompt_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_retry_image_prompt_value(item) for key, item in value.items()}
    return value


def create_lore_seed(rng: random.Random | None = None) -> dict[str, str]:
    chooser = rng or random.SystemRandom()
    return {key: chooser.choice(values) for key, values in LORE_SEED_OPTIONS.items()}


def _lore_seed_block(lore_seed: dict[str, str] | None) -> str:
    if not lore_seed:
        return ""
    ordered_keys = ("body_mechanism", "behavior_trigger", "habitat_pressure", "growth_clue")
    lines = [f"- {key}: {lore_seed[key]}" for key in ordered_keys if lore_seed.get(key)]
    if not lines:
        return ""
    return """
LORE_VARIATION_SEED:
Use this private seed only as a lens for the creature-description logic. It is not user-visible
text. It may shape body mechanism, behavior triggers, habitat pressure, and growth clues without
adding random settings, jobs, object societies, or proper-name lore. Do not copy it verbatim.
{lines}
""".strip().format(lines="\n".join(lines))


CREATURE_DESCRIPTION_STYLE_GUIDE = """
Internal style guide distilled from the local creature-description corpus.

Write the clean-generated character like an original species entry expanded into chat canon:
- Start from one physical anchor: seed, flame, shell, horn, fin, fur, crystal, mist, wing, tail,
  pouch, antenna, scale, bulb, core, leaf, ember, frost plate, cloud tuft, or another feature
  implied by the user's description.
- Give that anchor a practical function: stores energy, shows health, senses danger, protects,
  balances movement, releases scent, changes color, cools, warms, sheds, absorbs light, gathers
  water, conducts sparks, hardens, softens, or helps it hide.
- Tie emotion and needs to observable changes in the body. Good pattern: "when happy, X glows";
  "when tired, X droops"; "when scared, it withdraws into Y"; "when hungry, it seeks Z".
- The physical anchor is a foundation, not a verbal tic. Do not make the creature mention the
  same ability, field, glow, charge, flame, frost, shell, or body mechanism in most replies.
  Character can also show through relationship, opinions, routines, sensory noticing, small
  choices, hesitation, jokes, care, and tiny discoveries.
- Use habitat as ecology, not plot. Habitat is where this body makes sense: warm stones, shallow
  pools, cold caves, storm nests, berry roots, moonlit ledges, pantry corners, snowy hollows,
  quiet reeds, charging nooks. Avoid arbitrary towns, guilds, offices, drawers, labels, travel
  cases, maps, or jobs unless the user explicitly asked for an object/social premise.
- Keep every fact short, reusable, and sensory. The user should be able to ask "why?", "where?",
  "what do you eat?", "what are you afraid of?", and get answers grounded in the same mechanism.
- Growth is biological or elemental: the feature gets larger, brighter, steadier, heavier,
  sharper, calmer, more accurate, or easier to control. Do not turn growth into a career,
  bureaucracy, school rank, or completed past incident.
- Prefer 2-3 compact factual sentences over decorative paragraphs. No event logs. No proper-name
  cast by default. No moral lesson. No "I am useful in a drawer" unless the user asked for drawer
  creature.
- Do not copy or name real Pokemon, franchises, species names, or source descriptions. Use the
  structural style only.

For each generated pet, produce this chain before filling the JSON:
user idea -> physical anchor -> mechanism -> behavior trigger -> habitat -> want/conflict -> voice.
Every section of the JSON must stay compatible with that chain, but do not repeat the same
mechanism as the explanation for everything.
""".strip()


def build_character_bible_prompt(
    user_description: str,
    lore_seed: dict[str, str] | None = None,
    external_source_fragments: str | None = None,
    world_description_anchors: str | None = None,
) -> str:
    safe_description = rewrite_known_character_references(user_description.strip())
    lore_seed_block = _lore_seed_block(lore_seed)
    template = character_bible_prompt_config()
    persona_shape = "\n".join(f"- {item}" for item in template["personaShape"])
    top_level_fields = "\n".join(f"- {item}" for item in template["topLevelFields"])
    language_rules = "\n".join(f"- {item}" for item in template["languageRules"])
    rules = "\n".join(f"- {item}" for item in template["rules"])

    return f"""
{template["intro"]}

Use a tiny persona-file shape inspired by small AI pet projects:
{persona_shape}

USER_CHARACTER_DESCRIPTION:
{safe_description}

{lore_seed_block}

WORLD_DESCRIPTION_ANCHORS:
{world_description_anchors or "нет"}

{template["worldAnchorsRule"]}

Return JSON only with these top-level fields:
{top_level_fields}

Language rules:
{language_rules}

Rules:
{rules}
""".strip()



def _sprite_bible_view(
    character_bible: dict[str, Any],
    *,
    active_stage: str | None = None,
) -> dict[str, Any]:
    visual_keys = (
        "species",
        "main_colors",
        "signature_features",
        "materials",
        "proportions",
        "baby_design",
        "teen_design",
        "adult_design",
        "visual_constraints",
    )
    key_aliases = {
        "baby_design": "small_growth_form_design",
        "teen_design": "middle_growth_form_design",
        "adult_design": "mature_growth_form_design",
    }
    active_design_key = {
        "baby": "baby_design",
        "teen": "teen_design",
        "adult": "adult_design",
    }.get(active_stage or "")

    if active_design_key:
        return {
            (
                "active_growth_form_design"
                if key == active_design_key
                else key_aliases.get(key, key)
            ): character_bible[key]
            for key in visual_keys
            if key in character_bible
            and (
                key not in key_aliases
                or key == active_design_key
            )
        }

    return {
        key_aliases.get(key, key): character_bible[key]
        for key in visual_keys
        if key in character_bible
    }


def _sprite_bible_text(
    character_bible: str | dict[str, Any],
    *,
    active_stage: str | None = None,
) -> str:
    if isinstance(character_bible, str):
        return _sanitize_image_prompt_text(character_bible)
    return json.dumps(
        _sanitize_image_prompt_value(
            _sprite_bible_view(character_bible, active_stage=active_stage)
        ),
        ensure_ascii=False,
        indent=2,
    )


def _sprite_bible_retry_text(
    character_bible: str | dict[str, Any],
    *,
    active_stage: str | None = None,
) -> str:
    if isinstance(character_bible, str):
        return _sanitize_retry_image_prompt_text(character_bible)
    return json.dumps(
        _sanitize_retry_image_prompt_value(
            _sprite_bible_view(character_bible, active_stage=active_stage)
        ),
        ensure_ascii=False,
        indent=2,
    )


def build_pet_sprite_sheet_prompt(
    user_description: str, character_bible: str | dict[str, Any]
) -> str:
    safe_description = _sanitize_image_prompt_text(
        rewrite_known_character_references(user_description.strip())
    )
    bible_text = _sprite_bible_text(character_bible)

    return f"""
Create one clean 4-column by 3-row character sprite sheet for a family-friendly virtual pet web app.

STYLE_FRAME:
{STYLE_FRAME}

USER_CHARACTER_DESCRIPTION:
{safe_description}

CHARACTER_BIBLE:
{bible_text}

GRID:
- Columns from left to right: Idle, Happy, Sad, Hungry.
- Rows from top to bottom: Small growth form, Middle growth form, Mature growth form.

CONSISTENCY_RULES:
- USER_CHARACTER_DESCRIPTION and CHARACTER_BIBLE.visual_constraints define the visible body,
  species, costume, silhouette, and sprite anatomy. They override generic style-frame avoids
  and any inherited source-card anatomy if there is a conflict.
- If visual_constraints.forbidden_features is present, do not draw those features unless the
  USER_CHARACTER_DESCRIPTION explicitly asks for them.
- Same character identity in every cell.
- Preserve core visual concept, colors, accessories, silhouette, materials, and signature features.
- Only growth form, pose, expression, and emotional state may change.
- Small growth form should look smaller, rounder, and simpler.
- Middle growth form should look slightly taller and more energetic.
- Mature growth form should look fully developed while keeping the same identity.

OUTPUT_REQUIREMENTS:
- Cute stylized 3D mascot, full body, centered in each cell.
- Perfectly aligned 4 by 3 grid with equal cell sizes.
- Each cell must be a square app viewport composition: the complete character fits comfortably
  inside the square cell with visible padding on all sides.
- Support very round, very tall, very wide, tailed, eared, winged, or asymmetric silhouettes
  without cropping any body part, accessory, ear, horn, tail, wing, or shadowless extremity.
- Keep the character visually centered and grounded inside the square viewport.
- Flat pure white background across the entire sprite sheet and every cell.
- Do not use transparency, alpha-channel background, checkerboard pattern, transparency grid, or tiled square backdrop.
- The character must not cast any shadow outside its body.
- No cast shadow, contact shadow, ground shadow, floor shadow, drop shadow, glow, halo, vignette, or backdrop.
- Keep only internal 3D form shading on the character itself; the white background must stay clean and shadow-free.
- No text, no labels, no UI, no logo, no watermark, no borders.
- Keep clear padding inside each cell so every character can be cropped safely.
		""".strip()


def build_pet_single_sprite_prompt(
    user_description: str,
    character_bible: str | dict[str, Any],
    *,
    stage: str,
    state: str,
) -> str:
    safe_description = _sanitize_image_prompt_text(
        rewrite_known_character_references(user_description.strip())
    )
    bible_text = _sprite_bible_text(character_bible, active_stage=stage)
    stage_labels = {
        "baby": "Small growth form: smaller, rounder, simpler, softer proportions",
        "teen": "Middle growth form: slightly taller, more energetic, same creature identity",
        "adult": "Mature growth form: fully developed, stable silhouette, same identity",
    }
    state_labels = {
        "idle": "Idle: calm neutral pose and expression",
        "happy": "Happy: clearly happy, lively expression, friendly body language",
        "sad": "Sad: sad or tired expression, subdued body language",
        "hungry": "Hungry: hungry expression or gesture, wanting food, not aggressive",
    }

    return f"""
Create one standalone character sprite for a family-friendly virtual pet web app.

STYLE_FRAME:
{STYLE_FRAME}

USER_CHARACTER_DESCRIPTION:
{safe_description}

CHARACTER_BIBLE:
{bible_text}

VARIANT:
- Stage: {stage_labels.get(stage, stage)}
- State: {state_labels.get(state, state)}

CONSISTENCY_RULES:
- USER_CHARACTER_DESCRIPTION and CHARACTER_BIBLE.visual_constraints define the visible body,
  species, costume, silhouette, and sprite anatomy. They override generic style-frame avoids
  and any inherited source-card anatomy if there is a conflict.
- If visual_constraints.forbidden_features is present, do not draw those features unless the
  USER_CHARACTER_DESCRIPTION explicitly asks for them.
- Preserve core visual concept, colors, accessories, silhouette, materials, and signature features.
- Only growth form, pose, expression, and emotional state may change.

OUTPUT_REQUIREMENTS:
- Exactly one full-body character, centered, with comfortable padding around it.
- No sprite sheet, no grid, no panels, no multiple characters, no alternate poses in the same image.
- Square app viewport composition: the complete character fits comfortably inside the square image
  with visible padding on all sides.
- Support very round, very tall, very wide, tailed, eared, winged, or asymmetric silhouettes
  without cropping any body part, accessory, ear, horn, tail, wing, or shadowless extremity.
- Keep the character visually centered and grounded inside the square viewport.
- Flat pure white background.
- Do not use transparency, alpha-channel background, checkerboard pattern, transparency grid, or tiled square backdrop.
- The character must not cast any shadow outside its body.
- No cast shadow, contact shadow, ground shadow, floor shadow, drop shadow, glow, halo, vignette, or backdrop.
- Keep only internal 3D form shading on the character itself; the white background must stay clean and shadow-free.
- No text, no labels, no UI, no logo, no watermark, no borders.
        """.strip()


def build_pet_state_strip_prompt(
    user_description: str,
    character_bible: str | dict[str, Any],
    *,
    stage: str = "teen",
) -> str:
    safe_description = _sanitize_image_prompt_text(
        rewrite_known_character_references(user_description.strip())
    )
    bible_text = _sprite_bible_text(character_bible, active_stage=stage)
    stage_labels = {
        "baby": "Small growth form: smaller, rounder, simpler, softer proportions",
        "teen": "Middle growth form: slightly taller, more energetic, same creature identity",
        "adult": "Mature growth form: fully developed, stable silhouette, same identity",
    }

    return f"""
Create one horizontal 3-column character sprite strip for a family-friendly virtual pet web app.

STYLE_FRAME:
{STYLE_FRAME}

USER_CHARACTER_DESCRIPTION:
{safe_description}

CHARACTER_BIBLE:
{bible_text}

GRID:
- Exactly one row and three equal columns.
- Columns from left to right: Idle, Happy, Sad.
- All three cells show the same character and the same growth form.
- Growth form: {stage_labels.get(stage, stage)}

CONSISTENCY_RULES:
- USER_CHARACTER_DESCRIPTION and CHARACTER_BIBLE.visual_constraints define the visible body,
  species, costume, silhouette, and sprite anatomy. They override generic style-frame avoids
  and any inherited source-card anatomy if there is a conflict.
- If visual_constraints.forbidden_features is present, do not draw those features unless the
  USER_CHARACTER_DESCRIPTION explicitly asks for them.
- Preserve core visual concept, colors, accessories, silhouette, materials, and signature features.
- Only pose, expression, and emotional state may change between columns.

STATE_RULES:
- Idle: calm neutral pose and expression.
- Happy: clearly happy, lively expression, friendly body language.
- Sad: sad expression, subdued body language.

OUTPUT_REQUIREMENTS:
- Cute stylized 3D mascot, full body, centered in each cell.
- Perfectly aligned 1 by 3 horizontal grid with equal cell sizes.
- Each cell must be a square app viewport composition: the complete character fits comfortably
  inside the square cell with visible padding on all sides.
- Support very round, very tall, very wide, tailed, eared, winged, or asymmetric silhouettes
  without cropping any body part, accessory, ear, horn, tail, wing, or shadowless extremity.
- Keep the character visually centered and grounded inside the square viewport.
- Flat pure white background across the entire strip and every cell.
- Do not use transparency, alpha-channel background, checkerboard pattern, transparency grid, or tiled square backdrop.
- The character must not cast any shadow outside its body.
- No cast shadow, contact shadow, ground shadow, floor shadow, drop shadow, glow, halo, vignette, or backdrop.
- Keep only internal 3D form shading on the character itself; the white background must stay clean and shadow-free.
- No text, no labels, no UI, no logo, no watermark, no borders.
- Keep clear padding inside each cell so every character can be cropped safely.
        """.strip()


def build_pet_state_strip_safety_retry_prompt(
    user_description: str,
    character_bible: str | dict[str, Any],
    *,
    stage: str = "teen",
) -> str:
    safe_description = _sanitize_retry_image_prompt_text(
        rewrite_known_character_references(user_description.strip())
    )
    bible_text = _sprite_bible_retry_text(character_bible, active_stage=stage)
    stage_labels = {
        "baby": "small rounded form",
        "teen": "middle rounded form, a little taller but still simple",
        "adult": "mature rounded form with the same friendly toy identity",
    }

    return f"""
Create one horizontal 3-column sprite strip of a harmless rounded collectible toy mascot.

CORE_CONCEPT:
{safe_description}

STYLE_FRAME:
{STYLE_FRAME}

VISUAL_ANCHORS:
{bible_text}

GRID:
- Exactly one row and three equal square cells.
- Left cell: calm neutral pose.
- Middle cell: happy friendly pose.
- Right cell: mildly sad or sleepy pose.
- Same character in every cell.
- Growth form: {stage_labels.get(stage, stage)}

VISUAL_RULES:
- Rounded stylized 3D vinyl-toy mascot, full body, centered in each cell.
- Soft simple shapes, oversized head or body, tiny limbs, large clean color areas.
- All tips and decorative details are rounded and toy-like.
- Energy or element cues appear only as small yellow markings, material accents, or gentle color
  details on the character itself.
- Friendly cute expressions only; no scary creature styling, no action scene, no external effects.
- Preserve the core colors, silhouette, tiny wings if present, rounded split tail tip if present,
  and soft zigzag antenna-like horns if present.

OUTPUT_REQUIREMENTS:
- Perfectly aligned 1 by 3 horizontal grid with equal cell sizes.
- Complete character fits comfortably inside each square cell with visible padding on all sides.
- Flat pure white background across the entire strip and every cell.
- No text, labels, UI, logo, watermark, border, shadow, glow, halo, background scene, or props
  beyond one tiny harmless decorative object if it is part of the visual anchors.
        """.strip()
