# Architecture

## Persistence

- The active MVP has no external relational database dependency. Pet/chat/memory state is
  local to the frontend origin, Telegram delivery state uses a SQLite WAL registry,
  generation-job recovery uses SQLite on the same persistent volume, and generated assets plus
  push state use Docker volumes. Local and production Compose files therefore do not start
  PostgreSQL.
- Sliding-window quotas are persisted in `rate_limits.sqlite3` on the shared `push_data` volume.
  Backend API media endpoints and bot `/story`/`/full_story` consume the same per-Telegram-user
  `generation` bucket atomically; `/push` is text-only and does not consume that bucket. The legacy
  `ENABLE_IN_MEMORY_RATE_LIMIT` name remains the compatibility switch for the durable limiter.
  Push snapshots use two layers: a 60/hour revision-deduplicated quota and a compact one-row
  fixed-window attempt counter at 120/hour, so replayed revisions cannot turn into unbounded
  registry transactions without creating hundreds of SQLite events per user.
- Scheduled background-story image/video submissions share one durable global fixed UTC 24-hour
  counter in the same SQLite store. Recovery of an existing file precedes charging; each new image
  or video provider submission attempt consumes one unit. The fail-closed default cap is zero, so
  text or recovered media is delivered without a paid call until production explicitly sets a cap.
- Telegram push rows retain the former per-record, logical-total-byte and record-count limits. Each
  ordinary update writes one record plus O(1) capacity metadata; a partial index supports
  capacity-triggered cleanup of only explicitly unreachable records whose latest activity is older
  than retention. Reachable records and records without a valid activity timestamp are never
  eviction candidates. The first SQLite startup imports the locked legacy JSON in one transaction
  with a durable source hash marker; production migration fails closed while that configured source
  is missing. A confirmed clean install must explicitly disable the source requirement. JSON remains
  available only as an explicit compatibility mode.
- Push-snapshot deletion appends a per-pet durable reset fence. Delete-fence records are not
  retention/capacity-prune candidates, so an arbitrarily late snapshot for an old pet cannot
  resurrect it; the existing per-record byte ceiling remains the fail-closed bound.
## Backend Jobs and Errors

- Backend phrase generation lives in `backend/app/services/pet_reply_engine/lite_generator.py`.
- Pet-generation executors use 4 image workers, 2 video workers and a bounded queue of 40.
  Job responses and descriptions are persisted in SQLite on the shared `push_data` volume; queued
  or running jobs are claimed with expiring leases and requeued after a backend restart. The API
  remains a single Uvicorn process, while generation uses dedicated image and video thread pools.
  Heartbeats return the exact renewed job IDs and immediately fence local records missing from that
  set. Each paid job stage also holds a bounded-bucket lifetime `flock`; a recovered owner waits for
  the previous provider call to leave that stage, then rechecks its SQLite lease before dispatch.
  Every durable job uses a job-derived asset-set ID. Its character bible metadata is atomically
  persisted before the first paid media call, and completed base, sad, happy, video and deterministic
  Kandinsky-comparison files are reused when the pipeline resumes. A partial API result is preserved
  while recovery re-enters the pipeline. An idempotency alias may point to an active job only when
  description and image provider match exactly; coalescing after quota reservation refunds that
  reservation.
- Pet creation is explicitly two-phase for every user. The creation screen stops blocking as soon
  as the normal character scene and primary video are ready, persists the still-running generation
  job ID in the local asset set, and enters the app. Sad and happy image/video assets continue in
  the background; the dashboard polls the same job and atomically applies newer URLs. Until those
  assets are ready, mood rendering uses the normal asset fallback, which is safe because new-pet
  stats start at 100.
- Generation timing telemetry is stored independently from expiring job responses in the same
  SQLite database. It records queue start, normal-assets readiness, full derived-assets completion
  and failure status. The diagnostic Telegram user can view personal 30-day average, median, p95
  and recent timings in the debug panel; collection starts from the deployment of this telemetry.
  Metrics older than 365 days are pruned independently from the shorter job/idempotency retention.
- Production operations alerts use the existing Telegram bot and a dedicated admin ID allowlist.
  AI failures, unexpected HTTP 500s, scheduler failures, queue saturation and stuck generation jobs
  are deduplicated before delivery so an incident does not create an alert storm. Dedup identities
  are hashed and capped at 1024; at most eight deliveries may be queued.
- Async pet creation jobs live in
  `backend/app/services/generation_job_service.py`. The TMA router owns only
  HTTP/auth adaptation and injects image generation, video generation, response
  building, and failure mapping callbacks. Image and video stages use separate
  bounded executors; the service is created lazily and shut down from the
  FastAPI lifespan. Shutdown atomically fences new paid stages, cancels queued futures, drains only
  stages already past the fence, then leaves unfinished jobs durable and unleased for restart claim.
- AI/provider exception inspection, public error shaping and failure-file
  logging live in `backend/app/services/ai_error_service.py`. Routers select the
  user-facing operation message but do not parse provider payloads or write log
  files themselves.
- Public API errors now have a safe `message` for every user and an optional
  `diagnostic` object only for Telegram IDs in
  `Settings.diagnostic_telegram_ids` (empty by default; production explicitly configures
  `62943754`). The
  frontend renders diagnostics in a separate expandable block only when the
  same user passes the debug-menu allowlist. Provider messages, exception text
  and internal validation paths are never used as ordinary UI copy.
- Every backend response receives `X-Request-ID`; unexpected exceptions and
  request-validation failures use the same safe JSON error envelope. Frontend
  `ApiError` keeps the request ID and optional trusted diagnostic separately
  from its public message.
- Telegram push and background-story scheduler iterations are supervised: a
  failed iteration is logged and retried instead of terminating the task.
  `/health` becomes degraded when an enabled scheduler task exits or its latest
  iteration failed.
- `/health` also performs create/write/fsync/delete probes for generated assets,
  push data and logs. Disk capacity is measured once per underlying device. The protected floor is
  the greater of the absolute and percentage watermarks; the generated-assets device additionally
  requires the largest configured media reservation, so health and paid-media admission share the
  same free-space boundary. Detailed dependency/storage diagnostics remain server-side for logs and
  alerts; the unauthenticated response exposes only `{"status":"ok"}` or
  `{"status":"degraded"}`. Probe results are briefly cached to bound fsync load from repeated
  health requests.
- Public LLM and interactive-media handlers acquire process-local
  global/per-user leases in `request_admission_service` from async FastAPI dependencies before a
  synchronous handler enters AnyIO's worker pool. Rejection is immediate (per-user 429, global
  503 with `Retry-After`) and dependency teardown releases the lease. `/health` is async and runs
  its synchronous probe body in the event loop's default executor, independently of a saturated
  AnyIO sync-handler pool.
- Paid image/video calls cross a fresh generated-volume admission check inside `MediaGateway`,
  immediately before provider dispatch. Before reserving storage, the gateway acquires one of a
  fixed set of `fcntl.flock` slot files on the shared push-data volume (4 image and 2 video slots by
  default), globally bounding paid calls across backend and bot processes. Per-kind storage
  headroom is represented by lifetime-locked reservation files under the generated volume, so it is
  counted across backend and bot processes. The provider slot is released when bytes arrive, while
  the reservation context propagates through validation/transformation and is released only after
  the caller's fsynced atomic replace. Kernel-released locks let the next admission reclaim files
  left by a crashed process. Admission preserves the stricter of the absolute and percentage
  free-space floors; failure occurs before the provider is called and maps to the stable retryable
  storage-capacity error.
- HTTP request bodies are bounded globally by `RequestBodyLimitMiddleware` before
  Pydantic parsing. Telegram bot commands use a bounded per-chat single-flight
  dispatcher and persist the `getUpdates` offset atomically on the shared push volume.
  Paid `/story` and `/full_story` updates also use a SQLite inbox keyed by Telegram
  `update_id`: text and every completed media stage are stored as append-only,
  revisioned progress. Media files use deterministic atomic paths so a retry can
  recover a file saved immediately before a progress checkpoint. Lease heartbeats return the exact
  renewed update IDs; lost callbacks are removed from the keeper. A bounded-bucket lifetime
  `flock` serializes the whole update callback across bot processes, and ownership is rechecked
  before paid stages, state commits and Telegram delivery.
- All async Kandinsky image/video and OpenRouter video calls share one durable `provider_tasks`
  SQLite table on the push-data volume. `global` scopes cover standalone workflows; pet generation
  uses `job:<job_id>:<stage>`. Scope, provider, exact origin, non-secret account namespace,
  operation and payload SHA-256 form the identity and its bounded hashed `flock` key. Each row is
  the complete `admitted -> accepted -> provider_failed|media_saved` state machine; only
  `admitted` has a null remote task ID, and a partial unique index binds non-null task IDs to one
  provider/origin/account. Orphaned admissions, exhausted capacity and ambiguous submissions fail
  closed. Accepted/media-saved rows are never evicted or cascaded with generation-job pruning.
- Pending pet generation request key, authoritative description and optional job ID are stored in
  separate frontend localStorage records while generation runs. Recovery keeps the oldest and
  seven newest valid records, migrates the legacy singleton marker, and removes only the completed
  request. If another tab already owns the user's active backend job, the 409 response carries that
  job's persisted description; the frontend adopts both values and resumes with one GET/poll rather
  than issuing another paid POST. Missing or malformed recovery identity is rejected.
- Pet creation always uses OpenAI for the primary normal/sad/happy images and
  keeps the existing OpenRouter video path. After the primary normal image and video are ready, the same job
  starts a best-effort Kandinsky branch for normal/sad/happy images plus a normal-state video.
  The nested Kandinsky asset set is persisted with the job response; its failure
  is reported separately and never invalidates the usable OpenAI result.

## Generated Media Retention

- Generated-media cleanup has its own supervised scheduler and starts independently of Telegram
  credentials and background-story generation. It performs an immediate pass, polls every five
  minutes, and uses a shared `flock` plus a six-hour global-sweep timestamp to avoid duplicate work
  across backend and bot. Known processing-temp directory prefixes older than one day are removed
  only under the exact `.private/processing-tmp` root.
- Telegram background-story PNG/MP4 files use a strict owner-directory and filename allowlist.
  Active owner directories are derived from a truncated hash of Telegram owner plus canonical pet
  ID, so same-pet owners cannot recover each other's files and raw Telegram IDs do not enter public
  `/static/generated` URLs. The pre-owner-bound pet path is read/collected only for legacy media.
  A six-hour global sweep and targeted post-generation/reset cleanup remove only files older than
  eight days that are unreferenced by both the locked push registry and durable bot-command inbox.
  The private generated-assets subtree is never traversed. Media reads refresh mtime before a
  SQLite-to-push-store ownership transition so the two persistence systems do not need one shared
  transaction for GC safety.
- Expired failed pet jobs delete their deterministic asset directory only when the terminal job has
  no published result. If deletion fails, the durable job row is restored for retry. Successful pet
  and travel assets without server-side lifetime proof are retained.

## Production Routing

- Production containers join the shared `public_proxy` Docker network. The live
  `gigagochi.serega.works` virtual host is served by the shared
  `bizzy-radio-caddy-1` container using `/opt/bizzy-radio/Caddyfile`; the
  repository `deploy/Caddyfile` is the standalone compose equivalent, not the
  currently active proxy configuration on the server.
- Backend and bot run as UID/GID `10001`. A network-isolated, capability-limited
  `volume-permissions` init service repairs stale ownership on the three writable named volumes
  before backend starts; the first migration from the former root image stops both writers first.
  Backend and bot receive identical storage admission, capacity and media-cleanup settings. All
  application containers use read-only root filesystems, dropped capabilities, `no-new-privileges`
  and bounded tmpfs/cache mounts. Python installs umask `077`; writable private files/directories are
  repaired to `0600`/`0700` semantics.
- Volume backup/restore stops backend and bot before copying SQLite/WAL state, validates every
  database with `PRAGMA quick_check`, fsyncs bundle metadata and verifies strict SHA-256 manifests.
  Restore verifies before stopping writers, stages both volumes, and rolls both back or leaves the
  writers stopped when an atomic cross-volume apply cannot complete.
- Production application settings have one source: `backend/.env`. Compose env is limited to
  topology, container security and explicitly documented resource knobs, so app settings cannot be
  silently shadowed by a second default. Paid-media kill switches and all provider/profile settings
  are declared in the shared backend env used by both backend and bot.
- The frontend is a private Telegram Mini App rather than a searchable landing page: global metadata
  emits `noindex,nofollow`; canonical, Open Graph and Twitter metadata describe only the root URL.
- Backend production and CI dependencies are exact, SHA-256-verified wheel pins in
  `backend/requirements.lock` and `backend/requirements-dev.lock`. The supported lock target is
  CPython 3.12 on glibc 2.28+ x86_64/aarch64, using both manylinux_2_28 and compatible
  manylinux2014 wheels where required; Docker and CI install with
  `--require-hashes --only-binary=:all:` and do not resolve the open ranges in `pyproject.toml`.

## LLM Routing

- All text generation crosses the provider-neutral synchronous gateway in
  `backend/app/llm`: typed request/response contracts, a provider registry,
  capability checks and task routing are separate from prompts and business
  services. OpenAI Platform, OpenRouter, GigaChat and optional LiteLLM are
  adapters behind that boundary.
- `LLM_PROFILE` selects a profile from `backend/data/llm_runtime.json` for text
  only. The `legacy` profile preserves the old behavior by following
  `AI_PROVIDER`; named profiles can override provider/model per task.
  `AI_PROVIDER` remains the media selector, so switching text to GigaChat does
  not move image or video generation away from OpenAI/OpenRouter.
- GigaChat uses a dedicated text-only sync adapter with Basic login/password
  token acquisition (`/v1/token`, then `/token` fallback), a thread-safe token
  cache, one refresh after HTTP 401 and TLS verification enabled by default.
  Existing JSON Schema outputs are translated to legacy functions. The
  `gigachat` profile routes `visible_reply`, `lite_facts`,
  `memory_extraction` and `memory_consolidation` to `GigaChat-3-Lightning`
  while leaving the profile default on `$GIGACHAT_MODEL`, so chat-facing and
  post-reply lightweight tasks can use a fast model without moving longer text
  tasks off the default.
- GigaChat 3.5 structured output uses a prompt JSON contract. The adapter may append only missing
  trailing `}`/`]` when the rest of the response is valid JSON; it does not repair strings, commas
  or mismatched brackets.
- Production mounts the same `llm_runtime.json` and optional custom-CA directory
  into backend and bot. `/health` validates the active profile, configured
  providers, local dependencies, credentials presence and CA-file presence;
  it intentionally does not make a paid provider request.

## Media Routing

- Completed interactive travels are snapshotted as `finale.json` beside their generated assets.
  The normal final continuation saves the snapshot best-effort, and reopening a completed client
  session uploads its localStorage copy so journeys completed before this persistence path can be
  recovered. The local-only `/admin/travel-finale` lab reads these immutable source snapshots,
  compiles an editable 15-second video prompt, and stores prompt-addressed finale attempts without
  regenerating the journey.
- `/static` is served through `PublicMediaStaticFiles`: only media suffixes are public even when
  prompt, owner and finale metadata are colocated under the generated-assets volume.
- Reference-to-video is an explicit media capability. OpenRouter requests may use either one frame
  image or a set of reference assets; the finale lab uses the prior travel MP4 URLs with Seedance
  2.0 while ordinary pet and story animation remains image-to-video on the configured model.

- Image and video generation crosses the provider-neutral synchronous gateway in
  `backend/app/media`. Image requests declare t2i or i2i from the presence of reference
  images; video requests currently use i2v. Capability checks happen before transport calls.
- Pet-scene videos from every provider are post-processed provider-neutrally with FFmpeg into
  a forward-then-reverse MP4. The first-frame preroll and duplicate endpoint frames are removed,
  so the frontend only needs native looping and never seeks H.264 backwards. Provider bytes are
  forced through the local MOV/MP4 demuxer with the protocol whitelist restricted to `file` and
  external data references disabled.
- `MEDIA_PROFILE` selects `backend/data/media_runtime.json` independently from `LLM_PROFILE`.
  The `legacy` profile keeps images on `AI_PROVIDER` and video on OpenRouter. The `kandinsky`
  profile sends t2i/i2i tasks to Kandinsky 6.0 and i2v to Kandinsky 5 HD.
- Media profiles support provider overrides by task label. `ImageRequest.provider` is used internally
  by the parallel Kandinsky comparison branch; absent that field, normal profile routing applies.
  Video is routed independently; the comparison branch explicitly requests Kandinsky for its
  normal-state video while happy and sad remain static.
- Kandinsky uses Bearer authorization, `k6-image-t2i` for requests without references,
  `k6-i2i` for requests with references, and `k5-i2v-hd` for image-to-video. Task creation, polling and result download are bounded
  by retries and timeout. `/health` validates active media credentials without a paid request.
- Kandinsky prompt adaptation is task-aware and deterministic. Pet creation, background-story
  illustration and travel use compact Russian templates assembled from dynamic scene/identity
  fields; arbitrary LLM translation is not part of the media path. Isolated pet t2i uses a
  portrait resolution, while story/travel keep their own composition contracts.
- The Kandinsky pet-creation frame intentionally targets premium handcrafted collectible art-toy
  photography: melancholic childlike proportions, dense layered costume, one oversized wearable
  story object, matte resin and visibly worn mixed materials. The isolated identity request stays
  on white; the later scene request owns the mossy forest, cinematic light and depth of field.
- The creation screen does not expose provider selection. The dashboard debug panel can swap the
  rendered OpenAI asset set for the static Kandinsky comparison set without mutating the persisted
  primary assets; provider switching is disabled until the comparison set is complete.

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
  interaction updates. Valid character taps persist `petTapProgress`; every fifth
  tap adds 15 happiness with the normal interaction/tick semantics. The first such
  reward per pet and browser session uses the standard voiced reply bubble, guarded
  by `sessionStorage`; scene taps cannot dismiss that reply, and its own timer hides
  it after five seconds. `localPetStorage.ts` re-exports the public functions for
  compatibility but owns only persistence/migration and non-stat overlays.
- `localPetSnapshotRevision.ts` owns the independent per-pet dirty signal used by push sync;
  `localPetScopedCleanup.ts` is the single reset/replacement boundary for auxiliary per-pet browser
  state. Individual stores retain tombstones while persistent removal is unavailable.
- Synchronous pet-state mutations use one localStorage snapshot: read the fresh durable value,
  enforce the expected pet ID, apply the operation, verify the base has not already changed, write
  once and require an exact read-back. This is intentionally last-writer-wins rather than a local
  transaction; quota failures and observed lost races return failure/the actual winner instead of a
  false success. Travel-impact receipts remain inside that snapshot for idempotency.
- Dashboard browser effects are isolated from `PetDashboard.tsx`:
  `useConversationKeyboardOffset` owns Visual Viewport keyboard positioning and
  `usePetPushSnapshotSync` owns throttled server snapshot reconciliation. Background generation
  polling backs off through 2/4/8/15/30/60 seconds, pauses while offline or hidden, and resumes
  immediately when connectivity/visibility returns.
- Speech admin orchestration stays in `SpeechAdmin.tsx` (load, validate, save,
  deploy and polling); JSON path manipulation and the runtime/tone/template/raw
  editors live in `SpeechAdminEditors.tsx`.

## API Contracts

- FastAPI's Pydantic schemas are the source of truth for shared HTTP contracts.
  `backend/scripts/export_openapi.py` exports `frontend/openapi.json`, and
  `openapi-typescript` generates `frontend/src/lib/generated/openapi.d.ts`.
  Backend and frontend checks fail when either generated artifact is stale.
- `frontend/src/lib/apiTransport.ts` owns fetch, safe public errors and malformed
  JSON handling. It rejects and cancels response bodies above 8 MiB before runtime parsing.
  Requests have a five-minute default timeout; synchronous media endpoints and
  the local Next proxy use a separate twenty-minute window. `apiContracts.ts` validates successful payloads at runtime and
  normalizes nullable wire fields into the frontend domain before `api.ts`
  applies pet-specific request/response mapping.
- Local speech-admin routes also expose explicit Pydantic response models.
  `adminSpeechContracts.ts` derives their TypeScript types from OpenAPI and
  validates manifest/save/publish payloads before admin UI state sees them.

## Pet Replies

- LLM user-memory extraction and consolidation are isolated in
  `backend/app/services/pet_reply_engine/memory_operations.py`: JSON schemas,
  operation normalization and prompt assembly live there; provider calls cross
  the shared LLM gateway.
- Frontend user memory is schema v2 with lazy v1 migration. Each stored memory
  has a `memoryClass` (`core` / `fact` / `episode`), independent `recordedAt`
  and optional factual `occurredAt`; old records keep their original
  `createdAt` as recording time and do not invent an occurrence time.
- `/api/chat` carries `nowIso`, timezone, history message timestamps and memory
  timestamps. Backend prompt assembly renders absolute local time plus a
  deterministic relative label such as `вчера` or `позавчера`.
- Episodic user memories and recalled chat windows older than 30 days are
  excluded from spontaneous recall but remain available for explicit memory,
  identity and temporal questions. Episodic memory selected within the last 14
  days is suppressed by the spontaneous mention cooldown.
  `lite_generator.py` owns visible replies, context routing and lite/story facts.
- Deterministic recent-story tokenization, Russian stemming, event selection
  and prompt-block formatting live in
  `backend/app/services/pet_reply_engine/recent_events.py`; the reply engine and
  lite-fact conflict filters consume that shared event policy.
- Telegram background stories keep the ten full `recentStoryEvents` records for
  UI/chat continuity and a separate compact `storyNoveltyHistory` of up to 400
  title/tag signatures. A lexical duplicate candidate gets one regeneration;
  the compact archive is anti-repeat data, not story source material.
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
  24 hours, and partial server `statsPatch` updates only the affected keys.
- Pet death is persisted with `zeroStatSinceAt` and `diedAt`. A pet dies only
  after any one stat remains exactly zero for more than 24 continuous hours;
  restoring that stat clears its zero timer. New push snapshots enable the same
  lifecycle on the backend so dead pets stop receiving proactive pushes and
  background stories, while legacy snapshots remain active until refreshed by
  a new client.
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
  polling loop owns HTTP request formatting. Automatic pet pushes use at most
  three local-time windows per user (09:00, 15:00, 21:00), with a bounded
  delivery window so missed jobs are not replayed at night. Their reason comes
  from the current decayed stats, missing the owner, or the latest stored story;
  visible output is capped at 120 characters and two sentences. `/push` runs
  the same generation path manually without consuming an automatic window.
  Runtime `/story` and `/push` work is submitted by `app.bot` to a bounded worker
  pool so AI/image generation does not block `getUpdates` polling. Story stat commits
  atomically append a bounded per-pet generation receipt to the push registry; the
  receipt makes a replay idempotent even when a newer operation has overwritten
  `lastStory` or `lastFullStory`.
- A story-based push may use only the newest background event by `createdAt`,
  and only while it is at most 12 hours old. Its reason explicitly frames the
  event as something that happened recently; when no fresh event exists, the
  push falls back to a non-story topic instead of recalling an older episode.
- The push registry is SQLite WAL-backed and all reads and mutations go through
  `backend/app/services/telegram_push_store.py`. `BEGIN IMMEDIATE` preserves transactional updater
  semantics across backend and bot processes, while `synchronous=FULL` protects committed rows.
  The legacy locked/atomic JSON implementation remains selectable for rollback compatibility.
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
- Ordinary `/story` generation never serializes `recentStoryEvents`, their
  titles, tags or prose into the model prompt. Recent events are used only by
  backend code to select a less-used structural direction and by the
  post-generation lexical novelty check. Explicit serialized stories such as
  `/full_story`, and conversational recall, remain separate continuity paths.
- The ordinary `/story` character dossier excludes every mutable
  `lite_overlay` durable constraint. Background-story aftermath may still
  persist real consequences for dialogue, but an appearance consequence is
  accepted only when typed as `lasting_injury`; an episode-invented ability
  cannot become character canon.
- Full-story generation is hierarchical: create a four-part event plan, reject
  weak plans, render first-person scenes from the accepted plan, then compare
  the prose with that plan. Each planned part has an SVO event, before/after
  state, trigger, protagonist and opposition goals, decisive action, result,
  state changes and a carry-forward object ledger. A rejected plan may be
  repaired up to three times while preserving its sound causal core; each model
  stage has a 240-second minimum timeout. Plan quality is role-aware: the
  inciting part establishes commitment, the complication changes the plan, the
  turn creates an observable path to resolution, and only the finale must solve
  the overall goal.
- Full-story rendering keeps that detailed hidden plan but exposes only one or
  two short first-person sentences per part. The render schema represents each
  sentence as a separate item, and normalization joins them into one compact
  visible scene.
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
  Videos API with `OPENROUTER_VIDEO_MODEL` (default `x-ai/grok-imagine-video`),
  `resolution=480p`, `aspect_ratio=9:16`, `duration=4`, `generate_audio=false`,
  and a locked-camera prompt for one slow stepped stop-motion blink. The saved public `assetSet.images` point
  to the normalized scene PNG and `assetSet.videoUrl` points to the generated
  mp4. As soon as that required MP4 is saved, the running job exposes its base
  `result` and the frontend creates the pet and enters the dashboard. The same
  job then generates `teen-sad.png` from the exact composed idle scene and
  `teen-sad.mp4` from that sad frame in the background, moving through
  `generating_sad_image` and `generating_sad_video`. The job id and background
  phase are persisted with the frontend asset set, polled after navigation, and
  merged atomically only when both sad assets are ready. Background failure is
  recorded without failing or removing the already usable pet. Image generation
  and video polling use separate configurable thread pools (defaults: four image
  workers and two video workers), so slow video polling does not occupy an image
  worker. The dashboard renders the selected mp4 as the full-height background with the same
  normalized image as poster/fallback; there is no separate centered pet sprite,
  blink overlay, tap animation, or background removal step on the active path.
  If any of hunger, happiness, or energy is strictly below 30, the dashboard uses
  the sad image/video pair when available. Debug UI can force that visual pair
  without mutating stats or mood and shows the two-stage background progress.
  Legacy asset sets without a generation job probe deterministic sibling files
  `teen-sad.png` and `teen-sad.mp4` once on dashboard mount; this lets one-off
  production backfills become visible without rewriting browser localStorage.
  Sad scene generation is two-pass: the first edit creates the sitting/crying
  pose, and a second multi-reference edit treats the idle scene as the
  authoritative camera/scale while using the first result only as a pose
  reference. The temporary pose image is removed after refinement.
  Happy scene generation protects the full-frame composition deterministically:
  both image-edit passes operate on a fixed centered `480x720` character region
  extracted from the normalized `720x1280` idle scene. The refined region is
  composited back into the exact same `(120, 320)-(600, 1040)` box with an
  inward feather; every pixel outside that box remains from the idle scene.
  This compensates for image providers that accept references but no edit mask.
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
  prompt assembly keeps the latest 12 messages instead of dropping
  prior pet replies.
- `frontend/src/lib/localPetMutationLock.ts` serializes asynchronous per-pet browser mutations in
  separate `conversation`, `memory`, and `travel` scopes through native Web Locks. Chat and memory
  fail closed when that API is unavailable; interactive travel instead uses fresh durable reads and
  exact-write LWW because backend travel CAS is authoritative. LocalStorage leases are not treated
  as a mutex. Chat/ambient/proactive read their durable inputs only after acquiring `conversation`;
  memory extraction/consolidation rereads and merges the latest memory under `memory`. Chat UI and
  ambient dedupe refs reconcile matching `storage` events from other tabs.
- Normal `/api/chat` replies return `happinessDelta` from the same structured
  visible-reply pass. Its scale is `20, 0, -20, -40, -60, -80`; only actual
  user send handlers apply it to local `stats.happiness` with a `0..100` clamp.
  Ambient, proactive, push, and synthetic food-reaction turns do not change it.
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

## Interactive Travel Pilot

- `/pet/[id]/travel` is the production travel flow: simple curated destination choices, character reaction, compact one-sentence portions, a direct task-bank question, user choice, generated consequence and a separate next episode in the same location theme.
- The backend owns compact response mapping plus illustration/video generation; the frontend persists
  presentation progress locally and renders video-only backgrounds across the full Telegram
  viewport. Generated PNGs are intermediate video sources, not visible background fallbacks.
- Interactive-travel stat effects commit with bounded local-only receipts inside the same
  `LocalPetState` write. `appliedResultParts` in the separate travel session is only a presentation
  projection and a legacy-migration hint; push snapshots intentionally exclude the receipts.
- New interactive travels contain four independent episodes joined only by the
  destination theme. The backend selects one unused task from the reviewed
  task bank at a time; the model generates only that task's short fantasy
  setup. The direct question, four choices, correct answer and child-readable
  explanation stay server-authoritative and are never rewritten by the model.
- The next episode is selected and generated only after the current answer.
  `arcPlan` stores the generator version, used task IDs and answer metadata for
  generated episodes; it does not pre-generate a shared plot or ending. After
  episode four, the client adds one short line that the character is tired and
  goes home. Unfinished `task-bank-location-v2` sessions retain their prepared
  episodes for backward compatibility.
- Interactive travel uses its own daily rate-limit bucket. A diagnostic reset tombstones the
  current travel ID, deletes its generated directory and clears that diagnostic user's travel
  bucket so late image/video completions cannot restore stale assets.
- If an explicit new-pet start meets an owner-bound active session whose local record was lost, the
  backend conflict carries its travel ID. `interactiveTravelStartRecovery.ts` accepts only the
  validated 409/code/ID tuple, durably queues cancellation, calls the owner-checked cancel endpoint,
  and retries start exactly once. Network/ambiguous cancellation failures are not retried and keep
  the outbox entry.
- Interactive-travel pet identity uses the optional validated `LocalPetChatContext.petId`; name,
  stats and derived asset URL changes do not invalidate an active session. Payloads from legacy
  clients without `petId` retain the old content fingerprint fallback. Start preflight does not write
  a placeholder: free text generation runs outside SQLite, then one atomic commit creates the owner
  proof and session. Concurrent identical starts may both generate text, but only one result becomes
  durable and the loser replays it; a changed request reports `INTERACTIVE_TRAVEL_ALREADY_ACTIVE`.
- `InteractiveTravelSessionStore` owns exactly two tables in one private SQLite database: immutable
  owner proof/tombstones and authoritative narrative sessions. Continue follows the same
  preflight/free-generation/atomic-CAS shape; an exact retry replays the committed response and a
  stale different request conflicts. There are no durable `starting` states, operation tokens or
  expiring operation leases.
- Illustration, animation and finale capture validate owner, pet, part and exact narrative
  fingerprint before work and recheck that same fingerprint before late persistence. Cancellation
  tombstones the owner and deletes narrative state before cancelling generated media, fencing an
  in-flight completion. Completed sessions are retained for 180 days; completed owner proof remains
  after session pruning, while missing authoritative state makes late recovery fail closed.
- Access is canaried on both UI and API through `INTERACTIVE_TRAVEL_PILOT_TELEGRAM_IDS`; the initial production allowlist contains only Telegram ID `62943754`.
- Destination suggestions are sampled from a small server-side vocabulary without an LLM call.

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
- Admin batch saves are serialized across processes with `flock`; unique backups and replacement
  files are fsynced, and a partial multi-file failure restores every original before returning.
  The in-memory publish registry keeps all active jobs but only the newest terminal jobs, with a
  hard total target of 32.
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
