from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.llm.contracts import (
    LLMCapability,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    LLMUsage,
)


class GigaChatProviderError(LLMProviderError):
    pass


class GigaChatAuthenticationError(GigaChatProviderError):
    error_kind = "authentication"


class GigaChatResponseError(GigaChatProviderError):
    pass


class GigaChatUnsupportedFeatureError(GigaChatProviderError):
    pass


_CAPABILITIES = frozenset(
    {
        LLMCapability.TEXT,
        LLMCapability.STRUCTURED_OUTPUT,
        LLMCapability.TOOLS,
        LLMCapability.REASONING,
    }
)
_RESERVED_EXTRA_KEYS = frozenset(
    {
        "function_call",
        "functions",
        "max_completion_tokens",
        "max_tokens",
        "messages",
        "model",
        "reasoning_effort",
        "response_format",
        "stream",
        "temperature",
        "timeout",
        "tool_choice",
        "tools",
    }
)
_SUPPORTED_REASONING_EFFORTS = frozenset({"low", "medium", "high"})
_SCHEMA_META_KEYS = frozenset({"$schema", "$id", "$comment", "$defs", "definitions"})
_SUPPORTED_SCHEMA_FORMATS = frozenset({"date", "date-time", "time"})
_TEXT_CONTENT_TYPES = frozenset({"text", "input_text"})
_IMAGE_CONTENT_TYPES = frozenset({"image", "image_url", "input_image"})
_USER_AGENT = "GigaChat-GigaTool-LiteLLM"
_STRUCTURED_OUTPUT_ANCHOR_MESSAGE = (
    "Сгенерируй одну итоговую реплику по правилам выше. "
    "Верни её через указанную функцию структурированного ответа."
)
_PROMPT_JSON_CONTRACT_MESSAGE = (
    "Верни только JSON object без markdown, без пояснений и без текста вне JSON. "
    "JSON schema: {schema}"
)


def _required_text(value: Any, *, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} must not be empty")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _required_credential(value: Any, *, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} must not be empty")
    credential = str(value)
    if not credential.strip():
        raise ValueError(f"{field_name} must not be empty")
    return credential


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _response_text(response: httpx.Response, *, limit: int = 1000) -> str:
    try:
        return response.text[:limit]
    except Exception:
        return "<unavailable>"


def _json_arguments(value: Any, *, require_valid_json: bool) -> str:
    if value is None or value == "":
        value = {}
    if isinstance(value, str):
        if require_valid_json:
            try:
                json.loads(value)
            except json.JSONDecodeError as exc:
                raise GigaChatResponseError(
                    "GigaChat structured-output function returned invalid JSON arguments"
                ) from exc
        return value
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise GigaChatResponseError(
            "GigaChat function returned arguments that cannot be encoded as JSON"
        ) from exc
    if require_valid_json:
        json.loads(encoded)
    return encoded


def _content_text(content: Any) -> str | None:
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping) and part.get("text") is not None:
                parts.append(str(part["text"]))
            elif isinstance(part, str):
                parts.append(part)
        if parts:
            return "".join(parts)
    return str(content)


def _schema_ref_name(reference: str) -> str | None:
    for prefix in ("#/$defs/", "#/definitions/"):
        if reference.startswith(prefix) and len(reference) > len(prefix):
            return reference[len(prefix) :]
    return None


def _null_schema(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    schema_type = value.get("type")
    if schema_type == "null":
        return True
    return (
        isinstance(schema_type, list)
        and bool(schema_type)
        and all(item == "null" for item in schema_type)
    )


def _merge_schema(target: dict[str, Any], source: Any) -> None:
    if not isinstance(source, Mapping):
        return
    for key, value in source.items():
        if (
            key == "properties"
            and isinstance(target.get("properties"), Mapping)
            and isinstance(value, Mapping)
        ):
            target["properties"] = {**value, **target["properties"]}
        elif (
            key == "required"
            and isinstance(target.get("required"), list)
            and isinstance(value, list)
        ):
            target["required"] = list(dict.fromkeys([*target["required"], *value]))
        elif key not in target:
            target[key] = value


def _normalize_schema_node(
    node: Any,
    definitions: Mapping[str, Any],
    seen: frozenset[str],
) -> Any:
    if isinstance(node, list):
        return [_normalize_schema_node(item, definitions, seen) for item in node]
    if not isinstance(node, Mapping):
        return node

    reference = node.get("$ref")
    if isinstance(reference, str):
        name = _schema_ref_name(reference)
        referenced = definitions.get(name) if name is not None else None
        if referenced is None or name in seen:
            fallback: dict[str, Any] = {"type": "object", "properties": {}}
            for key, value in node.items():
                if key == "$ref" or key in _SCHEMA_META_KEYS:
                    continue
                fallback[key] = _normalize_schema_node(value, definitions, seen)
            return fallback
        merged = dict(referenced)
        merged.update({key: value for key, value in node.items() if key != "$ref"})
        return _normalize_schema_node(merged, definitions, seen | {name})

    normalized: dict[str, Any] = {}
    for key, value in node.items():
        if key in _SCHEMA_META_KEYS:
            continue
        if key == "format" and value not in _SUPPORTED_SCHEMA_FORMATS:
            continue
        if key == "type" and isinstance(value, list):
            non_null = [item for item in value if item != "null"]
            normalized[key] = non_null[0] if non_null else "string"
            continue
        if key == "enum" and isinstance(value, list):
            normalized[key] = [
                _normalize_schema_node(item, definitions, seen)
                for item in value
                if item is not None
            ]
            continue
        if key in {"anyOf", "oneOf"} and isinstance(value, list):
            branches = [item for item in value if not _null_schema(item)]
            selected = branches[0] if branches else (value[0] if value else {"type": "object"})
            _merge_schema(
                normalized,
                _normalize_schema_node(selected, definitions, seen),
            )
            continue
        if key == "allOf" and isinstance(value, list):
            for branch in value:
                _merge_schema(
                    normalized,
                    _normalize_schema_node(branch, definitions, seen),
                )
            continue
        normalized[key] = _normalize_schema_node(value, definitions, seen)

    if normalized.get("type") == "object" and "properties" not in normalized:
        normalized["properties"] = {}
    return normalized


def _legacy_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        value = schema.get(key)
        if isinstance(value, Mapping):
            definitions.update(value)
    normalized = _normalize_schema_node(schema, definitions, frozenset())
    if not isinstance(normalized, dict):
        raise GigaChatUnsupportedFeatureError("GigaChat function schema must be an object")
    return normalized


def _uses_prompt_json_contract(request: LLMRequest, selected_model: str) -> bool:
    return (
        request.structured_output is not None
        and not request.tools
        and selected_model.strip().lower().startswith("gigachat-3.5")
    )


def _append_system_contract(messages: list[dict[str, Any]], contract: str) -> None:
    for message in messages:
        if message.get("role") == "system":
            content = str(message.get("content") or "").strip()
            message["content"] = f"{content}\n\n{contract}" if content else contract
            return
    messages.insert(0, {"role": "system", "content": contract})


def _schema_allows_null(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return False
    schema_type = schema.get("type")
    return (
        schema_type == "null"
        or (isinstance(schema_type, list) and "null" in schema_type)
        or (isinstance(schema.get("enum"), list) and None in schema["enum"])
    )


def _structured_output_fallback_content(
    schema: Mapping[str, Any],
    content: str,
) -> str | None:
    normalized_content = content.strip()
    if not normalized_content:
        return None
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, Mapping) or "reply" not in properties:
        return None
    if required is not None and not isinstance(required, list):
        return None

    payload: dict[str, Any] = {"reply": normalized_content}
    for field in required or ():
        if field in payload or not isinstance(field, str):
            continue
        field_schema = properties.get(field)
        if _schema_allows_null(field_schema):
            payload[field] = None
        elif isinstance(field_schema, Mapping) and field_schema.get("type") == "integer":
            payload[field] = 0
        elif isinstance(field_schema, Mapping) and field_schema.get("type") == "number":
            payload[field] = 0
        elif isinstance(field_schema, Mapping) and field_schema.get("type") == "boolean":
            payload[field] = False
        else:
            payload[field] = ""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _repair_truncated_json_closers(content: str) -> str | None:
    normalized = content.strip()
    if not normalized:
        return None

    expected_closers: list[str] = []
    in_string = False
    escaped = False
    for character in normalized:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            expected_closers.append("}")
        elif character == "[":
            expected_closers.append("]")
        elif character in "}]":
            if not expected_closers or expected_closers.pop() != character:
                return None

    if in_string or not expected_closers:
        return None

    repaired = normalized + "".join(reversed(expected_closers))
    try:
        json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return repaired


def _api_urls(base_url: str) -> tuple[tuple[str, str], str]:
    normalized = _required_text(base_url, field_name="base_url").rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query parameters or a fragment")

    if parsed.path.rstrip("/").endswith("/v1"):
        api_v1_url = normalized
        root_url = normalized[:-3].rstrip("/")
    else:
        root_url = normalized
        api_v1_url = f"{normalized}/v1"
    return (f"{api_v1_url}/token", f"{root_url}/token"), (f"{api_v1_url}/chat/completions")


class GigaChatProvider:
    """Synchronous text-only adapter for GigaChat's legacy REST contract."""

    capabilities = _CAPABILITIES

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        default_model: str = "GigaChat-3-Ultra",
        name: str = "gigachat",
        verify_ssl: bool | str | None = None,
        verify: bool | str | None = None,
        timeout_seconds: float | None = None,
        token_timeout_seconds: float = 30.0,
        chat_timeout_seconds: float = 120.0,
        token_ttl_seconds: float | None = None,
        default_token_ttl_seconds: float | None = None,
        client: httpx.Client | None = None,
        client_factory: Callable[..., httpx.Client] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if client is not None and client_factory is not None:
            raise ValueError("pass either client or client_factory, not both")
        if verify_ssl is not None and verify is not None:
            raise ValueError("pass either verify_ssl or verify, not both")
        if timeout_seconds is not None:
            token_timeout_seconds = timeout_seconds
            chat_timeout_seconds = timeout_seconds
        if token_ttl_seconds is not None and default_token_ttl_seconds is not None:
            raise ValueError("pass either token_ttl_seconds or default_token_ttl_seconds, not both")
        resolved_token_ttl = (
            token_ttl_seconds
            if token_ttl_seconds is not None
            else default_token_ttl_seconds
            if default_token_ttl_seconds is not None
            else 1500.0
        )
        if token_timeout_seconds <= 0:
            raise ValueError("token_timeout_seconds must be greater than zero")
        if chat_timeout_seconds <= 0:
            raise ValueError("chat_timeout_seconds must be greater than zero")
        if resolved_token_ttl <= 0:
            raise ValueError("token TTL must be greater than zero")

        normalized_name = _required_text(name, field_name="name").lower()
        normalized_model = _required_text(default_model, field_name="default_model")
        token_urls, chat_url = _api_urls(base_url)

        self.name = normalized_name
        self.default_model = normalized_model
        self.base_url = _required_text(base_url, field_name="base_url").rstrip("/")
        self.verify_ssl = (
            verify_ssl if verify_ssl is not None else verify if verify is not None else True
        )
        self._username = _required_credential(username, field_name="username")
        self._password = _required_credential(password, field_name="password")
        self._token_timeout_seconds = float(token_timeout_seconds)
        self._chat_timeout_seconds = float(chat_timeout_seconds)
        self._token_ttl_seconds = float(resolved_token_ttl)
        self._token_urls = token_urls
        self._chat_url = chat_url
        self._clock = clock

        self._client = client
        self._client_factory = client_factory or httpx.Client
        self._owns_client = client is None
        self._client_lock = RLock()
        self._token_lock = RLock()
        self._token: str | None = None
        self._token_expires_at = 0.0

    @property
    def client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                self._client = self._client_factory(
                    verify=self.verify_ssl,
                    timeout=self._chat_timeout_seconds,
                    follow_redirects=True,
                )
        return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None and self._owns_client:
                self._client.close()
                self._client = None

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload, structured_function_name, selected_model = self._request_payload(request)
        token = self._get_token()
        response = self._post_chat(
            payload,
            token,
            timeout_seconds=request.timeout_seconds,
        )
        if response.status_code == 401:
            token = self._refresh_after_unauthorized(token)
            response = self._post_chat(
                payload,
                token,
                timeout_seconds=request.timeout_seconds,
            )
        if response.status_code >= 400:
            raise GigaChatProviderError(
                "GigaChat completion failed: "
                f"HTTP {response.status_code}: {_response_text(response)}",
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except Exception as exc:
            raise GigaChatResponseError(
                f"GigaChat completion returned invalid JSON: {_response_text(response)}"
            ) from exc
        if not isinstance(body, Mapping):
            raise GigaChatResponseError("GigaChat completion response must be a JSON object")
        return self._normalize_response(
            body,
            request=request,
            structured_function_name=structured_function_name,
            selected_model=selected_model,
        )

    def _request_payload(self, request: LLMRequest) -> tuple[dict[str, Any], str | None, str]:
        conflicts = _RESERVED_EXTRA_KEYS.intersection(request.extra)
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ValueError(f"request.extra cannot override normalized fields: {names}")

        selected_model = request.model or self.default_model
        if selected_model.lower().startswith("gigachat/"):
            selected_model = selected_model.split("/", 1)[1]
        selected_model = _required_text(selected_model, field_name="model")

        payload: dict[str, Any] = deepcopy(dict(request.extra))
        messages = self._messages(request.messages)
        payload.update(
            {
                "model": selected_model,
                "messages": messages,
            }
        )
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens

        if request.reasoning_effort is not None and not selected_model.strip().lower().startswith(
            "gigachat-3.5"
        ):
            reasoning_effort = request.reasoning_effort.strip().lower()
            if reasoning_effort == "none":
                pass
            elif reasoning_effort in _SUPPORTED_REASONING_EFFORTS:
                payload["reasoning_effort"] = reasoning_effort
            else:
                supported = ", ".join(sorted({*_SUPPORTED_REASONING_EFFORTS, "none"}))
                raise GigaChatUnsupportedFeatureError(
                    f"unsupported GigaChat reasoning_effort {request.reasoning_effort!r}; "
                    f"supported values: {supported}"
                )

        functions = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": _legacy_schema(tool.parameters),
            }
            for tool in request.tools
        ]
        structured_function_name: str | None = None
        use_prompt_json_contract = _uses_prompt_json_contract(request, selected_model)
        if request.structured_output is not None and not use_prompt_json_contract:
            structured_function_name = self._structured_function_name(
                request.structured_output.name,
                {function["name"] for function in functions},
            )
            functions.append(
                {
                    "name": structured_function_name,
                    "description": "Return the final answer using this JSON schema.",
                    "parameters": _legacy_schema(request.structured_output.schema),
                }
            )
        elif request.structured_output is not None:
            _append_system_contract(
                messages,
                _PROMPT_JSON_CONTRACT_MESSAGE.format(
                    schema=json.dumps(
                        _legacy_schema(request.structured_output.schema),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
            )

        if functions:
            if structured_function_name is not None and not self._has_function_call_anchor(
                messages
            ):
                messages.append(
                    {
                        "role": "user",
                        "content": _STRUCTURED_OUTPUT_ANCHOR_MESSAGE,
                    }
                )
            payload["functions"] = functions
            payload["function_call"] = self._function_choice(
                request,
                structured_function_name=structured_function_name,
                function_names=tuple(function["name"] for function in functions),
            )
        return payload, structured_function_name, selected_model

    @staticmethod
    def _has_function_call_anchor(messages: Sequence[Mapping[str, Any]]) -> bool:
        return any(message.get("role") in {"user", "function"} for message in messages)

    @staticmethod
    def _structured_function_name(name: str, used_names: set[str]) -> str:
        if name not in used_names:
            return name
        base = f"structured_output_{name}"
        candidate = base
        suffix = 2
        while candidate in used_names:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _function_choice(
        request: LLMRequest,
        *,
        structured_function_name: str | None,
        function_names: tuple[str, ...],
    ) -> str | dict[str, str]:
        choice = request.tool_choice
        if choice is None:
            if structured_function_name is not None and not request.tools:
                return {"name": structured_function_name}
            return "auto"

        if isinstance(choice, Mapping):
            function = choice.get("function")
            name = function.get("name") if isinstance(function, Mapping) else choice.get("name")
            if not isinstance(name, str) or name not in function_names:
                raise GigaChatUnsupportedFeatureError(
                    "GigaChat tool_choice must name one of the request functions"
                )
            return {"name": name}

        normalized = str(choice).strip().lower()
        if normalized == "auto":
            return "auto"
        if normalized == "none":
            if structured_function_name is not None:
                raise GigaChatUnsupportedFeatureError(
                    "tool_choice='none' is incompatible with structured output on GigaChat"
                )
            return "none"
        if normalized == "required":
            if len(function_names) != 1:
                raise GigaChatUnsupportedFeatureError(
                    "GigaChat can emulate tool_choice='required' only with one function"
                )
            return {"name": function_names[0]}
        raise GigaChatUnsupportedFeatureError(f"unsupported GigaChat tool_choice value: {choice!r}")

    @classmethod
    def _messages(cls, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        tool_names: dict[str, str] = {}
        for message in messages:
            calls = message.get("tool_calls")
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, Mapping) or call.get("id") is None:
                    continue
                function = call.get("function")
                if isinstance(function, Mapping) and function.get("name"):
                    tool_names[str(call["id"])] = str(function["name"])

        normalized: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "").strip().lower()
            if role == "developer":
                role = "system"
            if role not in {"assistant", "function", "system", "tool", "user"}:
                raise GigaChatUnsupportedFeatureError(
                    f"unsupported GigaChat message role: {role or '<empty>'!r}"
                )

            if role == "tool":
                call_id = message.get("tool_call_id")
                name = message.get("name") or (
                    tool_names.get(str(call_id)) if call_id is not None else None
                )
                if not name:
                    raise GigaChatUnsupportedFeatureError(
                        "GigaChat tool-result message requires a resolvable function name"
                    )
                normalized.append(
                    {
                        "role": "function",
                        "name": str(name),
                        "content": cls._function_result_content(message.get("content")),
                    }
                )
                continue

            output: dict[str, Any] = {
                "role": role,
                "content": cls._message_content(message.get("content")),
            }
            if message.get("name") is not None:
                output["name"] = str(message["name"])

            calls = message.get("tool_calls")
            if isinstance(calls, list) and calls:
                if role != "assistant":
                    raise GigaChatUnsupportedFeatureError(
                        "only assistant messages may contain tool_calls"
                    )
                if len(calls) != 1:
                    raise GigaChatUnsupportedFeatureError(
                        "GigaChat legacy history supports one tool call per assistant message"
                    )
                output["function_call"] = cls._history_function_call(calls[0])
            elif message.get("function_call") is not None:
                if role != "assistant":
                    raise GigaChatUnsupportedFeatureError(
                        "only assistant messages may contain function_call"
                    )
                output["function_call"] = cls._history_function_call(
                    {"function": message["function_call"]}
                )
            normalized.append(output)
        return normalized

    @staticmethod
    def _message_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            if "data:image/" in content:
                raise GigaChatUnsupportedFeatureError(
                    "GigaChatProvider is text-only and rejects inline image data"
                )
            return content
        if not isinstance(content, Sequence) or isinstance(content, (bytes, bytearray)):
            return str(content)

        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, Mapping):
                raise GigaChatUnsupportedFeatureError(
                    "GigaChatProvider accepts only text message content"
                )
            content_type = str(part.get("type") or "").strip().lower()
            if content_type in _IMAGE_CONTENT_TYPES or "image_url" in part:
                raise GigaChatUnsupportedFeatureError(
                    "GigaChatProvider is text-only and rejects image message content"
                )
            text = part.get("text")
            if text is None or (content_type and content_type not in _TEXT_CONTENT_TYPES):
                raise GigaChatUnsupportedFeatureError(
                    "GigaChatProvider accepts only text message content"
                )
            parts.append(str(text))
        joined = "".join(parts)
        if "data:image/" in joined:
            raise GigaChatUnsupportedFeatureError(
                "GigaChatProvider is text-only and rejects inline image data"
            )
        return joined

    @staticmethod
    def _function_result_content(content: Any) -> str:
        if isinstance(content, str):
            try:
                json.loads(content)
            except json.JSONDecodeError:
                return json.dumps({"result": content}, ensure_ascii=False)
            return content
        if content is None:
            content = {"result": ""}
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _history_function_call(call: Any) -> dict[str, Any]:
        if not isinstance(call, Mapping):
            raise GigaChatUnsupportedFeatureError("tool call history entry must be an object")
        function = call.get("function")
        if not isinstance(function, Mapping) or not function.get("name"):
            raise GigaChatUnsupportedFeatureError(
                "tool call history entry must contain a function name"
            )
        raw_arguments = function.get("arguments") or {}
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise GigaChatUnsupportedFeatureError(
                    "tool call history arguments must contain valid JSON"
                ) from exc
        else:
            arguments = deepcopy(raw_arguments)
        return {"name": str(function["name"]), "arguments": arguments}

    def _get_token(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._token_expires_at:
            return self._token
        with self._token_lock:
            now = self._clock()
            if self._token is not None and now < self._token_expires_at:
                return self._token
            return self._fetch_and_store_token(now)

    def _refresh_after_unauthorized(self, stale_token: str) -> str:
        with self._token_lock:
            now = self._clock()
            if (
                self._token is not None
                and self._token != stale_token
                and now < self._token_expires_at
            ):
                return self._token
            self._token = None
            self._token_expires_at = 0.0
            return self._fetch_and_store_token(now)

    def _fetch_and_store_token(self, now: float) -> str:
        auth = httpx.BasicAuth(self._username, self._password)
        headers = {
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "User-Agent": _USER_AGENT,
        }
        response: httpx.Response | None = None
        for index, url in enumerate(self._token_urls):
            try:
                response = self.client.post(
                    url,
                    auth=auth,
                    headers=headers,
                    timeout=self._token_timeout_seconds,
                )
            except httpx.HTTPError as exc:
                raise GigaChatAuthenticationError(
                    f"GigaChat token request failed for {url}: {exc}"
                ) from exc
            if response.status_code in {404, 405} and index < len(self._token_urls) - 1:
                continue
            if response.status_code >= 400:
                raise GigaChatAuthenticationError(
                    "GigaChat token request failed: "
                    f"HTTP {response.status_code}: {_response_text(response)}",
                    status_code=response.status_code,
                )
            break

        if response is None:
            raise GigaChatAuthenticationError("GigaChat token request did not return a response")
        try:
            payload = response.json()
        except Exception as exc:
            raise GigaChatAuthenticationError(
                f"GigaChat token response contained invalid JSON: {_response_text(response)}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise GigaChatAuthenticationError("GigaChat token response must be a JSON object")
        token = payload.get("tok") or payload.get("access_token") or payload.get("token")
        if not token:
            raise GigaChatAuthenticationError(
                "GigaChat token response is missing tok/access_token/token"
            )

        self._token = str(token)
        self._token_expires_at = self._token_expiry(payload, now)
        return self._token

    def _token_expiry(self, payload: Mapping[str, Any], now: float) -> float:
        expiries = [now + self._token_ttl_seconds]
        expires_in = payload.get("expires_in")
        try:
            expires_in_seconds = float(expires_in) if expires_in is not None else 0.0
        except (TypeError, ValueError):
            expires_in_seconds = 0.0
        if expires_in_seconds > 0:
            expiries.append(now + expires_in_seconds)

        for key in ("exp", "expires_at"):
            raw_expiry = payload.get(key)
            try:
                expiry = float(raw_expiry) if raw_expiry is not None else 0.0
            except (TypeError, ValueError):
                continue
            if expiry > 1_000_000_000_000:
                expiry /= 1000.0
            if expiry > now:
                expiries.append(expiry)
        return min(expiries)

    def _post_chat(
        self,
        payload: Mapping[str, Any],
        token: str,
        *,
        timeout_seconds: float | None,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "X-Request-ID": str(uuid.uuid4()),
        }
        try:
            return self.client.post(
                self._chat_url,
                headers=headers,
                json=payload,
                timeout=timeout_seconds or self._chat_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise GigaChatProviderError(f"GigaChat completion request failed: {exc}") from exc

    @staticmethod
    def _normalize_response(
        body: Mapping[str, Any],
        *,
        request: LLMRequest,
        structured_function_name: str | None,
        selected_model: str,
    ) -> LLMResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise GigaChatResponseError("GigaChat completion response has no choices")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise GigaChatResponseError("GigaChat completion choice must be an object")
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise GigaChatResponseError("GigaChat completion choice has no message")

        raw_calls: list[Mapping[str, Any]] = []
        function_call = message.get("function_call")
        if isinstance(function_call, Mapping):
            raw_calls.append(
                {
                    "id": function_call.get("id"),
                    "function": function_call,
                }
            )
        tool_calls_value = message.get("tool_calls")
        if isinstance(tool_calls_value, list):
            raw_calls.extend(call for call in tool_calls_value if isinstance(call, Mapping))

        structured_content: str | None = None
        tool_calls: list[LLMToolCall] = []
        for raw_call in raw_calls:
            function = raw_call.get("function")
            if not isinstance(function, Mapping) or not function.get("name"):
                raise GigaChatResponseError("GigaChat function call has no function name")
            name = str(function["name"])
            is_structured = (
                structured_function_name is not None and name == structured_function_name
            )
            arguments = _json_arguments(
                function.get("arguments"),
                require_valid_json=is_structured,
            )
            if is_structured:
                if structured_content is not None:
                    raise GigaChatResponseError(
                        "GigaChat returned more than one structured-output function call"
                    )
                structured_content = arguments
            else:
                call_id = raw_call.get("id") or f"call_{uuid.uuid4().hex[:24]}"
                tool_calls.append(
                    LLMToolCall(
                        id=str(call_id),
                        name=name,
                        arguments=arguments,
                    )
                )

        if structured_content is not None and tool_calls:
            raise GigaChatResponseError(
                "GigaChat returned structured output and ordinary tool calls together"
            )

        content = structured_content
        if content is None:
            content = _content_text(message.get("content"))
        if request.structured_output is not None and not tool_calls:
            if content is None:
                raise GigaChatResponseError("GigaChat returned no structured output")
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                repaired_content = _repair_truncated_json_closers(content)
                if repaired_content is not None:
                    content = repaired_content
                else:
                    fallback_content = _structured_output_fallback_content(
                        request.structured_output.schema,
                        content,
                    )
                    if fallback_content is None:
                        raise GigaChatResponseError(
                            "GigaChat returned non-JSON content for a structured-output request"
                        ) from exc
                    content = fallback_content

        usage_value = body.get("usage")
        usage = None
        if isinstance(usage_value, Mapping):
            usage = LLMUsage(
                prompt_tokens=_optional_int(usage_value.get("prompt_tokens")),
                completion_tokens=_optional_int(usage_value.get("completion_tokens")),
                total_tokens=_optional_int(usage_value.get("total_tokens")),
            )

        finish_reason_value = choice.get("finish_reason")
        finish_reason = str(finish_reason_value) if finish_reason_value is not None else None
        if finish_reason == "function_call":
            finish_reason = "tool_calls" if tool_calls else "stop"
        model_value = body.get("model")
        model = str(model_value) if model_value is not None else selected_model
        return LLMResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            model=model,
            finish_reason=finish_reason,
            usage=usage,
            raw=body,
        )
