# Interactive travel media race

## Status

Investigation only. The production behavior is not fixed yet. This document is the handoff for a separate implementation task.

## Reproduction evidence

Travel: `interactive-travel-2a9dca8f68554ef0bbcfbf319c6eab28` (`Холодный свет: путь Астеля в пещеру`).

Production timestamps on 2026-07-14 UTC:

- part 1 image saved at `18:33:14`; no animate request followed;
- part 2 image saved at `18:35:21`; no animate request followed;
- part 3 image saved at `18:37:30`; no animate request followed;
- part 4 image saved at `18:38:56`, video saved at `18:40:07`;
- part 5 image saved at `18:42:06`;
- completed finale snapshot saved at `18:48:19` with part 5 `backgroundVideoUrl = null`;
- part 5 video saved at `18:49:29`, after the snapshot.

The raw production snapshot is preserved as
`backend/static/generated/interactive-travel-2a9dca8f68554ef0bbcfbf319c6eab28/finale.server.json`.

## Root cause

Confidence: 99% from production timestamps, access logs, generated files and frontend control flow.

`ensureIllustration` and `ensureAnimation` capture `requestEpochRef.current`. After a slow request succeeds, both functions discard the response when the user has already advanced and the epoch changed:

- `frontend/src/components/InteractiveTravelScreen.tsx`, illustration guard around lines 383-392;
- the analogous animation guard around lines 334-339.

Image generation took roughly 55-60 seconds. For parts 1-3 the user advanced before it completed. The backend successfully wrote PNG and video-source files, but the frontend discarded their URLs. Since animation starts only when the active part in session has `backgroundImageUrl`, `/animate` was never called for those parts.

Part 5 demonstrates the second race. The animation continued after the journey completed. The backend finale save/capture stores the client-supplied travel immediately and does not reconcile media already present or still generating. The MP4 appeared 70 seconds after `finale.json`, leaving a stale null URL in the snapshot.

Errors are also sticky for the lifetime of the screen: keys placed in `failedIllustrationsRef` or `failedAnimationsRef` are not retried automatically.

## Proposed fix

1. Separate navigation/UI staleness from media persistence. A successful media response must patch the matching `travelId + partNumber` even if `requestEpoch` changed. The epoch may suppress visible UI transitions, but must not discard the URL.
2. Start animation from the successful illustration result directly, or enqueue it server-side, rather than relying only on a later effect for the currently active part.
3. Make the backend the canonical source of media state. Add a reconciliation function that discovers existing `interactive-travel-part-NN.png/mp4` files and fills missing URLs.
4. Run reconciliation before both automatic finale saving and `/finale/capture`. This also handles an animation that finishes after the client state was last written.
5. For in-flight animation at completion, either wait for bounded pending jobs or update `finale.json` atomically when the video finishes.
6. Replace permanent per-screen failure suppression with bounded retry/backoff plus a manual retry path.

## Required tests

- Resolve `illustrate` after the presentation epoch advances; the matching part still receives and persists `backgroundImageUrl`.
- Resolve `animate` after the epoch advances; the matching part still receives and persists `backgroundVideoUrl`.
- A completed travel captured before animation resolves is updated when the MP4 is written.
- Finale reconciliation adds URLs for existing media files without inventing URLs for missing files.
- Failed animation can be retried without reloading the entire app.
- Switching to a different `travelId` must still reject late responses from the previous journey.

## Local fixture

All five local MP4 files now exist under
`backend/static/generated/interactive-travel-2a9dca8f68554ef0bbcfbf319c6eab28/`.
Parts 1-3 were generated locally from the preserved production `video-source.png` files using the same commit and identical `backend/data/media_runtime.json` as production.
