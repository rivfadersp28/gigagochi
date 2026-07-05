from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{4,}")


@dataclass(frozen=True)
class ExternalSourceFragment:
    id: str
    source_family: str
    source_url: str
    license_note: str
    kind: str
    locale: str
    text: str
    tags: tuple[str, ...] = ()


def _data_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "external_character_sources"


def _fragment_from_payload(payload: dict[str, Any]) -> ExternalSourceFragment | None:
    fragment_id = str(payload.get("id") or "").strip()
    text = str(payload.get("text") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()
    if not fragment_id or not text or not source_url:
        return None
    tags = tuple(str(item).strip() for item in payload.get("tags") or () if str(item).strip())
    return ExternalSourceFragment(
        id=fragment_id,
        source_family=str(payload.get("source_family") or "external").strip(),
        source_url=source_url,
        license_note=str(payload.get("license_note") or "").strip(),
        kind=str(payload.get("kind") or "snippet").strip(),
        locale=str(payload.get("locale") or "en").strip(),
        text=text[:700],
        tags=tags,
    )


def load_external_source_fragments(
    *,
    data_dir: Path | None = None,
) -> tuple[ExternalSourceFragment, ...]:
    root = data_dir or _data_path()
    if not root.exists():
        return ()
    fragments: list[ExternalSourceFragment] = []
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fragment = _fragment_from_payload(json.loads(line))
            if fragment:
                fragments.append(fragment)
    return tuple(fragments)


def _tokens(text: str | None) -> set[str]:
    return {word.casefold() for word in WORD_PATTERN.findall(text or "")}


def select_external_character_fragments(
    *,
    user_description: str,
    count: int = 10,
    rng: random.Random | None = None,
    fragments: tuple[ExternalSourceFragment, ...] | None = None,
) -> tuple[ExternalSourceFragment, ...]:
    available = fragments if fragments is not None else load_external_source_fragments()
    if not available or count <= 0:
        return ()
    chooser = rng or random.SystemRandom()
    user_tokens = _tokens(user_description)

    by_kind: dict[str, list[ExternalSourceFragment]] = {}
    for fragment in available:
        by_kind.setdefault(fragment.kind, []).append(fragment)

    selected: list[ExternalSourceFragment] = []
    preferred_kinds = (
        "seed_reply",
        "voice_rule",
        "backstory",
        "preference",
        "conflict",
        "world",
        "relationship",
        "quirk",
    )
    for kind in preferred_kinds:
        candidates = by_kind.get(kind, [])
        if not candidates:
            continue
        scored = sorted(
            candidates,
            key=lambda item: (
                len((_tokens(item.text) | set(item.tags)) & user_tokens),
                chooser.random(),
            ),
            reverse=True,
        )
        selected.append(scored[0])
        if len(selected) >= count:
            return tuple(selected)

    remaining = [item for item in available if item not in selected]
    chooser.shuffle(remaining)
    return tuple([*selected, *remaining[: max(0, count - len(selected))]])


def external_fragments_prompt_block(fragments: tuple[ExternalSourceFragment, ...]) -> str:
    if not fragments:
        return "нет локального внешнего корпуса"
    lines: list[str] = []
    for fragment in fragments:
        lines.append(
            f"- {fragment.id} [{fragment.source_family}; {fragment.kind}; {fragment.locale}]: "
            f"{fragment.text}"
        )
    return "\n".join(lines)
