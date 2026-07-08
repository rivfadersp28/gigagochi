# Architecture

## Pet Replies

- Backend phrase generation lives in `backend/app/services/pet_reply_engine/lite_generator.py`.
- Chat, proactive and ambient replies are assembled through the same `PhrasePlan` structure: identity, persona contract, optional world context, memory and surface-specific rules.
- Context source resolution is centralized in
  `backend/app/services/pet_reply_engine/context_plan.py`. `ContextPlan` stores
  the surface, runtime source modes, router decision, final included source ids,
  router queries, and debug payload. Visible replies and `/story` both build this
  plan before prompt assembly.
- Before visible chat/proactive/ambient generation, `lite_generator.py` calls a
  `contextRouting` LLM gate configured in `backend/data/speech_runtime.json`
  only when at least one router-controlled source is set to `auto`. The gate
  returns enabled router sources for `worldContext`, `characterProfile`,
  `userMemory`, `chatHistory`, and `recentReplies`; `ContextPlan` then resolves
  final inclusion through the shared `contextSources` matrix.
- `speech_runtime.contextSources.surfaces` is the unified source policy for
  `chat`, `ambient`, `proactive`, `push`, and `backgroundStory`. Each source is
  `disabled`, `auto`, or `always`. The shared source ids are
  `characterProfile`, `stateParams`, `liteOverlay`, `storyLibrary`, `storyOverlay`,
  `userMemory`, `chatHistory`, and `recentReplies`.
- `contextSources.stateParams` controls current mutable pet parameters. Numeric
  hunger/happiness/energy are converted to admin-configured semantic labels in
  `stateLayer.stateParamLabels`; thresholds and optional usage rule live in
  `stateLayer`. Age/stage wording remains in `stateLayer.ageRoleHints`; the
  per-surface age flag is still under `stateLayer.surfaces`. It has no router
  source, so runtime validation and the admin UI allow only off/on modes for
  `stateParams`.
- `backend/app/services/context_assembler.py` no longer decides whether story
  context is needed from keywords. It only retrieves selected `WORLD_CONTEXT`
  bricks when `contextRouting.worldContext` enables it, then returns prompt text
  plus debug metadata.
- Ambient replies use the open `surfacePrompts.idle` field inside the same
  phrase engine. There is no selected dialogue move and no extra `surfaceRules`
  layer; the model can choose a natural micro-moment, observation, check-in, or
  question. Recent idle replies are passed only when `contextRouting.recentReplies`
  enables the anti-repeat context.
- New durable story entities can be extracted after a chat reply by `story_library_extraction` and returned as `debug.storyLibraryPatch`. Frontend applies that patch into the local per-pet story-library overlay.
- Background story events generated from the Telegram bot `/story` command are
  split into durable consequences and one-off event memory. Durable aftermath
  goes to `characterBible.extensions.lite_overlay`. The episode itself is
  stored in the backend push registry as `recentStoryEvents`, returned by
  `/api/push/snapshot` as `recentStoryEventsPatch`, and applied locally to
  `characterBible.extensions.recent_story_events` so normal chat can mention
  past events without making them RAG bricks. `/story` also preserves the
  `contextRouting.worldContext.query` when selecting global stories for the
  background-story dossier.
- The `/story` character dossier uses the same `ContextPlan` / `contextSources`
  matrix as visible replies for optional sources: character profile, semantic
  state params, lite overlay, global story library, user memory, chat history and
  recent replies. It does not consume the per-pet stories overlay: generated
  stories are conversation memory for chat/idle/proactive/push, not source
  material for new `/story` events. Its `currentState` is intentionally minimal
  (`name`, `stage`, optional semantic `params`); descriptive `pet.description`
  belongs to `characterProfile`, not `currentState`. It does not pass raw
  numeric `stats`.
- `/story` receives `recentStoryEvents` only as an `ANTI_REPEAT` block. That
  block is a negative constraint against repeating the same event shape, not
  context to continue or reuse.
- `/story` illustrations use `background_story_service.generate_background_story_image_bytes`.
  Before image generation, the service sends the generated story through a chat
  completion named `background_story_image_scene` with an artist-brief prompt
  that extracts one main visual scene. The final image prompt uses that extracted
  scene, pet identity, and the style template, then calls the shared
  `image_service.generate_image_bytes` path so production follows
  `AI_PROVIDER=openai` / `OPENAI_API_KEY` instead of direct Kandinsky tasks.
- Runtime speech regulator text that used to be hardcoded in the reply engine now lives in
  `backend/data/speech_runtime.json` and is read by
  `backend/app/services/pet_reply_engine/speech_runtime.py`. It covers persona
  contract, memory usage rule, ambient self-prompt, visible reply rules,
  character/user memory extractor prompts, world seeding,
  `WORLD_CONTEXT` prompt framing, unified `contextRouting`, shared
  `contextSources`, and the age plus hunger/happiness/energy `stateLayer` used
  by chat/proactive/ambient identity lines and semantic story params.
- Proactive replies keep their memory-derived reason as a neutral context line
  inside the phrase plan. The old configurable `surfaceRules` layer was removed
  so proactive/ambient behavior is shaped by visible reply rules, state, memory,
  world context and the idle self-prompt only.
- Backend chat/proactive/ambient prompts no longer inject `VOICE_CONTROL` from
  `characterBible.voice` / `dialogue_style`; the identity line and character
  description are the prompt source of voice. `voice_profile.py` remains in the
  codebase but is not on the visible-reply path.
- Generated pets follow a template -> instance contract in frontend local
  storage. `assetSet.characterTemplate` is the cleaned immutable snapshot from
  generation, while `assetSet.characterBible` is the mutable per-pet instance.
  Mutable facts stay in `characterBible.extensions.lite_overlay`; per-pet story
  bricks stay in `characterBible.extensions.story_library_overlay`.
- Frontend character instance normalization strips prompt-scaffolding fields
  (`voice`, `dialogue_style`, `lore.voice`) from `characterBible` and records
  the prompt model version in `extensions.instance`. The original
  `characterTemplate` can still keep the source data.
- Normal frontend user chat turns share
  `frontend/src/lib/localPetChatTurn.ts`. It appends chat history, sends the
  backend request, marks recalled memory as used, and runs post-reply lite-fact
  plus user-memory extraction. UI components apply returned mood/name/overlay
  patches.
- Lite chat can read character JSON for explicit lore questions, but no longer
  exposes `update_character_json`. Stable mutable facts are saved by the
  separate `/api/chat/lite-facts` post-reply extractor.

## Local Admin

- Local speech admin UI lives at `frontend/src/app/admin/speech/page.tsx` and
  `frontend/src/components/admin/SpeechAdmin.tsx`.
- The speech admin has a structured "Настройка" tab for `speech_runtime.json`
  so visible reply rules, idle prompt, context routing, memory extractor prompts,
  and `WORLD_CONTEXT` templates can be edited without touching Python code. Other
  managed datasets remain raw JSON/JSONL editors.
- The UI talks to `backend/app/routers/local_admin.py` at `/api/admin/speech`.
  The router is local-dev only: it requires `ALLOW_DEV_TMA_AUTH=true` and a
  local client host.
- Managed files are defined in `backend/app/services/local_admin_store.py` and
  include `speech_runtime.json`, story datasets, age speech examples, world
  descriptions, and the character-bible template.
- Publishing those local admin data edits is a separate opt-in flow. The
  frontend calls `/api/admin/speech/publish`, backed by
  `backend/app/services/local_admin_publish.py`; the job saves dirty drafts,
  commits only managed `backend/data` paths to GitHub, runs the Hetzner compose
  rebuild over SSH, and exposes polling logs/status back to the admin UI.
- With `ADMIN_SYNC_FROM_SERVER_ENABLED=true`, `GET /api/admin/speech` first
  reads the current Git commit from Hetzner over SSH and refreshes the local
  managed data files from that commit before returning the manifest.
- The admin manifest also exposes a dialogue influence map with prompt
  modifiers and RAG/memory/dataset collections, including runtime-only sources
  such as character profile overlays, recent history, proactive reason, tool
  definitions, reply limits, `contextRouting`, and the shared `contextSources`
  matrix.
- The `/admin/speech` UI edits local managed data and shows separate `Save` and
  `Deploy` actions. Local diffs from the server are a normal `local_dirty` state,
  not an error; deploy is the explicit production apply step.
- The "Копилки" matrix hides source/surface cells that have no runtime path:
  `chatHistory` is meaningful only for Chat and Story, while `recentReplies` is
  meaningful only for Idle and Story. `Параметры` (`stateParams`) validates and
  displays only `выкл` / `вкл`, because `auto` has no runtime routing signal.
