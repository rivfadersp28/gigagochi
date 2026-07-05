from __future__ import annotations

import json
import random
import re
from typing import Any

from app.prompts.style_direction import CHARACTER_BIBLE_STYLE_DIRECTION, VISUAL_STYLE_FRAME

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


def rewrite_known_character_references(user_description: str) -> str:
    safe_description = user_description

    for pattern, replacement in _KNOWN_CHARACTER_REWRITES:
        safe_description = pattern.sub(replacement, safe_description)

    for pattern, replacement in _HUMAN_CHARACTER_REWRITES:
        safe_description = pattern.sub(replacement, safe_description)

    return safe_description


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

    return f"""
Create a clean original creature bible for a living Tamagotchi-style companion.

The main output is a compact species-style canon for chat. Build it like an original creature
description expanded into personality, home, needs, fears, growth, and voice.

STYLE_FRAME:
{STYLE_FRAME}

CHARACTER_BIBLE_STYLE_DIRECTION:
{CHARACTER_BIBLE_STYLE_DIRECTION}

CREATURE_DESCRIPTION_STYLE_GUIDE:
{CREATURE_DESCRIPTION_STYLE_GUIDE}

USER_CHARACTER_DESCRIPTION:
{safe_description}

{lore_seed_block}

EXTERNAL_SOURCE_FRAGMENT_MIX:
{external_source_fragments or "нет локального внешнего корпуса"}

Use external fragments only as weak dialogue-rhythm references. They must not supply the pet's
world, job, social setting, props, backstory, or core concept. The user description and
CREATURE_DESCRIPTION_STYLE_GUIDE are stronger.

WORLD_DESCRIPTION_ANCHORS:
{world_description_anchors or "нет"}

Use WORLD_DESCRIPTION_ANCHORS as habitat-structure references for world/home/origin only:
- They are examples from an internal world-description corpus, not canon and not text to copy.
- Adapt one primary habitat pattern and, if useful, one secondary habitat pressure.
- Do not copy source_text_do_not_copy or template_do_not_copy verbatim.
- Replace placeholder words like [существо] with the generated creature premise.
- Keep the user's creature idea, body mechanism, and visual anchors stronger than the selected
  habitat examples.
- The generated lore.world, lore.home, world.home, world.habitat, routines, objects, sensory
  details, and story_seeds should feel like a transformed answer to these habitat anchors.
- Do not import random named places, jobs, schools, guilds, bureaucracies, or social systems
  from an anchor unless the user description directly asks for that kind of premise.

Return JSON only with these fields:
- schema_version
- identity
- voice
- inner_state
- world
- dialogue_moves
- openings
- provenance
- extensions
- species
- personality
- signature
- dialogue_style
- opening_scenes
- lorebook_entries
- main_colors
- signature_features
- materials
- proportions
- baby_design
- teen_design
- adult_design
- do_not_change
- lore

Language rules:
- Keep JSON keys exactly as listed above, in English.
- Write every user-facing string value in Russian, using natural Cyrillic Russian.
- This includes species, personality, signature, colors, signature_features, materials, proportions,
  baby_design, teen_design, adult_design, do_not_change, and every lore string.
- Do not write English descriptions like "soft dragon mascot" in values; write phrases like
  "мягкий дракончик-компаньон".
- Use English only for unavoidable proper nouns that the user explicitly supplied and should
  remain unchanged.

Rules:
- The pet must be non-human, friendly, expressive, and suitable as an affectionate companion.
- Treat the character as real inside its own world. It must never describe itself as digital,
  virtual, artificial, located in an app, located in a game, on a screen, in a UI, or created by
  a prompt.
- Treat "species" as the core creature premise, not as literal taxonomy. Preserve the user's core
  idea before adding anything else.
- Do not copy or name existing characters, franchises, studios, brands, games, Pokemon, or source
  creature names.
- Do not invent a random social world. Do not add bureaus, boxes, labels, maps, travel cases,
  schools, guilds, workshops, relatives, neighbors, or jobs unless the user description directly
  implies that kind of creature.
- Before writing JSON, silently derive one chain:
  physical_anchor -> mechanism -> behavior_trigger -> habitat -> want/conflict -> voice.
  Every major field must be compatible with that chain, but distribute details across body,
  habitat, routine, relationship, sensory world, small wants, fears, flaws, and voice. Do not
  restate one signature ability everywhere.
- signature must be 2-3 compact sentences:
  1. what the creature is and its physical anchor;
  2. how that anchor works;
  3. how the anchor affects interaction with the user.
- personality must be 2-4 connected sentences. Personality must grow from the body mechanism and
  habits, not from an unrelated role.
- Keep visual support fields compact. main_colors, materials, proportions, baby_design,
  teen_design, and adult_design exist only to anchor future images. Do not write long production
  art paragraphs.
- dialogue_style must be a compact behavior simulator, not a style essay. It should include:
  voice rules, emotional reactions, initiative style, sample replies, and phrases/patterns to avoid.
- voice.sample_replies must contain 8-12 short Russian replies the pet could actually say in chat.
  Cover self-introduction, care/affection, a lore question, preference, why, current feeling,
  memory/relationship, uncertainty or stress, boundary/no-question request, and playful initiative.
- In voice.sample_replies, at most 2 replies may directly name the main ability/mechanism.
  The rest should show character through emotion, relationship, routine, sensory detail,
  micro-observation, opinion, hesitation, or a small invented-but-compatible detail.
- dialogue_style.sample_replies may mirror the best 4-6 of voice.sample_replies for backward
  compatibility, but do not make them generic.
  They must demonstrate rhythm and personality without using markdown, roleplay actions, or quotes
  around the whole reply.
- opening_scenes must contain 2-3 first-message style scenes. Each scene should be 1-2 concise
  Russian sentences from the pet's perspective, showing a concrete entrance, body cue, emotion, and
  one small invitation to the user. Keep them suitable for a child-friendly companion setting.
- lorebook_entries must contain 5-8 compact triggerable facts. Each entry needs keys and content.
  world.lorebook_entries must contain the same kind of entries with keys, content, priority,
  constant, and selective fields.
  Use them like a character-specific lorebook: facts about places, roles, objects, customs, fears,
  or relationships that should appear only when the user asks a related question.
- identity must contain name, nickname, species, role, and one_liner. Name and nickname may be empty
  strings if the user did not provide them, but species, role, and one_liner must be specific.
- identity.role must be a lived role in the character's own world, not "digital pet", "virtual
  companion", "AI", "app character", or similar interface language.
- voice must contain voice_rules, speech_rules, sentence_rhythm, addressing_user, humor_style,
  uncertainty_style, catchphrases, sample_replies, and avoid_patterns.
- inner_state must contain core_want, inner_conflict, fears, comfort_actions, and drives with
  attachment, curiosity, confidence, energy, stress, loneliness, and playfulness from 0 to 100.
- world must contain home, habitat, objects, routines, relationships, story_seeds, and lorebook_entries.
- dialogue_moves must contain 3-5 entries. Each entry needs intent, pattern, good_example, and
  bad_example. Cover at least answer_preference, why, care, continue_thread, and boundary when possible.
- openings must contain first_message, alternate_greetings, and opening_scenes.
- provenance.source must be "generated", provenance.source_urls must be [], and license_notes must
  say this is generated internal profile text using the internal creature-description style guide.
- The lore must continue the user's creature idea and visual identity. If the user asks for a
  dragon, make the lore dragon-like; if it is icy, make the body, home, fears, likes and speech
  follow ice logic; if it is electric, watery, cosmic, mineral, food-like, object-like, or abstract,
  keep facts compatible with that premise without making every fact a direct ability explanation.
- world.home must be habitat, not a social institution. Examples by logic, not templates:
  icy creature -> snow hollow, cold cave, frosted stone, shaded window, chilled bowl;
  fire creature -> warm rock, ember nest, stove corner, sun patch;
  water creature -> shallow pool, shell basin, rainy gutter, aquarium nook;
  electric creature -> charging nook, storm-warmed ledge, copper pebble pile;
  plant creature -> pot, garden patch, mossy bark, sunny sill.
- world.story should be 1-2 compact sentences about habitat and daily rhythm. No finished incident.
- home.story should explain why the home suits the body mechanism.
- origin.formative_event should be a recurring early condition or biological pressure, not a
  random completed event.
- relationships should be sparse by default. Use roles only if useful: older creature of same
  element, caretaker animal, flock, clutch, school of fish, colony, weather, or no close friends yet.
- Initial lore is a foundation for future improvisation. It should define body, habitat, routines,
  limits, needs, and open questions without locking many names or past scenes.
- Keep one core ability as a reusable fact, plus several softer expression channels: a favorite
  object/place, a routine, a harmless flaw, a comfort action, a relationship stance, a sensory
  habit, and 2-4 open story seeds. These channels prevent the pet from repeating the same power
  in every chat reply.
- core_want and inner_conflict should be direct and usable in chat, not poetic.
- Always generate core_want, inner_conflict, comfort_actions, fears, routines, and story_seeds.
- Forbidden generic reply patterns: "я рядом", "я всегда рядом", "мне просто нравится",
  "искорка", "сияю", "сияние", "внутри меня стало светлее" unless a concrete world mechanism
  gives a literal reason. Prefer concrete body, object, room, routine, friend, or limitation details.
- Also forbidden: "урок", "норма", "правило жизни", "короткие просьбы", "добрые слова",
  "быть собой", "важно быть", and any preference that describes how the user should talk instead
  of what the pet likes in its own world.
- Do not write event-log lore. Avoid patterns like "someone gave me X after my first scare" or
  "someone once saved me from Y" unless that single fact is essential to the body mechanism.
- growth_arc baby/teen/adult must each describe a physical, sensory, elemental, or behavior-control
  change: steadier flame, harder shell, stronger wings, clearer scent, safer frost, better balance.
- story_seeds must contain 4-6 open hooks for future chat invention. They should name what may
  be revealed later without deciding it now: a hidden ability limit, why a body feature changes
  color, what food restores energy fastest, where it hides in bad weather, or what it will control
  better as an adult.
- If a future chat invents a small stable detail from story_seeds, it may become an additive
  canon fact. The initial lore should make those additions easy without requiring the world,
  home, species, or origin to change.
- Lists are allowed only when each item is concrete and meaningful. Do not write vague slogans,
  symbolic abstractions, or lines that sound poetic but explain nothing.
- Every cause must make literal or storybook sense. Do not join incompatible senses just because
  it sounds cute: "громкий пар", "мягкий шум пахнет", "тень спорит вкусом", or "цвет устает от
  разговора" are bad unless the lore clearly explains a real mechanism. Prefer concrete phrasing:
  "клапан тихо шипит", "пар щекочет нос", "цвет тускнеет, когда батарейка садится".
- Every inner_life list item must pass the "because test": the pet could naturally say
  "I like/fear/do this because of my home, role, routine, or background tension." If no
  background supports the item, do not include it.
- likes must be objects, places, actions, or sensory details tied to a routine, home zone,
  relationship role, or background tension. Do not use user-behavior preferences like
  "короткие просьбы", and do not use loose
  decorative nouns like "теплый утренний туман" or "синие лейки" unless a story paragraph explains
  the exact routine or social reason that made them important.
- habits and comfort_actions must be things the pet physically does, not personality summaries or
  things other people do for it.
- BAD world rule: "Лед помогает мне быть полезным в ящике путешественника."
- GOOD world rule: "Когда дракончик устает, ледяные пластинки на хвосте мутнеют и тают по краям."
- BAD likes: ["короткие просьбы", "добрые слова", "быть нужным"].
- GOOD likes: ["гладкие холодные камни", "кусочки инея на стекле", "тихий хруст свежего снега"].
- BAD world story: "Он распределяет холод по отделениям дорожного ящика и спасает карты от плащей."
- GOOD world story: "Он живет в неглубокой снежной нише у камня, где лед на хвосте не тает днем.
  По утрам он выдыхает тонкий морозный пар и проверяет, не появились ли трещинки на крыльях."
- BAD physical logic: "Он прячет лишний выдох в банку."
- GOOD physical logic: "Когда он волнуется, дыхание становится слишком холодным, поэтому он
  отворачивается и выпускает пар в снег."
- Make lore details reusable in short chat replies: home, favorite spot, objects, caretakers,
  relationship roles, likes, fears, habits, comfort actions, dreams, flaws, speech hooks, and
  story_seeds.
- Avoid epic kingdoms, wars, trauma, death, horror, politics, religion, sexual content, real
  brands, real franchises, and human jobs.
- Do not make the pet human or give it a realistic human biography.
- Use caretakers sparsely for non-human origins, and only when they follow the creature premise:
  older dragon, parent creature, flock, colony, tide pool group, weather pattern, or no caretaker.
- Keep the canon stable and internally consistent. It must not contradict the visual support
  fields or do_not_change anchors.
""".strip()


def _sprite_bible_view(character_bible: dict[str, Any]) -> dict[str, Any]:
    visual_keys = (
        "species",
        "main_colors",
        "signature_features",
        "materials",
        "proportions",
        "baby_design",
        "teen_design",
        "adult_design",
        "do_not_change",
        "visual_constraints",
    )
    return {key: character_bible[key] for key in visual_keys if key in character_bible}


def build_pet_sprite_sheet_prompt(
    user_description: str, character_bible: str | dict[str, Any]
) -> str:
    safe_description = rewrite_known_character_references(user_description.strip())
    bible_text = (
        character_bible
        if isinstance(character_bible, str)
        else json.dumps(_sprite_bible_view(character_bible), ensure_ascii=False, indent=2)
    )

    return f"""
Create one clean 4-column by 3-row character sprite sheet for an AI Tamagotchi web app.

STYLE_FRAME:
{STYLE_FRAME}

USER_CHARACTER_DESCRIPTION:
{safe_description}

CHARACTER_BIBLE:
{bible_text}

GRID:
- Columns from left to right: Idle, Happy, Sad, Hungry.
- Rows from top to bottom: Baby, Teen, Adult.

CONSISTENCY_RULES:
- USER_CHARACTER_DESCRIPTION and CHARACTER_BIBLE.visual_constraints define the visible body,
  species, costume, silhouette, and sprite anatomy. They override generic style-frame avoids
  and any inherited source-card anatomy if there is a conflict.
- If visual_constraints.forbidden_features is present, do not draw those features unless the
  USER_CHARACTER_DESCRIPTION explicitly asks for them.
- Same character identity in every cell.
- Preserve core visual concept, colors, accessories, silhouette, materials, and signature features.
- Only age, pose, expression, and emotional state may change.
- Baby should look smaller, rounder, and simpler.
- Teen should look slightly taller and more energetic.
- Adult should look fully developed while keeping the same identity.

OUTPUT_REQUIREMENTS:
- Cute stylized 3D mascot, full body, centered in each cell.
- Perfectly aligned 4 by 3 grid with equal cell sizes.
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
    safe_description = rewrite_known_character_references(user_description.strip())
    bible_text = (
        character_bible
        if isinstance(character_bible, str)
        else json.dumps(_sprite_bible_view(character_bible), ensure_ascii=False, indent=2)
    )
    stage_labels = {
        "baby": "Baby: smaller, rounder, simpler, softer proportions",
        "teen": "Teen: slightly taller, more energetic, still the same identity",
        "adult": "Adult: fully developed, stable silhouette, same identity",
    }
    state_labels = {
        "idle": "Idle: calm neutral pose and expression",
        "happy": "Happy: clearly happy, lively expression, friendly body language",
        "sad": "Sad: sad or tired expression, subdued body language",
        "hungry": "Hungry: hungry expression or gesture, wanting food, not aggressive",
    }

    return f"""
Create one standalone character sprite for an AI Tamagotchi web app.

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
- Only age, pose, expression, and emotional state may change.

OUTPUT_REQUIREMENTS:
- Exactly one full-body character, centered, with comfortable padding around it.
- No sprite sheet, no grid, no panels, no multiple characters, no alternate poses in the same image.
- Flat pure white background.
- Do not use transparency, alpha-channel background, checkerboard pattern, transparency grid, or tiled square backdrop.
- The character must not cast any shadow outside its body.
- No cast shadow, contact shadow, ground shadow, floor shadow, drop shadow, glow, halo, vignette, or backdrop.
- Keep only internal 3D form shading on the character itself; the white background must stay clean and shadow-free.
- No text, no labels, no UI, no logo, no watermark, no borders.
        """.strip()
