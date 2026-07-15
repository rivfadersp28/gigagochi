from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# Never let synthetic jobs sweep the developer's real generated-assets tree.
# Individual GC tests opt back in with an isolated temporary root.
os.environ["GENERATED_MEDIA_CLEANUP_ENABLED"] = "false"

# Environment variables override a developer's backend/.env in this process and
# every spawned child. A missed mock therefore has no usable paid credential.
for _credential_name in (
    "BOT_TOKEN",
    "GIGACHAT_PASSWORD",
    "GIGACHAT_USERNAME",
    "KANDINSKY_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
):
    os.environ[_credential_name] = ""

# Python subprocesses and multiprocessing spawn do not inherit the socket monkeypatch
# below. They do import sitecustomize from PYTHONPATH before application code.
_SUBPROCESS_GUARD_DIR = Path(__file__).resolve().parent / "network_guard"
_existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = os.pathsep.join(
    part for part in (str(_SUBPROCESS_GUARD_DIR), _existing_pythonpath) if part
)


@pytest.fixture(autouse=True)
def isolate_provider_task_receipts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    """Never reuse durable paid-task receipts between synthetic tests."""

    from app.config import get_settings

    monkeypatch.setenv(
        "PROVIDER_TASK_RECEIPT_STORE_PATH",
        str(tmp_path / "provider-task-receipts.sqlite3"),
    )
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def isolate_runtime_logs(tmp_path_factory: pytest.TempPathFactory):
    """Keep synthetic test diagnostics out of the developer's runtime logs."""

    from app.services import ai_error_service, prompt_debug

    log_dir = tmp_path_factory.mktemp("runtime-logs")
    original_paths = (
        prompt_debug.AI_PROMPT_LOG_PATH,
        prompt_debug.AI_RESPONSE_LOG_PATH,
        ai_error_service.AI_FAILURE_LOG_PATH,
    )
    prompt_debug.AI_PROMPT_LOG_PATH = log_dir / "ai-prompts.jsonl"
    prompt_debug.AI_RESPONSE_LOG_PATH = log_dir / "ai-responses.jsonl"
    ai_error_service.AI_FAILURE_LOG_PATH = log_dir / "ai-failures.jsonl"
    try:
        yield
    finally:
        (
            prompt_debug.AI_PROMPT_LOG_PATH,
            prompt_debug.AI_RESPONSE_LOG_PATH,
            ai_error_service.AI_FAILURE_LOG_PATH,
        ) = original_paths


@pytest.fixture(scope="session", autouse=True)
def block_external_network() -> None:
    """Prevent a missed mock from reaching paid providers during the test suite."""

    # httpx honors proxy variables by default. A loopback proxy would otherwise
    # bypass the socket guard and forward the request to an external provider.
    for variable in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(variable, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def assert_loopback(address: Any) -> None:
        if not isinstance(address, tuple) or not address:
            return
        host = str(address[0]).split("%", maxsplit=1)[0]
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = host.casefold() == "localhost"
        if not is_loopback:
            raise RuntimeError(f"External network is disabled in tests: {host}")

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        assert_loopback(address)
        return original_connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        assert_loopback(address)
        return original_connect_ex(sock, address)

    # Deliberately keep this patch for the whole pytest process. Background
    # executor threads may finish after an individual test fixture has torn down.
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
