"""Loading a Cellpose model without substitution, device fallback or silent download.

Part of :mod:`.segmentation_cellpose` (which re-exports these names). Cellpose, ``torch`` and
``dinov3`` are imported only inside the functions here, so importing this module stays inert;
the pinned release is :data:`CELLPOSE_VERSION` and another one is refused.

Cellpose 4.2.1.1 replaces a model name or path it cannot find by ``cpsam_v2`` with a log
warning, turns an unavailable GPU into the CPU, and downloads uncached built-in weights. Each
is checked here: the model is one of :data:`BUILTIN_MODELS` or an existing file (validated
before loading); the weights file the loaded model reports must be the one asked for; the
device is unavailable-refused up front, and after loading **every parameter and every buffer**
of the network (``model.net``) must sit on a device of the requested type (``cuda:1`` counts as
``cuda``; the index is not checked). ``model.device`` is not evidence: Cellpose 4.2.1.1 only
stores the device it was given. Uncached weights are downloaded only with ``allow_download``;
whether they were, and from where, goes into the record. The weights file is hashed for the
record.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from ._io import file_sha256
from .segmentation_record import DEVICES, SegmentationError

CELLPOSE_VERSION = "4.2.1.1"
"""The only Cellpose release this brick runs (the notebook installs it)."""
BUILTIN_MODELS = ("cpsam_v2", "cpsam", "cpdino", "cpdino-vitb")
"""The built-in models of Cellpose 4.2.1.1, the first being the default."""
DEFAULT_MODEL = BUILTIN_MODELS[0]
DINO_MODELS = ("cpdino", "cpdino-vitb")
"""The built-in models that need the DINOv3 package (:data:`DINOV3_COMMIT`)."""
DINOV3_URL = "https://github.com/facebookresearch/dinov3"
DINOV3_COMMIT = "6876159a11b4df116f30f667f8c9888617df0751"
"""The DINOv3 commit installed for ``cpdino`` models: the head of its default branch on
22 November 2026, **not verified against cpdino** (it is a pin, not a tested pairing). A model
that needs DINOv3 runs only with exactly this commit installed."""
DEVICE_NOTE = (
    "Cellpose may move the flow-threshold quality step to the CPU when the GPU has too little "
    "memory (images above 10000 x 10000 pixels on cuda), and always computes the masks from the "
    "flows on the CPU on mps. This is Cellpose's own behaviour; it is not refused, and the "
    "network itself ran on the device recorded."
)
"""Recorded with a batch that ran on a GPU (not refused: the network still runs on the device)."""
SAM_TILE, DINO_TILE = 256, 384
"""The tile size (``bsize``) of a SAM model and of a DINOv3 model."""


@dataclass(frozen=True)
class ModelChoice:
    """A model that passed :func:`choose_model`.

    Attributes
    ----------
    name
        The built-in name, or the file name of a custom model.
    path
        The custom model's absolute path, or None for a built-in model.
    """

    name: str
    path: Path | None = None

    @property
    def builtin(self) -> bool:
        """Return True for one of :data:`BUILTIN_MODELS`."""
        return self.path is None


def choose_model(model: str | os.PathLike) -> ModelChoice:
    """Validate the model asked for, before anything is loaded: a built-in name or a file.

    Cellpose 4.2.1.1 replaces a name or path it cannot find by ``cpsam_v2``; here that is a
    refusal. The text must be exactly one of :data:`BUILTIN_MODELS` or the path of an existing
    file (a folder is refused). Nothing is guessed from a near miss.

    Raises
    ------
    SegmentationError
        Naming the four models and the file rule.
    """
    text = os.fspath(model)
    if text in BUILTIN_MODELS:
        return ModelChoice(text)
    path = Path(text).expanduser()
    if path.is_file():
        return ModelChoice(path.name, path.resolve())
    what = "is a folder, not a model file" if path.is_dir() else "is not a model name or a file"
    raise SegmentationError(
        f"The model {text!r} {what}. Choose one of {', '.join(BUILTIN_MODELS)}, or give the path "
        "of an existing model file. Cellpose would silently use cpsam_v2 instead; this notebook "
        "never substitutes a model. (Cellpose 3 models such as cyto3 are not supported by "
        f"Cellpose {CELLPOSE_VERSION}.)"
    )


# --- Cellpose, torch and DINOv3, imported only here -------------------------------------------


def installed_version(distribution: str) -> str | None:
    """Return the installed version of a distribution, or None (a seam for the tests)."""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _cellpose_models() -> ModuleType:
    """Import ``cellpose.models`` of the pinned release, or refuse."""
    found = installed_version("cellpose")
    if found != CELLPOSE_VERSION:
        have = f"version {found} is installed" if found else "it is not installed"
        raise SegmentationError(
            f"This notebook runs Cellpose {CELLPOSE_VERSION}; {have}. Run the install cell, "
            "then restart the runtime if it asks."
        )
    return importlib.import_module("cellpose.models")


def _torch() -> ModuleType:
    """Import torch, or refuse."""
    try:
        return importlib.import_module("torch")
    except ImportError:
        raise SegmentationError(
            "PyTorch is not installed. Run the install cell (Cellpose installs it)."
        ) from None


def check_device(device: str) -> None:
    """Refuse a device that is not one of :data:`.segmentation_record.DEVICES` or not available.

    There is no fallback: a GPU that is not there is a refusal that names the CPU as a choice
    for the user to make, never a silent switch.

    Raises
    ------
    SegmentationError
        For an unknown device, or ``cuda`` or ``mps`` where PyTorch finds none.
    """
    if device not in DEVICES:
        raise SegmentationError(f"The device must be one of {', '.join(DEVICES)}, not {device!r}.")
    if device == "cpu":
        return
    torch = _torch()
    here = torch.cuda.is_available() if device == "cuda" else torch.backends.mps.is_available()
    if not here:
        raise SegmentationError(
            f"The device {device} was asked for but is not available here. Nothing falls back "
            "to the CPU. Switch the runtime to one with a GPU (Google Colab: Runtime > Change "
            "runtime type), or choose the device cpu on purpose (much slower)."
        )


def weights_file(name: str) -> Path:
    """Return where Cellpose keeps the weights of built-in model ``name`` (it may not exist yet)."""
    return Path(os.fspath(_cellpose_models().MODEL_DIR)) / name


def default_normalization() -> dict:
    """Return a copy of Cellpose's own normalisation defaults, read from its module right now.

    Cellpose 4.2.1.1 keeps them in ``cellpose.models.normalize_default`` (a module global that
    its own ``eval`` writes to when ``normalize`` is a bool), so they are read at call time and
    copied; the caller applies the user's values on the copy.
    """
    return dict(_cellpose_models().normalize_default)


def installed_dinov3_commit() -> str | None:
    """Return the Git commit the installed DINOv3 package came from, or None when unknown.

    Read from the ``direct_url.json`` that ``pip install git+...`` leaves in the package's
    metadata; a package with no such record has an unknown commit and is never assumed to be
    the pinned one.
    """
    importlib.invalidate_caches()
    for name in importlib.metadata.packages_distributions().get("dinov3", []):
        text = importlib.metadata.distribution(name).read_text("direct_url.json")
        if text:
            commit = json.loads(text).get("vcs_info", {}).get("commit_id")
            if commit:
                return str(commit)
    return None


def dinov3_install_command() -> list[str]:
    """Return the ``pip`` command that installs DINOv3 at :data:`DINOV3_COMMIT`, without deps.

    ``--no-deps``: DINOv3's requirements (``torch``, ``torchvision``, ``torchmetrics``, ...) are
    unpinned and could replace the torch Cellpose runs on. Cellpose reaches DINOv3 only through
    ``dinov3.hub.backbones``, which imports nothing but ``numpy`` and ``torch`` (read at the
    pinned commit).
    """
    spec = f"git+{DINOV3_URL}@{DINOV3_COMMIT}"
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-deps",
        spec,
    ]


def install_dinov3() -> None:
    """Install DINOv3 at the pinned commit (only ever called when the user chooses to).

    Raises
    ------
    SegmentationError
        When the installation fails.
    """
    try:
        subprocess.run(dinov3_install_command(), check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise SegmentationError(
            f"DINOv3 could not be installed ({exc}). Check the output and the internet access."
        ) from exc


def check_dinov3(models: ModuleType) -> str:
    """Return the commit of the installed DINOv3 if it is the pinned one, else refuse.

    Cellpose imports DINOv3 when it is first imported, so DINOv3 installed after that needs a
    restart of the runtime (the check says so): install it, restart, then load the model.
    """
    command = " ".join(dinov3_install_command()[2:])
    commit = installed_dinov3_commit()
    if commit != DINOV3_COMMIT:
        found = (
            f"commit {commit} is installed"
            if commit
            else "none is installed, or its commit is unknown"
        )
        raise SegmentationError(
            f"The DINOv3 models need DINOv3 at commit {DINOV3_COMMIT}; {found}. Tick the "
            f"installation in the notebook (it runs: {command}), then restart the runtime."
        )
    if not hasattr(models, "CPDINO"):
        raise SegmentationError(
            "DINOv3 is installed but Cellpose was imported before it. Restart the runtime, then "
            "run the cells again."
        )
    return commit


# --- loading and verifying a model ------------------------------------------------------------


@dataclass(frozen=True)
class LoadedModel:
    """A Cellpose model that was loaded and verified to be the one asked for.

    Attributes
    ----------
    model
        The Cellpose model object.
    name, file
        The name recorded (built-in name or the custom file's name) and the weights file
        (its name for a built-in model, its absolute path for a custom one).
    path
        The weights file that was loaded.
    sha256
        The SHA-256 of that file.
    device
        ``cuda``, ``cpu`` or ``mps``: the device type every parameter and buffer of the network
        was verified to sit on after loading.
    downloaded
        True when this call downloaded the built-in weights (they were not on disk).
    source
        Where Cellpose downloads the built-in weights from (its URL on Hugging Face), or None
        for a custom model file.
    backbone
        Cellpose's name of the network (``sam_vitl``, ``dino_vitl``, ...).
    tile_size
        The tile size (``bsize``) used: 256 for a SAM network, 384 otherwise.
    dinov3_commit
        The DINOv3 commit for a DINOv3 network, else None.
    """

    model: object
    name: str
    file: str
    path: Path
    sha256: str
    device: str
    backbone: str
    tile_size: int
    dinov3_commit: str | None = None
    downloaded: bool = False
    source: str | None = None


def _source(models: ModuleType, choice: ModelChoice) -> str | None:
    """Return the URL Cellpose downloads built-in weights ``choice`` from, None for a file."""
    return None if choice.path is not None else f"{models._MODEL_URL}{choice.name}"


def _expected(
    models: ModuleType, choice: ModelChoice, allow_download: bool
) -> tuple[Path, str, bool]:
    """Return the weights file, what to hand to Cellpose to load it, and whether it downloads.

    A built-in model already on disk is handed over by its absolute path, so Cellpose looks
    nowhere else (not in the working folder, not in its list of user models) and cannot
    download. One that is not on disk is handed over by name only when ``allow_download``.
    """
    if choice.path is not None:
        return choice.path, os.fspath(choice.path), False
    expected = Path(os.fspath(models.MODEL_DIR)) / choice.name
    if expected.is_file():
        return expected, os.fspath(expected), False
    if not allow_download:
        raise SegmentationError(
            f"The weights of {choice.name} are not on this computer ({expected}). The built-in "
            f"weights are downloaded from Hugging Face ({_source(models, choice)}) on first use. "
            "Allow the download on purpose (tick it in the notebook) and run the cell again."
        )
    if os.path.lexists(choice.name):
        raise SegmentationError(
            f"A file or folder named {choice.name} is in the working folder, and Cellpose would "
            "load it instead of downloading the model. Change to another working folder."
        )
    return expected, choice.name, True


def _construct(models: ModuleType, request: str, device: str) -> object:
    """Build the Cellpose model on the stated device; any Cellpose failure is a refusal."""
    torch = _torch()
    try:
        return models.CellposeModel(
            gpu=device != "cpu",
            pretrained_model=request,
            device=torch.device(device),
            use_bfloat16=True,
        )
    except Exception as exc:  # Cellpose raises many types; none may leave a model half-chosen
        raise SegmentationError(
            f"Cellpose could not load {request}: {type(exc).__name__}: {exc}"
        ) from exc


def _weight_devices(loaded: object) -> tuple[int, set[str]]:
    """Return how many tensors the network holds and the device types of all of them.

    Every parameter and every buffer of ``loaded.net`` counts. A model without a network that
    lists its tensors, or one with none, proves nothing and is refused.
    """
    net = getattr(loaded, "net", None)
    tensors = []
    for listing in ("parameters", "buffers"):
        method = getattr(net, listing, None)
        if not callable(method):
            raise SegmentationError(
                "The loaded model's network does not list its weights, so the device they sit "
                "on cannot be checked; refused."
            )
        tensors.extend(method())
    if not tensors:
        raise SegmentationError(
            "The loaded model's network holds no weights, so the device cannot be checked; refused."
        )
    return len(tensors), {str(t.device.type) for t in tensors}


def _verify(loaded: object, expected: Path, device: str, choice: ModelChoice) -> None:
    """Refuse a model that is not the file or device asked for."""
    got = os.fspath(getattr(loaded, "pretrained_model", ""))
    if not got or Path(got).resolve() != expected.resolve():
        raise SegmentationError(
            f"Cellpose loaded {got or 'an unknown file'} instead of {expected} for {choice.name}: "
            "it substituted another model. Nothing was segmented."
        )
    count, found = _weight_devices(loaded)
    if found != {device}:
        raise SegmentationError(
            f"The device {device} was asked for but weights of the model sit on "
            f"{', '.join(sorted(found))} ({count} parameters and buffers checked). Nothing "
            "falls back to another device."
        )


def load_model(
    model: str | os.PathLike, device: str, *, allow_download: bool = False
) -> LoadedModel:
    """Load a Cellpose model, verified to be the weights file and device asked for.

    The order is: the model and device are validated, Cellpose's version is checked, the
    weights must be on disk (or the download allowed), a DINOv3 model needs the pinned
    DINOv3; then the model is loaded and **verified** (the weights file it reports is the one
    asked for; every parameter and buffer of the network sits on the stated device type), and
    the weights file is hashed.

    Raises
    ------
    SegmentationError
        For an unknown model or one that does not exist, a device that is not available or
        not the one the model sits on, a substituted model, weights that are not downloaded
        (and no ``allow_download``), another Cellpose version or another DINOv3 commit.
    """
    choice = choose_model(model)
    check_device(device)
    models = _cellpose_models()
    dino = choice.name in DINO_MODELS
    commit = check_dinov3(models) if dino else None
    expected, request, downloads = _expected(models, choice, allow_download)
    loaded = _construct(models, request, device)
    _verify(loaded, expected, device, choice)
    backbone = getattr(loaded, "backbone", None)
    if not isinstance(backbone, str):
        raise SegmentationError("The loaded model does not say which network it is; refused.")
    if backbone.startswith("dino") and commit is None:
        commit = check_dinov3(models)
    return LoadedModel(
        model=loaded,
        name=choice.name,
        file=os.fspath(expected) if choice.path else expected.name,
        path=expected,
        sha256=file_sha256(expected),
        device=device,
        backbone=backbone,
        tile_size=SAM_TILE if backbone == "sam_vitl" else DINO_TILE,
        dinov3_commit=commit,
        downloaded=downloads,
        source=_source(models, choice),
    )
