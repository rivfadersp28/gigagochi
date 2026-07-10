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
- `frontend/public/figma/main-pet-bg.png` is not a full scene background; it is
  a transparent pet/backdrop layer. Main-screen scene background color/shape
  still lives in CSS unless `main-screen-bg.png` is explicitly used for the
  full 402x874 scene.
- In `frontend/src/app/globals.css`, plain `backdrop-filter: blur(...)` may be
  compiled to only `-webkit-backdrop-filter` by the current Next/Tailwind CSS
  pipeline. For main-screen glass surfaces, keep the Tailwind-style
  `--tw-backdrop-blur` rule that emits standard `backdrop-filter`, and verify
  with computed styles in the browser.
- Pet creation intentionally keeps generated visual state fallbacks fake/cheap
  for now. Do not change `FAST_GENERATION_STATE_FALLBACKS` unless the visual
  staging decision changes explicitly. The active path generates only
  `teen-idle-character.png` as an intermediate and `teen-idle.png` as the final
  composed base scene, then maps happy/sad/hungry to that scene until both
  background-generated sad assets are ready. Publish the sad image and video
  atomically so the dashboard never combines a sad poster with the idle video.
  Sad image prompts must explicitly lock camera distance, head size and character
  occupancy: a generic "change only the pose" instruction can still make the
  image model reframe the character as a close-up. Render sad URLs with a
  dedicated cache version when replacing a generated pair in place, because
  Telegram WebView may retain the previous MP4 under the original asset URL.
  Happy prompts alone do not reliably preserve the character's horizontal
  position. Keep happy edits confined to the fixed character-region crop and
  composite the result back into the idle scene; also bump the happy asset cache
  version whenever replacing an existing PNG/MP4 pair in place.
- The dashboard background is now the generated composed pet scene. Do not add
  a separate centered pet sprite, shadow, blink overlay, tap animation, or
  background-removal step unless the visual pipeline is intentionally changed.
- Pet scene image and Seedance first frame must stay the same `720x1280` 9:16
  PNG. Do not send the raw composed `1024x1536` image directly to Seedance or
  use it as the dashboard poster; the aspect mismatch can reintroduce initial
  reframe/jitter.
- Seedance can preserve its input image for the first two frames and then
  recompose it abruptly around 0.1 seconds. Dashboard playback intentionally
  skips that preroll and loops manually from the same offset; native `loop`
  makes the startup stretch recur on every loop. Keep the muted `autoPlay`
  attribute and call `play()` immediately after the initial seek: gating play on
  `seeked`, or seeking without resuming, can stall in Telegram WebView when
  mobile preload is deferred.
- Pet creation waits only for the required OpenRouter idle video after image
  composition. Its failure still fails creation, but sad image/video failures
  are best-effort and must keep the base `result`. Keep each image stage in the
  image executor and each video-polling stage in the video executor: polling may
  consume the full `OPENROUTER_VIDEO_TIMEOUT_SECONDS` and must not occupy an
  image worker. Frontend creation should return when a running job first exposes
  `result`, then persist and poll its job id for sad-asset progress.
- Do not recreate generation executors or the in-memory job registry in the
  TMA router. `GenerationJobService` owns them and FastAPI lifespan shutdown
  must call `tma.shutdown_generation_jobs()` so queued futures are cancelled
  during process exit.
- Do not mutate character template fields during chat. Evolving per-pet
  character facts belong in `extensions.lite_overlay`; evolving story entities
  belong in `extensions.story_library_overlay`.
- Do not add per-feature source toggles outside `speech_runtime.contextSources`.
  Chat, idle, proactive, push, and `/story` must use the same matrix:
  `disabled` / `auto` / `always`.
- Not every cell in the admin "Копилки" matrix has a runtime path. `chatHistory`
  is active for Chat, Idle and Story; `recentReplies` is active for Idle and
  Story. Keep unsupported admin cells aligned with actual backend consumption.
- Visible reply `contextRouting` is only useful when at least one
  router-controlled source is `auto`. If all visible sources are forced
  `disabled`/`always`, skip the router call instead of spending an extra LLM
  request that cannot change inclusion.
- Do not restore prompt-side anti-repeat for normal chat. Recent pet replies must
  remain assistant messages in the causal dialogue; treating their nouns and
  syntax as forbidden wording makes follow-ups incoherent. Ambient anti-repeat
  should guard only against near-verbatim repetition.
- A dashboard ambient/proactive hook is durable only when the user answers it:
  append the hook before the user message and send both as normal history. Do
  not reintroduce regex gating for whether the user's text counts as a reply.
- Full AI prompt content must not be logged or returned by default. Client
  `includeDebug=true` is honored only together with `ALLOW_DEV_TMA_AUTH=true`;
  production users must never receive prompt snapshots. Use
  `AI_PROMPT_LOG_FULL=true` only in an explicit local diagnostic session.
- Provider status and provider response text belong in server failure logs,
  not API responses or visible frontend errors. Public AI errors may contain
  the stable app code, safe message and provider request ID for correlation.
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
  semantic `params`. Do not put `pet.description` there; use the separate
  compact `identitySeed` for `name` plus raw description. The admin `Профиль`
  toggle should still control only the larger character-profile dossier.
- Do not feed previous generated per-pet stories back into `/story`. They are
  useful as conversational RAG for chat/idle/proactive/push, but using them as
  `/story` source material creates self-reinforcing repetition.
- Do not save one-off `/story` episodes as `lite_overlay` facts. `lite_overlay`
  is only for durable consequences that remain true after the episode. Store
  the episode itself in `recentStoryEvents` / `extensions.recent_story_events`
  and pass it to `/story` only as `ANTI_REPEAT`.
- Do not use memory `updatedAt` as the event time. `recordedAt` is storage time,
  `occurredAt` is factual event time, and legacy facts without reliable event
  time must keep `occurredAt` absent.
- Do not expand the ten full `recentStoryEvents` records to solve year-scale
  anti-repeat; full stories and images inflate push snapshots. Keep long-lived
  compact title/tag signatures in server-side `storyNoveltyHistory` instead.
- User-memory v2 is still frontend localStorage-backed. Lazy migration preserves
  existing local characters, but it does not provide cross-device or cleared-
  WebView durability; that requires an authoritative server memory store.
- Do not canonize a new ability/title/profession from one generated chat reply.
  The lite-fact extractor must validate such facts against the character capsule.
- `runLocalPetChatTurn` is also used for synthetic food-reaction prompts. Do not
  apply `happinessDelta` inside that shared helper; apply it only in actual user
  send handlers, or feeding will accidentally count as praise/abuse.
- Do not make the background-story aftermath analyzer choose stats again.
  `statImpacts[]` comes from the story generation payload and backend caps it;
  aftermath only extracts durable lite facts plus compact recent-event data.
- Do not put `extensions.recent_story_events` back into `CHARACTER_PROFILE`.
  Chat recall uses the deterministic `recentEvents` source and the canonical
  `RECENT_EVENTS` block, which must stay above generic `WORLD_CONTEXT`.
- `/story` image generation should keep the pre-image scene extraction step.
  Do not silently fall back to sending the raw generated story to the image
  model if `background_story_image_scene` returns an empty scene; let image
  generation fail so Telegram uses the existing text-only fallback.
- Direct OpenAI reference-image generation must use `images.edit`; `images.generate`
  does not consume the sprite reference. `/story` has no textual identity
  fallback: if the current asset reference is unavailable, preserve text-only
  delivery instead of asking the model to invent the pet design.
- Do not put the full sprite `VISUAL_STYLE_FRAME` into story illustrations: its
  studio/white-background presentation conflicts with narrative environments.
  Copy `VISUAL_CHARACTER_STYLE` exactly and keep only the compact scene plus
  reference-preservation rules around it.
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
- Do not append the full `lore_runtime` world block to every visible reply.
  Its nature/ruin/material palette strongly primes ornate mini-stories even when
  `storyLibrary` is disabled. Ordinary chat/idle/proactive/push use the compact
  character capsule plus `world.dialogueVocabulary`; richer world lore must
  enter through an explicit context path.
- Visible `reply` is direct speech, not a prose caption. A character may say
  `я охотилась на гоблинов`, but the generator must not substitute an authorial
  action description for words spoken aloud.
- Keep the visible-reply length ceiling in editable
  `speech_runtime.visibleReply.maxChars`; callers may request a lower limit but
  must not bypass the shared cap with surface-specific hardcoded maxima.
- Keep visible phrase model/reasoning in `speech_runtime.visibleReply`, not in
  global `OPENAI_CHAT_MODEL` / `OPENAI_CHAT_REASONING_EFFORT`. The global chat
  settings are also used by stories, travel, memory/extractors and image-scene
  preparation. `gpt-5.4-mini` Chat Completions rejects function tools combined
  with reasoning: ordinary chat should omit tools and keep phrase reasoning;
  only explicit rename-intent messages attach `update_pet_name` and omit the
  reasoning parameter.
- Deadline/event memories belong to proactive, not ordinary ambient idle.
  Ambient prompt assembly should only pass soft memory kinds such as
  preference/relationship/routine/boundary, and should not include memory
  summary lines that can pull deadline context into idle.
- Do not paste the whole idle intent catalog into one generation. Keep broad
  conversation purposes in editable `ambientDialogueImpulses` and inject only
  one selected impulse per idle reply; recent replies separately prevent reuse
  of the same wording and conversational purpose.
- Do not re-enable `VOICE_CONTROL` for visible replies without an explicit
  product decision. Current prompt behavior relies on character name/description
  instead of `characterBible.voice`, catchphrases, sample replies, or
  `dialogue_style`.
- Do not re-enable baby age examples as a visible-reply prompt layer without an
  explicit product decision. `age_speech_examples` is archival admin data now;
  baby stage should affect visible replies only through the compact identity
  wording such as `маленький/маленькая {identity}`.
- Do not inject `tone_runtime` into factual extractors unless there is a separate
  product decision. Memory and aftermath extractors should stay factual; tone is
  for generation prompts, context routing and image/story art direction.
- Admin publish is local-only and opt-in. Keep `ADMIN_PUBLISH_ENABLED=false` on
  Hetzner; the publish job must stage only `managed_admin_git_paths()` and never
  `.admin-backups/` or unrelated dirty/untracked files.
- Admin publish is a data-only deploy path. Production compose bind-mounts
  individual managed `./backend/data` files/directories into backend and bot
  containers and keeps `push_data` mounted at `/app/data/push`. Do not mount
  the whole `/app/data` parent as read-only: Docker cannot create the nested
  `/app/data/push` volume mountpoint and backend startup fails. Use
  `up -d --no-build --force-recreate backend bot` for admin data publishes so
  bind-mounted managed files such as `tone_runtime.json` are definitely visible
  inside running containers. Use full `--build` only for code/dependency/image
  changes.
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
- Next dev/Turbopack can keep serving stale compiled global CSS after editing
  `frontend/src/app/globals.css`, even when source and server chunks show newer
  component code. If localhost visually ignores CSS-only changes, restart the
  frontend dev server and re-check the served `/_next/static/chunks/*.css`.
- `npm run build` in `frontend/` rewrites `frontend/next-env.d.ts` from
  `./.next/dev/types/routes.d.ts` to `./.next/types/routes.d.ts`. Treat it as a
  generated build side effect and revert it unless the Next config/source setup
  intentionally changes.
- `npm audit` currently reports the moderate PostCSS advisory
  `GHSA-qx2v-qp2m-jg93` through Next's bundled PostCSS. npm proposes an
  incompatible downgrade to Next 9.3.3, so do not run `npm audit fix --force`;
  re-check when the pinned Next release updates its bundled dependency.
- After changing a FastAPI route or Pydantic schema, run the backend OpenAPI
  exporter and `npm run contracts`. `make check` deliberately fails on stale
  `frontend/openapi.json` or `src/lib/generated/openapi.d.ts`.
- User-facing "здоровье" still uses the legacy internal stat key `energy`.
  Keep API/storage compatibility and translate only at prompt/UI boundaries
  unless a deliberate migration is planned.
- Dashboard status arcs must render from live pet stats through
  `StatProgressRing`. The exported `status-*-new.svg` files contain baked arcs
  and cannot visually reflect chat, feeding, story, or decay changes.
- Main-screen pet tap feedback intentionally composes the active visual-mode
  poster with a client-side static heart overlay for 180 ms while pausing the
  video. This keeps the character pixel-aligned and makes the reaction available
  to existing localStorage pets without regenerating backend assets. Do not
  replace it with a newly generated full-frame image unless old-pet migration and
  per-mode alignment are handled explicitly.
- Server-generated story stat changes must sync back to Mini App through a
  partial `statsPatch`. Do not replace the whole stats object unless every
  `lastStatTickAt` key is also reset consistently; otherwise independent decay
  timers will double-decay or collapse into one shared clock.
- Do not derive the 24-hour death window from the latest stat tick: ticks keep
  advancing while a stat is already zero. Persist the first continuous zero
  time in `zeroStatSinceAt`, clear it as soon as the stat becomes positive, and
  set `diedAt` only when elapsed time is strictly greater than 24 hours.
- Telegram story photo captions are capped at 1024 chars. Keep the stat debug
  footer reserved during truncation, otherwise `/story` debugging can hide the
  analyzer result behind a long generated story.
- Backend and bot share the push registry volume from separate processes. Do
  not read, rewrite or replace `telegram_push_state.json` directly; use
  `JsonTelegramPushStore.update_record()` so the cross-process file lock and
  atomic write are preserved. Corrupt registry JSON is an operational error,
  not an empty store.
- Do not run `/story` or `/push` generation inline in the Telegram `getUpdates`
  loop. `app.bot` submits both to the bounded `telegram-command` executor;
  worker tasks create their own `httpx.Client` rather than sharing the polling
  client.
- Main-screen speech bubble must stretch from the bubble container, not from an
  absolutely positioned `<img>` SVG. Percentage height on that replaced element
  can stay at the intrinsic SVG height while animated text grows; use the SVG
  as a `background-size: 100% 100%` container background for stretchable bubbles.
- Dashboard keyboard geometry must follow the actual `visualViewport` bottom and
  move the composer with `transform`. Do not restore eager keyboard timers,
  assumed keyboard heights, or `bottom` transitions: they desynchronize from the
  native keyboard and make the composer jump. Keep the dashboard root on
  `overflow: clip`; `overflow: hidden` is still programmatically scrollable, so
  focus can change its `scrollTop` and shift the whole fixed scene.
