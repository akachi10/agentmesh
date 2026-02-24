"""PTY I/O loop — transparent stdin/stdout forwarding between human and Claude Code.

This module manages the raw terminal I/O:
- Human input (stdin) → Claude Code (master_fd)
- Claude Code output (master_fd) → human display (stdout)

Agent message injection is handled externally by tmux_injector — this module
only does transparent I/O forwarding.
"""

from __future__ import annotations

import logging
import os
import select
import sys
import termios
import tty
from collections.abc import Callable

logger = logging.getLogger(__name__)

SELECT_TIMEOUT = 1.0  # select loop poll interval


class PtyIO:
    """Transparent PTY I/O forwarder."""

    def __init__(self, master_fd: int):
        self.master_fd = master_fd
        self._running: bool = True

    def run(self) -> None:
        """Main I/O loop — forward stdin ↔ master_fd."""
        stdin_fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(stdin_fd)

        try:
            tty.setraw(stdin_fd)

            while self._running:
                read_fds = [stdin_fd, self.master_fd]

                try:
                    readable, _, _ = select.select(read_fds, [], [],
                                                   SELECT_TIMEOUT)
                except (ValueError, OSError):
                    break

                for fd in readable:
                    if fd == stdin_fd:
                        self._handle_stdin()
                    elif fd == self.master_fd:
                        self._handle_master()

        finally:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)

    def _handle_stdin(self) -> None:
        """Forward human input to Claude Code (master_fd)."""
        data = os.read(sys.stdin.fileno(), 1024)
        if not data:
            self._running = False
            return
        os.write(self.master_fd, data)

    def _handle_master(self) -> None:
        """Read Claude Code output and forward to stdout (human display)."""
        try:
            data = os.read(self.master_fd, 4096)
        except OSError:
            self._running = False
            return
        if not data:
            self._running = False
            return
        os.write(sys.stdout.fileno(), data)


def run_pty_loop(master_fd: int, child_pid: int,
                 setup_fn: Callable[[], None] | None = None) -> None:
    """Entry point called from pty_launcher after pty.fork().

    Args:
        master_fd: The master side of the PTY.
        child_pid: PID of the child (Claude Code) process.
        setup_fn: Optional callback to run before the I/O loop starts
                  (e.g. to start the SocketServer).
    """
    pty_io = PtyIO(master_fd)
    try:
        if setup_fn:
            setup_fn()
        pty_io.run()
    finally:
        try:
            os.waitpid(child_pid, 0)
        except ChildProcessError:
            pass
        os.close(master_fd)
