from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings
from app.services.android_analytics_service import AndroidAnalyticsForwarder
from app.services.android_feature_store import AndroidFeatureStore
from app.services.feature_owner import FeatureOwner
from app.services.generated_media_cleanup import cleanup_owned_generated_asset_directory
from app.services.generation_job_store import GenerationJobStore
from app.services.google_auth_service import GoogleAuthService
from app.services.google_auth_session_store import GoogleUserIdentity
from app.services.image_service import generated_dir_for, generation_job_asset_set_id
from app.services.provider_task_receipt_store import ProviderTaskReceiptStore
from app.services.rate_limit_service import get_rate_limiter
from app.services.travel_video_prototype_service import (
    delete_travel_video_prototypes_for_owner,
)

SAFE_DIRECTORY_NAME = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
GENERATED_PREFIX = "/static/generated/"
logger = logging.getLogger(__name__)


def _collect_generated_directories(values: list[Any], generated_root: Path) -> set[Path]:
    directories: set[Path] = set()
    pending = list(values)
    visited: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                try:
                    pending.append(json.loads(stripped))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            try:
                path = urlsplit(value).path
            except ValueError:
                continue
            if not path.startswith(GENERATED_PREFIX):
                continue
            relative = path.removeprefix(GENERATED_PREFIX)
            first = relative.split("/", 1)[0]
            if SAFE_DIRECTORY_NAME.fullmatch(first) and "/" in relative:
                directories.add(generated_root / first)
            continue
        if not isinstance(value, (dict, list, tuple)):
            continue
        identity = id(value)
        if identity in visited:
            continue
        visited.add(identity)
        pending.extend(value.values() if isinstance(value, dict) else value)
    return directories


class AndroidPrivacyService:
    def __init__(
        self,
        settings: Settings,
        *,
        analytics: AndroidAnalyticsForwarder,
        auth: GoogleAuthService,
        feature_store: AndroidFeatureStore,
    ) -> None:
        self.settings = settings
        self.analytics = analytics
        self.auth = auth
        self.feature_store = feature_store

    def delete_account(self, account_id: str, *, access_token: str) -> None:
        owner = FeatureOwner.from_google(
            # Only account_id participates in the opaque owner key.
            GoogleUserIdentity(0, account_id, "deleted", None, None)
        )
        actor_id = self.analytics.request_deletion(account_id)
        generation_store = GenerationJobStore(self.settings.generation_job_store_path)
        jobs = generation_store.owner_jobs_for_deletion(owner.storage_key)

        generated_root = Path(self.settings.storage_health_generated_assets_path).expanduser()
        if not generated_root.is_absolute():
            generated_root = (Path.cwd() / generated_root).resolve(strict=False)
        else:
            generated_root = generated_root.resolve(strict=False)

        values: list[Any] = [job.response.model_dump(mode="json") for job in jobs]
        feature_values = list(self.feature_store.owner_values_for_deletion(owner))
        values.extend(feature_values)
        directories = _collect_generated_directories(values, generated_root)
        for job in jobs:
            asset_set_id = generation_job_asset_set_id(job.response.jobId)
            directories.add(generated_dir_for(asset_set_id))
        for directory in sorted(directories):
            cleanup_owned_generated_asset_directory(
                generated_root=generated_root,
                asset_directory=directory,
                expected_owner_name=directory.name,
            )

        delete_travel_video_prototypes_for_owner(owner)
        deleted_jobs = generation_store.delete_owner(owner.storage_key)
        ProviderTaskReceiptStore(
            self.settings.provider_task_receipt_store_path,
            max_records=self.settings.provider_task_receipt_store_max_records,
        ).delete_generation_jobs([job.response.jobId for job in deleted_jobs])

        self.feature_store.delete_owner(owner)

        limiter = get_rate_limiter(self.settings.rate_limit_store_path)
        for bucket in ("generation", "chat", "interactive_travel"):
            limiter.clear(bucket, owner.storage_key)
        for bucket in (
            "android-analytics-minute",
            "android-analytics-burst",
        ):
            limiter.clear(bucket, actor_id)

        self.analytics.outbox.record_privacy_token(access_token)
        self.auth.delete_account(account_id)
        logger.info("android_privacy_delete_completed")
