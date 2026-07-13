from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.llm.contracts import LLMRequest, LLMResponse, LLMTool, StructuredOutputSchema
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.runtime import get_llm_gateway, resolve_llm_model


def _structured_output(value: Any) -> StructuredOutputSchema | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or value.get("type") != "json_schema":
        raise ValueError("only response_format.type=json_schema is supported by the LLM gateway")
    definition = value.get("json_schema")
    if not isinstance(definition, Mapping):
        raise ValueError("response_format.json_schema must be an object")
    schema = definition.get("schema")
    if not isinstance(schema, Mapping):
        raise ValueError("response_format.json_schema.schema must be an object")
    return StructuredOutputSchema(
        name=str(definition.get("name") or "structured_response"),
        schema=schema,
        strict=bool(definition.get("strict", True)),
    )


def _tools(value: Any) -> tuple[LLMTool, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("tools must be a list")
    tools: list[LLMTool] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("type") != "function":
            raise ValueError("only function tools are supported by the LLM gateway")
        function = item.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("tool.function must be an object")
        parameters = function.get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(parameters, Mapping):
            raise ValueError("tool.function.parameters must be an object")
        tools.append(
            LLMTool(
                name=str(function.get("name") or ""),
                description=str(function.get("description") or ""),
                parameters=parameters,
                strict=bool(function.get("strict", False)),
            )
        )
    return tuple(tools)


def llm_request_from_chat_kwargs(task: str, request_kwargs: Mapping[str, Any]) -> LLMRequest:
    values = dict(request_kwargs)
    messages = values.pop("messages", None)
    if not isinstance(messages, list):
        raise ValueError("chat request messages must be a list")
    model = values.pop("model", None)
    response_format = values.pop("response_format", None)
    tools_value = values.pop("tools", None)
    tool_choice = values.pop("tool_choice", None)
    temperature = values.pop("temperature", None)
    reasoning_effort = values.pop("reasoning_effort", None)
    timeout = values.pop("timeout", None)
    max_output_tokens = values.pop("max_completion_tokens", None)
    if max_output_tokens is None:
        max_output_tokens = values.pop("max_tokens", None)
    return LLMRequest(
        messages=messages,
        task=task,
        model=str(model) if model is not None else None,
        structured_output=_structured_output(response_format),
        tools=_tools(tools_value),
        tool_choice=tool_choice,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        reasoning_effort=str(reasoning_effort) if reasoning_effort is not None else None,
        timeout_seconds=float(timeout) if timeout is not None else None,
        extra=values,
    )


def complete_chat(
    task: str,
    request_kwargs: Mapping[str, Any],
    *,
    client: Any | None = None,
) -> LLMResponse:
    values = dict(request_kwargs)
    if client is None and values.get("model") is None:
        values["model"] = resolve_llm_model(task, values.get("model"))
    request = llm_request_from_chat_kwargs(task, values)
    if client is not None:
        return OpenAICompatibleProvider(
            name="client_override",
            client=client,
            default_model=request.model,
        ).complete(request)
    return get_llm_gateway().complete(request)


def response_log_value(response: LLMResponse) -> Any:
    return response.raw if response.raw is not None else response
