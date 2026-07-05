from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")
SPEAKER_PATTERN = re.compile(r"^\s*(?:Human|User|Alex|Evelyn|Lucky|Rosie|Sebastian):\s*", re.I)


def _clean(text: str, limit: int = 700) -> str:
    cleaned = (
        text.replace("\\n", " ")
        .replace("`", "")
        .replace("###", "")
        .replace("\r", " ")
        .strip()
    )
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit].strip()


def _kind_for_text(text: str, section: str) -> str:
    lowered = text.casefold()
    if section == "seedchat":
        return "seed_reply"
    if "like" in lowered or "love" in lowered or "favourite" in lowered or "favorite" in lowered:
        return "preference"
    if "dislike" in lowered or "fear" in lowered or "afraid" in lowered or "resent" in lowered:
        return "conflict"
    if "home" in lowered or "station" in lowered or "moon" in lowered or "town" in lowered:
        return "world"
    if "friend" in lowered or "family" in lowered or "parents" in lowered or "pet" in lowered:
        return "relationship"
    if section == "preamble":
        return "voice_rule"
    return "backstory"


def _split_companion_sections(text: str) -> tuple[str, str, str]:
    preamble, _, rest = text.partition("###ENDPREAMBLE###")
    seedchat, _, backstory = rest.partition("###ENDSEEDCHAT###")
    return preamble, seedchat, backstory


def _sentences(text: str) -> list[str]:
    result: list[str] = []
    for raw in SENTENCE_PATTERN.split(text):
        cleaned = _clean(SPEAKER_PATTERN.sub("", raw))
        if len(cleaned.split()) < 4:
            continue
        result.append(cleaned)
    return result


def _companion_fragments(companion_dir: Path) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for path in sorted(companion_dir.glob("*.txt")):
        character = path.stem
        preamble, seedchat, backstory = _split_companion_sections(
            path.read_text(encoding="utf-8")
        )
        for section_name, section_text in (
            ("preamble", preamble),
            ("seedchat", seedchat),
            ("backstory", backstory),
        ):
            for index, sentence in enumerate(_sentences(section_text)):
                fragments.append(
                    {
                        "id": f"companion_app_{character.lower()}_{section_name}_{index:03d}",
                        "source_family": "external_a16z_companion_app",
                        "source_url": "https://github.com/a16z-infra/companion-app",
                        "license_note": (
                            "MIT licensed companion-app source text; test-mode local corpus"
                        ),
                        "kind": _kind_for_text(sentence, section_name),
                        "locale": "en",
                        "text": sentence,
                        "tags": [character.lower(), section_name],
                    }
                )
    return fragments


def _jsonl_fragments(path: Path, source_url: str, source_family: str) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        payload = json.loads(line)
        text = ""
        if isinstance(payload, dict):
            for key in ("text", "content", "response", "reply", "message", "dialogue"):
                if isinstance(payload.get(key), str):
                    text = payload[key]
                    break
        elif isinstance(payload, str):
            text = payload
        cleaned = _clean(text)
        if len(cleaned.split()) < 4:
            continue
        fragments.append(
            {
                "id": f"{path.stem}_{index:06d}",
                "source_family": source_family,
                "source_url": source_url,
                "license_note": (
                    "external local test corpus; verify upstream license before production"
                ),
                "kind": _kind_for_text(cleaned, "jsonl"),
                "locale": "mixed",
                "text": cleaned,
                "tags": [path.stem],
            }
        )
    return fragments


def _txt_fragments(path: Path, source_url: str, source_family: str) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for index, sentence in enumerate(_sentences(path.read_text(encoding="utf-8"))):
        fragments.append(
            {
                "id": f"{path.stem}_{index:06d}",
                "source_family": source_family,
                "source_url": source_url,
                "license_note": (
                    "external local test corpus; verify upstream license before production"
                ),
                "kind": _kind_for_text(sentence, "txt"),
                "locale": "mixed",
                "text": sentence,
                "tags": [path.stem],
            }
        )
    return fragments


def main() -> None:
    parser = argparse.ArgumentParser(description="Import external character source fragments.")
    parser.add_argument(
        "--companion-app-dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "companion-app-main" / "companions",
    )
    parser.add_argument("--input", type=Path, action="append", default=[])
    parser.add_argument("--source-url", default="local://external-test-corpus")
    parser.add_argument("--source-family", default="external_local_test_corpus")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "data"
        / "external_character_sources"
        / "fragments.jsonl",
    )
    args = parser.parse_args()

    fragments: list[dict[str, Any]] = []
    if args.companion_app_dir.exists():
        fragments.extend(_companion_fragments(args.companion_app_dir))
    for input_path in args.input:
        if input_path.suffix == ".jsonl":
            fragments.extend(
                _jsonl_fragments(input_path, args.source_url, args.source_family)
            )
        else:
            fragments.extend(_txt_fragments(input_path, args.source_url, args.source_family))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(
            json.dumps(fragment, ensure_ascii=False, separators=(",", ":"))
            for fragment in fragments
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(fragments)} fragments to {args.output}")


if __name__ == "__main__":
    main()
