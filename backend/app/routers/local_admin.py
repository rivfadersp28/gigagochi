from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.local_admin_publish import (
    AdminPublishError,
    get_admin_publish_job,
    start_admin_publish,
)
from app.services.local_admin_store import read_admin_manifest, save_admin_files

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class AdminFileUpdate(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    content: str


class AdminSaveRequest(BaseModel):
    files: list[AdminFileUpdate] = Field(min_length=1, max_length=16)


class AdminPublishRequest(BaseModel):
    files: list[AdminFileUpdate] = Field(default_factory=list, max_length=16)
    message: str | None = Field(default=None, max_length=180)


def require_local_admin(request: Request) -> None:
    settings = get_settings()
    host = request.client.host if request.client else ""
    if settings.allow_dev_tma_auth and host in LOCAL_HOSTS:
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "LOCAL_ADMIN_DISABLED", "message": "Local admin is disabled."},
    )


router = APIRouter(
    prefix="/api/admin",
    tags=["local-admin"],
    dependencies=[Depends(require_local_admin)],
)


@router.get("/speech")
def speech_admin_manifest() -> dict[str, Any]:
    settings = get_settings()
    deploy_enabled = bool(getattr(settings, "admin_publish_enabled", False))
    deploy_message = (
        (
            "Публикация отправит изменения в GitHub "
            "и запустит deploy на Hetzner."
        )
        if deploy_enabled
        else (
            "Публикация отключена. "
            "Задай ADMIN_PUBLISH_ENABLED=true и SSH-настройки."
        )
    )
    return read_admin_manifest(deploy_enabled=deploy_enabled, deploy_message=deploy_message)


@router.put("/speech")
def save_speech_admin(payload: AdminSaveRequest) -> dict[str, Any]:
    result = save_admin_files([item.model_dump() for item in payload.files])
    if not result["saved"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result,
        )
    return result


@router.post("/speech/publish")
def publish_speech_admin(payload: AdminPublishRequest) -> dict[str, Any]:
    try:
        return start_admin_publish(
            files=[item.model_dump() for item in payload.files],
            settings=get_settings(),
            commit_message=payload.message,
        )
    except AdminPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.get("/speech/publish/{job_id}")
def speech_admin_publish_job(job_id: str) -> dict[str, Any]:
    result = get_admin_publish_job(job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ADMIN_PUBLISH_JOB_NOT_FOUND", "message": "Publish job not found."},
        )
    return result
