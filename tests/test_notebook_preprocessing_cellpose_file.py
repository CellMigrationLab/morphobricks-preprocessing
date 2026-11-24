"""The combined notebook's file (#441): the Preprocessing notebook's cells, then cells 4 and 5.

``MorphoBricks_Preprocessing_Cellpose.ipynb`` is the Preprocessing notebook's install, Drive and
app cells, then **Segment with Cellpose** and **Register the masks**. The shared cells are one
text (as ``_direct_url``/``_installed_commit`` are in both install cells, #437): only the
lines that name the notebook (``notebook_name``, LabConstrictor's key) and its pin
(``RELEASE_REF``) differ. The pin is checked by ``test_notebook_cellpose.py``.
"""

import ast
import json
import re
from pathlib import Path

import pytest

from notebook_pin import code_sources, install_setting
from test_notebook_file import (
    _errors,
    _execute,
    _kernel_modules,
    _shows_a_widget,
    _text,
)

ROOT = Path(__file__).parents[1]
NOTEBOOK = ROOT / "notebooks/MorphoBricks_Preprocessing_Cellpose.ipynb"
PREPROCESSING = ROOT / "notebooks/MorphoBricks_Preprocessing.ipynb"
VERSIONS = ROOT / "notebooks/notebook_latest_versions.yaml"
INSTALL, DRIVE, APP, SEGMENT, REGISTER = range(5)
SHARED = (INSTALL, DRIVE, APP)
OWN_LINES = re.compile(r'^(notebook_name|RELEASE_REF) = "[^"]*"$', re.M)
"""The only lines of the shared cells that differ: the notebook's name and its pin."""
TITLES = (
    "# @title 1. Install MorphoBricks preprocessing",
    "# @title 2. Mount Google Drive",
    "# @title 3. Run preprocessing",
    "# @title 4. Segment with Cellpose",
    "# @title 5. Register the masks",
)


def sources(path=NOTEBOOK):
    return code_sources(path)


def test_an_introduction_five_explained_code_cells_and_the_citation():
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    assert [c["cell_type"] for c in cells] == ["markdown", *("markdown", "code") * 5, "markdown"]
    code = [c for c in cells if c["cell_type"] == "code"]
    assert [c["source"][0].rstrip("\n") for c in code] == list(TITLES)
    for position, cell in enumerate(code):
        assert TITLES[position].removeprefix("# @title ") in "".join(
            cells[1 + 2 * position]["source"]
        )
        assert cell["outputs"] == [] and cell["execution_count"] is None
        assert cell["metadata"].get("jupyter", {}).get("source_hidden") is True
        assert cell["metadata"].get("cellView") == "form"
    intro = "".join(cells[0]["source"])
    for text in ("Cellpose 4.2.1.1", "2D images only", "Cells 1 to 3 are exactly those"):
        assert text in intro


def test_the_install_drive_and_app_cells_are_the_preprocessing_notebooks_text():
    own, theirs = sources(), sources(PREPROCESSING)
    for role in SHARED:
        assert OWN_LINES.sub(r"\1 = ...", own[role]) == OWN_LINES.sub(r"\1 = ...", theirs[role])
    assert own[DRIVE] == theirs[DRIVE] and own[APP] == theirs[APP]  # byte for byte
    assert len(OWN_LINES.findall(own[INSTALL])) == 2  # each line once, in cell 1 only


def test_its_own_name_and_pin():
    install = sources()[INSTALL]
    assert install_setting(install, "notebook_name") == NOTEBOOK.stem
    preprocessing = sources(PREPROCESSING)[INSTALL]
    # Not the Preprocessing release pin: a commit holding cells 4 and 5's code (checked in
    # test_notebook_cellpose.py); the version is the package's, as cell 1 checks a checkout.
    assert re.fullmatch(r"[0-9a-f]{40}", install_setting(install, "RELEASE_REF"))
    for name in ("current_version", "RELEASE_TAG", "REPOSITORY"):
        assert install_setting(install, name) == install_setting(preprocessing, name)
    index = dict(
        re.fullmatch(r'"([^"]+)": "([^"]+)"', line).groups()
        for line in VERSIONS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert index[NOTEBOOK.stem] == install_setting(install, "current_version")


def test_preprocessing_never_installs_cellpose_and_cells_4_and_5_are_thin():
    code = sources()
    for role in SHARED:
        text = OWN_LINES.sub("", code[role])  # notebook_name names this notebook
        assert "cellpose" not in text.lower() and "torch" not in text
    for role, call in ((SEGMENT, "segmentation_step(notebook)"), (REGISTER, "register_step(")):
        text = code[role]
        assert call in text and text.rstrip().splitlines()[-1].startswith("display(")
        assert len(text.splitlines()) < 25
        for other in ("pip", "subprocess", "import cellpose", "import torch", "RELEASE_REF"):
            assert other not in text
    assert 'if "notebook" not in globals():' in code[SEGMENT]
    assert 'if "segmentation" not in globals():' in code[REGISTER]


def test_cells_1_to_4_run_in_a_plain_kernel_without_cellpose():
    nbformat = _kernel_modules()
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    install = code[INSTALL]
    # The kernel runs this checkout, not RELEASE_REF: the developer's explicit override.
    assert install.source.count("DEVELOPMENT_OVERRIDE = False") == 1
    install.source = install.source.replace(
        "DEVELOPMENT_OVERRIDE = False", "DEVELOPMENT_OVERRIDE = True"
    )
    probe = nbformat.v4.new_code_cell(
        "import sys\n"
        "heavy = ('cellpose', 'torch')\n"
        "print('LOADED', sorted(m for m in sys.modules if m.split('.')[0] in heavy))\n"
        "print('NOTICE', 'No finished run to segment' in segmentation.notice.value)\n"
    )
    notebook.cells = [*code[: SEGMENT + 1], probe]
    _execute(notebook)
    cells = notebook.cells
    assert [e for cell in cells for e in _errors(cell)] == []
    assert "Ready: preprocessing" in _text(cells[INSTALL])
    assert _shows_a_widget(cells[APP]) and _shows_a_widget(cells[SEGMENT])
    assert "LOADED []" in _text(cells[-1])  # cell 4 imports no Cellpose and no torch
    assert "NOTICE True" in _text(cells[-1])  # the app has no run yet: cell 3 is named


@pytest.mark.parametrize("role", [SEGMENT, REGISTER])
def test_cells_4_and_5_alone_name_the_cell_to_run_first(role):
    namespace = {}
    with pytest.raises(RuntimeError, match="Run cell 3"):
        exec(compile(sources()[role], f"<cell {role + 1}>", "exec"), namespace)


def test_cell_4_installs_cellpose_by_the_rules_of_the_cellpose_notebooks_install_cell():
    # That cell runs before the package is installed, so it cannot import cellpose_install;
    # until the Cellpose notebook is retired, the two keep one pin and one restart list.
    from morphobricks_preprocessing import cellpose_install
    from morphobricks_preprocessing.segmentation_cellpose_model import CELLPOSE_VERSION

    cell = sources(ROOT / "notebooks/MorphoBricks_Cellpose.ipynb")[INSTALL]
    assert install_setting(cell, "CELLPOSE_VERSION") == CELLPOSE_VERSION
    (assigned,) = [
        node.value
        for node in ast.parse(cell).body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "LOADED_BY_KERNEL"
    ]
    assert ast.literal_eval(assigned) == cellpose_install.LOADED_BY_KERNEL
    assert cellpose_install.cellpose_install_command()[-1] == f"cellpose=={CELLPOSE_VERSION}"


COMPONENT = (
    "notebook_cellpose*.py",
    "segmentation_cellpose*.py",
    "segmentation_record.py",
    "notebook_stop.py",
)
"""The segmentation component's modules, shared by the Cellpose notebook and cell 4 here."""
CELL_NUMBER = re.compile(r"\bcells?\s+\d|^\d+ · ", re.I)
"""A cell number (``cell 5``, ``cells 1 to 4``) or a numbered form title (``3 · Open...``)."""


def _messages(path):
    """Return the text of every string of ``path`` but its docstrings (bare string lines)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bare = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in bare:
            texts.append(node.value)
    return texts


def test_no_component_message_names_a_cell_number():
    # In the combined notebook the whole component is cell 4: the Cellpose notebook's cell
    # numbers ("Run cell 5 first", "3 · Open the preprocessing run") were wrong there. The
    # component names its steps instead (#463 review).
    source = ROOT / "src/morphobricks_preprocessing"
    paths = sorted({p for pattern in COMPONENT for p in source.glob(pattern)})
    assert len(paths) > 10
    found = [(p.name, t) for p in paths for t in _messages(p) if CELL_NUMBER.search(t)]
    assert found == []
