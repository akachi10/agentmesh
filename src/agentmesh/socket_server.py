"""Socket server — listen on a Unix Domain Socket for incoming messages.

Supports two connection types, distinguished by the first message received:

1. **Write connections** (short-lived): First message has ``type`` != "subscribe"
   (or no ``type`` field). It's a regular agent message. Read, route, close.

2. **Subscribe connections** (long-lived): First message is
   ``{"type": "subscribe", "msg_id": "<uuid>"}``.
   The connection stays open. When a reply matching ``msg_id`` arrives,
   the server writes it back on this connection, then both sides close.

Message routing for regular messages:
- Has matching subscriber for response_id → forward to subscriber
- response_id is null → new message → call on_new_message callback (tmux inject)
- response_id set but no subscriber → discard with warning log
"""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_MESSAGE_SIZE = 1 * 1024 * 1024  # 1 MB
HEADER_SIZE = 4  # 4-byte big-endian length prefix


def recv_exact(conn: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *conn*."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed before receiving all data")
        buf.extend(chunk)
    return bytes(buf)


def read_message(conn: socket.socket) -> dict | None:
    """Read one length-prefixed JSON message from *conn*. Returns None on error."""
    try:
        header = recv_exact(conn, HEADER_SIZE)
    except OSError:
        return None

    length = struct.unpack(">I", header)[0]
    if length > MAX_MESSAGE_SIZE:
        logger.warning("Message too large (%d bytes), dropping", length)
        return None

    try:
        raw = recv_exact(conn, length)
    except OSError as exc:
        logger.error("Failed to read message payload: %s", exc)
        return None

    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error("Failed to decode message: %s", exc)
        return None


def send_message_on_conn(conn: socket.socket, msg: dict) -> bool:
    """Send one length-prefixed JSON message on *conn*. Returns success."""
    try:
        raw = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        conn.sendall(struct.pack(">I", len(raw)) + raw)
        return True
    except OSError as exc:
        logger.error("Failed to send message on connection: %s", exc)
        return False


class SocketServer:
    """Listens on a Unix Domain Socket, routes messages to subscribers or callback.

    Args:
        sock_path: Path for the Unix Domain Socket file.
        on_new_message: Callback for new messages (response_id is null).
                        Called with the full message dict.
    """

    def __init__(self, sock_path: str,
                 on_new_message: Callable[[dict], None]):
        self.sock_path = sock_path
        self.on_new_message = on_new_message

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

        # Subscribers: msg_id → socket connection (kept open)
        # MCP connects, sends subscribe request, connection stays open.
        # When a matching reply arrives, the reply is forwarded on this conn.
        self._subscribers: dict[str, socket.socket] = {}
        self._sub_lock = threading.Lock()

    def start(self) -> None:
        """Create the socket and start the listener thread."""
        Path(self.sock_path).parent.mkdir(parents=True, exist_ok=True)

        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.sock_path)
        self._sock.listen(32)
        self._sock.settimeout(1.0)

        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Socket server listening on %s", self.sock_path)

    def stop(self) -> None:
        """Stop the listener and clean up."""
        self._running = False

        # Close all subscriber connections to unblock them
        with self._sub_lock:
            for msg_id, conn in self._subscribers.items():
                try:
                    conn.close()
                except OSError:
                    pass
            self._subscribers.clear()

        if self._sock:
            self._sock.close()
            self._sock = None
        if self._thread:
            self._thread.join(timeout=3)
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        logger.info("Socket server stopped: %s", self.sock_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _listen_loop(self) -> None:
        """Accept connections and dispatch to handler threads."""
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            t = threading.Thread(target=self._handle_connection, args=(conn,),
                                 daemon=True)
            t.start()

    def _handle_connection(self, conn: socket.socket) -> None:
        """Read the first message and decide: subscribe or write."""
        conn.settimeout(5.0)
        try:
            msg = read_message(conn)
            if msg is None:
                conn.close()
                return

            if msg.get("type") == "subscribe":
                # Subscribe connection — keep open
                self._handle_subscribe(conn, msg)
            else:
                # Write connection — route and close
                try:
                    self._route_message(msg)
                finally:
                    conn.close()

        except Exception as exc:
            logger.error("Error handling connection: %s", exc)
            try:
                conn.close()
            except OSError:
                pass

    def _handle_subscribe(self, conn: socket.socket, msg: dict) -> None:
        """Handle a subscribe request — keep connection open for reply delivery.

        The connection stays open indefinitely. It will be closed when:
        - A matching reply arrives and is forwarded
        - The server stops
        - The MCP disconnects (detected when we try to write)
        """
        msg_id = msg.get("msg_id")
        if not msg_id:
            logger.warning("Subscribe request without msg_id, dropping")
            conn.close()
            return

        # Clear the 5-second timeout set during initial read —
        # subscribe connections may stay open for a long time.
        conn.settimeout(None)

        with self._sub_lock:
            self._subscribers[msg_id] = conn
        logger.debug("Subscriber registered for msg_id=%s", msg_id)
        # Do NOT close conn — it stays open for reply delivery

    def _route_message(self, msg: dict) -> None:
        """Route a regular message to subscriber or callback."""
        response_id = msg.get("response_id")
        sender = msg.get("from", "unknown")

        if response_id:
            # This is a reply — try to deliver to a waiting subscriber
            with self._sub_lock:
                sub_conn = self._subscribers.pop(response_id, None)

            if sub_conn:
                send_message_on_conn(sub_conn, msg)
                try:
                    sub_conn.close()
                except OSError:
                    pass
                logger.debug("Reply (response_id=%s) delivered to subscriber",
                             response_id)
            else:
                logger.warning(
                    "Reply from %s with response_id=%s has no waiting "
                    "subscriber — discarded", sender, response_id,
                )
        else:
            # New message — deliver via callback (tmux injection)
            logger.debug("New message from %s, delivering via callback",
                         sender)
            self.on_new_message(msg)
