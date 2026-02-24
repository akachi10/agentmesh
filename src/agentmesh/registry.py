"""Registry management — track active agents via a shared JSON file with file locking."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_DIR = Path.home() / "agentmesh"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"
SOCK_DIR = REGISTRY_DIR / "sock"


def _ensure_dirs() -> None:
    """Create runtime directories if they don't exist."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    SOCK_DIR.mkdir(parents=True, exist_ok=True)


def _read_locked(f) -> dict[str, Any]:
    """Read registry content from an already-opened, locked file."""
    f.seek(0)
    content = f.read()
    if not content:
        return {"agents": []}
    return json.loads(content)


def _write_locked(f, data: dict[str, Any]) -> None:
    """Write registry content to an already-opened, locked file."""
    f.seek(0)
    f.truncate()
    f.write(json.dumps(data, indent=2, ensure_ascii=False))
    f.flush()


def register(name: str, agent_id: str, sock_path: str,
             tmux_pane: str = "") -> None:
    """Register a new agent in the registry.

    Raises ValueError if the name is already taken by a different agent.
    """
    _ensure_dirs()
    REGISTRY_FILE.touch(exist_ok=True)
    with open(REGISTRY_FILE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = _read_locked(f)
            # Atomic name uniqueness check (prevents TOCTOU race)
            for a in data["agents"]:
                if a["name"] == name and a["id"] != agent_id:
                    raise ValueError(f"Name '{name}' is already taken by agent {a['id']}")
            # Remove any stale entry with same id
            data["agents"] = [a for a in data["agents"] if a["id"] != agent_id]
            data["agents"].append({
                "name": name,
                "id": agent_id,
                "sock": sock_path,
                "tmux_pane": tmux_pane,
                "registered_at": datetime.now(timezone.utc).isoformat(),
            })
            _write_locked(f, data)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def unregister(agent_id: str) -> None:
    """Remove an agent from the registry by its id."""
    if not REGISTRY_FILE.exists():
        return
    with open(REGISTRY_FILE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = _read_locked(f)
            data["agents"] = [a for a in data["agents"] if a["id"] != agent_id]
            _write_locked(f, data)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def list_all() -> list[dict[str, Any]]:
    """Return all registered agents."""
    if not REGISTRY_FILE.exists():
        return []
    with open(REGISTRY_FILE, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            data = _read_locked(f)
            return data.get("agents", [])
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def remove_by_id(agent_id: str) -> None:
    """Remove an agent entry and its sock file — used for cleaning up dead agents."""
    if not REGISTRY_FILE.exists():
        return
    with open(REGISTRY_FILE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = _read_locked(f)
            agent = None
            for a in data["agents"]:
                if a["id"] == agent_id:
                    agent = a
                    break
            if agent:
                sock_path = agent.get("sock")
                if sock_path and os.path.exists(sock_path):
                    os.unlink(sock_path)
                data["agents"] = [a for a in data["agents"] if a["id"] != agent_id]
                _write_locked(f, data)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def find_by_id(agent_id: str) -> dict[str, Any] | None:
    """Find an agent by id. Returns the first match or None."""
    for agent in list_all():
        if agent["id"] == agent_id:
            return agent
    return None


def find_by_name(name: str) -> dict[str, Any] | None:
    """Find an agent by name. Returns the first match or None."""
    for agent in list_all():
        if agent["name"] == name:
            return agent
    return None


def is_name_taken(name: str) -> bool:
    """Check whether an agent with the given name is already registered."""
    return find_by_name(name) is not None


def is_pid_alive(pid: str) -> bool:
    """Check whether a process is alive and is an amesh process.

    Uses os.kill(pid, 0) for existence check, then verifies the command line
    contains 'amesh' via ``ps`` (macOS-compatible).
    """
    try:
        int_pid = int(pid)
    except (ValueError, TypeError):
        return False

    # Check process exists
    try:
        os.kill(int_pid, 0)
    except OSError:
        return False

    # Verify it's an amesh process
    try:
        result = subprocess.run(
            ["ps", "-p", str(int_pid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        )
        return "amesh" in result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False


def cleanup_stale_socks() -> None:
    """Remove sock files that have no matching registry entry, and registry entries whose sock files are missing.

    Uses a two-phase approach to avoid holding the file lock while doing
    slow PID checks (which spawn subprocess).
    """
    _ensure_dirs()
    if not REGISTRY_FILE.exists():
        # No registry — just remove any leftover sock files
        for sock_file in SOCK_DIR.glob("agent-*.sock"):
            sock_file.unlink(missing_ok=True)
        return

    # Phase 1: Read registry (short shared lock)
    with open(REGISTRY_FILE, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            data = _read_locked(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    # PID checks outside of any lock — may take seconds
    dead_ids = set()
    for a in data["agents"]:
        if not os.path.exists(a["sock"]) or not is_pid_alive(a["id"]):
            dead_ids.add(a["id"])

    if not dead_ids:
        # Nothing to clean — just remove orphan sock files
        registered_socks = {a["sock"] for a in data["agents"]}
        for sock_file in SOCK_DIR.glob("agent-*.sock"):
            if str(sock_file) not in registered_socks:
                sock_file.unlink(missing_ok=True)
        return

    # Phase 2: Remove dead entries (short exclusive lock)
    # Re-read inside lock to preserve any new registrations that happened
    # between phase 1 and phase 2.
    with open(REGISTRY_FILE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = _read_locked(f)
            # Remove dead agent sock files
            for a in data["agents"]:
                if a["id"] in dead_ids:
                    sock_path = a.get("sock")
                    if sock_path and os.path.exists(sock_path):
                        os.unlink(sock_path)
            # Keep only agents NOT in dead_ids
            data["agents"] = [
                a for a in data["agents"]
                if a["id"] not in dead_ids
            ]
            _write_locked(f, data)
            registered_socks = {a["sock"] for a in data["agents"]}
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    # Remove orphan sock files
    for sock_file in SOCK_DIR.glob("agent-*.sock"):
        if str(sock_file) not in registered_socks:
            sock_file.unlink(missing_ok=True)
