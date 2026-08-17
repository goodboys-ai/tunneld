import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tunneld.ipc import (
    MAX_LINE_BYTES,
    IPCError,
    _recv_line,
    handle_connection,
)


class FakeConnection:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = bytearray()
        self.closed = False

    def recv(self, size):
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, data):
        self.sent.extend(data)

    def close(self):
        self.closed = True


def test_recv_line_rejects_oversized_messages():
    conn = FakeConnection([b"x" * (MAX_LINE_BYTES + 1)])
    with pytest.raises(IPCError, match="message too large"):
        _recv_line(conn)


def test_recv_line_rejects_invalid_utf8():
    conn = FakeConnection([b"\xff\n"])
    with pytest.raises(IPCError, match="not valid UTF-8"):
        _recv_line(conn)


def test_oversized_request_receives_bounded_error_response():
    conn = FakeConnection([b"x" * (MAX_LINE_BYTES + 1)])
    handle_connection(conn, lambda op, args: {})
    response = json.loads(bytes(conn.sent).decode("utf-8"))
    assert response == {"ok": False, "error": "message too large"}
    assert conn.closed


def test_malformed_json_receives_protocol_error():
    server, client = socket.socketpair()
    thread = threading.Thread(
        target=handle_connection, args=(server, lambda op, args: {})
    )
    thread.start()
    client.sendall(b"{not-json}\n")
    response = json.loads(_recv_line(client))
    thread.join(timeout=2)
    client.close()
    assert response == {"ok": False, "error": "malformed request"}


def _round_trip(index):
    server, client = socket.socketpair()
    thread = threading.Thread(
        target=handle_connection,
        args=(server, lambda op, args: {"op": op, "index": args["index"]}),
    )
    thread.start()
    request = json.dumps({"op": "echo", "args": {"index": index}}) + "\n"
    client.sendall(request.encode("utf-8"))
    response = json.loads(_recv_line(client))
    thread.join(timeout=2)
    client.close()
    return response


def test_concurrent_ipc_connections_remain_independent():
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(_round_trip, range(32)))
    assert [response["data"]["index"] for response in responses] == list(range(32))
    assert all(response["ok"] for response in responses)
