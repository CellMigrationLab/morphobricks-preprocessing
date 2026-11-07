"""Registers ``SkipIsFailure`` in a pytest run started with ``-p spec_gate_plugin``.

``SPEC_GATE_EXPECTED`` (comma-separated module names) sets the modules a run over the current
folder must collect (default: none), for the gate's own tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from spec_gate import SkipIsFailure


def pytest_configure(config):
    expected = [name for name in os.environ.get("SPEC_GATE_EXPECTED", "").split(",") if name]
    plugin = SkipIsFailure(expected, Path.cwd())
    config.pluginmanager.register(plugin, "spec-skip-is-failure")
