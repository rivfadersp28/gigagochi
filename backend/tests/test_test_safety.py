from __future__ import annotations

import os
import subprocess
import sys


def test_python_subprocess_inherits_external_network_guard() -> None:
    script = """
import socket

try:
    socket.socket().connect((\"203.0.113.1\", 443))
except RuntimeError as exc:
    assert \"External network is disabled in test subprocesses\" in str(exc)
else:
    raise AssertionError(\"external socket guard was not installed\")

try:
    socket.socket().connect_ex((\"203.0.113.1\", 443))
except RuntimeError as exc:
    assert \"External network is disabled in test subprocesses\" in str(exc)
else:
    raise AssertionError(\"external connect_ex guard was not installed\")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ},
    )

    assert result.returncode == 0, result.stderr


def test_paid_credentials_are_blank_in_test_environment() -> None:
    for name in (
        "BOT_TOKEN",
        "GIGACHAT_PASSWORD",
        "GIGACHAT_USERNAME",
        "KANDINSKY_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        assert os.environ[name] == ""
