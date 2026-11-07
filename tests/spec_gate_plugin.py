"""Registers ``SkipIsFailure`` in a pytest run started with ``-p spec_gate_plugin``."""

from __future__ import annotations

from spec_gate import SkipIsFailure


def pytest_configure(config):
    config.pluginmanager.register(SkipIsFailure(), "spec-skip-is-failure")
