from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

GENERATED_ASSET_PATH_PREFIX = "/static/generated/"
GENERATED_IMAGE_SUFFIXES = {".jpeg", ".jpg", ".png", ".webp"}


def _public_origin(value: Any) -> tuple[str, str, int | None] | None:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), parsed.hostname.lower(), port


def _configured_public_urls(settings: Any) -> list[str]:
    urls: list[str] = []
    for value in (
        getattr(settings, "backend_public_url", None),
        getattr(settings, "webapp_url", None),
    ):
        cleaned = str(value or "").strip()
        if cleaned and _public_origin(cleaned) is not None:
            urls.append(cleaned)
    return urls


def _is_generated_image_path(path: str) -> bool:
    if not path.startswith(GENERATED_ASSET_PATH_PREFIX) or "%" in path or "\\" in path:
        return False
    decoded = unquote(path)
    normalized = posixpath.normpath(decoded)
    if not normalized.startswith(GENERATED_ASSET_PATH_PREFIX):
        return False
    return PurePosixPath(normalized).suffix.lower() in GENERATED_IMAGE_SUFFIXES


def trusted_generated_asset_url(image_url: str, settings: Any) -> str:
    """Return a canonical own-origin generated image URL or an empty string."""

    value = str(image_url or "").strip()
    if not value:
        return ""

    parsed = urlsplit(value)
    if not _is_generated_image_path(parsed.path):
        return ""

    configured_urls = _configured_public_urls(settings)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        origin = _public_origin(value)
        trusted_origins = {_public_origin(url) for url in configured_urls}
        if origin is None or origin not in trusted_origins:
            return ""
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ""))

    if not value.startswith("/") or not configured_urls:
        return ""
    base = urlsplit(configured_urls[0])
    return urlunsplit((base.scheme.lower(), base.netloc, parsed.path, parsed.query, ""))
