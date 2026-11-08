"""A skipped Spec conformance test is a failure (conformance plan section 1).

Every test of a module named ``test_spec_*.py`` is marked ``spec_conformance``. When such a
test is skipped (``pytest.skip``, ``skipif``, ``importorskip``, a skipping fixture) or
expected to fail (``xfail``), the report is turned into a failure, so a missing package,
file or weight cannot make the release gate pass silently.

Two cases never reach a test item, so two more checks hold them (issue #203):

- a ``test_spec_*.py`` module skipped while it is collected (``pytest.skip(...,
  allow_module_level=True)``, a module-level ``importorskip``) is reported as a failed
  collection (:meth:`SkipIsFailure.pytest_collectreport`);
- a deleted or emptied module: a run over the whole tests folder must have collected at
  least one item from every module of :data:`EXPECTED_MODULES`, else it is refused
  (:meth:`SkipIsFailure.pytest_collection_finish`). Adding a Spec module means listing it
  there; removing one is a reviewed change to this list.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from pathlib import Path

import pytest

MARKER = "spec_conformance"
MODULE_PREFIX = "test_spec_"
EXPECTED_MODULES = (
    "test_spec_catalogue.py",
    "test_spec_derived_design.py",
    "test_spec_discovery.py",
    "test_spec_encoding.py",
    "test_spec_gate.py",
    "test_spec_no_fallback.py",
    "test_spec_pin.py",
    "test_spec_publication.py",
    "test_spec_reader.py",
    "test_spec_reader_phases_1_3.py",
    "test_spec_reconcile.py",
    "test_spec_records.py",
    "test_spec_refval_crosscheck.py",
    "test_spec_rules_table.py",
    "test_spec_run_description.py",
    "test_spec_run_inputs_semantic.py",
    "test_spec_transitions.py",
    "test_spec_vendored_missing.py",
)


class SkipIsFailure:
    """A pytest plugin: marks the Spec tests and fails the ones that did not run.

    Parameters
    ----------
    expected
        Names of the Spec modules a run over ``root`` must collect items from.
    root
        The folder holding them (default: this file's folder, the tests folder).
    """

    def __init__(self, expected: Collection[str] = (), root: Path | None = None) -> None:
        self.expected = tuple(expected)
        self.root = (root or Path(__file__).resolve().parent).resolve()
        self.collected: Counter[str] = Counter()

    def pytest_configure(self, config: pytest.Config) -> None:
        """Register the marker."""
        config.addinivalue_line("markers", f"{MARKER}: a Spec test that must never skip")

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        """Mark every test of a ``test_spec_*.py`` module."""
        for item in items:
            if item.path.name.startswith(MODULE_PREFIX):
                item.add_marker(MARKER)

    def pytest_itemcollected(self, item: pytest.Item) -> None:
        """Count the items collected from each ``test_spec_*.py`` module (before any -k)."""
        if item.path.name.startswith(MODULE_PREFIX):
            self.collected[item.path.name] += 1

    @pytest.hookimpl(tryfirst=True)
    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        """Fail a ``test_spec_*.py`` module that was skipped while it was collected."""
        name = Path(report.nodeid).name
        if not report.skipped or "::" in report.nodeid or not name.startswith(MODULE_PREFIX):
            return
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        report.outcome = "failed"
        report.longrepr = f"a Spec conformance module must be collected, it was skipped: {reason}"

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Refuse a whole-folder run that collected no item from an expected Spec module."""
        config = session.config
        folders = [Path(arg) for arg in config.args]
        # A run of chosen files, tests or an unrelated folder checks only what it selects.
        if not self.expected or not all(
            f.is_dir() and (f.resolve() == self.root or f.resolve() in self.root.parents)
            for f in folders
        ):
            return
        missing = [name for name in self.expected if not self.collected[name]]
        if missing:
            raise pytest.UsageError(
                "no test was collected from the Spec conformance module(s) "
                + ", ".join(missing)
                + ": deleted, emptied or skipped while collected (tests/spec_gate.py)"
            )

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
