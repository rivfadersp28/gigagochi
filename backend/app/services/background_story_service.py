from __future__ import annotations

import copy
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from app.config import get_settings
from app.prompts.style_direction import VISUAL_CHARACTER_STYLE
from app.schemas import LocalChatHistoryItem, LocalPetChatContext, LocalPetMemoryContext
from app.services.character_dossier import story_character_data
from app.services.image_service import generate_image_bytes
from app.services.lite_overlay import (
    LITE_FACT_KINDS,
    LITE_FACT_SPHERES,
    overlay_patch_from_extracted_facts,
)
from app.services.lore_runtime import lore_prompt_block
from app.services.openai_service import (
    chat_reasoning_effort_kwargs,
    get_chat_model,
    get_openai_client,
)
from app.services.pet_reply_engine.context_plan import (
    CONTEXT_ROUTING_SOURCE_IDS,
    ContextPlan,
    ContextRoutingDecision,
    build_context_plan,
    router_sources_for_auto_modes,
)
from app.services.pet_reply_engine.speech_runtime import (
    CONTEXT_SOURCE_KEYS,
    background_story_aftermath_extraction_system_prompt,
    background_story_aftermath_extraction_user_prompt,
    background_story_coherence_check_system_prompt,
    background_story_coherence_check_user_prompt,
    background_story_default_event_type,
    background_story_max_rag_chars,
    background_story_max_story_chars,
    background_story_reasoning_effort,
    background_story_source_flags,
    background_story_system_prompt,
    background_story_user_prompt,
    context_routing_sources,
    context_routing_system_prompt,
    context_source_enabled,
    context_source_mode,
    state_param_labels,
    state_param_usage_rule,
)
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response
from app.services.story_library import search_story_library
from app.services.tone_runtime import tone_prompt_block

logger = logging.getLogger(__name__)

MAX_CHARACTER_DOSSIER_CHARS = 12000
MAX_DOSSIER_LIST_ITEMS = 12
MAX_AFTERMATH_CONTEXT_CHARS = 12000
AFTERMATH_CONFIDENCE_THRESHOLD = 0.7
BACKGROUND_ROUTING_SOURCE_IDS = CONTEXT_ROUTING_SOURCE_IDS
STORY_STAT_KEYS = {"hunger", "happiness", "energy"}
STORY_STAT_MAX_ITEMS = 2
STORY_STAT_MAX_SINGLE_DAMAGE = 25
STORY_STAT_MAX_TOTAL_DAMAGE = 35
STORY_DIRECTION_HISTORY_LIMIT = 12
STORY_MODE_COOLDOWN = 3
LOCAL_REFERENCE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
BACKGROUND_STORY_IMAGE_PROMPT_MAX_CHARS = 8400
BACKGROUND_STORY_IMAGE_SCENE_STORY_MAX_CHARS = 2400
BACKGROUND_STORY_IMAGE_SCENE_MAX_CHARS = 700
BACKGROUND_STORY_IMAGE_HERO_POSE_MAX_CHARS = 240
BACKGROUND_STORY_IMAGE_CAMERA_MAX_CHARS = 140
BACKGROUND_STORY_IMAGE_POSE_HISTORY_LIMIT = 3
BACKGROUND_STORY_IMAGE_POSE_FAMILIES = (
    "locomotion",
    "crouching_observation",
    "reaching_or_manipulating",
    "carrying_pushing_or_pulling",
    "defending_or_evading",
    "resting_or_recovering",
    "physical_interaction",
)
BACKGROUND_STORY_IMAGE_POSE_GUIDANCE = {
    "locomotion": "walking, running, climbing, balancing or crossing",
    "crouching_observation": "crouching, kneeling, leaning close or looking underneath",
    "reaching_or_manipulating": "reaching, lifting, opening, repairing or using an object",
    "carrying_pushing_or_pulling": "carrying, dragging, pushing or pulling with visible effort",
    "defending_or_evading": "bracing, shielding, dodging, hiding or escaping",
    "resting_or_recovering": "sitting, lying, stretching, warming up or recovering",
    "physical_interaction": "helping, greeting, holding, supporting or playing with another",
}
BackgroundStoryImagePromptMode = Literal[
    "baseline",
    "isolated_identity",
    "full_stop_motion",
]
BACKGROUND_STORY_IMAGE_SCENE_INSTRUCTION = (
    "Выдели один самый иллюстративный момент истории. Верни компактное визуальное описание "
    "одного кадра: действие, окружение и важные предметы. Не пересказывай всю историю, "
    "не добавляй название, жанр, теги, мораль или сведения, которых нет в сюжете. "
    "Сохрани указанный в истории вид каждого участника: человек остаётся человеком, дух — "
    "духом, животное — животным. Для второстепенных персонажей кратко укажи различимые "
    "силуэт, занятие и соответствующее моменту настроение; не копируй им автоматически "
    "внешность или меланхоличное выражение главного героя. Референс главного героя фиксирует "
    "только его личность и дизайн, но не исходную позу, положение головы, рук, ног или камеры. "
    "Выбери для него сюжетно необходимую активную позу: опиши наклон корпуса, направление "
    "взгляда, положение конечностей, перенос веса и контакт с землёй, предметом или другим "
    "участником. Не используй нейтральное фронтальное стояние с опущенными руками, если оно "
    "не является важной частью действия."
)
BACKGROUND_STORY_SCENE_STYLE = """
ART DIRECTION FOR THE WHOLE SCENE:
- Treat the attached image as the exact identity anchor for the main character and as a loose
  reference for palette, tactile materials and shape language. Do not turn every other character
  into a copy of the hero.
- Keep the cast diverse and faithful to the story. Supporting characters may be stylized humans,
  animals, spirits, plants, objects or unfamiliar creatures, with varied ages, body shapes,
  silhouettes, clothing and materials. Humans must remain recognizably human; spirits may be
  translucent, abstract or weightless; animals keep their species-specific anatomy.
- Give supporting characters expressions and poses appropriate to their role and the current
  action. They may be cheerful, alert, busy, stern, frightened, kind or strange. Do not give
  everyone the hero's sleepy eyes, large head, tiny body, melancholic mood, patched clothes or
  signature accessories.
- Unify the cast through one authored illustration language: simplified expressive shapes,
  restrained facial detail, tactile matte surfaces, the same color harmony and the same soft
  lighting. Avoid photorealistic skin, portrait faces, live-action extras and realistic wildlife
  photography, but preserve meaningful differences between people, spirits, animals and creatures.

ENVIRONMENT FRAME — APPLY ONLY TO THE WORLD AROUND THE CHARACTERS:
- These rules control only scenery, architecture, foliage, terrain, atmospheric depth, lighting
  and non-character props. They must never redesign, replace or restyle the referenced main
  character. Story-required supporting characters keep the diverse character direction above.
- Build the environment as a handcrafted stop-motion miniature set with tactile diorama scenery.
  Use painted wood, cardboard, paper, fabric, matte resin, clay and occasional stitched elements,
  with restrained handmade imperfections rather than realistic construction or natural textures.
- Use Japanese-inspired minimalism: three to five large readable environmental shapes, a clean
  silhouette, calm negative space and an uncluttered open area around the main action. Favor a
  balanced, near-symmetrical composition when the story action allows it; clarity is more important
  than rigid symmetry.
- Keep distant architecture, foliage, rocks and props simple, graphic and slightly theatrical.
  The setting should feel like an elegant empty miniature stage before the cast was placed into it,
  while still matching the location described by the story.
- Use soft diffused practical lighting, gentle atmospheric depth and only a subtle shallow focus.
  Keep a muted earthy palette, nostalgic warmth and quiet melancholy in the environment without
  overriding the actual emotion or valence of the story and its characters.

DETAIL HIERARCHY AND RESTRAINT:
- Detail hierarchy: highest detail on the main character, medium detail on important supporting
  characters and story objects, low detail in the background. Keep texture selective and localized
  near the focal action.
- Do not invent background people, animals, vehicles, signs or decorative foreground subjects.
  Include such elements only when the story explicitly requires them. Never add text, logos or
  readable signage.
- Avoid micro-detail everywhere, dense prop clutter, countless repeated objects, busy foliage,
  individually rendered stones, excessive surface scratches, dramatic depth-of-field bokeh,
  glossy cinematic spectacle and photorealistic environment rendering. Aim for selectively crafted
  detail and premium cinematic composition, not an AI-made maximalist fantasy render.
""".strip()
BACKGROUND_STORY_ISOLATED_IDENTITY_STYLE = """
CHARACTER IDENTITY — APPLY ONLY TO THE REFERENCED MAIN CHARACTER:
- Treat the attached isolated character image as an identity reference, not as a composition or
  background reference. Ignore and replace any white, transparent or studio background around it.
- Preserve the character's exact species, silhouette, face, proportions, colors, clothing,
  accessories and signature details. Do not redesign or simplify the character.
- Keep the character's existing authored rendering style. Match it to the scene only through
  shared lighting, color grading and contact shadows; do not spread its surface detail or realism
  into the environment.
""".strip()
BACKGROUND_STORY_FULL_STOP_MOTION_CHARACTER_STYLE = """
MAIN CHARACTER — TRANSLATE THE REFERENCE INTO THE SAME STOP-MOTION WORLD:
- Keep the referenced character unmistakably the same: preserve species, silhouette, face,
  proportions, palette, clothing, accessories and every signature detail.
- The reference fixes identity only, not pose or camera. Re-articulate the puppet into the exact
  story-driven hero pose specified above, with clear weight, balance, limb placement, gaze and
  physical contact. Do not copy the neutral standing pose from the reference.
- Rebuild the character as a handcrafted stop-motion puppet made from matte clay, painted wood,
  felt, stitched fabric, paper and small practical metal parts. Use simplified readable forms,
  restrained seams and selective handmade imperfections.
- The character and environment must look physically made by the same miniature workshop and
  photographed together on one practical set. Use the same scale, light, lens language and tactile
  material vocabulary across the whole frame.
- Avoid photoreal fur, skin, vegetation or stone; glossy CGI, vinyl-toy rendering, hyper-detailed
  scratches, cinematic fantasy realism and a pasted-in character appearance.
""".strip()
STORY_DIRECTION_FIELDS = (
    "plotMode",
    "incidentClass",
    "causalOrigin",
    "eventScale",
    "settingClass",
    "oppositionClass",
    "resolutionMode",
    "resolutionFamily",
    "valenceTarget",
)
STORY_INCIDENT_PUZZLE_COOLDOWN = 7
STORY_INCIDENT_INSTRUCTIONS = {
    "accident": "непреднамеренное происшествие с немедленным наблюдаемым последствием",
    "plan_disrupted": "внешнее событие срывает конкретный план или занятие героя",
    "other_agent_action": "чужой осознанный поступок заметно меняет положение героя",
    "resource_loss_or_damage": (
        "существенный запас, путь, укрытие или рабочая вещь теряется либо повреждается"
    ),
    "conflict_or_dispute": "цели двух сторон сталкиваются и требуют решения",
    "rescue_or_aid": "конкретный участник оказывается в затруднении и получает деятельную помощь",
    "competition_or_test": "участники соревнуются или проходят понятное практическое испытание",
    "unexpected_opportunity": (
        "внешняя возможность требует решения и приводит к заметному результату"
    ),
    "environmental_change": "погода, вода, огонь, грунт или пространство реально меняют условия",
    "puzzle_discovery": "редкая загадка с заранее наблюдаемыми уликами и проверяемым ответом",
}
STORY_INCIDENTS_BY_MODE = {
    "encounter": (
        "other_agent_action",
        "conflict_or_dispute",
        "competition_or_test",
        "unexpected_opportunity",
    ),
    "exploration": (
        "accident",
        "plan_disrupted",
        "environmental_change",
        "unexpected_opportunity",
        "puzzle_discovery",
    ),
    "mystery": (
        "other_agent_action",
        "resource_loss_or_damage",
        "plan_disrupted",
        "puzzle_discovery",
    ),
    "social_event": (
        "other_agent_action",
        "conflict_or_dispute",
        "competition_or_test",
        "unexpected_opportunity",
    ),
    "pursuit_or_conflict": (
        "other_agent_action",
        "resource_loss_or_damage",
        "conflict_or_dispute",
        "rescue_or_aid",
    ),
    "rescue_or_help": (
        "accident",
        "plan_disrupted",
        "rescue_or_aid",
        "environmental_change",
    ),
    "discovery": (
        "unexpected_opportunity",
        "environmental_change",
        "other_agent_action",
        "puzzle_discovery",
    ),
    "environmental_event": (
        "accident",
        "plan_disrupted",
        "resource_loss_or_damage",
        "environmental_change",
    ),
    "peaceful_change": (
        "plan_disrupted",
        "other_agent_action",
        "competition_or_test",
        "unexpected_opportunity",
    ),
}
STORY_CAUSAL_ORIGINS_BY_INCIDENT = {
    "accident": ("hero_mistake", "equipment_failure", "terrain_failure", "other_agent_mistake"),
    "plan_disrupted": ("weather", "other_agent", "material_failure", "animal_behavior"),
    "other_agent_action": ("other_agent",),
    "resource_loss_or_damage": ("theft", "weather", "collision", "material_failure"),
    "conflict_or_dispute": ("incompatible_goals", "scarcity", "misunderstanding"),
    "rescue_or_aid": ("accident", "weather", "pursuit", "exhaustion"),
    "competition_or_test": ("shared_goal", "limited_time", "limited_resource"),
    "unexpected_opportunity": ("arrival", "discovery", "invitation", "temporary_change"),
    "environmental_change": ("weather", "water", "fire", "terrain_failure"),
    "puzzle_discovery": ("unknown_cause",),
}
STORY_CAUSAL_ORIGIN_INSTRUCTIONS = {
    "hero_mistake": "ошибка или неверная оценка самого героя",
    "equipment_failure": "поломка работающего снаряжения или устройства",
    "terrain_failure": "обрушение, просадка или изменение проходимости",
    "other_agent_mistake": "непреднамеренная ошибка другого участника",
    "weather": "резкая перемена погоды",
    "other_agent": "осознанное действие другого участника",
    "material_failure": "поломка конструкции, крепления или полезного предмета",
    "animal_behavior": "обычное целенаправленное поведение живого существа",
    "theft": "кража или попытка присвоения",
    "collision": "столкновение или удар",
    "incompatible_goals": "несовместимые цели сторон",
    "scarcity": "нехватка места, времени или ресурса",
    "misunderstanding": "конкретно показанное неверное понимание намерений",
    "accident": "случайное происшествие с другим участником",
    "pursuit": "преследование или активная угроза",
    "exhaustion": "физическое истощение участника",
    "shared_goal": "общая цель с разными способами её достичь",
    "limited_time": "ясное ограничение времени",
    "limited_resource": "ясно ограниченный полезный ресурс",
    "arrival": "прибытие участника, груза или группы",
    "discovery": "обнаружение реально существующей возможности",
    "invitation": "предложение присоединиться к конкретному делу",
    "temporary_change": "краткое изменение условий создаёт возможность",
    "water": "подъём, спад или движение воды",
    "fire": "возгорание, дым или распространение жара",
    "unknown_cause": "неизвестная причина, раскрываемая проверкой наблюдаемых улик",
}
STORY_EVENT_SCALE_INSTRUCTIONS = {
    "immediate_incident": "заметно меняется безопасность, запас, работа или положение участников",
    "journey_disruption": "меняется маршрут, цель или возможность продолжать путь",
    "shared_situation": "событие затрагивает несколько участников или обитаемое место",
}
STORY_RESOLUTION_FAMILIES = {
    "dialogue_or_bargain": "negotiation",
    "outwit": "strategic_choice",
    "cooperation": "coordinated_action",
    "contest": "direct_confrontation",
    "investigation": "evidence_based_investigation",
    "discovery": "evidence_based_investigation",
    "journey_or_relocation": "relocation",
    "celebration_or_rest": "social_resolution",
    "stealth_or_escape": "evasion",
    "craft_or_ability": "practical_intervention",
}
STORY_VALENCE_WEIGHTS = {
    "positive": 4,
    "negative": 4,
    "mixed": 1,
    "neutral": 1,
}
STORY_VALENCE_INSTRUCTIONS = {
    "positive": (
        "чисто положительное событие: итог улучшает положение питомца без доминирующей "
        "травмы, потери или испуга; верни 1–2 только положительных statImpacts"
    ),
    "negative": (
        "чисто отрицательное событие: итог реально ухудшает положение питомца; "
        "верни 1–2 только отрицательных statImpacts"
    ),
    "mixed": (
        "смешанное событие: в результате есть одновременно выигрыш и цена; "
        "statImpacts должны соответствовать явно показанным последствиям"
    ),
    "neutral": (
        "нейтральное событие: оно содержательно и запоминаемо, но не улучшает и не ухудшает "
        "параметры; statImpacts должен быть пустым"
    ),
}
STORY_DIRECTION_SPECS: dict[str, dict[str, Any]] = {
    "encounter": {
        "instruction": (
            "Центр истории — встреча с самостоятельным существом или группой. "
            "У другой стороны есть собственная цель; сцена развивается через взаимодействие, "
            "а не через случайную западню."
        ),
        "settings": ("wild_frontier", "inhabited_place", "ancient_site", "liminal_place"),
        "oppositions": ("creature", "supernatural", "person_or_group"),
        "resolutions": ("dialogue_or_bargain", "outwit", "cooperation", "contest"),
    },
    "exploration": {
        "instruction": (
            "Центр истории — исследование значимого места. Герой должен войти глубже, "
            "сделать выбор маршрута или понять устройство места; не своди исследование "
            "к плите, петле, обвалу или необходимости просто выбраться."
        ),
        "settings": ("castle_or_tower", "ancient_site", "underground", "remote_landscape"),
        "oppositions": ("unknown_or_puzzle", "supernatural", "none"),
        "resolutions": ("investigation", "discovery", "journey_or_relocation"),
    },
    "mystery": {
        "instruction": (
            "Центр истории — необъяснимое присутствие, знак или исчезновение. "
            "Герой наблюдает, проверяет догадку и получает конкретный ответ или улику."
        ),
        "settings": ("liminal_place", "castle_or_tower", "wild_frontier", "inhabited_place"),
        "oppositions": ("supernatural", "unknown_or_puzzle", "creature"),
        "resolutions": ("investigation", "dialogue_or_bargain", "discovery"),
    },
    "social_event": {
        "instruction": (
            "Центр истории — просьба, спор, обмен, совместное дело, праздник или знакомство. "
            "Решающее изменение происходит в отношениях или договорённости, "
            "а не из-за физической аварии."
        ),
        "settings": ("inhabited_place", "road_or_crossing", "wild_frontier", "ancient_site"),
        "oppositions": ("person_or_group", "creature", "none"),
        "resolutions": ("dialogue_or_bargain", "cooperation", "celebration_or_rest"),
    },
    "pursuit_or_conflict": {
        "instruction": (
            "Центр истории — активное противостояние, преследование или защита цели "
            "от существа, духа или разумного соперника. Противник действует намеренно."
        ),
        "settings": ("wild_frontier", "castle_or_tower", "road_or_crossing", "underground"),
        "oppositions": ("creature", "supernatural", "person_or_group"),
        "resolutions": ("outwit", "stealth_or_escape", "contest", "dialogue_or_bargain"),
    },
    "rescue_or_help": {
        "instruction": (
            "Центр истории — помощь конкретному участнику или месту. У спасаемого есть роль "
            "в сцене, а герой меняет ситуацию осознанным поступком."
        ),
        "settings": ("wild_frontier", "inhabited_place", "ancient_site", "remote_landscape"),
        "oppositions": ("environment", "creature", "supernatural", "person_or_group"),
        "resolutions": ("cooperation", "craft_or_ability", "journey_or_relocation"),
    },
    "discovery": {
        "instruction": (
            "Центр истории — открытие знания, прохода, явления или свойства мира. "
            "Находка не обязана быть переносимым предметом и не должна автоматически "
            "становиться наградой."
        ),
        "settings": ("ancient_site", "liminal_place", "remote_landscape", "underground"),
        "oppositions": ("unknown_or_puzzle", "supernatural", "none"),
        "resolutions": ("discovery", "investigation", "journey_or_relocation"),
    },
    "environmental_event": {
        "instruction": (
            "Центр истории — крупное природное или пространственное событие. "
            "Не используй маленькую скрытую ловушку и не заканчивай обязательной "
            "травмой или потерей вещи."
        ),
        "settings": ("remote_landscape", "wild_frontier", "water_or_shore", "road_or_crossing"),
        "oppositions": ("environment",),
        "resolutions": ("craft_or_ability", "journey_or_relocation", "cooperation"),
    },
    "peaceful_change": {
        "instruction": (
            "Центр истории — тёплое, смешное, красивое или странное изменение "
            "без обязательной угрозы. "
            "Событие всё равно должно иметь развитие и запоминающийся результат."
        ),
        "settings": ("inhabited_place", "wild_frontier", "liminal_place", "water_or_shore"),
        "oppositions": ("none", "creature", "person_or_group"),
        "resolutions": ("celebration_or_rest", "cooperation", "dialogue_or_bargain", "discovery"),
    },
}
STORY_SETTING_INSTRUCTIONS = {
    "wild_frontier": "дикая природа или открытая граница обитаемого мира",
    "inhabited_place": "обитаемое место, стоянка, поселение, рынок или мастерская",
    "ancient_site": "древнее место со следами прежних обитателей",
    "liminal_place": "странное пограничное место, где допустимо присутствие духа или привидения",
    "castle_or_tower": "замок, башня или большая крепость, которую можно исследовать",
    "underground": "пещера, подземный комплекс или скрытый зал",
    "remote_landscape": "горы, пустошь, ущелье или иная дальняя местность",
    "road_or_crossing": "дорога, переправа или место встречи путников",
    "water_or_shore": "река, озеро, болото, побережье или остров",
}
STORY_OPPOSITION_INSTRUCTIONS = {
    "creature": "самостоятельное живое существо или монстр",
    "supernatural": "дух, привидение или локальное сверхъестественное явление",
    "person_or_group": "разумный незнакомец, соперник или группа",
    "unknown_or_puzzle": "тайна, неизвестный сигнал или задача для понимания",
    "environment": "среда или природное событие",
    "none": "без антагониста и без обязательной опасности",
}
STORY_RESOLUTION_INSTRUCTIONS = {
    "dialogue_or_bargain": "разговор, договор или обмен",
    "outwit": "хитрость и понимание чужой цели",
    "cooperation": "совместное действие",
    "contest": "открытое состязание или противостояние",
    "investigation": "наблюдение и проверка догадки",
    "discovery": "получение знания или открытие пути/явления",
    "journey_or_relocation": "осмысленное продолжение пути или переход в новое место",
    "celebration_or_rest": "праздник, игра, отдых или эмоциональное сближение",
    "stealth_or_escape": "скрытность, отвлечение или уход от активного преследователя",
    "craft_or_ability": "применение навыка, способности или созданного решения",
}
BACKGROUND_STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "causalPlan": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "setup": {"type": "string", "maxLength": 240},
                "problem": {"type": "string", "maxLength": 240},
                "action": {"type": "string", "maxLength": 240},
                "whyActionWorks": {"type": "string", "maxLength": 240},
                "consequence": {"type": "string", "maxLength": 240},
            },
            "required": ["setup", "problem", "action", "whyActionWorks", "consequence"],
        },
        "title": {"type": "string", "maxLength": 120},
        "summary": {"type": "string", "maxLength": 360},
        "storyParagraphs": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 220},
        },
        "eventType": {"type": "string", "maxLength": 60},
        "valence": {
            "type": "string",
            "enum": ["negative", "neutral", "positive", "mixed"],
        },
        "tags": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 40},
        },
        "statImpacts": {
            "type": "array",
            "maxItems": STORY_STAT_MAX_ITEMS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stat": {
                        "type": "string",
                        "enum": ["hunger", "happiness", "energy"],
                    },
                    "amount": {
                        "type": "number",
                        "minimum": -STORY_STAT_MAX_SINGLE_DAMAGE,
                        "maximum": STORY_STAT_MAX_SINGLE_DAMAGE,
                    },
                    "reason": {"type": "string", "maxLength": 280},
                },
                "required": ["stat", "amount", "reason"],
            },
        },
        "ragText": {"type": "string", "maxLength": 900},
    },
    "required": [
        "causalPlan",
        "title",
        "summary",
        "storyParagraphs",
        "eventType",
        "valence",
        "tags",
        "statImpacts",
        "ragText",
    ],
}
BACKGROUND_STORY_ROUTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sources": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                source: {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "query": {"type": "string", "maxLength": 500},
                    },
                    "required": ["enabled", "query"],
                }
                for source in BACKGROUND_ROUTING_SOURCE_IDS
            },
            "required": list(BACKGROUND_ROUTING_SOURCE_IDS),
        },
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["sources", "reason"],
}
BACKGROUND_STORY_COHERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "coherent": {"type": "boolean"},
        "eventful": {"type": "boolean"},
        "patternClass": {
            "type": "string",
            "enum": [
                "concrete_incident",
                "micro_clue_unlock",
                "passive_observation",
                "decorative_discovery",
                "other_weak",
            ],
        },
        "issues": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "maxLength": 240},
        },
        "retryInstruction": {"type": "string", "maxLength": 600},
    },
    "required": ["coherent", "eventful", "patternClass", "issues", "retryInstruction"],
}
BACKGROUND_STORY_AFTERMATH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "facts": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sphere": {"type": "string", "enum": list(LITE_FACT_SPHERES)},
                    "kind": {"type": "string", "enum": list(LITE_FACT_KINDS)},
                    "text": {"type": "string", "maxLength": 500},
                    "pathHint": {"type": "string", "maxLength": 120},
                    "source": {"type": "string", "maxLength": 80},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "sphere",
                    "kind",
                    "text",
                    "pathHint",
                    "source",
                    "confidence",
                ],
            },
        },
        "recentEvent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string", "maxLength": 500},
                "eventType": {"type": "string", "maxLength": 60},
                "participants": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "actions": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "objects": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "location": {"type": "string", "maxLength": 160},
                "outcome": {"type": "string", "maxLength": 260},
                "compactText": {"type": "string", "maxLength": 500},
                "canonicalFacts": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {"type": "string", "maxLength": 180},
                },
                "statusChanges": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "entity": {"type": "string", "maxLength": 120},
                            "state": {"type": "string", "maxLength": 80},
                            "owner": {"type": "string", "maxLength": 120},
                        },
                        "required": ["entity", "state", "owner"],
                    },
                },
            },
            "required": [
                "summary",
                "eventType",
                "participants",
                "actions",
                "objects",
                "location",
                "outcome",
                "compactText",
                "canonicalFacts",
                "statusChanges",
            ],
        },
    },
    "required": ["facts", "recentEvent"],
}
BACKGROUND_STORY_IMAGE_SCENE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scene": {
            "type": "string",
            "maxLength": BACKGROUND_STORY_IMAGE_SCENE_MAX_CHARS,
        },
        "poseFamily": {
            "type": "string",
            "enum": list(BACKGROUND_STORY_IMAGE_POSE_FAMILIES),
        },
        "heroPose": {
            "type": "string",
            "maxLength": BACKGROUND_STORY_IMAGE_HERO_POSE_MAX_CHARS,
        },
        "camera": {
            "type": "string",
            "maxLength": BACKGROUND_STORY_IMAGE_CAMERA_MAX_CHARS,
        },
    },
    "required": ["scene", "poseFamily", "heroPose", "camera"],
}


@dataclass(frozen=True)
class BackgroundStoryResult:
    title: str
    summary: str
    story_text: str
    event_type: str
    valence: str
    tags: tuple[str, ...]
    rag_text: str
    story_library_patch: dict[str, Any] | None
    lite_overlay_patch: dict[str, Any] | None
    recent_story_event: dict[str, Any] | None
    prompt_debug: list[dict[str, Any]]
    stat_impacts: tuple[dict[str, Any], ...] = ()
    stat_impact: dict[str, Any] | None = None
    stat_validation: dict[str, Any] | None = None
    plot_mode: str = ""
    incident_class: str = ""
    causal_origin: str = ""
    event_scale: str = ""
    setting_class: str = ""
    opposition_class: str = ""
    resolution_mode: str = ""
    resolution_family: str = ""
    valence_target: str = ""

    def model_dump(self) -> dict[str, Any]:
        stat_impacts = list(self.stat_impacts)
        if not stat_impacts and self.stat_impact:
            stat_impacts = list(
                _normalize_story_stat_impacts(
                    None,
                    legacy=self.stat_impact,
                    valence=self.valence,
                )
            )
        legacy_stat_impact = self.stat_impact or (stat_impacts[0] if stat_impacts else None)
        return {
            "title": self.title,
            "summary": self.summary,
            "storyText": self.story_text,
            "eventType": self.event_type,
            "valence": self.valence,
            "tags": list(self.tags),
            "ragText": self.rag_text,
            "storyLibraryPatch": self.story_library_patch,
            "liteOverlayPatch": self.lite_overlay_patch,
            "recentStoryEvent": self.recent_story_event,
            "promptDebug": self.prompt_debug,
            "statImpacts": stat_impacts,
            "statImpact": legacy_stat_impact,
            "statValidation": self.stat_validation,
            "plotMode": self.plot_mode,
            "incidentClass": self.incident_class,
            "causalOrigin": self.causal_origin,
            "eventScale": self.event_scale,
            "settingClass": self.setting_class,
            "oppositionClass": self.opposition_class,
            "resolutionMode": self.resolution_mode,
            "resolutionFamily": self.resolution_family,
            "valenceTarget": self.valence_target,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _text_value(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    text = _compact_spaces(str(value))
    return text[:limit].rstrip()


def _truncate_text(value: str, limit: int) -> str:
    text = _compact_spaces(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _string_list(value: Any, *, limit: int = MAX_DOSSIER_LIST_ITEMS) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text_value(item, limit=220)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _compact_json(value: Any, *, limit: int = 1400) -> str:
    if value in (None, "", [], {}):
        return ""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _truncate_text(text, limit)


def _select_record(value: Any, keys: tuple[str, ...], *, limit: int = 800) -> dict[str, Any]:
    if not _is_record(value):
        return {}
    result: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item in (None, "", [], {}):
            continue
        if isinstance(item, str):
            text = _text_value(item, limit=limit)
            if text:
                result[key] = text
        elif isinstance(item, list):
            values = _string_list(item)
            if values:
                result[key] = values
        elif isinstance(item, dict):
            nested = _clean_context_value(item)
            if nested not in (None, "", [], {}):
                result[key] = nested
        else:
            result[key] = item
    return result


def _valence_label(valence: str) -> str:
    return {
        "positive": "позитивный",
        "negative": "негативный",
        "neutral": "нейтральный",
        "mixed": "смешанный",
    }.get(valence, valence)


def _current_asset_image_url(pet: LocalPetChatContext) -> str:
    asset_images = pet.assetImages
    if not isinstance(asset_images, dict):
        return ""
    stage_images = asset_images.get(pet.stage)
    if not isinstance(stage_images, dict):
        return ""
    return _text_value(stage_images.get(pet.mood), limit=1000)


def _isolated_character_asset_image_url(pet: LocalPetChatContext) -> str:
    asset_images = pet.assetImages
    source_url = ""
    if isinstance(asset_images, dict):
        teen_images = asset_images.get("teen")
        if isinstance(teen_images, dict):
            source_url = _text_value(teen_images.get("idle"), limit=1000)
    source_url = source_url or _current_asset_image_url(pet)
    if not source_url or source_url.startswith("data:image/"):
        return ""

    parsed = urlparse(source_url)
    path_prefix, separator, _filename = parsed.path.rpartition("/")
    if not separator or "/static/generated/" not in parsed.path:
        return ""
    character_path = f"{path_prefix}/teen-idle-character.png"
    return parsed._replace(path=character_path).geturl()


def _is_public_reference_url(image_url: str) -> bool:
    if image_url.startswith("data:image/"):
        return True
    parsed = urlparse(image_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = parsed.hostname or ""
    return hostname not in LOCAL_REFERENCE_HOSTS and not hostname.endswith(".local")


def _absolute_reference_url(image_url: str, settings: Any) -> str:
    if _is_public_reference_url(image_url):
        return image_url
    if not image_url.startswith("/"):
        return ""

    base_url = _text_value(getattr(settings, "backend_public_url", None)) or _text_value(
        getattr(settings, "webapp_url", None)
    )
    if not base_url:
        return ""

    absolute_url = f"{base_url.rstrip('/')}/{image_url.lstrip('/')}"
    return absolute_url if _is_public_reference_url(absolute_url) else ""


def _isolated_character_asset_reference_url(pet: LocalPetChatContext) -> str:
    return _absolute_reference_url(_isolated_character_asset_image_url(pet), get_settings())


def _asset_input_references_for_background_story(
    pet: LocalPetChatContext,
) -> list[dict[str, Any]]:
    image_url = _isolated_character_asset_reference_url(pet)
    if not image_url:
        return []
    return [{"type": "image_url", "image_url": {"url": image_url}}]


def _background_story_text_for_image_scene(story: BackgroundStoryResult) -> str:
    tags = ", ".join(story.tags)
    return _truncate_text(
        f"""
Название: {story.title}
Кратко: {story.summary}
Сюжет: {story.story_text}
Тип события: {story.event_type}
Тон: {_valence_label(story.valence)}
Теги: {tags or "нет"}
""",
        BACKGROUND_STORY_IMAGE_SCENE_STORY_MAX_CHARS,
    )


def _recent_background_story_pose_families(
    recent_story_events: list[dict[str, Any]] | None,
) -> list[str]:
    families: list[str] = []
    for item in reversed(recent_story_events or []):
        if not isinstance(item, dict):
            continue
        family = _text_value(item.get("imagePoseFamily"), limit=80)
        if family not in BACKGROUND_STORY_IMAGE_POSE_FAMILIES or family in families:
            continue
        families.append(family)
        if len(families) >= BACKGROUND_STORY_IMAGE_POSE_HISTORY_LIMIT:
            break
    return families


def _available_background_story_pose_families(
    recent_story_events: list[dict[str, Any]] | None,
) -> tuple[str, ...]:
    blocked = set(_recent_background_story_pose_families(recent_story_events))
    available = tuple(
        family for family in BACKGROUND_STORY_IMAGE_POSE_FAMILIES if family not in blocked
    )
    return available or BACKGROUND_STORY_IMAGE_POSE_FAMILIES


def _background_story_image_scene_schema(
    pose_families: tuple[str, ...],
) -> dict[str, Any]:
    schema = copy.deepcopy(BACKGROUND_STORY_IMAGE_SCENE_SCHEMA)
    schema["properties"]["poseFamily"]["enum"] = list(pose_families)
    return schema


def _background_story_pose_options_block(pose_families: tuple[str, ...]) -> str:
    return "\n".join(
        f"- {family}: {BACKGROUND_STORY_IMAGE_POSE_GUIDANCE[family]}"
        for family in pose_families
    )


def extract_background_story_image_scene(
    story: BackgroundStoryResult,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
    prompt_debug: list[dict[str, Any]] | None = None,
    recent_story_events: list[dict[str, Any]] | None = None,
    direction_output: dict[str, str] | None = None,
) -> str:
    settings = get_settings()
    openai_client = client or get_openai_client()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    pose_families = _available_background_story_pose_families(recent_story_events)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты арт-директор для генерации иллюстраций. Не отвечай пользователю. "
                    "Верни только JSON по схеме. Сцена должна быть конкретной, визуальной "
                    "и пригодной как техническое задание художнику.\n\n"
                    f"{tone_prompt_block('imagePrompt')}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{BACKGROUND_STORY_IMAGE_SCENE_INSTRUCTION}\n\n"
                    "Выбери ровно одну допустимую poseFamily из списка ниже и не подменяй её "
                    "нейтральным стоянием:\n"
                    f"{_background_story_pose_options_block(pose_families)}\n\n"
                    f"Текст истории:\n{_background_story_text_for_image_scene(story)}"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_image_scene",
                "schema": _background_story_image_scene_schema(pose_families),
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    debug_entry = log_chat_completion_prompt("background_story/image_scene", request_kwargs)
    if prompt_debug is not None:
        prompt_debug.append(debug_entry)
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/image_scene", completion)
    content = completion.choices[0].message.content or "{}"
    payload = _json_record_from_text(content)
    scene = _text_value(payload.get("scene"), limit=BACKGROUND_STORY_IMAGE_SCENE_MAX_CHARS)
    if not scene:
        raise RuntimeError("BACKGROUND_STORY_IMAGE_SCENE_EMPTY")
    pose_family = _text_value(payload.get("poseFamily"), limit=80)
    if pose_family not in pose_families:
        pose_family = pose_families[0]
    hero_pose = _text_value(
        payload.get("heroPose"),
        limit=BACKGROUND_STORY_IMAGE_HERO_POSE_MAX_CHARS,
    )
    camera = _text_value(payload.get("camera"), limit=BACKGROUND_STORY_IMAGE_CAMERA_MAX_CHARS)
    if direction_output is not None:
        direction_output.update(
            {
                "poseFamily": pose_family,
                "heroPose": hero_pose,
                "camera": camera,
            }
        )
    return scene


def build_background_story_image_prompt(
    *,
    scene: str,
    mode: BackgroundStoryImagePromptMode = "baseline",
    pose_family: str = "",
    hero_pose: str = "",
    camera: str = "",
) -> str:
    if mode == "baseline":
        character_direction = (
            "Используй персонажа с приложенной референсной картинки без редизайна: "
            "точно сохрани его\n"
            "силуэт, лицо, пропорции, цвета, материалы, одежду, аксессуары "
            "и отличительные детали.\n"
            "Помести этого же персонажа в описанную сцену."
        )
        trailing_style = f"VISUAL_CHARACTER_STYLE:\n{VISUAL_CHARACTER_STYLE}"
    elif mode == "isolated_identity":
        character_direction = BACKGROUND_STORY_ISOLATED_IDENTITY_STYLE
        trailing_style = ""
    elif mode == "full_stop_motion":
        character_direction = BACKGROUND_STORY_FULL_STOP_MOTION_CHARACTER_STYLE
        trailing_style = ""
    else:
        raise ValueError(f"Unsupported background story image prompt mode: {mode}")

    pose_direction = ""
    if hero_pose:
        compact_hero_pose = _truncate_text(
            hero_pose,
            BACKGROUND_STORY_IMAGE_HERO_POSE_MAX_CHARS,
        )
        compact_camera = (
            _truncate_text(camera, BACKGROUND_STORY_IMAGE_CAMERA_MAX_CHARS)
            or "serve the action clearly"
        )
        pose_direction = f"""
HERO POSE — REQUIRED, DO NOT COPY THE REFERENCE POSE:
- Pose family: {pose_family or "story-driven action"}
- Body mechanics: {compact_hero_pose}
- Camera and framing: {compact_camera}
- Make the changed pose unmistakable through the torso, head, limbs, weight distribution and
  contact points. Identity details stay exact, but the reference stance must not survive.
""".strip()

    prompt = f"""
СЦЕНА:
{_truncate_text(scene, BACKGROUND_STORY_IMAGE_SCENE_MAX_CHARS)}

{pose_direction}

{character_direction}

Один цельный кадр, без текста, подписей,
логотипов, водяных знаков, коллажа и интерфейса.

{BACKGROUND_STORY_SCENE_STYLE}

{trailing_style}
""".strip()
    return _truncate_text(prompt, BACKGROUND_STORY_IMAGE_PROMPT_MAX_CHARS)


def generate_background_story_image_bytes(
    *,
    pet: LocalPetChatContext,
    story: BackgroundStoryResult,
    prompt_mode: BackgroundStoryImagePromptMode = "full_stop_motion",
    recent_story_events: list[dict[str, Any]] | None = None,
    direction_output: dict[str, str] | None = None,
) -> bytes:
    input_references = _asset_input_references_for_background_story(pet)
    if not input_references:
        raise RuntimeError("BACKGROUND_STORY_IMAGE_REFERENCE_MISSING")
    image_direction: dict[str, str] = {}
    scene = extract_background_story_image_scene(
        story,
        prompt_debug=story.prompt_debug,
        recent_story_events=recent_story_events,
        direction_output=image_direction,
    )
    if direction_output is not None:
        direction_output.update(image_direction)
    return generate_image_bytes(
        build_background_story_image_prompt(
            scene=scene,
            mode=prompt_mode,
            pose_family=image_direction.get("poseFamily", ""),
            hero_pose=image_direction.get("heroPose", ""),
            camera=image_direction.get("camera", ""),
        ),
        label="background_story/image",
        input_references=input_references,
    )


def _clean_context_value(value: Any) -> Any:
    if isinstance(value, str):
        return _text_value(value, limit=500)
    if isinstance(value, list):
        cleaned = [_clean_context_value(item) for item in value[:MAX_DOSSIER_LIST_ITEMS]]
        return [item for item in cleaned if item not in (None, "", [], {})]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"source_urls", "provenance", "dialogue_moves"}:
                continue
            cleaned = _clean_context_value(item)
            if cleaned not in (None, "", [], {}):
                result[str(key)] = cleaned
        return result
    return value


def _global_story_briefs(
    *,
    pet: LocalPetChatContext,
    query: str | None = None,
) -> list[dict[str, str]]:
    query_text = _compact_spaces(
        query
        or " ".join([_background_story_character_name(pet), pet.description, pet.stage, pet.mood])
    )
    result = search_story_library(
        query=query_text,
        pool_hints=[],
        limit=5,
        character_bible=pet.characterBible,
        include_global=True,
        include_overlay=False,
        include_patch=False,
        diverse_pools=True,
    )
    bricks = result.get("bricks") if isinstance(result.get("bricks"), list) else []
    briefs: list[dict[str, str]] = []
    for brick in bricks:
        if not _is_record(brick):
            continue
        name = _text_value(brick.get("name"), limit=120)
        text = _text_value(brick.get("text"), limit=360)
        if name or text:
            briefs.append({"name": name, "text": text})
    return briefs


def _memory_brief(memory_context: LocalPetMemoryContext | None) -> dict[str, Any] | None:
    if not memory_context:
        return None
    episodes: list[dict[str, Any]] = []
    for episode in memory_context.episodes[:3]:
        messages = [
            {
                "role": message.role,
                "text": _text_value(message.text, limit=500),
            }
            for message in episode.messages
            if _text_value(message.text, limit=500)
        ]
        if messages:
            episodes.append({"id": episode.id, "messages": messages})
    result = {
        "profile": _text_value(memory_context.userProfile, limit=700),
        "summary": _text_value(memory_context.summary, limit=700),
        "facts": [
            {
                "kind": item.kind,
                "text": _text_value(item.text, limit=360),
            }
            for item in memory_context.relevantMemories[:5]
        ],
        "episodes": episodes,
    }
    return {key: value for key, value in result.items() if value not in ("", [], None)}


def _history_brief(history: list[LocalChatHistoryItem] | None) -> list[dict[str, str]]:
    if not history:
        return []
    return [
        {
            "role": item.role,
            "text": _text_value(item.text, limit=500),
        }
        for item in history[-6:]
        if _text_value(item.text, limit=500)
    ]


def _recent_replies_brief(recent_replies: list[str] | None) -> list[str]:
    if not recent_replies:
        return []
    return [text for text in (_text_value(item, limit=500) for item in recent_replies[-6:]) if text]


def _story_event_briefs(recent_story_events: list[dict[str, Any]] | None) -> list[str]:
    if not recent_story_events:
        return []
    briefs: list[str] = []
    for item in recent_story_events[-8:]:
        if not _is_record(item):
            continue
        parts: list[str] = []
        title = _text_value(item.get("title"), limit=120)
        if title:
            parts.append(f"название: {title}")
        tags = _string_list(item.get("tags"), limit=6)
        if tags:
            parts.append(f"ключевые мотивы: {', '.join(tags)}")
        structure = [
            _text_value(item.get(field), limit=80)
            for field in (
                "incidentClass",
                "causalOrigin",
                "eventScale",
                "resolutionFamily",
            )
        ]
        structure = [value for value in structure if value]
        if structure:
            parts.append(f"структурный каркас: {', '.join(structure)}")
        brief = "; ".join(parts)
        if brief:
            briefs.append(brief)
    return briefs


def _anti_repeat_block(recent_story_events: list[dict[str, Any]] | None) -> str:
    briefs = _story_event_briefs(recent_story_events)
    if not briefs:
        return ""
    lines = "\n".join(f"- {brief}" for brief in briefs)
    return (
        "ANTI_REPEAT: эти события уже происходили. "
        "Используй список только как запрет на повтор, "
        "не как источник новых деталей сюжета. "
        "Не повторяй центральные слова, образы и мотивы из названий и тегов. "
        "Не повторяй структурный каркас последних событий, "
        "даже с другим прилагательным или в другой словоформе. "
        "Не развивай и не комбинируй детали из этого списка; придумай независимое событие.\n"
        f"{lines}"
    )


def _recent_story_directions(
    recent_story_events: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    if not recent_story_events:
        return []
    result: list[dict[str, str]] = []
    for item in recent_story_events[-STORY_DIRECTION_HISTORY_LIMIT:]:
        if not _is_record(item):
            continue
        direction = {
            field: _text_value(item.get(field), limit=80)
            for field in STORY_DIRECTION_FIELDS
        }
        if any(direction.values()):
            result.append(direction)
    return result


def _least_used_choice(
    values: tuple[str, ...],
    *,
    history: list[dict[str, str]],
    field: str,
    rng: random.Random | random.SystemRandom,
) -> str:
    counts = {value: 0 for value in values}
    for item in history:
        value = item.get(field)
        if value in counts:
            counts[value] += 1
    minimum = min(counts.values())
    candidates = [value for value in values if counts[value] == minimum]
    return rng.choice(candidates)


def _weighted_least_used_choice(
    values: tuple[str, ...],
    *,
    history: list[dict[str, str]],
    field: str,
    weights: dict[str, int],
    rng: random.Random | random.SystemRandom,
) -> str:
    counts = {value: 0 for value in values}
    for item in history:
        value = item.get(field)
        if value in counts:
            counts[value] += 1
    scores = {value: counts[value] / weights[value] for value in values}
    minimum = min(scores.values())
    candidates = [value for value in values if scores[value] == minimum]
    return rng.choice(candidates)


def select_background_story_direction(
    recent_story_events: list[dict[str, Any]] | None,
    *,
    current_stats: dict[str, int] | None = None,
    rng: random.Random | random.SystemRandom | None = None,
) -> dict[str, str]:
    history = _recent_story_directions(recent_story_events)
    rng = rng or random.SystemRandom()
    recent_modes = {
        item.get("plotMode")
        for item in history[-STORY_MODE_COOLDOWN:]
        if item.get("plotMode")
    }
    available_modes = tuple(
        mode for mode in STORY_DIRECTION_SPECS if mode not in recent_modes
    ) or tuple(STORY_DIRECTION_SPECS)
    plot_mode = _least_used_choice(
        available_modes,
        history=history,
        field="plotMode",
        rng=rng,
    )
    spec = STORY_DIRECTION_SPECS[plot_mode]
    resolution_mode = _least_used_choice(
        spec["resolutions"], history=history, field="resolutionMode", rng=rng
    )
    incident_candidates = STORY_INCIDENTS_BY_MODE[plot_mode]
    classified_history = [item for item in history if item.get("incidentClass")]
    recent_incidents = {
        item.get("incidentClass")
        for item in classified_history[-STORY_INCIDENT_PUZZLE_COOLDOWN:]
    }
    if (
        len(classified_history) < STORY_INCIDENT_PUZZLE_COOLDOWN
        or "puzzle_discovery" in recent_incidents
    ):
        incident_candidates = tuple(
            value for value in incident_candidates if value != "puzzle_discovery"
        )
    incident_class = _least_used_choice(
        incident_candidates,
        history=history,
        field="incidentClass",
        rng=rng,
    )
    causal_origin = _least_used_choice(
        STORY_CAUSAL_ORIGINS_BY_INCIDENT[incident_class],
        history=history,
        field="causalOrigin",
        rng=rng,
    )
    event_scale = _least_used_choice(
        tuple(STORY_EVENT_SCALE_INSTRUCTIONS),
        history=history,
        field="eventScale",
        rng=rng,
    )
    available_valences = tuple(STORY_VALENCE_WEIGHTS)
    if plot_mode == "peaceful_change" or resolution_mode == "celebration_or_rest":
        available_valences = tuple(
            value for value in available_valences if value != "negative"
        )
    if current_stats:
        if all(value >= 100 for value in current_stats.values()):
            available_valences = tuple(
                value for value in available_valences if value != "positive"
            )
        if all(value <= 0 for value in current_stats.values()):
            available_valences = tuple(
                value for value in available_valences if value != "negative"
            )
    return {
        "plotMode": plot_mode,
        "incidentClass": incident_class,
        "causalOrigin": causal_origin,
        "eventScale": event_scale,
        "settingClass": _least_used_choice(
            spec["settings"], history=history, field="settingClass", rng=rng
        ),
        "oppositionClass": _least_used_choice(
            spec["oppositions"], history=history, field="oppositionClass", rng=rng
        ),
        "resolutionMode": resolution_mode,
        "resolutionFamily": STORY_RESOLUTION_FAMILIES[resolution_mode],
        "valenceTarget": _weighted_least_used_choice(
            available_valences,
            history=history,
            field="valenceTarget",
            weights=STORY_VALENCE_WEIGHTS,
            rng=rng,
        ),
    }


def story_direction_block(
    direction: dict[str, str],
    *,
    enforce_single_valence: bool = True,
) -> str:
    plot_mode = direction["plotMode"]
    incident_class = direction["incidentClass"]
    causal_origin = direction["causalOrigin"]
    event_scale = direction["eventScale"]
    setting_class = direction["settingClass"]
    opposition_class = direction["oppositionClass"]
    resolution_mode = direction["resolutionMode"]
    valence_target = direction["valenceTarget"]
    valence_instruction = STORY_VALENCE_INSTRUCTIONS[valence_target]
    valence_rules = (
        "Значение valence в JSON должно точно совпасть с valenceTarget. Для положительного "
        "события каждый statImpact положительный, для отрицательного — отрицательный, "
        "для нейтрального statImpacts пуст. "
        if enforce_single_valence
        else (
            "valenceTarget задаёт общий эмоциональный итог всей арки; отдельные части могут "
            "иметь разную valence, если их последствия прямо показаны в тексте. "
        )
    )
    if not enforce_single_valence:
        valence_instruction = {
            "positive": "общий итог арки заметно улучшает положение питомца",
            "negative": "общий итог арки заметно ухудшает положение питомца",
            "mixed": "общий итог арки сочетает содержательный выигрыш и цену",
            "neutral": "плюсы и минусы арки уравновешены без доминирующего изменения",
        }[valence_target]
    return (
        "STORY_DIRECTION: обязательное структурное направление этой истории. "
        "Это не готовый сюжет; конкретные события придумай самостоятельно.\n"
        f"- plotMode={plot_mode}: {STORY_DIRECTION_SPECS[plot_mode]['instruction']}\n"
        f"- incidentClass={incident_class}: {STORY_INCIDENT_INSTRUCTIONS[incident_class]}.\n"
        f"- causalOrigin={causal_origin}: "
        f"{STORY_CAUSAL_ORIGIN_INSTRUCTIONS[causal_origin]}.\n"
        f"- eventScale={event_scale}: {STORY_EVENT_SCALE_INSTRUCTIONS[event_scale]}.\n"
        f"- settingClass={setting_class}: {STORY_SETTING_INSTRUCTIONS[setting_class]}.\n"
        f"- oppositionClass={opposition_class}: "
        f"{STORY_OPPOSITION_INSTRUCTIONS[opposition_class]}.\n"
        f"- resolutionMode={resolution_mode}: "
        f"{STORY_RESOLUTION_INSTRUCTIONS[resolution_mode]}.\n"
        f"- resolutionFamily={direction['resolutionFamily']}.\n"
        f"- valenceTarget={valence_target}: {valence_instruction}.\n"
        f"{valence_rules}Снижение hunger объясняй пропущенной едой, "
        "потерей еды или долгой нагрузкой без возможности поесть; повышение hunger — едой. "
        "Energy меняется от травмы, болезни, лечения, отдыха или восстановления, happiness — "
        "от эмоционального результата. Не упоминай автоматически каждую текущую травму и "
        "каждый ранее полученный предмет. Существующую травму включай только если история "
        "прямо посвящена её лечению либо она действительно меняет центральное решение или исход. "
        "Используй не более одного старого предмета и только когда без него не работает "
        "причинная линия.\n"
        "Не заменяй выбранное направление привычной схемой «герой случайно попал в ловушку, "
        "выбрался и потерял вещь/получил травму». Событие должно менять положение, план, "
        "безопасность, ресурс или отношения участников. Незначительная находка, отметка, щель, "
        "рисунок, травинка, шёпот или маленький ритуал не могут быть центром истории."
    )


def _story_direction_block(direction: dict[str, str]) -> str:
    return story_direction_block(direction)


def _background_story_schema_for_direction(direction: dict[str, str]) -> dict[str, Any]:
    schema = copy.deepcopy(BACKGROUND_STORY_SCHEMA)
    valence_target = direction["valenceTarget"]
    schema["properties"]["valence"]["enum"] = [valence_target]
    stat_impacts = schema["properties"]["statImpacts"]
    amount = stat_impacts["items"]["properties"]["amount"]
    if valence_target == "positive":
        stat_impacts["minItems"] = 1
        amount["minimum"] = 1
        amount["maximum"] = STORY_STAT_MAX_SINGLE_DAMAGE
    elif valence_target == "negative":
        stat_impacts["minItems"] = 1
        amount["minimum"] = -STORY_STAT_MAX_SINGLE_DAMAGE
        amount["maximum"] = -1
    elif valence_target == "neutral":
        stat_impacts["maxItems"] = 0
    return schema


def _state_params_brief(pet: LocalPetChatContext) -> dict[str, Any]:
    labels = state_param_labels(
        hunger=pet.stats.hunger,
        happiness=pet.stats.happiness,
        energy=pet.stats.energy,
    )
    return {
        "usageRule": state_param_usage_rule(),
        "scale": "0–100; больше — лучше",
        "голод": {"value": pet.stats.hunger, "label": labels["hunger"]},
        "настроение": {"value": pet.stats.happiness, "label": labels["happiness"]},
        "здоровье": {"value": pet.stats.energy, "label": labels["energy"]},
    }


def _background_context_modes() -> dict[str, str]:
    modes = {
        source: context_source_mode("backgroundStory", source) for source in CONTEXT_SOURCE_KEYS
    }
    # Previous generated pet stories are conversation memory only. Feeding them
    # back into /story makes the story generator repeat its own past outputs.
    modes["storyOverlay"] = "disabled"
    return modes


def _background_context_source_enabled(
    surface: str,
    source: str,
    *,
    router_enabled: bool | None = None,
    auto_default: bool = False,
) -> bool:
    if surface == "backgroundStory" and source == "storyOverlay":
        return False
    return context_source_enabled(
        surface,
        source,
        router_enabled=router_enabled,
        auto_default=auto_default,
    )


def _background_context_plan_from_routing(
    *,
    modes: dict[str, str] | None = None,
    routing: ContextRoutingDecision | None,
) -> ContextPlan:
    return build_context_plan(
        surface="backgroundStory",
        modes=modes or _background_context_modes(),
        routing=routing,
        source_enabled=_background_context_source_enabled,
    )


def _background_routing_payload(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None,
    history: list[LocalChatHistoryItem] | None,
    recent_replies: list[str] | None,
    now_iso: str | None,
    timezone: str | None,
) -> dict[str, Any]:
    pet_payload: dict[str, Any] = {
        "name": _background_story_character_name(pet) or None,
        "stage": pet.stage,
    }
    if context_source_enabled("backgroundStory", "stateParams", auto_default=True):
        pet_payload["params"] = _state_params_brief(pet)
    payload = {
        "surface": "backgroundStory",
        "task": "generate_background_story",
        "now": now_iso or _now_iso(),
        "timezone": timezone,
        "pet": pet_payload,
        "sources": context_routing_sources(),
        "memoryBrief": _memory_brief(memory_context) or {},
        "recentChatHistory": _history_brief(history),
    }
    if context_source_mode("backgroundStory", "recentReplies") != "disabled":
        payload["recentReplies"] = _recent_replies_brief(recent_replies)
    return payload


def _background_story_identity_seed(pet: LocalPetChatContext) -> str:
    name = _background_story_character_name(pet)
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    identity = bible.get("identity") if _is_record(bible.get("identity")) else {}
    species = _text_value(identity.get("species") or bible.get("species"), limit=180)
    identity_description = species or _text_value(pet.description, limit=220)
    if name and identity_description:
        return f"{name}: {identity_description}"
    return name or identity_description


def _background_story_character_name(pet: LocalPetChatContext) -> str:
    explicit_name = _text_value(pet.name, limit=80)
    if explicit_name:
        return explicit_name
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    identity = bible.get("identity") if _is_record(bible.get("identity")) else {}
    return _text_value(identity.get("name") or bible.get("name"), limit=80)


def _parse_background_routing_payload(value: str) -> ContextRoutingDecision:
    parsed = _json_record_from_text(value)
    sources = parsed.get("sources") if _is_record(parsed.get("sources")) else {}
    enabled: set[str] = set()
    queries: dict[str, str] = {}
    for source in BACKGROUND_ROUTING_SOURCE_IDS:
        item = sources.get(source)
        source_enabled = False
        query = ""
        if isinstance(item, bool):
            source_enabled = item
        elif _is_record(item):
            source_enabled = bool(item.get("enabled"))
            query = _text_value(item.get("query"), limit=500)
        if source_enabled:
            enabled.add(source)
        if query:
            queries[source] = query
    reason = parsed.get("reason")
    return ContextRoutingDecision(
        surface="backgroundStory",
        enabled_sources=frozenset(enabled),
        queries=queries,
        reason=_text_value(reason) if isinstance(reason, str) else "",
        raw=parsed or {"parseError": True, "raw": value[:1000]},
    )


def _plan_background_story_context(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None,
    history: list[LocalChatHistoryItem] | None,
    recent_replies: list[str] | None,
    now_iso: str | None,
    timezone: str | None,
    client: Any,
    model: str,
    timeout: float,
) -> tuple[ContextPlan, dict[str, Any] | None]:
    modes = _background_context_modes()
    if not router_sources_for_auto_modes(modes):
        return (
            _background_context_plan_from_routing(
                modes=modes,
                routing=ContextRoutingDecision(
                    surface="backgroundStory",
                    reason="no_auto_context_sources",
                    raw={"skipped": True, "sourceModes": modes},
                ),
            ),
            None,
        )
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": context_routing_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    _background_routing_payload(
                        pet=pet,
                        memory_context=memory_context,
                        history=history,
                        recent_replies=recent_replies,
                        now_iso=now_iso,
                        timezone=timezone,
                    ),
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_context_routing",
                "schema": BACKGROUND_STORY_ROUTING_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs("none"),
    }
    prompt_debug = log_chat_completion_prompt("background_story/context_routing", request_kwargs)
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/context_routing", completion)
    return (
        _background_context_plan_from_routing(
            modes=modes,
            routing=_parse_background_routing_payload(
                completion.choices[0].message.content or "{}"
            ),
        ),
        prompt_debug,
    )


def character_dossier_for_background_story(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None = None,
    history: list[LocalChatHistoryItem] | None = None,
    recent_replies: list[str] | None = None,
    now_iso: str | None = None,
    timezone: str | None = None,
    context_plan: ContextPlan | None = None,
    source_flags: dict[str, bool] | None = None,
    include_story_library: bool | None = None,
    story_library_query: str | None = None,
) -> str:
    if context_plan is not None:
        sources = {source: context_plan.includes(source) for source in CONTEXT_SOURCE_KEYS}
        if include_story_library is None:
            include_story_library = context_plan.includes("storyLibrary")
        if story_library_query is None:
            story_library_query = context_plan.query("worldContext")
    else:
        sources = source_flags if source_flags is not None else background_story_source_flags()

    def enabled(source: str) -> bool:
        return sources.get(source, True)

    current_state: dict[str, Any] = {
        "name": _background_story_character_name(pet) or None,
        "stage": pet.stage,
    }
    if enabled("stateParams"):
        current_state["params"] = _state_params_brief(pet)
    dossier: dict[str, Any] = {
        "now": now_iso or _now_iso(),
        "timezone": timezone,
        "identitySeed": _background_story_identity_seed(pet),
        "currentState": current_state,
        "characterCanon": story_character_data(pet),
    }

    if enabled("characterProfile"):
        dossier["identityDescription"] = _text_value(pet.description, limit=300)
    if include_story_library is None:
        include_story_library = context_source_enabled(
            "backgroundStory",
            "storyLibrary",
            auto_default=False,
        )
    if include_story_library:
        dossier["globalStoryBricks"] = _global_story_briefs(
            pet=pet,
            query=story_library_query,
        )
    memory = _memory_brief(memory_context) if enabled("userMemory") else None
    if memory:
        dossier["userMemory"] = memory
    recent_history = _history_brief(history) if enabled("chatHistory") else []
    if recent_history:
        dossier["recentChatHistory"] = recent_history
    recent_reply_brief = _recent_replies_brief(recent_replies) if enabled("recentReplies") else []
    if recent_reply_brief:
        dossier["recentReplies"] = recent_reply_brief

    compact = {key: value for key, value in dossier.items() if value not in (None, "", [], {})}
    return _truncate_text(
        json.dumps(compact, ensure_ascii=False, indent=2, default=str),
        MAX_CHARACTER_DOSSIER_CHARS,
    )


def _json_record_from_text(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text_value(item, limit=40)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= 8:
            break
    return tuple(result)


def _story_stat_damage(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0
    if amount == 0:
        return 0
    return max(1, min(STORY_STAT_MAX_SINGLE_DAMAGE, round(abs(amount))))


def _iter_raw_story_stat_impacts(
    value: Any,
    *,
    legacy: Any = None,
) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    items = [item for item in raw_items if _is_record(item)]
    if items:
        return items
    if not _is_record(legacy):
        return []
    applies = legacy.get("applies") is True and legacy.get("isNegativeOutcome") is True
    stat = _text_value(legacy.get("stat"), limit=40)
    if not applies or stat not in STORY_STAT_KEYS:
        return []
    return [
        {
            "stat": stat,
            "amount": -_story_stat_damage(legacy.get("amount")),
            "reason": legacy.get("reason"),
        }
    ]


def _normalize_story_stat_impacts(
    value: Any,
    *,
    legacy: Any = None,
    valence: str,
) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    seen_stats: set[str] = set()
    total_change = 0
    for raw in _iter_raw_story_stat_impacts(value, legacy=legacy):
        stat = _text_value(raw.get("stat"), limit=40)
        if stat not in STORY_STAT_KEYS or stat in seen_stats:
            continue
        try:
            raw_amount = float(raw.get("amount"))
        except (TypeError, ValueError):
            continue
        magnitude = _story_stat_damage(raw_amount)
        if magnitude <= 0:
            continue
        remaining_total = STORY_STAT_MAX_TOTAL_DAMAGE - total_change
        if remaining_total <= 0:
            break
        applied_magnitude = min(magnitude, remaining_total)
        applied_amount = applied_magnitude if raw_amount > 0 else -applied_magnitude
        result.append(
            {
                "stat": stat,
                "amount": applied_amount,
                "reason": _text_value(raw.get("reason"), limit=280),
            }
        )
        seen_stats.add(stat)
        total_change += applied_magnitude
        if len(result) >= STORY_STAT_MAX_ITEMS:
            break
    return tuple(result)


def _normalize_story_payload(payload: dict[str, Any]) -> BackgroundStoryResult:
    max_story_chars = max(200, background_story_max_story_chars())
    max_rag_chars = max(120, background_story_max_rag_chars())
    fallback_event_type = background_story_default_event_type()

    title = _text_value(payload.get("title"), limit=120) or "Фоновое событие"
    summary = _text_value(payload.get("summary"), limit=360)
    raw_paragraphs = payload.get("storyParagraphs")
    paragraphs = (
        [_text_value(value, limit=220) for value in raw_paragraphs[:3]]
        if isinstance(raw_paragraphs, list)
        else []
    )
    paragraphs = [value for value in paragraphs if value]
    if len(paragraphs) == 3:
        story_text = "\n\n".join(paragraphs)[:max_story_chars].rstrip()
    else:
        # Backward compatibility for stored stories and pre-contract test fixtures.
        story_text = _truncate_text(
            _text_value(payload.get("storyText"), limit=2200),
            max_story_chars,
        )
    event_type = _text_value(payload.get("eventType"), limit=60) or fallback_event_type
    valence = _text_value(payload.get("valence"), limit=20) or "mixed"
    if valence not in {"negative", "neutral", "positive", "mixed"}:
        valence = "mixed"
    tags = _clean_tags(payload.get("tags"))
    rag_text = _truncate_text(_text_value(payload.get("ragText"), limit=1000), max_rag_chars)

    if not summary:
        summary = _truncate_text(story_text, 260) if story_text else title
    if not story_text:
        story_text = summary
    if not rag_text:
        rag_text = summary
    stat_impacts = _normalize_story_stat_impacts(
        payload.get("statImpacts"),
        legacy=payload.get("statImpact"),
        valence=valence,
    )
    stat_validation = {"dropped": False, "reason": ""}

    return BackgroundStoryResult(
        title=title,
        summary=summary,
        story_text=story_text,
        event_type=event_type,
        valence=valence,
        tags=tags,
        rag_text=rag_text,
        story_library_patch=None,
        lite_overlay_patch=None,
        recent_story_event=None,
        prompt_debug=[],
        stat_impacts=stat_impacts,
        stat_impact=stat_impacts[0] if stat_impacts else None,
        stat_validation=stat_validation,
    )


def _clamp_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _aftermath_character_context(pet: LocalPetChatContext) -> str:
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else {}
    payload = {
        "name": _background_story_character_name(pet) or None,
        "description": pet.description,
        "stage": pet.stage,
        "currentState": {
            "name": _background_story_character_name(pet) or None,
            "stage": pet.stage,
            "params": _state_params_brief(pet),
        },
        "identity": _select_record(
            bible.get("identity"),
            ("name", "nickname", "one_liner", "role", "species"),
        ),
        "signature": _text_value(bible.get("signature"), limit=300),
        "species": _text_value(bible.get("species"), limit=200),
        "visual": _select_record(
            bible.get("visual"),
            ("anchors", "colors", "features", "growth_forms", "materials", "proportions"),
        ),
        "innerState": _select_record(
            bible.get("inner_state"),
            ("core_want", "inner_conflict", "fears", "comfort_actions", "drives"),
        ),
        "lore": _select_record(
            lore,
            ("origin", "home", "world", "relationships", "inner_life", "growth_arc"),
        ),
        "world": _select_record(
            bible.get("world"),
            ("habitat", "home", "objects", "relationships", "routines"),
        ),
        "liteOverlay": _clean_context_value(extensions.get("lite_overlay"))
        if _is_record(extensions.get("lite_overlay"))
        else {},
    }
    compact = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    return _truncate_text(
        json.dumps(compact, ensure_ascii=False, indent=2, default=str),
        MAX_AFTERMATH_CONTEXT_CHARS,
    )


def _aftermath_story_payload(result: BackgroundStoryResult) -> str:
    return _truncate_text(
        json.dumps(
            {
                "title": result.title,
                "summary": result.summary,
                "storyText": result.story_text,
                "eventType": result.event_type,
                "valence": result.valence,
                "tags": list(result.tags),
                "statImpacts": list(result.stat_impacts),
                "ragText": result.rag_text,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        MAX_AFTERMATH_CONTEXT_CHARS,
    )


def _status_change_list(value: Any, *, limit: int = 5) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not _is_record(item):
            continue
        entity = _text_value(item.get("entity"), limit=120)
        state = _text_value(item.get("state"), limit=80)
        owner = _text_value(item.get("owner"), limit=120)
        if not entity or not state:
            continue
        result.append({"entity": entity, "state": state, "owner": owner})
        if len(result) >= limit:
            break
    return result


def _recent_story_event_id(*, created_at: str, story: BackgroundStoryResult) -> str:
    seed = f"{created_at}:{story.title}:{story.summary}"
    suffix = re.sub(r"[^0-9a-z]+", "", seed.casefold())[:48]
    return f"evt_{suffix or 'story'}"


def _normalize_recent_story_event(
    value: Any,
    *,
    story: BackgroundStoryResult,
) -> dict[str, Any]:
    item = value if _is_record(value) else {}
    summary = _text_value(item.get("summary"), limit=500) or story.summary
    compact_text = (
        _text_value(item.get("compactText"), limit=500)
        or summary
        or _truncate_text(story.story_text, 500)
    )
    canonical_facts = _string_list(item.get("canonicalFacts"), limit=5)
    actions = _string_list(item.get("actions"), limit=6)
    outcome = _text_value(item.get("outcome"), limit=260)
    if not canonical_facts:
        canonical_facts = [*actions[:4], outcome][:5]
        canonical_facts = [fact for fact in canonical_facts if fact]
    created_at = _now_iso()
    event = {
        "id": _text_value(item.get("id"), limit=120)
        or _recent_story_event_id(created_at=created_at, story=story),
        "title": story.title,
        "summary": summary,
        "compactText": compact_text,
        "eventType": _text_value(item.get("eventType"), limit=60) or story.event_type,
        "valence": story.valence,
        "participants": _string_list(item.get("participants"), limit=6),
        "actions": actions,
        "objects": _string_list(item.get("objects"), limit=6),
        "location": _text_value(item.get("location"), limit=160),
        "outcome": outcome,
        "canonicalFacts": canonical_facts,
        "statusChanges": _status_change_list(item.get("statusChanges"), limit=5),
        "statImpacts": list(story.stat_impacts),
        "tags": list(story.tags),
        "createdAt": created_at,
        "source": "background_story",
    }
    if not event["summary"]:
        event["summary"] = _truncate_text(story.story_text, 500)
    if not event["compactText"]:
        event["compactText"] = event["summary"]
    return {
        key: item_value for key, item_value in event.items() if item_value not in (None, "", [], {})
    }


def _parse_aftermath_extraction_payload(
    raw_content: str,
    *,
    story: BackgroundStoryResult,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    parsed = _json_record_from_text(raw_content)
    raw_facts = parsed.get("facts")

    facts: list[dict[str, Any]] = []
    if isinstance(raw_facts, list):
        for raw_fact in raw_facts:
            if not _is_record(raw_fact):
                continue
            if _clamp_float(raw_fact.get("confidence"), 0.0) < AFTERMATH_CONFIDENCE_THRESHOLD:
                continue
            fact = dict(raw_fact)
            fact["source"] = "background_story_aftermath"
            facts.append(fact)
    patch = overlay_patch_from_extracted_facts(
        facts,
        default_source="background_story_aftermath",
    )
    recent_event = _normalize_recent_story_event(parsed.get("recentEvent"), story=story)
    return patch, recent_event


def _extract_background_story_aftermath_patch(
    *,
    pet: LocalPetChatContext,
    story: BackgroundStoryResult,
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": background_story_aftermath_extraction_system_prompt(),
            },
            {
                "role": "user",
                "content": background_story_aftermath_extraction_user_prompt(
                    {
                        "character_context": _aftermath_character_context(pet),
                        "story_payload": _aftermath_story_payload(story),
                    }
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_aftermath_extraction",
                "schema": BACKGROUND_STORY_AFTERMATH_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(get_settings().openai_chat_reasoning_effort),
    }
    prompt_debug.append(
        log_chat_completion_prompt("background_story/aftermath_extraction", request_kwargs)
    )
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/aftermath_extraction", completion)
    return _parse_aftermath_extraction_payload(
        completion.choices[0].message.content or "{}",
        story=story,
    )


def _has_forbidden_micro_unlock_pattern(
    story_payload: dict[str, Any],
    story_direction: dict[str, str],
) -> bool:
    if story_direction.get("incidentClass") == "puzzle_discovery":
        return False
    paragraphs = story_payload.get("storyParagraphs")
    visible_parts = paragraphs if isinstance(paragraphs, list) else []
    visible_text = " ".join(
        [
            *[str(value) for value in visible_parts],
            str(story_payload.get("storyText") or ""),
        ]
    ).casefold()
    clue_pattern = re.compile(
        r"узор|рисунк|рун|метк|травин|мох|пыл[ьи]|саж|щел[ьи]|плит|ш[её]п|знак"
    )
    unlock_pattern = re.compile(
        r"открыл|проявил|тайн\w*\s+(?:ход|двер)|скрыт\w*\s+(?:ход|двер)|"
        r"служебн\w*\s+ход|обходн\w*\s+(?:ход|лестниц)"
    )
    return bool(clue_pattern.search(visible_text) and unlock_pattern.search(visible_text))


def _check_background_story_coherence(
    *,
    story_payload: dict[str, Any],
    story_direction: dict[str, str],
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> tuple[bool, list[str], str]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": background_story_coherence_check_system_prompt(),
            },
            {
                "role": "user",
                "content": background_story_coherence_check_user_prompt(
                    {
                        "story_direction": _story_direction_block(story_direction),
                        "story_payload": json.dumps(
                            story_payload,
                            ensure_ascii=False,
                            indent=2,
                            default=str,
                        ),
                    }
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_coherence_check",
                "schema": BACKGROUND_STORY_COHERENCE_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs("low"),
    }
    prompt_debug.append(
        log_chat_completion_prompt("background_story/coherence_check", request_kwargs)
    )
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/coherence_check", completion)
    parsed = _json_record_from_text(completion.choices[0].message.content or "{}")
    issues = _string_list(parsed.get("issues"), limit=4)
    retry_instruction = _text_value(parsed.get("retryInstruction"), limit=600)
    coherent = parsed.get("coherent") is not False
    eventful = parsed.get("eventful") is not False
    pattern_class = _text_value(parsed.get("patternClass"), limit=80) or "concrete_incident"
    if _has_forbidden_micro_unlock_pattern(story_payload, story_direction):
        eventful = False
        pattern_class = "micro_clue_unlock"
        issues.append(
            "История повторяет запрещённую схему: маленькая улика или предмет открывает "
            "скрытый проход вместо самостоятельного происшествия."
        )
        retry_instruction = (
            "Замени центральную линию на внешнее происшествие из incidentClass; не используй "
            "знаки, рисунки, травинки, щели, плиты и открывающиеся скрытые проходы."
        )
    accepted = coherent and eventful
    prompt_debug.append(
        {
            "event": "background_story_coherence_result",
            "coherent": coherent,
            "eventful": eventful,
            "patternClass": pattern_class,
            "accepted": accepted,
            "issues": issues,
            "retryInstruction": retry_instruction,
        }
    )
    return accepted, issues, retry_instruction


def generate_background_story(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None = None,
    history: list[LocalChatHistoryItem] | None = None,
    recent_replies: list[str] | None = None,
    recent_story_events: list[dict[str, Any]] | None = None,
    now_iso: str | None = None,
    timezone: str | None = None,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> BackgroundStoryResult:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    context_plan, routing_debug = _plan_background_story_context(
        pet=pet,
        memory_context=memory_context,
        history=history,
        recent_replies=recent_replies,
        now_iso=now_iso,
        timezone=timezone,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    character = character_dossier_for_background_story(
        pet=pet,
        memory_context=memory_context,
        history=history,
        recent_replies=recent_replies,
        now_iso=now_iso,
        timezone=timezone,
        context_plan=context_plan,
    )
    user_content = background_story_user_prompt(
        {
            "character": character,
        }
    )
    story_direction = select_background_story_direction(
        recent_story_events,
        current_stats={
            "hunger": pet.stats.hunger,
            "happiness": pet.stats.happiness,
            "energy": pet.stats.energy,
        },
    )
    user_content = f"{user_content}\n\n{_story_direction_block(story_direction)}"
    anti_repeat = _anti_repeat_block(recent_story_events)
    if anti_repeat:
        user_content = f"{user_content}\n\n{anti_repeat}"
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{background_story_system_prompt()}\n\n{lore_prompt_block('backgroundStory')}"
                ),
            },
            {"role": "user", "content": user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story",
                "schema": _background_story_schema_for_direction(story_direction),
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(background_story_reasoning_effort()),
    }
    prompt_debug = [item for item in (routing_debug,) if item is not None]
    prompt_debug.append(
        {
            "event": "background_story_direction",
            **story_direction,
        }
    )
    prompt_debug.append(log_chat_completion_prompt("background_story/generate", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/generate", completion)
    content = completion.choices[0].message.content or "{}"
    raw_story_payload = _json_record_from_text(content)
    try:
        accepted, issues, retry_instruction = _check_background_story_coherence(
            story_payload=raw_story_payload,
            story_direction=story_direction,
            client=openai_client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
    except Exception as exc:
        logger.exception("background_story_coherence_check failed")
        prompt_debug.append(
            {
                "event": "background_story_coherence_error",
                "error": exc.__class__.__name__,
            }
        )
        micro_unlock = _has_forbidden_micro_unlock_pattern(
            raw_story_payload,
            story_direction,
        )
        accepted = not micro_unlock
        issues = (
            ["Запрещённая схема маленькой улики и открывающегося скрытого прохода."]
            if micro_unlock
            else []
        )
        retry_instruction = "Замени микроголоволомку самостоятельным происшествием."
    if not accepted:
        issue_lines = "\n".join(f"- {issue}" for issue in issues)
        repair_instruction = retry_instruction or (
            "Перепиши историю от первого лица: действие героя должно иметь заранее "
            "показанный понятный механизм, а результат — прямо следовать из действия."
        )
        retry_user_content = (
            f"{user_content}\n\nQUALITY_RETRY: предыдущая версия отклонена редактором. "
            "Создай новый полный JSON этой истории, сохрани STORY_DIRECTION, но исправь "
            "причинность и прозу. Не упоминай редактора в видимом тексте.\n"
            f"Замечания:\n{issue_lines or '- причинность или проза недостаточно ясны'}\n"
            f"Указание:\n{repair_instruction}"
        )
        retry_request_kwargs = {
            **request_kwargs,
            "messages": [
                request_kwargs["messages"][0],
                {"role": "user", "content": retry_user_content},
            ],
        }
        prompt_debug.append(
            log_chat_completion_prompt(
                "background_story/generate_retry",
                retry_request_kwargs,
            )
        )
        retry_completion = openai_client.chat.completions.create(**retry_request_kwargs)
        log_chat_completion_response("background_story/generate_retry", retry_completion)
        raw_story_payload = _json_record_from_text(
            retry_completion.choices[0].message.content or "{}"
        )
        try:
            retry_accepted, _, _ = _check_background_story_coherence(
                story_payload=raw_story_payload,
                story_direction=story_direction,
                client=openai_client,
                model=model,
                timeout=timeout,
                prompt_debug=prompt_debug,
            )
        except Exception as exc:
            logger.exception("background_story_retry_coherence_check failed")
            retry_accepted = not _has_forbidden_micro_unlock_pattern(
                raw_story_payload,
                story_direction,
            )
            prompt_debug.append(
                {
                    "event": "background_story_retry_coherence_error",
                    "error": exc.__class__.__name__,
                    "acceptedByDeterministicFallback": retry_accepted,
                }
            )
        if not retry_accepted:
            raise RuntimeError("BACKGROUND_STORY_QUALITY_REJECTED")
    result = _normalize_story_payload(raw_story_payload)
    prompt_debug.append(
        {
            "event": "background_story_causal_plan",
            "plan": raw_story_payload.get("causalPlan"),
        }
    )
    prompt_debug.append(
        {
            "event": "background_story_stat_impacts",
            "rawStatImpacts": raw_story_payload.get("statImpacts"),
            "legacyStatImpact": raw_story_payload.get("statImpact"),
            "appliedStatImpacts": list(result.stat_impacts),
            "statValidation": result.stat_validation,
            "valence": result.valence,
        }
    )
    try:
        lite_overlay_patch, recent_story_event = _extract_background_story_aftermath_patch(
            pet=pet,
            story=result,
            client=openai_client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
    except Exception as exc:
        logger.exception("background_story_aftermath_extraction failed")
        lite_overlay_patch = None
        recent_story_event = _normalize_recent_story_event(None, story=result)
        prompt_debug.append(
            {
                "event": "background_story_aftermath_fallback",
                "error": exc.__class__.__name__,
            }
        )
    return BackgroundStoryResult(
        title=result.title,
        summary=result.summary,
        story_text=result.story_text,
        event_type=result.event_type,
        valence=result.valence,
        tags=result.tags,
        rag_text=result.rag_text,
        story_library_patch=result.story_library_patch,
        lite_overlay_patch=lite_overlay_patch,
        recent_story_event=recent_story_event,
        prompt_debug=prompt_debug,
        stat_impacts=result.stat_impacts,
        stat_impact=result.stat_impact,
        stat_validation=result.stat_validation,
        plot_mode=story_direction["plotMode"],
        incident_class=story_direction["incidentClass"],
        causal_origin=story_direction["causalOrigin"],
        event_scale=story_direction["eventScale"],
        setting_class=story_direction["settingClass"],
        opposition_class=story_direction["oppositionClass"],
        resolution_mode=story_direction["resolutionMode"],
        resolution_family=story_direction["resolutionFamily"],
        valence_target=story_direction["valenceTarget"],
    )
