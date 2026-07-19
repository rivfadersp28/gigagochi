from __future__ import annotations

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.llm import (
    LLMCapability,
    LLMRequest,
    LLMTool,
    StructuredOutputSchema,
)
from app.llm.providers.gigachat import (
    GigaChatProvider,
    GigaChatProviderError,
    GigaChatResponseError,
    GigaChatUnsupportedFeatureError,
)


def _completion(content: str = "готово", *, model: str = "GigaChat-test") -> dict:
    return {
        "model": model,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def _provider(
    handler,
    *,
    base_url: str = "https://giga.test",
    clock=None,
    token_ttl_seconds: float = 1500,
    default_model: str = "GigaChat-test",
) -> GigaChatProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GigaChatProvider(
        base_url=base_url,
        username="alice",
        password="secret",
        default_model=default_model,
        client=client,
        clock=clock or time.time,
        token_ttl_seconds=token_ttl_seconds,
    )


def test_maps_plain_text_request_and_uses_basic_auth_token_fallback() -> None:
    paths: list[str] = []
    chat_payloads: list[dict] = []
    chat_timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/token":
            return httpx.Response(404, json={"error": "route not found"})
        if request.url.path == "/token":
            expected = base64.b64encode(b"alice:secret").decode("ascii")
            assert request.headers["Authorization"] == f"Basic {expected}"
            assert request.headers["RqUID"]
            return httpx.Response(200, json={"tok": "token-1", "expires_in": 600})
        if request.url.path == "/v1/chat/completions":
            assert request.headers["Authorization"] == "Bearer token-1"
            chat_payloads.append(json.loads(request.content))
            chat_timeouts.append(request.extensions["timeout"]["read"])
            return httpx.Response(200, json=_completion())
        raise AssertionError(f"unexpected URL: {request.url}")

    provider = _provider(handler)
    response = provider.complete(
        LLMRequest(
            messages=[
                {"role": "system", "content": "Ты питомец"},
                {"role": "user", "content": "Привет"},
            ],
            temperature=0.4,
            max_output_tokens=120,
            reasoning_effort="high",
            timeout_seconds=17,
            extra={"seed": 7},
        )
    )

    assert paths == ["/v1/token", "/token", "/v1/chat/completions"]
    assert chat_payloads == [
        {
            "seed": 7,
            "model": "GigaChat-test",
            "messages": [
                {"role": "system", "content": "Ты питомец"},
                {"role": "user", "content": "Привет"},
            ],
            "temperature": 0.4,
            "max_tokens": 120,
            "reasoning_effort": "high",
        }
    ]
    assert chat_timeouts == [17]
    assert response.content == "готово"
    assert response.model == "GigaChat-test"
    assert response.finish_reason == "stop"
    assert response.usage is not None
    assert response.usage.total_tokens == 5
    assert provider.capabilities == frozenset(LLMCapability)


def test_default_owned_client_enables_tls_verification() -> None:
    client_kwargs: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        return httpx.Response(200, json=_completion())

    def client_factory(**kwargs):
        client_kwargs.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handler))

    provider = GigaChatProvider(
        base_url="https://giga.test/v1",
        username="alice",
        password="secret",
        client_factory=client_factory,
    )
    try:
        assert (
            provider.complete(LLMRequest(messages=[{"role": "user", "content": "Привет"}])).content
            == "готово"
        )
    finally:
        provider.close()

    assert len(client_kwargs) == 1
    assert client_kwargs[0]["verify"] is True
    assert client_kwargs[0]["follow_redirects"] is True


def test_token_cache_obeys_ttl() -> None:
    now = [100.0]
    token_calls = 0
    chat_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/v1/token":
            token_calls += 1
            return httpx.Response(200, json={"access_token": f"token-{token_calls}"})
        chat_tokens.append(request.headers["Authorization"])
        return httpx.Response(200, json=_completion())

    provider = _provider(
        handler,
        clock=lambda: now[0],
        token_ttl_seconds=10,
    )
    request = LLMRequest(messages=[{"role": "user", "content": "Привет"}])

    provider.complete(request)
    now[0] = 109.9
    provider.complete(request)
    now[0] = 110.0
    provider.complete(request)

    assert token_calls == 2
    assert chat_tokens == ["Bearer token-1", "Bearer token-1", "Bearer token-2"]


def test_refreshes_token_once_after_401_and_reuses_refreshed_token() -> None:
    token_calls = 0
    chat_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/v1/token":
            token_calls += 1
            return httpx.Response(200, json={"token": f"token-{token_calls}"})
        authorization = request.headers["Authorization"]
        chat_tokens.append(authorization)
        if authorization == "Bearer token-1":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json=_completion("после refresh"))

    provider = _provider(handler)
    request = LLMRequest(messages=[{"role": "user", "content": "Привет"}])

    assert provider.complete(request).content == "после refresh"
    assert provider.complete(request).content == "после refresh"

    assert token_calls == 2
    assert chat_tokens == ["Bearer token-1", "Bearer token-2", "Bearer token-2"]


def test_stops_after_one_unauthorized_retry() -> None:
    token_calls = 0
    chat_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_calls, token_calls
        if request.url.path == "/v1/token":
            token_calls += 1
            return httpx.Response(200, json={"tok": f"token-{token_calls}"})
        chat_calls += 1
        return httpx.Response(401, json={"error": "still unauthorized"})

    provider = _provider(handler)
    with pytest.raises(GigaChatProviderError) as error:
        provider.complete(LLMRequest(messages=[{"role": "user", "content": "Привет"}]))

    assert error.value.status_code == 401
    assert token_calls == 2
    assert chat_calls == 2


def test_structured_output_and_ordinary_tools_share_legacy_functions() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            function_call = {"name": "rename_pet", "arguments": {"name": "Луна"}}
        else:
            function_call = {
                "name": "reply",
                "arguments": {"reply": "Привет!", "mood": "happy"},
            }
        return httpx.Response(
            200,
            json={
                "model": "GigaChat-test",
                "choices": [
                    {
                        "finish_reason": "function_call",
                        "message": {"content": None, "function_call": function_call},
                    }
                ],
            },
        )

    provider = _provider(handler)
    request = LLMRequest(
        messages=[{"role": "user", "content": "Назови себя Луной"}],
        structured_output=StructuredOutputSchema(
            name="reply",
            schema={
                "$defs": {
                    "Reply": {
                        "type": "object",
                        "properties": {
                            "reply": {"type": "string"},
                            "mood": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        },
                        "required": ["reply"],
                    }
                },
                "$ref": "#/$defs/Reply",
            },
        ),
        tools=[
            LLMTool(
                name="rename_pet",
                description="Rename the pet",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "format": "hostname"},
                    },
                    "required": ["name"],
                },
                strict=True,
            )
        ],
        tool_choice="auto",
    )

    tool_result = provider.complete(request)
    structured_result = provider.complete(request)

    assert [function["name"] for function in payloads[0]["functions"]] == [
        "rename_pet",
        "reply",
    ]
    assert payloads[0]["function_call"] == "auto"
    assert "tools" not in payloads[0]
    assert all(function["name"] != "text2image" for function in payloads[0]["functions"])
    assert "$defs" not in payloads[0]["functions"][1]["parameters"]
    assert payloads[0]["functions"][1]["parameters"]["properties"]["mood"] == {"type": "string"}
    assert "format" not in payloads[0]["functions"][0]["parameters"]["properties"]["name"]

    assert tool_result.content is None
    assert tool_result.finish_reason == "tool_calls"
    assert tool_result.tool_calls[0].name == "rename_pet"
    assert json.loads(tool_result.tool_calls[0].arguments) == {"name": "Луна"}
    assert structured_result.tool_calls == ()
    assert structured_result.finish_reason == "stop"
    assert json.loads(structured_result.content or "") == {
        "reply": "Привет!",
        "mood": "happy",
    }


def test_structured_output_with_system_only_prompt_adds_user_anchor() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(
            200,
            json={
                "model": "GigaChat-test",
                "choices": [
                    {
                        "finish_reason": "function_call",
                        "message": {
                            "content": None,
                            "function_call": {
                                "name": "visible_pet_reply",
                                "arguments": {"reply": "Слышу ветки.", "moodHint": None},
                            },
                        },
                    }
                ],
            },
        )

    provider = _provider(handler)
    response = provider.complete(
        LLMRequest(
            messages=[{"role": "system", "content": "Ты питомец. Ответь коротко."}],
            structured_output=StructuredOutputSchema(
                name="visible_pet_reply",
                schema={
                    "type": "object",
                    "properties": {
                        "reply": {"type": "string"},
                        "moodHint": {"type": ["string", "null"]},
                    },
                    "required": ["reply", "moodHint"],
                },
            ),
        )
    )

    assert payloads[0]["messages"] == [
        {"role": "system", "content": "Ты питомец. Ответь коротко."},
        {
            "role": "user",
            "content": (
                "Сгенерируй одну итоговую реплику по правилам выше. "
                "Верни её через указанную функцию структурированного ответа."
            ),
        },
    ]
    assert payloads[0]["function_call"] == {"name": "visible_pet_reply"}
    assert json.loads(response.content or "") == {
        "reply": "Слышу ветки.",
        "moodHint": None,
    }


def test_gigachat_35_structured_output_uses_system_json_contract_without_functions() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(
            200,
            json=_completion(
                json.dumps(
                    {"reply": "Ты Серега.", "faceHint": "happy"},
                    ensure_ascii=False,
                ),
                model="GigaChat-3.5-432B-A28B:32.9",
            ),
        )

    provider = _provider(handler, default_model="GigaChat-3.5-432B-A28B")
    response = provider.complete(
        LLMRequest(
            messages=[{"role": "user", "content": "кто я"}],
            structured_output=StructuredOutputSchema(
                name="visible_pet_reply",
                schema={
                    "type": "object",
                    "properties": {
                        "reply": {"type": "string"},
                        "faceHint": {"type": "string"},
                    },
                    "required": ["reply", "faceHint"],
                },
            ),
        )
    )

    assert "functions" not in payloads[0]
    assert "function_call" not in payloads[0]
    assert [message["role"] for message in payloads[0]["messages"]] == ["system", "user"]
    assert "JSON schema:" in payloads[0]["messages"][0]["content"]
    assert payloads[0]["messages"][-1] == {"role": "user", "content": "кто я"}
    assert json.loads(response.content or "") == {
        "reply": "Ты Серега.",
        "faceHint": "happy",
    }


def test_gigachat_35_omits_reasoning_effort() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=_completion("ок"))

    provider = _provider(handler, default_model="GigaChat-3.5-432B-A28B")
    provider.complete(
        LLMRequest(
            messages=[{"role": "user", "content": "привет"}],
            reasoning_effort="high",
        )
    )

    assert "reasoning_effort" not in payloads[0]


def test_structured_output_schema_removes_null_enum_values() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "GigaChat-test",
                "choices": [
                    {
                        "finish_reason": "function_call",
                        "message": {
                            "content": None,
                            "function_call": {
                                "name": "visible_pet_reply",
                                "arguments": {"faceHint": None},
                            },
                        },
                    }
                ],
            },
        )

    provider = _provider(handler)
    provider.complete(
        LLMRequest(
            messages=[{"role": "user", "content": "Ответь JSON"}],
            structured_output=StructuredOutputSchema(
                name="visible_pet_reply",
                schema={
                    "type": "object",
                    "properties": {
                        "faceHint": {
                            "type": ["string", "null"],
                            "enum": ["neutral", "happy", None],
                        },
                    },
                    "required": ["faceHint"],
                },
            ),
        )
    )

    assert payloads[0]["functions"][0]["parameters"]["properties"]["faceHint"] == {
        "type": "string",
        "enum": ["neutral", "happy"],
    }


def test_structured_output_reply_schema_wraps_plain_text_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        return httpx.Response(200, json=_completion("Просто текстом."))

    provider = _provider(handler)
    response = provider.complete(
        LLMRequest(
            messages=[{"role": "user", "content": "Ответь коротко"}],
            structured_output=StructuredOutputSchema(
                name="visible_pet_reply",
                schema={
                    "type": "object",
                    "properties": {
                        "reply": {"type": "string"},
                        "faceHint": {
                            "type": ["string", "null"],
                            "enum": ["neutral", "happy", None],
                        },
                        "moodHint": {
                            "type": ["string", "null"],
                            "enum": ["idle", "happy", None],
                        },
                    },
                    "required": ["reply", "faceHint", "moodHint"],
                },
            ),
        )
    )

    assert json.loads(response.content or "") == {
        "reply": "Просто текстом.",
        "faceHint": None,
        "moodHint": None,
    }


def test_structured_output_repairs_missing_object_closer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        return httpx.Response(200, json=_completion('{"value":"готово"'))

    response = _provider(handler).complete(
        LLMRequest(
            messages=[{"role": "user", "content": "Ответь JSON"}],
            structured_output=StructuredOutputSchema(
                name="structured_value",
                schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            ),
        )
    )

    assert json.loads(response.content or "") == {"value": "готово"}


def test_structured_output_repairs_nested_array_and_object_closers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        return httpx.Response(200, json=_completion('{"items":[{"value":"готово"}'))

    response = _provider(handler).complete(
        LLMRequest(
            messages=[{"role": "user", "content": "Ответь JSON"}],
            structured_output=StructuredOutputSchema(
                name="structured_items",
                schema={
                    "type": "object",
                    "properties": {"items": {"type": "array"}},
                    "required": ["items"],
                },
            ),
        )
    )

    assert json.loads(response.content or "") == {"items": [{"value": "готово"}]}


@pytest.mark.parametrize(
    "content",
    [
        '{"value":"незакрытая строка}',
        '{"items":[1}',
        '{"first":1 "second":2',
    ],
    ids=["unterminated-string", "mismatched-closer", "missing-comma"],
)
def test_structured_output_does_not_repair_other_invalid_json(content: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        return httpx.Response(200, json=_completion(content))

    with pytest.raises(GigaChatResponseError, match="non-JSON content"):
        _provider(handler).complete(
            LLMRequest(
                messages=[{"role": "user", "content": "Ответь JSON"}],
                structured_output=StructuredOutputSchema(
                    name="structured_value",
                    schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                ),
            )
        )


def test_translates_tool_history_to_legacy_messages() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=_completion())

    provider = _provider(handler)
    provider.complete(
        LLMRequest(
            messages=[
                {"role": "user", "content": "Новое имя?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "rename_pet",
                                "arguments": '{"name":"Луна"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "успешно"},
            ]
        )
    )

    assert payloads[0]["messages"][1] == {
        "role": "assistant",
        "content": "",
        "function_call": {"name": "rename_pet", "arguments": {"name": "Луна"}},
    }
    assert payloads[0]["messages"][2] == {
        "role": "function",
        "name": "rename_pet",
        "content": json.dumps({"result": "успешно"}, ensure_ascii=False),
    }


def test_reasoning_none_is_omitted_and_unknown_value_fails_before_http() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"tok": "token"})
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=_completion())

    provider = _provider(handler)
    provider.complete(
        LLMRequest(
            messages=[{"role": "user", "content": "Привет"}],
            reasoning_effort="none",
        )
    )
    request_count = len(payloads)

    with pytest.raises(GigaChatUnsupportedFeatureError, match="minimal"):
        provider.complete(
            LLMRequest(
                messages=[{"role": "user", "content": "Привет"}],
                reasoning_effort="minimal",
            )
        )

    assert "reasoning_effort" not in payloads[0]
    assert len(payloads) == request_count


def test_rejects_image_content_before_authentication() -> None:
    request_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        return httpx.Response(500)

    provider = _provider(handler)
    request = LLMRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Что здесь?"},
                    {"type": "image_url", "image_url": {"url": "https://img.test/a.png"}},
                ],
            }
        ]
    )

    with pytest.raises(GigaChatUnsupportedFeatureError, match="text-only"):
        provider.complete(request)

    assert request_paths == []


def test_token_cache_is_single_flight_across_threads() -> None:
    token_calls = 0
    counter_lock = threading.Lock()
    start = threading.Barrier(8)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/v1/token":
            with counter_lock:
                token_calls += 1
            time.sleep(0.02)
            return httpx.Response(200, json={"tok": "shared-token"})
        assert request.headers["Authorization"] == "Bearer shared-token"
        return httpx.Response(200, json=_completion())

    provider = _provider(handler)
    request = LLMRequest(messages=[{"role": "user", "content": "Привет"}])

    def complete() -> str | None:
        start.wait()
        return provider.complete(request).content

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: complete(), range(8)))

    assert results == ["готово"] * 8
    assert token_calls == 1
