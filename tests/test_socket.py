"""Tests for socket server and client — message sending with callback."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentmesh.socket_client import send
from agentmesh.socket_server import SocketServer


@pytest.fixture
def sock_path(short_tmp):
    return os.path.join(short_tmp, "t.sock")


@pytest.fixture
def received_messages():
    """A list that collects messages from the callback."""
    return []


@pytest.fixture
def server(sock_path, received_messages):
    srv = SocketServer(sock_path, on_message=lambda msg: received_messages.append(msg))
    srv.start()
    yield srv
    srv.stop()


class TestSocketServerClient:
    def test_send_and_receive_message(self, server, sock_path, received_messages):
        msg = {
            "from": "sender",
            "from_id": "s1",
            "to": "test-agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "message",
            "content": "hello from sender",
        }

        send(sock_path, msg)
        time.sleep(0.1)

        assert len(received_messages) == 1
        assert received_messages[0]["content"] == "hello from sender"
        assert received_messages[0]["from"] == "sender"

    def test_multiple_messages(self, server, sock_path, received_messages):
        for i in range(5):
            msg = {
                "type": "message",
                "from": f"agent-{i}",
                "content": f"message {i}",
            }
            send(sock_path, msg)

        time.sleep(0.2)

        assert len(received_messages) == 5
        contents = [m["content"] for m in received_messages]
        for i in range(5):
            assert f"message {i}" in contents

    def test_server_stop_cleanup(self, sock_path):
        msgs = []
        srv = SocketServer(sock_path, on_message=lambda msg: msgs.append(msg))
        srv.start()
        assert Path(sock_path).exists()
        srv.stop()
        assert not Path(sock_path).exists()

    def test_unicode_message(self, server, sock_path, received_messages):
        msg = {
            "type": "message",
            "from": "架构师",
            "content": "数据库用 PostgreSQL，Schema 在 docs/database/schema.md",
        }
        send(sock_path, msg)
        time.sleep(0.1)

        assert len(received_messages) == 1
        assert received_messages[0]["from"] == "架构师"
        assert "PostgreSQL" in received_messages[0]["content"]
