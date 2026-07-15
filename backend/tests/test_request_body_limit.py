from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.main import app, settings
from app.middleware import RequestBodyLimitMiddleware


class EchoPayload(BaseModel):
    text: str


def _limited_app(*, max_body_bytes: int) -> FastAPI:
    limited_app = FastAPI()
    limited_app.state.echo_calls = 0
    limited_app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=max_body_bytes,
    )

    @limited_app.post("/echo")
    async def echo(payload: EchoPayload) -> dict[str, int]:
        limited_app.state.echo_calls += 1
        return {"bodyBytes": len(payload.text.encode())}

    return limited_app


def test_global_limit_rejects_oversized_content_length() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/push/snapshot",
        content=b"x" * (settings.http_request_max_body_bytes + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": {
            "code": "REQUEST_TOO_LARGE",
            "message": "Размер запроса превышает допустимый лимит.",
            "maxBytes": settings.http_request_max_body_bytes,
        }
    }


def test_limit_allows_normal_request() -> None:
    limited_app = _limited_app(max_body_bytes=32)

    response = TestClient(limited_app).post("/echo", json={"text": "ok"})

    assert response.status_code == 200
    assert response.json() == {"bodyBytes": 2}
    assert limited_app.state.echo_calls == 1


def test_limit_counts_streamed_chunks_before_pydantic_endpoint() -> None:
    limited_app = _limited_app(max_body_bytes=16)

    async def request() -> httpx.Response:
        async def chunks() -> AsyncIterator[bytes]:
            yield b'{"text":"'
            yield b'1234567890"}'

        transport = httpx.ASGITransport(app=limited_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/echo", content=chunks())

    response = asyncio.run(request())

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"
    assert limited_app.state.echo_calls == 0
