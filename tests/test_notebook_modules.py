"""The notebook split into step modules (PP-9): the missing-extra message and the logger."""

import logging
import subprocess
import sys

import numpy as np
import pytest

from tiff_tree import write_data

FRIENDLY = 'pip install "morphobricks-preprocessing[notebook]"'

IMPORT_WITHOUT = """
import sys
sys.modules[{name!r}] = None  # as if the package were not installed
try:
    import morphobricks_preprocessing.notebook_ui
except ImportError as exc:
    print(exc)
else:
    print("imported")
"""


@pytest.mark.parametrize("missing", ["ipywidgets", "matplotlib"])
def test_a_missing_notebook_extra_names_the_install_command(missing):
    code = IMPORT_WITHOUT.format(name=missing)
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert "The notebook interface needs ipywidgets and matplotlib" in done.stdout
    assert FRIENDLY in done.stdout


def test_a_design_that_cannot_be_saved_is_logged_under_the_package_logger(
    tmp_path, monkeypatch, caplog
):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing import notebook_design
    from morphobricks_preprocessing.notebook_options import _PACKAGE_LOGGER
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    root = tmp_path / "images"
    for condition in ("ctrl", "drug"):
        write_data(root / condition / "rep1" / "a.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")

    def read_only(*args, **kwargs):
        raise OSError("read-only output folder")

    monkeypatch.setattr(notebook_design, "write_design", read_only)
    notebook = PreprocessingNotebook(str(root))
    for step in (notebook.welcome, notebook.folder_step, notebook.axis_step):
        step()
    notebook.check_button.click()
    with caplog.at_level(logging.WARNING, logger=_PACKAGE_LOGGER):
        notebook.labels_confirmed.button.click()
    saved = [r for r in caplog.records if r.getMessage() == "Cannot save the design"]
    assert len(saved) == 1
    assert saved[0].name.startswith(_PACKAGE_LOGGER + ".")  # what "Technical details" attaches to
