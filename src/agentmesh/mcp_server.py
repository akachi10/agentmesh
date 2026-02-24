"""MCP Server — expose agent communication tools to Claude Code.

Can be run as: amesh-mcp --agent-id ID --agent-name NAME

Provides list_agents and send_message tools. send_message is synchronous when
sending new messages (blocks until reply) and fire-and-forget when sending
replies (has response_id).

The MCP server is a separate process launched by Claude Code. It communicates
with the client's SocketServer via the same Unix Domain Socket.
"""

from __future__ import annotations

import argparse
import json
import logging
import select
import socket
import struct
import uuid
from datetime import datetime, timezone

from mcp.server import FastMCP

from agentmesh import registry
from agentmesh.registry import SOCK_DIR

logger = logging.getLogger(__name__)

# Module-level state — set before tools are called
_agent_id: str = ""
_agent_name: str = ""

# Liveness check interval (seconds)
_LIVENESS_POLL_INTERVAL = 3.0

server = FastMCP(name="amesh")


def _send_to_sock(sock_path: str, msg: dict) -> None:
    """Send a length-prefixed JSON message to a Unix Domain Socket (short connection)."""
    raw = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(sock_path)
        sock.sendall(struct.pack(">I", len(raw)) + raw)
    finally:
        sock.close()


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *conn*. Raises OSError on failure."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed before receiving all data")
        buf.extend(chunk)
    return bytes(buf)


def _read_message_blocking(conn: socket.socket) -> dict | None:
    """Read one length-prefixed JSON message from conn (blocking, no timeout).

    Only call this when data is known to be available (e.g. after select).
    """
    try:
        header = _recv_exact(conn, 4)
    except OSError:
        return None

    length = struct.unpack(">I", header)[0]
    if length > 1 * 1024 * 1024:
        return None

    try:
        raw = _recv_exact(conn, length)
    except OSError:
        return None

    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _check_target_alive(target_name: str, target_id: str) -> bool:
    """Check whether the target agent is still alive and has the same name."""
    agent = registry.find_by_id(target_id)
    if agent is None:
        return False
    if agent["name"] != target_name:
        return False
    return registry.is_pid_alive(target_id)


def _subscribe(msg_id: str) -> socket.socket | str:
    """Connect to own SocketServer and register as subscriber for msg_id.

    Returns the connected socket on success, or an error string on failure.
    The caller should use _wait_for_reply() to block on this socket.
    """
    own_sock_path = str(SOCK_DIR / f"agent-{_agent_id}.sock")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(own_sock_path)
    except OSError as exc:
        return f"Error: cannot connect to own socket for reply subscription: {exc}"

    # Send subscribe request (length-prefixed)
    sub_request = {"type": "subscribe", "msg_id": msg_id}
    raw = json.dumps(sub_request, ensure_ascii=False).encode("utf-8")
    try:
        sock.sendall(struct.pack(">I", len(raw)) + raw)
    except OSError as exc:
        sock.close()
        return f"Error: failed to send subscription request: {exc}"

    # Set to blocking for the wait phase (select handles timeouts)
    sock.settimeout(None)
    return sock


def _wait_for_reply(sock: socket.socket, target_name: str,
                    target_id: str) -> dict | str:
    """Block on a subscribe socket until a reply arrives or target dies.

    Uses select() for timeout so we never do partial reads on timeout.
    Returns the reply message dict, or an error string on failure.
    """
    try:
        while True:
            # Wait for data with periodic liveness checks
            readable, _, _ = select.select(
                [sock], [], [], _LIVENESS_POLL_INTERVAL,
            )

            if readable:
                # Data available — read the complete message (blocking is fine,
                # SocketServer sends the full message atomically)
                reply = _read_message_blocking(sock)
                if reply is not None:
                    return reply
                # Connection closed without a valid message
                return "Error: subscription connection closed without reply"
            else:
                # Timeout — check target liveness
                if not _check_target_alive(target_name, target_id):
                    return f"Error: agent '{target_name}' 断链，目标已下线"
                # Target still alive — continue waiting
    finally:
        sock.close()


@server.tool()
def list_agents() -> str:
    """List all currently available agents. Uses PID check to verify liveness."""
    agents = registry.list_all()
    available = []
    dead_ids = []

    for agent in agents:
        if agent["id"] == _agent_id:
            available.append(f"- {agent['name']} (id={agent['id']}) [self]")
        elif registry.is_pid_alive(agent["id"]):
            available.append(f"- {agent['name']} (id={agent['id']})")
        else:
            dead_ids.append(agent["id"])

    for dead_id in dead_ids:
        registry.remove_by_id(dead_id)

    if not available:
        return "No agents currently available."

    return "Available agents:\n" + "\n".join(available)


@server.tool()
def send_message(to_name: str, content: str,
                 response_id: str | None = None) -> str:
    """Send a message to another agent by name.

    When response_id is not provided (new message): blocks until the target
    agent replies. The reply content is returned directly.

    When response_id is provided (replying to a message): sends immediately
    and returns without waiting.

    Args:
        to_name: The name of the target agent.
        content: The message content to send.
        response_id: If replying, the msg_id of the message being replied to.
    """
    target = registry.find_by_name(to_name)
    if not target:
        return f"Error: agent '{to_name}' not found or offline."

    if target["id"] == _agent_id:
        return "Error: cannot send message to yourself."

    # Check target is alive
    if not registry.is_pid_alive(target["id"]):
        registry.remove_by_id(target["id"])
        return f"Error: agent '{to_name}' is unreachable. Removed from registry."

    msg_id = str(uuid.uuid4())

    msg = {
        "msg_id": msg_id,
        "response_id": response_id,
        "from": _agent_name,
        "from_id": _agent_id,
        "to": to_name,
        "to_id": target["id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content": content,
    }

    if response_id:
        # Sending a reply — fire and forget
        try:
            _send_to_sock(target["sock"], msg)
            return f"Reply sent to {to_name}."
        except OSError:
            return f"Error: agent '{to_name}' is unreachable (failed to send reply)."
    else:
        # Sending a new message — synchronous, wait for reply
        # CRITICAL: subscribe BEFORE sending, to avoid race condition where
        # the target replies before we start listening.
        sub_result = _subscribe(msg_id)
        if isinstance(sub_result, str):
            return sub_result  # Error string

        sub_sock = sub_result

        # Now send the message to target
        try:
            _send_to_sock(target["sock"], msg)
        except OSError:
            sub_sock.close()
            registry.remove_by_id(target["id"])
            return f"Error: agent '{to_name}' is unreachable. Removed from registry."

        # Wait for reply on the subscribe socket
        result = _wait_for_reply(sub_sock, to_name, target["id"])

        if isinstance(result, str):
            return result  # Error string
        else:
            reply_content = result.get("content", "")
            reply_from = result.get("from", to_name)
            return f"[Reply from {reply_from}]: {reply_content}"


def main() -> None:
    """Entry point when run as a subprocess by Claude Code."""
    global _agent_id, _agent_name

    parser = argparse.ArgumentParser(description="AgentMesh MCP Server")
    parser.add_argument("--agent-id", required=True,
                        help="Unique agent identifier")
    parser.add_argument("--agent-name", required=True,
                        help="Human-readable agent name")
    args = parser.parse_args()

    _agent_id = args.agent_id
    _agent_name = args.agent_name

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
