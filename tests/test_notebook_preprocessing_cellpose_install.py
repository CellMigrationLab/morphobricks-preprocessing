"""Cell 4's Install Cellpose step of the combined notebook (#441, #574 item 1, cpdino fix).

Moved from ``test_notebook_preprocessing_cellpose.py`` (600-line rule). ``pip`` is replaced by
a stand-in that prints pip's lines; nothing is installed.
"""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from cellpose_stub import install

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import (  # noqa: E402
    cellpose_install,
    notebook_preprocessing_cellpose,
)
from morphobricks_preprocessing import segmentation_cellpose_model as model  # noqa: E402
from morphobricks_preprocessing.cellpose_install import (  # noqa: E402
    install_changes,
    installed_distributions,
)
from morphobricks_preprocessing.notebook_cellpose_install import pip_step  # noqa: E402
from morphobricks_preprocessing.notebook_preprocessing_cellpose import (  # noqa: E402
    segmentation_step,
)
from test_notebook_preprocessing_cellpose import ran_app  # noqa: E402

pytestmark = pytest.mark.usefixtures("legacy_grid")


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


# installing Cellpose on first use ------------------------------------------------------------


@pytest.fixture
def pip(monkeypatch, stub):
    """Cellpose not installed; ``pip`` installs it (and may change numpy) when it runs."""
    state = SimpleNamespace(commands=[], numpy=None, lines=PIP_LINES, returncode=0, seen=[])
    stub.version = None
    real = model.installed_version

    def version(name):
        if name == "cellpose":
            return stub.version
        if name == "numpy" and state.numpy:
            return state.numpy[1 if state.commands else 0]
        return real(name)

    class Pip:
        """pip as ``cellpose_install`` runs it: its lines as it prints them, then its status."""

        def __init__(self, command, **options):
            assert options["stderr"] is subprocess.STDOUT and options["text"]
            state.commands.append(command)
            self.stdout, self.returncode = iter(state.lines), None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.returncode = state.returncode
            if not state.returncode:
                stub.version = "4.2.1.1"

    monkeypatch.setattr(model, "installed_version", version)
    monkeypatch.setattr(cellpose_install.subprocess, "Popen", Pip)
    monkeypatch.setattr(cellpose_install.subprocess, "run", None)  # never the silent run
    return state


PIP_LINES = [
    "Collecting cellpose==4.2.1.1\n",
    "  Downloading cellpose-4.2.1.1-py3-none-any.whl.metadata (24 kB)\n",
    "Requirement already satisfied: torch>=1.6 in /usr/lib/python3/site-packages (2.9.0)\n",
    "Downloading opencv_python_headless-5.1.0.95-cp37-abi3-manylinux_2_28_x86_64.whl (63.7 MB)\n",
    "Installing collected packages: cellpose\n",
    "Successfully installed cellpose-4.2.1.1\n",
]
"""What pip prints for the installation on Colab, where PyTorch is already there (shortened)."""


def test_cellpose_is_installed_only_when_ticked_then_the_component_appears(tmp_path, pip):
    step = segmentation_step(ran_app(tmp_path))
    assert step.segmentation is None and step.install_button.disabled
    assert "tick the installation" in step.install_blocker.value
    assert not step.install()  # a press without the tick installs nothing
    assert pip.commands == [] and "Tick the installation first" in step.install_status.value
    step.install_tick.value = True
    assert not step.install_button.disabled and step.install()
    (command,) = pip.commands
    assert command[1:4] == ["-m", "pip", "install"] and command[-1] == "cellpose==4.2.1.1"
    assert step.segmentation is not None and step.workflow.inputs is None  # no masks folder yet


def test_dinov3_is_installed_before_cellpose_when_none_is_there(tmp_path, pip, stub):
    # cpdino fix (v0.9.1): DINOv3 in place before Cellpose is ever imported, so a cpdino model
    # loaded after a cpsam one needs no runtime restart.
    stub.dinov3_commit = None
    step = segmentation_step(ran_app(tmp_path))
    step.install_tick.value = True
    assert step.install()
    first, second = pip.commands
    assert first == model.dinov3_install_command() and "--no-deps" in first
    assert second[-1] == "cellpose==4.2.1.1"


def test_a_dinov3_already_installed_is_left_as_it_is(tmp_path, pip, stub):
    stub.dinov3_commit, stub.dinov3_present = None, True  # one of the user's own, commit unknown
    step = segmentation_step(ran_app(tmp_path))
    step.install_tick.value = True
    assert step.install()
    (command,) = pip.commands
    assert command[-1] == "cellpose==4.2.1.1"


def test_a_failed_dinov3_install_installs_no_cellpose(tmp_path, pip, stub):
    stub.dinov3_commit, pip.returncode = None, 1
    step = segmentation_step(ran_app(tmp_path))
    step.install_tick.value = True
    assert not step.install()
    assert len(pip.commands) == 1 and "DINOv3" in step.install_status.value
    assert "Cellpose was not installed" in step.install_status.value
    assert step.segmentation is None


def test_an_install_that_changes_a_loaded_numpy_asks_for_a_restart(tmp_path, pip):
    pip.numpy = ("2.0.0", "2.3.0")
    step = segmentation_step(ran_app(tmp_path))
    step.install_tick.value = True
    assert not step.install()
    assert "numpy 2.0.0 -&gt; 2.3.0" in step.install_status.value
    assert "Restart the runtime" in step.install_status.value and step.segmentation is None
    assert "run cells 1 to 3 again" in step.install_status.value


def test_the_install_shows_what_pip_does_then_what_it_installed(tmp_path, pip, monkeypatch):
    # Owner's Colab test (#574): the first installation was slow and showed nothing useful.
    snapshots = iter([{"torch": "2.9.0"}, {"torch": "2.9.0", "cellpose": "4.2.1.1"}])
    monkeypatch.setattr(
        notebook_preprocessing_cellpose, "installed_distributions", lambda: next(snapshots)
    )
    step = segmentation_step(ran_app(tmp_path))
    shown = []
    step.install_status.observe(lambda change: shown.append(change["new"]), names="value")
    step.install_tick.value = True
    assert step.install()
    said = "".join(shown)
    for step_said in (
        "Installing Cellpose 4.2.1.1 (with PyTorch): starting pip",
        "Installing Cellpose 4.2.1.1 (with PyTorch): finding cellpose==4.2.1.1",
        "downloading opencv_python_headless-5.1.0.95-cp37-abi3-manylinux_2_28_x86_64.whl (63.7 MB)",
        "installing the packages (this can take minutes)",
    ):
        assert step_said in said, step_said
    assert "elapsed" in said
    assert "Cellpose 4.2.1.1 is installed (0:0" in step.installed.value
    assert "pip installed or changed: cellpose 4.2.1.1." in step.installed.value
    assert step.installed in step.children and step.segmentation is not None


def test_a_failed_install_shows_pips_last_lines(tmp_path, pip):
    pip.lines, pip.returncode = ["Collecting cellpose==4.2.1.1", "ERROR: No matching dist"], 1
    step = segmentation_step(ran_app(tmp_path))
    step.install_tick.value = True
    assert not step.install() and step.segmentation is None
    status = step.install_status.value
    assert "Cellpose is not installed" in status and "could not be installed" in status
    assert "pip's last lines" in status and "ERROR: No matching dist" in status
    assert step.installed.value == ""


def test_an_interrupted_install_kills_pip(monkeypatch):
    # #574 review: subprocess.run killed pip on any exception; the streaming loop must too.
    started = []

    class Recorded(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            started.append(self)

    def interrupt(line):
        raise KeyboardInterrupt

    monkeypatch.setattr(cellpose_install.subprocess, "Popen", Recorded)
    child = "import time; print('Collecting cellpose', flush=True); time.sleep(120)"
    with pytest.raises(KeyboardInterrupt):
        cellpose_install._pip([sys.executable, "-c", child], interrupt)
    (pip,) = started
    assert pip.returncode is not None and pip.returncode != 0  # killed, and waited for


def test_pip_output_that_is_not_text_is_replaced_not_fatal():
    lines = []
    child = "import sys; sys.stdout.buffer.write(b'Downloading caf\\xe9 (1 MB)\\n')"
    cellpose_install._pip([sys.executable, "-c", child], lines.append)
    assert lines == ["Downloading caf\ufffd (1 MB)"]


def test_pip_steps_and_the_list_of_changes():
    assert pip_step("Collecting fastremap (from cellpose==4.2.1.1)") == "finding fastremap"
    assert pip_step("Requirement already satisfied: numpy in /usr (2.0.2)") is None
    before = {"numpy": "2.0.2", "torch": "2.9.0"}
    after = {"numpy": "2.3.0", "torch": "2.9.0", "fill-voids": "2.1.2"}
    assert install_changes(before, after) == ["fill-voids 2.1.2", "numpy 2.3.0 (was 2.0.2)"]
    assert install_changes(after, after) == []
    names = installed_distributions()
    assert names["morphobricks-preprocessing"] and "pytest" in names


def test_preprocessing_alone_never_imports_cellpose_or_torch():
    code = (
        "import sys\n"
        "from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook\n"
        "nb = PreprocessingNotebook()\n"
        "nb.welcome(); nb.steps(); nb.results_step()\n"
        "names = ('cellpose', 'torch', 'morphobricks_preprocessing.cellpose_install',\n"
        "         'morphobricks_preprocessing.notebook_preprocessing_cellpose')\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in names[:2] or m in names))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
