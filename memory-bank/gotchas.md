# Gotchas

- Do not send the whole story dataset in every reply prompt.
  `contextRouting.worldContext` may request a small `WORLD_CONTEXT`, but final
  inclusion must still pass `speech_runtime.contextSources`.
- For production deploys, read `memory-bank/hetzner-deploy.md` first. It
  contains the current Hetzner IP/domain, SSH target/key path, server repo path,
  and fast deploy commands.
- Do not force story retrieval for every ambient phrase. Idle phrases should
  stay varied and dialogue-oriented; retrieval is only for relevant context.
  Generic wording like `фан-факт`, `вопрос`, or `скажи что-нибудь` must not be a
  hidden world-context trigger.
- Do not add a post-check/regenerate loop for replies unless explicitly requested. The current architecture avoids point 5 and keeps generation single-pass, with optional background extraction only for new story entities.
- `storyLibraryPatch` is data, not debug UI. Visible chat responses and
  `/api/push/snapshot` should expose it as a top-level field; keep
  `debug.storyLibraryPatch` only as a backward-compatible diagnostic copy.
- Story-library extraction after chat is best-effort, but failures should be
  logged. Do not use silent `except Exception` around it; otherwise lost
  patches are impossible to diagnose.
- The worktree may contain unrelated dirty frontend/deploy files. Do not stage or revert them unless the task explicitly targets them.
- Pet creation intentionally keeps generated sprite stage fallbacks fake/cheap
  for now. Do not change `FAST_GENERATION_STATE_FALLBACKS` unless the visual
  staging decision changes explicitly.
- Do not mutate character template fields during chat. Evolving per-pet
  character facts belong in `extensions.lite_overlay`; evolving story entities
  belong in `extensions.story_library_overlay`.
- Do not add per-feature source toggles outside `speech_runtime.contextSources`.
  Chat, idle, proactive, push, and `/story` must use the same matrix:
  `disabled` / `auto` / `always`.
- Not every cell in the admin "Копилки" matrix has a runtime path. Do not add
  editable cells for `chatHistory` on Idle/Pro/Push or `recentReplies` on
  Chat/Pro/Push unless the backend actually starts consuming those sources on
  those surfaces.
- Visible reply `contextRouting` is only useful when at least one
  router-controlled source is `auto`. If all visible sources are forced
  `disabled`/`always`, skip the router call instead of spending an extra LLM
  request that cannot change inclusion.
- In `ContextPlan`, `routing=None` means no router was available and selected
  direct builders may use their legacy `auto_default` sources. An empty
  `ContextRoutingDecision` means the router ran or was intentionally skipped and
  enabled nothing. Do not collapse these two states.
- `Параметры` in the admin context matrix is `contextSources.stateParams`.
  It controls hunger/happiness/energy injection. Story prompts must receive
  semantic labels from `stateLayer.stateParamLabels`, not raw numeric stats.
  Do not re-add duplicate per-surface mood/hunger/energy toggles under prompt
  sections, and do not expose `auto` for `stateParams` unless it gets a real
  router signal.
- `/story` `currentState` must stay minimal: `name`, `stage`, and optional
  semantic `params`. Do not put `pet.description` there; it belongs to
  `characterProfile` so the admin `Профиль` toggle actually controls
  descriptive identity.
- Do not feed previous generated per-pet stories back into `/story`. They are
  useful as conversational RAG for chat/idle/proactive/push, but using them as
  `/story` source material creates self-reinforcing repetition.
- Do not save one-off `/story` episodes as `lite_overlay` facts. `lite_overlay`
  is only for durable consequences that remain true after the episode. Store
  the episode itself in `recentStoryEvents` / `extensions.recent_story_events`
  and pass it to `/story` only as `ANTI_REPEAT`.
- `/story` image generation should keep the pre-image scene extraction step.
  Do not silently fall back to sending the raw generated story to the image
  model if `background_story_image_scene` returns an empty scene; let image
  generation fail so Telegram uses the existing text-only fallback.
- Direct OpenAI `/story` image generation should not rely on sprite reference
  images being consumed by the provider. Keep enough compact pet identity and
  visual detail in the final image prompt.
- `/api/admin/speech` is intentionally local-dev only. It should stay disabled
  in production by requiring `ALLOW_DEV_TMA_AUTH=true` plus a local client host.
- Speech/dataset saves validate JSON or JSONL, create backups under
  `backend/data/.admin-backups/`, and clear runtime `lru_cache` loaders. If a
  new cached dataset is added, include its cache clear hook in
  `local_admin_store._clear_runtime_caches()`.
- `speech_runtime.json` must keep `meta.format=tamagochi-speech-runtime-v1`.
  If it starts with story-library keys like `pools`, it was overwritten with
  the wrong admin file and should be restored before publishing.
- Main-screen ambient must not reintroduce fixed dialogue moves or prompt
  examples such as inner weather/day map/mini quest. Use the open
  `surfacePrompts.idle` plus `contextRouting.recentReplies` as anti-repeat
  context.
- Do not reintroduce `surfaceRules` in `speech_runtime.json` or Python
  defaults. Proactive keeps only a neutral reason context line; ambient is
  steered by `surfacePrompts.idle`, visible reply rules, state, memory and
  optional routed context.
- Empty rule arrays in `speech_runtime.json` are intentional overrides, not a
  signal to use Python fallback defaults. This matters for admin-cleared
  `visibleReply.ambientRules`.
- `speech_runtime.json` `worldContext.template` must keep `{lines}`. Without it,
  selected stories are computed but not shown to the model.
- Avoid putting literal `WORLD_CONTEXT` into generic visible reply rules. Tests
  use that marker to distinguish actual injected world context from normal
  speech instructions.
- Deadline/event memories belong to proactive, not ordinary ambient idle.
  Ambient prompt assembly should only pass soft memory kinds such as
  preference/relationship/routine/boundary, and should not include memory
  summary lines that can pull deadline context into idle.
- Do not re-enable `VOICE_CONTROL` for visible replies without an explicit
  product decision. Current prompt behavior relies on character name/description
  instead of `characterBible.voice`, catchphrases, sample replies, or
  `dialogue_style`.
- Admin publish is local-only and opt-in. Keep `ADMIN_PUBLISH_ENABLED=false` on
  Hetzner; the publish job must stage only `managed_admin_git_paths()` and never
  `.admin-backups/` or unrelated dirty/untracked files.
- Admin publish is a data-only deploy path. Production compose bind-mounts
  individual managed `./backend/data` files/directories into backend and bot
  containers and keeps `push_data` mounted at `/app/data/push`. Do not mount
  the whole `/app/data` parent as read-only: Docker cannot create the nested
  `/app/data/push` volume mountpoint and backend startup fails. Use
  `up -d --no-build backend bot` for admin data publishes; use full `--build`
  only for code/dependency/image changes.
- Admin server-sync is also local-only and opt-in. Keep
  `ADMIN_SYNC_FROM_SERVER_ENABLED=false` on Hetzner; local sync should refuse to
  overwrite managed data files when they already differ from the server commit.
- Do not write production admin data directly over SSH. Production source in
  `/admin/speech` is for reading Hetzner `backend/data`; changes must still go
  through the publish/deploy pipeline so GitHub, local files, and Hetzner do
  not drift.
- Production admin apply should publish the full managed file set read from
  Hetzner, not only edited drafts. Otherwise unrelated local managed-file diffs
  can be swept into the production commit.
- Age plus hunger/happiness/energy prompt modifiers now belong to
  `speech_runtime.json` `stateLayer`. Do not reintroduce separate hardcoded
  thresholds, semantic labels, optional-usage rules, or age labels in generators.
- `cleanliness` is not an active pet parameter. Do not reintroduce it in API
  schemas, local storage, prompt contexts, or story/travel stat signals unless a
  visible UI/control for it is added first.
- Hetzner `/opt/gigagochi` may not have branch upstream tracking configured.
  Use explicit `git pull --ff-only origin main` in deploy commands.
- Local speech admin uses the Next same-origin proxy. Keep
  `frontend/.env.local` `BACKEND_URL` aligned with the local backend port
  (`8000` in the current dev setup), and keep `127.0.0.1`/`localhost` in
  `next.config.ts` `allowedDevOrigins` so Next dev HMR is not blocked.
- `npm run build` in `frontend/` rewrites `frontend/next-env.d.ts` from
  `./.next/dev/types/routes.d.ts` to `./.next/types/routes.d.ts`. Treat it as a
  generated build side effect and revert it unless the Next config/source setup
  intentionally changes.
- User-facing "здоровье" still uses the legacy internal stat key `energy`.
  Keep API/storage compatibility and translate only at prompt/UI boundaries
  unless a deliberate migration is planned.
- Server-generated story stat changes must sync back to Mini App through a
  partial `statsPatch`. Do not replace the whole stats object unless every
  `lastStatTickAt` key is also reset consistently; otherwise independent decay
  timers will double-decay or collapse into one shared clock.
