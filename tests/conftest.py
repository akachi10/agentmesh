"""Shared fixtures — short socket paths to avoid AF_UNIX path length limit."""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def short_tmp(request):
    """Create a short temp directory suitable for Unix domain sockets.

    macOS has a ~104 char limit on AF_UNIX paths. pytest's tmp_path
    can exceed this, so we use /tmp directly.
    """
    d = tempfile.mkdtemp(prefix="ah_", dir="/tmp")
    yield d
    # Cleanup
    import shutil
    shutil.rmtree(d, ignore_errors=True)
