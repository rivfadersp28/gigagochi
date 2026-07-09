from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import local_admin, tma
from app.services.telegram_push_service import (
    start_background_story_scheduler,
    start_daily_push_scheduler,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    push_task = start_daily_push_scheduler()
    story_task = start_background_story_scheduler()
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
def health() -> dict[str, str]:
    return {"status": "ok"}
