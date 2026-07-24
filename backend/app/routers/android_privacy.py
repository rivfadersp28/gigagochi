from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.dependencies import get_google_auth_service
from app.routers.android import get_android_feature_store
from app.routers.android_analytics import get_android_analytics_forwarder
from app.services.android_analytics_service import (
    AnalyticsNotConfiguredError,
    AndroidAnalyticsForwarder,
)
from app.services.android_feature_store import (
    AndroidFeatureOwnerDeletionBusyError,
    AndroidFeatureStore,
)
from app.services.android_privacy_service import AndroidPrivacyService
from app.services.generation_job_store import GenerationOwnerDeletionBusyError
from app.services.google_auth_service import GoogleAuthService
from app.services.google_auth_session_store import GoogleUserIdentity
from app.services.travel_video_prototype_service import (
    TravelVideoPrototypeDeletionBusyError,
)

router = APIRouter(prefix="/api/android/privacy", tags=["android-privacy"])


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


AuthService = Annotated[GoogleAuthService, Depends(get_google_auth_service)]
FeatureStore = Annotated[AndroidFeatureStore, Depends(get_android_feature_store)]
AnalyticsForwarder = Annotated[
    AndroidAnalyticsForwarder,
    Depends(get_android_analytics_forwarder),
]


@dataclass(frozen=True, slots=True)
class PrivacyAuthorization:
    identity: GoogleUserIdentity | None
    access_token: str
    already_deleted: bool = False


def get_privacy_authorization(
    request: Request,
    auth: AuthService,
    analytics: AnalyticsForwarder,
) -> PrivacyAuthorization:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token or token != token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_INVALID", "message": "Не удалось подтвердить сессию."},
        )
    identity = auth.authenticate_access_token(token)
    if identity is not None:
        return PrivacyAuthorization(identity=identity, access_token=token)
    if analytics.outbox.is_privacy_token(token):
        return PrivacyAuthorization(
            identity=None,
            access_token=token,
            already_deleted=True,
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "AUTH_INVALID", "message": "Не удалось подтвердить сессию."},
    )


PrivacyAuth = Annotated[PrivacyAuthorization, Depends(get_privacy_authorization)]


@router.post("/delete", include_in_schema=False)
def delete_android_account(
    _payload: DeleteAccountRequest,
    response: Response,
    authorization: PrivacyAuth,
    auth: AuthService,
    feature_store: FeatureStore,
    analytics: AnalyticsForwarder,
) -> dict[str, bool]:
    if authorization.already_deleted:
        response.headers["Cache-Control"] = "no-store"
        return {"deleted": True}
    identity = authorization.identity
    if identity is None:
        raise RuntimeError("privacy authorization identity is missing")
    try:
        AndroidPrivacyService(
            get_settings(),
            analytics=analytics,
            auth=auth,
            feature_store=feature_store,
        ).delete_account(
            identity.account_id,
            access_token=authorization.access_token,
        )
    except AnalyticsNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PRIVACY_UNAVAILABLE", "message": "Удаление временно недоступно."},
            headers={"Retry-After": "60"},
        ) from None
    except (
        AndroidFeatureOwnerDeletionBusyError,
        GenerationOwnerDeletionBusyError,
        TravelVideoPrototypeDeletionBusyError,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ACCOUNT_BUSY", "message": "Дождитесь завершения генерации."},
            headers={"Retry-After": "30"},
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return {"deleted": True}
