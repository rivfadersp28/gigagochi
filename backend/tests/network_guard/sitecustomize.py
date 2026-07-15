"""Deny non-loopback sockets in Python children spawned by the test suite."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _assert_loopback(address: Any) -> None:
    if not isinstance(address, tuple) or not address:
        return
    host = str(address[0]).split("%", maxsplit=1)[0]
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.casefold() == "localhost"
    if not is_loopback:
        raise RuntimeError(f"External network is disabled in test subprocesses: {host}")


def _guarded_connect(sock: socket.socket, address: Any) -> Any:
    _assert_loopback(address)
    return _original_connect(sock, address)


def _guarded_connect_ex(sock: socket.socket, address: Any) -> int:
    _assert_loopback(address)
    return _original_connect_ex(sock, address)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
