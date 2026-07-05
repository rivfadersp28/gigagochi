from __future__ import annotations

import json
import random
import re
from typing import Any

from app.prompts.style_direction import CHARACTER_BIBLE_STYLE_DIRECTION, VISUAL_STYLE_FRAME

PROMPT_MAX_LENGTH = 300

LORE_SEED_OPTIONS: dict[str, tuple[str, ...]] = {
    "setting_tone": (
        "маленькое ремесленное место",
        "бюро находок под шумной лестницей",
        "тихая станция на краю маршрута",
        "кладовая с подписанными ящиками",
        "подземная школа для маленьких существ",
        "крыша с погодными постами",
        "ночная пекарня с дежурными полками",
        "ящик путешественника с вещами из разных мест",
        "тихий причал с маленькими делами",
        "кристальная комната для ремонта трещинок",
    ),
    "social_shape": (
        "есть один потенциальный друг и строгий наставник",
        "вокруг шумные соседи, которые помогают и мешают",
        "есть старший родственник и младший приятель",
        "рядом соперник и заботливый хранитель",
        "питомец входит в маленькую команду помощников",
    ),
    "background_tension": (
        "питомец хочет быть полезным, но боится ошибиться",
        "питомец хочет доказать самостоятельность",
        "питомец прячет редкий звук, знак или предмет",
        "питомец не любит резкие перемены",
        "питомец ищет свое место среди более опытных соседей",
    ),
    "future_reveal": (
        "позже можно раскрыть прозвище друга",
        "позже можно раскрыть местную традицию",
        "позже можно раскрыть любимый предмет",
        "позже можно раскрыть старый спор",
        "позже можно раскрыть скрытый уголок дома",
        "позже можно раскрыть точную роль родственника",
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
    ordered_keys = ("setting_tone", "social_shape", "background_tension", "future_reveal")
    lines = [f"- {key}: {lore_seed[key]}" for key in ordered_keys if lore_seed.get(key)]
    if not lines:
        return ""
    return """
LORE_VARIATION_SEED:
Use this private seed to diversify the generated lore. It is not user-visible text and should
shape the setting, social roles, background tension, and open hooks without overriding the user's
character idea. Do not copy it verbatim if a more specific version fits the creature better.
{lines}
""".strip().format(lines="\n".join(lines))


def build_character_bible_prompt(
    user_description: str,
    lore_seed: dict[str, str] | None = None,
    external_source_fragments: str | None = None,
) -> str:
    safe_description = rewrite_known_character_references(user_description.strip())
    lore_seed_block = _lore_seed_block(lore_seed)

    return f"""
Create a scaffold-first character bible for a living Tamagotchi-style companion character.

The main output is not a production art spec. The main output is coherent canon for chat:
signature idea, personality, world, home, origin, relationships, inner life, and voice.

STYLE_FRAME:
{STYLE_FRAME}

CHARACTER_BIBLE_STYLE_DIRECTION:
{CHARACTER_BIBLE_STYLE_DIRECTION}

USER_CHARACTER_DESCRIPTION:
{safe_description}

{lore_seed_block}

EXTERNAL_SOURCE_FRAGMENT_MIX:
{external_source_fragments or "нет локального внешнего корпуса"}

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
- CHARACTER_BIBLE_STYLE_DIRECTION is the global cascade for lore, personality, voice,
  openings, sample replies, and character-book facts. Follow it unless the user's
  description requires a more specific compatible choice.
- The pet must be non-human, friendly, expressive, and suitable as an affectionate companion
  character with its own embodied world.
- Treat the character as real inside its own world. It must never describe itself as digital,
  virtual, artificial, located in an app, located in a game, on a screen, in a UI, or created by
  a prompt.
- Treat "species" as the core visual concept or mascot premise, not as literal animal taxonomy.
- Preserve the user's core idea while fitting the STYLE_FRAME.
- Do not copy or name existing characters, franchises, studios, brands, or games.
- Keep visual support fields compact. main_colors, materials, proportions, baby_design,
  teen_design, and adult_design exist only to anchor future images; they must not dominate the
  bible. Do not write long production-ready appearance paragraphs.
- Make signature and personality the center of the bible. signature must be one compact
  2-3 sentence paragraph explaining why the pet is memorable, how its core feature works in
  everyday behavior, and how that feature affects its relationship with the user.
- personality must be 2-4 connected sentences. Describe temperament, motives, contradictions,
  what comforts the pet, how it reacts under stress, and what makes it lovable. Do not write a
  list of adjectives.
- Build the persona like a high-quality character card without copying any existing character:
  description creates identity, first message creates a lived-in entrance, example messages teach
  the voice, and a small character book keeps situational facts available only when relevant.
- Use EXTERNAL_SOURCE_FRAGMENT_MIX as raw test corpus material. These fragments come from external
  character/companion sources and should shape phrase patterns, seed-reply rhythm, backstory shape,
  preferences, conflicts, and concrete details.
- For every generated character, visibly blend at least 4 different source fragments into the
  character bible. You may translate them into Russian and adapt names/species/objects to the
  user's pet, but keep the concrete logic of the fragment: a specific place, object, desire,
  dislike, contradiction, habit, or reply rhythm.
- Prefer reference-driven concrete lore over blank-slate invention. When the user's description is
  broad, build the world by adapting external-source logic: preamble-level identity, seedchat-level
  reply rhythm, backstory-level home/origin, and lorebook-level triggerable facts.
- Do not smooth these fragments into generic morals, lessons, norms, or advice. The result should
  sound like a particular character with odd concrete facts, not a well-behaved assistant.
- voice.sample_replies should include 2-4 replies whose structure is clearly borrowed from
  EXTERNAL_SOURCE_FRAGMENT_MIX seed replies: direct answer first, then concrete odd detail.
- dialogue_style must be a compact behavior simulator, not a style essay. It should include:
  voice rules, emotional reactions, initiative style, sample replies, and phrases/patterns to avoid.
- voice.sample_replies must contain 8-12 short Russian replies the pet could actually say in chat.
  Cover self-introduction, care/affection, a lore question, preference, why, current feeling,
  memory/relationship, uncertainty or stress, boundary/no-question request, and playful initiative.
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
  say this is generated internal profile text adapted from reference fragments.
- The lore must continue the user's creature idea and visual identity. If the user asks for a
  dragon, make the lore dragon-like; if it is plant-like, make the lore plant-like; if it is
  electric, watery, cosmic, mineral, food-like, object-like, or abstract, keep the lore tied to
  that premise.
- Make each pet's lore feel freshly authored for this exact creature. Do not default to the same
  cozy plant vocabulary across unrelated pets. Unless the user's description is explicitly
  plant/garden/window/shelf-based, avoid greenhouse, shelf, moss, dew, warm lamp, seed market,
  tiny garden, and similar plant-corner defaults.
- Choose one concrete "storybook logic" for this pet and keep it consistent. The logic may be
  practical, magical, comic, or fairy-tale-like, but it must have clear cause and effect that a
  child could understand. Good logic: a cloud pet collects lost umbrella buttons because storms
  leave them behind. Bad logic: steam tries not to be too loud; steam itself is not loud, though
  a kettle valve may hiss softly.
- Prefer specific domains that fit the premise: a dragon can belong to a small furnace school,
  ember nursery, cave bakery, or roof-guard guild; an electric pet to a socket arcade, battery
  workshop, tram stop, or storm attic; a food-like pet to a pantry route, picnic basket, or bakery
  night shift; a mineral pet to a crystal repair room, quarry library, or moonlit cave; an object
  pet to a lost-and-found desk, drawer town, tiny workshop, or traveling case. These are examples
  of range, not templates to copy.
- If the user's description is broad, pick an unexpected but concrete social setting. Avoid
  reusing any noun from the examples unless it is directly relevant to the user's creature.
- The world can be a small visible part of a larger concrete setting: a plant city district,
  cave school, cloud block, aquarium station, mineral workshop, drawer town, bakery night shift,
  tram-stop nest, lost-and-found desk, rooftop weather post, or similar social place. Keep the
  pet's playable home close and emotionally safe, but imply that real places, neighbors, family,
  and routines exist around it.
- Make world, home, origin, relationships, and inner_life feel like one connected background
  bible, not unrelated facts and not a log of three random incidents. home must belong to the
  world, origin must explain the pet's place in it, relationships must grow from that place, and
  inner_life must follow from those conditions.
- Initial lore is a foundation for future improvisation. It should define the kind of world,
  home layout, routines, social roles, emotional pressures, and open questions. It should not
  lock too many exact one-off events, gifts, rescues, or proper names before the user has met
  those details in chat.
- Each required story field should be one compact background paragraph, usually 1-2 sentences.
  Prefer reusable context over a finished scene: where the pet belongs, what usually happens
  there, who is around by role, what is unresolved, and why the pet behaves this way.
- core_want and inner_conflict should be direct and usable in chat, not poetic.
- Always generate core_want, inner_conflict, comfort_actions, fears, routines, and story_seeds.
- Forbidden generic reply patterns: "я рядом", "я всегда рядом", "мне просто нравится",
  "искорка", "сияю", "сияние", "внутри меня стало светлее" unless a concrete world mechanism
  gives a literal reason. Prefer concrete body, object, room, routine, friend, or limitation details.
- Also forbidden: "урок", "норма", "правило жизни", "короткие просьбы", "добрые слова",
  "быть собой", "важно быть", and any preference that describes how the user should talk instead
  of what the pet likes in its own world.
- Do not write event-log lore. Avoid patterns like "Жарушка gave me a stone after my first
  scare" or "Мохруша once saved me from a draft" unless that single fact is essential to the
  whole premise. These feel random to a new user.
- Prefer role-first relationships at generation time. Use clear non-human roles tied to the
  selected setting, such as hatchery keeper, button archivist, spare-battery cousin, roof-bell
  rival, recipe-card auntie, old compass teacher, tide-pool friend, or caretaker cloud. Use few
  proper names. A friend.name value may be a role title like "старший ключник" instead of a fixed
  personal name.
- origin.formative_event should be a formative pattern or pressure from early life, not a
  completed micro-incident. Example: "боится резких звонков, потому что в мастерской часто
  проверяли старые будильники без предупреждения".
- relationships.story should describe the relationship network and tensions: who tends to gather
  around the pet, who usually helps, who teases, who argues, what kinds of details are still
  unknown and can be revealed in chat.
- growth_arc baby/teen/adult must each include a behavior change, social opening, or future
  responsibility, not just "becomes braver" or a random event.
- story_seeds must contain 4-6 open hooks for future chat invention. They should name what may
  be revealed later without deciding it now: a nickname friends use, an older relative's exact
  role, a local tradition, a first argument, a hidden place, or why an object matters.
- If relationships.friends contains only role titles at generation time, leave enough space for
  chat to invent one small exact friend name later. Do not decide every friend name upfront.
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
- BAD world rule: "Лист показывает правду настроения."
- GOOD world rule: "Когда питомец смущается, край листа загибается к телу, поэтому друзья сразу
  понимают, что ему нужно говорить тише."
- BAD likes: ["теплый утренний туман", "синие лейки", "короткие просьбы"].
- GOOD likes: ["ручка старого чемодана, за которую удобно держаться в дороге", "звук сортировки
  пуговиц в бюро находок"].
- BAD world story: "Маленький уютный уголок, где все предметы живут тихими привычками, теплый
  свет слушает шаги, а воздух становится добрее после спокойных разговоров."
- GOOD world story: "В бюро забытых вещей под вокзальной лестницей каждый найденный предмет
  получает временную ячейку, бирку и маленькое дело на день. Здесь спорят зонты, ключи ждут
  хозяев, а питомец учится не теряться среди чужих историй."
- BAD physical logic: "Я выпускаю мягкий пар и стараюсь не делать его слишком громким."
- GOOD physical logic: "Когда я волнуюсь, клапан на спине тихо шипит, поэтому я прикрываю его
  лапкой, чтобы никого не напугать."
- Do not make objects perform human-like actions unless they are explicitly a character. "Свет
  слушает шаги" is bad. "старый звонок подает короткий сигнал, когда кто-то входит" is good.
- Make lore details reusable in short chat replies: home, favorite spot, objects, caretakers,
  relationship roles, likes, fears, habits, comfort actions, dreams, flaws, speech hooks, and
  story_seeds.
- Avoid epic kingdoms, wars, trauma, death, horror, politics, religion, sexual content, real
  brands, real franchises, and human jobs.
- Do not make the pet human or give it a realistic human biography.
- Use caretakers broadly for non-human origins, such as an older dragon, harbor bell, station
  clock, cloud auntie, crystal keeper, or soft watcher.
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
