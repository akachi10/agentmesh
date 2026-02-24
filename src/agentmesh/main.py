"""Entry point — start an AgentMesh instance.

Handles tmux detection/auto-creation, name input, custom prompt input,
then delegates to pty_launcher.
"""

from __future__ import annotations

import atexit
import os
import re
import signal
import shutil
import subprocess
import sys

from agentmesh import registry
from agentmesh.pty_launcher import launch

# Regex to strip ANSI escape sequences (CSI, OSC, etc.)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]")


def _flush_stdin() -> None:
    """Discard any pending bytes in stdin (e.g. terminal DA responses).

    tmux sends Device Attributes queries when creating a session, and the
    terminal response (ESC[?6c etc.) may linger in the stdin buffer.
    This drains them before we call input().
    """
    import select
    import time
    time.sleep(0.1)  # Brief wait for any pending terminal responses
    try:
        while select.select([sys.stdin], [], [], 0)[0]:
            os.read(sys.stdin.fileno(), 4096)
    except (OSError, ValueError):
        pass


def _check_tmux() -> None:
    """Verify tmux is installed and >= 3.3."""
    if not shutil.which("tmux"):
        print("Error: tmux not found. AgentMesh requires tmux >= 3.3.")
        print("Install tmux and try again.")
        sys.exit(1)

    try:
        result = subprocess.run(
            ["tmux", "-V"], capture_output=True, text=True, timeout=5,
        )
        # Output like "tmux 3.4" or "tmux 3.3a"
        version_str = result.stdout.strip().replace("tmux ", "")
        # Extract major.minor (ignore patch letters)
        parts = version_str.split(".")
        major = int(parts[0])
        # Minor may have trailing letters like "3a"
        minor_str = ""
        for ch in parts[1] if len(parts) > 1 else "0":
            if ch.isdigit():
                minor_str += ch
            else:
                break
        minor = int(minor_str) if minor_str else 0

        if major < 3 or (major == 3 and minor < 3):
            print(f"Error: tmux >= 3.3 required (found {version_str}).")
            sys.exit(1)
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print(f"Error: cannot determine tmux version: {exc}")
        sys.exit(1)


def _kill_session(session_name: str) -> None:
    """Kill a tmux session by name. Silent if already dead."""
    subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        capture_output=True,
    )


def _ensure_tmux() -> None:
    """Ensure we are running inside a tmux session.

    If not, create a tmux session and register signal handlers (SIGHUP,
    SIGTERM, SIGINT) + atexit to kill the session on any form of exit.

    - Terminal window closed → SIGHUP → kill session → all children die
    - User Ctrl+C → SIGINT → kill session
    - Process killed → SIGTERM → kill session
    - Normal exit / detach → atexit → kill session
    """
    if os.environ.get("TMUX_PANE"):
        return  # Already in tmux

    pid = os.getpid()
    session_name = f"amesh-{pid}"

    # Build the command to re-run ourselves inside tmux
    argv = sys.argv[:]
    if argv[0].endswith("amesh"):
        reexec_cmd = " ".join(argv)
    else:
        reexec_cmd = f"{sys.executable} -m agentmesh.main"

    # Register cleanup for ALL exit paths
    def cleanup(*_args):
        _kill_session(session_name)

    atexit.register(cleanup)
    signal.signal(signal.SIGHUP, lambda *_: (cleanup(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (cleanup(), sys.exit(0)))

    # Create and attach (foreground, blocks)
    try:
        subprocess.run([
            "tmux", "new-session",
            "-s", session_name,
            reexec_cmd,
        ])
    except KeyboardInterrupt:
        pass

    # Also cleanup on normal return (detach, session end)
    cleanup()
    sys.exit(0)


def main() -> None:
    # Check tmux prerequisite
    _check_tmux()

    # Ensure we're inside tmux (auto-create if needed)
    _ensure_tmux()

    agent_id = str(os.getpid())

    # Clean up stale socks from previous runs
    registry.cleanup_stale_socks()

    # Flush any stale terminal responses (e.g. Device Attributes) from stdin
    _flush_stdin()

    print("=== AgentMesh ===")
    print()

    # Prompt for agent name with validation
    while True:
        raw_name = input("Enter agent name (e.g. 架构师, 开发者): ")
        agent_name = _ANSI_ESCAPE_RE.sub("", raw_name).strip()
        if not agent_name:
            print("Error: agent name cannot be empty.")
            continue
        if registry.is_name_taken(agent_name):
            print(f"Error: name '{agent_name}' is already taken. Choose another.")
            continue
        break

    # Prompt for custom instructions (optional)
    print()
    raw_prompt = input(
        "Custom instructions (e.g. 你是系统架构师，负责技术选型。Press Enter to skip): "
    )
    custom_prompt = _ANSI_ESCAPE_RE.sub("", raw_prompt).strip() or None

    # Launch
    launch(agent_name, agent_id, custom_prompt=custom_prompt)


if __name__ == "__main__":
    main()
