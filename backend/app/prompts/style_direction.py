from __future__ import annotations

STYLE_DIRECTION_VERSION = "pokemon-description-clean-v3"

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

VISUAL_STYLE_FRAME = """
Create a premium collectible designer toy in the style of an independent blind-box vinyl art figure. Reimagine the requested subject into a charming, emotionally expressive collectible character while preserving its recognizable identity. The result does not have to be human. It may become a whimsical creature, an anthropomorphic animal, a living object, a tiny spirit, or a melancholic childlike character—whichever interpretation best captures the essence of the prompt. Always prioritize originality, emotional appeal, and a memorable silhouette over literal realism. The design language is quiet, nostalgic, whimsical, and subtly surreal. Every character should feel like it has its own inner world and untold story. The mood emphasizes innocence, curiosity, loneliness, kindness, quiet determination, and gentle melancholy rather than exaggerated cuteness or comedy. Use simplified collectible toy proportions with a large rounded head, compact body, tiny limbs, oversized feet or paws, and a bold, instantly recognizable silhouette. Whether the character is human, animal, object, or fantasy creature, it should retain soft rounded forms and toy-like proportions suitable for a collectible vinyl figure. Faces should remain minimalistic and emotionally restrained. Use sleepy half-closed eyes, side glances, tiny pouty lips or a small muzzle, a subtle rounded nose, soft blushing cheeks, light freckles when appropriate, and a calm, thoughtful, slightly grumpy expression. Avoid exaggerated smiles, wide-open mouths, or energetic cartoon expressions. Rather than literally reproducing the prompt, reinterpret it into the language of designer toys. The requested subject may inspire wearable costumes, oversized hats, symbolic accessories, stylized anatomy, or entirely original creature designs. A rat may become a tiny melancholic rat with oversized ears, stitched clothing, and a backpack, or a child wearing a handmade rat hood. A tree may become a living forest spirit or a child wrapped in bark and leaves. A rock may become a small stone creature with moss growing on its head. A cloud may become a floating fluffy spirit. The result should always feel like an original collectible character instead of a realistic depiction. Materials should resemble high-quality matte vinyl, smooth resin, sculpted clay, hand-painted figurines, brushed fabric, knitted wool, felt, cardboard, paper, stitched canvas, polished wood, ceramic details, soft rubber, and other handcrafted materials. Surfaces should include subtle imperfections such as gentle paint wear, tiny scratches, stitched seams, fabric fibers, paper texture, sculpting marks, and softly rounded edges. Avoid glossy plastic and photorealistic rendering. Use a restrained palette built around warm cream, peach skin, dusty olive, sage green, terracotta, muted mustard, cocoa brown, faded denim, warm gray, pale turquoise, dusty coral, charcoal, off-white, and occasional desaturated red or antique gold accents. Colors should feel soft, harmonious, earthy, and slightly faded rather than vibrant or saturated. Lighting should resemble premium studio product photography with large diffused softboxes, soft ambient bounce light, delicate contact shadows, gentle ambient occlusion, smooth gradients, and subtle depth. Avoid dramatic cinematic lighting, hard shadows, strong rim lights, or high contrast. Compose the image as a clean collectible product shot. Show a centered full-body character standing or sitting naturally against a seamless warm white or very light gray studio background with generous negative space. Keep the composition minimal, uncluttered, and immediately readable even at thumbnail size. Include one memorable handcrafted storytelling element that gives the character personality without overwhelming the design. This may be an oversized knitted hat, stitched hood, cardboard crown, paper house, tiny backpack, old clock, lantern, balloon companion, toy dinosaur, handmade sign, cracked umbrella, miniature pet, worn scarf, or another symbolic object inspired by the prompt. Every accessory should feel handmade, imperfect, poetic, and emotionally meaningful. The final image should look like a premium collectible art toy from an independent designer brand: handcrafted, emotionally expressive, minimalist, whimsical, softly surreal, highly recognizable, instantly lovable, and visually iconic. The artistic identity should remain consistent regardless of the prompt, whether the subject is a rat, bird, mushroom, cloud, rock, tree, teacup, robot, monster, or any other simple concept.
""".strip()
