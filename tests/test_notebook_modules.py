"""The notebook split into step modules (PP-9): the missing-extra message and the logger."""

import subprocess
import sys

import pytest

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
