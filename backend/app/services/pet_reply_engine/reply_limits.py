from __future__ import annotations

import re

MAX_REPLY_CHARS = 300


def clamp_reply_text(text: str, limit: int = MAX_REPLY_CHARS) -> str:
    clean = text.strip()
    if len(clean) < limit:
        return clean

    head = clean[:limit].rstrip()
    natural_break = max(head.rfind("."), head.rfind("!"), head.rfind("?"), head.rfind("…"))
    if natural_break >= max(80, limit // 2):
        return head[: natural_break + 1].rstrip()

    space_break = head.rfind(" ")
    if space_break >= max(80, limit // 2):
        head = head[:space_break].rstrip()

    head = re.sub(r"[,.!?;:]+$", "", head).rstrip()
    return f"{head[: limit - 1].rstrip()}…"
