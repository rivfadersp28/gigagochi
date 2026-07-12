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
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import local_admin, tma
from app.services.telegram_push_service import (
    scheduler_runtime_status,
    start_background_story_scheduler,
    start_daily_push_scheduler,
)

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    push_task = start_daily_push_scheduler()
    story_task = start_background_story_scheduler()
    app.state.scheduler_tasks = {
        "dailyPush": push_task,
        "backgroundStory": story_task,
    }
    try:
        yield
    finally:
        scheduler_tasks = [task for task in (push_task, story_task) if task is not None]
        for task in scheduler_tasks:
            task.cancel()
        if scheduler_tasks:
            await asyncio.gather(*scheduler_tasks, return_exceptions=True)
        tma.shutdown_generation_jobs()


app = FastAPI(title="AI Tamagotchi API", lifespan=lifespan)


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
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(local_admin.router)
app.include_router(tma.router)


@app.get("/health")
def health(request: Request):
    tasks = getattr(request.app.state, "scheduler_tasks", {})
    runtime = scheduler_runtime_status()
    failed = [
        name
        for name, task in tasks.items()
        if task is not None
        and (task.done() or int(runtime.get(name, {}).get("consecutiveFailures", 0)) > 0)
    ]
    payload: dict[str, object] = {"status": "ok" if not failed else "degraded"}
    if failed:
        payload["failedComponents"] = failed
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
    return payload
