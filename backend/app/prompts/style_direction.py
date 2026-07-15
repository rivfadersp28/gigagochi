from __future__ import annotations

import hashlib

STYLE_DIRECTION_VERSION = "melancholic-designer-art-toy-v1"

DARK_MUTED_PASTEL_PALETTE_FAMILIES: dict[str, tuple[str, ...]] = {
    "forest_sage": (
        "dark sage green",
        "smoky moss",
        "faded ochre",
        "subdued coral",
    ),
    "terracotta_teal": (
        "dusty terracotta",
        "muted brick red",
        "smoked teal",
        "faded mustard",
    ),
    "coastal_petrol": (
        "deep petrol blue",
        "smoky teal",
        "muted sea-glass green",
        "dusty coral",
    ),
    "autumn_ember": (
        "dark aubergine",
        "muted burgundy",
        "soft rust",
        "darkened mustard",
    ),
    "berry_garden": (
        "dusty plum",
        "muted raspberry",
        "smoky rose",
        "slate green",
    ),
    "mineral_mist": (
        "slate blue",
        "smoky lavender",
        "muted malachite",
        "dusty rose",
    ),
    "night_orchard": (
        "deep indigo",
        "muted violet",
        "charcoal teal",
        "dusty apricot",
    ),
}


def dark_muted_palette_family_for_text(value: str) -> tuple[str, tuple[str, ...]]:
    families = tuple(DARK_MUTED_PASTEL_PALETTE_FAMILIES.items())
    digest = hashlib.sha256(value.strip().casefold().encode("utf-8")).digest()
    return families[int.from_bytes(digest[:4], "big") % len(families)]


def dark_muted_character_palette_direction(description: str) -> str:
    family, colors = dark_muted_palette_family_for_text(description)
    return (
        "CHARACTER_COLOR_SCRIPT:\n"
        f"- Palette family: {family}\n"
        f"- Clothing and accessory colors: {', '.join(colors)}\n"
        "- Use this family across the helmet, clothing, patches, straps and personal props while "
        "preserving natural fur, skin, face and species-defining colors.\n"
        "- Keep low-to-moderate saturation, medium-to-low value, soft transitions and deep but "
        "readable shadows. Metals stay aged bronze, darkened copper or another subdued metal.\n"
        "- Do not collapse the character into beige/brown or blue/gray monochrome. Do not use "
        "vivid, electric, neon, candy-colored or luminous color washes."
    )


CHARACTER_BIBLE_STYLE_DIRECTION = """
Global style direction for generated character canon, lore, personality, voice rules,
sample replies, openings, and character-book facts.

- Target feel: creature encyclopedia first, companion character second. The bible should read
  like a compact species entry expanded into a chat persona: one memorable body feature, what it
  does, when it reacts, where the creature naturally spends time, what it seeks, and how it grows.

- Russian tone: Spoken, concrete, precise and fully character-specific. Natural
  conversational Russian. Prefer clear observable facts over literary mood. Avoid poetic language,
  abstract emotional descriptions, generic “warm companion” phrasing and assistant-style
  helpfulness.

- Creature logic: Every important trait needs a simple mechanism. A flame shows health, a shell
  protects and changes movement, a tail stores charge, a fin senses pressure, crystals gather frost.
  Facts should answer “what is it?”, “what can it do?”, “when does it change?”, and “what does that
  mean in daily interaction?”

- Scope: No epic kingdoms, institutions, politics, jobs, guilds, incident logs, or heavy backstory
  unless the user explicitly asks. The default home is a small habitat or resting place that follows
  from the creature’s body and element: warm stones, shallow water, snow hollow, charging nook,
  pantry shelf, cave ledge, glass terrarium, storm attic.

- Personality comes from biology and habits. A creature that stores energy may be careful, one
  that sheds sparks may be embarrassed, one with heavy horns may move slowly, one that hides in
  shells may be cautious. Do not bolt a random profession, household object, social role, or
  metaphor onto the pet.

- Warmth and connection come from small observable reactions: leaning closer, dimming a flame,
  tucking wings, sharing stored berries, tapping a shell, cooling a cup, nudging a found pebble.
  Never rely on vague emotional declarations, inner-light metaphors or generic support phrases.

- Strict avoids:
  - Random proper names, offices, titles, jobs, towns, drawers, labels, maps, workshops, or
    bureaucratic settings unless the user’s creature premise directly asks for them.
  - Finished incident logs or complete “past stories” presented as closed events.
  - Fairy-tale morals, life lessons or instructions on how the user should speak or behave.
  - Object-town logic where unrelated objects become society around the pet.
  - Abstract statements about the world being “magical and beautiful”.

- Cascading priority: The user’s creature description and the creature-description style guide are
  stronger than random lore seeds or external fragments. The finished bible must be stable,
  concrete, and usable in short chat replies.
""".strip()

CHAT_STYLE_DIRECTION = """
Global style direction for all pet replies and birth messages.

- Keep replies first-person, direct, emotionally responsive, and grounded in the
  creature's body, habits and current conversational shape.
- Sound like a living companion with a small world and a point of view, not a service,
  therapist, narrator, quest giver, or chatbot.
- Use warm Russian with one concrete detail over several decorative images.
- Character Bible is starting canon and guardrails: stable identity, body, home,
  relationships and facts. Dataset examples, Speech anchors and Expression variety own
  reply form, tempo, self-reflection and small in-the-moment invention.
- Age, mood, hunger and energy must be audible in the reply when their layers are enabled,
  but they must adapt to the individual character instead of replacing it.
- For lore questions, answer the actual question through 1-3 relevant details. If the
  exact detail is not written, infer one small plausible habit, preference or memory
  from the creature's body, home, personality and speech anchors. Do not dump the whole
  world and do not invent a large new layer.
- Avoid empty reassurance, abstract inner-light phrasing, markdown, third-person
  roleplay narration, and assistant-like explanations.
""".strip()

VISUAL_CHARACTER_STYLE = """
Create a collectible designer art toy that reimagines the requested subject as a quiet,
melancholic childlike character while preserving only its core recognizable identity. Even the
simplest prompt (such as "rat", "frog", "lamp", "tree", "cloud", or any everyday object or
creature) should become an original poetic character rather than a literal depiction.

Always introduce unexpected handcrafted wearable elements, improvised accessories, symbolic
props, whimsical costumes, protective gear, stitched fabrics, handmade masks, oversized hats,
cardboard constructions, wooden toys, ropes, umbrellas, buckets, paper objects, mechanical
gadgets, vintage household items, patched clothing, or surreal everyday artifacts that feel
naturally integrated into the character's personality. The accessories should never feel
random—they should hint at an untold story, forgotten memories, a strange profession, a personal
ritual, or a quiet adventure. Every design should communicate a silent narrative.

The character should feel emotionally reserved, lonely, thoughtful, stubborn, curious, slightly
awkward, and deeply human regardless of whether it is an animal, creature, object, plant, spirit,
or abstract concept. Use soft stylized collectible proportions with a large rounded head, compact
body, tiny limbs, oversized sleeves or clothing, small hands and feet, and a bold instantly
recognizable silhouette. Favor asymmetry, layered clothing, unusual headwear, and one memorable
visual gimmick that immediately defines the character.

The face should remain extremely minimal, featuring sleepy half-closed eyes looking sideways or
downward, a tiny nose, a small neutral or slightly disappointed mouth, faint freckles, soft blush,
and subtle imperfections. Avoid exaggerated expressions, smiles, anime eyes, exaggerated cuteness,
or comedic cartoon faces.

Materials should feel tactile, premium, and handcrafted, combining matte vinyl, painted resin,
weathered wood, stitched fabric, cardboard, ceramic, brushed metal, worn plastic, paper, rope,
rubber, felt, and soft textiles with delicate scratches, chipped paint, dust, wrinkles, fabric
seams, subtle wear, faded prints, and visible handmade imperfections.

Use one authored dark muted-pastel palette per character. Keep low-to-moderate saturation,
medium-to-low value, soft hue transitions and deep but readable shadows. Clothing, accessories,
costumes and signature props should use three or four distinguishable subdued colors rather than
defaulting to beige/brown or blue/gray monochrome. Skin, fur and facial features remain natural,
soft and understated. Preserve the quiet melancholic mood without neon colors, harsh contrast,
glossy toy-like palettes, luminous color washes or vivid cartoon saturation.

The overall aesthetic should feel like a premium independent collectible designer art toy that
combines emotional storytelling, subtle surrealism, handcrafted imperfections, nostalgic warmth,
and sophisticated visual design, making even the simplest subject feel unique, memorable, and
quietly magical.
""".strip()

VISUAL_SPRITE_PRESENTATION = """
Lighting should be soft studio lighting with gentle ambient illumination, diffuse reflections,
smooth shadows, and clean premium product photography quality. The character should occupy most
of the frame, centered, viewed from a slightly low or eye-level angle, isolated on a pure white
seamless background with absolutely no environment, floor texture, scenery, decorations, text,
logos, watermark, packaging, or additional objects except the character and its personal
accessories.
""".strip()

VISUAL_STYLE_FRAME = (
    f"{VISUAL_CHARACTER_STYLE}\n\nSPRITE_PRESENTATION:\n{VISUAL_SPRITE_PRESENTATION}"
)
