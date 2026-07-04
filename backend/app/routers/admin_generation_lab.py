from __future__ import annotations

import secrets
from time import perf_counter
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.config import get_settings
from app.schemas import (
    AdminGenerateError,
    AdminGenerateOneRequest,
    AdminGenerateOneResponse,
)
from app.services import admin_generation_lab_service as admin_service
from app.services.image_service import generation_error_code
from app.services.openai_service import MissingOpenAIAPIKey

router = APIRouter(prefix="/admin/generation-lab", tags=["admin-generation-lab"])

LOCALHOST_NAMES = {"localhost", "127.0.0.1", "::1"}


def hostname_from_host_header(host_header: str | None) -> str | None:
    if not host_header:
        return None
    return urlparse(f"http://{host_header}").hostname


def hostname_from_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    return urlparse(origin).hostname


def is_localhost_name(hostname: str | None) -> bool:
    return hostname in LOCALHOST_NAMES


def admin_error(
    *,
    code: str,
    message: str,
    status_code: int,
    slot_id: str | None = None,
    description: str = "",
    duration_ms: int = 0,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=AdminGenerateError(
            slotId=slot_id,
            description=description,
            status="failed",
            code=code,
            message=message,
            durationMs=duration_ms,
        ).model_dump(),
    )


def require_admin_generation_lab_access(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    settings = get_settings()
    if not settings.enable_admin_generation_lab:
        raise admin_error(
            code="ADMIN_GENERATION_LAB_DISABLED",
            message="Admin generation lab is disabled.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    host_name = hostname_from_host_header(request.headers.get("host"))
    origin_name = hostname_from_origin(request.headers.get("origin"))
    if not is_localhost_name(host_name) or (
        request.headers.get("origin") is not None and not is_localhost_name(origin_name)
    ):
        raise admin_error(
            code="ADMIN_GENERATION_LAB_FORBIDDEN",
            message="Admin generation lab is available only from localhost.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    expected_token = settings.admin_generation_lab_token
    if expected_token and not secrets.compare_digest(expected_token, x_admin_token or ""):
        raise admin_error(
            code="ADMIN_GENERATION_LAB_FORBIDDEN",
            message="Invalid admin generation lab token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )


AdminAccess = Depends(require_admin_generation_lab_access)


def admin_generation_error_message(code: str) -> str:
    if code == "OPENAI_TIMEOUT":
        return (
            "OpenAI did not finish character profile generation before the timeout. "
            "Try a smaller count or rerun the slot."
        )
    return "Generation failed."


@router.get("/status", dependencies=[AdminAccess])
def admin_generation_lab_status() -> dict[str, str]:
    return {"status": "ready"}


@router.post(
    "/generate-one",
    response_model=AdminGenerateOneResponse,
    dependencies=[AdminAccess],
)
def generate_one(payload: AdminGenerateOneRequest) -> AdminGenerateOneResponse:
    description = payload.description.strip()
    started_at = perf_counter()
    if payload.mode == "full_assets":
        raise admin_error(
            code="ADMIN_FULL_ASSETS_DISABLED",
            message="Image generation is temporarily disabled in admin generation lab.",
            status_code=status.HTTP_400_BAD_REQUEST,
            slot_id=payload.slotId,
            description=description,
            duration_ms=0,
        )

    try:
        result = admin_service.generate_admin_profile_only(
            description,
            payload.includeDebugPrompts,
            payload.includeSelfIntroBenchmark,
            payload.includeConversationBenchmark,
        )
        duration_ms = int((perf_counter() - started_at) * 1000)
        return AdminGenerateOneResponse.model_validate(
            {
                **result,
                "slotId": payload.slotId,
                "description": description,
                "mode": payload.mode,
                "status": "ready",
                "durationMs": duration_ms,
            }
        )
    except MissingOpenAIAPIKey:
        duration_ms = int((perf_counter() - started_at) * 1000)
        raise admin_error(
            code="MISSING_OPENAI_API_KEY",
            message="OpenAI API key is not configured.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            slot_id=payload.slotId,
            description=description,
            duration_ms=duration_ms,
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        duration_ms = int((perf_counter() - started_at) * 1000)
        code = generation_error_code(exc)
        raise admin_error(
            code=code,
            message=admin_generation_error_message(code),
            status_code=status.HTTP_502_BAD_GATEWAY,
            slot_id=payload.slotId,
            description=description,
            duration_ms=duration_ms,
        ) from exc
