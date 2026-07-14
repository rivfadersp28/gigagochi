# Gotchas

- Interactive-travel text and choices live in browser localStorage during the journey; generated
  PNG/MP4 files alone cannot reconstruct them. Preserve the completed `finale.json` snapshot or
  recover it by reopening the still-present completed client session before starting a new travel.
- Finale reference-to-video needs public HTTPS asset URLs. A localhost static URL is not fetchable
  by OpenRouter; keep the production asset origin in the snapshot or supply it as the lab's
  reference base URL.

- Do not add Uvicorn workers while generation jobs use the local SQLite-backed executor. Multiple
  API processes would each recover the same active jobs. Scale beyond one backend process only after
  moving execution ownership to a broker/worker system such as Redis plus a dedicated worker.
- Do not use `get_openai_client()` for text. It is now the media compatibility
  selector controlled by `AI_PROVIDER`; every reply, extractor, character
  bible and story text call must cross `app.llm.complete_chat`. Use
  `LLM_PROFILE=legacy` only when text should follow the media provider.
- Do not select image/video vendors inside story, travel or pet-generation services. Route them
  through `app.media`; `MEDIA_PROFILE=kandinsky` changes t2i/i2i and i2v together. Kandinsky i2i
  accepts base64 images under `params.image` and uses task type
  `k6-i2i`; the i2i REST URL shown as `k6-image-t2i` in the supplied example is a documentation
  typo. Keep TLS verification enabled even though the standalone sample disables it.
- Do not mutate `MEDIA_PROFILE` or shared runtime JSON to build the Kandinsky comparison: concurrent
  jobs would race. Keep the primary job on OpenAI, pass `ImageRequest.provider="kandinsky"` only to
  the nested comparison branch, reuse the primary character bible, skip tap generation and generate
  Kandinsky video only for the normal state. Treat its error as best-effort comparison metadata, not as a primary
  generation failure.
- Post-reply memory extraction may remain asynchronous so the visible answer is not delayed, but turns
  for the same pet must await the previous persistence tail before reading memory. Unordered
  fire-and-forget extraction lets an older turn overwrite a newer fact with the same normalized key.
- Do not reuse OpenAI images as posters or i2v inputs in the Kandinsky comparison, including test
  fixtures. Each provider owns a complete visual lineage: provider-adapted image prompts, its own
  normal/sad/happy images, and normal video generated only from that provider's normal image.
- Kandinsky K5 i2v accepts one base64 string in `params.image`, not the image array used by K6 i2i.
  A raw 2.2 MB PNG exceeded the current nginx JSON body limit with HTTP 413; encode the same frame as
  optimized JPEG before base64. The HD endpoint currently returns a fixed `896x1280` H.264 MP4 even
  when the source scene is `720x1280`; the dashboard centers and clips scene media by design.
- Kandinsky 6.0 rejects prompts longer than 2048 characters with HTTP 422. Keep provider-specific
  compaction in the Kandinsky transport. Replace the full shared style frame with its curated
  Kandinsky variant before applying the generic head/tail fallback; otherwise the middle face
  rules are lost and normal assets stop looking sleepy and melancholic. Do not weaken prompts
  sent to OpenAI or OpenRouter.
- Kandinsky may report task status `done` before its output-censor dependency can return the file.
  Retry only the exact transient result response `output censor service unavailable` within the
  one-minute pickup window; do not retry unrelated 422 validation or moderation failures.
- Kandinsky i2i carries reference images as base64 inside JSON. Sending original multi-megabyte
  PNG backgrounds exceeds the API proxy body limit with HTTP 413. Resize references to a bounded
  long side, flatten alpha onto white and encode as optimized JPEG before base64.
- Do not send the shared mixed English visual prompts to Kandinsky verbatim. Its task-specific
  adapter uses compact Russian templates for pet, story and travel. Keep full-body framing and
  source-species anatomy near the start of pet prompts; a landscape request or late English
  framing instruction produced close-ups and over-humanized creatures.
- Kandinsky interprets toy, figurine, miniature, resin and macro wording as a small object. The
  active pet-creation direction now intentionally accepts that collectible-art-toy character,
  superseding the earlier full-size-creature experiment. Compensate with explicit full-body framing,
  large occupancy in the vertical frame, dense layered costume, functional composite accessories
  and tactile wear so the result does not collapse into a tiny simple object. Keep the isolated
  identity on white and add the mossy forest only during scene i2i; that separation stabilizes
  identity and prevents the initial t2i request from spending its detail budget on environment.
- Changing `backend/.env` does not take effect through `docker compose restart`.
  Recreate backend and bot after an `LLM_PROFILE` change. A task that changes
  provider in `llm_runtime.json` must also define its own model, so a model name
  cannot leak across providers.
- GigaChat is intentionally text-only here. Do not pass image content or enable
  the supplied adapter's text-to-image injection. Its reasoning values are
  `low`, `medium`, `high` (or omitted); JSON Schema is implemented through
  legacy functions. Some GigaChat models reject `null` inside legacy function
  schema enums and may return ordinary text instead of a function call; keep the
  adapter's enum cleanup, system-only anchor message and plain-text
  visible-reply wrapper in place.
- `GigaChat-3.5-*` is not compatible with the same structured-output payload as
  Lightning/Pro/Ultra. On the current endpoint, native `response_format`
  rejects JSON modes, legacy `functions/function_call` hangs, and any
  `reasoning_effort` value (`low`/`high` reproduced) can timeout even on a tiny
  prompt. For 3.5 structured outputs without ordinary tools, use prompt-only
  JSON schema instructions and omit `reasoning_effort`.
- GigaChat 3.5 can prepend generic support openers such as `Я рядом, ...` to
  memory-grounded visible replies even when the response is not a backend
  fallback. Keep the GigaChat-only visible-reply cleanup that removes this
  opener only when a substantive continuation remains; do not apply it to
  OpenAI routes because existing OpenAI behavior intentionally preserves those
  words.
- A model routed through LiteLLM still has to support JSON Schema and function
  tools. Provider-level capability metadata cannot prove that every LiteLLM
  model supports those features; qualify a new model before enabling its
  profile.
- `GENERATION_IMAGE_WORKERS=20` means accepted concurrency, not guaranteed provider throughput.
  OpenAI project rate limits and billing must be checked separately; keep the bounded queue and retry
  policy enabled, and watch memory before increasing the worker count further.
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
- Full stories are the explicit exception to the single-pass reply rule. A
  single prose call tends to produce four action summaries instead of four
  events. Preserve plan -> plan quality -> render -> prose quality, require a
  visible before/after turn in every part, track consumed/lost/attached objects
  across parts, and reject opposition that acts against its own stated goal only
  to manufacture conflict.
- Do not apply the finale's quality bar to all four planned events. Requiring
  every decisive action to overcome its obstacle over-constrains the inciting
  event and turn, causing artificial action chains and needless full rewrites.
  Judge each part against its narrative function and preserve sound events when
  repairing a rejected plan.
- Distinct narrative functions do not guarantee distinct events. Compare the
  immediate trigger mechanisms too: repeated collapses, blocked exits, attacks,
  or reappearances of the same hazard make four parts feel like one event
  restated. Keep hidden event/state fields short and atomic; verbose ledgers are
  prone to damaged endings during structured retries and do not improve the
  compact visible story.
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
- Interactive travel is the exception: its intro renders a separate transparent
  pet layer. Generation must persist `teen-idle-foreground.png` alongside the
  opaque `teen-idle-character.png`; existing pets need that foreground migrated.
- Pet scene poster and video first frame must stay the same `720x1280` 9:16
  PNG. Do not send the raw composed `1024x1536` image directly to the video model or
  use it as the dashboard poster; the aspect mismatch can reintroduce initial
  reframe/jitter.
- Seedance can preserve its input image for the first two frames and then
  recompose it abruptly around 0.1 seconds. Backend FFmpeg post-processing trims the first 0.2
  seconds from OpenRouter/Seedance pet scenes before splitting the clip into forward and reverse
  halves, so the boundary reframe cannot return at the end of the reverse half. Keep the previous
  0.1-second trim for other video providers unless their output proves to need a different cutoff.
  It then encodes both halves into one ping-pong MP4 without duplicated endpoints. Keep
  the output at constant 24 FPS, H.264 Level 3.1 and MP4 track timescale 12288. Leaving FFmpeg's
  filter timebase unconstrained produces Level 6.2 MP4 files that Chrome can play but Telegram's
  iOS WebView may reject, leaving the dashboard on its asset-loading screen.
  native muted `autoPlay` plus `loop` on the dashboard. Do not restore client-side reverse seeking:
  H.264 must decode from an earlier keyframe and visibly freezes at the turn in Chrome/Safari.
  Existing production pet MP4 files predate this cutoff; migrate them once with
  `backend/scripts/migrate_pet_scene_video_preroll.py`, keeping its backup directory and state file
  so interrupted runs resume without trimming the same asset twice. Use `--restore-from-backup`
  when repairing an already completed migration; reprocessing the migrated files accumulates trims.
  Trim the provider video's low-motion final 0.35 seconds before reversing; removing only an exact
  duplicate endpoint frame still leaves a visible hold because i2v providers often settle before
  the nominal end. Production backend images therefore require the `ffmpeg` and `ffprobe` binaries.
- Grok may return AAC audio and an attached MJPEG preview even with
  `generate_audio=false`. Story videos must pass through
  `strip_generated_video_auxiliary_streams` before persistence or Telegram delivery.
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
- Frontend user-memory storage owns provider-neutral normalization for memory
  operations returned by any LLM. Keep canonical core keys stable
  (`user-name`, `pet-nickname`) and coerce future dated `event + dueAt`
  operations to `deadline`; provider adapters may emit aliases such as
  `user_name` or classify tomorrow obligations as events.
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
  and keep it out of the ordinary `/story` prompt. Use only its structured
  direction metadata in backend selection/novelty code.
- A negative `ANTI_REPEAT` prompt still primes the model with the forbidden
  title, tags and imagery. Do not serialize prior story details into ordinary
  `/story`; control diversity outside the generative prompt.
- Do not pass mutable `lite_overlay` appearance facts into ordinary `/story`.
  A story-created improvised action can otherwise be misclassified as a durable
  ability and immediately reinforce itself in the next episode. Aftermath
  appearance facts require the structured `lasting_injury` durability type.
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
- Keep visible phrase limits/reasoning in `speech_runtime.visibleReply`, and
  provider/model selection in `llm_runtime.json` task routes. Do not put
  visible-reply model selection in global `OPENAI_CHAT_MODEL` /
  `OPENAI_CHAT_REASONING_EFFORT`: the global chat settings are also used by
  stories, travel, memory/extractors and image-scene preparation. `gpt-5.4-mini`
  Chat Completions rejects function tools combined with reasoning: ordinary
  chat should omit tools and keep phrase reasoning; only explicit rename-intent
  messages attach `update_pet_name` and omit the reasoning parameter.
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
- Next 16 Turbopack production builds spawn an internal PostCSS process that
  binds a loopback port. In a restricted sandbox this can appear to hang and
  eventually panic with `binding to a port: Operation not permitted`; rerun the
  build with the required sandbox permission instead of treating it as an app
  deadlock.
- Do not put provider messages, exception strings, backend configuration hints
  or Pydantic field paths back into public error `message`. Trusted details
  belong only in the optional server-authenticated `diagnostic` object and the
  Sergey-only expandable UI block.
- Next's bundled PostCSS previously triggered `GHSA-qx2v-qp2m-jg93`. Keep the
  compatible `overrides.next.postcss` pin and do not run `npm audit fix --force`;
  the release audit currently reports 0 vulnerabilities.
- The live domain is routed by the shared `/opt/bizzy-radio/Caddyfile`, not by
  `/opt/gigagochi/deploy/Caddyfile`. Validate changes inside
  `bizzy-radio-caddy-1` before reloading. Replacing the bind-mounted file with
  `mv` leaves the running container on the old inode; reload the validated file
  from a container-visible temporary path or recreate the proxy container.
- After changing a FastAPI route or Pydantic schema, run the backend OpenAPI
  exporter and `npm run contracts`. `make check` deliberately fails on stale
  `frontend/openapi.json` or `src/lib/generated/openapi.d.ts`.
- Interactive-travel provider output may omit the visible time-gap prefix even when transition metadata is valid. Normalize the next part with a deterministic `Через N часов…` sentence instead of returning a 502 after repeated LLM repair.
- Interactive-travel's 80-character sentence limit is a generation-quality target, not a hard
  response-contract limit. Retry one compactness violation, but if the retry is still over the
  length/word target, preserve the complete sentence. Do not reintroduce a smaller Pydantic or
  frontend-parser cap for `departureHook`, or the lenient fallback will become a 502 again.
- User-facing "здоровье" still uses the legacy internal stat key `energy`.
  Keep API/storage compatibility and translate only at prompt/UI boundaries
  unless a deliberate migration is planned.
- Dashboard status arcs must render from live pet stats through
  `StatProgressRing`. The exported `status-*-new.svg` files contain baked arcs
  and cannot visually reflect chat, feeding, story, or decay changes.
- Main-screen pet tap feedback must not pause the mood video or swap it for a
  separately generated reaction scene: generated frames visibly shift the
  character and background. Reaction-image generation and its API contract were removed;
  the dashboard uploads the current active video
  frame into a short-lived WebGL canvas and applies a circular radial bulge centered
  on the tap for 180 ms, with a roughly 190 px screen-space radius. Keep the canvas
  on the exact centered `9:16` media rectangle rather than stretching it to the
  mobile scene, cap its device scale at 2, and stop every `requestAnimationFrame`
  loop at the bounded deadline. Particle bursts are throttled to one per 80 ms;
  keep at most two fully active bursts plus one 120 ms fading burst, because
  `partycles` treats `lifetime` as animation frames and creates a separate React root
  for every burst. Reduced-motion mode suppresses particles and weakens/shortens
  the distortion; sound and light haptic feedback may still stack per tap.
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
- The interactive-travel entry MP4 must be warmed from the dashboard and keep its versioned URL
  cacheable. `preload="auto"` on the travel screen starts too late, while the generic `/figma/*`
  `no-store` header otherwise discards the warm download. Keep the scene at full viewport size;
  a fixed `402px` cap leaves side gutters in wider Telegram WebViews.
- Interactive travel needs 9–21 text/image/video calls for a complete 3–7-part route. Never charge
  them to the pet-generation `3/day` bucket. The debug reset must invalidate the travel ID before
  deleting its directory; deleting files alone lets an already-running provider call write them back.
- Do not restore interactive-travel root matching, named-term checks, event counters, continuity
  anchors or validator-specific retries. They made semantic heuristics drive generation and caused
  valid Russian output to fail. Do not restore a parallel `step1...stepN` plot either: the model can
  make it disagree with the scene produced after a user's choice. New travels also have no fixed
  `partCount`: a preselected length made the model add an epilogue after an already completed goal.
  Let the selected action end the story on parts 3–5 and force closure only on part 6. If quality
  regresses, first adjust the short goal or immediate context, then verify with complete synthetic stories.
- GigaChat 3.5 may return arrays as nested objects/strings and may omit final JSON closers. Travel
  choices therefore use three scalar schema fields. The provider repairs only missing trailing
  brackets when a strict stack scan plus `json.loads` proves that no other syntax is damaged.
- Keep the exact opening goal in `arcPlan` and build the visible final achieved/failed sentence from
  it. In a final response, `outcome=positive` means the goal was achieved and `negative` means it
  definitively failed; the model's concrete result must say the same thing. Semantic validation is
  intentionally not used to reject or regenerate it.
