from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from threading import RLock
from typing import Any

from app.llm.contracts import (
    LLMCapability,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    LLMUsage,
)


class OpenAICompatibleProviderError(LLMProviderError):
    pass


class OpenAICompatibleResponseError(OpenAICompatibleProviderError):
    pass


_DEFAULT_CAPABILITIES = frozenset(
    {
        LLMCapability.TEXT,
        LLMCapability.STRUCTURED_OUTPUT,
        LLMCapability.TOOLS,
        LLMCapability.REASONING,
    }
)

_RESERVED_REQUEST_KEYS = frozenset(
    {
        "model",
        "messages",
        "response_format",
        "tools",
        "tool_choice",
        "temperature",
        "max_tokens",
        "max_completion_tokens",
        "reasoning_effort",
        "timeout",
    }
)


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _content_text(content: Any) -> str | None:
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        parts: list[str] = []
        for part in content:
            text = _value(part, "text")
            if text is not None:
                parts.append(str(text))
        if parts:
            return "".join(parts)
    return str(content)


class OpenAICompatibleProvider:
    """Adapter for sync clients implementing ``client.chat.completions.create``."""

    def __init__(
        self,
        *,
        name: str = "openai",
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
        default_model: str | None = None,
        capabilities: Iterable[LLMCapability] | None = None,
        max_tokens_parameter: str = "max_completion_tokens",
    ) -> None:
        if client is not None and client_factory is not None:
            raise ValueError("pass either client or client_factory, not both")
        if (client is not None or client_factory is not None) and client_kwargs:
            raise ValueError("client_kwargs require the built-in OpenAI client factory")

        normalized_name = str(name).strip().lower()
        if not normalized_name:
            raise ValueError("provider name must not be empty")
        normalized_max_tokens_parameter = str(max_tokens_parameter).strip()
        if not normalized_max_tokens_parameter:
            raise ValueError("max_tokens_parameter must not be empty")

        self.name = normalized_name
        self.capabilities = frozenset(
            _DEFAULT_CAPABILITIES if capabilities is None else capabilities
        )
        self.default_model = str(default_model).strip() if default_model else None
        self._client = client
        self._client_factory = client_factory
        self._client_kwargs = dict(client_kwargs or {})
        self._max_tokens_parameter = normalized_max_tokens_parameter
        self._client_lock = RLock()

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                if self._client_factory is not None:
                    self._client = self._client_factory()
                else:
                    from openai import OpenAI

                    self._client = OpenAI(**self._client_kwargs)
        return self._client

    def complete(self, request: LLMRequest) -> LLMResponse:
        request_kwargs = self._request_kwargs(request)
        completion = self.client.chat.completions.create(**request_kwargs)
        if inspect.isawaitable(completion):
            raise OpenAICompatibleProviderError(
                "OpenAICompatibleProvider requires a synchronous client"
            )
        return self._response(completion)

    def _request_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        model = request.model or self.default_model
        if not model:
            raise OpenAICompatibleProviderError(
                f"no model configured for LLM provider {self.name!r}"
            )

        conflicts = _RESERVED_REQUEST_KEYS.intersection(request.extra)
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ValueError(f"request.extra cannot override normalized fields: {names}")

        kwargs: dict[str, Any] = dict(request.extra)
        kwargs.update(
            {
                "model": model,
                "messages": [dict(message) for message in request.messages],
            }
        )
        if request.structured_output is not None:
            output = request.structured_output
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": output.name,
                    "schema": dict(output.schema),
                    "strict": output.strict,
                },
            }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.parameters),
                        "strict": tool.strict,
                    },
                }
                for tool in request.tools
            ]
        if request.tool_choice is not None:
            kwargs["tool_choice"] = (
                dict(request.tool_choice)
                if isinstance(request.tool_choice, Mapping)
                else request.tool_choice
            )
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs[self._max_tokens_parameter] = request.max_output_tokens
        if request.reasoning_effort is not None:
            kwargs["reasoning_effort"] = request.reasoning_effort
        if request.timeout_seconds is not None:
            kwargs["timeout"] = request.timeout_seconds
        return kwargs

    def _response(self, completion: Any) -> LLMResponse:
        choices = _value(completion, "choices")
        if not choices:
            raise OpenAICompatibleResponseError("completion response has no choices")

        choice = choices[0]
        message = _value(choice, "message")
        if message is None:
            raise OpenAICompatibleResponseError("completion choice has no message")

        tool_calls: list[LLMToolCall] = []
        for call in _value(message, "tool_calls", ()) or ():
            function = _value(call, "function")
            name = _value(function, "name")
            if not name:
                raise OpenAICompatibleResponseError("tool call has no function name")
            arguments = _value(function, "arguments", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            tool_calls.append(
                LLMToolCall(
                    id=_value(call, "id"),
                    name=str(name),
                    arguments=arguments,
                )
            )

        usage_value = _value(completion, "usage")
        usage = None
        if usage_value is not None:
            usage = LLMUsage(
                prompt_tokens=_optional_int(_value(usage_value, "prompt_tokens")),
                completion_tokens=_optional_int(_value(usage_value, "completion_tokens")),
                total_tokens=_optional_int(_value(usage_value, "total_tokens")),
            )

        model = _value(completion, "model")
        finish_reason = _value(choice, "finish_reason")
        return LLMResponse(
            content=_content_text(_value(message, "content")),
            tool_calls=tuple(tool_calls),
            model=str(model) if model is not None else None,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
            usage=usage,
            raw=completion,
        )
