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
        if limit <= 0:
            return

        now = datetime.now(UTC)
        cutoff = now - window
        key = (bucket, user_id)

        with self._lock:
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after_seconds = max(1, int((events[0] + window - now).total_seconds()) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "rate_limited",
                        "message": "Слишком много запросов.",
                        "retryAfterSeconds": retry_after_seconds,
                    },
                )
            events.append(now)


rate_limiter = InMemoryRateLimiter()
