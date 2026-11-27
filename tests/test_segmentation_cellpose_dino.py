"""The cpdino fix of v0.9.1: DINOv3 before Cellpose, and a restart asked only when needed.

Cellpose 4.2.1.1 imports DINOv3 once, when ``cellpose.vit`` is first imported. The owner's Colab
test of v0.9.0 had ``cpdino`` ask for a runtime restart. A restart is now asked for only when
DINOv3 imports now but Cellpose missed it; a DINOv3 that does not import is refused with its
import error. The install step's half is in ``test_notebook_preprocessing_cellpose.py``.
"""

import sys
import types

import pytest

from cellpose_stub import install
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing import segmentation_cellpose_model as model_module
from morphobricks_preprocessing.segmentation_record import SegmentationError


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def test_a_restart_is_asked_only_when_dinov3_imports_now(monkeypatch, tmp_path):
    # cpdino fix (v0.9.1): Cellpose imported without DINOv3, and DINOv3 does not import now
    # either: a restart would not help, so the refusal names the import error instead.
    state = install(monkeypatch, tmp_path, has_cpdino=False)
    state.dinov3_import_problem = "ImportError: cannot import name 'x' from 'torch'"
    with pytest.raises(SegmentationError) as refused:
        cp.load_model("cpdino", "cpu")
    assert "cannot be imported here (ImportError" in str(refused.value)
    assert "A restart does not fix this" in str(refused.value)
    assert "Restart the runtime" not in str(refused.value) and state.constructed == []
    state.dinov3_import_problem = None  # importable now: installed after Cellpose was imported
    with pytest.raises(SegmentationError, match="Restart the runtime"):
        cp.load_model("cpdino", "cpu")


def test_no_restart_is_asked_when_dinov3_was_there_before_cellpose(stub):
    stub.dinov3_import_problem = "would only be asked if Cellpose had missed DINOv3"
    loaded = cp.load_model("cpdino", "cpu")  # cellpose.vit has DINOv3: nothing else is asked
    assert loaded.dinov3_commit == cp.DINOV3_COMMIT and stub.constructed


def test_dinov3_import_problem_reports_the_real_import(monkeypatch):
    fresh = model_module  # the real function: no stand-in installed in this test
    hub = types.ModuleType("dinov3.hub")
    backbones = types.ModuleType("dinov3.hub.backbones")
    for name, module in (
        ("dinov3", types.ModuleType("dinov3")),
        ("dinov3.hub", hub),
        ("dinov3.hub.backbones", backbones),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    assert fresh.dinov3_import_problem() is None
    monkeypatch.setitem(sys.modules, "dinov3.hub.backbones", None)  # import refused
    problem = fresh.dinov3_import_problem()
    assert problem is not None and problem.startswith(("ModuleNotFoundError", "ImportError"))


def test_ensure_dinov3_clears_the_import_caches_after_installing(monkeypatch):
    order = []
    monkeypatch.setattr(model_module, "dinov3_present", lambda: False)
    monkeypatch.setattr(model_module, "install_dinov3", lambda: order.append("install"))
    monkeypatch.setattr(
        model_module.importlib, "invalidate_caches", lambda: order.append("invalidate")
    )
    assert model_module.ensure_dinov3() is True
    assert order == ["install", "invalidate"]  # so Cellpose imported next finds DINOv3
