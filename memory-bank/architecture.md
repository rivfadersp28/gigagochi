# Architecture

## Pet Replies

- Backend phrase generation lives in `backend/app/services/pet_reply_engine/lite_generator.py`.
- Chat, proactive and ambient replies are assembled through the same `PhrasePlan` structure: identity, persona contract, optional world context, memory and surface-specific rules.
- Before visible chat/proactive/ambient generation, `lite_generator.py` calls a
  `contextRouting` LLM gate configured in `backend/data/speech_runtime.json`.
  The gate returns enabled sources for `worldContext`, `characterProfile`,
  `userMemory`, and `recentReplies`.
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
- Runtime speech regulator text that used to be hardcoded in the reply engine now lives in
  `backend/data/speech_runtime.json` and is read by
  `backend/app/services/pet_reply_engine/speech_runtime.py`. It covers persona
  contract, memory usage rule, ambient self-prompt, visible reply rules,
  character/user memory extractor prompts, world seeding,
  `WORLD_CONTEXT` prompt framing, unified `contextRouting`, and the visible
  age/mood/hunger/energy `stateLayer` used by chat/proactive/ambient identity
  lines.
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
  definitions, reply limits and `contextRouting`.
- The `/admin/speech` UI edits local managed data and shows separate `Save` and
  `Deploy` actions. Local diffs from the server are a normal `local_dirty` state,
  not an error; deploy is the explicit production apply step.
