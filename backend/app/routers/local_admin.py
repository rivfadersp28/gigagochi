from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.local_admin_publish import (
    AdminPublishError,
    get_admin_publish_job,
    read_admin_manifest_from_server,
    start_admin_publish,
    sync_admin_files_from_server,
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


AdminSource = Literal["local", "production"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sync_warning_result(exc: AdminPublishError) -> dict[str, Any]:
    return {
        "status": "local_dirty",
        "message": (
            "Есть незадеплоенные изменения. "
            "Форма показывает локальные data-файлы; нажми Deploy, когда они готовы."
        ),
        "serverCommit": None,
        "updatedAt": _now_iso(),
    }


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
def speech_admin_manifest(source: AdminSource = "local") -> dict[str, Any]:
    settings = get_settings()
    deploy_enabled = bool(getattr(settings, "admin_publish_enabled", False))
    deploy_message = (
        (
            "Deploy отправит data-файлы в GitHub и применит их на Hetzner."
        )
        if deploy_enabled
        else (
            "Deploy отключен. "
            "Задай ADMIN_PUBLISH_ENABLED=true и SSH-настройки."
        )
    )
    try:
        if source == "production":
            return read_admin_manifest_from_server(
                settings,
                deploy_enabled=deploy_enabled,
                deploy_message=deploy_message,
            )
        sync_result = None
        if getattr(settings, "admin_sync_from_server_enabled", False):
            try:
                sync_result = sync_admin_files_from_server(settings)
            except AdminPublishError as exc:
                if exc.code != "ADMIN_SYNC_LOCAL_DIRTY":
                    raise
                sync_result = _sync_warning_result(exc)
        return read_admin_manifest(
            deploy_enabled=deploy_enabled,
            deploy_message=deploy_message,
            sync_result=sync_result,
        )
    except AdminPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.put("/speech")
def save_speech_admin(payload: AdminSaveRequest, source: AdminSource = "local") -> dict[str, Any]:
    if source == "production":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "ADMIN_PRODUCTION_DIRECT_SAVE_DISABLED",
                "message": "Production меняется только через publish/deploy.",
            },
        )
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
