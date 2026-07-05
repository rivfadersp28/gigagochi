from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status

from app.routers.admin_generation_lab import AdminAccess
from app.schemas import (
    CalibrationLabStatusResponse,
    CalibrationRunCreateRequest,
    CalibrationRunCreateResponse,
    CalibrationTaskResponse,
    CalibrationVoteCreateRequest,
    CalibrationVoteResponse,
)
from app.services import calibration_lab_service as calibration_service

router = APIRouter(prefix="/admin/calibration-lab", tags=["admin-calibration-lab"])


def calibration_error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "status": "failed",
            "code": code,
            "message": message,
        },
    )


@router.get(
    "/status",
    response_model=CalibrationLabStatusResponse,
    dependencies=[AdminAccess],
)
def calibration_lab_status() -> dict[str, object]:
    counts = calibration_service.storage_counts()
    return {
        "status": "ready",
        "storage": "jsonl",
        **counts,
    }


@router.post(
    "/runs",
    response_model=CalibrationRunCreateResponse,
    dependencies=[AdminAccess],
)
def create_calibration_run(
    payload: CalibrationRunCreateRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    try:
        response, work = calibration_service.create_run_record(payload.model_dump())
        background_tasks.add_task(calibration_service.generate_run_tasks, work)
        return response
    except ValueError as exc:
        raise calibration_error(
            "CALIBRATION_RUN_INVALID",
            str(exc),
            status.HTTP_400_BAD_REQUEST,
        ) from exc


@router.get(
    "/tasks/next",
    response_model=CalibrationTaskResponse | None,
    dependencies=[AdminAccess],
)
def get_next_calibration_task(
    task_type: str | None = Query(default=None, alias="taskType"),
    run_id: str | None = Query(default=None, alias="runId"),
) -> dict[str, object] | None:
    return calibration_service.get_next_task(task_type=task_type, run_id=run_id)


@router.get(
    "/tasks/{task_id}",
    response_model=CalibrationTaskResponse,
    dependencies=[AdminAccess],
)
def get_calibration_task(task_id: str) -> dict[str, object]:
    task = calibration_service.get_task(task_id)
    if task is None:
        raise calibration_error(
            "CALIBRATION_TASK_NOT_FOUND",
            "Calibration task was not found.",
            status.HTTP_404_NOT_FOUND,
        )
    return task


@router.post(
    "/votes",
    response_model=CalibrationVoteResponse,
    dependencies=[AdminAccess],
)
def save_calibration_vote(payload: CalibrationVoteCreateRequest) -> dict[str, object]:
    try:
        return calibration_service.save_vote(payload.model_dump())
    except ValueError as exc:
        raise calibration_error(
            "CALIBRATION_VOTE_INVALID",
            str(exc),
            status.HTTP_400_BAD_REQUEST,
        ) from exc


@router.get("/export/votes", response_model=None, dependencies=[AdminAccess])
def export_calibration_votes(
    format: Literal["jsonl", "json"] = "jsonl",
):
    if format == "json":
        return calibration_service.export_votes_json()
    return Response(
        calibration_service.export_votes_jsonl(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="calibration_votes.jsonl"'},
    )


@router.get("/export/winners", response_model=None, dependencies=[AdminAccess])
def export_calibration_winners(
    format: Literal["jsonl", "json"] = "jsonl",
):
    if format == "json":
        return calibration_service.export_winners()
    return Response(
        calibration_service.export_winners_jsonl(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="calibration_winners.jsonl"'},
    )
