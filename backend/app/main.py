from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import admin_calibration_lab, admin_generation_lab, chat, pets, tma, users

settings = get_settings()

app = FastAPI(title="AI Tamagotchi API")

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

app.include_router(users.router)
app.include_router(pets.router)
app.include_router(chat.router)
app.include_router(tma.router)
app.include_router(admin_generation_lab.router)
app.include_router(admin_calibration_lab.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
