from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.services.pet_reply_engine.speech_runtime import (
    CONTEXT_SOURCE_KEYS,
    ContextSourceMode,
    ContextSurface,
)

CONTEXT_ROUTING_SOURCE_IDS = (
    "worldContext",
    "characterProfile",
    "userMemory",
    "chatHistory",
    "recentReplies",
)

CONTEXT_SOURCE_TO_ROUTER_SOURCE: dict[str, str | None] = {
    "characterProfile": "characterProfile",
    "stateParams": None,
    "liteOverlay": "characterProfile",
    "storyLibrary": "worldContext",
    "storyOverlay": None,
    "recentEvents": None,
    "userMemory": "userMemory",
    "chatHistory": "chatHistory",
    "recentReplies": "recentReplies",
}

@dataclass(frozen=True)
class ContextRoutingDecision:
    surface: ContextSurface
    enabled_sources: frozenset[str] = frozenset()
    queries: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    raw: dict[str, Any] | None = None

    def enabled(self, source: str, *, default: bool = False) -> bool:
        if source not in CONTEXT_ROUTING_SOURCE_IDS:
            return default
        return source in self.enabled_sources

    def query(self, source: str) -> str:
        return self.queries.get(source, "")


@dataclass(frozen=True)
class ContextPlan:
    surface: ContextSurface
    modes: dict[str, ContextSourceMode]
    router_decision: ContextRoutingDecision
    included_sources: frozenset[str]
    queries: dict[str, str]
    debug: dict[str, Any]

    def includes(self, source: str) -> bool:
        return source in self.included_sources

    def query(self, source: str) -> str:
        return self.queries.get(source, "")


def router_source_for_context_source(source: str) -> str | None:
    return CONTEXT_SOURCE_TO_ROUTER_SOURCE.get(source)


def router_sources_for_auto_modes(modes: Mapping[str, str]) -> set[str]:
    router_sources: set[str] = set()
    for source, mode in modes.items():
        if mode != "auto":
            continue
        router_source = router_source_for_context_source(source)
        if router_source:
            router_sources.add(router_source)
    return router_sources


def build_context_plan(
    *,
    surface: ContextSurface,
    modes: Mapping[str, ContextSourceMode],
    routing: ContextRoutingDecision | None,
    source_enabled: Callable[..., bool],
    auto_default_sources: set[str] | frozenset[str] | None = None,
) -> ContextPlan:
    router_decision = routing or ContextRoutingDecision(surface=surface)
    auto_defaults = auto_default_sources or frozenset()
    source_modes = {
        source: modes[source]
        for source in CONTEXT_SOURCE_KEYS
        if source in modes
    }
    included: set[str] = set()
    for source in source_modes:
        router_source = router_source_for_context_source(source)
        router_enabled = routing.enabled(router_source) if routing and router_source else None
        if source_enabled(
            surface,
            source,
            router_enabled=router_enabled,
            auto_default=source in auto_defaults,
        ):
            included.add(source)
    queries = dict(router_decision.queries)
    debug = {
        "surface": surface,
        "sourceModes": dict(source_modes),
        "enabledSources": sorted(router_decision.enabled_sources),
        "routerEnabledSources": sorted(router_decision.enabled_sources),
        "includedSources": sorted(included),
        "queries": queries,
        "reason": router_decision.reason,
        "raw": router_decision.raw,
    }
    return ContextPlan(
        surface=surface,
        modes=dict(source_modes),
        router_decision=router_decision,
        included_sources=frozenset(included),
        queries=queries,
        debug=debug,
    )
