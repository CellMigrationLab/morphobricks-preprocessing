"""The standalone notebook file: portable, readable and executable in a real kernel."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NOTEBOOK = ROOT / "notebooks/MorphoBricks_Preprocessing.ipynb"
VERSIONS = ROOT / "notebooks/notebook_latest_versions.yaml"


def _notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cell():
    cells = [cell for cell in _notebook()["cells"] if cell["cell_type"] == "code"]
    assert len(cells) == 1
    return cells[0]


def _source():
    return "".join(_code_cell()["source"])


def _setting(name):
    match = re.search(rf'^{name} = "([^"]*)"$', _source(), re.M)
    assert match is not None, f"the setup cell sets no {name}"
    return match.group(1)


def test_notebook_is_one_readable_markdown_cell_and_one_runnable_cell():
    notebook = _notebook()
    assert notebook["nbformat"] == 4
    assert [cell["cell_type"] for cell in notebook["cells"]] == ["markdown", "code"]

    markdown = "".join(notebook["cells"][0]["source"])
    assert "What it does" in markdown
    assert "Before you start" in markdown
    assert "Google Colab" in markdown
    assert "JupyterLab / LabConstrictor" in markdown
    assert "\\n" not in markdown

    code = _code_cell()
    assert code["outputs"] == []
    assert code["execution_count"] is None
    assert _source().splitlines()[0].startswith("# @title ")
    assert code["metadata"].get("jupyter", {}).get("source_hidden") is True


def test_notebook_uses_stable_page_api_not_a_new_app_method():
    source = _source()
    assert "notebook.app(" not in source
    for call in (
        "notebook.welcome()",
        "notebook.folder_step()",
        "notebook.axis_step()",
        "notebook.run_step()",
        "notebook.results_step()",
    ):
        assert call in source
    assert "display(app)" in source


def test_colab_setup_mounts_drive_without_custom_widget_manager_or_restart():
    source = _source()
    assert 'if "google.colab" in sys.modules:' in source
    assert 'drive.mount("/content/drive")' in source
    assert "enable_custom_widget_manager" not in source
    assert "Restart session" not in source


def test_remote_install_is_pinned_to_one_exact_commit():
    source = _source()
    tag = _setting("RELEASE_TAG")
    ref = _setting("RELEASE_REF")
    assert re.fullmatch(r"v\d+\.\d+\.\d+(rc\d+)?", tag), tag
    assert re.fullmatch(r"[0-9a-f]{40}", ref), ref
    assert "@{RELEASE_REF}" in source
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

    code = next(cell for cell in notebook.cells if cell.cell_type == "code")
    assert not [output for output in code.outputs if output.output_type == "error"]
    text = "".join(output.get("text", "") for output in code.outputs)
    assert "Ready: preprocessing" in text
    assert any(
        "application/vnd.jupyter.widget-view+json" in output.get("data", {})
        for output in code.outputs
    )
