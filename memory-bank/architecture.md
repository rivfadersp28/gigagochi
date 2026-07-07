# Architecture

## Pet Replies

- Backend phrase generation lives in `backend/app/services/pet_reply_engine/lite_generator.py`.
- Chat, proactive and ambient replies are assembled through the same `PhrasePlan` structure: identity, persona contract, optional voice block, optional world context, memory and surface-specific rules.
- `backend/app/services/context_assembler.py` decides whether story context is needed before the model call. It returns selected `WORLD_CONTEXT` bricks plus debug metadata instead of embedding the full story dataset.
- Story retrieval is heuristic-gated by story-sphere signals. Generic small talk should not retrieve world bricks.
- Tone of voice is rendered by `backend/app/services/pet_reply_engine/voice_profile.py`. It is intended to change speech form only, not facts, answer meaning or selected story bricks.
- Ambient replies use `IDLE_DIALOGUE_ENGINE` inside the same phrase engine. They are expected to address the owner, ask questions or invite dialogue, with recent idle replies used as anti-repeat context.
- New durable story entities can be extracted after a chat reply by `story_library_extraction` and returned as `debug.storyLibraryPatch`. Frontend applies that patch into the local per-pet story-library overlay.

