from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ops-alert")
_dedup_lock = Lock()
_last_sent: dict[str, float] = {}


def notify_ops(key: str, text: str) -> None:
    settings = get_settings()
    if not settings.ops_alerts_enabled or not settings.bot_token:
        return
    now = time.monotonic()
    with _dedup_lock:
        last_sent = _last_sent.get(key, 0.0)
        if now - last_sent < settings.ops_alert_dedup_seconds:
            return
        _last_sent[key] = now
    _executor.submit(_send_alert, text[:3800])


def _send_alert(text: str) -> None:
    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    for chat_id in settings.ops_alert_telegram_ids:
        try:
            response = httpx.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": f"🚨 Gigagochi\n{text}",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            response.raise_for_status()
        except Exception:
            logger.warning("ops_alert_delivery_failed chatId=%s", chat_id, exc_info=True)
