"""The notebook file: LabConstrictor's portability rules, its release pin, and that it runs.

The rules follow LabConstrictor's notebook portability guide (plan §12 question 58): every
code cell starts with ``# @title``; Colab-only code runs inside
``if "google.colab" in sys.modules:``; no shell or line magics; ``current_version`` and
``notebook_name`` as its version check reads them. This notebook is an app-like widget
notebook, so every code cell's source is hidden (LabConstrictor's code-hiding guide).
"""

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NOTEBOOK = ROOT / "notebooks" / "MorphoBricks_Preprocessing.ipynb"
VERSIONS = ROOT / "notebooks" / "notebook_latest_versions.yaml"
HIDDEN = {
    "cellView": "form",
    "collapsed": False,
    "jupyter": {"source_hidden": True},
    "jupyterlabHideCode": {"locked": True},
}
STEP_CALLS = [
    "notebook.folder_step()",
    "notebook.axis_step()",
    "notebook.run_step()",
    "notebook.results_step()",
]


def _notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cells():
    return ["".join(c["source"]) for c in _notebook()["cells"] if c["cell_type"] == "code"]


def _markdown():
    return "\n".join(
        "".join(c["source"]) for c in _notebook()["cells"] if c["cell_type"] == "markdown"
    )


def _setting(name):
    match = re.search(rf'^{name} = "([^"]*)"$', _code_cells()[0], re.M)
    assert match is not None, f"the setup cell sets no {name}"
    return match.group(1)


def _colab_guard_lines(source):
    """Return the line numbers inside the setup cell's top-level Colab guard."""
    guards = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "'google.colab' in sys.modules"
    ]
    assert len(guards) == 1, "the setup cell needs one `if 'google.colab' in sys.modules:` block"
    (guard,) = guards
    return set(range(guard.body[0].lineno, guard.body[-1].end_lineno + 1))


def test_notebook_is_valid_and_has_no_outputs():
    notebook = _notebook()
    assert notebook["nbformat"] == 4
    code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert all(cell["outputs"] == [] and cell["execution_count"] is None for cell in code)


def test_every_code_cell_is_titled_and_hidden():
    for cell in _notebook()["cells"]:
        if cell["cell_type"] == "code":
            first_line = "".join(cell["source"]).splitlines()[0]
            assert first_line.startswith("# @title Run to "), first_line
            assert cell["metadata"] == HIDDEN, first_line


def test_colab_only_code_is_guarded_and_no_magics_are_used():
    for number, source in enumerate(_code_cells()):
        assert not re.search(r"^\s*[!%]", source, re.M), "magics only run in IPython"
        assert "get_ipython" not in source
        guarded = _colab_guard_lines(source) if number == 0 else set()
        for line_number, line in enumerate(source.splitlines(), start=1):
            colab = "google.colab" in line and "sys.modules" not in line
            if colab or "/content" in line:
                assert line_number in guarded, f"Colab-only code outside the guard: {line!r}"


def test_colab_guard_mounts_drive_and_enables_the_widget_manager():
    source = _code_cells()[0]
    guarded = "\n".join(
        line
        for number, line in enumerate(source.splitlines(), start=1)
        if number in _colab_guard_lines(source)
    )
    assert 'drive.mount("/content/drive")' in guarded
    assert "output.enable_custom_widget_manager()" in guarded


def test_github_install_is_pinned_to_the_release_tag():
    tag = _setting("RELEASE_TAG")
    assert re.fullmatch(r"v\d+\.\d+\.\d+(rc\d+)?", tag), tag
    setup = _code_cells()[0]
    assert setup.count("git+") == 1
    assert '@ git+{REPOSITORY}@{RELEASE_TAG}"' in setup
    assert _setting("REPOSITORY") == (
        "https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
    )
    # The notebook's version is its release, and the Colab callout names the tag installed.
    assert _setting("current_version") == tag.removeprefix("v")
    assert f"**{tag}**" in _markdown()


def test_version_metadata_matches_labconstrictor_index():
    assert _setting("notebook_name") == NOTEBOOK.stem
    index = dict(
        re.fullmatch(r'"([^"]+)": "([^"]+)"', line).groups()
        for line in VERSIONS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert index == {NOTEBOOK.stem: _setting("current_version")}


def test_notebook_keeps_processing_in_the_package():
    sources = [source.split("\n", 1)[-1].strip() for source in _code_cells()]
    # Step 0 must work before the package or third-party dependencies are installed.
    setup_imports = set()
    for node in ast.walk(ast.parse(sources[0])):
        if isinstance(node, ast.Import):
            setup_imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            setup_imports.add(node.module.split(".")[0])
    assert setup_imports <= {"subprocess", "sys", "importlib", "pathlib", "platform", "google"}
    assert sources[1:] == STEP_CALLS


def test_notebook_names_buttons_that_exist():
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    text = "".join("".join(c["source"]) for c in _notebook()["cells"])
    nb = PreprocessingNotebook()
    for button in (
        nb.check_button,
        nb.labels_confirmed.button,
        nb.preview_button,
        nb.preview_checked.button,
        nb.missing_as_singleton.button,
        nb.confirm_button,
        nb.convert_roles_checked.button,
        nb.convert_button,
        nb.convert_recheck_button,
        nb.image_type_checked.button,
        nb.run_button,
        nb.mask_step_button,
        nb.statistics_button,
    ):
        assert f"**{button.description.strip()}**" in text


def test_notebook_cells_run(capsys):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    namespace: dict = {}
    for source in _code_cells():
        exec(source, namespace)  # noqa: S102 - our own notebook
    assert isinstance(namespace["notebook"], PreprocessingNotebook)
    assert "Local Jupyter: Python" in capsys.readouterr().out


def test_notebook_runs_in_a_kernel():
    # The whole file, as Jupyter runs it: a fresh kernel started in the notebooks folder.
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
    outputs = [output for cell in code for output in cell.outputs]
    assert not [output for output in outputs if output.output_type == "error"]
    setup_text = "".join(o.get("text", "") for o in code[0].outputs)
    assert "Local Jupyter: Python" in setup_text
    assert "Using the local checkout" in setup_text
    for cell in code:  # Step 0's ready card and every step's page are widgets
        assert any(
            "application/vnd.jupyter.widget-view+json" in output.get("data", {})
            for output in cell.outputs
        ), cell.source.splitlines()[0]
