"""Bounded NDJSON-over-Unix-socket control channel."""

from __future__ import annotations

import contextlib
import json
import os
import socket
from typing import Any, Callable, Optional

from . import state

IPC_PROTOCOL_VERSION = 1
IPC_TIMEOUT_SECONDS = 10.0
MAX_LINE_BYTES = 1024 * 1024


class IPCError(Exception):
    """Report daemon control-channel and protocol failures."""


def _decode_line(data: bytearray) -> str:
    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        raise IPCError("message is not valid UTF-8") from None


def _recv_line(conn: socket.socket) -> Optional[str]:
    data = bytearray()
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            return None if not data else _decode_line(data)
        newline = chunk.find(b"\n")
        if newline >= 0:
            data.extend(chunk[:newline])
            if len(data) > MAX_LINE_BYTES:
                raise IPCError("message too large")
            return _decode_line(data)
        data.extend(chunk)
        if len(data) > MAX_LINE_BYTES:
            raise IPCError("message too large")


def _encode_line(payload: dict[str, Any]) -> bytes:
    data = (json.dumps(payload) + "\n").encode("utf-8")
    if len(data) > MAX_LINE_BYTES:
        raise IPCError("message too large")
    return data


def _send_response(conn: socket.socket, payload: dict[str, Any]) -> None:
    try:
        data = _encode_line(payload)
    except IPCError:
        data = _encode_line({"ok": False, "error": "response too large"})
    conn.sendall(data)


def send_request(op: str, **args: Any) -> dict[str, Any]:
    """Send one bounded request to the daemon and return its data mapping."""
    request = _encode_line({"op": op, "args": args})
    sp = str(state.socket_path())
    if not os.path.exists(sp):
        raise IPCError("daemon not running (try 'tunneld up')")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(IPC_TIMEOUT_SECONDS)
    try:
        sock.connect(sp)
    except OSError as exc:
        sock.close()
        raise IPCError(f"cannot connect to daemon: {exc}") from None
    try:
        sock.sendall(request)
        line = _recv_line(sock)
    except IPCError:
        raise
    except OSError as exc:
        raise IPCError(f"daemon communication failed: {exc}") from None
    finally:
        sock.close()
    if line is None:
        raise IPCError("daemon closed connection without responding")
    try:
        response = json.loads(line)
    except json.JSONDecodeError:
        raise IPCError("malformed response from daemon") from None
    if not isinstance(response, dict):
        raise IPCError("malformed response from daemon")
    if not response.get("ok"):
        raise IPCError(str(response.get("error", "unknown error")))
    data = response.get("data", {})
    if not isinstance(data, dict):
        raise IPCError("malformed response from daemon")
    return data


def _parse_request(line: str) -> tuple[str, dict[str, Any]]:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        raise IPCError("malformed request") from None
    if not isinstance(request, dict):
        raise IPCError("request must be a JSON object")
    op = request.get("op")
    args = request.get("args", {})
    if not isinstance(op, str) or not op:
        raise IPCError("request op must be a non-empty string")
    if not isinstance(args, dict):
        raise IPCError("request args must be an object")
    return op, args


def handle_connection(
    conn: socket.socket, handler: Callable[[str, dict[str, Any]], dict[str, Any]]
) -> None:
    """Serve exactly one bounded request/response on *conn*."""
    try:
        try:
            line = _recv_line(conn)
            if line is None:
                return
            op, args = _parse_request(line)
            data = handler(op, args)
            response: dict[str, Any] = {"ok": True, "data": data}
        except Exception as exc:  # Return handler and protocol errors to the client.
            response = {"ok": False, "error": str(exc)}
        _send_response(conn, response)
    except (OSError, IPCError):
        pass
    finally:
        with contextlib.suppress(OSError):
            conn.close()
