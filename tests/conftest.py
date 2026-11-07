import sys
from pathlib import Path

# Lets tests import the shared simulation helpers.
sys.path.insert(0, str(Path(__file__).parent))

from spec_gate import SkipIsFailure  # noqa: E402


def pytest_configure(config):
    # A skipped Spec conformance test is a failure (conformance plan section 1).
    config.pluginmanager.register(SkipIsFailure(), "spec-skip-is-failure")
