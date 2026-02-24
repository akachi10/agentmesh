"""Tests for MCP server tools — list_agents and send_message."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest import mock

import pytest

from agentmesh import registry
from agentmesh import mcp_server
from agentmesh.socket_server import SocketServer


@pytest.fixture(autouse=True)
def tmp_registry(short_tmp):
    """Redirect registry and sock dir to a short temp directory."""
    reg_dir = Path(short_tmp)
    sock_dir = reg_dir / "s"
    sock_dir.mkdir()

    with mock.patch.object(registry, "REGISTRY_DIR", reg_dir), \
         mock.patch.object(registry, "REGISTRY_FILE", reg_dir / "r.json"), \
         mock.patch.object(registry, "SOCK_DIR", sock_dir):
        yield reg_dir


@pytest.fixture
def agent_a_messages():
    """Collect messages received by agent A."""
    return []


@pytest.fixture
def agent_b_messages():
    """Collect messages received by agent B."""
    return []


@pytest.fixture
def agent_a(tmp_registry, agent_a_messages):
    """Set up agent A with its own socket server and MCP tools configured."""
    sock_dir = tmp_registry / "s"
    sock_path = str(sock_dir / "a.sock")

    srv = SocketServer(sock_path, on_message=lambda msg: agent_a_messages.append(msg))
    srv.start()
    registry.register("alice", "100", sock_path)

    mcp_server._agent_id = "100"
    mcp_server._agent_name = "alice"

    yield {"name": "alice", "id": "100", "sock_path": sock_path, "server": srv}

    srv.stop()


@pytest.fixture
def agent_b(tmp_registry, agent_b_messages):
    """Set up agent B with its own socket server."""
    sock_dir = tmp_registry / "s"
    sock_path = str(sock_dir / "b.sock")

    srv = SocketServer(sock_path, on_message=lambda msg: agent_b_messages.append(msg))
    srv.start()
    registry.register("bob", "101", sock_path)

    yield {"name": "bob", "id": "101", "sock_path": sock_path, "server": srv}

    srv.stop()


class TestListAgents:
    def test_list_self_only(self, agent_a):
        with mock.patch.object(registry, "is_pid_alive", return_value=True):
            result = mcp_server.list_agents()
        assert "alice" in result
        assert "[self]" in result

    def test_list_with_other_agent(self, agent_a, agent_b):
        with mock.patch.object(registry, "is_pid_alive", return_value=True):
            result = mcp_server.list_agents()
        assert "alice" in result
        assert "bob" in result
        assert "[self]" in result

    def test_list_cleans_dead_agents(self, agent_a, tmp_registry):
        registry.register("ghost", "999", str(tmp_registry / "s" / "g.sock"))

        with mock.patch.object(registry, "is_pid_alive", return_value=False):
            result = mcp_server.list_agents()
        # Ghost should be cleaned up (pid dead), alice should remain since it's [self]
        assert "ghost" not in result
        assert registry.find_by_name("ghost") is None

    def test_list_no_agents(self):
        mcp_server._agent_id = "999"
        mcp_server._agent_name = "nobody"

        result = mcp_server.list_agents()
        assert "No agents" in result


class TestSendMessage:
    def test_send_to_existing_agent(self, agent_a, agent_b, agent_b_messages):
        result = mcp_server.send_message("bob", "hello bob")
        assert "sent" in result.lower()

        time.sleep(0.1)
        assert len(agent_b_messages) == 1
        assert agent_b_messages[0]["content"] == "hello bob"
        assert agent_b_messages[0]["from"] == "alice"

    def test_send_to_nonexistent_agent(self, agent_a):
        result = mcp_server.send_message("nobody", "hello?")
        assert "not found" in result.lower() or "error" in result.lower()

    def test_send_to_self(self, agent_a):
        result = mcp_server.send_message("alice", "talking to myself")
        assert "yourself" in result.lower() or "error" in result.lower()

    def test_send_to_dead_agent(self, agent_a, tmp_registry):
        fake_sock = str(tmp_registry / "s" / "d.sock")
        registry.register("dead", "888", fake_sock)

        result = mcp_server.send_message("dead", "hello?")
        assert "unreachable" in result.lower() or "error" in result.lower()
        assert registry.find_by_name("dead") is None
