import sys
from pathlib import Path

import pytest

# Lets tests import the shared simulation helpers.
sys.path.insert(0, str(Path(__file__).parent))

from spec_gate import EXPECTED_MODULES, SkipIsFailure  # noqa: E402


def pytest_configure(config):
    # A skipped Spec conformance test is a failure (conformance plan section 1).
    config.pluginmanager.register(SkipIsFailure(EXPECTED_MODULES), "spec-skip-is-failure")


@pytest.fixture
def segment_reads(monkeypatch):
    """Record the index of every TIFF strip or tile the block reader reads (window tests)."""
    from morphobricks_preprocessing import tiff_pieces

    reads = []
    original = tiff_pieces._read_segment

    def counting(page, index, *args):
        reads.append(index)
        return original(page, index, *args)

    monkeypatch.setattr(tiff_pieces, "_read_segment", counting)
    return reads


@pytest.fixture
def statement_off(monkeypatch):
    """Open *Tag channels* with "same channels in the same order" unticked: the PR 17b blocks.

    The tests of the blocks, "Tag like the saved group" and their reopen folds check the page
    the user gets by unticking the statement, which must stay exactly as it was.
    """
    from morphobricks_preprocessing.notebook_channels_grid import TagGrid

    monkeypatch.setattr(TagGrid, "SAME_CHANNELS", False)
