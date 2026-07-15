from __future__ import annotations

import json
from pathlib import Path

from app.main import app

FRONTEND_OPENAPI_PATH = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def test_frontend_openapi_matches_fastapi_schema() -> None:
    saved_schema = json.loads(FRONTEND_OPENAPI_PATH.read_text(encoding="utf-8"))

    assert saved_schema == app.openapi(), (
        "frontend/openapi.json is stale; run "
        "backend/.venv/bin/python backend/scripts/export_openapi.py frontend/openapi.json"
    )
