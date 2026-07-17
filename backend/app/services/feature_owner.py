from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from app.services.google_auth_session_store import GoogleUserIdentity
from app.services.telegram_auth_service import TelegramUserContext

FeatureOwnerNamespace = Literal["telegram", "google"]
FeatureOwnerStorageKey = int | str


@dataclass(frozen=True, slots=True)
class TelegramNotificationTarget:
    chat_id: int


@dataclass(frozen=True, slots=True)
class FeatureOwner:
    namespace: FeatureOwnerNamespace
    storage_key: FeatureOwnerStorageKey
    notification_target: TelegramNotificationTarget | None = None
    username: str | None = None
    first_name: str | None = None

    def __post_init__(self) -> None:
        if self.namespace == "google":
            if not isinstance(self.storage_key, str) or re.fullmatch(
                r"google:[a-f0-9]{64}", self.storage_key
            ) is None:
                raise ValueError("google owner storage key must be opaque")
            if self.notification_target is not None:
                raise ValueError("google owner cannot have Telegram notification capability")
        elif self.namespace != "telegram":
            raise ValueError("unknown feature owner namespace")
        elif not isinstance(self.storage_key, int) or isinstance(self.storage_key, bool):
            raise ValueError("telegram owner storage key must be an integer")
        elif (
            self.notification_target is not None
            and self.notification_target.chat_id != self.storage_key
        ):
            raise ValueError("telegram notification target must match owner")

    @classmethod
    def from_telegram(cls, user: TelegramUserContext) -> FeatureOwner:
        return cls(
            namespace="telegram",
            storage_key=user.telegram_id,
            notification_target=TelegramNotificationTarget(user.telegram_id),
            username=user.username,
            first_name=user.first_name,
        )

    @classmethod
    def from_google(cls, identity: GoogleUserIdentity) -> FeatureOwner:
        digest = hashlib.sha256(identity.account_id.encode()).hexdigest()
        return cls(
            namespace="google",
            storage_key=f"google:{digest}",
        )

    @property
    def audit_label(self) -> str:
        if self.namespace == "telegram":
            return f"telegram:{self.storage_key}"
        digest = hashlib.sha256(str(self.storage_key).encode()).hexdigest()[:12]
        return f"google:{digest}"


def stored_owner_audit_label(namespace: str, storage_key: FeatureOwnerStorageKey) -> str:
    if namespace == "telegram":
        return f"telegram:{storage_key}"
    digest = hashlib.sha256(str(storage_key).encode()).hexdigest()[:12]
    return f"google:{digest}"
