from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from app.llm.contracts import LLMCapability, LLMRequest, LLMResponse
from app.llm.registry import ProviderRegistry, normalize_provider_name


class LLMGatewayError(RuntimeError):
    pass


class UnsupportedCapabilityError(LLMGatewayError):
    def __init__(self, provider: str, missing: frozenset[LLMCapability]) -> None:
        self.provider = provider
        self.missing = missing
        names = ", ".join(sorted(capability.value for capability in missing))
        super().__init__(f"LLM provider {provider!r} does not support: {names}")


class InvalidProviderResponseError(LLMGatewayError):
    pass


@dataclass(frozen=True, slots=True)
class LLMRoute:
    provider: str
    model: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", normalize_provider_name(self.provider))
        model = str(self.model).strip() if self.model is not None else None
        object.__setattr__(self, "model", model or None)


class TaskRouter(Protocol):
    def resolve(self, request: LLMRequest) -> LLMRoute: ...


RouteValue = LLMRoute | str
RouterHook = TaskRouter | Callable[[LLMRequest], RouteValue]


def _coerce_route(value: RouteValue) -> LLMRoute:
    if isinstance(value, LLMRoute):
        return value
    if isinstance(value, str):
        return LLMRoute(provider=value)
    raise TypeError("router must return LLMRoute or provider name")


class StaticTaskRouter:
    """Exact task routes with one required fallback route."""

    def __init__(
        self,
        default: RouteValue,
        tasks: Mapping[str, RouteValue] | None = None,
    ) -> None:
        self._default = _coerce_route(default)
        self._tasks = {
            str(task).strip(): _coerce_route(route) for task, route in (tasks or {}).items()
        }
        if any(not task for task in self._tasks):
            raise ValueError("task route names must not be empty")

    def resolve(self, request: LLMRequest) -> LLMRoute:
        return self._tasks.get(request.task, self._default)


class LLMGateway:
    def __init__(self, registry: ProviderRegistry, router: RouterHook) -> None:
        self._registry = registry
        self._router = router

    def complete(
        self,
        request: LLMRequest,
        *,
        provider_override: str | None = None,
    ) -> LLMResponse:
        route = self._resolve_route(request, provider_override=provider_override)
        provider = self._registry.get(route.provider)

        supported = frozenset(LLMCapability(value) for value in provider.capabilities)
        missing = request.required_capabilities - supported
        if missing:
            raise UnsupportedCapabilityError(route.provider, missing)

        routed_request = request
        if request.model is None and route.model is not None:
            routed_request = replace(request, model=route.model)

        response = provider.complete(routed_request)
        if not isinstance(response, LLMResponse):
            raise InvalidProviderResponseError(
                f"LLM provider {route.provider!r} returned {type(response).__name__}, "
                "expected LLMResponse"
            )
        return response

    def _resolve_route(
        self,
        request: LLMRequest,
        *,
        provider_override: str | None,
    ) -> LLMRoute:
        if provider_override is not None:
            return LLMRoute(provider=provider_override)

        resolver = getattr(self._router, "resolve", None)
        if callable(resolver):
            route = resolver(request)
        elif callable(self._router):
            route = self._router(request)
        else:
            raise TypeError("router must be callable or expose resolve(request)")
        return _coerce_route(route)
