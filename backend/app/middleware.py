from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if _declared_content_length(scope) > self.max_body_bytes:
            await self._too_large_response()(scope, receive, send)
            return

        received_body_bytes = 0

        async def receive_with_limit() -> Message:
            nonlocal received_body_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_body_bytes += len(message.get("body", b""))
                if received_body_bytes > self.max_body_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=self._error_detail(),
                    )
            return message

        await self.app(scope, receive_with_limit, send)

    def _too_large_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={"detail": self._error_detail()},
        )

    def _error_detail(self) -> dict[str, object]:
        return {
            "code": "REQUEST_TOO_LARGE",
            "message": "Размер запроса превышает допустимый лимит.",
            "maxBytes": self.max_body_bytes,
        }


def _declared_content_length(scope: Scope) -> int:
    declared_lengths: list[int] = []
    for name, raw_value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        for raw_length in raw_value.split(b","):
            try:
                length = int(raw_length.strip())
            except ValueError:
                continue
            if length >= 0:
                declared_lengths.append(length)
    return max(declared_lengths, default=0)
