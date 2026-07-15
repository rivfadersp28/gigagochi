from __future__ import annotations

from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

PUBLIC_MEDIA_SUFFIXES = frozenset(
    {
        ".gif",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".ogg",
        ".png",
        ".wav",
        ".webm",
        ".webp",
    }
)


class PublicMediaStaticFiles(StaticFiles):
    """Serve generated media without exposing colocated prompts or state files."""

    async def get_response(self, path: str, scope: Scope):
        requested_path = PurePosixPath(path)
        if any(part.startswith(".") for part in requested_path.parts):
            raise HTTPException(status_code=404)
        if requested_path.suffix.lower() not in PUBLIC_MEDIA_SUFFIXES:
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)
