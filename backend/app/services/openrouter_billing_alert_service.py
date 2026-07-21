from __future__ import annotations

import json

from app.config import get_settings
from app.services.ops_alert_service import notify_telegram_alert

OPENROUTER_CREDITS_URL = "https://openrouter.ai/settings/credits"
OPENROUTER_CREDITS_ALERT_KEY = "openrouter:credits-exhausted"

_CREDIT_ERROR_MARKERS = (
    "insufficient credit",
    "insufficient balance",
    "not enough credit",
    "credit balance",
    "add more credit",
    "requires more credit",
    "can only afford",
)


def _compact_message(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(value)
    return " ".join(serialized.split()).casefold()


def openrouter_credits_exhausted(
    *,
    status_code: int | None,
    provider_message: object = None,
) -> bool:
    if status_code == 402:
        return True
    message = _compact_message(provider_message)
    return any(marker in message for marker in _CREDIT_ERROR_MARKERS)


def notify_openrouter_credits_exhausted(
    *,
    status_code: int | None,
    provider_message: object = None,
    source: str,
) -> bool:
    if not openrouter_credits_exhausted(
        status_code=status_code,
        provider_message=provider_message,
    ):
        return False
    status_label = str(status_code) if status_code is not None else "unknown"
    settings = get_settings()
    notify_telegram_alert(
        OPENROUTER_CREDITS_ALERT_KEY,
        "\n".join(
            (
                "На OpenRouter закончились средства.",
                "Сергей (@rivfader), пополни баланс:",
                OPENROUTER_CREDITS_URL,
                f"Источник: {source}",
                f"HTTP: {status_label}",
            )
        ),
        enabled=settings.openrouter_billing_alerts_enabled,
        telegram_ids=settings.openrouter_billing_alert_telegram_ids,
        dedup_seconds=settings.openrouter_billing_alert_dedup_seconds,
    )
    return True
