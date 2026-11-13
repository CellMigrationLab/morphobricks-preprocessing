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
        # A Colab form with the code hidden (AGENTS.md section 7, as ZeroCostDL4Mic).
        assert code["metadata"].get("cellView") == "form"
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
    # v0.5.1: every run is described; there is no describe argument.
    "d023ff91289a2d2204e1ae56f7dee114d1a665e4": "PreprocessingNotebook()",
    # v0.5.2: the same call.
    "cadbed4bb049995b2385e3b8499c8d247d3754bd": "PreprocessingNotebook()",
    # v0.5.3: the same call.
    "14f6b1a0e37474d38f3dd54138bbfdcce69d6e9a": "PreprocessingNotebook()",
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


def _kernel_notebook(nbformat, *, roles=(INSTALL, DRIVE, RUN), colab_drive=None, override=True):
    """Return the notebook's code cells ``roles`` as a notebook ready for this checkout's kernel.

    With ``colab_drive``, a first cell fakes ``google.colab`` whose ``drive.mount`` makes a
    ``MyDrive`` folder, and the cells' ``/content/drive`` is ``colab_drive``. ``override=False``
    leaves cell 1 as shipped, so it refuses this checkout (not the ``RELEASE_REF`` commit).
    """
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    install, run = code[INSTALL], code[RUN]
    # The kernel runs in this checkout, whose commit is not RELEASE_REF (#103): the one edit a
    # developer makes to use a checkout is the explicit override.
    assert install.source.count("DEVELOPMENT_OVERRIDE = False") == 1
    if override:
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
            "output = types.SimpleNamespace(no_vertical_scroll=lambda: None)\n"
            "sys.modules['google.colab'] = types.SimpleNamespace(drive=drive, output=output)\n"
        )
        cells = [fake, *cells]
    notebook.cells = cells
    return notebook, cells[-len(roles) :]


def _execute(notebook, **options):
    """Run the notebook in a fresh kernel; run it again when the kernel's output was lost.

    The app sends a message per widget: more than two thousand at once. ZeroMQ keeps 1,000
    messages for a subscriber on each side and drops the rest, so a client that reads slowly
    (macOS CI) lost the end of the stream ("Timeout waiting for IOPub output"), and with it
    the cell's display. That is the transport, not the notebook: this client's queue is made
    unlimited (:func:`_unlimited_queue`), and a run that still loses its output is repeated,
    twice at most; a notebook that really shows nothing still fails every time.
    """
    nbclient = pytest.importorskip("nbclient", reason="the dev extra installs nbclient")
    zmq = pytest.importorskip("zmq", reason="the kernel's transport")
    for attempt in range(3):
        client = nbclient.NotebookClient(
            notebook,
            timeout=300,
            iopub_timeout=120,
            raise_on_iopub_timeout=True,
            kernel_name="python3",
            resources={"metadata": {"path": str(NOTEBOOK.parent)}},
            **options,
        )
        _unlimited_queue(client.create_kernel_manager(), zmq)
        try:
            client.execute()
        except (RuntimeError, TimeoutError) as exc:  # nbclient's CellTimeoutError is the latter
            if "IOPub" not in str(exc) or attempt == 2:
                raise
        else:
            return


def _unlimited_queue(manager, zmq):
    """Make the kernel client the manager creates read its sockets without a queue limit."""
    make = manager.client

    def client(**options):
        made = make(**options)
        made.context.setsockopt(zmq.RCVHWM, 0)  # before its channels connect
        return made

    manager.client = client


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
    assert error.ename == "CellStop"  # the cell's own stop, not an ImportError of the package
    assert "run cell 1 (Install MorphoBricks preprocessing) first" in error.evalue
    # One line, without a traceback (owner, v0.5.1): what Colab and JupyterLab show.
    assert error.traceback == [error.evalue]
    assert not _shows_a_widget(run)


def test_a_refusal_of_cell_1_in_a_kernel_is_one_line_without_a_traceback():
    nbformat = _kernel_modules()
    # As shipped, cell 1 refuses this checkout: its commit is not RELEASE_REF (#103).
    notebook, (install,) = _kernel_notebook(nbformat, roles=(INSTALL,), override=False)
    _execute(notebook, allow_errors=True)

    (error,) = _errors(install)
    assert error.ename == "CellStop"
    assert "is not the release this notebook pins" in error.evalue
    assert error.traceback == [error.evalue]
    assert "\n" not in error.evalue


def test_on_a_faked_colab_without_drive_cell_3_opens_the_app_after_one_line(tmp_path):
    nbformat = _kernel_modules()
    drive_root = tmp_path / "content/drive"
    notebook, (install, run) = _kernel_notebook(
        nbformat, roles=(INSTALL, RUN), colab_drive=drive_root
    )
    _execute(notebook)

    assert not [*_errors(install), *_errors(run)]
    assert _text(run).splitlines() == [
        "Google Drive is not mounted: only files on this Colab runtime are visible, "
        "and they are deleted when the runtime stops."
    ]
    assert not drive_root.exists()  # nothing mounted for the user
    assert _shows_a_widget(run)


def _cell_after(nbformat, notebook, source):
    """Put a cell running ``source`` in front of the notebook's last cell."""
    notebook.cells.insert(len(notebook.cells) - 1, nbformat.v4.new_code_cell(source))


def test_an_unexpected_error_in_a_cell_keeps_its_traceback():
    # Only the cells' own stops are one line: a real error still shows where it happened.
    nbformat = _kernel_modules()
    notebook, (install, run) = _kernel_notebook(nbformat, roles=(INSTALL, RUN))
    _cell_after(
        nbformat,
        notebook,
        "import morphobricks_preprocessing.notebook_ui as interface\n"
        "def broken(*arguments, **options):\n"
        "    raise KeyError('a real defect')\n"
        "interface.PreprocessingNotebook = broken\n",
    )
    _execute(notebook, allow_errors=True)

    assert not _errors(install)
    (error,) = _errors(run)
    assert error.ename == "KeyError"
    assert len(error.traceback) > 1
    assert any("broken" in line for line in error.traceback)


def test_the_stop_for_an_interface_that_cannot_load_names_the_import_error():
    # The one-line stop keeps the cause on its line: it never hides what failed.
    nbformat = _kernel_modules()
    notebook, (install, run) = _kernel_notebook(nbformat, roles=(INSTALL, RUN))
    _cell_after(
        nbformat,
        notebook,
        "import sys\nsys.modules['morphobricks_preprocessing.notebook_ui'] = None\n",
    )
    _execute(notebook, allow_errors=True)

    assert not _errors(install)
    (error,) = _errors(run)
    assert error.ename == "CellStop"
    assert "could not load (ModuleNotFoundError: import of " in error.evalue
    assert "morphobricks_preprocessing.notebook_ui" in error.evalue
    assert error.evalue.endswith("Run cell 1 again.")
    assert error.traceback == [error.evalue]
