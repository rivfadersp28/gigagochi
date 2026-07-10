from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas import (
    LiteFactExtractionRequest,
    LocalAmbientRequest,
    LocalChatHistoryItem,
    LocalChatRequest,
    LocalPetChatContext,
    LocalPetMemoryContext,
    MemoryExtractionRequest,
)
from app.services.background_story_service import generate_background_story
from app.services.character_dossier import effective_character_data
from app.services.chat_service import chat_with_local_pet
from app.services.image_service import create_character_bible
from app.services.pet_reply_engine.lite_generator import (
    extract_lite_overlay_patch_from_reply,
    generate_ambient_pet_message,
)
from app.services.pet_reply_engine.memory_operations import extract_user_memory_operations

DEFAULT_DESCRIPTION = (
    "небольшой шестилапый зверёк из потемневшей меди, который слышит дождь через полые рога"
)
MODERN_INTRUSIONS = (
    "смартфон",
    "телевизор",
    "вайфай",
    "wi-fi",
    "лифт",
    "супермаркет",
    "офис",
    "автомобиль",
)


def _memory_items(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if operation.get("type") not in {"remember_user_fact", "replace_user_fact"}:
            continue
        text = operation.get("text")
        kind = operation.get("kind")
        if not isinstance(text, str) or not isinstance(kind, str):
            continue
        result.append({"id": f"eval-memory-{index}", "kind": kind, "text": text})
    return result[:5]


def _modern_intrusions(texts: list[str]) -> list[str]:
    corpus = "\n".join(texts).casefold()
    result: list[str] = []
    for token in MODERN_INTRUSIONS:
        adoption = re.search(
            rf"(?:у меня есть|у меня стоит|я пользуюсь|я живу[^.?!]{{0,50}}(?:в|рядом с))"
            rf"[^.?!]{{0,50}}{re.escape(token)}",
            corpus,
        )
        if adoption:
            result.append(token)
    return result


def run(description: str) -> dict[str, Any]:
    bible = create_character_bible(description)
    identity = bible.get("identity") if isinstance(bible.get("identity"), dict) else {}
    name = str(identity.get("name") or "Искра")
    pet = LocalPetChatContext.model_validate(
        {
            "name": name,
            "description": description,
            "stage": "adult",
            "mood": "happy",
            "stats": {"hunger": 74, "happiness": 86, "energy": 78},
            "characterBible": bible,
        }
    )

    history: list[LocalChatHistoryItem] = []
    chat_outputs: list[dict[str, str]] = []
    for message in (
        "Кто ты, где живёшь и чем обычно занимаешься?",
        "Что ты любишь и чего боишься?",
        "У тебя дома есть неоновый телевизор и лифт?",
    ):
        response = chat_with_local_pet(
            LocalChatRequest.model_validate(
                {"message": message, "pet": pet, "history": history[-12:]}
            )
        )
        chat_outputs.append({"message": message, "reply": response.reply})
        history.extend(
            (
                LocalChatHistoryItem(role="user", text=message),
                LocalChatHistoryItem(role="pet", text=response.reply),
            )
        )

    memory_message = "Меня зовут Сергей, я люблю крепкий чай с чабрецом."
    memory_reply = chat_with_local_pet(
        LocalChatRequest.model_validate(
            {"message": memory_message, "pet": pet, "history": history[-12:]}
        )
    ).reply
    extraction = extract_user_memory_operations(
        MemoryExtractionRequest.model_validate(
            {
                "message": memory_message,
                "reply": memory_reply,
                "pet": pet,
                "history": history[-12:],
                "nowIso": datetime.now(UTC).isoformat(),
                "timezone": "Europe/Moscow",
            }
        )
    )
    memory_items = _memory_items(extraction.operations)
    memory_context = LocalPetMemoryContext.model_validate(
        {
            "summary": "Владелец представился и назвал любимый напиток.",
            "userProfile": "Владелец предпочитает конкретные ответы без выдуманных фактов.",
            "relevantMemories": memory_items,
        }
    )
    recall_message = "Как меня зовут и что я люблю?"
    recall_reply = chat_with_local_pet(
        LocalChatRequest.model_validate(
            {
                "message": recall_message,
                "pet": pet,
                "history": history[-10:]
                + [
                    LocalChatHistoryItem(role="user", text=memory_message),
                    LocalChatHistoryItem(role="pet", text=memory_reply),
                ],
                "memoryContext": memory_context,
            }
        )
    ).reply
    chat_outputs.append({"message": memory_message, "reply": memory_reply})
    chat_outputs.append({"message": recall_message, "reply": recall_reply})

    fact_patch, _ = extract_lite_overlay_patch_from_reply(
        LiteFactExtractionRequest.model_validate(
            {
                "message": "У тебя дома есть неоновый телевизор и лифт?",
                "reply": chat_outputs[2]["reply"],
                "pet": pet,
                "history": history[-12:],
            }
        )
    )

    story = generate_background_story(
        pet=pet,
        memory_context=memory_context,
        history=history[-12:],
        now_iso=datetime.now(UTC).isoformat(),
        timezone="Europe/Moscow",
    )

    ambient_replies: list[str] = []
    for _ in range(3):
        ambient = generate_ambient_pet_message(
            LocalAmbientRequest.model_validate(
                {
                    "pet": pet,
                    "history": history[-12:],
                    "recentAmbientReplies": ambient_replies,
                    "memoryContext": memory_context,
                    "replyMaxChars": 160,
                }
            )
        )
        ambient_replies.append(ambient.reply)

    visible_texts = [item["reply"] for item in chat_outputs]
    visible_texts.extend((story.story_text, *ambient_replies))
    return {
        "inputDescription": description,
        "character": effective_character_data(pet),
        "chat": chat_outputs,
        "memoryExtraction": extraction.operations,
        "memoryRecallPassed": "серге" in recall_reply.casefold()
        and "ча" in recall_reply.casefold(),
        "unsupportedAssistantCanonPatch": fact_patch,
        "story": {
            "title": story.title,
            "text": story.story_text,
            "aftermath": story.lite_overlay_patch,
        },
        "ambientReplies": ambient_replies,
        "checks": {
            "modernIntrusions": _modern_intrusions(visible_texts),
            "uniqueAmbientReplies": len(set(map(str.casefold, ambient_replies)))
            == len(ambient_replies),
            "assistantReplyDidNotBecomeCanon": fact_patch is None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.description)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(f"{output}\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
