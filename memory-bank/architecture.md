# Architecture

## Pet Replies

- Backend phrase generation lives in `backend/app/services/pet_reply_engine/lite_generator.py`.
- Chat, proactive and ambient replies are assembled through the same `PhrasePlan` structure: identity, persona contract, optional voice block, optional world context, memory and surface-specific rules.
- `backend/app/services/context_assembler.py` decides whether story context is needed before the model call. It returns selected `WORLD_CONTEXT` bricks plus debug metadata instead of embedding the full story dataset.
- Story retrieval is heuristic-gated by story-sphere signals. Generic small talk should not retrieve world bricks.
- Tone of voice is rendered by `backend/app/services/pet_reply_engine/voice_profile.py`. It is intended to change speech form only, not facts, answer meaning or selected story bricks.
- Ambient replies use `IDLE_DIALOGUE_ENGINE` inside the same phrase engine. They are expected to address the owner, ask questions or invite dialogue, with recent idle replies used as anti-repeat context.
- New durable story entities can be extracted after a chat reply by `story_library_extraction` and returned as `debug.storyLibraryPatch`. Frontend applies that patch into the local per-pet story-library overlay.
- Runtime speech regulator text that used to be hardcoded in the reply engine now lives in
  `backend/data/speech_runtime.json` and is read by
  `backend/app/services/pet_reply_engine/speech_runtime.py`. It covers persona
  contract, memory usage rule, ambient dialogue moves/examples, surface rules,
  and `WORLD_CONTEXT` prompt framing.
- Generated pets follow a template -> instance contract in frontend local
  storage. `assetSet.characterTemplate` is the cleaned immutable snapshot from
  generation, while `assetSet.characterBible` is the mutable per-pet instance.
  Mutable facts stay in `characterBible.extensions.lite_overlay`; per-pet story
  bricks stay in `characterBible.extensions.story_library_overlay`.
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
- The UI talks to `backend/app/routers/local_admin.py` at `/api/admin/speech`.
  The router is local-dev only: it requires `ALLOW_DEV_TMA_AUTH=true` and a
  local client host.
- Managed files are defined in `backend/app/services/local_admin_store.py` and
  include `speech_runtime.json`, story datasets, age speech examples, world
  descriptions, and external character-source JSONL.
