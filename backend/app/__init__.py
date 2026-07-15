"""Application package defaults."""

from __future__ import annotations

import os

# Runtime stores contain user dialogue, Telegram identifiers, provider task receipts,
# prompts and generated media. Backend and bot deliberately share one UID, so no
# group/world access is required for any file they create.
os.umask(0o077)
