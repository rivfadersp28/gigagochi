# Gotchas

- Never replace `PublicMediaStaticFiles` with an unrestricted `StaticFiles` mount. Generated media
  directories also contain `finale.json`, Telegram owner data, prompts and provider metadata; only
  the media-extension allowlist may be reachable through `/static`.
- Never turn generated-media cleanup into an age-only recursive sweep. Background-story GC must
  preserve references from both the push registry and `bot_command_inbox.prepared_json`, honor the
  eight-day grace fence, and skip `.private` entirely. Whole asset directories may be deleted only
  from durable terminal proof: a failed pet job without `result` or an explicit fenced
  interactive-travel reset. Successful pet/travel assets remain until a durable client
  ownership/expiry registry exists.
- Keep `lite_overlay` bounded in both frontend and backend merges (80 aggregate facts, 40 per
  sphere). Unbounded extraction eventually exceeds WebView localStorage and the push-snapshot body
  contract. Backend persistence also allowlists the four known spheres and fact fields, truncates
  text/source/path/timestamps, and drops arbitrary `worldSeed`/overlay keys. Do not restore a
  recursive union of client `characterBible`: modern full bibles replace client-owned state, while
  extension-only legacy snapshots may preserve only the bounded server overlay.
- Video prompts must cross `log_video_generation_prompt`; direct INFO logging bypasses the default
  hash/length redaction and can expose user story content and reference URLs.
- Every delayed frontend mutation must carry the originating `expectedPetId`. A missing current pet
  is a superseded request too: reset must not let a late chat/media response recreate history or
  overwrite a new pet.
- A frontend push snapshot is dirty when pet state, chat history, memory, ambient replies or
  overlays change; `pet.updatedAt` alone does not cover those independently persisted inputs.
  Keep the per-pet dirty revision separate. Revisions use a shared wall-clock-backed localStorage
  counter, but each tab must keep its own writer ID in sessionStorage; a shared writer lets two tabs
  issue the same `(writer, revision)` pair after a racy read-modify-write.
- Failed `localStorage.removeItem()` is not a successful reset. Keep per-pet clear tombstones in
  memory until removal succeeds, and route reset/replacement through the centralized scoped cleanup
  so stale travel, introduction, compliment and ambient data cannot reappear.
- Test suites deny external network by default (`backend/tests/conftest.py` and frontend Vitest
  setup). The Python guard patches both `socket.connect` and `connect_ex`; subprocess tests must
  preserve the inherited `PYTHONPATH` so `tests/network_guard/sitecustomize.py` is imported.
  Provider, Telegram and media tests must stub transport explicitly; this is the guard against an
  incomplete mock triggering a paid generation.
- Pet-generation recovery identity is the pair `(jobId, description)`, not the job ID alone. On
  `GENERATION_ALREADY_ACTIVE`, use only the authenticated backend's persisted active description or
  an exact local marker for that job; otherwise one tab can create the result of prompt A under the
  description from prompt B. Recovery performs one GET/poll and never repeats the paid POST.
- Active-job idempotency aliases are valid only for the exact `(description, image provider)`
  payload. If alias adoption is discovered after a quota reservation, refund that new reservation;
  never charge a coalesced request or bind a different prompt to the active result.
- Async-provider receipts close the restart window only after the remote task ID has been written
  to SQLite. If a provider accepts a paid POST but the process dies before its response/task ID is
  durably saved, automatic recovery cannot distinguish that from a request that never arrived.
  Synchronous OpenAI/OpenRouter image endpoints expose no resumable task ID at all. Do not prune
  accepted/media-saved receipts to recover capacity: the unified store deliberately fails
  closed at its configured cap instead of risking a duplicate paid submission.
  A durable `admitted` state without a task ID is ambiguous and must also fail closed: only a
  definite HTTP rejection, pre-send connection failure or missing credential may release it.
  Configure the stable non-secret provider account namespace before rotating a credential for the
  same account; otherwise the safe fallback credential hash intentionally creates a new identity.
- Storage health and media admission share one boundary: free space must cover the greater of the
  byte/percentage floors plus the largest configured media reservation on the generated-assets
  device. Keep write/delete probes per logical mount even when disk telemetry is deduplicated by
  device; a degraded health result must block paid generation before provider dispatch.
- Provider timeout maxima must stay below the 20-minute container stop grace. Resumable video stages
  are capped at 900 seconds; requests with client retries are capped at 300 seconds per attempt, so
  graceful shutdown has headroom before Docker sends SIGKILL.
- A storage reservation must outlive provider return. Production media writers use the reserved
  context variants and keep that context open through validation, post-processing and the fsynced
  atomic commit. The bytes-only generation helpers are compatibility APIs; do not use them for a
  new durable path. Reservation files live under generated `.private`, hold `flock` for their whole
  lifetime and are reclaimed as stale only when their lock can be acquired.
- Keep provider/post-processing temporary directories under the exact generated-volume path
  `.private/processing-tmp` and use only the cleanup service's known prefixes. A generic tmp sweep,
  symlink traversal or moving `TMPDIR` back to the container overlay can either delete unrelated
  files or exhaust ephemeral rootfs while the persistent generated volume still appears healthy.

- Interactive-travel text and choices live in browser localStorage during the journey; generated
  PNG/MP4 files alone cannot reconstruct them. Preserve the completed `finale.json` snapshot or
  recover it by reopening the still-present completed client session before starting a new travel.
- Every interactive-travel media/finale side effect must authorize against the durable session's
  exact owner, pet, part and narrative fingerprint before provider admission and before persistence.
  Terminal session rows expire after 180 days; completed owner proof remains, but late recovery then
  fails closed because assets alone are not authoritative narrative state.
- Do not add durable `starting`/`in_progress` rows or expiring operation leases around interactive
  travel text calls. Text generation is free and may race; reliability comes from the short atomic
  start/continue commit, which replays an exact winner and rejects a stale different request.
- Finale reference-to-video needs public HTTPS asset URLs. A localhost static URL is not fetchable
  by OpenRouter; keep the production asset origin in the snapshot or supply it as the lab's
  reference base URL.
- Backend and bot must mount the same `push_data` volume and use the same
  `RATE_LIMIT_STORE_PATH`/`GENERATION_RATE_LIMIT_PER_DAY`; otherwise bot media commands bypass the
  API quota. `/full_story` is one user generation action, while `/push` stays outside the paid
  generation bucket.
- `request_admission_service` protects FastAPI's local sync-handler pool, not all Uvicorn processes
  or bot work. Keep its async dependency ahead of every public synchronous LLM/media handler and
  release in dependency teardown; moving admission into the sync endpoint is too late because the
  request already occupies an AnyIO worker.
- `SCHEDULED_BACKGROUND_STORY_PAID_MEDIA_DAILY_CAP` is deliberately fail-closed at zero and counts
  provider submission attempts globally, not successful files or users. Check deterministic saved
  media before charging, charge immediately before each image/video reservation, and do not add a
  request-key dedupe or refund: a crash after provider acceptance must make a retry consume another
  unit. Budget-disabled/exhausted delivery is a successful text/photo fallback with persisted
  status, so it must not degrade scheduler health or remain due for paid retry. Sort due records by
  the hash of the UTC budget-window date plus Telegram ID (with Telegram ID as the tie-breaker): JSON
  insertion order permanently starves later users when the global cap is smaller than the queue.
- Never put a raw Telegram ID in a public generated-media owner directory. Background-story paths
  must remain owner-bound via the hash of `(telegram_id, canonical_pet_id)`; compatibility reads or
  GC for older pet-only paths must not become active cross-owner discovery.

- Generation-job SQLite leases fence duplicate recovery across processes, but each Uvicorn process
  still owns a separate local queue and worker pools. Do not scale worker count casually: admission
  capacity, SQLite contention and shutdown/drain behavior must be designed as a multi-process system.
  A broker plus dedicated workers remains the safer scaling boundary.
- Never treat a bulk lease-renew `rowcount` as proof that every requested task is still owned. Renew
  with exact IDs, fence the missing local tasks, and recheck ownership only after acquiring the
  paid-stage/update lifetime lock. The bucketed lock files intentionally cap inode growth.
- Paid generation checkpoints must write nonempty media through atomic replace and persist character
  metadata before the first provider call. SQLite job commits use `synchronous=FULL`. This still
  cannot eliminate the narrow crash window after a provider accepts a request but before its bytes
  reach local storage; retries in that window may be billed twice.
- Deployments that previously served `POST /api/travel` may leave
  `legacy_travel_requests.sqlite3`. The application no longer reads that database. Archive it after
  stopping old backend processes; do not bulk-delete generated directories from its rows because
  completed assets have no independent lifetime proof.
- Every real image/video provider dispatch must stay inside `MediaGateway` so the shared
  `MEDIA_CONCURRENCY_LOCK_DIR` file slots apply to backend and bot together. Acquire the global slot
  before storage reservation, block when full instead of rejecting, and never create request-derived
  lock filenames; the fixed image/video files keep inode growth bounded.
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
- Generation worker counts control local pipeline scheduling, not global provider throughput. The
  defaults are 4 image/2 video workers and the shared media slots independently cap actual paid
  calls; keep both limits coordinated before increasing either one.
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
- In interactive-travel character-by-character text, do not render whitespace
  as animated inline-block characters with `white-space: pre`. A preserved
  space can wrap onto the next visual line and create a false left indent;
  render inter-word whitespace as normal collapsible text instead.
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
- Treat downloaded provider video as hostile input. Force FFprobe/FFmpeg to the local MOV/MP4
  demuxer, allow only the `file` protocol, and disable external/absolute data references; otherwise
  a crafted manifest can turn post-processing into an SSRF or local-file read.
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
  must call `tma.shutdown_generation_jobs()`. Shutdown first fences every new paid stage, cancels
  queued futures, and waits only for stages already past the fence; unfinished jobs must remain
  durable, unleased and claimable after restart rather than being marked failed.
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
- Never persist interactive-travel stats before its idempotency receipt. Both belong in one bounded
  `LocalPetState` write keyed by `travelId:partNumber`; the travel session's `appliedResultParts`
  marker can lag or disappear after a crash and must not authorize a second delta.
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
  `local_admin_store._clear_runtime_caches()`. Keep the cross-process write lock, unique backup
  creation, file/directory fsync and whole-batch rollback together; independent per-file writes can
  publish a mixed runtime configuration after a crash.
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
  `.admin-backups/` or unrelated dirty/untracked files. Retain every active publish job but prune old
  terminal entries before insertion so the in-memory registry stays at 32 jobs.
- Admin publish is a data-only deploy path. Production compose bind-mounts
  individual managed `./backend/data` files/directories into backend and bot
  containers and keeps `push_data` mounted at `/app/data/push`. Do not mount
  the whole `/app/data` parent as read-only: Docker cannot create the nested
  `/app/data/push` volume mountpoint and backend startup fails. Use
  `up -d --no-build --force-recreate backend bot` for admin data publishes so
  bind-mounted managed files such as `tone_runtime.json` are definitely visible
  inside running containers. Use full `--build` only for code/dependency/image
  changes.
- Do not remove the `volume-permissions` compose dependency while persistent volumes from the old
  root-running backend may exist. Image-layer `chown` does not affect an already-created named
  volume. Stop backend and bot for the first UID/GID 10001 migration so an old root writer cannot
  create a stale file after the ownership scan. Keep bot storage/GC environment values identical to
  backend because bot `/story` dispatches through the same media admission boundary. Keep Python's
  `077` umask and the init service's group/world permission repair; read-only container roots do not
  protect secrets already created with permissive modes on persistent volumes.
- Never archive or restore the named volumes while backend/bot may write SQLite WAL files. Mark the
  cleanup obligation before the first Compose stop, validate `quick_check` and checksums before
  mutation, and leave both writers stopped if cross-volume restore plus automatic rollback cannot be
  proven complete.
- Do not duplicate application defaults in `docker-compose.prod.yml`: `backend/.env` is the shared
  source for provider/profile/scheduler/limit settings. Compose env owns only topology, security and
  documented resource knobs; shadowing a key there makes backend and bot drift during env changes.
- Do not regenerate backend locks with `pip lock` on a developer Mac: pip guarantees that output
  only for the current Python/platform. Resolve and hash CPython 3.12 manylinux2014 wheels for both
  x86_64 and aarch64, keep both hashes for compiled packages, and verify both targets with
  `pip download --require-hashes --only-binary=:all:` before changing the checked-in locks.
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
- Interactive travel deliberately uses the fixed transition «Я иду дальше».
  Do not add time-gap generation or compactness retries back to this simple path.
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
- `useLocalPetState` keeps `feed`/`play`/tap and most pet-state patches synchronous, so browser
  localStorage cannot make them atomic across tabs. Keep the fresh read, expected-pet fence,
  pre-write stale check, single snapshot write and exact read-back; these prevent false success but
  do not eliminate a last-writer-wins race after read-back. Do not reintroduce a journal, generation
  WAL or localStorage pseudo-lock. Interactive travel relies on backend CAS when native Web Locks are
  missing; conversation/memory mutations fail closed.
- Do not derive the 24-hour death window from the latest stat tick: ticks keep
  advancing while a stat is already zero. Persist the first continuous zero
  time in `zeroStatSinceAt`, clear it as soon as the stat becomes positive, and
  set `diedAt` only when elapsed time is strictly greater than 24 hours.
- Telegram story photo captions are capped at 1024 chars. Keep the stat debug
  footer reserved during truncation, otherwise `/story` debugging can hide the
  analyzer result behind a long generated story.
- Backend and bot share the SQLite WAL push registry from separate processes. Always use the store
  `update_record()` contract so `BEGIN IMMEDIATE`, logical capacity metadata and retention indexes
  stay consistent. The first JSON-to-SQLite deploy must stop both writers together; a mixed rollout
  can append to the now-inactive JSON after the durable import marker and create split-brain state.
  Keep the legacy JSON and a consistent volume backup until the SQLite marker and delivery flow are
  verified; never merge it over a non-empty unmarked SQLite database. Keep
  `TELEGRAM_PUSH_LEGACY_JSON_REQUIRED=true` during upgrades/restores; use `false` only for a confirmed
  clean install, otherwise a temporarily missing mount can become silent empty state.
- A pet unregister is a permanent late-write fence, not an eight-entry recent-history cache. Preserve
  every `petResetTombstones` entry within the record byte limit and never treat a record containing
  one as unreachable/prunable; otherwise a delayed old snapshot can resurrect a deleted pet.
- Push-store updaters receive an isolated deep copy. Keep all changes inside the updater and return
  the resulting object; direct nested mutation of a previously read snapshot is not transactional.
- Do not run `/story` or `/push` generation inline in the Telegram `getUpdates`
  loop. `app.bot` submits both to the bounded `telegram-command` executor;
  worker tasks create their own `httpx.Client` rather than sharing the polling
  client.
- Telegram `/story` and `/full_story` durable progress is append-only: add a new
  top-level stage key and increment the checkpoint revision; never rewrite an
  existing nested value or list. Preserve bounded `botGenerationReceipts` when
  registering a snapshot for the same pet, otherwise replay after a newer story
  overwrites `lastStory`/`lastFullStory` can apply stat deltas twice.
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
- The outfit input must reuse the dashboard conversation composer and its transform offset. A
  separate fixed overlay plus a calculated keyboard `bottom` inset double-counts iOS visual-viewport
  movement and lifts the submit button into the prompt.
- In the Telegram iOS WebView, do not rely on a focused form's native submit button for the outfit
  action. Keep an explicit button `onClick`, matching chat, and call the shared submit routine;
  otherwise a tap with the software keyboard open can be swallowed without dispatching `submit`.
- The interactive-travel entry MP4 must be warmed from the dashboard and keep its versioned URL
  cacheable. `preload="auto"` on the travel screen starts too late, while the generic `/figma/*`
  `no-store` header otherwise discards the warm download. Keep the scene at full viewport size;
  a fixed `402px` cap leaves side gutters in wider Telegram WebViews.
- A missing or failed transparent character foreground must count as a completed intro entrance.
  Never gate the intro timer only on the image animation flag: `onError` intentionally clears the
  foreground, and otherwise the reaction/departure flow stalls before the first story screen.
- Interactive travel makes one text call for all four lead-ins and may make eight media calls
  (image + video per part). Never charge them to the pet-generation `3/day` bucket. The debug reset
  must invalidate the travel ID before deleting its directory; deleting files alone lets an
  already-running provider call write them back.
- The active `x-ai/grok-imagine-video` OpenRouter endpoint rejects `4:5` with HTTP 400 even though
  the image provider supports it. Interactive-travel poster and video generation use `3:4`, the
  nearest accepted portrait ratio; do not infer the video allowlist from the image allowlist.
- Do not restore interactive-travel goals, target states, root matching, named-term checks, event
  counters, continuity anchors or validator-specific retries. The current product decision accepts
  independent episodes in exchange for a fixed four-task generator that is easy to control.
- `leadIn` is a fixed location-only template, not model output. Do not let it mention or paraphrase
  the sampled situation: `storyText` appends the bank situation verbatim, so a content-aware lead-in
  creates visible duplication. Do not normalize, clip or regenerate the bank's situation, question,
  four choices, four self-contained outcome branches or correct answer; those reviewed strings are
  the stability boundary of the feature. Keep legacy plans without outcome branches or with a
  separate explanation readable, but every newly sampled bank task must carry all four branches in
  choice order and display only the selected branch.
- Treat every image-provider result as untrusted even after a compressed-byte limit. Validate it
  with Pillow before any `convert()`/`load()`, reject either side above 8192 px or more than
  16 million pixels, and reopen the bytes for subsequent processing; Pillow's default bomb warning
  alone permits payloads far larger than this service can safely decode concurrently.
- Async paid media tasks must persist provider origin, task ID and a request fingerprint in the
  unified provider-task SQLite store before the first poll, while still holding the existing paid-stage
  lock. A `media_saved` receipt is not proof that the local file still exists: if the file is
  missing, resume poll/download using the same task instead of submitting again. Synchronous image
  endpoints expose no resumable task ID; keep automatic retries disabled, and treat a provider-
  accepted request whose response was lost before local persistence as an irreducible ambiguity
  unless the provider adds a client idempotency token.
- `reserve_background_story_video_bytes()` returns a downloaded provider result but does not mark
  its durable receipt `media_saved`; the caller must do so only after atomically persisting the MP4.
  Until then the receipt intentionally remains `accepted`, even when a valid local video exists.
- Dashboard speech portions use surface-specific message IDs to decide whether the shared bubble is
  visible. Advancing a portion must move both the conversation and feed IDs when either owns the
  current message; updating only the conversation ID makes multi-sentence feed replies disappear.
- Background mood completion and outfit replacement use different asset-set CAS semantics. A mood
  delta must retain the original `assetSetId` and merge against its base snapshot; a completed
  outfit must atomically verify that same base is still current and then replace it with the new
  `assetSetId`. Requiring the generated ID to equal the base silently leaves the old outfit active.
