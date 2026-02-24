"""Socket client — send messages to other agents via Unix Domain Socket."""

from __future__ import annotations

import json
import logging
import socket
import struct

logger = logging.getLogger(__name__)

TIMEOUT = 2  # seconds (local Unix sockets should respond fast)


def send(sock_path: str, message: dict) -> None:
    """Connect to a target agent's socket and send a length-prefixed message."""
    raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT)
    try:
        sock.connect(sock_path)
        sock.sendall(struct.pack(">I", len(raw)) + raw)
    finally:
        sock.close()
