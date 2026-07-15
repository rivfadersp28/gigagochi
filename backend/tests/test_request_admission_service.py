from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.config import Settings
from app.routers import tma
from app.services.request_admission_service import (
    InFlightRequestAdmission,
    RequestAdmissionRejected,
)


def test_request_admission_enforces_user_then_global_and_releases() -> None:
    admission = InFlightRequestAdmission()
    first = admission.acquire("llm", 1, global_limit=2, per_user_limit=1)
    second = admission.acquire("llm", 2, global_limit=2, per_user_limit=1)

    with pytest.raises(RequestAdmissionRejected) as user_rejection:
        admission.acquire("llm", 1, global_limit=2, per_user_limit=1)
    assert user_rejection.value.scope == "user"

    with pytest.raises(RequestAdmissionRejected) as global_rejection:
        admission.acquire("llm", 3, global_limit=2, per_user_limit=1)
    assert global_rejection.value.scope == "global"

    first.release()
    first.release()
    replacement = admission.acquire("llm", 3, global_limit=2, per_user_limit=1)
    second.release()
    replacement.release()
    assert admission.snapshot() == {"global": {}, "users": {}}


def test_request_admission_config_defaults_and_bounds() -> None:
    fields = Settings.model_fields
    assert fields["http_llm_global_concurrency"].default == 16
    assert fields["http_llm_per_user_concurrency"].default == 2
    assert fields["http_media_global_concurrency"].default == 4
    assert fields["http_media_per_user_concurrency"].default == 1

    with pytest.raises(ValueError):
        Settings(_env_file=None, http_llm_global_concurrency=33)
    with pytest.raises(ValueError):
        Settings(_env_file=None, http_media_per_user_concurrency=0)


def test_all_public_sync_provider_routes_have_async_admission_dependency() -> None:
    expected = {
        "/api/chat": tma._llm_request_admission,
        "/api/chat/ambient": tma._llm_request_admission,
        "/api/chat/lite-facts": tma._llm_request_admission,
        "/api/chat/memory-extract": tma._llm_request_admission,
        "/api/chat/memory-consolidate": tma._llm_request_admission,
        "/api/chat/proactive": tma._llm_request_admission,
        "/api/travel/interactive/suggestions": tma._llm_request_admission,
        "/api/travel/interactive/start": tma._llm_request_admission,
        "/api/travel/interactive/illustrate": tma._media_request_admission,
        "/api/travel/interactive/animate": tma._media_request_admission,
        "/api/travel/interactive/continue": tma._llm_request_admission,
    }
    routes = {
        route.path: route
        for route in tma.router.routes
        if isinstance(route, APIRoute) and route.path in expected
    }

    assert routes.keys() == expected.keys()
    for path, dependency in expected.items():
        dependency_calls = {item.call for item in routes[path].dependant.dependencies}
        assert dependency in dependency_calls, path
