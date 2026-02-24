"""tmux message injection — inject text into a tmux pane via bracketed paste + Enter.

Uses tmux set-buffer + paste-buffer -p + send-keys Enter to generate real
keyboard events that Ink (Claude Code's TUI framework) can recognise.

Thread-safe: a global lock + unique buffer names prevent concurrent injections
from corrupting each other.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import uuid

logger = logging.getLogger(__name__)

# Global lock — only one injection at a time per process
_inject_lock = threading.Lock()

# Counter for system prompt reinjection
_inject_counter = 0
_counter_lock = threading.Lock()

REINJECT_INTERVAL = 20


def inject_message(tmux_pane: str, text: str) -> None:
    """Inject *text* into the given tmux pane and press Enter.

    Steps:
    1. Load text into a uniquely named tmux paste buffer
    2. Paste with bracketed-paste mode (-p) so Ink treats it as pasted content
    3. Short sleep for Ink to process
    4. Send Enter key to submit
    5. Delete the temporary buffer

    The entire sequence is serialised by ``_inject_lock``.
    """
    buf_name = f"agent-msg-{uuid.uuid4().hex[:8]}"

    with _inject_lock:
        try:
            subprocess.run(
                ["tmux", "set-buffer", "-b", buf_name, "--", text],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-p", "-b", buf_name, "-t", tmux_pane],
                check=True, capture_output=True,
            )
            time.sleep(0.3)
            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_pane, "Enter"],
                check=True, capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error("tmux injection failed: %s", exc.stderr)
            raise
        finally:
            # Always try to clean up the buffer
            subprocess.run(
                ["tmux", "delete-buffer", "-b", buf_name],
                capture_output=True,
            )


def inject_agent_message(tmux_pane: str, msg: dict,
                         system_prompt: str = "") -> None:
    """Format an agent message and inject it, with periodic prompt reinjection.

    Args:
        tmux_pane: Target tmux pane identifier (e.g. "%5").
        msg: Message dict with keys: from, from_id, msg_id, content.
        system_prompt: Full system prompt text for periodic reinjection.
    """
    global _inject_counter

    sender = msg.get("from", "unknown")
    sender_pid = msg.get("from_id", "?")
    msg_id = msg.get("msg_id", "?")
    content = msg.get("content", "")

    inject_text = (
        f"[Agent Message] from {sender} (pid={sender_pid}, "
        f"msg_id={msg_id}): {content}"
    )
    inject_message(tmux_pane, inject_text)

    # Increment counter and reinject system prompt if needed
    with _counter_lock:
        _inject_counter += 1
        if system_prompt and _inject_counter % REINJECT_INTERVAL == 0:
            _reinject_system_prompt(tmux_pane, system_prompt)


def _reinject_system_prompt(tmux_pane: str, system_prompt: str) -> None:
    """Reinject the system prompt via tmux to prevent context dilution."""
    text = (
        f"[提示词重注入 — 这是压缩恢复时重复的内容，无需回复确认] "
        f"{system_prompt}"
    )
    inject_message(tmux_pane, text)
