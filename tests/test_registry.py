"""Tests for registry module — concurrent read/write safety."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest

from agentmesh import registry


@pytest.fixture(autouse=True)
def tmp_registry(tmp_path):
    """Redirect registry to a temp directory for each test."""
    reg_dir = tmp_path / "agentmesh"
    reg_dir.mkdir()
    sock_dir = reg_dir / "sock"
    sock_dir.mkdir()

    with mock.patch.object(registry, "REGISTRY_DIR", reg_dir), \
         mock.patch.object(registry, "REGISTRY_FILE", reg_dir / "registry.json"), \
         mock.patch.object(registry, "SOCK_DIR", sock_dir):
        yield reg_dir


class TestRegister:
    def test_register_new_agent(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        agents = registry.list_all()
        assert len(agents) == 1
        assert agents[0]["name"] == "alice"
        assert agents[0]["id"] == "100"

    def test_register_multiple_agents(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        registry.register("bob", "101", "/tmp/sock/agent-101.sock")
        agents = registry.list_all()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"alice", "bob"}

    def test_register_replaces_same_id(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        registry.register("alice-v2", "100", "/tmp/sock/agent-100.sock")
        agents = registry.list_all()
        assert len(agents) == 1
        assert agents[0]["name"] == "alice-v2"

    def test_register_rejects_duplicate_name(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        with pytest.raises(ValueError, match="already taken"):
            registry.register("alice", "200", "/tmp/sock/agent-200.sock")


class TestUnregister:
    def test_unregister_existing(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        registry.unregister("100")
        assert registry.list_all() == []

    def test_unregister_nonexistent(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        registry.unregister("999")
        assert len(registry.list_all()) == 1

    def test_unregister_no_file(self):
        # Should not raise
        registry.unregister("100")


class TestFindByName:
    def test_found(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        result = registry.find_by_name("alice")
        assert result is not None
        assert result["id"] == "100"

    def test_not_found(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        assert registry.find_by_name("bob") is None


class TestFindById:
    def test_found(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        result = registry.find_by_id("100")
        assert result is not None
        assert result["name"] == "alice"

    def test_not_found(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        assert registry.find_by_id("999") is None


class TestIsNameTaken:
    def test_taken(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        assert registry.is_name_taken("alice") is True

    def test_not_taken(self):
        assert registry.is_name_taken("bob") is False

    def test_after_unregister(self):
        registry.register("alice", "100", "/tmp/sock/agent-100.sock")
        registry.unregister("100")
        assert registry.is_name_taken("alice") is False


class TestRemoveById:
    def test_removes_entry_and_sock_file(self, tmp_registry):
        sock_dir = tmp_registry / "sock"
        sock_path = str(sock_dir / "agent-100.sock")
        # Create a fake sock file
        Path(sock_path).touch()
        assert Path(sock_path).exists()

        registry.register("alice", "100", sock_path)
        registry.remove_by_id("100")

        # Both registry entry and sock file should be gone
        assert registry.find_by_id("100") is None
        assert not Path(sock_path).exists()

    def test_removes_entry_without_sock_file(self):
        registry.register("alice", "100", "/tmp/nonexistent.sock")
        registry.remove_by_id("100")
        assert registry.find_by_id("100") is None

    def test_remove_nonexistent_id(self):
        # Should not raise
        registry.remove_by_id("999")


class TestConcurrency:
    def test_concurrent_register(self):
        """Multiple threads registering simultaneously should not corrupt the file."""
        errors = []

        def register_agent(n):
            try:
                registry.register(f"agent-{n}", str(n), f"/tmp/sock/agent-{n}.sock")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_agent, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent registration: {errors}"
        agents = registry.list_all()
        assert len(agents) == 20

    def test_concurrent_register_unregister(self):
        """Concurrent register and unregister should not corrupt the file."""
        # Pre-register some agents
        for i in range(10):
            registry.register(f"agent-{i}", str(i), f"/tmp/sock/agent-{i}.sock")

        errors = []

        def unregister_agent(n):
            try:
                registry.unregister(str(n))
            except Exception as e:
                errors.append(e)

        def register_agent(n):
            try:
                registry.register(f"new-{n}", str(100 + n), f"/tmp/sock/agent-{100 + n}.sock")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=unregister_agent, args=(i,)))
            threads.append(threading.Thread(target=register_agent, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        agents = registry.list_all()
        # All original agents removed, 10 new ones added
        assert len(agents) == 10
        ids = {a["id"] for a in agents}
        for i in range(10):
            assert str(100 + i) in ids


class TestIsPidAlive:
    def test_alive_amesh_process(self):
        """A PID that exists and is an amesh process should return True."""
        with mock.patch("os.kill") as mock_kill, \
             mock.patch("subprocess.run") as mock_run:
            mock_kill.return_value = None
            mock_run.return_value = mock.Mock(stdout="python amesh --name foo")
            assert registry.is_pid_alive("123") is True

    def test_pid_does_not_exist(self):
        """A PID that doesn't exist should return False."""
        with mock.patch("os.kill", side_effect=OSError("No such process")):
            assert registry.is_pid_alive("99999") is False

    def test_pid_exists_but_not_amesh(self):
        """A PID that exists but is not amesh should return False."""
        with mock.patch("os.kill") as mock_kill, \
             mock.patch("subprocess.run") as mock_run:
            mock_kill.return_value = None
            mock_run.return_value = mock.Mock(stdout="/usr/bin/vim")
            assert registry.is_pid_alive("123") is False

    def test_invalid_pid(self):
        """Non-numeric PIDs should return False."""
        assert registry.is_pid_alive("abc") is False
        assert registry.is_pid_alive("") is False
        assert registry.is_pid_alive(None) is False

    def test_subprocess_timeout(self):
        """If ps times out, should return False."""
        import subprocess
        with mock.patch("os.kill") as mock_kill, \
             mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 2)):
            mock_kill.return_value = None
            assert registry.is_pid_alive("123") is False


class TestCleanupStaleSocks:
    def test_removes_orphan_sock_files(self, tmp_registry):
        sock_dir = tmp_registry / "sock"
        # Create orphan sock files
        (sock_dir / "agent-999.sock").touch()
        (sock_dir / "agent-888.sock").touch()

        registry.cleanup_stale_socks()

        assert not (sock_dir / "agent-999.sock").exists()
        assert not (sock_dir / "agent-888.sock").exists()

    def test_removes_entries_with_missing_sock(self, tmp_registry):
        # Register an agent but don't create its sock file
        registry.register("ghost", "777", str(tmp_registry / "sock" / "agent-777.sock"))

        registry.cleanup_stale_socks()

        agents = registry.list_all()
        assert len(agents) == 0

    def test_removes_entries_with_dead_pid(self, tmp_registry):
        """Entries with existing sock file but dead PID should be removed."""
        sock_dir = tmp_registry / "sock"
        sock_path = str(sock_dir / "agent-777.sock")
        Path(sock_path).touch()
        registry.register("ghost", "777", sock_path)

        with mock.patch.object(registry, "is_pid_alive", return_value=False):
            registry.cleanup_stale_socks()

        agents = registry.list_all()
        assert len(agents) == 0

    def test_keeps_valid_entries(self, tmp_registry):
        sock_dir = tmp_registry / "sock"
        sock_path = str(sock_dir / "agent-100.sock")
        # Register and create the sock file
        registry.register("alive", "100", sock_path)
        Path(sock_path).touch()

        with mock.patch.object(registry, "is_pid_alive", return_value=True):
            registry.cleanup_stale_socks()

        agents = registry.list_all()
        assert len(agents) == 1
        assert agents[0]["name"] == "alive"
