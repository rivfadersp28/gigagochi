from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock

import httpx

from app.config import get_settings
from app.services.telegram_client import redact_telegram_token

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ops-alert")
_dedup_lock = Lock()
_last_sent: dict[str, float] = {}
_pending_slots = BoundedSemaphore(8)
_MAX_DEDUP_KEYS = 1_024


def _dedup_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _prune_dedup_keys(now: float, dedup_seconds: int) -> None:
    cutoff = now - dedup_seconds
    expired = [key for key, sent_at in _last_sent.items() if sent_at < cutoff]
    for key in expired:
        _last_sent.pop(key, None)
    if len(_last_sent) <= _MAX_DEDUP_KEYS:
        return
    oldest = sorted(_last_sent, key=_last_sent.__getitem__)
    for key in oldest[: len(_last_sent) - _MAX_DEDUP_KEYS]:
        _last_sent.pop(key, None)


def notify_ops(key: str, text: str) -> None:
    settings = get_settings()
    notify_telegram_alert(
        key,
        text,
        enabled=settings.ops_alerts_enabled,
        telegram_ids=settings.ops_alert_telegram_ids,
        dedup_seconds=settings.ops_alert_dedup_seconds,
    )


def notify_telegram_alert(
    key: str,
    text: str,
    *,
    enabled: bool,
    telegram_ids: set[int],
    dedup_seconds: int,
) -> None:
    settings = get_settings()
    recipients = tuple(sorted(telegram_ids))
    if not enabled or not settings.bot_token or not recipients:
        return
    now = time.monotonic()
    normalized_key = _dedup_key(key)
    if not _pending_slots.acquire(blocking=False):
        logger.warning("ops_alert_queue_full")
        return
    with _dedup_lock:
        _prune_dedup_keys(now, dedup_seconds)
        last_sent = _last_sent.get(normalized_key, 0.0)
        if now - last_sent < dedup_seconds:
            _pending_slots.release()
            return
        _last_sent[normalized_key] = now
        _prune_dedup_keys(now, dedup_seconds)
    try:
        _executor.submit(_send_alert_and_release, text[:3800], recipients)
    except RuntimeError:
        with _dedup_lock:
            if _last_sent.get(normalized_key) == now:
                _last_sent.pop(normalized_key, None)
        _pending_slots.release()
        logger.warning("ops_alert_executor_unavailable")


def _send_alert_and_release(text: str, telegram_ids: tuple[int, ...]) -> None:
    try:
        _send_alert(text, telegram_ids)
    finally:
        _pending_slots.release()


def _send_alert(text: str, telegram_ids: tuple[int, ...]) -> None:
    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    for chat_id in telegram_ids:
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
        except Exception as exc:
            logger.warning(
                "ops_alert_delivery_failed chatId=%s errorType=%s error=%s",
                chat_id,
                type(exc).__name__,
                redact_telegram_token(exc, settings.bot_token),
            )
