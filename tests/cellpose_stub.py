"""A stand-in for Cellpose 4.2.1.1 and torch, injected through ``sys.modules``.

Cellpose is not a dependency of the brick and is not installed in CI. The stand-in keeps what
the real ``CellposeModel`` does that matters here, read from the 4.2.1.1 source: a name or path
it cannot find is replaced by ``cpsam_v2`` (downloaded if absent), a built-in name is looked for
in ``MODEL_DIR`` and fetched when missing, and ``pretrained_model`` / ``device`` / ``backbone``
are set on the loaded model. Its ``eval`` records every call (the stack and the keywords) and
returns a fixed label image, so a test sees exactly what Cellpose was given.
"""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from morphobricks_preprocessing import segmentation_cellpose_model as model_module

BUILTINS = ("cpsam_v2", "cpsam", "cpdino", "cpdino-vitb")


def weights_bytes(name):
    return f"weights of {name}".encode()


def labels_for(shape):
    labels = np.zeros(shape, np.uint16)
    labels[1:4, 1:4] = 1
    labels[5:7, 5:7] = 2
    return labels


class Device:
    def __init__(self, type):
        self.type = type


def model_class(state):
    """Return the stand-in ``CellposeModel`` class bound to ``state``."""

    class Model:
        def __init__(self, gpu=False, pretrained_model="cpsam_v2", device=None, **kwargs):
            state.constructed.append(
                SimpleNamespace(gpu=gpu, pretrained_model=pretrained_model, device=device, **kwargs)
            )
            self.pretrained_model = state.substitute or self._resolve(pretrained_model)
            self.device = Device(state.device_type or device.type)
            dino = "dino" in Path(self.pretrained_model).name
            self.backbone = "dino_vitl" if dino else "sam_vitl"

        @staticmethod
        def _resolve(requested):
            if Path(requested).exists():
                return requested
            path = state.models_dir / requested
            if requested not in BUILTINS:
                return str(state.models_dir / "cpsam_v2")  # the silent substitution
            if not path.exists():
                state.downloaded.append(requested)
                path.write_bytes(weights_bytes(requested))
            return str(path)

        def eval(self, x, **kwargs):
            state.calls.append(SimpleNamespace(x=np.array(x), kwargs=kwargs))
            if state.fail is not None:
                state.fail(x)
            return labels_for(x.shape[1:]), [], []

    return Model


def modules(state):
    """Return the fake ``cellpose``, ``cellpose.models`` and ``torch`` modules."""
    models = types.ModuleType("cellpose.models")
    models.MODEL_DIR = state.models_dir
    models.CellposeModel = model_class(state)
    if state.has_cpdino:
        models.CPDINO = object
    package = types.ModuleType("cellpose")
    package.models = models
    torch = types.ModuleType("torch")
    torch.device = Device
    torch.cuda = SimpleNamespace(is_available=lambda: state.cuda)
    torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: state.mps))
    return {"cellpose": package, "cellpose.models": models, "torch": torch}


def install(monkeypatch, tmp_path, **options):
    """Install the stand-in; return its state (calls, constructed, and switches to flip).

    Switches (set on the returned state before loading): ``cuda`` / ``mps`` availability,
    ``substitute`` (the path every loaded model reports, whatever was asked), ``device_type``
    (the device every loaded model reports), ``fail`` (a function of the stack that raises),
    ``version`` and ``dinov3_commit``.
    """
    models_dir = tmp_path / "cellpose_models"
    models_dir.mkdir(exist_ok=True)
    state = SimpleNamespace(
        calls=[],
        constructed=[],
        downloaded=[],
        models_dir=models_dir,
        cuda=False,
        mps=False,
        substitute=None,
        device_type=None,
        fail=None,
        version="4.2.1.1",
        dinov3_commit=model_module.DINOV3_COMMIT,
        has_cpdino=True,
    )
    for name, value in options.items():
        setattr(state, name, value)
    for name in BUILTINS[:2]:
        (models_dir / name).write_bytes(weights_bytes(name))
    for name, module in modules(state).items():
        monkeypatch.setitem(sys.modules, name, module)
    real_version = model_module.installed_version
    monkeypatch.setattr(
        model_module,
        "installed_version",
        lambda name: state.version if name == "cellpose" else real_version(name),
    )
    monkeypatch.setattr(model_module, "installed_dinov3_commit", lambda: state.dinov3_commit)
    return state


def custom_model(tmp_path, name="my_model"):
    """Write a custom model file and return its path."""
    path = tmp_path / "custom" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(weights_bytes(name))
    return path
