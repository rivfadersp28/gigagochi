from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.llm.runtime import llm_runtime_status
from app.media.runtime import media_runtime_status
from app.middleware import RequestBodyLimitMiddleware
from app.public_media import PublicMediaStaticFiles
from app.routers import local_admin, tma
from app.services.ops_alert_service import notify_ops
from app.services.provider_task_checkpoint import provider_task_runtime_status
from app.services.storage_health_service import storage_runtime_status
from app.services.telegram_push_service import (
    scheduler_runtime_status,
    start_background_story_scheduler,
    start_daily_push_scheduler,
    start_generated_media_cleanup_scheduler,
    start_scheduled_short_story_scheduler,
)

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tma.start_generation_jobs()
    push_task = start_daily_push_scheduler()
    story_task = start_background_story_scheduler()
    short_story_task = start_scheduled_short_story_scheduler()
    cleanup_task = start_generated_media_cleanup_scheduler()
    app.state.scheduler_tasks = {
        "dailyPush": push_task,
        "backgroundStory": story_task,
        "scheduledShortStory": short_story_task,
        "generatedMediaCleanup": cleanup_task,
    }
    try:
        yield
    finally:
        scheduler_tasks = [
            task
            for task in (push_task, story_task, short_story_task, cleanup_task)
            if task is not None
        ]
        for task in scheduler_tasks:
            task.cancel()
        if scheduler_tasks:
            await asyncio.gather(*scheduler_tasks, return_exceptions=True)
        tma.shutdown_generation_jobs(wait=True)


app = FastAPI(title="AI Tamagotchi API", lifespan=lifespan)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=settings.http_request_max_body_bytes,
)


def _diagnostic_user(request: Request) -> bool:
    user = getattr(request.state, "telegram_user", None)
    telegram_id = getattr(user, "telegram_id", None)
    return telegram_id in settings.diagnostic_telegram_ids


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "").strip()[:120] or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    detail: dict[str, object] = {
        "code": "INVALID_REQUEST",
        "message": "Не получилось обработать данные. Обновите приложение и попробуйте снова.",
    }
    if _diagnostic_user(request):
        detail["diagnostic"] = {"errors": exc.errors()}
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    logger.exception("unhandled_request_error requestId=%s path=%s", request_id, request.url.path)
    notify_ops(
        f"http:{request.url.path}:{type(exc).__name__}",
        f"HTTP 500: {request.url.path}\n{type(exc).__name__}\nrequest: {request_id}",
    )
    detail: dict[str, object] = {
        "code": "INTERNAL_ERROR",
        "message": "Сервис временно недоступен. Попробуйте позже.",
    }
    if _diagnostic_user(request):
        detail["diagnostic"] = {
            "exceptionType": type(exc).__name__,
            "exceptionMessage": str(exc)[:1200],
        }
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(__file__).resolve().parent.parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", PublicMediaStaticFiles(directory=static_dir), name="static")

app.include_router(local_admin.router)
app.include_router(tma.router)


def _health_response(request: Request):
    tasks = getattr(request.app.state, "scheduler_tasks", {})
    runtime = scheduler_runtime_status()
    failed: list[str] = []
    for name, task in tasks.items():
        scheduler = runtime.get(name, {})
        is_managed = task is not None or scheduler.get("running") is True
        if is_managed and (
            (task is not None and task.done())
            or int(scheduler.get("consecutiveFailures", 0)) > 0
            or scheduler.get("deliveryDegraded") is True
        ):
            failed.append(name)
    generation = tma.generation_job_runtime_status()
    llm = llm_runtime_status()
    if llm["status"] != "ok":
        failed.append("llm")
    media = media_runtime_status()
    if media["status"] != "ok":
        failed.append("media")
    storage = storage_runtime_status()
    if storage["status"] != "ok":
        failed.append("storage")
    provider_tasks = provider_task_runtime_status()
    if provider_tasks["status"] != "ok":
        failed.append("providerTasks")
    for component in failed:
        detail = ""
        if component == "storage":
            detail = f" ({', '.join(storage['failedPaths'])})"
        notify_ops(
            f"health:{component}",
            f"Health degraded: {component}{detail}",
        )
    if generation["stuck"] > 0:
        failed.append("generationJobs")
        notify_ops(
            "generation:stuck",
            f"Stuck generation jobs: {generation['stuck']}",
        )
    payload = {"status": "ok" if not failed else "degraded"}
    if failed:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
    return payload


@app.get("/health")
async def health(request: Request):
    # FastAPI's sync handlers share AnyIO's bounded worker pool. Health must remain
    # schedulable when provider calls or idempotency locks occupy that entire pool.
    return await asyncio.to_thread(_health_response, request)
