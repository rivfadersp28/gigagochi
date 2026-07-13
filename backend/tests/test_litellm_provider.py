from __future__ import annotations

import sys
from types import ModuleType

from app.llm import LLMRequest, LLMTool, StructuredOutputSchema
from app.llm.providers import LiteLLMProvider


def test_litellm_provider_preserves_explicit_empty_capabilities() -> None:
    provider = LiteLLMProvider(default_model="provider/model", capabilities=())

    assert provider.capabilities == frozenset()


def test_litellm_provider_imports_lazily_and_maps_neutral_contract(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "litellm", raising=False)
    provider = LiteLLMProvider(
        default_model="openai/gpt-test",
        completion_kwargs={"timeout": 30},
    )
    assert "litellm" not in sys.modules

    calls: list[dict] = []
    response = {
        "model": "openai/gpt-test",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "rename_pet",
                                "arguments": {"name": "Луна"},
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
    }
    fake_litellm = ModuleType("litellm")

    def completion(**kwargs):
        calls.append(kwargs)
        return response

    fake_litellm.completion = completion
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    result = provider.complete(
        LLMRequest(
            messages=[{"role": "user", "content": "Назови питомца"}],
            structured_output=StructuredOutputSchema(
                name="reply",
                schema={"type": "object"},
            ),
            tools=[
                LLMTool(
                    name="rename_pet",
                    description="Rename the pet",
                    parameters={"type": "object"},
                    strict=True,
                )
            ],
            tool_choice="auto",
            temperature=0.3,
            max_output_tokens=120,
            reasoning_effort="low",
            extra={"seed": 7},
        )
    )

    assert calls == [
        {
            "timeout": 30,
            "seed": 7,
            "model": "openai/gpt-test",
            "messages": [{"role": "user", "content": "Назови питомца"}],
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
            "temperature": 0.3,
            "max_tokens": 120,
            "reasoning_effort": "low",
        }
    ]
    assert result.content is None
    assert result.tool_calls[0].name == "rename_pet"
    assert result.tool_calls[0].arguments == '{"name": "Луна"}'
    assert result.usage is not None
    assert result.usage.total_tokens == 11
    assert result.raw is response
