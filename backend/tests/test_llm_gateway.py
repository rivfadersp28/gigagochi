from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm import (
    LLMCapability,
    LLMGateway,
    LLMRequest,
    LLMResponse,
    LLMRoute,
    LLMTool,
    ProviderAlreadyRegisteredError,
    ProviderNotFoundError,
    ProviderRegistry,
    StaticTaskRouter,
    StructuredOutputSchema,
    UnsupportedCapabilityError,
)
from app.llm.providers import OpenAICompatibleProvider


class RecordingProvider:
    def __init__(
        self,
        name: str,
        capabilities: frozenset[LLMCapability] | None = None,
    ) -> None:
        self.name = name
        self.capabilities = capabilities or frozenset({LLMCapability.TEXT})
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(content=f"{self.name}:{request.model}")


class FakeCompletions:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(response):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response)))


def test_request_derives_required_capabilities() -> None:
    request = LLMRequest(
        messages=[{"role": "user", "content": "hello"}],
        structured_output=StructuredOutputSchema(
            name="answer",
            schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        ),
        tools=[
            LLMTool(
                name="rename_pet",
                description="Rename the pet",
                parameters={"type": "object", "properties": {"name": {"type": "string"}}},
            )
        ],
        tool_choice="auto",
        reasoning_effort="low",
    )

    assert request.required_capabilities == frozenset(LLMCapability)


def test_registry_rejects_duplicates_and_reports_missing_provider() -> None:
    provider = RecordingProvider("OpenAI")
    registry = ProviderRegistry([provider])

    assert registry.get(" openai ") is provider
    assert registry.names() == ("openai",)
    with pytest.raises(ProviderAlreadyRegisteredError):
        registry.register(RecordingProvider("openai"))
    with pytest.raises(ProviderNotFoundError, match="available: openai"):
        registry.get("gigachat")


def test_gateway_routes_by_task_and_explicit_request_model_wins() -> None:
    openai = RecordingProvider("openai")
    gigachat = RecordingProvider("gigachat")
    gateway = LLMGateway(
        ProviderRegistry([openai, gigachat]),
        StaticTaskRouter(
            default=LLMRoute("openai", "gpt-default"),
            tasks={"story": LLMRoute("gigachat", "giga-story")},
        ),
    )

    routed = gateway.complete(
        LLMRequest(messages=[{"role": "user", "content": "story"}], task="story")
    )
    explicit = gateway.complete(
        LLMRequest(
            messages=[{"role": "user", "content": "chat"}],
            model="gpt-explicit",
        )
    )

    assert routed.content == "gigachat:giga-story"
    assert gigachat.requests[0].model == "giga-story"
    assert explicit.content == "openai:gpt-explicit"
    assert openai.requests[0].model == "gpt-explicit"


def test_gateway_supports_callable_router_and_provider_override() -> None:
    openai = RecordingProvider("openai")
    gigachat = RecordingProvider("gigachat")
    gateway = LLMGateway(
        ProviderRegistry([openai, gigachat]),
        lambda request: LLMRoute("gigachat", f"model-for-{request.task}"),
    )
    request = LLMRequest(
        messages=[{"role": "user", "content": "hello"}],
        task="dialogue",
        model="override-model",
    )

    assert gateway.complete(request).content == "gigachat:override-model"
    assert gateway.complete(request, provider_override="openai").content == (
        "openai:override-model"
    )


def test_gateway_checks_capabilities_before_calling_provider() -> None:
    provider = RecordingProvider("plain", frozenset({LLMCapability.TEXT}))
    gateway = LLMGateway(ProviderRegistry([provider]), StaticTaskRouter("plain"))
    request = LLMRequest(
        messages=[{"role": "user", "content": "json please"}],
        structured_output=StructuredOutputSchema(name="answer", schema={"type": "object"}),
    )

    with pytest.raises(UnsupportedCapabilityError) as error:
        gateway.complete(request)

    assert error.value.provider == "plain"
    assert error.value.missing == frozenset({LLMCapability.STRUCTURED_OUTPUT})
    assert provider.requests == []


def test_openai_compatible_provider_maps_request_and_object_response() -> None:
    response = SimpleNamespace(
        model="gpt-test",
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(
                                name="rename_pet",
                                arguments='{"name":"Луна"}',
                            ),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
    )
    client = fake_client(response)
    provider = OpenAICompatibleProvider(client=client, default_model="gpt-test")
    request = LLMRequest(
        messages=[{"role": "system", "content": "You are a pet"}],
        structured_output=StructuredOutputSchema(name="reply", schema={"type": "object"}),
        tools=[
            LLMTool(
                name="rename_pet",
                description="Rename the pet",
                parameters={"type": "object"},
                strict=True,
            )
        ],
        tool_choice="auto",
        temperature=0.4,
        max_output_tokens=120,
        reasoning_effort="low",
        timeout_seconds=12,
        extra={"seed": 7},
    )

    result = provider.complete(request)

    call = client.chat.completions.calls[0]
    assert call == {
        "seed": 7,
        "model": "gpt-test",
        "messages": [{"role": "system", "content": "You are a pet"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "reply",
                "schema": {"type": "object"},
                "strict": True,
            },
        },
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "rename_pet",
                    "description": "Rename the pet",
                    "parameters": {"type": "object"},
                    "strict": True,
                },
            }
        ],
        "tool_choice": "auto",
        "temperature": 0.4,
        "max_completion_tokens": 120,
        "reasoning_effort": "low",
        "timeout": 12,
    }
    assert result.content is None
    assert result.tool_calls[0].name == "rename_pet"
    assert result.tool_calls[0].arguments == '{"name":"Луна"}'
    assert result.usage is not None
    assert result.usage.total_tokens == 14
    assert result.raw is response


def test_openai_compatible_provider_accepts_dict_response_and_fake_factory() -> None:
    response = {
        "model": "compatible-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": "готово", "tool_calls": []},
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }
    client = fake_client(response)
    factory_calls = 0

    def client_factory():
        nonlocal factory_calls
        factory_calls += 1
        return client

    provider = OpenAICompatibleProvider(
        name="openrouter",
        client_factory=client_factory,
        default_model="compatible-model",
        max_tokens_parameter="max_tokens",
    )
    request = LLMRequest(
        messages=[{"role": "user", "content": "привет"}],
        max_output_tokens=10,
    )

    first = provider.complete(request)
    second = provider.complete(request)

    assert first.content == "готово"
    assert first.model == "compatible-model"
    assert first.finish_reason == "stop"
    assert second.content == "готово"
    assert factory_calls == 1
    assert client.chat.completions.calls[0]["max_tokens"] == 10


def test_openai_compatible_provider_preserves_explicit_empty_capabilities() -> None:
    provider = OpenAICompatibleProvider(
        client=fake_client({"choices": []}),
        default_model="model",
        capabilities=(),
    )

    assert provider.capabilities == frozenset()


def test_openai_compatible_provider_rejects_ignored_factory_kwargs() -> None:
    with pytest.raises(ValueError, match="built-in OpenAI client factory"):
        OpenAICompatibleProvider(
            client_factory=lambda: object(),
            client_kwargs={"api_key": "unused"},
        )
