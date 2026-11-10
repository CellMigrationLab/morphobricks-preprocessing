"""The standalone notebook file: portable, readable and executable in a real kernel."""

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NOTEBOOK = ROOT / "notebooks/MorphoBricks_Preprocessing.ipynb"
VERSIONS = ROOT / "notebooks/notebook_latest_versions.yaml"

# The three code cells, in order, as in the lab's ZeroCostDL4Mic and NucleiSky notebooks
# (owner, 10 November 2026): install, mount Google Drive, run the app.
INSTALL, DRIVE, RUN = 0, 1, 2
CELL_TITLES = (
    "# @title 1. Install MorphoBricks preprocessing",
    "# @title 2. Mount Google Drive",
    "# @title 3. Run preprocessing",
)
HEADINGS = (
    "## **1. Install MorphoBricks preprocessing**",
    "## **2. Mount Google Drive**",
    "## **3. Run preprocessing**",
)
COLAB_GUARD = 'if "google.colab" in sys.modules:'


def _notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cells():
    return [cell for cell in _notebook()["cells"] if cell["cell_type"] == "code"]


def _source(role):
    return "".join(_code_cells()[role]["source"])


def _all_code():
    return "\n".join(_source(role) for role in (INSTALL, DRIVE, RUN))


def _setting(name):
    match = re.search(rf'^{name} = "([^"]*)"$', _source(INSTALL), re.M)
    assert match is not None, f"the install cell sets no {name}"
    return match.group(1)


def test_notebook_is_an_introduction_and_three_explained_code_cells():
    notebook = _notebook()
    assert notebook["nbformat"] == 4
    cells = notebook["cells"]
    assert [cell["cell_type"] for cell in cells] == [
        "markdown",
        *("markdown", "code") * 3,
    ]

    introduction = "".join(cells[0]["source"])
    for text in ("What it does", "Before you start", "Google Colab", "JupyterLab / LabConstrictor"):
        assert text in introduction
    for step in ("1. **Install**", "2. **Mount Google Drive**", "3. **Run preprocessing**"):
        assert step in introduction
    assert "single code cell" not in introduction

    code_cells = [cell for cell in cells if cell["cell_type"] == "code"]
    assert [cell["source"][0].rstrip("\n") for cell in code_cells] == list(CELL_TITLES)
    for position, (heading, code) in enumerate(zip(HEADINGS, code_cells, strict=True)):
        explanation = "".join(cells[1 + 2 * position]["source"])
        assert heading in explanation.splitlines()
        assert cells[2 + 2 * position] is code  # each heading sits right above its own cell
        assert code["outputs"] == []
        assert code["execution_count"] is None
        assert code["metadata"].get("jupyter", {}).get("source_hidden") is True
    for cell in cells:
        if cell["cell_type"] == "markdown":
            assert "\\n" not in "".join(cell["source"])


def test_each_code_cell_does_only_its_own_part():
    install, drive, run = (_source(role) for role in (INSTALL, DRIVE, RUN))
    # 1: the pinned install and the version report; no Drive, no app.
    assert "RELEASE_REF = " in install
    assert '"pip",' in install
    assert "Ready: preprocessing" in install
    assert "drive.mount" not in install
    assert "PreprocessingNotebook" not in install
    assert "display(" not in install
    # 2: the Drive mount only.
    assert 'drive.mount("/content/drive")' in drive
    for other in ("pip", "subprocess", "morphobricks", "PreprocessingNotebook", "display("):
        assert other not in drive
    # 3: the app only. It never installs, mounts, or runs an earlier cell's code for the user.
    assert "display(app)" in run
    for other in ("pip", "subprocess", "drive.mount", "RELEASE_REF", "_install_preprocessing"):
        assert other not in run
    assert 'globals().get("SETUP_RECORD")' in run


def test_notebook_uses_stable_page_api_not_a_new_app_method():
    source = _source(RUN)
    assert "notebook.app(" not in source
    # One stepper holds steps 1-13 (owner, 2026-11-09); the statistics stay a page of their own.
    for call in ("notebook.welcome()", "notebook.steps()", "notebook.results_step()"):
        assert call in source
    for page in ("folder_step(", "axis_step(", "channels_step(", "check_save_step(", "run_step("):
        assert f"notebook.{page}" not in _all_code()  # never shown twice beside the stepper
    assert release_call() in source
    assert "display(app)" in source


# The shipped notebook installs the package at its RELEASE_REF, not this checkout: its cell must
# call the API of the code at that commit. Between releases main's code moves on (v0.5.1 removed
# ``describe``), while the cell keeps the released call until the release PR moves RELEASE_REF and
# the call together, adding the new pair here.
RELEASE_CALLS = {
    # v0.5.0: the described run is ``describe=True``; the default is the undescribed v0.4 run.
    "fd7afd339bcaab7579c806a28a65bb7ecebe940c": "PreprocessingNotebook(describe=True)",
}
CHECKOUT_CALL = "PreprocessingNotebook()"
"""The call of this checkout's code (every run described, no ``describe`` argument)."""


def release_call():
    """Return the ``PreprocessingNotebook`` call of the code at the notebook's RELEASE_REF."""
    ref = _setting("RELEASE_REF")
    assert ref in RELEASE_CALLS, (
        f"RELEASE_REF {ref} has no recorded PreprocessingNotebook call: the release that moves "
        "RELEASE_REF records the call of the code at that commit in RELEASE_CALLS"
    )
    return RELEASE_CALLS[ref]


def test_the_shipped_cell_calls_the_api_of_its_release_ref():
    calls = re.findall(r"PreprocessingNotebook\([^)]*\)", _all_code())
    assert calls == [release_call()]  # one call, the released code's: never this checkout's
    assert release_call() in _source(RUN)


def test_colab_setup_mounts_drive_without_custom_widget_manager_or_restart():
    source = _source(DRIVE)
    assert COLAB_GUARD in source
    assert 'drive.mount("/content/drive")' in source
    for role in (INSTALL, DRIVE, RUN):
        assert "enable_custom_widget_manager" not in _source(role)
        assert "Restart session" not in _source(role)


GUARD_TEST = ast.unparse(ast.parse(COLAB_GUARD.removeprefix("if ").removesuffix(":")))


def _colab_only_nodes(tree):
    """Yield the nodes inside an ``if "google.colab" in sys.modules:`` block of ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test) == GUARD_TEST:
            for statement in node.body:
                yield from ast.walk(statement)


@pytest.mark.parametrize("role", [INSTALL, DRIVE, RUN])
def test_colab_only_code_and_content_paths_stay_inside_the_colab_guard(role):
    tree = ast.parse(_source(role))
    inside = {id(node) for node in _colab_only_nodes(tree)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("/content"):
                assert id(node) in inside, node.value
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("google"):
            assert id(node) in inside
    assert "!pip" not in _source(role)
    assert not re.search(r"^\s*[%!]", _source(role), re.M)  # no magics


def test_remote_install_is_pinned_to_one_exact_commit():
    source = _source(INSTALL)
    tag = _setting("RELEASE_TAG")
    ref = _setting("RELEASE_REF")
    assert re.fullmatch(r"v\d+\.\d+\.\d+(rc\d+)?", tag), tag
    assert re.fullmatch(r"[0-9a-f]{40}", ref), ref
    assert "@{RELEASE_REF}" in source
    assert _setting("current_version") == tag.removeprefix("v")
    assert _setting("REPOSITORY") == (
        "https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
    )
    for role in (DRIVE, RUN):  # one place sets the pin
        assert "RELEASE_TAG" not in _source(role)
        assert "current_version" not in _source(role)


def test_version_metadata_matches_labconstrictor_index():
    assert _setting("notebook_name") == NOTEBOOK.stem
    index = dict(
        re.fullmatch(r'"([^"]+)": "([^"]+)"', line).groups()
        for line in VERSIONS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert index == {NOTEBOOK.stem: _setting("current_version")}


def _kernel_notebook(nbformat, *, roles=(INSTALL, DRIVE, RUN), colab_drive=None):
    """Return the notebook's code cells ``roles`` as a notebook ready for this checkout's kernel.

    With ``colab_drive``, a first cell fakes ``google.colab`` whose ``drive.mount`` makes a
    ``MyDrive`` folder, and the cells' ``/content/drive`` is ``colab_drive``.
    """
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    install, run = code[INSTALL], code[RUN]
    # The kernel runs in this checkout, whose commit is not RELEASE_REF (#103): the one edit a
    # developer makes to use a checkout is the explicit override.
    assert install.source.count("DEVELOPMENT_OVERRIDE = False") == 1
    install.source = install.source.replace(
        "DEVELOPMENT_OVERRIDE = False", "DEVELOPMENT_OVERRIDE = True"
    )
    # While RELEASE_REF is behind this checkout, the cell calls the released API: the kernel,
    # running this checkout, is given this checkout's call (the release PR makes them one).
    assert run.source.count(release_call()) == 1
    run.source = run.source.replace(release_call(), CHECKOUT_CALL)
    cells = [code[role] for role in roles]
    if colab_drive is not None:
        for cell in cells:  # the path goes into a Python string: no backslashes (Windows)
            cell.source = cell.source.replace("/content/drive", colab_drive.as_posix())
        fake = nbformat.v4.new_code_cell(
            "import sys, types\n"
            "from pathlib import Path\n"
            "drive = types.SimpleNamespace(\n"
            "    mount=lambda path: (Path(path) / 'MyDrive').mkdir(parents=True, exist_ok=True)\n"
            ")\n"
            "sys.modules['google.colab'] = types.SimpleNamespace(drive=drive)\n"
        )
        cells = [fake, *cells]
    notebook.cells = cells
    return notebook, cells[-len(roles) :]


def _execute(notebook, **options):
    nbclient = pytest.importorskip("nbclient", reason="the dev extra installs nbclient")
    client = nbclient.NotebookClient(
        notebook,
        timeout=300,
        kernel_name="python3",
        resources={"metadata": {"path": str(NOTEBOOK.parent)}},
        **options,
    )
    client.execute()


def _text(cell):
    return "".join(output.get("text", "") for output in cell.outputs)


def _errors(cell):
    return [output for output in cell.outputs if output.output_type == "error"]


def _shows_a_widget(cell):
    return any(
        "application/vnd.jupyter.widget-view+json" in output.get("data", {})
        for output in cell.outputs
    )


def _kernel_modules():
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    pytest.importorskip("ipykernel", reason="the dev extra installs ipykernel")
    pytest.importorskip("nbclient", reason="the dev extra installs nbclient")
    return pytest.importorskip("nbformat")


def test_notebook_runs_in_a_kernel():
    nbformat = _kernel_modules()
    notebook, (install, drive, run) = _kernel_notebook(nbformat)
    _execute(notebook)

    assert not [*_errors(install), *_errors(drive), *_errors(run)]
    assert "Ready: preprocessing" in _text(install)
    assert "Next: run cell 2 (Mount Google Drive), then cell 3" in _text(install)
    # Outside Colab the Drive cell prints one line and does nothing else.
    assert _text(drive).strip().splitlines() == [
        "Not Google Colab: there is no Google Drive to mount here, so nothing was done."
    ]
    assert not _shows_a_widget(install)
    assert _shows_a_widget(run)


def test_notebook_runs_in_a_kernel_with_a_faked_colab(tmp_path):
    nbformat = _kernel_modules()
    drive_root = tmp_path / "content/drive"
    notebook, (install, drive, run) = _kernel_notebook(nbformat, colab_drive=drive_root)
    _execute(notebook)

    assert not [*_errors(install), *_errors(drive), *_errors(run)]
    assert "Google Colab: preparing MorphoBricks preprocessing" in _text(install)
    assert "Google Colab: connecting Google Drive" in _text(drive)
    assert (drive_root / "MyDrive").is_dir()
    assert _shows_a_widget(run)


def test_the_run_cell_alone_in_a_fresh_kernel_names_cell_1():
    pytest.importorskip("ipykernel", reason="the dev extra installs ipykernel")
    nbformat = _kernel_modules()
    notebook, (run,) = _kernel_notebook(nbformat, roles=(RUN,))
    _execute(notebook, allow_errors=True)

    (error,) = _errors(run)
    assert error.ename == "RuntimeError"  # not an ImportError of the package
    assert "run cell 1 (Install MorphoBricks preprocessing) first" in error.evalue
    assert not _shows_a_widget(run)
