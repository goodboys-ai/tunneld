"""Minimal NDJSON-over-Unix-socket control channel."""

from __future__ import annotations

import json
import os
import socket
from typing import Any, Callable, Dict, Optional

from . import state


class IPCError(Exception):
    pass


def _recv_line(conn: socket.socket) -> Optional[str]:
    data = b""
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            return None if not data else data.decode("utf-8")
        data += chunk
        if b"\n" in data:
            line, _, _ = data.partition(b"\n")
            return line.decode("utf-8")


def send_request(op: str, **args: Any) -> Dict[str, Any]:
    sp = str(state.socket_path())
    if not os.path.exists(sp):
        raise IPCError("daemon not running (try 'tunneld up')")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    try:
        sock.connect(sp)
    except OSError as exc:
        sock.close()
        raise IPCError(f"cannot connect to daemon: {exc}")
    try:
        sock.sendall((json.dumps({"op": op, "args": args}) + "\n").encode("utf-8"))
        line = _recv_line(sock)
    finally:
        sock.close()
    if line is None:
        raise IPCError("daemon closed connection without responding")
    try:
        resp = json.loads(line)
    except json.JSONDecodeError:
        raise IPCError("malformed response from daemon")
    if not resp.get("ok"):
        raise IPCError(resp.get("error", "unknown error"))
    return resp.get("data", {})


def handle_connection(conn: socket.socket, handler: Callable[[str, Dict], Dict]) -> None:
    """Serve exactly one request/response on *conn*."""
    try:
        line = _recv_line(conn)
        if line is None:
            return
        req = json.loads(line)
        try:
            data = handler(req.get("op"), req.get("args", {}))
            resp: Dict[str, Any] = {"ok": True, "data": data}
        except Exception as exc:  # noqa: BLE001
            resp = {"ok": False, "error": str(exc)}
        conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
