"""A stand-in for Cellpose 4.2.1.1 and torch, injected through ``sys.modules``.

Cellpose is not a dependency of the brick and is not installed in CI. The stand-in keeps what
the real ``CellposeModel`` does that matters here, read from the 4.2.1.1 source: a name or path
it cannot find is replaced by ``cpsam_v2`` (downloaded if absent), a built-in name is looked for
in ``MODEL_DIR`` and fetched when missing (``cellpose_downloaded``: the brick hands Cellpose a
path, so this must stay empty), and ``pretrained_model`` / ``device`` / ``backbone`` are set on
the loaded model. Nothing is ever downloaded: the brick's own download
(:mod:`.segmentation_cellpose_weights`) gets the stand-in bytes of :func:`weights_bytes`
(``downloaded``), whose SHA-256 and size are made the pins; ``download_fail`` makes it fail.
As in 4.2.1.1 ``device`` is only the device it was given; where
the weights sit is told by ``net`` (parameters and buffers with a ``device``). Its ``eval``
records every call (the stack and the keywords) and returns a fixed label image, so a test sees
exactly what Cellpose was given.
"""

import hashlib
import io
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from morphobricks_preprocessing import segmentation_cellpose_model as model_module
from morphobricks_preprocessing import segmentation_cellpose_weights as weights_module
from morphobricks_preprocessing.run_inputs import RunInputs
from test_segmentation_handoff import EXTRA_FIRST
from test_stage3_channels import described_run, extra_stain

BUILTINS = ("cpsam_v2", "cpsam", "cpdino", "cpdino-vitb")
CPDINO_WARNING = (
    "Could not import CPDINO, run `pip install git+https://github.com/facebookresearch/dinov3` "
    "to use CPDINO model"
)
"""Cellpose 4.2.1.1's own words (``cellpose/models.py``, line 23)."""
PLOT_TITLES = ("original image", "predicted outlines", "predicted masks", "predicted cell pose")
"""The titles of ``cellpose.plot.show_segmentation``'s four panels in 4.2.1.1."""


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


class Net:
    """The network: parameters and buffers with a ``device`` (``state.params`` / ``buffers``)."""

    def __init__(self, state, device_type):
        self._params = [Device(t) for t in state.params or [device_type] * 3]
        self._buffers = [Device(t) for t in state.buffers or [device_type] * 2]

    def parameters(self):
        return iter(SimpleNamespace(device=d) for d in self._params)

    def buffers(self):
        return iter(SimpleNamespace(device=d) for d in self._buffers)


class TextBar:
    """Stands for ``tqdm.tqdm``, the plain text bar ``cellpose.utils`` imports."""

    def __init__(self, *args, **kwargs):
        self.kwargs, self.done = kwargs, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, n):
        self.done += n


def model_class(state):
    """Return the stand-in ``CellposeModel`` class bound to ``state``."""

    class Model:
        def __init__(self, gpu=False, pretrained_model="cpsam_v2", device=None, **kwargs):
            state.constructed.append(
                SimpleNamespace(gpu=gpu, pretrained_model=pretrained_model, device=device, **kwargs)
            )
            if state.on_construct is not None:  # something else touches the file meanwhile
                state.on_construct(pretrained_model)
            if state.cpdino_log:  # 4.2.1.1 logs this when cellpose.models cannot import DINOv3
                logging.getLogger("cellpose.models").warning(CPDINO_WARNING)
            self.pretrained_model = state.substitute or self._resolve(pretrained_model)
            self.device = device  # as in Cellpose: only what was passed in
            self.net = None if state.no_net else Net(state, device.type)
            dino = "dino" in Path(self.pretrained_model).name
            self.backbone = "dino_vitl" if dino else "sam_vitl"

        @staticmethod
        def _resolve(requested):
            if Path(requested).exists():
                return requested
            path = state.models_dir / requested
            if requested not in BUILTINS:
                logging.getLogger("cellpose.models").warning(
                    "pretrained model %s not found, using default model", requested
                )
                return str(state.models_dir / "cpsam_v2")  # the silent substitution
            if not path.exists():
                state.cellpose_downloaded.append(requested)
                data = weights_bytes(requested)
                # As cellpose.utils.download_url_to_file: a bar of cellpose.utils.tqdm.
                bar_class = sys.modules["cellpose.utils"].tqdm
                state.bars.append(bar_class)
                with bar_class(total=len(data), unit="B", unit_scale=True) as bar:
                    bar.update(len(data))
                logging.getLogger("cellpose.models").info("Downloading: %s", requested)
                path.write_bytes(data)
            return str(path)

        def eval(self, x, **kwargs):
            state.calls.append(SimpleNamespace(x=np.array(x), kwargs=kwargs))
            if state.fail is not None:
                state.fail(x)
            shape = x.shape[1:]
            flows = [np.full((*shape, 3), 7, np.uint8), np.zeros((2, *shape), np.float32)]
            return labels_for(shape), flows, []

    return Model


def modules(state):
    """Return the fake ``cellpose``, ``cellpose.models`` and ``torch`` modules."""
    models = types.ModuleType("cellpose.models")
    models.MODEL_DIR = state.models_dir
    models._MODEL_URL = "https://huggingface.co/mouseland/cellpose-sam/resolve/main/"
    models.normalize_default = dict(state.normalize_default)
    models.CellposeModel = model_class(state)
    models.CPDINO = object  # 4.2.1.1 defines it whether or not DINOv3 imports
    vit = types.ModuleType("cellpose.vit")
    if state.has_cpdino:  # what cellpose.vit imports from DINOv3 when it can
        vit.dinov3_vitl16 = vit.dinov3_vitb16 = object
    plot = types.ModuleType("cellpose.plot")

    def show_segmentation(fig, img, maski, flowi, channels=(0, 0), file_name=None):
        # Cellpose 4.2.1.1's signature; four panels, like the real one (nothing is saved).
        state.plotted.append(SimpleNamespace(img=img, masks=maski, flow=flowi, channels=channels))
        for n, title in enumerate(PLOT_TITLES, start=1):
            fig.add_subplot(1, 4, n).set_title(title)

    plot.show_segmentation = show_segmentation
    utils = types.ModuleType("cellpose.utils")
    utils.tqdm = TextBar  # Cellpose's own: tqdm's plain text bar
    package = types.ModuleType("cellpose")
    package.models, package.plot, package.utils, package.vit = models, plot, utils, vit
    torch = types.ModuleType("torch")
    torch.device = Device
    torch.cuda = SimpleNamespace(is_available=lambda: state.cuda)
    torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: state.mps))
    return {
        "cellpose": package,
        "cellpose.models": models,
        "cellpose.plot": plot,
        "cellpose.utils": utils,
        "cellpose.vit": vit,
        "torch": torch,
    }


def install(monkeypatch, tmp_path, **options):
    """Install the stand-in; return its state (calls, constructed, and switches to flip).

    Switches (set on the returned state before loading): ``cuda`` / ``mps`` availability,
    ``substitute`` (the path every loaded model reports, whatever was asked), ``params`` /
    ``buffers`` (the device types of the network's tensors, when not the requested one),
    ``no_net``, ``fail`` (a function of the stack that raises), ``version``,
    ``dinov3_commit`` (None: no DINOv3 installed; ``install_dinov3`` then installs the pinned
    one and records it in ``dinov3_installs``), ``dinov3_present`` (a DINOv3 of unknown commit),
    ``dinov3_import_problem`` (why ``dinov3.hub.backbones`` would not import; None: it would)
    and ``download_fail`` (an exception the download raises).
    """
    models_dir = tmp_path / "cellpose_models"
    models_dir.mkdir(exist_ok=True)
    state = SimpleNamespace(
        calls=[],
        plotted=[],
        constructed=[],
        bars=[],
        downloaded=[],
        urls=[],
        cellpose_downloaded=[],
        dinov3_installs=[],
        dinov3_present=False,
        dinov3_import_problem=None,
        download_fail=None,
        models_dir=models_dir,
        cuda=False,
        mps=False,
        substitute=None,
        params=None,
        buffers=None,
        no_net=False,
        fail=None,
        version="4.2.1.1",
        dinov3_commit=model_module.DINOV3_COMMIT,
        has_cpdino=True,
        cpdino_log=False,
        on_construct=None,
        normalize_default={
            "lowhigh": None,
            "percentile": None,
            "normalize": True,
            "norm3D": True,
            "sharpen_radius": 0,
            "smooth_radius": 0,
            "tile_norm_blocksize": 0,
            "tile_norm_smooth3D": 1,
            "invert": False,
        },
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
    fetching(monkeypatch, state)
    return state


def fetching(monkeypatch, state):
    """Stand in for DINOv3's installation and the weights download (nothing is fetched)."""
    monkeypatch.setattr(
        model_module,
        "dinov3_present",
        lambda: state.dinov3_commit is not None or state.dinov3_present,
    )

    def install_dinov3():
        state.dinov3_installs.append(model_module.DINOV3_COMMIT)
        state.dinov3_commit = model_module.DINOV3_COMMIT

    monkeypatch.setattr(model_module, "install_dinov3", install_dinov3)
    monkeypatch.setattr(model_module, "dinov3_import_problem", lambda: state.dinov3_import_problem)
    monkeypatch.setattr(weights_module, "PINNED_WEIGHTS", fake_pins())
    monkeypatch.setattr(weights_module, "_open_url", lambda url, timeout: served(state, url))


def fake_pins():
    """Return pins for the stand-in bytes of every built-in model (as the real pins are)."""
    return {
        name: weights_module.PinnedFile(
            hashlib.sha256(weights_bytes(name)).hexdigest(), len(weights_bytes(name))
        )
        for name in BUILTINS
    }


class Response(io.BytesIO):
    """What ``urlopen`` returns: a readable body with headers."""

    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers


def served(state, url):
    """Answer the brick's download of ``url`` with the stand-in bytes (never the network)."""
    name = url.rsplit("/", 1)[-1]
    state.downloaded.append(name)
    state.urls.append(url)
    if state.download_fail is not None:
        raise state.download_fail
    data = weights_bytes(name)
    return Response(data, {"Content-Length": str(len(data))})


def custom_model(tmp_path, name="my_model"):
    """Write a custom model file and return its path."""
    path = tmp_path / "custom" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(weights_bytes(name))
    return path


def mixed_run(tmp_path):
    """Run the owner's mixed-channel case: DNA, ACTIN and TUB everywhere, EXTRA first in one repeat.

    Returns a namespace with the opened ``inputs``, the ``out`` (run), ``experiment`` and ``masks``
    folders and the description ``data``.
    """
    data = extra_stain().replace(*EXTRA_FIRST)
    result, out, _ = described_run(tmp_path, data)
    assert result.written, [(i.code, i.message) for i in result.issues]
    base = out.parent
    return SimpleNamespace(
        inputs=RunInputs.open(out),
        out=out,
        experiment=base / "exp",
        masks=base / "masks",
        data=data,
    )


def two_channel_run(tmp_path):
    """Return a described run whose every image has DNA then TUB (C = 1, 2): one layout."""
    data = extra_stain()
    for old, new in (
        (b'["DNA", "ACTIN", "TUB", "EXTRA"]', b'["DNA", "TUB"]'),
        (b'["DNA", "ACTIN", "TUB"]', b'["DNA", "TUB"]'),
        (b'["ACTIN", "DNA", "EXTRA", "TUB"]', b'["DNA", "TUB"]'),
        (b"[channels.ACTIN]\n\n", b""),
        (b"[channels.EXTRA]\n\n", b""),
    ):
        assert old in data, old
        data = data.replace(old, new)
    result, out, _ = described_run(tmp_path, data)
    assert result.written, [(i.code, i.message) for i in result.issues]
    base = out.parent
    return SimpleNamespace(
        inputs=RunInputs.open(out),
        out=out,
        experiment=base / "exp",
        masks=base / "masks",
        data=data,
    )
