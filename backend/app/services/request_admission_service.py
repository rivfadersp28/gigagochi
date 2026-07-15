from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

AdmissionScope = Literal["global", "user"]


class RequestAdmissionRejected(RuntimeError):
    def __init__(self, *, bucket: str, scope: AdmissionScope) -> None:
        super().__init__(f"{bucket} request admission {scope} limit reached")
        self.bucket = bucket
        self.scope = scope


@dataclass(slots=True)
class RequestAdmissionLease:
    _admission: InFlightRequestAdmission
    bucket: str
    user_id: int
    _released: bool = False
    _release_lock: Lock = field(default_factory=Lock, repr=False)

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
        self._admission._release(self.bucket, self.user_id)


class InFlightRequestAdmission:
    """Fail-fast per-process admission before FastAPI's sync threadpool."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._global_counts: dict[str, int] = {}
        self._user_counts: dict[tuple[str, int], int] = {}

    def acquire(
        self,
        bucket: str,
        user_id: int,
        *,
        global_limit: int,
        per_user_limit: int,
    ) -> RequestAdmissionLease:
        if not bucket or len(bucket) > 64:
            raise ValueError("admission bucket must contain between 1 and 64 characters")
        if type(user_id) is not int:
            raise ValueError("admission user_id must be an integer")
        if global_limit < 1 or per_user_limit < 1:
            raise ValueError("admission limits must be positive")

        user_key = (bucket, user_id)
        with self._lock:
            user_count = self._user_counts.get(user_key, 0)
            if user_count >= per_user_limit:
                raise RequestAdmissionRejected(bucket=bucket, scope="user")
            global_count = self._global_counts.get(bucket, 0)
            if global_count >= global_limit:
                raise RequestAdmissionRejected(bucket=bucket, scope="global")
            self._user_counts[user_key] = user_count + 1
            self._global_counts[bucket] = global_count + 1
        return RequestAdmissionLease(self, bucket, user_id)

    def _release(self, bucket: str, user_id: int) -> None:
        user_key = (bucket, user_id)
        with self._lock:
            user_count = self._user_counts.get(user_key, 0)
            global_count = self._global_counts.get(bucket, 0)
            if user_count <= 0 or global_count <= 0:
                raise RuntimeError("request admission lease accounting underflow")
            if user_count == 1:
                self._user_counts.pop(user_key, None)
            else:
                self._user_counts[user_key] = user_count - 1
            if global_count == 1:
                self._global_counts.pop(bucket, None)
            else:
                self._global_counts[bucket] = global_count - 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "global": dict(self._global_counts),
                "users": dict(self._user_counts),
            }


public_request_admission = InFlightRequestAdmission()
