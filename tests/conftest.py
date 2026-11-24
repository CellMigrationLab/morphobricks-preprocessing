import sys
from pathlib import Path

import pytest

# Lets tests import the shared simulation helpers.
sys.path.insert(0, str(Path(__file__).parent))

from spec_gate import EXPECTED_MODULES, SkipIsFailure  # noqa: E402


def pytest_configure(config):
    # A skipped Spec conformance test is a failure (conformance plan section 1).
    config.pluginmanager.register(SkipIsFailure(EXPECTED_MODULES), "spec-skip-is-failure")


def _widget_registry():
    """Return ipywidgets' registry of open widgets and the widget class, or None if not imported.

    ipywidgets 8 keeps the open widgets in ``widget._instances``; 7.x in ``Widget.widgets``.
    """
    module = sys.modules.get("ipywidgets.widgets.widget")
    if module is None:
        return None
    registry = module._instances if hasattr(module, "_instances") else module.Widget.widgets
    return registry, module.Widget


def _module_level_widgets(widget_class) -> set[int]:
    """Return the ids of the widgets held by this package's or the tests' modules and classes.

    A module-level widget (such as a shared ``Layout``) is made by the first test that imports
    its module and is used by every later test, so it must stay open.
    """
    tests = Path(__file__).parent
    found = set()
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None) or ""
        if not (name.startswith("morphobricks_preprocessing") or Path(file).parent == tests):
            continue
        values = list(vars(module).values())
        values += [
            value
            for item in values
            if isinstance(item, type) and item.__module__ == name
            for value in vars(item).values()
        ]
        for value in values:
            if isinstance(value, dict):
                items = value.values()
            elif isinstance(value, list | tuple | set | frozenset):
                items = value
            else:
                items = (value,)
            found.update(id(item) for item in items if isinstance(item, widget_class))
    return found


@pytest.fixture(autouse=True)
def _release_widgets_and_figures():
    """Close the widgets and pyplot figures a test made once it ends (issue #457).

    ipywidgets keeps every open widget in its registry and in the comm manager, so the pages of
    the notebook tests were never freed and one pytest process grew to about 10 GB. Closing
    them after the test changes nothing the test sees; widgets made before it (module-level
    ones included) stay open.
    """
    found = _widget_registry()
    before = set(found[0]) if found else set()
    yield
    pyplot = sys.modules.get("matplotlib.pyplot")
    if pyplot is not None:
        pyplot.close("all")
    found = _widget_registry()
    if found is None:
        return
    registry, widget_class = found
    made = [widget for key, widget in list(registry.items()) if key not in before]
    if not made:
        return
    kept = _module_level_widgets(widget_class)
    for widget in made:
        if id(widget) not in kept:
            widget.close()


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
def mixed_run(tmp_path):
    """A described run with a fourth stain in one repeat (the Cellpose tests; no Cellpose)."""
    from cellpose_stub import mixed_run as build

    return build(tmp_path)


@pytest.fixture
def statement_off(monkeypatch):
    """Open *Tag channels* with "same channels in the same order" unticked: the PR 17b blocks.

    The tests of the blocks, "Tag like the saved group" and their reopen folds check the page
    the user gets by unticking the statement, which must stay exactly as it was.
    """
    from morphobricks_preprocessing.notebook_channels_grid import TagGrid

    monkeypatch.setattr(TagGrid, "SAME_CHANNELS", False)
