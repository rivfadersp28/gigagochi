# Gotchas

- Do not send the whole story dataset in every reply prompt. Use `assemble_pet_context` to select a small `WORLD_CONTEXT` only when the current request/history/memory has story-sphere signals.
- Do not force story retrieval for every ambient phrase. Idle phrases should stay varied and dialogue-oriented; retrieval is only for relevant context.
- Do not add a post-check/regenerate loop for replies unless explicitly requested. The current architecture avoids point 5 and keeps generation single-pass, with optional background extraction only for new story entities.
- `storyLibraryPatch` is returned under `debug`, but frontend uses it as data, not just debug UI. Removing debug payload can break local story-library persistence.
- The worktree may contain unrelated dirty frontend/deploy files. Do not stage or revert them unless the task explicitly targets them.
- Pet creation intentionally keeps generated sprite stage fallbacks fake/cheap
  for now. Do not change `FAST_GENERATION_STATE_FALLBACKS` unless the visual
  staging decision changes explicitly.
- Do not mutate character template fields during chat. Evolving per-pet
  character facts belong in `extensions.lite_overlay`; evolving story entities
  belong in `extensions.story_library_overlay`.
- Voice regulation/memory is intentionally separate from character instance
  simplification for now. Avoid changing `voice_profile.py`,
  `frontend/src/lib/petVoice.ts`, or speech-runtime memory behavior as part of
  character-template cleanup.
- `/api/admin/speech` is intentionally local-dev only. It should stay disabled
  in production by requiring `ALLOW_DEV_TMA_AUTH=true` plus a local client host.
- Speech/dataset saves validate JSON or JSONL, create backups under
  `backend/data/.admin-backups/`, and clear runtime `lru_cache` loaders. If a
  new cached dataset is added, include its cache clear hook in
  `local_admin_store._clear_runtime_caches()`.
- `speech_runtime.json` must keep `meta.format=tamagochi-speech-runtime-v1`.
  If it starts with story-library keys like `pools`, it was overwritten with
  the wrong admin file and should be restored before publishing.
- Admin publish is local-only and opt-in. Keep `ADMIN_PUBLISH_ENABLED=false` on
  Hetzner; the publish job must stage only `managed_admin_git_paths()` and never
  `.admin-backups/` or unrelated dirty/untracked files.
- Admin server-sync is also local-only and opt-in. Keep
  `ADMIN_SYNC_FROM_SERVER_ENABLED=false` on Hetzner; local sync should refuse to
  overwrite managed data files when they already differ from the server commit.
- Hetzner `/opt/gigagochi` may not have branch upstream tracking configured.
  Use explicit `git pull --ff-only origin main` in deploy commands.
- Local speech admin uses the Next same-origin proxy. Keep
  `frontend/.env.local` `BACKEND_URL` aligned with the local backend port
  (`8000` in the current dev setup), and keep `127.0.0.1`/`localhost` in
  `next.config.ts` `allowedDevOrigins` so Next dev HMR is not blocked.
