from __future__ import annotations

from collections.abc import Iterable
from threading import RLock

from app.llm.contracts import LLMProvider


class ProviderRegistryError(RuntimeError):
    pass


class ProviderAlreadyRegisteredError(ProviderRegistryError):
    pass


class ProviderNotFoundError(ProviderRegistryError):
    pass


def normalize_provider_name(name: str) -> str:
    normalized = str(name).strip().lower()
    if not normalized:
        raise ValueError("provider name must not be empty")
    return normalized


class ProviderRegistry:
    """Thread-safe provider registry intended to be assembled during app startup."""

    def __init__(self, providers: Iterable[LLMProvider] = ()) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._lock = RLock()
        for provider in providers:
            self.register(provider)

    def register(self, provider: LLMProvider, *, replace: bool = False) -> LLMProvider:
        try:
            name = normalize_provider_name(provider.name)
        except AttributeError as exc:
            raise TypeError("provider must expose a name") from exc
        if not callable(getattr(provider, "complete", None)):
            raise TypeError("provider must expose complete(request)")
        if not hasattr(provider, "capabilities"):
            raise TypeError("provider must expose capabilities")

        with self._lock:
            if name in self._providers and not replace:
                raise ProviderAlreadyRegisteredError(f"LLM provider {name!r} is already registered")
            self._providers[name] = provider
        return provider

    def get(self, name: str) -> LLMProvider:
        normalized = normalize_provider_name(name)
        with self._lock:
            try:
                return self._providers[normalized]
            except KeyError as exc:
                available = ", ".join(sorted(self._providers)) or "none"
                raise ProviderNotFoundError(
                    f"LLM provider {normalized!r} is not registered; available: {available}"
                ) from exc

    def unregister(self, name: str) -> LLMProvider:
        normalized = normalize_provider_name(name)
        with self._lock:
            try:
                return self._providers.pop(normalized)
            except KeyError as exc:
                raise ProviderNotFoundError(
                    f"LLM provider {normalized!r} is not registered"
                ) from exc

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._providers))

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        try:
            normalized = normalize_provider_name(name)
        except ValueError:
            return False
        with self._lock:
            return normalized in self._providers

    def __len__(self) -> int:
        with self._lock:
            return len(self._providers)
