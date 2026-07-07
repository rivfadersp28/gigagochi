# Gotchas

- Do not send the whole story dataset in every reply prompt. Use `assemble_pet_context` to select a small `WORLD_CONTEXT` only when the current request/history/memory has story-sphere signals.
- Do not force story retrieval for every ambient phrase. Idle phrases should stay varied and dialogue-oriented; retrieval is only for relevant context.
- Do not add a post-check/regenerate loop for replies unless explicitly requested. The current architecture avoids point 5 and keeps generation single-pass, with optional background extraction only for new story entities.
- `storyLibraryPatch` is returned under `debug`, but frontend uses it as data, not just debug UI. Removing debug payload can break local story-library persistence.
- The worktree may contain unrelated dirty frontend/deploy files. Do not stage or revert them unless the task explicitly targets them.

