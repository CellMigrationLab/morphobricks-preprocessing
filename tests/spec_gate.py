"""A skipped Spec conformance test is a failure (conformance plan section 1).

Every test of a module named ``test_spec_*.py`` is marked ``spec_conformance``. When such a
test is skipped (``pytest.skip``, ``skipif``, ``importorskip``, a skipping fixture) or
expected to fail (``xfail``), the report is turned into a failure, so a missing package,
file or weight cannot make the release gate pass silently.
"""

from __future__ import annotations

import pytest

MARKER = "spec_conformance"
MODULE_PREFIX = "test_spec_"


class SkipIsFailure:
    """A pytest plugin: marks the Spec tests and fails the ones that did not run."""

    def pytest_configure(self, config: pytest.Config) -> None:
        """Register the marker."""
        config.addinivalue_line("markers", f"{MARKER}: a Spec test that must never skip")

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        """Mark every test of a ``test_spec_*.py`` module."""
        for item in items:
            if item.path.name.startswith(MODULE_PREFIX):
                item.add_marker(MARKER)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo):
        """Turn a skipped or xfailed report of a marked test into a failure."""
        outcome = yield
        report = outcome.get_result()
        if item.get_closest_marker(MARKER) is None or not report.skipped:
            return
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        if hasattr(report, "wasxfail"):
            reason = f"expected to fail: {report.wasxfail}"
        report.outcome = "failed"
        report.longrepr = f"a Spec conformance test must run, it was skipped: {reason}"
