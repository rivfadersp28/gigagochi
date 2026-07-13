from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    pass


class LLMProviderError(LLMError):
    error_kind = "provider"

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMCapability(StrEnum):
    """Optional provider features that a request can require."""

    TEXT = "text"
    STRUCTURED_OUTPUT = "structured_output"
    TOOLS = "tools"
    REASONING = "reasoning"


def _required_name(value: str, *, field_name: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


@dataclass(frozen=True, slots=True)
class StructuredOutputSchema:
    name: str
    schema: Mapping[str, Any]
    strict: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_name(self.name, field_name="name"))
        object.__setattr__(self, "schema", dict(self.schema))


@dataclass(frozen=True, slots=True)
class LLMTool:
    name: str
    description: str
    parameters: Mapping[str, Any]
    strict: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_name(self.name, field_name="name"))
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "parameters", dict(self.parameters))


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    id: str | None
    name: str
    arguments: str

    def __post_init__(self) -> None:
        if self.id is not None:
            object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "name", _required_name(self.name, field_name="name"))
        object.__setattr__(self, "arguments", str(self.arguments))


@dataclass(frozen=True, slots=True)
class LLMUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Provider-neutral synchronous text generation request."""

    messages: Sequence[Mapping[str, Any]]
    task: str = "default"
    model: str | None = None
    structured_output: StructuredOutputSchema | None = None
    tools: Sequence[LLMTool] = ()
    tool_choice: str | Mapping[str, Any] | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    timeout_seconds: float | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        messages: list[dict[str, Any]] = []
        for message in self.messages:
            if not isinstance(message, Mapping):
                raise TypeError("each message must be a mapping")
            messages.append(dict(message))
        if not messages:
            raise ValueError("messages must not be empty")

        task = _required_name(self.task, field_name="task")
        model = str(self.model).strip() if self.model is not None else None
        if model == "":
            model = None

        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        tools = tuple(self.tools)
        if any(not isinstance(tool, LLMTool) for tool in tools):
            raise TypeError("tools must contain LLMTool values")
        if self.tool_choice is not None and not tools:
            raise ValueError("tool_choice requires at least one tool")

        reasoning_effort = (
            str(self.reasoning_effort).strip() if self.reasoning_effort is not None else None
        )
        if reasoning_effort == "":
            reasoning_effort = None

        object.__setattr__(self, "messages", tuple(messages))
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "reasoning_effort", reasoning_effort)
        object.__setattr__(self, "extra", dict(self.extra))

    @property
    def required_capabilities(self) -> frozenset[LLMCapability]:
        capabilities = {LLMCapability.TEXT}
        if self.structured_output is not None:
            capabilities.add(LLMCapability.STRUCTURED_OUTPUT)
        if self.tools:
            capabilities.add(LLMCapability.TOOLS)
        if self.reasoning_effort is not None:
            capabilities.add(LLMCapability.REASONING)
        return frozenset(capabilities)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str | None
    tool_calls: tuple[LLMToolCall, ...] = ()
    model: str | None = None
    finish_reason: str | None = None
    usage: LLMUsage | None = None
    raw: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.content is not None:
            object.__setattr__(self, "content", str(self.content))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    capabilities: frozenset[LLMCapability]

    def complete(self, request: LLMRequest) -> LLMResponse: ...
