from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from threading import Lock

from fastapi import HTTPException, status


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, int], deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, bucket: str, user_id: int, limit: int, window: timedelta) -> None:
        now = datetime.now(UTC)
        cutoff = now - window
        key = (bucket, user_id)

        with self._lock:
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={"code": "rate_limited", "message": "Слишком много запросов."},
                )
            events.append(now)


rate_limiter = InMemoryRateLimiter()
