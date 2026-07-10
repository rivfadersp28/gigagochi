# Architecture

## Persistence

- The active MVP has no relational database dependency. Pet/chat/memory state is
  local to the frontend origin, Telegram delivery state uses the locked JSON
  registry, and generated assets plus push state use Docker volumes. Local and
  production Compose files therefore do not start PostgreSQL.

## Backend Jobs and Errors

- Backend phrase generation lives in `backend/app/services/pet_reply_engine/lite_generator.py`.
- Async pet creation jobs live in
  `backend/app/services/generation_job_service.py`. The TMA router owns only
  HTTP/auth adaptation and injects image generation, video generation, response
  building, and failure mapping callbacks. Image and video stages use separate
  bounded executors; the service is created lazily and shut down from the
  FastAPI lifespan.
- AI/provider exception inspection, public error shaping and failure-file
  logging live in `backend/app/services/ai_error_service.py`. Routers select the
  user-facing operation message but do not parse provider payloads or write log
  files themselves.

## Frontend Interaction Primitives

- Modal focus behavior uses Radix primitives. `DebugPanel` is a controlled
  Radix Dialog and destructive dashboard actions use `ConfirmActionDialog`
  backed by Radix AlertDialog; Escape handling, focus trapping and focus return
  are not implemented by hand.
- Frontend checks include ESLint, TypeScript and Vitest. Initial component and
  domain tests cover the destructive confirmation contract plus local pet stat
  decay and partial server stat patches.
- Pet stat rules are isolated in `frontend/src/lib/localPetStats.ts`: clamping,
  independent ticks, offline decay, stage/mood derivation, server patches and
  interaction updates. `localPetStorage.ts` re-exports the public functions for
  compatibility but owns only persistence/migration and non-stat overlays.
- Dashboard browser effects are isolated from `PetDashboard.tsx`:
  `useConversationKeyboardOffset` owns Visual Viewport keyboard positioning and
  `usePetPushSnapshotSync` owns throttled server snapshot reconciliation.
- Speech admin orchestration stays in `SpeechAdmin.tsx` (load, validate, save,
  deploy and polling); JSON path manipulation and the runtime/tone/template/raw
  editors live in `SpeechAdminEditors.tsx`.

## API Contracts

- FastAPI's Pydantic schemas are the source of truth for shared HTTP contracts.
  `backend/scripts/export_openapi.py` exports `frontend/openapi.json`, and
  `openapi-typescript` generates `frontend/src/lib/generated/openapi.d.ts`.
  Backend and frontend checks fail when either generated artifact is stale.
- `frontend/src/lib/apiTransport.ts` owns fetch, safe public errors and malformed
  JSON handling. `apiContracts.ts` validates successful payloads at runtime and
  normalizes nullable wire fields into the frontend domain before `api.ts`
  applies pet-specific request/response mapping.
- Local speech-admin routes also expose explicit Pydantic response models.
  `adminSpeechContracts.ts` derives their TypeScript types from OpenAPI and
  validates manifest/save/publish payloads before admin UI state sees them.

## Pet Replies

- Legacy LLM user-memory extraction and consolidation are isolated in
  `backend/app/services/pet_reply_engine/memory_operations.py`: JSON schemas,
  operation normalization, prompt assembly and provider calls live there.
  `lite_generator.py` owns visible replies, context routing and lite/story facts.
- Deterministic recent-story tokenization, Russian stemming, event selection
  and prompt-block formatting live in
  `backend/app/services/pet_reply_engine/recent_events.py`; the reply engine and
  lite-fact conflict filters consume that shared event policy.
- Chat, proactive and ambient replies are assembled through the same `PhrasePlan`
  structure: identity, persona contract, optional dialogue-memory episodes and
  surface-specific rules.
- Chat, proactive, ambient and push visible replies use a structured
  `visible_pet_reply` JSON schema response contract. The model returns
  `reply`, `faceHint` and `moodHint`; backend normalization clamps the visible
  reply, validates hints, records `structuredReplyDebug`, and returns a short
  safe fallback instead of parsing legacy `THOUGHT:` / `FACE:` text lines when
  the response is invalid.
- Context source resolution is centralized in
  `backend/app/services/pet_reply_engine/context_plan.py`. `ContextPlan` stores
  the surface, runtime source modes, router decision, final included source ids,
  router queries, and debug payload. Visible replies and `/story` both build this
  plan before prompt assembly.
- Visible context routing is deterministic for already-selected `userMemory`,
  recent `chatHistory`, and ambient `recentReplies`; these sources no longer
  spend an extra LLM call. The `contextRouting` LLM gate remains available only
  for genuinely semantic auto sources such as future world/profile routing.
- `speech_runtime.contextSources.surfaces` is the unified source policy for
  `chat`, `ambient`, `proactive`, `push`, and `backgroundStory`. Each source is
  `disabled`, `auto`, or `always`. The shared source ids are
  `characterProfile`, `stateParams`, `liteOverlay`, `storyLibrary`, `storyOverlay`,
  `recentEvents`, `userMemory`, `chatHistory`, and `recentReplies`.
- `contextSources.stateParams` controls current mutable pet parameters. Numeric
  hunger/happiness/energy are converted to admin-configured semantic labels in
  `stateLayer.stateParamLabels`; thresholds and optional usage rule live in
  `stateLayer`. Age/stage wording remains in `stateLayer.ageRoleHints`; the
  per-surface age flag is still under `stateLayer.surfaces`. It has no router
  source, so runtime validation and the admin UI allow only off/on modes for
  `stateParams`.
- Mutable pet stats use three internal keys: `hunger`, `happiness`, and legacy
  `energy` (shown to users as health). Frontend local storage and backend push
  snapshots track `lastStatTickAt` per key; a stat decays from full to zero over
  six hours, and partial server `statsPatch` updates only the affected keys.
- `backend/app/services/context_assembler.py` can still retrieve selected
  `WORLD_CONTEXT` bricks when a surface enables `storyLibrary`, but the current
  runtime disables `storyLibrary` for chat, ambient, proactive, push and
  backgroundStory so global world bricks are not an active visible-reply source.
- Ambient replies use the open `surfacePrompts.idle` field inside the same
  phrase engine. There is no selected dialogue move and no extra `surfaceRules`
  layer; the model can choose a natural micro-moment, observation, check-in, or
  question. Recent idle replies are passed only when `contextRouting.recentReplies`
  enables the anti-repeat context.
- Chat no longer runs LLM extraction for durable story entities or user facts
  after every reply. The active lightweight user-memory path is deterministic:
  `frontend/src/lib/localPetDeterministicMemory.ts` captures obvious phrases
  such as "меня зовут", "я люблю", "запомни" and "не шути про" into
  localStorage memory. `localPetMemoryRecall.ts` selectively sends up to five
  relevant memory items plus older dialogue episodes; backend
  `_memory_context_block` includes selected `relevantMemories`, summary/profile
  and episodes when the shared context matrix enables `userMemory`.
- Background story events are generated by the backend Telegram scheduler for
  reachable pet snapshots every six hours per character; `/story` remains only
  a manual fallback path. Durable aftermath goes to
  `characterBible.extensions.lite_overlay`. The episode itself is stored in the
  backend push registry as `recentStoryEvents`, returned by `/api/push/snapshot`
  as `recentStoryEventsPatch`, and applied locally to
  `characterBible.extensions.recent_story_events` so normal chat can mention
  past events without making them global world bricks. Story generation returns
  `statImpacts[]` directly; backend validation caps it to at most two negative
  impacts, max 25 per stat and max 35 total. Story delivery includes a
  debug stat footer built from the actual applied `statsDelta` (`здоровье`,
  `голод`, `настроение`) and shows only changed stats or `без изменений`. Server
  stat changes sync back through `/api/push/snapshot` partial `statsPatch`.
- Telegram Bot API transport is isolated in
  `backend/app/services/telegram_client.py`; neither push orchestration nor the
  polling loop owns HTTP request formatting. Runtime `/story` work is submitted
  by `app.bot` to a bounded worker pool so long AI/image generation does not
  block `getUpdates` polling.
- The MVP push registry remains JSON-backed, but all reads and mutations go
  through `backend/app/services/telegram_push_store.py`. It uses an advisory
  file lock shared by backend and bot processes, unique temporary files plus
  `os.replace`, and transactional record updaters. Invalid JSON fails loudly
  instead of being treated as an empty registry.
  `/story` also preserves the `contextRouting.worldContext.query` when selecting
  global stories for the background-story dossier.
- Chat has a lightweight deterministic `recentEvents` source for
  `characterBible.extensions.recent_story_events`. It is not an LLM router
  source: runtime mode `auto` runs a token/status matcher and injects a
  `RECENT_EVENTS` canonical block above `WORLD_CONTEXT` only when the user asks
  about recent events, objects, participants, or unresolved status. The same
  relevant recent events are passed to lite-fact extraction, and a deterministic
  post-filter drops extracted durable facts that contradict recent event
  canonical facts or status changes.
- The `/story` character dossier uses the same `ContextPlan` / `contextSources`
  matrix as visible replies for optional sources: character profile, semantic
  state params, lite overlay, global story library, dialogue-memory episodes,
  chat history and recent replies. Current runtime disables character profile,
  lite overlay, global story library, dialogue-memory episodes and recent
  replies for backgroundStory; chat history remains the active optional
  continuity source. The dossier always includes a compact `identitySeed`
  (`name` plus raw `pet.description`) so the story knows who the hero is without
  enabling the full character profile. It does not consume the per-pet stories
  overlay: generated stories are conversation memory for chat/idle/proactive/push,
  not source material for new `/story` events. Its `currentState` is intentionally
  minimal (`name`, `stage`, optional semantic `params`) and does not pass raw
  numeric `stats`.
- `/story` receives `recentStoryEvents` only as an `ANTI_REPEAT` block. That
  block is a negative constraint against repeating the same event shape, not
  context to continue or reuse.
- `/story` illustrations use `background_story_service.generate_background_story_image_bytes`.
  Before image generation, the service sends the generated story through a chat
  completion named `background_story_image_scene` with an artist-brief prompt
  that extracts one compact visual scene. The final image prompt contains only
  that scene, exact-reference preservation rules and the same
  `VISUAL_CHARACTER_STYLE` block used inside pet creation. It requires the
  current stage/mood asset as an input reference to the shared
  `image_service.generate_image_bytes` path. Direct OpenAI generation downloads
  that reference and uses `images.edit`; OpenRouter receives it as
  `input_references`. Missing references produce the existing text-only story
  fallback rather than a newly invented character design.
- Background-story aftermath extraction persists the structured episode in
  `recentStoryEvents` and durable consequences in `extensions.lite_overlay`.
  Story stat changes are signed: negative amounts damage state and positive
  amounts restore it only when recovery is explicit in the story text.
- Runtime speech regulator text that used to be hardcoded in the reply engine now lives in
  `backend/data/speech_runtime.json` and is read by
  `backend/app/services/pet_reply_engine/speech_runtime.py`. It covers persona
  contract, memory usage rule, ambient self-prompt, visible reply rules,
  legacy character/user memory extractor prompts, world seeding,
  `WORLD_CONTEXT` prompt framing, unified `contextRouting`, shared
  `contextSources`, and the age plus hunger/happiness/energy `stateLayer` used
  by chat/proactive/ambient identity lines and semantic story params.
- Global generation profile now lives in `backend/data/tone_runtime.json` and
  is read by `backend/app/services/tone_runtime.py`. The active preset exposes
  only `label`, `setting` and `toneOfVoice`; model-facing prompt blocks print
  only `setting` and `tone`. The current active setting is a multi-genre handle:
  `Dark Fairy Tale, Folk Fantasy, Fantasy Adventure`.
  `GENERATION_PROFILE` blocks are injected into visible replies,
  `WORLD_CONTEXT`, `/story` generation and illustration prompts, travel
  full-story, storyboard and image prompts.
  Travel full-story generation no longer injects `story_constructor` bricks; it
  uses the selected structural plot brief plus the same short generation profile.
  Character-bible generation is not used for new pets. Legacy factual
  extractors remain in code/admin for compatibility but are not called by the
  active frontend chat turn. Age speech examples are archival admin data and are
  not injected into visible reply prompts.
- Proactive replies keep their dialogue-derived reason as a neutral context line
  inside the phrase plan. The old configurable `surfaceRules` layer was removed
  so proactive/ambient behavior is shaped by visible reply rules, state,
  dialogue-memory episodes and the idle self-prompt only.
- Backend chat/proactive/ambient prompts use a compact core character capsule;
  the larger raw `CHARACTER_PROFILE` and selectively matched `liteOverlay` facts
  remain controlled separately by the context-source policy. The identity line
  uses the display name, or the raw pet description when the name is missing.
  Baby stage only rewrites it as `маленький/маленькая {identity}` instead of
  injecting age examples. All visible surfaces share one concise speech shape
  from `identityTemplate`. Chat, idle, proactive and push share the isolated
  `visibleReply` runtime: `gpt-5.4-mini`, reasoning `high`, and the mechanical
  `maxChars` cap (`120` currently). Background stories, travel, memory/extractor
  passes and image-scene preparation continue using their existing global/task
  model settings. Normal chat only attaches `update_pet_name` for messages with
  an explicit rename signal; that rare tool path uses the same phrase model with
  reasoning omitted because Chat Completions rejects function tools plus
  reasoning for `gpt-5.4-mini`. Visible text is contracted as words spoken aloud in first
  person, not an authorial action caption. The full global `lore_runtime` world
  block is not injected into ordinary visible replies; they receive only the
  compact `world.dialogueVocabulary` noun palette, while richer world material
  belongs to explicit world-context paths. Generic small-talk resets omit old
  chat history, while actual
  continuations keep it. Ambient idle omits chat history and uses recent idle
  replies only for anti-repeat. Each idle generation receives one randomly
  selected, admin-editable `ambientDialogueImpulses` item rather than the whole
  intent catalog. The frontend sends a dedicated ambient memory context with
  the user name prioritized; backend filtering keeps only soft kinds such as
  user facts, preferences and relationships, excluding deadlines/events and
  summary/profile text. The context router receives only minimal pet state
  (`name`, `stage`, `mood`), not raw `pet.description`.
- Generated pets follow a template -> instance contract in frontend local
  storage for legacy pets only. New pet asset generation does not call
  `create_character_bible` and returns no `characterBible`; image prompts use the
  user's raw character description as the visual seed plus the global
  `VISUAL_STYLE_FRAME` from `backend/app/prompts/style_direction.py`.
  `VISUAL_STYLE_FRAME` combines the reusable quiet melancholic collectible
  designer-toy `VISUAL_CHARACTER_STYLE` with sprite-only studio/white-background
  presentation rules. Story illustrations reuse the character-style block but
  replace the sprite presentation with narrative composition. The active visual pipeline first generates a
  standalone character from `{user_description}` plus `VISUAL_STYLE_FRAME`, then
  sends that character and `backend/static/backgrounds/pet-generation-forest.png`
  to a multi-image edit prompt: `Добавь персонажа с первой картинки на вторую`.
  The raw composed image is center-cropped/resized to `720x1280` before saving as
  `teen-idle.png`; that exact PNG is also sent as the `first_frame` to OpenRouter
  Videos API with `OPENROUTER_VIDEO_MODEL` (default `bytedance/seedance-2.0`),
  `resolution=720p`, `aspect_ratio=9:16`, `duration=4`, `generate_audio=false`,
  and a locked-camera blink-only prompt. The saved public `assetSet.images` point
  to the normalized scene PNG and `assetSet.videoUrl` points to the generated
  mp4. As soon as that required MP4 is saved, the running job exposes its base
  `result` and the frontend creates the pet and enters the dashboard. The same
  job then generates `teen-sad.png` from the exact composed idle scene and
  `teen-sad.mp4` from that sad frame in the background, moving through
  `generating_sad_image` and `generating_sad_video`. The job id and background
  phase are persisted with the frontend asset set, polled after navigation, and
  merged atomically only when both sad assets are ready. Background failure is
  recorded without failing or removing the already usable pet. Image generation
  and video polling use separate configurable thread pools (defaults: three image
  workers and four video workers), so slow video polling does not occupy an image
  worker. The dashboard renders the selected mp4 as the full-height background with the same
  normalized image as poster/fallback; there is no separate centered pet sprite,
  blink overlay, tap animation, or background removal step on the active path.
  If any of hunger, happiness, or energy is strictly below 30, the dashboard uses
  the sad image/video pair when available. Debug UI can force that visual pair
  without mutating stats or mood and shows the two-stage background progress.
  Legacy asset sets without a generation job probe deterministic sibling files
  `teen-sad.png` and `teen-sad.mp4` once on dashboard mount; this lets one-off
  production backfills become visible without rewriting browser localStorage.
  Per-pet story events stay in
  `characterBible.extensions.recent_story_events` for old pets when present, but
  chat canon should come from history/memory rather than `lite_overlay`.
- Frontend character instance normalization strips prompt-scaffolding fields
  (`voice`, `dialogue_style`, `lore.voice`) from `characterBible` and records
  the prompt model version in `extensions.instance`. The original
  `characterTemplate` can still keep the source data.
- Normal frontend user chat turns share
  `frontend/src/lib/localPetChatTurn.ts`. A visible ambient/proactive hook is
  appended as a real pet history message when the user answers it, so the next
  request receives the exact causal turn without regex classification. Chat
  prompt assembly keeps the latest eight complete messages instead of dropping
  prior pet replies.
- After the visible reply, `localPetChatTurn.ts` runs user-memory and character-
  fact extraction in the background. `/api/chat/memory-extract`,
  `/api/chat/memory-consolidate`, and `/api/chat/lite-facts` call the existing
  structured extractors; results update local user memory and
  `extensions.lite_overlay` without delaying the visible answer. Deterministic
  extraction still handles explicit names, preferences, boundaries, and
  `запомни` immediately.
- Chat has no prompt-side anti-repeat block. Repetition is preserved when it is
  needed for conversational cohesion. Ambient keeps only a narrow exact-repeat
  reminder and can read the last four chat messages as optional conversational
  context; deadline/user-memory blocks remain disabled for ambient.
- `lite_overlay` is retrieved into chat only when a stored fact has lexical or
  Russian-stem overlap with the current message, with a maximum of three facts.
  The whole overlay is never pasted into every prompt.
- Post-reply extraction rejects a newly invented ability, title, profession, or
  magical skill unless the fact is supported by the character capsule. A single
  assistant reply is not sufficient evidence for durable character canon.
- New pets may start without a generated `characterBible`; frontend overlay and
  recent-story patch application lazily creates a minimal mutable
  `characterBible.extensions` container so dialogue/story memory still works.
- Visible reply prompts no longer inject the global `tone_runtime` setting and
  no longer duplicate JSON-schema limits/enums in prose. Structured response
  validation remains enforced by `response_format`.
- API `debug` payloads are returned only when `includeDebug=true` and
  `ALLOW_DEV_TMA_AUTH=true`; production Telegram clients cannot request system
  prompts or prompt-context diagnostics. Server prompt
  logs contain hashes, sizes, model and schema metadata by default; full prompt
  text/stdout requires `AI_PROMPT_LOG_FULL=true`.
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
  include `speech_runtime.json`, `tone_runtime.json`, story datasets, age speech
  examples, world descriptions, and the character-bible template. New character
  creation currently uses a model-only content path: a short `SETTING_HINT`,
  schema and template rules shape the output, but world-description datasets,
  few-shot habitat anchors and random lore seed fragments are not injected into
  the character bible prompt.
- Publishing those local admin data edits is a separate opt-in flow. The
  frontend calls `/api/admin/speech/publish`, backed by
  `backend/app/services/local_admin_publish.py`; the job saves dirty drafts,
  commits only managed `backend/data` paths to GitHub, runs a Hetzner data-only
  deploy over SSH (`up -d --no-build --force-recreate backend bot`), and exposes polling
  logs/status back to the admin UI. Production compose bind-mounts individual
  managed `./backend/data` files/directories into backend and bot containers as
  read-only mounts, while `push_data` remains a separate writable
  `/app/data/push` volume.
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
- `/admin/speech` no longer exposes manual Telegram push status or send
  controls. Story delivery is owned by the backend background-story scheduler.
- The "Копилки" matrix hides source/surface cells that have no runtime path:
  `chatHistory` is meaningful only for Chat and Story, while `recentReplies` is
  meaningful only for Idle and Story. `Параметры` (`stateParams`) validates and
  displays only `выкл` / `вкл`, because `auto` has no runtime routing signal.
