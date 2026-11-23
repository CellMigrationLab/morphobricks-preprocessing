"""The Cellpose notebook's cells after the structure audit (owner, 23 November 2026).

``CellStop`` is defined once, in :mod:`morphobricks_preprocessing.notebook_stop`; cells 2 to 8
import it and only cell 1 (the install, run before the package is there) keeps its own copy.
"""

import pytest

from morphobricks_preprocessing import notebook_cellpose, notebook_stop
from test_notebook_cellpose import CHANNELS, DRIVE, INSTALL, NEXT, OPEN, source

IMPORT = "from morphobricks_preprocessing.notebook_stop import CellStop"


def test_cellstop_is_defined_once_in_the_package_and_cell_1_keeps_its_copy():
    assert "class CellStop(RuntimeError):" in source(INSTALL)
    for number in range(DRIVE, NEXT + 1):
        text = source(number)
        assert "class CellStop" not in text, f"cell {number + 1}"
        assert "Run cell 1 first: it installs what this notebook needs." in text
        if number != OPEN:  # cell 3 stops on nothing of its own
            assert IMPORT in text, f"cell {number + 1}"
    assert notebook_cellpose.CellStop is notebook_stop.CellStop
    assert notebook_stop.CellStop("one line")._render_traceback_() == ["one line"]


def test_a_cell_run_too_early_stops_with_one_line_in_a_real_kernel():
    nbformat = pytest.importorskip("nbformat")
    pytest.importorskip("ipykernel", reason="the dev extra installs ipykernel")
    from test_notebook_file import _execute

    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(source(CHANNELS))])
    _execute(notebook, allow_errors=True)
    (error,) = [o for o in notebook.cells[0].outputs if o.output_type == "error"]
    assert (error.ename, error.evalue) == ("CellStop", "Run cell 3 first.")
    assert error.traceback == ["Run cell 3 first."]
