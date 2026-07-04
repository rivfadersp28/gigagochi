from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from app.config import get_settings


class MissingOpenAIAPIKey(RuntimeError):
    pass


def chat_reasoning_effort_kwargs(reasoning_effort: str | None) -> dict[str, str]:
    effort = (reasoning_effort or "").strip()
    return {"reasoning_effort": effort} if effort else {}


@lru_cache
def get_openai_client() -> OpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise MissingOpenAIAPIKey
    return OpenAI(api_key=settings.openai_api_key, max_retries=settings.openai_max_retries)
