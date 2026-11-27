"""Install Cellpose and the code loaded in the runtime (external review bc of #582, B7).

- Cellpose pinned but no DINOv3 (installed by another notebook, say): cell 4 mounted without
  offering an installation, and the first cpdino Preview pip-installed DINOv3. Owner, 27 Nov
  2026: the DINOv3 code installs with *Install Cellpose*, never from Preview or Run.
- A Cellpose, torch or DINOv3 replaced on disk under a running kernel stayed loaded while the
  metadata named the new one; the old code must not run as the pinned one.
"""

import sys
from types import ModuleType

import pytest

from cellpose_stub import install
from test_notebook_preprocessing_cellpose_install import PIP_LINES

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import cellpose_install  # noqa: E402
from morphobricks_preprocessing import segmentation_cellpose as cp  # noqa: E402
from morphobricks_preprocessing import segmentation_cellpose_model as model  # noqa: E402
from morphobricks_preprocessing import segmentation_cellpose_runtime as runtime  # noqa: E402
from morphobricks_preprocessing.notebook_preprocessing_cellpose import (  # noqa: E402
    segmentation_step,
)
from morphobricks_preprocessing.segmentation_record import SegmentationError  # noqa: E402
from test_notebook_preprocessing_cellpose import ran_app  # noqa: E402

pytestmark = pytest.mark.usefixtures("legacy_grid")


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


@pytest.fixture
def pip(monkeypatch, stub):
    """``pip`` that installs DINOv3 (the stand-in's commit) and records each command."""
    commands = []

    class Pip:
        def __init__(self, command, **options):
            commands.append(command)
            self.command, self.stdout, self.returncode = command, iter(PIP_LINES), None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.returncode = 0
            if self.command == model.dinov3_install_command():
                stub.dinov3_commit = model.DINOV3_COMMIT

    monkeypatch.setattr(cellpose_install.subprocess, "Popen", Pip)
    monkeypatch.setattr(model, "install_dinov3", lambda: commands.append("load-time install"))
    return commands


def _no_cellpose_imported(monkeypatch):
    for name in [n for n in sys.modules if n == "cellpose" or n.startswith("cellpose.")]:
        monkeypatch.delitem(sys.modules, name)


def test_pinned_cellpose_without_dinov3_offers_the_code_install_and_preview_installs_nothing(
    tmp_path, stub, pip, monkeypatch
):
    stub.dinov3_commit = None
    step = segmentation_step(ran_app(tmp_path))
    assert step.segmentation is not None  # the SAM models work now
    assert step.install_box.children[0] is not step.segmentation  # the install card is offered
    assert step.dinov3_only and "DINOv3 code" in step.install_tick.description
    assert not step.segmentation.workflow.install_dinov3_on_load
    # A cpdino Preview / Load the model: refused, no pip at all.
    with pytest.raises(SegmentationError, match="needs the DINOv3 code"):
        cp.load_model("cpdino", "cpu", install_dinov3=step.workflow.install_dinov3_on_load)
    assert pip == [] and stub.dinov3_installs == []
    # The explicit, ticked installation installs the DINOv3 code only.
    _no_cellpose_imported(monkeypatch)
    step.install_tick.value = True
    assert step.install(), step.install_status.value
    assert pip == [model.dinov3_install_command()]
    assert step.install_box.children == (step.segmentation,)


def test_the_code_install_after_cellpose_was_imported_asks_for_a_restart(tmp_path, stub, pip):
    stub.dinov3_commit = None
    step = segmentation_step(ran_app(tmp_path))
    assert "cellpose.vit" in sys.modules  # imported (the stand-in is loaded)
    step.install_tick.value = True
    assert not step.install()
    assert pip == [model.dinov3_install_command()]
    assert "Restart the runtime" in step.install_status.value
    assert "The installation is not finished" in step.install_status.value


def test_nothing_is_offered_or_installed_when_both_are_there(tmp_path, stub, pip):
    step = segmentation_step(ran_app(tmp_path))
    assert step.install_box.children == (step.segmentation,)
    assert cellpose_install.install_cellpose(False) == model.CELLPOSE_VERSION
    assert pip == []


# what is loaded is what is installed ----------------------------------------------------------


def _loaded_cellpose(monkeypatch, version):
    module = ModuleType("cellpose.version")
    module.version = version
    monkeypatch.setitem(sys.modules, "cellpose.version", module)


def test_a_cellpose_replaced_under_the_kernel_asks_for_a_restart(stub, monkeypatch):
    _loaded_cellpose(monkeypatch, "4.1.0")  # loaded; 4.2.1.1 on disk now
    with pytest.raises(SegmentationError, match=r"Cellpose was replaced.*loaded: 4.1.0"):
        cp.load_model("cpsam", "cpu")
    assert stub.constructed == []


def test_a_torch_replaced_under_the_kernel_asks_for_a_restart(stub, monkeypatch):
    sys.modules["torch"].__version__ = "2.4.0"
    real = model.installed_version
    monkeypatch.setattr(model, "installed_version", lambda n: "2.5.1" if n == "torch" else real(n))
    with pytest.raises(SegmentationError, match=r"PyTorch was replaced.*2.4.0.*2.5.1"):
        cp.load_model("cpsam", "cpu")


def test_an_unchanged_runtime_is_not_asked_to_restart(stub, monkeypatch):
    _loaded_cellpose(monkeypatch, "4.2.1.1")
    sys.modules["torch"].__version__ = "2.5.1"
    real = model.installed_version
    monkeypatch.setattr(model, "installed_version", lambda n: "2.5.1" if n == "torch" else real(n))
    cp.load_model("cpsam", "cpu")
    cp.load_model("cpdino", "cpu")


def test_a_dinov3_replaced_since_cellpose_imported_it_asks_for_a_restart(stub, monkeypatch):
    cp.load_model("cpdino", "cpu")  # imported with the pinned commit
    stub.dinov3_commit = "0" * 40
    with pytest.raises(SegmentationError, match="commit"):
        cp.load_model("cpdino", "cpu")
    # Loaded at one commit (recorded when this brick imported cellpose.vit), pinned one on disk.
    monkeypatch.setattr(runtime, "_imported_dinov3", ["1" * 40])
    stub.dinov3_commit = model.DINOV3_COMMIT
    with pytest.raises(SegmentationError, match="DINOv3 was replaced"):
        cp.load_model("cpdino", "cpu")


def test_a_dinov3_without_the_vitb_constructor_is_refused_for_cpdino_vitb(stub):
    del sys.modules["cellpose.vit"].dinov3_vitb16
    with pytest.raises(SegmentationError, match="lacks dinov3_vitb16"):
        cp.load_model("cpdino-vitb", "cpu")
    assert stub.constructed == []
    cp.load_model("cpdino", "cpu")  # its own constructor is there
