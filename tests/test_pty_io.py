"""Tests for PTY I/O — immediate message injection, self-pipe, reinjection."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from agentmesh.pty_io import PtyIO, REINJECT_INTERVAL


@pytest.fixture
def pty_io():
    """Create a PtyIO instance with a dummy master_fd (not connected to real PTY)."""
    r, w = os.pipe()
    io = PtyIO(master_fd=w, system_prompt="You are test-agent (id=42).")
    yield io
    os.close(r)
    os.close(w)
    try:
        os.close(io._pipe_r)
    except OSError:
        pass
    try:
        os.close(io._pipe_w)
    except OSError:
        pass


class TestInitialState:
    def test_defaults(self, pty_io):
        assert pty_io.message_counter == 0
        assert pty_io._running is True


class TestPrintAndInject:
    def test_message_format(self, pty_io):
        """Message should include sender name and PID."""
        msg = {"from": "alice", "from_id": "999", "content": "hello"}

        with mock.patch("os.write") as mock_write:
            pty_io._print_and_inject(msg)

        calls = mock_write.call_args_list
        written = b"".join(c[0][1] for c in calls if c[0][0] == pty_io.master_fd)
        text = written.decode("utf-8")
        assert "from alice (pid=999)" in text
        assert "hello" in text

    def test_increments_counter(self, pty_io):
        msg = {"from": "bob", "from_id": "1", "content": "hi"}
        with mock.patch("os.write"):
            pty_io._print_and_inject(msg)
        assert pty_io.message_counter == 1


class TestMessageCounter:
    def test_counter_increments(self, pty_io):
        with mock.patch("os.write"):
            pty_io._increment_counter()
        assert pty_io.message_counter == 1

    def test_reinject_at_interval(self, pty_io):
        """System prompt should be reinjected every REINJECT_INTERVAL messages."""
        pty_io.message_counter = REINJECT_INTERVAL - 1
        with mock.patch("os.write") as mock_write:
            pty_io._increment_counter()

        assert pty_io.message_counter == REINJECT_INTERVAL
        calls = mock_write.call_args_list
        written = b"".join(c[0][1] for c in calls if c[0][0] == pty_io.master_fd)
        text = written.decode("utf-8")
        assert "You are test-agent" in text
        assert "压缩恢复" in text

    def test_no_reinject_before_interval(self, pty_io):
        pty_io.message_counter = REINJECT_INTERVAL - 2
        with mock.patch("os.write"):
            pty_io._increment_counter()
        assert pty_io.message_counter == REINJECT_INTERVAL - 1

    def test_no_reinject_without_prompt(self):
        r, w = os.pipe()
        io = PtyIO(master_fd=w, system_prompt="")
        io.message_counter = REINJECT_INTERVAL - 1
        with mock.patch("os.write") as mock_write:
            io._increment_counter()
        for call in mock_write.call_args_list:
            assert call[0][0] != w, "Should not write to master_fd without system_prompt"
        os.close(r)
        os.close(w)
        os.close(io._pipe_r)
        os.close(io._pipe_w)


class TestSelfPipe:
    def test_on_message_from_socket_queues_and_signals(self, pty_io):
        msg = {"from": "alice", "from_id": "100", "content": "hi"}
        pty_io.on_message_from_socket(msg)

        assert not pty_io._message_queue.empty()
        queued = pty_io._message_queue.get_nowait()
        assert queued["from"] == "alice"

    def test_drain_incoming_injects_immediately(self, pty_io):
        msg1 = {"from": "a", "from_id": "1", "content": "msg1"}
        msg2 = {"from": "b", "from_id": "2", "content": "msg2"}

        pty_io._message_queue.put(msg1)
        pty_io._message_queue.put(msg2)
        os.write(pty_io._pipe_w, b"\x00\x00")

        with mock.patch("os.write") as mock_write:
            pty_io._drain_incoming()

        assert pty_io._message_queue.empty()
        assert pty_io.message_counter == 2
        mock_write.assert_called()
