# Android analytics and privacy

The Android APK sends only allow-listed schema-v1 product events to
`POST /api/android/analytics/events` with its normal bearer session. The
backend derives a stable pseudonymous actor with HMAC-SHA256 and writes events
to a bounded durable SQLite outbox before returning `202`.

The forwarding worker sends at most 50 events per request to
`GIGAGOCHI_STATS_BASE_URL/events`. It never follows redirects and authenticates
with `GIGAGOCHI_STATS_INGEST_TOKEN`. The HMAC key
`GIGAGOCHI_STATS_ACTOR_SECRET` is a separate secret and must never be placed in
the APK, WebView bundle, logs, or the Traction host.

`POST /api/android/privacy/delete`:

1. queues a server-to-server analytics tombstone;
2. refuses with `409` while owner-bound generation work is active;
3. removes generated media, provider receipts, generation/idempotency/story
   state, rate-limit state, and auth sessions;
4. records only a short-lived SHA-256 digest of the bearer token so a lost
   success response can be replayed idempotently for 24 hours.

Provider-side retention is governed by each provider's API and contract. The
backend removes local provider task IDs and polling URLs; if a provider does not
offer deletion, that limitation must be stated in the public privacy notice.

Production requires:

```text
GIGAGOCHI_STATS_BASE_URL=https://stats.multitool.works/p/gigagochi
GIGAGOCHI_STATS_INGEST_TOKEN=<random project token>
GIGAGOCHI_STATS_ACTOR_SECRET=<independent random secret, at least 32 bytes>
```

The production Compose file enables the durable SQLite rate limiter. Raw
prompts and replies are disabled in production logs; `AI_PROMPT_LOG_FULL` is
honored only when development auth is explicitly enabled.
