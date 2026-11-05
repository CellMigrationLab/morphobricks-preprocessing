"""The standalone notebook file: simple, pinned and executable in a real kernel."""

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NOTEBOOK = ROOT / "notebooks/MorphoBricks_Preprocessing.ipynb"
VERSIONS = ROOT / "notebooks/notebook_latest_versions.yaml"


def _notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cells():
    return ["".join(c["source"]) for c in _notebook()["cells"] if c["cell_type"] == "code"]


def _setting(name):
    match = re.search(rf'^{name} = "([^"]*)"$', _code_cells()[0], re.M)
    assert match is not None, f"the setup cell sets no {name}"
    return match.group(1)


def test_notebook_is_small_and_has_no_saved_outputs():
    notebook = _notebook()
    code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert notebook["nbformat"] == 4
    assert len(notebook["cells"]) == 4
    assert len(code) == 2
    assert all(cell["outputs"] == [] and cell["execution_count"] is None for cell in code)
    assert all(cell["metadata"] == {} for cell in code)


def test_setup_cell_needs_no_notebook_dependencies_before_installing():
    setup = _code_cells()[0]
    imports = set()
    for node in ast.walk(ast.parse(setup)):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module.split(".")[0])
    assert imports <= {"subprocess", "sys", "importlib", "pathlib", "platform", "google"}


def test_colab_setup_mounts_drive_but_does_not_enable_a_custom_widget_manager():
    setup = _code_cells()[0]
    assert 'drive.mount("/content/drive")' in setup
    assert "enable_custom_widget_manager" not in setup
    assert "ipywidgets 8" not in setup
    assert "Restart session" not in setup


def test_remote_install_is_pinned_to_one_exact_commit():
    setup = _code_cells()[0]
    tag = _setting("RELEASE_TAG")
    ref = _setting("RELEASE_REF")
    assert re.fullmatch(r"v\d+\.\d+\.\d+(rc\d+)?", tag), tag
    assert re.fullmatch(r"[0-9a-f]{40}", ref), ref
    assert f"@{{RELEASE_REF}}" in setup
    assert _setting("current_version") == tag.removeprefix("v")
    assert _setting("REPOSITORY") == (
        "https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
    )


def test_version_metadata_matches_labconstrictor_index():
    assert _setting("notebook_name") == NOTEBOOK.stem
    index = dict(
        re.fullmatch(r'"([^"]+)": "([^"]+)"', line).groups()
        for line in VERSIONS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert index == {NOTEBOOK.stem: _setting("current_version")}


def test_the_only_app_cell_uses_explicit_display():
    app = _code_cells()[1].strip()
    assert app == (
        "# Open the preprocessing app\n"
        "from IPython.display import display\n\n"
        "display(notebook.app())"
    )


def test_notebook_app_contains_the_complete_workflow():
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    import ipywidgets as widgets

    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    notebook = PreprocessingNotebook()
    app = notebook.app()
    assert isinstance(app, widgets.VBox)
    assert len(app.children) == 5


def test_notebook_runs_in_a_kernel():
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    pytest.importorskip("ipykernel", reason="the dev extra installs ipykernel")
    nbclient = pytest.importorskip("nbclient", reason="the dev extra installs nbclient")
    nbformat = pytest.importorskip("nbformat")

    notebook = nbformat.read(NOTEBOOK, as_version=4)
    client = nbclient.NotebookClient(
        notebook,
        timeout=300,
        kernel_name="python3",
        resources={"metadata": {"path": str(NOTEBOOK.parent)}},
    )
    client.execute()
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    assert not [
        output
        for cell in code
        for output in cell.outputs
        if output.output_type == "error"
    ]
    setup_text = "".join(output.get("text", "") for output in code[0].outputs)
    assert "Ready. MorphoBricks preprocessing" in setup_text
    assert any(
        "application/vnd.jupyter.widget-view+json" in output.get("data", {})
        for output in code[1].outputs
    )
